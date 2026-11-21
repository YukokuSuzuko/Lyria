"""Windows Qt/offscreen integration. No network, models or user project data."""
from copy import deepcopy
from pathlib import Path
import pytest
from PySide6.QtCore import QObject, QUrl, QMetaObject
from PySide6.QtQml import QQmlApplicationEngine
from test_dev5_feedback_navigation import app, bridge
from test_dev5_feedback_ocr_flow import setup_ocr, fake_run
from test_qt_entry_initialization_dev5 import settled
from lyricsync.model import Mapping
from tools.qt_event_loop_wait import delay
from test_qt_startup_phase5 import find,click


def test_stale_ocr_auto_ai_flag_cannot_send(bridge,tmp_path,monkeypatch):
    setup_ocr(bridge,tmp_path)
    monkeypatch.setattr('lyricsync.pipeline.run',fake_run)
    def prohibited(*args,**kwargs):raise AssertionError('AI must not run')
    monkeypatch.setattr(bridge,'_api_check',prohibited)
    monkeypatch.setattr(bridge,'_api_alignment',prohibited)
    bridge.document.project.settings['api_enabled']=True
    assert bridge.command('ocr.start',{'autoAi':True})
    settled(bridge)
    assert bridge.stage=='review'
    assert len(bridge.document.main_timeline)==2


@pytest.mark.parametrize('command',['row.align','row.referenceTranslation','api.review','alignment.batch','alignment.forceRetry'])
def test_hidden_ai_dispatch_rejects_without_request(bridge,command,monkeypatch):
    before=bridge.document.snapshot()
    assert not bridge.command(command,{})
    assert bridge.document.snapshot()==before


def test_qml_startup_modes_keep_same_document_and_hidden_features(app,bridge,monkeypatch):
    monkeypatch.setattr("desktop_qt.dialogs.choose_asset_clear",lambda:"detach")
    engine=window=None
    try:
        engine=QQmlApplicationEngine();warnings=[]
        engine.warnings.connect(lambda messages:warnings.extend(m.toString() for m in messages))
        engine.rootContext().setContextProperty('bridge',bridge)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1]/'desktop_qt/qml/Main.qml')))
        assert engine.rootObjects(),warnings
        window=engine.rootObjects()[0]
        bridge.document.project.mappings=[Mapping('keep',[],text_override='manual',start=.2,end=.8,human_edited=True)]
        bridge._changed();before=bridge.document.snapshot();document_id=bridge.document.id
        for index,mode in enumerate(('ocr','audio','edit','ocr')):
            assert bridge.command('project.home',{})
            delay(50)
            button=find(window,'startupEntry_'+mode)
            assert button
            click(window,button)
            delay(50)
            assert bridge.document.id==document_id
            assert bridge.document.project.mappings[0].text_override=='manual'
            assert bridge._entry_mode==('ocr' if index==0 else 'edit') and not bridge.state['startupVisible']
        assert bridge.command('stage.set',{'stage':'review'})
        delay(60)
        for name in ('qcWorkspaceButton','startupQc','candidateActionRow','candidatePreviewBanner','ocrAutoAiCheckBox'):
            obj=window.findChild(QObject,name);assert obj and not obj.property('visible'),name
        assert window.findChild(QObject,'candidateCompareApplyButton') is None
        restored_handle = window.findChild(QObject,'timelineSplitHandle')
        assert restored_handle is not None and restored_handle.isVisible() and restored_handle.height() >= 6
        assert window.findChild(QObject,'playerControlsResizeHandle') is not None
        assert window.findChild(QObject,'playerSeekRail') is not None
        multi=window.findChild(QObject,'multiSelectMode')
        assert multi is None
        add=window.findChild(QObject,'reviewAddBlankButton')
        assert add is not None and add.isVisible() and add.isEnabled()
        assert bridge.document.project.mappings[0].text_override=='manual'
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


@pytest.fixture(autouse=True)
def accepted_settings_for_downstream_ocr_contracts(monkeypatch):
    # These older suites exercise work AFTER a user's settings acceptance.
    # Actual modal acceptance/cancel/reentrancy is covered in dev8 entry tests.
    monkeypatch.setattr('desktop_qt.dialogs.ocr_settings', lambda *args, **kwargs: True)
