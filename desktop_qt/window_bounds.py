"""Keep normal windows inside the current Qt work area, in logical pixels.

This is not an interactive move/resize implementation. Native operations retain
ownership; the policy runs before first show and after screen/state changes.
"""
from PySide6.QtCore import QObject, QRect, QSize, QTimer
from PySide6.QtGui import QWindow


def fitted_geometry(current, available):
    """Shrink/reposition only an out-of-bounds rectangle, never upscale it."""
    if available.width() <= 0 or available.height() <= 0:
        return QRect(current)
    width = min(current.width(), available.width())
    height = min(current.height(), available.height())
    x = min(max(current.x(), available.x()), available.x() + available.width() - width)
    y = min(max(current.y(), available.y()), available.y() + available.height() - height)
    return QRect(x, y, width, height)


class WindowBounds(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.screen = None
        self.preferred_minimum = QSize(window.minimumWidth(), window.minimumHeight())
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.fit)
        window.screenChanged.connect(self.bind_screen)
        window.visibilityChanged.connect(self.schedule)
        self.bind_screen(window.screen())
        self.fit()

    def schedule(self, *args):
        self.timer.start(0)

    def bind_screen(self, screen):
        if self.screen is not None:
            try:
                self.screen.availableGeometryChanged.disconnect(self.schedule)
                self.screen.logicalDotsPerInchChanged.disconnect(self.schedule)
            except (RuntimeError, TypeError):
                pass
        self.screen = screen
        if screen is not None:
            screen.availableGeometryChanged.connect(self.schedule)
            screen.logicalDotsPerInchChanged.connect(self.schedule)
        self.schedule()

    def fit(self):
        window = self.window
        if self.screen is None or window.visibility() not in (
                QWindow.Visibility.Hidden, QWindow.Visibility.Windowed):
            return False
        # availableGeometry and window.geometry already share Qt logical units.
        # Native-frame fallback needs its actual frame margins reserved as well.
        area = self.screen.availableGeometry().marginsRemoved(window.frameMargins())
        if area.width() <= 0 or area.height() <= 0:
            return False
        minimum = QSize(min(self.preferred_minimum.width(), area.width()),
                        min(self.preferred_minimum.height(), area.height()))
        if window.minimumSize() != minimum:
            window.setMinimumSize(minimum)
        before = window.geometry()
        after = fitted_geometry(before, area)
        if after != before:
            window.setGeometry(after)
        return after != before
