"""Dev7 F01/F03/F06/F07/F10, offline Qt. Never opens a user's window/model."""
from copy import deepcopy
from pathlib import Path
import threading
import pytest
from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtWidgets import QApplication, QMessageBox, QDialog, QComboBox, QLabel
from PySide6.QtQml import QQmlApplicationEngine
from test_dev5_feedback_navigation import app, bridge
from test_qt_entry_initialization_dev5 import wait
from lyricsync.model import Mapping, Translation
from lyricsync.local_models import UVR, UVR_HQ1
from tools.qt_event_loop_wait import delay
from test_qt_startup_phase5 import find


def seed(bridge,tmp_path):
    p=bridge.document.project
    source=tmp_path/'source.txt';source.write_text('source',encoding='utf-8')
    p.inputs={'ktv':str(source)}
    p.translations=[Translation('t','translated')]
    p.mappings=[Mapping('m',['t'],text_override='manual',start=.2,end=.8,confirmed=True,human_edited=True)]
    p.settings['pending_text_splits']=[{'left':'m','right':''}]
    p.settings['manual_locked_ids']=['m']
    bridge._has_workspace=True;bridge._changed()
    return source


@pytest.mark.parametrize('mode',['audio','edit','separate'])
def test_mode_cancel_preserves_everything(bridge,tmp_path,monkeypatch,mode):
    seed(bridge,tmp_path);before=bridge.document.snapshot();history=deepcopy(bridge.history.undo_stack)
    monkeypatch.setattr('desktop_qt.dialogs.choose_asset_clear',lambda:'cancel')
    assert not bridge.command('editor.start',{'mode':mode})
    assert bridge._entry_mode=='ocr' and bridge.document.snapshot()==before
    assert bridge.history.undo_stack==history


@pytest.mark.parametrize('mode',['audio','edit','separate'])
def test_detach_preserves_timeline_undo_and_repeat_are_safe(bridge,tmp_path,monkeypatch,mode):
    source=seed(bridge,tmp_path);before=bridge.document.project.to_dict();identity=bridge.document.id
    calls=[];monkeypatch.setattr('desktop_qt.dialogs.choose_asset_clear',lambda:calls.append(True) or 'detach')
    assert bridge.command('editor.start',{'mode':mode})
    assert bridge._entry_mode=='edit' and bridge.document.project.to_dict()['mappings'][0]==before['mappings'][0]
    assert bridge.document.id==identity and bridge.document.project.inputs==before['inputs']
    assert bridge.document.project.translations[0].text=='translated' and source.read_text()=='source'
    assert bridge.document.project.settings['pending_text_splits']==before['settings']['pending_text_splits']
    bridge.document.validate_project(bridge.document.project)
    assert bridge.command('editor.start',{'mode':'edit'}) and len(calls)==1
    assert bridge.command('history.undo',{})
    assert bridge.document.project.to_dict()==before
    assert bridge.document.main_timeline[0].confirmed and bridge._entry_mode=='ocr'
    assert bridge.command('history.redo',{}) and bridge._entry_mode=='edit'
    assert bridge.document.project.to_dict()['mappings'][0]==before['mappings'][0]
    assert bridge.command('history.undo',{}) and bridge._entry_mode=='ocr'
    assert bridge.command('project.home',{}) and bridge.command('project.resume',{})
    assert bridge.document.project.to_dict()==before


def test_mode_reentry_newer_edit_and_busy_are_not_cleared(bridge,tmp_path,monkeypatch):
    seed(bridge,tmp_path)
    def confirm():
        assert not bridge.command('editor.start',{'mode':'edit'})
        bridge.document.project.mappings[0].text_override='newer';bridge._changed()
        return 'detach'
    monkeypatch.setattr('desktop_qt.dialogs.choose_asset_clear',confirm)
    assert not bridge.command('editor.start',{'mode':'audio'})
    assert bridge._entry_mode=='ocr' and bridge.document.main_timeline[0].text_override=='newer'
    bridge._task['running']=True
    assert not bridge.command('editor.start',{'mode':'separate'})
    bridge._task['running']=False


@pytest.mark.parametrize('action',['stay','close','escape','clear'])
def test_native_confirmation_requires_only_explicit_clear(app,action):
    from desktop_qt.dialogs import confirm_mode_switch
    def choose():
        box=QApplication.activeModalWidget();assert isinstance(box,QMessageBox)
        assert '将清空当前时间轴' in box.text()
        assert box.defaultButton().text()=='留在原模式'
        assert box.escapeButton().text()=='留在原模式'
        if action=='clear':box.findChild(QObject,'clearTimelineAndSwitch').click()
        elif action=='stay':box.findChild(QObject,'stayInCurrentMode').click()
        elif action=='escape':box.reject()
        else:box.close()
    QTimer.singleShot(0,choose)
    assert confirm_mode_switch() is (action=='clear')


@pytest.mark.parametrize('available',[True,False])
def test_ocr_device_label_and_missing_backend_are_truthful(bridge,monkeypatch,available):
    from desktop_qt.dialogs import ocr_settings
    bridge.document.project.settings['ocr_device']='directml';before=bridge.document.snapshot()
    def inspect(dialog):
        device=dialog.findChild(QComboBox,'ocrComputeDevice')
        assert device.findData('cpu')>=0
        if available:assert device.itemText(device.findData('directml'))=='显卡加速（DirectML）'
        else:
            assert device.findData('directml')==-1 and device.currentData()=='cpu'
            assert '不可用' in dialog.findChild(QLabel,'ocrDeviceAvailability').text()
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog,'exec',inspect)
    ocr_settings(bridge,['CPUExecutionProvider']+(['DmlExecutionProvider'] if available else []))
    assert bridge.document.snapshot()==before


def test_export_checkmark_is_visual_and_role_caption_removed(app,bridge):
    p=bridge.document.project;p.mappings=[Mapping('source',[],text_override='original',text_role='source_import',start=.2,end=.8)]
    bridge._startup_visible=False;bridge.stage='review';bridge._changed();before=bridge.document.snapshot()
    engine=window=None
    try:
        engine=QQmlApplicationEngine();warnings=[];engine.warnings.connect(lambda msgs:warnings.extend(m.toString() for m in msgs))
        engine.rootContext().setContextProperty('bridge',bridge)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1]/'desktop_qt/qml/Main.qml')))
        assert engine.rootObjects(),warnings
        window=engine.rootObjects()[0]
        delay(80)
        assert window.findChild(QObject,'mainTextRole_source') is None
        assert bridge.state['rows'][0]['textRole']=='source_import'
        assert not bridge.state['rows'][0]['confirmed']
        assert bridge.command('stage.set',{'stage':'export'});delay(40)
        assert find(window,'stageButton_review').property('completed')
        assert not bridge.state['stageCompletion']['review']
        assert bridge.document.snapshot()==before
        assert bridge.command('stage.set',{'stage':'review'});delay(40)
        assert not find(window,'stageButton_review').property('completed')
        assert find(window,'startupEntry_separate') is not None
    finally:
        from PySide6.QtCore import QEvent
        from tools.qt_event_loop_wait import wait_until
        bridge.shutdown();bridge.wait_for_shutdown(app)
        bridge.media_controller.worker.thread.join(10)
        assert not bridge.media_controller.worker.thread.is_alive()
        if window is not None:window.setProperty('allowClose',True);window.close()
        if engine is not None:
            destroyed=[];engine.destroyed.connect(lambda:destroyed.append(True))
            engine.deleteLater();app.sendPostedEvents(None,QEvent.Type.DeferredDelete)
            wait_until(lambda:bool(destroyed))
        app.processEvents()
    assert not warnings,warnings


@pytest.fixture
def separation(app,tmp_path,monkeypatch):
    from desktop_qt.local_model_tasks import ModelTasks
    from desktop_qt.separation_dialog import SeparationDialog
    service=ModelTasks(app)
    monkeypatch.setattr('desktop_qt.separation_dialog.model_tasks',lambda:service)
    dialog=SeparationDialog(default_audio=str(tmp_path/'audio.wav'),initial_options={'cache_root':str(tmp_path/'models'),'device':'cpu'})
    yield dialog,service
    service.shutdown();wait(lambda:service.task is None)
    from shiboken6 import isValid
    if isValid(dialog):dialog.reject();dialog.deleteLater()
    service.deleteLater();app.processEvents()


def result(tmp_path,model=UVR.id):
    from lyricsync.separation_preview import file_stamp
    original=tmp_path/'audio.wav'
    if not original.exists():original.write_bytes(b'original')
    stem=tmp_path/(model+'.wav');stem.write_bytes(b'stem')
    return dict(kind='separation_preview',model_id=model,source_path=str(original),stem_path=str(stem),
        offset_seconds=12.,duration_seconds=5.,source_stamp=file_stamp(original),stem_stamp=file_stamp(stem),warning='test only')


def test_separation_without_lyrics_repeated_clicks_and_result_compare(separation,tmp_path,monkeypatch):
    dialog,service=separation;gate=threading.Event();started=threading.Event();calls=[]
    monkeypatch.setattr('desktop_qt.separation_dialog.validate_separation_options',lambda options:dict(options))
    def generate(*,options,cancel,progress):
        calls.append(options);started.set();assert gate.wait(5);return result(tmp_path,options['separation_model_id'])
    monkeypatch.setattr('desktop_qt.separation_dialog.generate_separation_preview',generate)
    try:
        dialog._generate();wait(started.is_set);dialog._generate();assert len(calls)==1
        assert not dialog.run.isEnabled();gate.set();wait(lambda:service.task is None)
        assert dialog.results.count()==1 and dialog._preview_player is None
        assert '未生成歌词时间轴' in dialog.status.text()
        dialog.separation_model.setCurrentIndex(dialog.separation_model.findData(UVR_HQ1.id))
        dialog._generate();wait(lambda:service.task is None)
        assert dialog.results.count()==2 and dialog._records[-1]['model_id']==UVR_HQ1.id
        played=[];monkeypatch.setattr(dialog,'_listen_audio',lambda record,vocals:played.append((record,vocals)))
        dialog.results.setCurrentIndex(0);dialog._listen(False);dialog._listen(True)
        assert [v for _,v in played]==[False,True]
        assert all(r['model_id']==UVR.id for r,_ in played)
    finally:gate.set()


@pytest.mark.parametrize('outcome',['cancel','failure'])
def test_failed_or_cancelled_separation_never_reports_success(separation,tmp_path,monkeypatch,outcome):
    dialog,service=separation;gate=threading.Event();started=threading.Event()
    monkeypatch.setattr('desktop_qt.separation_dialog.validate_separation_options',lambda options:options)
    def generate(**kwargs):
        started.set();assert gate.wait(5)
        if outcome=='failure':raise RuntimeError('test separation failed')
        return result(tmp_path)  # late success after cancellation must not be accepted
    monkeypatch.setattr('desktop_qt.separation_dialog.generate_separation_preview',generate)
    try:
        dialog._generate();wait(started.is_set)
        if outcome=='cancel':service.stop('cancel')
        gate.set();wait(lambda:service.task is None)
        assert not service.separation_results and not dialog._records
        assert ('已取消' if outcome=='cancel' else 'failed') in dialog.status.text()
        assert dialog.run.isEnabled()
    finally:gate.set()


def test_close_does_not_cancel_and_reopen_shows_result(separation,tmp_path,monkeypatch):
    dialog,service=separation;gate=threading.Event();started=threading.Event()
    monkeypatch.setattr('desktop_qt.separation_dialog.validate_separation_options',lambda options:options)
    def generate(**kwargs):started.set();assert gate.wait(5);return result(tmp_path)
    monkeypatch.setattr('desktop_qt.separation_dialog.generate_separation_preview',generate)
    try:
        dialog._generate();wait(started.is_set);dialog.reject()
        assert not service.stop_reason;gate.set();wait(lambda:service.task is None)
        from desktop_qt.separation_dialog import SeparationDialog
        reopened=SeparationDialog(initial_options={'cache_root':str(tmp_path/'models')})
        assert reopened.results.count()==1 and reopened._preview_player is None
        reopened.reject();reopened.deleteLater()
    finally:gate.set()


def test_new_mode_requires_save_decision_before_source_reimport(bridge,tmp_path,monkeypatch):
    from test_qt_entry_initialization_dev5 import settled
    from lyricsync.model import Lyric
    source=tmp_path/'lyrics.txt';source.write_text('first original\nsecond original',encoding='utf-8')
    p=bridge.document.project;p.inputs['ktv']=str(source)
    p.lyrics=[Lyric('l','first original',None,None)]
    p.mappings=[Mapping('old',[],lyric_ids=['l'],text_override='edited original',text_role='source_import',start=1.,end=2.,human_edited=True)]
    bridge._has_workspace=True;bridge._changed();before=bridge.document.snapshot();identity=bridge.document.id
    monkeypatch.setattr('desktop_qt.dialogs.choose_asset_clear',lambda:'new')
    assert not bridge.command('editor.start',{'mode':'audio'})
    assert bridge.document.snapshot()==before and bridge.document.id==identity
    assert bridge._continuation is not None
    # Explicitly cancel the save/replacement guard; choosing New never discards by itself.
    assert bridge.command('close.resolve',{'choice':'cancel'})
    assert bridge.document.snapshot()==before and bridge.document.id==identity
    assert source.read_text(encoding='utf-8')=='first original\nsecond original'
    # Retry New and explicitly save the synthetic old project before replacement.
    saved=tmp_path/'preserved.lyria';bridge.path=str(saved)
    assert not bridge.command('editor.start',{'mode':'audio'})
    assert bridge.command('close.resolve',{'choice':'save'})
    settled(bridge)
    assert saved.is_file() and bridge.document.id==identity
    from lyricsync.project_document import load_document
    preserved=load_document(saved)
    assert preserved.project.mappings[0].text_override=='edited original'
    assert bridge._close_dialog['unexported'] and not bridge._close_dialog['dirty']
    # The old project is durably saved. Explicitly skip its separate subtitle export.
    assert bridge.command('close.resolve',{'choice':'discard'})
    assert bridge.document.id!=identity and bridge._entry_mode=='audio'
    assert not bridge.document.main_timeline
    assert bridge.command('input.import',{'role':'source','path':str(source)})
    settled(bridge)
    assert [m.text_override for m in bridge.document.main_timeline]==['first original','second original']
    assert source.read_text(encoding='utf-8')=='first original\nsecond original'


def test_changed_audio_is_not_presented_as_original_comparison(separation,tmp_path,monkeypatch):
    dialog,service=separation
    service._outcome(result(tmp_path),'');dialog._reload_results()
    Path(dialog._records[0]['source_path']).write_bytes(b'changed source')
    monkeypatch.setattr(dialog,'_listen_audio',lambda *a:pytest.fail('changed source played'))
    dialog._listen(False)
    assert '已变化' in dialog.status.text()


def test_separation_command_does_not_touch_existing_edits_or_completion(bridge,tmp_path,monkeypatch):
    seed(bridge,tmp_path);before=bridge.document.snapshot();completion=deepcopy(bridge.state['stageCompletion'])
    def fake_exec(dialog):
        # The dialog gets no document, so even a successful result cannot install rows.
        assert not hasattr(dialog,'document')
        dialog._service._outcome(result(tmp_path),'')
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr('desktop_qt.separation_dialog.SeparationDialog.exec',fake_exec)
    assert bridge.command('separation.open',{})
    assert bridge.document.snapshot()==before and bridge.state['stageCompletion']==completion


def test_failure_keeps_prior_audition_results(separation,tmp_path):
    dialog,service=separation
    service._outcome(result(tmp_path),'');before=deepcopy(service.separation_results)
    service._outcome(None,'new run failed')
    assert service.separation_results==before and dialog.results.count()==1
    assert 'new run failed' in dialog.status.text()


def test_initial_mode_selection_also_refuses_inflight_import(bridge,monkeypatch):
    assert not bridge._has_workspace and not bridge.document.main_timeline
    before=bridge.document.snapshot();bridge._task['running']=True
    monkeypatch.setattr('desktop_qt.dialogs.confirm_mode_switch',lambda:pytest.fail('busy initial entry must not prompt'))
    assert not bridge.command('editor.start',{'mode':'separate'})
    assert bridge.document.snapshot()==before and bridge._entry_mode=='ocr'
    bridge._task['running']=False


@pytest.mark.parametrize('outcome',['success','failure','cancel'])
def test_deleted_dialog_recovers_finished_separation_outcome(separation,tmp_path,monkeypatch,outcome):
    from PySide6.QtCore import QCoreApplication,QEvent
    from desktop_qt.separation_dialog import SeparationDialog
    dialog,service=separation;gate=threading.Event();started=threading.Event()
    monkeypatch.setattr('desktop_qt.separation_dialog.validate_separation_options',lambda options:options)
    def generate(**kwargs):
        started.set();assert gate.wait(5)
        if outcome=='failure':raise RuntimeError('EXPECTED BACKGROUND SEPARATOR FAILURE')
        return result(tmp_path)
    monkeypatch.setattr('desktop_qt.separation_dialog.generate_separation_preview',generate)
    try:
        dialog._generate();wait(started.is_set)
        dialog.reject();dialog.deleteLater();QCoreApplication.sendPostedEvents(dialog,QEvent.Type.DeferredDelete)
        if outcome=='cancel':service.stop('cancel')
        gate.set();wait(lambda:service.task is None)
        reopened=SeparationDialog(initial_options={'cache_root':str(tmp_path/'models')})
        try:
            if outcome=='success':
                assert '上次人声分离已完成' in reopened.status.text() and reopened.results.count()==1
            else:
                assert '上次人声分离未完成' in reopened.status.text() and reopened.results.count()==0
                assert ('已取消' if outcome=='cancel' else 'EXPECTED BACKGROUND SEPARATOR FAILURE') in reopened.status.text()
            assert reopened._preview_player is None
        finally:reopened.reject();reopened.deleteLater()
    finally:gate.set()


def test_reopen_does_not_label_model_download_failure_as_separation(separation,tmp_path):
    from desktop_qt.separation_dialog import SeparationDialog
    _,service=separation;service.action='download';service.error='DOWNLOAD FAILURE';service.message=service.error
    reopened=SeparationDialog(initial_options={'cache_root':str(tmp_path/'models')})
    try:
        assert reopened.status.text()=='就绪'
        assert '分离未完成' not in reopened.status.text()
    finally:reopened.reject();reopened.deleteLater()


def test_empty_mode_switch_cancel_preserves_pending_mark_confirm_clears_it(bridge,monkeypatch):
    assert bridge.command('project.newEntry',{'mode':'edit'})
    assert bridge.stage=='import'
    assert bridge.command('editor.continue',{})
    assert bridge.command('timeline.manualMode',{'enabled':True})
    assert bridge.command('media.seek',{'frame':10})
    assert bridge.command('timeline.manualMark',{})
    assert bridge._manual_start_frame==10 and not bridge.document.main_timeline
    before=bridge.document.snapshot();depth=len(bridge.history.undo_stack)
    monkeypatch.setattr('desktop_qt.dialogs.confirm_mode_switch',lambda *,pending_only: not pending_only)
    assert not bridge.command('editor.start',{'mode':'audio'})
    assert bridge._manual_start_frame==10 and bridge._manual_axis_mode and bridge._entry_mode=='edit'
    assert bridge.document.snapshot()==before and len(bridge.history.undo_stack)==depth
    monkeypatch.setattr('desktop_qt.dialogs.confirm_mode_switch',lambda *,pending_only: pending_only)
    assert bridge.command('editor.start',{'mode':'audio'})
    assert bridge._manual_start_frame is None and not bridge._manual_axis_mode
    after=bridge.document.snapshot()
    assert after['entry_mode']=='audio' and after['revision']>=before['revision']
    assert after==dict(before,entry_mode='audio',revision=after['revision']) and len(bridge.history.undo_stack)==depth+1
    assert bridge.command('editor.start',{'mode':'edit'})
    assert bridge._manual_start_frame is None and not bridge._manual_axis_mode
    assert bridge.command('timeline.manualMode',{'enabled':True})
    assert bridge.command('media.seek',{'frame':20})
    assert bridge.command('timeline.manualMark',{})
    assert bridge._manual_start_frame==20 and not bridge.document.main_timeline


def test_consecutive_empty_mode_changes_undo_in_order_without_dirty_revision(bridge,tmp_path,monkeypatch):
    assert bridge.command('project.newEntry',{'mode':'ocr'})
    original=bridge.document.project.to_dict()
    monkeypatch.setattr('desktop_qt.dialogs.confirm_mode_switch',lambda:True)
    monkeypatch.setattr('desktop_qt.dialogs.choose_asset_clear',lambda:pytest.fail('empty project has no protected axis'))
    assert bridge.command('editor.start',{'mode':'audio'})
    empty=bridge.document.snapshot();depth=len(bridge.history.undo_stack)
    assert bridge.command('editor.start',{'mode':'separate'})
    assert bridge.document.snapshot()==dict(empty,entry_mode='separate') and len(bridge.history.undo_stack)==depth+1
    assert bridge.command('history.undo',{})
    assert bridge._entry_mode=='audio' and bridge.document.snapshot()==empty
    assert bridge.command('history.redo',{})
    assert bridge._entry_mode=='separate' and bridge.document.snapshot()==dict(empty,entry_mode='separate')
    assert bridge.command('history.undo',{})
    assert bridge.command('history.undo',{})
    assert bridge._entry_mode=='ocr' and bridge.document.project.to_dict()==original
    assert bridge.command('history.redo',{}) and bridge._entry_mode=='audio'
    empty_again=bridge.document.snapshot()
    assert bridge.command('history.redo',{}) and bridge._entry_mode=='separate'
    assert bridge.document.snapshot()==dict(empty_again,entry_mode='separate')


@pytest.mark.parametrize('action',['cancel','close','escape','detach','new'])
def test_native_asset_choice_defaults_to_cancel(app,action):
    from desktop_qt.dialogs import choose_asset_clear
    errors=[]
    def choose():
        box=QApplication.activeModalWidget()
        try:
            assert isinstance(box,QMessageBox) and box.objectName()=='assetClearConfirmation'
            assert box.defaultButton().objectName()=='cancelAssetClear'
            assert box.escapeButton().objectName()=='cancelAssetClear'
            if action in ('cancel','detach','new'):
                box.findChild(QObject,{'cancel':'cancelAssetClear','detach':'detachAssetToEdit','new':'newProjectForAsset'}[action]).click()
            elif action=='escape':box.reject()
            else:box.close()
        except BaseException as error:
            errors.append(error)
            if box is not None:box.reject()
    QTimer.singleShot(0,choose)
    result=choose_asset_clear()
    if errors:raise errors[0]
    assert result==(action if action in ('detach','new') else 'cancel')
