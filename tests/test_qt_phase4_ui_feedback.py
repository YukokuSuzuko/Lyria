"""Dev4 UI feedback contracts, generated fixtures and offscreen software only.

These are geometry/input/state checks, not Windows DWM/GPU or user acceptance.
Set LYRIA_PHASE4_UI_EVIDENCE to capture unretouched offscreen screenshots.
"""
import os
import time
from copy import deepcopy
from pathlib import Path
import pytest
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QObject, QMetaObject, QTimer
from PySide6.QtTest import QTest
from test_qt_preview_visibility import install_cues, present, find as preview_find
from test_qt_visual_feedback import preview_ui
from test_qt_window_chrome import ui as chrome_ui, find, click
from test_qt_review_selection import ui as review_ui
from lyricsync.model import Project
from lyricsync.candidate_tracks import capture_source, candidate_from_project
from desktop_qt.workspace_layout import update_layout, DEFAULTS
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
from PySide6.QtGui import QFontDatabase, QWheelEvent
from PySide6.QtQuickControls2 import QQuickStyle

@pytest.fixture(scope="module")
def application():
    QQuickStyle.setStyle("Basic")
    app=QApplication.instance() or QApplication([])
    # Offscreen Windows may not find installed faces automatically. Registration
    # is process-local and does not install, redistribute, or change fonts.
    for filename in ("msyh.ttc","msyhbd.ttc","arial.ttf"):
        font=Path(os.environ.get("WINDIR",r"C:\Windows"))/"Fonts"/filename
        if font.is_file(): QFontDatabase.addApplicationFont(str(font))
    return app



def shot(window, name):
    destination = os.environ.get("LYRIA_PHASE4_UI_EVIDENCE")
    if destination:
        root = Path(destination); root.mkdir(parents=True, exist_ok=True)
        assert window.grabWindow().save(str(root / (name + ".png")))


@pytest.mark.parametrize("resolution,size", [((1600,300),(900,700)),((300,1600),(900,700)),((640,360),(480,700)),((1920,1080),(1400,500))])
def test_actual_video_is_centered_without_changing_aspect(preview_ui, resolution, size):
    ui = preview_ui; ui.window.resize(*size)
    install_cues(ui, resolution=resolution, layout=False); present(ui, 2)
    video = preview_find(ui,"videoPreview"); viewport = preview_find(ui,"videoViewport")
    rect = video.property("contentRect")
    center = video.mapToItem(viewport,rect.center())
    assert center.x() == pytest.approx(viewport.width()/2,abs=.6)
    assert center.y() == pytest.approx(viewport.height()/2,abs=.6)
    assert rect.width()/rect.height() == pytest.approx(resolution[0]/resolution[1],rel=.001)
    assert rect.width() <= viewport.width()+.1 and rect.height() <= viewport.height()+.1
    shot(ui.window,"centered-%s-%s" % (resolution[0],size[0]))


@pytest.mark.parametrize("width",[330,500,900])
def test_controls_compact_internal_gap_retains_all_controls(preview_ui,width):
    ui=preview_ui; ui.window.resize(width,700)
    install_cues(ui,layout=False);present(ui,2)
    controls=preview_find(ui,"playerControls"); seek=preview_find(ui,"playerSeekRail")
    assert not preview_find(ui,"hiddenCueHint").isVisible()
    assert seek.height()==18
    summary=preview_find(ui,"transportSummary")
    assert seek.y()-(summary.y()+summary.height())<=1.1
    assert controls.height() < (125 if width < 900 else 90)
    assert preview_find(ui,"frameSummary").height()+1.5 <= preview_find(ui,"transportButtons").y()
    for name in ("transportPreviousCue","transportCueFirst","transportForwardOne","transportCueLast","transportNextCue","addMarkerButton","morePlaybackTools","playCurrentCueButton","muteButton","subtitlePreviewToggle","subtitleStyleButton"):
        item=preview_find(ui,name);pos=item.mapToItem(controls,QPointF())
        assert item.isVisible() and pos.x()>=0 and pos.x()+item.width()<=controls.width()+.1
        assert pos.y()>=0 and pos.y()+item.height()<=controls.height()+.1
    assert not preview_find(ui,"showRoiButton").isVisible()
    menu=preview_find(ui,"morePlaybackTools")
    QTest.mouseClick(ui.window,Qt.LeftButton,Qt.NoModifier,menu.mapToScene(menu.boundingRect().center()).toPoint());QTest.qWait(80)
    clear=ui.window.findChild(QObject,"clearMarkersButton");assert clear and clear.property("visible")
    QTest.keyClick(ui.window,Qt.Key_Escape);QTest.qWait(110)
    # v0.22 keeps a visible 24 px picture and respects the available panel budget.
    # A 120 px global preference ceiling is not a promise that every narrow
    # fitted picture can yield 120 px without disappearing.
    viewport=preview_find(ui,"videoViewport")
    baseline_height=controls.height();baseline_y=controls.mapToScene(QPointF()).y()
    for positive in (12, 50, 500):
        ui.panel.setProperty("controlsExtraHeight", positive); QTest.qWait(30)
        assert ui.panel.property("effectiveControlsExtraHeight") == 0
        assert controls.height() == pytest.approx(baseline_height, abs=1)
        assert controls.mapToScene(QPointF()).y() == pytest.approx(baseline_y, abs=1)
    measured=[]
    for step in range(11):
        requested = -52 * step / 10
        ui.panel.setProperty("controlsExtraHeight", requested); QTest.qWait(30)
        assert ui.panel.property("effectiveControlsExtraHeight") == pytest.approx(requested)
        assert controls.height() <= baseline_height + .5
        assert viewport.height() >= 24
        measured.append(controls.height())
    assert len(set(measured)) >= 2
    ui.panel.setProperty("controlsExtraHeight", -120); QTest.qWait(30)
    assert ui.panel.property("effectiveControlsExtraHeight") == pytest.approx(-52)
    assert viewport.height()>=24
    ui.panel.setProperty("controlsExtraHeight", 0); QTest.qWait(30)
    for name in ("transportPreviousCue","transportCueFirst","transportForwardOne","transportCueLast","transportNextCue","addMarkerButton","morePlaybackTools","playCurrentCueButton","muteButton","subtitlePreviewToggle","subtitleStyleButton"):
        item=preview_find(ui,name);pos=item.mapToItem(controls,QPointF())
        assert item.isVisible() and pos.x()>=0 and pos.x()+item.width()<=controls.width()+.1
        assert pos.y()>=0 and pos.y()+item.height()<=controls.height()+.1
    shot(ui.window,"controls-%s" % width)


def test_compact_partition_limits_validate_without_changing_project():
    layout=update_layout(DEFAULTS,{"previewHeight":100,"controlsHeight":999})
    assert layout["previewHeight"]==240 and layout["controlsHeight"]==0
    assert update_layout(layout,{"controlsHeight":-20})["controlsHeight"]==-20
    assert update_layout(layout,{"controlsHeight":-70})["controlsHeight"]==-52


@pytest.mark.parametrize("width",[760,900,1400])
def test_flush_opaque_titlebar_preserves_resize_and_controls(chrome_ui,width):
    app,bridge,chrome,window=chrome_ui
    window.resize(width,900);QTest.qWait(100)
    strip=find(window,"integratedTitlebarStrip");pos=strip.mapToScene(QPointF())
    assert pos==QPointF(0,0) and strip.width()==window.width()
    assert window.property("color").alpha()==255
    assert window.property("resizeBorder")==0
    # Offscreen must not pretend it exercised DWM even with Windows policy.
    assert chrome.applyWindowCorners(window) is False
    for name in ("windowMinimize","windowMaximize","windowClose","fileMenuButton","toolsMenuButton"):
        item=find(window,name);loc=item.mapToScene(QPointF())
        assert item.isVisible() and loc.x()>=6 and loc.y()>=6
        assert loc.x()+item.width()<=window.width()-6
    assert find(window,"windowResize_3").isVisible()
    assert find(window,"stageButton_import").property("text")=="导入 / 画面识别"
    assert window.findChild(QObject,"stageButton_ocr") is None
    shot(window,"flush-import-%s" % width)


def test_ocr_missing_roi_click_is_actionable_and_does_not_start(chrome_ui,monkeypatch):
    app,bridge,chrome,window=chrome_ui
    bridge._entry_mode="ocr"
    bridge.media_controller.metadata["media_kind"]="video"
    bridge.media_controller._state.update(ready=True,mediaKind="video",width=640,height=360)
    bridge.media_controller.changed.emit(); bridge._refresh(False)
    # Capability states can differ with host OCR installation. Override only the
    # panel's detached state to test this presentation guard, not backend safety.
    panel=find(window,"importRecognitionPanel")
    state=deepcopy(bridge.uiState);state["capabilities"]["ocr.start"]=True;state["entryMode"]="ocr";state["roi"]=None
    panel.setProperty("state",state)
    panel.setProperty("media",{"ready":True,"mediaKind":"video"});QTest.qWait(100)
    before=deepcopy(bridge.document.project.to_dict())
    button=find(window,"startOcrButton")
    assert not button.isEnabled() and find(window,"missingRegionHint").isVisible()
    QMetaObject.invokeMethod(panel,"startOcr")
    assert window.findChild(QObject,"ocrRangeRequiredDialog") is None
    assert bridge._candidate_job is None and bridge.document.project.to_dict()==before
    shot(window,"ocr-range-inline-guidance")


def _fully_in_input_view(window, item):
    """Qt isVisible alone does not account for scroll-ancestor clipping."""
    scene = QRectF(item.mapToScene(QPointF()), item.size())
    if not QRectF(0, 0, window.width(), window.height()).contains(scene):
        return False
    parent = item.parentItem()
    while parent is not None:
        if parent.clip():
            local = QRectF(parent.mapFromScene(scene.topLeft()), scene.size())
            if not QRectF(0, 0, parent.width(), parent.height()).contains(local):
                return False
        parent = parent.parentItem()
    return True


def _import_flick(item):
    flick=item.parentItem()
    while flick is not None and flick.property("contentY") is None:flick=flick.parentItem()
    assert flick is not None,"Import action must live in an internally scrollable panel"
    return flick


def _wheel_workspace_until_visible(app, window, item, force_scroll=False):
    from tools.qt_event_loop_wait import wait_until
    assert find(window,"makeScroll").property("contentItem") is None
    scroll=flick=_import_flick(item)
    before = flick.property("contentY")
    for _ in range(12):
        if _fully_in_input_view(window, item) and (not force_scroll or flick.property("contentY")>before):
            break
        # Send real wheel input to the internal import viewport, not contentY assignment.
        pos = scroll.mapToScene(QPointF(scroll.width()/2, scroll.height()/2))
        wheel = QWheelEvent(pos, QPointF(window.mapToGlobal(pos.toPoint())),
                            QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        wheel.setTimestamp(int(time.monotonic() * 1000))
        QTest.mouseMove(window, pos.toPoint())
        app.sendEvent(window, wheel)
        QTest.qWait(40)
        wait_until(lambda: not flick.property("moving"), timeout_ms=2000)
    shot(window,"import-scroll-"+str(window.width())+"x"+str(window.height()))
    assert flick.property("contentY") > before, ("No actual internal import scrolling occurred",
        dict(viewport=(scroll.width(),scroll.height()), content=flick.property("contentHeight"),
             position=(scroll.mapToScene(QPointF()).x(),scroll.mapToScene(QPointF()).y()),
             button=(item.mapToScene(QPointF()).x(),item.mapToScene(QPointF()).y())))
    assert _fully_in_input_view(window, item), "OCR button remains clipped after scrolling"


def _pointer_click(window, item):
    assert _fully_in_input_view(window, item)
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseMove(window, point)
    QTest.qWait(20)
    click(window, item)


@pytest.mark.parametrize("size", [(800, 800), (760, 600)])
def test_small_window_scroll_click_enters_actual_roi_setup(chrome_ui, size):
    app, bridge, chrome, window = chrome_ui
    window.resize(*size)
    QTest.qWait(100)
    assert (window.width(), window.height()) == size
    bridge._entry_mode = "ocr"
    bridge.media_controller.metadata["media_kind"] = "video"
    bridge.media_controller._state.update(ready=True, mediaKind="video", width=640, height=360)
    bridge.media_controller.changed.emit()
    bridge._refresh(False)
    panel = find(window, "importRecognitionPanel")
    state = deepcopy(bridge.uiState)
    state["capabilities"]["ocr.start"] = True
    state["entryMode"] = "ocr"
    state["roi"] = None
    panel.setProperty("state", state)
    panel.setProperty("media", {"ready": True, "mediaKind": "video"})
    QTest.qWait(100)
    before = deepcopy(bridge.document.project.to_dict())
    button = find(window, "startOcrButton")
    assert button.isVisible() and not button.isEnabled()
    button=find(window,"addOcrRegion")
    assert button.isVisible() and button.isEnabled()
    flick=_import_flick(button)
    assert find(window,"makeScroll").property("contentItem") is None
    assert _fully_in_input_view(window,flick),"The complete import viewport must fit inside the window"
    overflow=flick.property("contentHeight")>flick.height()+.1
    if overflow or not _fully_in_input_view(window,button):
        # Overflow must be reachable by actual wheel input even if the primary
        # button is already visible. No contentY assignment or artificial sizing.
        _wheel_workspace_until_visible(app,window,button,force_scroll=overflow)
    assert _fully_in_input_view(window,button)
    _pointer_click(window, button)
    assert window.findChild(QObject, "ocrRangeRequiredDialog") is None
    choice=window.findChild(QObject,"regionSetupDialog")
    preview=find(window,"mainPreviewPanel")
    assert not choice.property("visible") and preview.property("roiSelecting")
    assert bridge._candidate_job is None and bridge.document.project.to_dict() == before
    shot(window, "small-ocr-manual-roi-%sx%s" % size)


def test_candidate_view_stays_readonly_without_public_apply_and_keeps_private_safety(review_ui):
    ui=review_ui;bridge=ui.bridge
    from test_qt_window_chrome import walk
    from lyricsync.candidate_tracks import prepare_apply
    target = bridge.selected
    generated = deepcopy(bridge.document.project)
    row = next(row for row in generated.mappings if row.id == target)
    row.start += .1
    before_install = deepcopy(bridge.document.project.to_dict())
    record=candidate_from_project(capture_source(deepcopy(bridge.document),"ocr",bridge.media_controller.index),generated)
    bridge._candidate_install(record);before=deepcopy(bridge.document.project.to_dict())
    assert before == before_install  # Existing work is never silently replaced.
    bridge.switchCandidateSource("ocr");QTest.qWait(250)
    panel=find(ui.window,"reviewPanel");assert not panel.property("editable")
    for name in ("reviewTimingStrip","confirmCurrent","confirmAndNext","reviewAddBlankButton","deleteCurrentCueButton"):
        assert not find(ui.window,name).isVisible(),name
    assert find(ui.window,"reviewOperationsButton").isVisible()
    editor=find(ui.window,"chineseEditor_"+bridge.selected)
    QMetaObject.invokeMethod(panel,"editSelectedRow");QTest.qWait(30)
    assert editor.property("readOnly")
    assert "只读" in find(ui.window,"alignmentCandidateStatus").property("text")
    assert not any(item.objectName() == "candidateCompareApplyButton"
                   for item in walk(ui.window.contentItem()))
    assert find(ui.window,"selectedCueAccent_"+bridge.selected).isVisible()
    assert bridge.document.project.to_dict()==before
    shot(ui.window,"ocr-private-read-only-no-public-apply")
    bridge.switchCandidateSource("main");QTest.qWait(200)
    assert panel.property("editable") and find(ui.window,"reviewTimingStrip").isVisible()
    shot(ui.window,"main-editable-accent")
    # Removing the UI entry must not erase the retained atomic apply/undo rules.
    # Exercise these through the private backend, never by restoring a button.
    bridge.document.project.settings["manual_locked_ids"] = [target]
    locked = deepcopy(bridge.document.project.to_dict())
    blocked = prepare_apply(bridge.document, record["id"], [target],
                            frame_index=bridge.media_controller.index)
    with pytest.raises(ValueError):
        bridge.candidate_apply(blocked)
    assert bridge.document.project.to_dict() == locked
    bridge.document.project.settings.pop("manual_locked_ids")
    plan = prepare_apply(bridge.document, record["id"], [target],
                         frame_index=bridge.media_controller.index)
    depth = len(bridge.history.undo_stack)
    assert bridge.candidate_apply(plan) == [target]
    assert len(bridge.history.undo_stack) == depth + 1
    assert next(row for row in bridge.document.project.mappings if row.id == target).start == pytest.approx(row.start)
    after = deepcopy(bridge.document.project.to_dict())
    assert after != before
    assert bridge.command("history.undo", {})
    assert bridge.document.project.to_dict() == before
    assert bridge.command("history.redo", {})
    assert bridge.document.project.to_dict() == after


def test_magnet_icon_opens_left():
    source=(Path(__file__).resolve().parents[1]/"desktop_qt/assets/icons/magnet.svg").read_text(encoding="utf-8")
    assert 'rotate(-90 12 12)' in source


def test_dwm_corner_request_uses_33_2_without_touching_opacity(chrome_ui, monkeypatch):
    from types import SimpleNamespace
    import ctypes
    import desktop_qt.window_chrome as module
    app,bridge,chrome,window=chrome_ui
    calls=[]
    class Setter:
        def __call__(self, hwnd, attribute, value, size):
            calls.append((hwnd,attribute,ctypes.cast(value,ctypes.POINTER(ctypes.c_int)).contents.value,size))
            return 0
    monkeypatch.setattr(module.ctypes,"windll",SimpleNamespace(dwmapi=SimpleNamespace(DwmSetWindowAttribute=Setter())),raising=False)
    monkeypatch.setattr(module.sys,"platform","win32")
    monkeypatch.setattr(module.QGuiApplication,"platformName",staticmethod(lambda:"windows"))
    chrome._native_frame=SimpleNamespace(apply=lambda hwnd:True)  # Mock HWND styles, never touch offscreen handles.
    before=window.property("color")
    assert chrome.applyWindowCorners(window)
    assert calls==[(int(window.winId()),33,2,ctypes.sizeof(ctypes.c_int))]
    assert window.property("color")==before and before.alpha()==255


def test_play_cue_uses_edge_marks_not_superseded_braces(preview_ui):
    import xml.etree.ElementTree as ET
    source=Path(__file__).resolve().parents[1]/"desktop_qt/assets/icons/play-cue.svg"
    paths=ET.fromstring(source.read_text(encoding="utf-8")).findall("{http://www.w3.org/2000/svg}path")
    assert len(paths)==2 and paths[0].attrib["stroke-width"]=="1.4"
    assert "c" not in paths[0].attrib["d"].lower(), "No curly-brace curves"
    assert paths[1].attrib["d"]=="M9 7.5 16 12l-7 4.5V7.5Z"
    assert preview_find(preview_ui,"playCurrentCueButton").property("glyphSize")==18


def test_entry_selection_is_not_inference_and_edit_uses_one_editor(chrome_ui,tmp_path,monkeypatch):
    from lyricsync.frames import FrameIndex
    from lyricsync.model import Mapping, Lyric, Translation, Visual
    app,bridge,chrome,window=chrome_ui
    project=bridge.document.project
    source=tmp_path/"ui-entry-source.txt";source.write_text("retained original",encoding="utf-8")
    project.inputs["ktv"]=str(source);project.video={"duration":4.}
    project.lyrics=[Lyric("original","retained original",None,None)]
    project.translations=[Translation("translation","retained translation")]
    project.visuals=[Visual("ocr","retained OCR",.2,1.,[0,0,10,10],.9,2)]
    project.settings["frame_markers"]=[3]
    project.mappings=[Mapping("manual",[],text_override="human edit",start=.2,end=1.,confirmed=True,human_edited=True)]
    bridge.media_controller.index=FrameIndex([n*.1 for n in range(40)],4.)
    bridge._changed();QTest.qWait(80)
    before=deepcopy(project.to_dict());snapshot=bridge.document.snapshot();identity=bridge.document.id
    history=deepcopy(bridge.history.undo_stack)
    def forbidden(*args,**kwargs):pytest.fail("entry selection must not start inference")
    monkeypatch.setattr(bridge,"_start_job",forbidden)
    monkeypatch.setattr(bridge,"_start_candidate_job",forbidden)
    monkeypatch.setattr("desktop_qt.local_model_tasks.ModelTasks.start",forbidden)
    def select(entry,answer):
        click(window,find(window,"changeEntryModeButton"));QTest.qWait(80)
        old_snapshot=bridge.document.snapshot();old_mode=bridge._entry_mode
        observed=[];errors=[];timer=QTimer();timer.setInterval(10)
        def answer_actual_dialog():
            box=QApplication.activeModalWidget()
            if box is None:return
            timer.stop()
            try:
                assert isinstance(box,QMessageBox) and box.objectName()=="assetClearConfirmation"
                assert "保留字幕" in box.text()
                assert bridge.document.snapshot()==old_snapshot and bridge._entry_mode==old_mode
                assert box.defaultButton().objectName()=="cancelAssetClear"
                button=box.findChild(QPushButton,answer);assert button is not None
                observed.append(answer)
                QTest.mouseClick(button,Qt.MouseButton.LeftButton)
            except BaseException as error:
                errors.append(error);box.reject()  # Report callback failures outside the modal loop.
        timer.timeout.connect(answer_actual_dialog);timer.start()
        try:click(window,find(window,entry))
        finally:timer.stop()
        if errors:raise errors[0]
        assert observed==[answer],"The real confirmation dialog must be answered"
        assert bridge.document.id==identity and source.read_text(encoding="utf-8")=="retained original"
        assert not bridge.jobs.active and bridge._candidate_job is None
        assert not bridge.uiState["task"]["running"] and not bridge.uiState["candidateTask"]["running"]
    select("audioAutoTimingEntry","cancelAssetClear")
    assert bridge.uiState["entryMode"]=="ocr" and bridge.document.snapshot()==snapshot
    assert bridge.history.undo_stack==history and project.mappings[0].confirmed
    select("audioAutoTimingEntry","detachAssetToEdit")
    assert bridge.document.project.to_dict()['mappings']==before['mappings']
    assert bridge.document.project.inputs==before['inputs']
    assert bridge.uiState["entryMode"]=="edit" and bridge.uiState["stage"]=="review"
    assert len(bridge.history.undo_stack)==len(history)+1
    assert bridge.command('history.undo',{})
    assert bridge.document.project.to_dict()==before and bridge._entry_mode=='ocr'
    from test_qt_window_chrome import walk
    assert len([i for i in walk(window.contentItem()) if i.objectName()=="reviewPanel"])==1


def test_ocr_progress_remains_visible_independent_of_foreground_ai(review_ui):
    ui=review_ui;bridge=ui.bridge
    old_candidate=deepcopy(bridge._candidate_task);old_foreground=deepcopy(bridge._task)
    # Publish controlled task state through the real Bridge, rather than a QML
    # property override that a genuine layout refresh legitimately rebinds.
    try:
        bridge._candidate_task.update(running=True,label="OCR 取样 7 / 20",progress=.35,cancelling=False)
        for foreground_running in (False,True,False):
            bridge._task.update(running=foreground_running,label="AI 配对",api=True,progress=.2)
            bridge._refresh(False);QTest.qWait(60)
            assert find(ui.window,"reviewTaskStatus").isVisible()
            assert find(ui.window,"reviewTaskProgress").property("value")==pytest.approx(.35)
            assert find(ui.window,"reviewCancelCandidateTask").isEnabled()
        shot(ui.window,"ocr-independent-task-progress")
    finally:
        bridge._candidate_task=old_candidate;bridge._task=old_foreground;bridge._refresh(False)


@pytest.mark.parametrize("source,label", [("ocr", " OCR"), ("vocal", "打轴")])
@pytest.mark.parametrize("cancelling", [False, True])
def test_candidate_cancel_labels_match_source(chrome_ui, source, label, cancelling):
    app, bridge, chrome, window = chrome_ui
    old_candidate = deepcopy(bridge._candidate_task)
    old_stage = bridge.stage
    try:
        bridge._candidate_task.update(running=True, source=source, label="synthetic task", progress=.4, cancelling=cancelling)
        expected = ("正在取消" if cancelling else "取消") + label + ("…" if cancelling else "")
        for stage, name in (("import", "importCancelCandidateTaskButton"), ("review", "reviewCancelCandidateTask")):
            bridge.stage = stage
            bridge._refresh(False)
            QTest.qWait(60)
            button = find(window, name)
            assert button.isVisible()
            assert button.property("text") == expected
            assert button.isEnabled() is not cancelling
    finally:
        bridge._candidate_task = old_candidate
        bridge.stage = old_stage
        bridge._refresh(False)


@pytest.mark.parametrize("width", [330, 500, 900])
def test_hidden_cue_header_preserves_compact_geometry(preview_ui, width):
    ui=preview_ui;ui.window.resize(width,700)
    install_cues(ui,layout=False);present(ui,1)
    stage=preview_find(ui,"videoStage");controls=preview_find(ui,"playerControls")
    header=preview_find(ui,"previewHeader");heading=preview_find(ui,"previewHeading")
    def geometry():
        return [(item.x(),item.y(),item.width(),item.height()) for item in (stage,controls,header)]
    before=geometry();media=deepcopy(ui.bridge.media)
    for hidden in (True,False,True):
        ui.panel.setProperty("media",dict(media,hiddenCueCount=1 if hidden else 0,
                             frameMessage="当前字幕在列表显示范围之外，请滚动查看。"*20 if hidden else ""))
        QTest.qWait(60)
        assert geometry()==before
        hint=preview_find(ui,"hiddenCueHint")
        assert hint.isVisible() is hidden
        assert bool(hint.property("text")) is hidden
        if hidden:
            assert hint.x()>=heading.x()+heading.width()
            assert hint.width()>0 and hint.x()+hint.width()<=header.width()+.1
            assert hint.height()<=header.height() and hint.y()>=0
            assert heading.width()>=heading.implicitWidth()


def test_ocr_range_dialog_has_no_qml_size_warnings_through_lifecycle(application,tmp_path):
    from PySide6.QtCore import QCoreApplication, QEvent, QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from desktop_qt.controller import Bridge
    from tools.qt_event_loop_wait import wait_until
    bridge=Bridge(storage_dir=tmp_path/"recovery")
    engine=QQmlApplicationEngine();warnings=[];phase="engine.load"
    engine.warnings.connect(lambda items:warnings.extend((phase,str(item)) for item in items))
    engine.rootContext().setContextProperty("bridge",bridge)
    window=None
    try:
        engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1]/"desktop_qt/qml/Main.qml")))
        assert engine.rootObjects(),warnings
        phase="initial-settle";QTest.qWait(40)
        window=engine.rootObjects()[0]
        panel=find(window,"importRecognitionPanel")
        message=find(window,"missingRegionHint")
        before=deepcopy(bridge.document.project.to_dict())
        for width in (760,1400,900):
            phase="resize-"+str(width)
            window.resize(width,700);QTest.qWait(40)
            assert QMetaObject.invokeMethod(panel,"startOcr")
            QTest.qWait(40)
            assert window.findChild(QObject,"ocrRangeRequiredDialog") is None
            assert message.width()>0 and message.height()>=message.implicitHeight()>0
            assert not find(window,"startOcrButton").isEnabled()
            window.resize(width,800);QTest.qWait(40)
            assert bridge._candidate_job is None and bridge.document.project.to_dict()==before
    finally:
        phase="close-and-shutdown"
        if window is not None:
            window.setProperty("allowClose",True);window.close()
        bridge.shutdown()
        wait_until(lambda:not bridge.media_controller.worker.thread.is_alive(),timeout_ms=3000)
        phase="deferred-destruction"
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        application.processEvents()
    assert not warnings,warnings


@pytest.mark.parametrize("width",[330,500])
def test_narrow_player_real_mouse_is_continuous_bidirectional_and_bounded(preview_ui,width,tmp_path):
    import json
    import math
    ui=preview_ui;ui.window.resize(width,700)
    install_cues(ui,layout=False);present(ui,2)
    panel=ui.panel;controls=preview_find(ui,"playerControls");viewport=preview_find(ui,"videoViewport")
    handle=preview_find(ui,"playerControlsResizeHandle")
    panel.controlsHeightEdited.connect(lambda value:panel.setProperty("controlsExtraHeight",value))
    baseline=controls.height();panel_geometry=(panel.x(),panel.y(),panel.width(),panel.height())
    traces=[]
    # First compress downward (delta > 0 -> extra in [-52, 0]), then expand back upward toward 0 without ever exceeding 0.
    for direction in (1, -1):
        initial=panel.property("effectiveControlsExtraHeight")
        root_y=handle.mapToScene(QPointF()).y()
        start=handle.mapToScene(QPointF(handle.width()/2,handle.height()/2)).toPoint()
        amounts=[round(52*i/10) for i in range(11)]+[72]
        QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,start)
        heights=[]
        for amount in amounts:
            delta=direction*amount
            QTest.mouseMove(ui.window,start+QPoint(0,delta),30);QTest.qWait(20)
            expected=max(-52,min(0,initial-delta))
            actual=panel.property("effectiveControlsExtraHeight")
            assert actual==pytest.approx(expected,abs=.01)
            assert actual <= 0
            if expected == 0:
                assert controls.height()==pytest.approx(baseline,abs=1)
            else:
                assert 50 <= controls.height() < baseline
            actual_y=handle.mapToScene(QPointF()).y()
            assert viewport.height()>=24
            assert (panel.x(),panel.y(),panel.width(),panel.height())==panel_geometry
            heights.append(controls.height())
            traces.append(dict(direction=direction,pointer_delta=delta,expected_extra=expected,actual_extra=actual,handle_root_y=actual_y,controls_height=controls.height(),viewport_height=viewport.height()))
        assert len(set(heights[:11]))>=6
        before_release=(controls.height(),handle.mapToScene(QPointF()).y())
        QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,start+QPoint(0,direction*amounts[-1]));QTest.qWait(40)
        assert (controls.height(),handle.mapToScene(QPointF()).y())==before_release
        assert panel.property("controlsExtraHeight")==pytest.approx(expected)
    (tmp_path/f'narrow-player-{width}-mouse.json').write_text(json.dumps(traces,indent=2))
    assert ui.window.grabWindow().save(str(tmp_path/f'narrow-player-{width}.png'))


def test_saved_wide_extra_has_no_narrow_window_drag_dead_zone(preview_ui,tmp_path):
    import json
    ui=preview_ui;ui.window.resize(900,700)
    install_cues(ui,layout=False);present(ui,2)
    panel=ui.panel;panel.controlsHeightEdited.connect(lambda value:panel.setProperty("controlsExtraHeight",value))
    # Even a legacy positive extra is strictly clamped to 0 (no blank space above controls).
    panel.setProperty("controlsExtraHeight",120);QTest.qWait(40)
    assert panel.property("effectiveControlsExtraHeight")==0
    panel.setProperty("controlsExtraHeight",-12);QTest.qWait(40)
    ui.window.resize(330,700);QTest.qWait(80)
    initial=panel.property("effectiveControlsExtraHeight")
    assert initial==pytest.approx(-12)
    handle=preview_find(ui,"playerControlsResizeHandle")
    start=handle.mapToScene(QPointF(handle.width()/2,handle.height()/2)).toPoint()
    rows=[]
    QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,start)
    for delta in (0,4,8,12,16,20,30,45,-5,-12,-30,0):
        QTest.mouseMove(ui.window,start+QPoint(0,delta),30);QTest.qWait(20)
        expected=max(-52,min(0,initial-delta))
        actual=panel.property("effectiveControlsExtraHeight")
        assert actual==pytest.approx(expected,abs=.01), (delta,initial,actual)
        rows.append(dict(delta=delta,effective=actual,root_y=handle.mapToScene(QPointF()).y()))
    QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,start);QTest.qWait(40)
    assert panel.property("controlsExtraHeight")==pytest.approx(initial)
    (tmp_path/'wide-to-narrow-drag.json').write_text(json.dumps(rows,indent=2))
