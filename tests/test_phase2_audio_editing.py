"""Real decode + offscreen Bridge acceptance, using generated disposable media."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
import time
import wave
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QEvent
from shiboken6 import isValid
from desktop_qt.controller import Bridge
from lyricsync.audio_analysis import analyze_audio
from lyricsync.subtitles import import_translation
from lyricsync.virtual_timeline import VirtualFrameIndex
from lyricsync.project_document import load_document

@pytest.fixture(scope='module')
def app(): return QApplication.instance() or QApplication([])

@pytest.fixture
def bridge(app,tmp_path):
    value=Bridge(storage_dir=tmp_path/'storage')
    try:
        value.media_controller.audio.setMuted(True)
        yield value
    finally:
        # Decoder exit alone is insufficient: drain Jobs/pending writes while
        # QObject owners live, then destroy the Qt tree on the owning GUI thread.
        value.wait_for_shutdown(app)
        assert value.shutdown_complete and not value.jobs.live_threads()
        value.deleteLater()
        QCoreApplication.sendPostedEvents(value,QEvent.Type.DeferredDelete)
        assert not isValid(value)

def wait(app,predicate,timeout=10):
    deadline=time.monotonic()+timeout
    while not predicate() and time.monotonic()<deadline:
        app.processEvents();time.sleep(.005)
    assert predicate()

def wav(path,duration=2.,frequency=1000.,amplitude=.7):
    rate=16000
    values=amplitude*np.sin(np.arange(round(duration*rate))*2*np.pi*frequency/rate)
    with wave.open(str(path),'wb') as out:
        out.setnchannels(1);out.setsampwidth(2);out.setframerate(rate)
        out.writeframes((values*32767).astype('<i2').tobytes())
    return path

def test_virtual_index_is_lazy_precise_and_bounded():
    index=VirtualFrameIndex(86400)
    assert len(index.stamps)==8640000 and not isinstance(index.stamps,list)
    assert index.stamp(126)==1.25
    assert index.interval(126,126)==(1.25,1.26)
    assert index.current(1.259)==126
    for invalid in (0,-1,float('inf'),86401):
        with pytest.raises(ValueError):VirtualFrameIndex(invalid)

def test_srt_exact_boundaries_ids_undo_save_reopen(app,bridge,tmp_path):
    path=tmp_path/'timed.srt'
    path.write_text('1\n00:00:00,123 --> 00:00:00,987\nRepeat\n\n2\n00:00:01,001 --> 00:00:01,999\nRepeat\n',encoding='utf-8')
    assert bridge.command('input.import',{'role':'translation','path':str(path)})
    wait(app,lambda:not bridge.state['task']['running'])
    assert not bridge.state['error']
    assert bridge.media['mediaKind']=='virtual'
    rows=bridge.document.project.mappings
    assert [(r.start,r.end) for r in rows]==[(.123,.987),(1.001,1.999)]
    identities=[r.id for r in rows];assert len(set(identities))==2
    assert bridge.command('row.edit',{'id':identities[1],'text':'changed'})
    assert bridge.command('history.undo',{})
    assert bridge.document.project.mappings[1].text_override is None
    assert bridge.command('history.redo',{})
    assert [r.id for r in bridge.document.project.mappings]==identities
    saved=tmp_path/'pure.lyria'
    assert bridge.command('project.saveAs',{'path':str(saved)})
    wait(app,lambda:saved.exists() and bridge.path==str(saved))
    loaded=load_document(saved)
    assert loaded.project.mappings[0].start==.123
    bridge._new()
    assert bridge.command('project.open',{'path':str(saved)})
    wait(app,lambda:bridge.media['ready'])
    assert bridge.media['mediaKind']=='virtual'
    assert [r.id for r in bridge.document.project.mappings]==identities
    assert bridge.document.project.mappings[1].text_override=='changed'

def test_empty_pure_editing_and_real_transport(app,bridge):
    assert bridge.command('project.pureEditing',{'durationSeconds':20.})
    assert bridge.media['mediaKind']=='virtual' and bridge.media['totalFrames']==2000
    assert bridge.command('timeline.createRange',{'first':126,'last':150})
    assert bridge.document.project.mappings[0].start==1.25
    assert bridge.command('media.seekSeconds',{'seconds':1.25})
    assert bridge.media['positionSeconds']==1.25
    assert bridge.command('media.step',{'delta':1})
    assert bridge.media['positionSeconds']==1.26
    assert bridge.command('media.play',{})
    wait(app,lambda:bridge.media['positionSeconds']>1.3)
    assert bridge.command('media.pause',{})
    assert not bridge.media['playing']

def test_lrc_offsets_repeats_unknown_tail_and_blank_end(tmp_path):
    path=tmp_path/'song.lrc'
    path.write_text('[offset:100]\n[00:01.123][00:03.12]Repeat\n[00:05.000]Last\n',encoding='utf-8')
    rows=import_translation(path)
    assert [(r.start,r.end) for r in rows]==[(1.223,3.22),(3.22,5.1),(5.1,None)]
    assert len({r.id for r in rows})==3
    path.write_text('[00:01.000]A\n[00:02.000]\n',encoding='utf-8')
    assert import_translation(path)[0].end==2.
    for content in ('[00:99.0]bad','[offset:-2000]\n[00:01.0]bad','[00:01.0]<00:01.1>word'):
        path.write_text(content,encoding='utf-8')
        with pytest.raises(ValueError):import_translation(path)

def test_real_waveform_fft_silence_and_cancel(tmp_path):
    path=wav(tmp_path/'tone.wav')
    result=analyze_audio(path,2.)
    assert result['status']=='ready'
    assert len(result['waveform'])==16384 and 1<=len(result['spectrum'])<=2048
    assert all(len(column)==128 for column in result['spectrum'])
    assert max(v[1] for v in result['waveform'])>.65
    spectrum=np.array(result['spectrum']).mean(axis=0)
    assert 30<=int(spectrum.argmax())<=32  # Actual 1 kHz peak.
    silence=analyze_audio(wav(tmp_path/'silence.wav',amplitude=0),2.)
    assert not np.array(silence['waveform']).any() and not np.array(silence['spectrum']).any()
    with pytest.raises(InterruptedError):analyze_audio(path,2.,lambda:True)

def test_audio_load_seek_step_analysis_save_reopen(app,bridge,tmp_path):
    path=wav(tmp_path/'纯音频.wav')
    assert bridge.command('media.open',{'path':str(path)})
    wait(app,lambda:bridge.state['audioAnalysis']['status'] in ('ready','error'))
    assert bridge.state['audioAnalysis']['status']=='ready',bridge.state['audioAnalysis']
    assert bridge.media['mediaKind']=='audio'
    assert bridge.media['width']==0 and bridge.media['decodedFrames']==0
    assert 'video' not in bridge.document.project.inputs
    assert bridge.command('media.seekSeconds',{'seconds':1.25})
    assert bridge.media['positionSeconds']==1.25
    assert bridge.command('media.step',{'delta':-1})
    assert bridge.media['positionSeconds']==1.24
    assert bridge.command('timeline.createRange',{'first':10,'last':30})
    identity=bridge.selected
    saved=tmp_path/'audio.lyria'
    assert bridge.command('project.saveAs',{'path':str(saved)})
    wait(app,lambda:saved.exists() and bridge.path==str(saved))
    bridge._new()
    assert bridge.command('project.open',{'path':str(saved)})
    wait(app,lambda:bridge.media['ready'] and bridge.state['audioAnalysis']['status']=='ready')
    assert bridge.media['mediaKind']=='audio' and bridge.document.project.mappings[0].id==identity
    # Explicitly muted offscreen playback validates transport, not audible device output.
    assert bridge.command('media.mute',{'value':True})
    assert bridge.command('media.play',{})
    wait(app,lambda:bridge.media['positionSeconds']>.05)
    assert bridge.command('media.pause',{})

def test_stale_audio_analysis_does_not_leak_into_new_project(app,bridge,tmp_path):
    path=wav(tmp_path/'tone.wav',duration=5.)
    assert bridge.command('media.open',{'path':str(path)})
    bridge._new()
    wait(app,lambda:not bridge.jobs.active)
    assert bridge.state['audioAnalysis']['status']=='idle'
    assert not bridge.media['ready']


def test_lrc_partial_end_bridge_save_and_atomic_oversize(app,bridge,tmp_path):
    path=tmp_path/'partial.lrc';path.write_text('[00:01.123]First\n[00:02.987]Tail\n',encoding='utf-8')
    assert bridge.command('input.import',{'role':'translation','path':str(path)})
    wait(app,lambda:not bridge.state['task']['running'])
    assert [(r.start,r.end) for r in bridge.document.project.mappings]==[(1.123,2.987),(2.987,None)]
    saved=tmp_path/'partial.lyria'
    assert bridge.command('project.saveAs',{'path':str(saved)})
    wait(app,lambda:bridge.path==str(saved))
    assert load_document(saved).project.mappings[1].end is None
    before=bridge.document.project.to_dict();depth=len(bridge.history.undo_stack)
    oversized=tmp_path/'oversized.srt';oversized.write_text('1\n24:00:00,000 --> 24:00:01,000\nToo late\n',encoding='utf-8')
    assert bridge.command('input.import',{'role':'translation','path':str(oversized)})
    wait(app,lambda:not bridge.state['task']['running'])
    assert bridge.document.project.to_dict()==before and len(bridge.history.undo_stack)==depth

def test_undo_virtual_creation_restores_transport(app,bridge):
    assert bridge.command('project.pureEditing',{'durationSeconds':1.})
    assert bridge.media['durationSeconds']==1.
    assert bridge.command('history.undo',{})
    assert not bridge.media['ready']
    assert bridge.command('history.redo',{})
    assert bridge.media['mediaKind']=='virtual' and bridge.media['durationSeconds']==1.


@pytest.mark.parametrize('codec,suffix',[('libmp3lame','.mp3'),('flac','.flac')])
def test_real_compressed_audio_import(app,bridge,tmp_path,codec,suffix):
    import av
    path=tmp_path/('compressed'+suffix)
    rate=16000
    samples=(np.sin(np.arange(rate)*2*np.pi*440/rate)*.5).astype(np.float32)
    with av.open(str(path),'w') as container:
        stream=container.add_stream(codec,rate=rate);stream.layout='mono'
        frame=av.AudioFrame.from_ndarray(samples[None,:],format='fltp',layout='mono');frame.sample_rate=rate
        for packet in stream.encode(frame):container.mux(packet)
        for packet in stream.encode(None):container.mux(packet)
    assert bridge.command('media.open',{'path':str(path)})
    wait(app,lambda:bridge.state['audioAnalysis']['status'] in ('ready','error'))
    assert bridge.state['audioAnalysis']['status']=='ready',bridge.state['error']
    assert bridge.media['mediaKind']=='audio' and bridge.media['totalFrames']>=100
    assert bridge.command('media.seekSeconds',{'seconds':.5})
    assert bridge.media['positionSeconds']==.5


def test_full_24_hour_tail_save_reopen_preserves_boundaries(app,bridge,tmp_path):
    assert bridge.command('project.pureEditing',{'durationSeconds':86400.})
    assert bridge.command('timeline.createRange',{'first':8639501,'last':8640000})
    identity=bridge.selected
    assert [(r.start,r.end) for r in bridge.document.project.mappings]==[(86395.,86400.)]
    saved=tmp_path/'full-day.lyria'
    assert bridge.command('project.saveAs',{'path':str(saved)})
    wait(app,lambda:bridge.path==str(saved))
    bridge._new()
    assert bridge.command('project.open',{'path':str(saved)})
    wait(app,lambda:bridge.media['ready'])
    assert bridge.media['durationSeconds']==86400.
    assert [(r.id,r.start,r.end) for r in bridge.document.project.mappings]==[(identity,86395.,86400.)]
    assert not bridge.state['error']

def test_full_24_hour_srt_import_preserves_tail(app,bridge,tmp_path):
    path=tmp_path/'tail.srt';path.write_text('1\n23:59:55,123 --> 24:00:00,000\nTail\n',encoding='utf-8')
    assert bridge.command('input.import',{'role':'translation','path':str(path)})
    wait(app,lambda:not bridge.state['task']['running'])
    assert not bridge.state['error']
    assert bridge.media['durationSeconds']==86400.
    assert [(r.start,r.end) for r in bridge.document.project.mappings]==[(86395.123,86400.)]


@pytest.mark.parametrize('suffix,codec',[('.mp3','libmp3lame'),('.m4a','aac')])
def test_real_audio_with_embedded_cover_is_not_video(app,bridge,tmp_path,suffix,codec):
    import av
    import shutil
    import subprocess
    from PIL import Image
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('Local FFmpeg required to author attached-picture fixture')
    source=wav(tmp_path/'source.wav')
    cover=tmp_path/'cover.png';Image.new('RGB',(64,64),(90,30,180)).save(cover)
    target=tmp_path/('album'+suffix)
    completed=subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-y',
        '-i',str(source),'-i',str(cover),'-map','0:a','-map','1:v',
        '-c:a',codec,'-c:v','png','-disposition:v','attached_pic',str(target)],
        capture_output=True,text=True,timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    assert completed.returncode==0,completed.stderr
    with av.open(str(target)) as container:
        assert container.streams.audio and container.streams.video
        assert container.streams.video[0].disposition & av.stream.Disposition.attached_pic
    assert bridge.command('media.open',{'path':str(target)})
    wait(app,lambda:bridge.state['audioAnalysis']['status'] in ('ready','error'))
    assert bridge.state['audioAnalysis']['status']=='ready',bridge.state['error']
    assert bridge.media['mediaKind']=='audio' and bridge.media['width']==0
    assert bridge.media['decodedFrames']==0
    assert bridge.command('media.seekSeconds',{'seconds':1.25})
    assert bridge.media['positionSeconds']==1.25


def test_reopen_explicit_pure_project_does_not_rewrite_data(app,bridge,tmp_path):
    assert bridge.command('project.pureEditing',{'durationSeconds':300.})
    assert bridge.command('timeline.createRange',{'first':100,'last':29990})
    before=bridge.document.project.to_dict()
    saved=tmp_path/'unchanged-pure.lyria'
    assert bridge.command('project.saveAs',{'path':str(saved)})
    wait(app,lambda:bridge.path==str(saved))
    bridge._new()
    assert bridge.command('project.open',{'path':str(saved)})
    wait(app,lambda:bridge.media['ready'])
    assert bridge.document.project.to_dict()==before
    assert bridge.media['durationSeconds']==300.
