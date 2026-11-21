"""Isolated Qt/offscreen coverage, not Windows foreground acceptance."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from copy import deepcopy
from pathlib import Path
import json
import pytest
from PySide6.QtCore import QObject,Qt,QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QFileDialog
from desktop_qt.controller import Bridge
from desktop_qt.file_dialog_history import FileDialogHistory
from desktop_qt.diagnostics import DiagnosticStore,clean_event,now
from lyricsync.model import Mapping,Translation
from tools.qt_event_loop_wait import delay

@pytest.fixture(scope='module')
def app():
    QQuickStyle.setStyle('Basic')
    return QApplication.instance() or QApplication([])

@pytest.fixture
def bridge(app,tmp_path):
    b=Bridge(storage_dir=tmp_path/'isolated',startup=True)
    yield b
    b.shutdown();b.wait_for_shutdown(app)


def test_empty_chosen_workspace_can_return_from_home(bridge):
    assert not bridge.state['canResumeProject']
    assert bridge.command('editor.start',{'mode':'ocr'})
    assert bridge.command('stage.set',{'stage':'review'})
    before=bridge.document.snapshot()
    assert bridge.command('project.home',{})
    assert bridge.state['canResumeProject']
    assert bridge.command('project.resume',{})
    assert bridge.stage=='review' and not bridge.state['startupVisible']
    assert bridge.document.snapshot()==before


def test_clear_selected_reference_keeps_edits_files_and_is_undoable(bridge,tmp_path):
    file=tmp_path/'lyrics.txt';file.write_text('original',encoding='utf-8')
    p=bridge.document.project
    p.inputs['translation']=str(file);p.translations=[Translation('t','manual translation')]
    p.mappings=[Mapping('m',['t'],start=1.,end=2.,human_edited=True,confirmed=True)]
    bridge._changed();before=bridge.document.snapshot()
    assert bridge.command('input.clear',{'role':'translation'})
    assert 'translation' not in p.inputs and file.read_text()=='original'
    assert not p.translations and p.mappings[0].text_override=='manual translation' and p.mappings[0].confirmed
    assert p.settings['timeline_archives'][-1]['project']['translations'][0]['text']=='manual translation'
    assert bridge.command('history.undo',{})
    assert bridge.document.project.inputs['translation']==str(file)
    assert bridge.document.project.translations[0].text=='manual translation'


def test_clear_media_retains_times_and_does_not_delete_file(bridge,tmp_path):
    file=tmp_path/'original.mp4';file.write_bytes(b'not decoded')
    p=bridge.document.project;p.inputs['video']=str(file)
    p.video={'content_hash':'original-fingerprint'}
    p.mappings=[Mapping('m',[],text_override='keep',start=1.,end=2.,human_edited=True)]
    bridge._changed()
    assert bridge.command('input.clear',{'role':'media'})
    assert not p.inputs and file.read_bytes()==b'not decoded'
    assert p.video=={} and p.settings['media_detached_with_timing'] is True
    assert (p.mappings[0].start,p.mappings[0].end,p.mappings[0].text_override)==(1.,2.,'keep')
    assert bridge.media['mediaKind']=='virtual'
    assert bridge.command('input.clear',{'role':'media'})


def test_clear_busy_is_noop(bridge):
    bridge.document.project.inputs['ktv']='source.txt'
    before=bridge.document.snapshot();bridge._task['running']=True
    assert not bridge.command('input.clear',{'role':'source'})
    assert bridge.document.snapshot()==before
    bridge._task['running']=False


def test_picker_remembers_shared_import_folder_and_cancel_keeps_it(bridge,tmp_path,monkeypatch):
    folder=tmp_path/'素材';folder.mkdir();file=folder/'lyrics.txt';file.write_text('line')
    calls=[]
    def choose(*args):calls.append(args);return (str(file),'')
    monkeypatch.setattr(QFileDialog,'getOpenFileName',choose)
    assert bridge._path({},category='import')==str(file)
    bridge._path({},category='import')
    assert calls[-1][2]==str(folder)
    saved=FileDialogHistory(bridge.storage_dir)
    assert saved.initial('import')==str(folder)
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *args:('',''))
    assert bridge._path({},category='import')==''
    assert saved.initial('import')==str(folder)
    saved.folders['import']=str(tmp_path/'missing')
    assert saved.initial('import')==str(Path.home())


def test_diagnostics_safe_stage_code_exclude_untrusted_fields(tmp_path):
    store=DiagnosticStore(tmp_path/'log')
    store.record('worker-failed',FileNotFoundError('SECRET /private/path'),stage='alignment')
    event=store.snapshot()[-1]
    assert event['stage']=='alignment' and event['code']=='file_missing'
    assert 'SECRET' not in json.dumps(event)
    bad=clean_event({'time':now(),'event':'worker-failed','stage':['SECRET'],'code':'SECRET'})
    assert 'stage' not in bad and 'code' not in bad


def test_settings_escape_retains_changes_and_child_dialog_has_priority(app,bridge):
    engine=QQmlApplicationEngine();warnings=[]
    engine.warnings.connect(lambda msgs:warnings.extend(m.toString() for m in msgs))
    engine.rootContext().setContextProperty('bridge',bridge)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1]/'desktop_qt/qml/Main.qml')))
    assert engine.rootObjects(),warnings
    window=engine.rootObjects()[0]
    try:
        window.setProperty('workspace','qc');window.setProperty('workspace','settings');delay(350)
        page=window.findChild(QObject,'settingsPage');page.forceActiveFocus()
        assert bridge.command('settings.set',{'key':'motion','value':False})
        before=bridge.document.snapshot()
        QTest.keyClick(window,Qt.Key_Escape);delay(50)
        assert window.property('workspace')=='qc'
        assert bridge.document.snapshot()==before
        assert not bridge.document.project.settings['motion']
        window.setProperty('workspace','settings');page.forceActiveFocus();delay(50)
        # QML child dialog owns Escape: first key closes it, not settings.
        dialog=window.findChild(QObject,'closeDialog')
        assert dialog
        # A close-protection dialog has NoAutoClose by design; Esc must not exit settings behind it.
        dialog.setProperty('visible',True);delay(50)
        QTest.keyClick(window,Qt.Key_Escape);delay(50)
        assert window.property('workspace')=='settings'
        dialog.setProperty('visible',False)
    finally:
        window.setProperty('allowClose',True);window.close();engine.deleteLater();app.processEvents()
    assert not warnings,warnings


def test_progress_signal_preserves_human_summary_and_details(app,bridge):
    from lyricsync.api_client import batch_progress,transport_progress
    identity='controlled-progress'
    bridge._task={'id':identity,'running':True,'api':True}
    label=batch_progress(transport_progress({'phase':'read_stream','received_bytes':50,'heartbeat_count':2}),1,4,2,9,'safe diagnostic counters')
    bridge.jobs.progressed.emit(identity,.2,label);app.processEvents()
    assert bridge.state['task']['label']=='正在核对第 2 / 9 批 · 等待回复内容'
    assert bridge.state['task']['details']=='safe diagnostic counters'
    bridge._task={'id':'','running':False}


def test_clear_media_preflight_failure_is_atomic(bridge):
    p=bridge.document.project;p.inputs['video']='preserved.mp4'
    p.mappings=[Mapping('long',[],text_override='keep',start=86400.,end=86401.)]
    before=bridge.document.snapshot();history=deepcopy(bridge.history.undo_stack)
    assert not bridge.command('input.clear',{'role':'media'})
    assert bridge.document.snapshot()==before and bridge.history.undo_stack==history


@pytest.mark.parametrize('duration',[-1,0,float('inf'),float('nan'),'invalid'])
def test_clear_media_invalid_duration_preserves_project_history_and_player(bridge,duration):
    bridge.media_controller.open_virtual(300.)
    p=bridge.document.project;p.inputs['video']='preserved.mp4'
    p.settings['editing_duration']=duration
    p.settings['roi']=[0,100,1920,200]
    before=bridge.document.snapshot();history=deepcopy(bridge.history.undo_stack)
    media=deepcopy(bridge.media);index=bridge.media_controller.index
    assert not bridge.command('input.clear',{'role':'media'})
    assert bridge.document.snapshot()==before
    assert bridge.history.undo_stack==history
    assert bridge.media==media and bridge.media_controller.index is index


@pytest.fixture(autouse=True)
def explicit_detach_choice(monkeypatch):
    from desktop_qt import dialogs
    monkeypatch.setattr(dialogs,'choose_asset_clear',lambda:'detach')
