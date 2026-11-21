"""Verified analysis-source switching. Synthetic WAV/candidate evidence, no ML."""
from copy import deepcopy
import hashlib
import json
import threading
import time
import pytest
from PySide6.QtWidgets import QApplication
from desktop_qt.controller import Bridge
from lyricsync.model import Mapping, Project
from lyricsync.local_models import UVR
from lyricsync.candidate_tracks import capture_source, candidate_from_project
from test_dev4_spectrogram import encoded_audio


def wait(condition, timeout=12):
    app=QApplication.instance(); end=time.monotonic()+timeout
    while time.monotonic()<end:
        app.processEvents()
        if condition(): return
        time.sleep(.003)
    assert condition()


@pytest.fixture
def source_ui(tmp_path):
    app=QApplication.instance() or QApplication([])
    bridge=Bridge(storage_dir=tmp_path/'storage')
    path=encoded_audio(tmp_path/'mix.wav', 2)
    assert bridge.command('media.open', {'path':str(path)})
    wait(lambda:bridge.audioAnalysis['status']=='ready')
    yield bridge,tmp_path,path
    bridge.shutdown(); bridge.wait_for_shutdown(app)


def add_stem(bridge, root, path, offset=0., duration=2., mapping_updates=None):
    stem=encoded_audio(root/'vocals.wav', duration)
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    manifest=root/'mapping.json'
    manifest.write_text(json.dumps(dict(path=str(stem.resolve()),source_path=str(path.resolve()),
        sha256=sha(stem),source_sha256=sha(path),model_id=UVR.id,model_revision=UVR.revision,
        offset_seconds=offset,duration_seconds=duration,latency_samples=0,padding_removed=True,
        time_mapping='project_seconds = offset_seconds + stem_sample / sample_rate')),encoding='utf8')
    if mapping_updates:
        data=json.loads(manifest.read_text(encoding='utf8'));data.update(mapping_updates)
        manifest.write_text(json.dumps(data),encoding='utf8')
    bridge.document.project.mappings=[Mapping('a',[],text_override='synthetic',start=.1,end=.3)]
    bridge._changed()
    snapshot=capture_source(deepcopy(bridge.document),'vocal',bridge.media_controller.index,
        auxiliary_paths={'alignment_audio':str(path),'separated_vocals':str(stem),'separation_manifest':str(manifest)})
    record=candidate_from_project(snapshot,Project.from_dict(bridge.document.project.to_dict()))
    bridge._candidate_install(record)
    return record,stem


def test_mix_vocal_mix_is_analysis_only_and_preserves_project(source_ui):
    b,root,path=source_ui; record,stem=add_stem(b,root,path)
    before=b.document.project.to_dict(); media_before=b.media_controller.snapshot()
    b.selectAudioAnalysisSource('vocal');wait(lambda:b.audioAnalysis['status']!='loading')
    assert b.audioAnalysis['status']=='ready'
    assert b.audioAnalysis['source']=='vocal'
    assert b.audioAnalysis['sourcePath']==str(stem.resolve())
    assert b.media_controller.path==str(path)
    assert b.media_controller.snapshot()['positionSeconds']==media_before['positionSeconds']
    assert b.document.project.to_dict()==before
    b.selectAudioAnalysisSource('original');wait(lambda:b.audioAnalysis['status']=='ready')
    assert b.audioAnalysis['source']=='original' and b.audioAnalysis['sourcePath']==str(path)
    assert b.document.project.to_dict()==before


@pytest.mark.parametrize('damage',['other-project','stem','manifest','source'])
def test_untrusted_or_modified_source_rejected(source_ui,damage):
    b,root,path=source_ui;record,stem=add_stem(b,root,path)
    if damage=='other-project': b.document.source_candidates[-1]['document_id']='different-project'
    elif damage=='stem': stem.write_bytes(b'changed')
    elif damage=='manifest': (root/'mapping.json').write_text('{}')
    else: path.write_bytes(b'changed')
    b.selectAudioAnalysisSource('vocal');wait(lambda:b.audioAnalysis['status']!='loading')
    assert b.audioAnalysis['status']=='error'
    assert not b.audioAnalysis.get('sourcePath')


def test_selected_stem_modification_invalidates_active_plot(source_ui):
    b,root,path=source_ui;record,stem=add_stem(b,root,path)
    b.selectAudioAnalysisSource('vocal');wait(lambda:b.audioAnalysis['status']=='ready')
    stem.write_bytes(b'changed while displayed')
    b._check_analysis_source()
    assert b.audioAnalysis['status']=='error' and not b.audioAnalysis.get('sourcePath')


@pytest.mark.parametrize('replacement',['original','media'])
def test_cancelled_or_replaced_worker_cannot_restore_old_vocal_plot(source_ui,monkeypatch,replacement):
    import lyricsync.audio_analysis as analysis
    b,root,path=source_ui;record,stem=add_stem(b,root,path)
    entered=threading.Event();release=threading.Event();real=analysis.analyze_audio
    def delayed(filename,duration,cancel):
        if str(filename)==str(stem.resolve()):
            entered.set();assert release.wait(8)
            # Deliberately emulate a late worker which ignores cancellation.
            return real(filename,duration,lambda:False)
        return real(filename,duration,cancel)
    monkeypatch.setattr(analysis,'analyze_audio',delayed)
    b.selectAudioAnalysisSource('vocal');wait(entered.is_set)
    if replacement=='original': b.selectAudioAnalysisSource('original'); expected=path
    else:
        expected=encoded_audio(root/'replacement.wav',3)
        b.switchCandidateSource('main')
        b.document.project.mappings=[]  # replacement is allowed only with no existing subtitles
        b._changed()
        assert b.command('media.open',{'path':str(expected)})
    wait(lambda:b.audioAnalysis['status']=='ready' and b.audioAnalysis['source']=='original')
    release.set();wait(lambda:not b.jobs.live_threads())
    assert b.audioAnalysis['source']=='original' and b.audioAnalysis['sourcePath']==str(expected)


def test_partial_stem_keeps_real_offset_not_fabricated_full_length(source_ui):
    b,root,path=source_ui;add_stem(b,root,path,offset=.5,duration=1.)
    b.selectAudioAnalysisSource('vocal');wait(lambda:b.audioAnalysis['status']!='loading')
    assert b.audioAnalysis['status']=='ready'
    assert b.audioAnalysis['sourceOffsetSeconds']==.5
    assert b.audioAnalysis['durationSeconds']==1.


def test_partial_spectrum_maps_project_times_to_stem_times(source_ui):
    import numpy as np
    from desktop_qt.spectrogram_item import SpectrogramItem
    b,root,path=source_ui
    assert b.metaObject().indexOfMethod('selectAudioAnalysisSource(QString)') >= 0
    class Worker:
        def __init__(self): self.requests=[]
        def request(self,value): self.requests.append(value)
    surface=SpectrogramItem(); worker=Worker(); surface._worker=worker
    surface._controller=b; surface._path=str(path); surface._duration=1.;surface._source_offset=.5
    surface._active=True;surface._first=101;surface._span=100;surface.setWidth(100);surface.setHeight(50)
    surface._timer.stop();surface._submit(); request=worker.requests[-1]
    first_index, actual=request['tiles'][0]
    step=request['step'];first_frame=1+first_index*256*step
    expected=np.asarray(b.spectrumColumnTimes(first_frame,256*step,256))-.5
    assert np.allclose(actual,expected)
    assert request['source'][3]==.5
    assert request['source'][4]==22050  # Strong identity also includes rate/version/type.
    surface._active=False;surface._timer.stop();surface._worker=None

from test_qt_phase1_preview_workspace import ui
from test_qt_preview_visibility import application, find


def test_real_qml_source_selector_and_spectrum_use_verified_stem(ui,tmp_path):
    from PySide6.QtCore import QMetaObject,Q_ARG
    from test_qt_dev4_spectrum_timeline import setup
    from tools.qt_event_loop_wait import delay
    timeline,surface=setup(ui,tmp_path)
    b=ui.bridge; source=__import__('pathlib').Path(b.media_controller.path)
    record,stem=add_stem(b,tmp_path,source,offset=.5,duration=1.)
    selector=find(ui,'audioSourceSelector')
    assert selector and selector.isVisible()
    assert QMetaObject.invokeMethod(selector,'activated',Q_ARG(int,1))
    wait(lambda:b.audioAnalysis['status']=='ready' and b.audioAnalysis['source']=='vocal')
    wait(lambda:__import__('os').path.normcase(surface.property('sourcePath'))==__import__('os').path.normcase(str(stem.resolve())) and surface.property('status')=='ready')
    assert selector.property('currentIndex')==1
    assert surface.property('sourceOffsetSeconds')==.5
    assert surface.property('vocalSource') is True
    assert surface._worker.latest['source'][3]==.5
    assert b.media_controller.path==str(source)
    assert QMetaObject.invokeMethod(selector,'activated',Q_ARG(int,0))
    wait(lambda:b.audioAnalysis['status']=='ready' and b.audioAnalysis['source']=='original')
    assert selector.property('currentIndex')==0

@pytest.mark.parametrize('change',[{'offset_seconds':-1}, {'duration_seconds':30}, {'latency_samples':1},
                                  {'padding_removed':False}, {'time_mapping':'unknown'}])
def test_signed_but_unusable_time_mapping_is_rejected(source_ui,change):
    b,root,path=source_ui;add_stem(b,root,path,mapping_updates=change)
    b.selectAudioAnalysisSource('vocal');wait(lambda:b.audioAnalysis['status']!='loading')
    assert b.audioAnalysis['status']=='error' and not b.audioAnalysis.get('sourcePath')


def test_missing_stem_keeps_original_analysis(source_ui):
    b,root,path=source_ui;before=deepcopy(b.audioAnalysis)
    b.selectAudioAnalysisSource('vocal')
    assert b.audioAnalysis['source']=='original' and b.audioAnalysis['sourcePath']==str(path)
    assert b.audioAnalysis['waveform']==before['waveform']
