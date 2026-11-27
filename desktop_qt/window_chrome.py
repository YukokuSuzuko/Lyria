"""Qt-supported client decoration with a conservative native-frame fallback.

Windows keeps DWM frame styles with a scoped non-client calculation filter.
No global cursor arithmetic or DPI scaling during native interactions.
Qt receives the original pointer gesture through startSystemMove/Resize and
keeps ownership of snapping and interactive window geometry. Offscreen/minimal
and macOS retain their native frame by default; Windows acceptance is separate.
"""
from __future__ import annotations
import ctypes
import sys

from PySide6.QtCore import QObject, Property, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication, QWindow


_CLIENT_DECORATION_PLATFORMS = frozenset({"windows", "xcb", "wayland", "wayland-egl"})
_RESIZE_EDGES = frozenset({
    Qt.Edge.TopEdge.value, Qt.Edge.BottomEdge.value,
    Qt.Edge.LeftEdge.value, Qt.Edge.RightEdge.value,
    (Qt.Edge.TopEdge | Qt.Edge.LeftEdge).value,
    (Qt.Edge.TopEdge | Qt.Edge.RightEdge).value,
    (Qt.Edge.BottomEdge | Qt.Edge.LeftEdge).value,
    (Qt.Edge.BottomEdge | Qt.Edge.RightEdge).value,
})


class WindowChromeController(QObject):
    """Use native interactive operations, never simulate move/resize in Python."""

    enabledChanged = Signal()
    fallbackTriggered = Signal(str)

    def __init__(self, parent=None, *, native_frame=False, platform_name=None):
        super().__init__(parent)
        self._platform_name = (QGuiApplication.platformName()
                               if platform_name is None else platform_name)
        self._enabled = not native_frame and self._platform_name in _CLIENT_DECORATION_PLATFORMS
        self._native_frame = None
        self._fallback_reason = ("requested-native-frame" if native_frame else
                                 "" if self._enabled else "unsupported-platform")

    @Property(bool, notify=enabledChanged)
    def enabled(self):
        return self._enabled

    @Property(str, constant=True)
    def platformName(self):
        return self._platform_name

    @Property(str, notify=enabledChanged)
    def fallbackReason(self):
        return self._fallback_reason

    def _fallback(self, window, reason):
        visibility = window.visibility()
        active = window.isActive()
        if self._native_frame is not None:
            QGuiApplication.instance().removeNativeEventFilter(self._native_frame)
            self._native_frame = None
        self._fallback_reason = reason
        self._enabled = False
        self.enabledChanged.emit()
        # Main.qml also binds this flag to enabled. Explicit removal makes the
        # safety path work for a QWindow without that binding (and in tests).
        # Preserve visibility/maximization, rather than forcing showNormal().
        window.setFlag(Qt.WindowType.FramelessWindowHint, False)
        window.setVisibility(visibility)
        if active:
            window.requestActivate()
        self.fallbackTriggered.emit("当前窗口环境不支持系统拖动或缩放，已恢复系统标题栏。")

    @Slot(QObject)
    def prepareWindow(self, window):
        if isinstance(window, QWindow) and not hasattr(self, "_bounds_policy"):
            from .window_bounds import WindowBounds
            self._bounds_policy = WindowBounds(window)

    @Slot(QObject, result=bool)
    def applyWindowCorners(self, window):
        """Ask Windows 11 DWM for system corners, without a transparent window.

        The preference is not a fixed-radius API. Older Windows and offscreen
        testing can decline it; native move/resize policy is unaffected.
        """
        if (not self._enabled or self._platform_name != "windows"
                or sys.platform != "win32" or QGuiApplication.platformName() != "windows"
                or not isinstance(window, QWindow) or not window.isVisible()):
            return False
        try:
            if self._native_frame is None:
                from .windows_client_frame import WindowsClientFrame
                self._native_frame = WindowsClientFrame()
                QGuiApplication.instance().installNativeEventFilter(self._native_frame)
            if not self._native_frame.apply(int(window.winId())):
                self._fallback(window, "native-frame-style-unavailable")
                return False
            preference = ctypes.c_int(2)  # DWMWCP_ROUND
            setter = ctypes.windll.dwmapi.DwmSetWindowAttribute
            setter.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
            setter.restype = ctypes.c_long
            return setter(int(window.winId()), 33, ctypes.byref(preference), ctypes.sizeof(preference)) == 0
        except (AttributeError, OSError, TypeError, ValueError):
            return False

    @Slot(QObject, result=bool)
    def minimize(self, window):
        if self._native_frame and self._enabled:
            return self._native_frame.system_command(0xF020)  # SC_MINIMIZE
        return False

    @Slot(QObject, result=bool)
    def toggleMaximize(self, window):
        if self._native_frame and self._enabled and isinstance(window, QWindow):
            return self._native_frame.system_command(0xF120 if window.visibility() == QWindow.Visibility.Maximized else 0xF030)
        return False

    @Slot(QObject, result=bool)
    def startMove(self, window):
        if not self._enabled or not isinstance(window, QWindow) or not window.isVisible():
            return False
        if window.visibility() not in (QWindow.Visibility.Windowed, QWindow.Visibility.Maximized):
            return False
        visibility = window.visibility()
        if visibility == QWindow.Visibility.Maximized:
            # A frameless Windows window can otherwise move while retaining
            # maximized geometry. Let Qt restore its saved normal geometry.
            window.showNormal()
        if window.startSystemMove():
            return True
        window.setVisibility(visibility)
        self._fallback(window, "system-move-unavailable")
        return False

    @Slot(QObject, int, result=bool)
    def startResize(self, window, edges):
        if (not self._enabled or not isinstance(window, QWindow) or not window.isVisible()
                or edges not in _RESIZE_EDGES
                or window.visibility() != QWindow.Visibility.Windowed):
            return False
        if window.startSystemResize(Qt.Edge(edges)):
            return True
        self._fallback(window, "system-resize-unavailable")
        return False
