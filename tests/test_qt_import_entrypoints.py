"""Real media import through the file-picker boundary; no native UI or OCR."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from pathlib import Path
import shutil
import time
import pytest
from PySide6.QtWidgets import QApplication, QFileDialog
from desktop_qt.controller import Bridge
from lyricsync.model import Mapping, Translation


@pytest.fixture(scope='module')
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def bridge(application,tmp_path):
    instance=Bridge(storage_dir=tmp_path/'recovery')
    yield instance
    instance.shutdown()
    deadline=time.monotonic()+3
    while instance.media_controller.worker.thread.is_alive() and time.monotonic()<deadline:
        application.processEvents();time.sleep(.005)


def wait_for(application,predicate,timeout=8):
    deadline=time.monotonic()+timeout
    while not predicate() and time.monotonic()<deadline:
        application.processEvents();time.sleep(.005)
    assert predicate(),'timed out waiting for decoded media'


def fixture_video(tmp_path,name):
    source=Path(__file__).resolve().parents[1]/'examples/synthetic_mv.mp4'
    target=tmp_path/name;shutil.copy2(source,target)
    return target


@pytest.mark.parametrize('filename',['picked clip.mp4','中文 日本語 片段.mp4'])
@pytest.mark.parametrize('entrypoint',['file-dialog','explicit-path'])
def test_imported_frame_is_available_to_manual_ocr(application,bridge,tmp_path,monkeypatch,filename,entrypoint):
    video=fixture_video(tmp_path,filename)
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a,**k:(video.as_posix(),'Video'))
    indexed=[]
    bridge.media_controller.loaded.connect(lambda metadata,index:indexed.append(dict(metadata)))
    payload={} if entrypoint=='file-dialog' else {'path':video.as_posix()}
    assert bridge.command('media.open',payload),bridge.state['error']
    wait_for(application,lambda:bridge.media['ready'] and bridge.media['displayedFrame']>0)
    assert indexed and indexed[-1]['width']>0 and indexed[-1]['height']>0
    ok=bridge.command('roi.set',{'x':.1,'y':.6,'width':.8,'height':.3})
    assert ok,('decoded frame exists but manual OCR rejected it: '+bridge.state['error']+
        '; project.video='+repr(bridge.document.project.video)+
        '; project.path='+repr(bridge.document.project.inputs.get('video'))+
        '; media.path='+repr(bridge.media_controller.path))
    assert bridge.document.project.video['width']==indexed[-1]['width']
    assert bridge.document.project.video['height']==indexed[-1]['height']
    assert bridge.document.project.settings['roi'][2]>bridge.document.project.settings['roi'][0]


def test_picker_reselecting_same_video_preserves_existing_cues(application,bridge,tmp_path,monkeypatch):
    video=fixture_video(tmp_path,'same clip.mp4')
    assert bridge.command('media.open',{'path':str(video)})
    wait_for(application,lambda:bridge.media['ready'] and bridge.media['displayedFrame']>0)
    project=bridge.document.project
    project.translations=[Translation('t','测试字幕')]
    project.mappings=[Mapping('m',['t'],start=.2,end=1.,confirmed=True)]
    bridge._changed()
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a,**k:(video.as_posix(),'Video'))
    assert bridge.command('media.open',{}),bridge.state['error']
    wait_for(application,lambda:bridge.media['ready'] and bridge.media['displayedFrame']>0)
    assert [(m.id,m.start,m.end,m.confirmed) for m in project.mappings]==[('m',.2,1.,True)]


def test_missing_video_does_not_change_document_or_history(bridge,tmp_path,monkeypatch):
    missing=tmp_path/'missing clip.mp4';assert not missing.exists()
    before=bridge.document.snapshot();history=len(bridge.history.undo_stack);stage=bridge.stage
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a,**k:(missing.as_posix(),'Video'))
    assert not bridge.command('media.open',{})
    assert bridge.document.snapshot()==before
    assert len(bridge.history.undo_stack)==history and bridge.stage==stage


def test_legacy_project_path_receives_real_index_metadata(application,bridge,tmp_path):
    video=fixture_video(tmp_path,'legacy path.mp4')
    bridge.document.project.inputs['video']=video.as_posix()
    bridge.media_controller.open(str(video.resolve()))
    wait_for(application,lambda:bridge.media['ready'] and bridge.media['displayedFrame']>0)
    assert bridge.document.project.video.get('width',0)>0
    assert bridge.command('roi.set',{'x':0.,'y':.5,'width':1.,'height':.4}),bridge.state['error']


def test_cancelled_picker_keeps_document_and_history(bridge,monkeypatch):
    before=bridge.document.snapshot();history=len(bridge.history.undo_stack)
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a,**k:('',''))
    assert bridge.command('media.open',{})
    assert bridge.document.snapshot()==before
    assert len(bridge.history.undo_stack)==history


def test_other_video_index_cannot_update_current_document(bridge,tmp_path):
    bridge.document.project.inputs['video']=str(tmp_path/'current.mp4')
    bridge.media_controller.path=str(tmp_path/'stale.mp4')
    before=bridge.document.snapshot()
    bridge._media_loaded({'width':960,'height':540,'content_hash':'stale'},None)
    assert bridge.document.snapshot()==before
