"""Independent timeline views are explicit and browsing cannot mutate main."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from copy import deepcopy
from pathlib import Path
import time
import pytest
from PySide6.QtWidgets import QApplication
from desktop_qt.controller import Bridge
from desktop_qt.timeline_views import archive_main, archives, timeline_choices
from lyricsync.model import Mapping, Project, Translation
from lyricsync.project_document import ProjectDocument, write_document, load_document


@pytest.fixture
def bridge(tmp_path):
    app=QApplication.instance() or QApplication([])
    b=Bridge(storage_dir=tmp_path/'storage')
    b.document.project.translations=[Translation('t','保留译文')]
    b.document.project.mappings=[Mapping('m',['t'],start=1.,end=2.,human_edited=True)]
    b._changed()
    yield b
    b.shutdown()
    until=time.monotonic()+3
    while b.media_controller.worker.thread.is_alive() and time.monotonic()<until:
        app.processEvents();time.sleep(.005)


def test_single_cue_edit_does_not_create_timeline_choice(bridge):
    assert not bridge.state['timelineSwitchAvailable']
    bridge.document.project.mappings[0].start=1.5;bridge._changed()
    assert not bridge.state['timelineSwitchAvailable']


def test_explicit_manual_copy_browse_save_reopen_and_undo(bridge,tmp_path):
    original=deepcopy(bridge.document.project.to_dict())
    assert bridge.command('timeline.copyManual',{})
    assert bridge.state['timelineSwitchAvailable']
    record=archives(bridge.document.project)[0]
    bridge.document.project.mappings[0].start=1.7
    bridge._changed()
    before=deepcopy(bridge.document.snapshot())
    bridge.switchCandidateSource('saved:'+record['id'])
    assert bridge.state['candidateReadOnly']
    assert bridge._display_project().mappings[0].start==1.
    assert bridge.document.snapshot()==before
    bridge.switchCandidateSource('main')
    assert bridge.document.project.mappings[0].start==1.7
    path=tmp_path/'views.lyria'
    write_document(bridge.document.snapshot(),path)
    loaded=load_document(path)
    assert len(timeline_choices(loaded))==2
    assert archives(loaded.project)[0]['project']['translations']==original['translations']
    assert bridge.command('history.undo',{})
    assert bridge.document.project.to_dict()==original


def test_new_timed_import_preserves_previous_user_axis(bridge,tmp_path):
    bridge.document.project.settings['pending_text_splits']=[{'left':'m','right':''}]
    bridge.document.project.settings['manual_locked_ids']=['m']
    path=tmp_path/'new.srt'
    path.write_text('1\n00:00:05,000 --> 00:00:06,000\nnew subtitle\n',encoding='utf-8')
    assert bridge.command('input.import',{'role':'translation','path':str(path)})
    app=QApplication.instance();until=time.monotonic()+5
    while bridge._task.get('running') and time.monotonic()<until:
        app.processEvents();time.sleep(.005)
    assert not bridge.state['error'],bridge.state['error']
    assert bridge.document.project.mappings[0].start==5.
    assert archives(bridge.document.project)[0]['project']['mappings'][0]['start']==1.
    assert not bridge.document.project.settings.get('pending_text_splits')
    assert not bridge.document.project.settings.get('manual_locked_ids')
    assert archives(bridge.document.project)[0]['project']['settings']['pending_text_splits']==[{'left':'m','right':''}]
    assert bridge.state['timelineSwitchAvailable']


def test_first_unapplied_candidate_on_edited_main_is_visible(bridge):
    from lyricsync.candidate_tracks import capture_source,candidate_from_project
    snapshot=capture_source(bridge.document,'vocal')
    generated=deepcopy(bridge.document.project)
    generated.mappings[0].start=5.;generated.mappings[0].end=6.
    record=candidate_from_project(snapshot,generated)
    bridge._candidate_install(record,initial_complete=False)
    assert bridge.document.project.mappings[0].start==1.
    assert bridge.state['timelineSwitchAvailable']
    assert 'vocal' in [item['id'] for item in bridge.state['timelineChoices']]
    bridge.switchCandidateSource('vocal')
    assert bridge.state['candidateAvailable'] and bridge.state['candidateReadOnly']
    assert bridge.document.project.mappings[0].start==1.


def test_archived_source_remains_protected_for_export_and_save(bridge,tmp_path):
    from lyricsync.storage import protected_project_inputs, protect_output
    source=tmp_path/'original.srt';source.write_text('original source',encoding='utf-8')
    bridge.document.project.inputs['translation']=str(source)
    archive_main(bridge.document.project,'before import')
    bridge.document.project.inputs['translation']=str(tmp_path/'second.srt')
    before=source.read_bytes()
    with pytest.raises(ValueError):bridge._protect_artifact_output(source)
    with pytest.raises(ValueError):write_document(bridge.document.snapshot(),source)
    with pytest.raises(ValueError):protect_output(source,protected_project_inputs(bridge.document.project))
    assert source.read_bytes()==before
