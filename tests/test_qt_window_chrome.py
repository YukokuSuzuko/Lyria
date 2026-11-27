"""Native-operation contracts and actual QML input, not Windows qualification.

The offscreen UI fixture explicitly selects the Windows *policy* for decoration
and intercepts native move/resize calls. This proves event routing/fallback and
logical geometry, not OS snapping, monitor transitions, or real native resize.
"""
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QAccessible, QWindow
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop_qt.controller import Bridge
from desktop_qt.window_chrome import WindowChromeController

ROOT = Path(__file__).resolve().parents[1]
EDGES = [1, 2, 4, 8, 3, 5, 10, 12]


@pytest.fixture(scope="module")
def application():
    QQuickStyle.setStyle("Basic")
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("platform,enabled", [
    ("windows", True), ("xcb", True), ("wayland", True), ("wayland-egl", True),
    ("cocoa", False), ("offscreen", False), ("minimal", False), ("unknown", False),
])
def test_platform_policy_and_native_escape_hatch(application, platform, enabled):
    assert WindowChromeController(platform_name=platform).enabled is enabled
    explicit = WindowChromeController(platform_name=platform, native_frame=True)
    assert not explicit.enabled and explicit.fallbackReason == "requested-native-frame"


def test_native_move_delegates_without_setting_geometry(application, monkeypatch):
    window = QWindow(); window.resize(760, 600); window.show()
    chrome = WindowChromeController(platform_name="windows")
    calls = []
    monkeypatch.setattr(window, "startSystemMove", lambda: calls.append("move") or True)
    geometry = window.geometry()
    try:
        assert chrome.startMove(window)
        assert calls == ["move"] and window.geometry() == geometry
        assert chrome.enabled
    finally:
        window.close()

def test_maximized_drag_restores_before_native_move_and_rejects_fullscreen(application, monkeypatch):
    window=QWindow(); window.resize(760,600); window.show()
    chrome=WindowChromeController(platform_name='windows')
    calls=[]
    monkeypatch.setattr(window,'startSystemMove',lambda:calls.append(window.visibility()) or True)
    try:
        window.showMaximized()
        assert chrome.startMove(window)
        assert calls == [QWindow.Visibility.Windowed]
        window.showFullScreen()
        assert not chrome.startMove(window)
        assert window.visibility() == QWindow.Visibility.FullScreen
        assert len(calls)==1
    finally:
        window.close()


@pytest.mark.parametrize("edges", EDGES)
def test_all_valid_native_resize_edges(application, monkeypatch, edges):
    window = QWindow(); window.resize(760, 600); window.show()
    chrome = WindowChromeController(platform_name="windows")
    calls = []
    monkeypatch.setattr(window, "startSystemResize", lambda edge: calls.append(edge.value) or True)
    geometry = window.geometry()
    try:
        assert chrome.startResize(window, edges)
        assert calls == [edges] and window.geometry() == geometry
    finally:
        window.close()


def test_resize_rejects_invalid_edges_hidden_and_maximized(application, monkeypatch):
    window = QWindow(); window.show()
    chrome = WindowChromeController(platform_name="windows")
    calls = []
    monkeypatch.setattr(window, "startSystemResize", lambda edge: calls.append(edge) or True)
    try:
        for edges in (0, 6, 9, 15, -1, 16):
            assert not chrome.startResize(window, edges)
        window.showMaximized()
        assert not chrome.startResize(window, 1)
        window.hide()
        assert not chrome.startResize(window, 1)
        assert not chrome.startMove(window)
        assert not chrome.startResize(QObject(), 1)
        assert not calls and chrome.enabled
    finally:
        window.close()


@pytest.mark.parametrize("operation,maximized", [("move", False), ("move", True), ("resize", False)])
def test_unsupported_native_operation_restores_native_frame(application, monkeypatch, operation, maximized):
    window = QWindow(); window.setFlag(Qt.WindowType.FramelessWindowHint, True)
    window.resize(760, 600)
    window.showMaximized() if maximized else window.show()
    chrome = WindowChromeController(platform_name="windows")
    calls = []
    chrome.fallbackTriggered.connect(calls.append)
    monkeypatch.setattr(window, "startSystemMove", lambda: False)
    monkeypatch.setattr(window, "startSystemResize", lambda edge: False)
    visibility = window.visibility()
    try:
        result = chrome.startMove(window) if operation == "move" else chrome.startResize(window, 1)
        assert not result and not chrome.enabled
        assert chrome.fallbackReason == "system-" + operation + "-unavailable"
        assert not window.flags() & Qt.WindowType.FramelessWindowHint
        assert window.isVisible() and window.visibility() == visibility
        assert len(calls) == 1
        assert not chrome.startMove(window)  # Do not loop failed native requests.
        assert len(calls) == 1
    finally:
        window.close()


def walk(item):
    yield item
    for child in item.childItems():
        yield from walk(child)


def find(window, name):
    result = next((item for item in walk(window.contentItem()) if item.objectName() == name), None)
    assert result is not None, name
    return result


def center(item):
    return item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()


def click(window, item):
    assert item.isVisible() and item.isEnabled()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center(item))
    QTest.qWait(30)


@pytest.fixture
def ui(application, tmp_path, request):
    bridge = engine = window = None
    try:
        bridge = Bridge(storage_dir=tmp_path / "recovery")
        chrome = WindowChromeController(platform_name=getattr(request, "param", "windows"))
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("bridge", bridge)
        engine.rootContext().setContextProperty("windowChrome", chrome)
        engine.load(QUrl.fromLocalFile(str(ROOT / "desktop_qt/qml/Main.qml")))
        assert engine.rootObjects(), "Actual Main.qml did not load"
        window = engine.rootObjects()[0]
        QTest.qWait(70)
        # Let production first-show work-area fitting finish before the fixture's
        # explicit user resize. The offscreen screen is only 800x800; inheriting its
        # initial narrow layout makes a wide-layout test click clipped coordinates.
        from tools.qt_event_loop_wait import wait_until
        wait_until(lambda: window.isVisible() and not chrome._bounds_policy.timer.isActive(),
                   timeout_ms=1000)
        window.resize(1400, 940)
        QTest.qWait(70)
        assert (window.width(), window.height()) == (1400, 940)
        yield application, bridge, chrome, window
    finally:
        if bridge is not None:
            bridge.shutdown()
            bridge.wait_for_shutdown(application)
            assert bridge.shutdown_complete
            assert not bridge.media_controller.worker.thread.is_alive()
            assert not bridge.jobs.live_threads()
        if window is not None:
            window.setProperty('allowClose', True)
            window.close()
        if engine is not None:
            from PySide6.QtCore import QEvent
            destroyed=[]
            engine.destroyed.connect(lambda: destroyed.append(True))
            engine.deleteLater()
            application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            from tools.qt_event_loop_wait import wait_until
            wait_until(lambda: bool(destroyed))
        application.processEvents()


@pytest.mark.parametrize("width", [760, 900, 1400])
def test_header_fits_with_reserved_nonoverlapping_caption_and_grips(ui, width, tmp_path):
    app, bridge, chrome, window = ui
    window.resize(width, 900); QTest.qWait(80)
    assert window.property("integratedTitlebar")
    assert window.flags() & Qt.WindowType.FramelessWindowHint
    assert window.minimumWidth() == 760
    caption = find(window, "titlebarCaption")
    controls = find(window, "windowControls")
    assert caption.width() >= 48
    assert controls.width() == 108
    for name in ("fileMenuButton", "toolsMenuButton", "windowMinimize", "windowMaximize", "windowClose"):
        item = find(window, name)
        start = item.mapToScene(QPointF(0, 0))
        assert item.isVisible() and start.x() >= 6 and start.y() >= 6
        assert start.x() + item.width() <= window.width() - 6
        assert start.y() + item.height() < 60
        assert not caption.contains(caption.mapFromScene(QPointF(center(item))))
    for edge in EDGES:
        grip = find(window, "windowResize_" + str(edge))
        assert grip.isEnabled() and grip.isVisible()
        assert grip.width() == 6 or grip.height() == 6
        assert not grip.contains(grip.mapFromScene(QPointF(center(caption))))
    evidence = Path(os.environ.get("LYRIA_CHROME_EVIDENCE", str(tmp_path)))
    evidence.mkdir(parents=True, exist_ok=True)
    assert window.grabWindow().save(str(evidence / ("integrated-titlebar-" + str(width) + ".png")))


@pytest.mark.parametrize("ui", ["offscreen"], indirect=True)
def test_qml_unsupported_platform_keeps_native_frame_and_menus(ui):
    app, bridge, chrome, window = ui
    assert not chrome.enabled and not window.property("integratedTitlebar")
    assert not window.flags() & Qt.WindowType.FramelessWindowHint
    assert not find(window, "windowControls").isVisible()
    assert not find(window, "windowResizeFrame").isEnabled()
    click(window, find(window, "fileMenuButton"))
    assert window.findChild(QObject, "fileMenu").property("visible")
    QTest.keyClick(window, Qt.Key.Key_Escape)


def test_caption_drag_and_all_grips_route_only_native_operations(ui, monkeypatch):
    app, bridge, chrome, window = ui
    moves, resizes = [], []
    monkeypatch.setattr(window, "startSystemMove", lambda: moves.append(True) or True)
    monkeypatch.setattr(window, "startSystemResize", lambda edges: resizes.append(edges.value) or True)
    original = window.geometry()
    caption = find(window, "titlebarCaption")
    start = center(caption)
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(window, start + QPoint(30, 0), 30)
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(30, 0))
    assert moves == [True]
    for edge in EDGES:
        click(window, find(window, "windowResize_" + str(edge)))
    assert resizes == EDGES and window.geometry() == original
    click(window, find(window, "fileMenuButton"))
    menu = window.findChild(QObject, "fileMenu")
    assert menu and menu.property("visible")
    QTest.keyClick(window, Qt.Key.Key_Escape)
    assert moves == [True] and resizes == EDGES


def test_caption_doubleclick_buttons_and_keyboard(ui, monkeypatch):
    app, bridge, chrome, window = ui
    window.resize(760, 900); QTest.qWait(60)
    # An offscreen 800 px work area must not restore the deliberately oversized
    # 900 px fixture below its work area. In-bounds sizes remain unchanged.
    from desktop_qt.window_bounds import fitted_geometry
    normal_size = fitted_geometry(window.geometry(), window.screen().availableGeometry().marginsRemoved(window.frameMargins())).size()
    caption = find(window, "titlebarCaption")
    QTest.mouseDClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center(caption))
    QTest.qWait(40)
    assert window.visibility() == QWindow.Visibility.Maximized
    assert not find(window, "windowResizeFrame").isEnabled()
    assert find(window, "windowMaximize").property("text") == "还原"
    previous_focus = find(window, "fileMenuButton")
    previous_focus.forceActiveFocus()
    click(window, find(window, "windowMaximize"))
    assert window.visibility() == QWindow.Visibility.Windowed and window.size() == normal_size
    assert previous_focus.hasActiveFocus()
    # A ready-media Space shortcut must not steal keyboard button activation.
    original_snapshot = bridge.media_controller.snapshot
    monkeypatch.setattr(bridge.media_controller, "snapshot", lambda: {**original_snapshot(), "ready": True})
    bridge.mediaChanged.emit(); app.processEvents()
    button = find(window, "windowMaximize")
    button.forceActiveFocus(); QTest.qWait(20)
    assert button.hasActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Space); QTest.qWait(40)
    assert window.visibility() == QWindow.Visibility.Maximized
    for name, text in (("windowMinimize", "最小化"), ("windowMaximize", "还原"), ("windowClose", "关闭窗口")):
        interface = QAccessible.queryAccessibleInterface(find(window, name))
        assert interface and interface.text(QAccessible.Text.Name) == text
    click(window, find(window, "windowMinimize"))
    assert window.visibility() == QWindow.Visibility.Minimized
    window.showNormal(); QTest.qWait(30)
    assert window.isVisible()


def test_custom_close_keeps_unsaved_protection_and_cancel(ui):
    app, bridge, chrome, window = ui
    assert bridge.command("row.add", {})
    assert bridge.state["project"]["dirty"]
    for _ in range(2):
        click(window, find(window, "windowClose"))
        dialog = window.findChild(QObject, "closeDialog")
        assert window.isVisible() and dialog.property("visible")
        click(window, find(window, "closeCancel"))
        assert not dialog.property("visible") and window.isVisible()
        assert bridge.state["project"]["dirty"]


def test_qml_failed_system_drag_reverts_decoration_without_losing_document(ui, monkeypatch):
    app, bridge, chrome, window = ui
    assert bridge.command("row.add", {})
    before = bridge.document.snapshot()
    monkeypatch.setattr(window, "startSystemMove", lambda: False)
    caption = find(window, "titlebarCaption")
    start = center(caption)
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(window, start + QPoint(30, 0), 30)
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(30, 0))
    QTest.qWait(40)
    assert not chrome.enabled and not window.property("integratedTitlebar")
    assert not window.flags() & Qt.WindowType.FramelessWindowHint
    assert not find(window, "windowControls").isVisible()
    assert not find(window, "windowResizeFrame").isEnabled()
    assert window.isVisible() and bridge.document.snapshot() == before
