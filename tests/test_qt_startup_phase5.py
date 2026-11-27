"""Phase 5 real Qt event-loop checks; not native Windows visual acceptance."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from pathlib import Path
import hashlib
import json
import time
import pytest
from PySide6.QtCore import QObject,QPointF,Qt,QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication,QFrame
from PySide6.QtTest import QTest
from desktop_qt.controller import Bridge
from desktop_qt.recent_projects import RecentProjects
from desktop_qt.windows_client_frame import native_frame_style,FRAME_STYLES
from tools.qt_event_loop_wait import delay,wait_until

ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture(scope='module')
def application():
    QQuickStyle.setStyle('Basic')
    app=QApplication.instance() or QApplication([])
    from PySide6.QtGui import QFontDatabase
    for font in ("msyh.ttc","msyhbd.ttc","segoeui.ttf"):
        path=Path("C:/Windows/Fonts")/font
        if path.is_file():QFontDatabase.addApplicationFont(str(path))
    return app

def find(window,name):
    def walk(item):
        if item.objectName()==name:return item
        for child in item.childItems():
            answer=walk(child)
            if answer is not None:return answer
    result=window.findChild(QObject,name) or walk(window.contentItem())
    assert result is not None,name
    return result

def click(window,item):
    assert item.isEnabled() and item.isVisible()
    QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint())

@pytest.fixture
def ui(application,tmp_path):
    from PySide6.QtCore import QEvent
    bridge=engine=window=None;warnings=[]
    try:
        bridge=Bridge(storage_dir=tmp_path/'state',startup=True)
        engine=QQmlApplicationEngine()
        engine.warnings.connect(lambda messages:warnings.extend(m.toString() for m in messages))
        engine.rootContext().setContextProperty('bridge',bridge)
        engine.load(QUrl.fromLocalFile(str(ROOT/'desktop_qt/qml/Main.qml')))
        assert engine.rootObjects(),warnings
        window=engine.rootObjects()[0]
        yield bridge,window,warnings
    finally:
        if bridge is not None:
            bridge.shutdown();bridge.wait_for_shutdown(application)
            bridge.media_controller.worker.thread.join(10)
            assert not bridge.media_controller.worker.thread.is_alive()
        if window is not None:
            window.setProperty('allowClose',True);window.close()
        if engine is not None:
            destroyed=[];engine.destroyed.connect(lambda:destroyed.append(True))
            engine.deleteLater();application.sendPostedEvents(None,QEvent.Type.DeferredDelete)
            wait_until(lambda:bool(destroyed))
        application.processEvents()
    assert not warnings,"\n".join(warnings)

def test_recent_paths_missing_and_roundtrip(tmp_path):
    store=RecentProjects(tmp_path/'state')
    actual=tmp_path/'真实工程.lyria';actual.write_text('{}',encoding='utf-8')
    store.remember(actual);store.remember(actual)
    assert len(store.snapshot())==1 and store.snapshot()[0]['available']
    assert RecentProjects(tmp_path/'state').snapshot()==store.snapshot()
    actual.unlink()
    assert not store.snapshot()[0]['available']
    store.forget(str(actual));assert not store.snapshot()
    assert not actual.exists()
    store.path.write_text('[null, {}, {"path":"relative.lyria"}]',encoding='utf-8')
    assert RecentProjects(tmp_path/'state').snapshot()==[]

def test_startup_stagger_true_frames_and_interruption(ui,tmp_path,monkeypatch):
    bridge,window,warnings=ui
    out=Path(os.environ.get('LYRIA_STARTUP_EVIDENCE',tmp_path));out.mkdir(parents=True,exist_ok=True)
    reveals=[find(window,'startupEntryReveal_'+mode) for mode in ('audio','ocr','edit')]
    assert bridge.state['startupVisible']
    samples=[];frames=[]
    for n in range(12):
        delay(80)
        frame=window.grabWindow();frame.save(str(out/f'build-{n:02}.png'))
        samples.append({'timeMs':80*(n+1),'opacity':[v.opacity() for v in reveals],
                        'offset':[v.property('entryOffset') for v in reveals]})
        frames.append(str(out/f'build-{n:02}.png'))
    assert any(0<s['opacity'][0]<1 and s['opacity'][2]==0 for s in samples),samples
    assert all(v.opacity()==1 and v.property('entryOffset')==0 for v in reveals)
    assert not bridge.jobs.active and not bridge.document.project.mappings
    # Actual click routes each entry to one same-document editor.start command; safe
    # initialization semantics are tested in the candidate integration suite.
    calls=[]
    original=bridge.command
    monkeypatch.setattr(bridge,'command',lambda name,payload:calls.append((name,dict(payload))) or True)
    for mode in ('audio','ocr','edit'):click(window,find(window,'startupEntry_'+mode))
    assert calls==[('editor.start',{'mode':m}) for m in ('audio','ocr','edit')]
    monkeypatch.setattr(bridge,'command',original)
    assert bridge.command('project.resume',{})
    assert not find(window,'startupPage').isVisible()
    assert bridge.command('project.home',{})
    delay(35)
    assert bridge.command('settings.set',{'key':'motion','value':False})
    assert all(v.opacity()==1 and not v.property('animating') for v in reveals)
    for width,height in [(760,600),(920,740),(1400,940)]:
        window.resize(width,height);delay(120)
        for mode in ('audio','ocr','edit'):
            button=find(window,'startupEntry_'+mode);point=button.mapToScene(QPointF(0,0))
            assert 0<=point.x()<window.width() and button.width()>130
        window.grabWindow().save(str(out/f'startup-{width}.png'))
    (out/'animation-samples.json').write_text(json.dumps({'samples':samples,'scope':'offscreen actual event loop, not DWM visual acceptance'},indent=2),encoding='utf-8')
    try:
        from PIL import Image
        images=[Image.open(p).convert('RGB') for p in frames]
        images[0].save(out/'startup-build.gif',save_all=True,append_images=images[1:],duration=80,loop=0)
    except ImportError:pass

def test_loading_shimmer_has_real_temporal_change_and_stops(ui,tmp_path):
    bridge,window,_=ui
    delay(1200)  # Startup is fully settled; only the busy text may change.
    bridge._start_job('离线动效测试任务',lambda cancel,progress:time.sleep(1.5),lambda result,error:None)
    label=find(window,'startupLoadingText')
    def crop_label():
        image=window.grabWindow()
        # Offscreen grabWindow may report DPR 1 while containing scaled pixels.
        sx=image.width()/window.width();sy=image.height()/window.height()
        point=label.mapToScene(QPointF(0,0))
        return image.copy(int(point.x()*sx),int(point.y()*sy),int(label.width()*sx),int(label.height()*sy))
    delay(120);first=label.property('phase');frame1=crop_label()
    delay(220);second=label.property('phase');frame2=crop_label()
    assert label.property('animating') and first!=second
    assert not frame1.isNull() and frame1!=frame2, 'Actual label pixels must move, not only an animation property'
    out=Path(os.environ.get('LYRIA_STARTUP_EVIDENCE',tmp_path));out.mkdir(parents=True,exist_ok=True)
    frame1.save(str(out/'shimmer-120ms.png'));frame2.save(str(out/'shimmer-340ms.png'))
    wait_until(lambda:not bridge.state['task']['running'],timeout_ms=4000)
    assert not label.isVisible() and not label.property('animating')

def test_home_does_not_replace_dirty_document(application,tmp_path):
    b=Bridge(storage_dir=tmp_path,startup=True)
    try:
        b.document.project.settings['untouched']='same';b._changed();identity=b.document.id;revision=b.document.revision
        assert b.command('project.home',{}) and b.state['startupVisible']
        assert b.document.id==identity and b.document.revision==revision
        assert b.command('project.resume',{}) and not b.state['startupVisible']
        assert b.document.project.settings['untouched']=='same'
    finally:b.shutdown()

def test_dwm_native_style_preserves_existing_bits():
    old=0x80000000|0x04000000
    assert native_frame_style(old)&old==old
    assert native_frame_style(old)&FRAME_STYLES==FRAME_STYLES
    assert native_frame_style(native_frame_style(old))==native_frame_style(old)

def test_api_advanced_checkbox_never_sits_on_border(application,tmp_path,monkeypatch):
    from desktop_qt.dialogs import ApiSettingsDialog
    from tests.test_qt_native_theme import ReadOnlyBridge
    dialog=ApiSettingsDialog(ReadOnlyBridge());dialog.show();delay(100)
    try:
        assert dialog.name.isEnabled() and dialog.provider.currentData()=='deepseek'
        if dialog.screen().availableGeometry().height() >= 780 and dialog.screen().availableGeometry().width() >= 740:
            assert dialog._api_scroll.verticalScrollBar().maximum()==0
        # UI-to-config contract independent of the AI team's pending normalizer
        # merge. Parent integration must repeat using the real normalized().
        from lyricsync import ai_config
        monkeypatch.setattr(ai_config,'normalized',lambda values:values)
        dialog.transport.setCurrentIndex(dialog.transport.findData('sse'))
        dialog.thinking.setCurrentIndex(dialog.thinking.findData('disabled'))
        dialog.reasoning.setCurrentIndex(dialog.reasoning.findData('low'))
        dialog.idle_timeout.setValue(25);dialog.total_timeout.setValue(90)
        values=dialog.values()
        assert {key:values[key] for key in ('api_transport','api_thinking','api_reasoning_effort','api_idle_timeout_seconds','api_total_timeout_seconds')}==dict(api_transport='sse',api_thinking='disabled',api_reasoning_effort='low',api_idle_timeout_seconds=25,api_total_timeout_seconds=90)
        dialog.model.setCurrentText('manual-test-model')
        dialog.bridge.api_state={'status':'synthetic list failure','models':[]}
        dialog.refresh_status()
        assert dialog.model.currentText()=='manual-test-model'
        frame=dialog.findChild(QFrame,'apiAdvancedBody')
        for checked in (False,True,False):
            dialog.advanced.setChecked(checked);delay(40)
            assert dialog.advanced.geometry().bottom()<frame.geometry().top()
            assert frame.isEnabled()==checked
            assert dialog.max_calls.isVisible() and dialog.transport.isVisible()
        out=Path(os.environ.get('LYRIA_STARTUP_EVIDENCE',tmp_path));out.mkdir(parents=True,exist_ok=True)
        dialog.grab().save(str(out/'api-default-offscreen.png'))
        dialog.resize(620,420);delay(60)
        assert dialog._api_compact_mode==1
        assert dialog._api_scroll.horizontalScrollBar().maximum()==0
        assert dialog._api_buttons.isVisible() and dialog._api_buttons.geometry().bottom()<dialog.height()
        assert dialog._api_scroll.verticalScrollBar().maximum()>0
        dialog.resize(360,340);delay(100)
        assert dialog._api_compact_mode==2
        assert dialog._api_scroll.horizontalScrollBar().maximum()==0
        dialog.grab().save(str(out/'api-narrow-offscreen.png'))
    finally:dialog.reject()


@pytest.mark.skipif(os.name != "nt", reason="Win32 ABI filter unit test")
def test_native_filter_only_handles_owned_nonclient_message(monkeypatch):
    import ctypes
    from desktop_qt.windows_client_frame import WindowsClientFrame,MSG,RECT
    frame=WindowsClientFrame();frame.hwnd=123
    class Api:
        @staticmethod
        def IsZoomed(hwnd):return False
    frame.user32=Api()
    rect=RECT(0,0,900,600)
    msg=MSG();msg.hwnd=123;msg.message=0x0083;msg.wParam=1;msg.lParam=ctypes.addressof(rect)
    assert frame.nativeEventFilter(b'windows_generic_MSG',ctypes.addressof(msg))==(True,0)
    msg.hwnd=321
    assert frame.nativeEventFilter(b'windows_generic_MSG',ctypes.addressof(msg))==(False,0)
    msg.hwnd=123;msg.message=0x0100
    assert frame.nativeEventFilter(b'windows_generic_MSG',ctypes.addressof(msg))==(False,0)
    assert (rect.left,rect.top,rect.right,rect.bottom)==(0,0,900,600)


def test_real_recent_open_failure_save_and_dirty_cancel(application,tmp_path):
    from lyricsync.project_document import ProjectDocument,write_document
    from lyricsync.model import Project
    target=tmp_path/'saved.lyria';document=ProjectDocument(project=Project())
    write_document(document.snapshot(explicit_save=True),target)
    b=Bridge(storage_dir=tmp_path/'state',startup=True)
    try:
        identity=b.document.id
        missing=tmp_path/'missing.lyria'
        b._recent_projects.remember(missing);b.command('project.home',{})
        assert b.state['recentProjects'][0]['available'] is False
        assert b.command('project.open',{'path':str(missing)})
        wait_until(lambda:not b.state['task']['running'])
        assert b.document.id==identity and b.state['startupVisible'] and b.state['error']
        assert b.command('project.open',{'path':str(target)})
        wait_until(lambda:b.document.id==document.id)
        assert not b.state['startupVisible'] and b.state['recentProjects'][0]['path']==str(target)
        b.document.project.settings['edited']='preserved';b._changed()
        b.command('project.home',{});b.command('project.open',{'path':str(target)})
        assert b.state['closeDialog']['visible']
        assert b.command('close.resolve',{'choice':'cancel'})
        assert b.document.project.settings['edited']=='preserved' and b.state['startupVisible']
        saved=tmp_path/'saved-new.lyria'
        assert b.command('project.saveAs',{'path':str(saved)})
        wait_until(lambda:b._save_running is None)
        assert b.state['recentProjects'][0]['path']==str(saved)
        assert RecentProjects(b.storage_dir).items[0]['path']==str(saved)
    finally:b.shutdown()


def test_new_menu_opens_mode_selection_without_discarding(ui):
    bridge,window,_=ui
    bridge.document.project.settings['owned-text']='preserved';bridge._changed()
    bridge.command('project.resume',{});delay(300)
    identity=bridge.document.id;revision=bridge.document.revision
    click(window,find(window,'fileMenuButton'));delay(180)
    click(window,find(window,'newProjectMenuAction'));delay(180)
    assert bridge.state['startupVisible']
    assert bridge.document.id==identity and bridge.document.revision==revision
    assert bridge.document.project.settings['owned-text']=='preserved'


def test_real_recent_card_opens_saved_project(ui,tmp_path):
    from lyricsync.project_document import ProjectDocument,write_document
    from lyricsync.model import Project
    bridge,window,_=ui
    target=tmp_path/'recent-ui.lyria';document=ProjectDocument(project=Project())
    write_document(document.snapshot(explicit_save=True),target)
    bridge._recent_projects.remember(target);bridge.command('project.home',{})
    delay(1100)
    card=find(window,'recentProject_0')
    assert card.property('text')=='recent-ui'
    click(window,card)
    wait_until(lambda:bridge.document.id==document.id)
    assert not bridge.state['startupVisible'] and not find(window,'startupPage').isVisible()


def test_import_controls_selection_hierarchy_order_and_manual_actions(ui,monkeypatch):
    monkeypatch.setattr("desktop_qt.dialogs.confirm_mode_switch",lambda:True)
    bridge,window,_=ui
    for mode,name in [('audio','audioAutoTimingEntry'),('ocr','ocrEntryButton'),('edit','pureEditingButton')]:
        bridge.command('editor.start',{'mode':mode});delay(100)
        assert bridge.state['entryMode']==mode
        assert find(window,'changeEntryModeButton') is not None
        if mode!='edit':
            click(window,find(window,'changeEntryModeButton'));delay(80)
            button=find(window,name)
            assert button.isVisible()
            click(window,button);delay(80)
        assert not bridge.jobs.active
    bridge.command('editor.start',{'mode':'audio'});delay(100)
    start=find(window,'startLocalVocalAlignment')
    assert start.property('text')=='开始自动打轴' and start.property('primary')
    assert window.findChild(QObject,'configureLocalVocalModel') is None
    # Removed duplicate import-page button; the model-management menu command remains usable.
    assert 'text: "本地模型管理"; onTriggered: window.cmd("vocal.configure")' in (ROOT/'desktop_qt/qml/Main.qml').read_text('utf-8')
    configured=[]
    monkeypatch.setattr(bridge,'_configure_vocal',lambda setup_only=False:configured.append(setup_only))
    assert bridge.command('vocal.configure',{})
    assert configured==[True]
    assert window.findChild(QObject,'importPairingStructureButton') is None
    bridge.command('editor.start',{'mode':'ocr'});delay(80)
    assert find(window,'ocrAutoAiCheckBox') is not None
    assert window.findChild(QObject,'chooseOcrEngineButton') is None
    assert find(window,'startOcrButton').isVisible()
    assert all(item.property('text')!='回到逐句核对' for item in window.findChildren(QObject))
