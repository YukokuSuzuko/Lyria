"""Item-local native cursors for the existing timeline input surfaces.

This QObject has no geometry, input handlers, event filter, pointer grab or
application-wide override cursor. Qt Quick retains cursor hit testing and the
existing MouseAreas retain every click/drag. The custom pixmap is a real QCursor,
not a painted imitation following the mouse.
"""
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QObject, Property, QRectF, Signal, Slot, Qt
from PySide6.QtGui import QCursor, QPainter, QPixmap, QScreen
from PySide6.QtQuick import QQuickItem, QQuickWindow
from shiboken6 import isValid
from PySide6.QtSvg import QSvgRenderer


@lru_cache(maxsize=16)
def razor_cursor(forbidden: bool, ratio: float = 1.0) -> QCursor:
    """Rasterize our own scalable artwork only when a native cursor is needed."""
    ratio = max(1.0, ratio)
    size = round(40 * ratio)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    source = 'razor-forbidden-cursor.svg' if forbidden else 'razor-cursor.svg'
    renderer = QSvgRenderer(str(Path(__file__).parent / 'assets' / 'icons' / source))
    if not renderer.isValid():
        raise RuntimeError('缺少时间轴光标图标：' + source)
    painter = QPainter(pixmap)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    # The short vertical dash marks the cut. Keep the original logical hotspot;
    # only the blade artwork moves to its right and leans down-right.
    return QCursor(pixmap, 8, 22)


class ToolCursor(QObject):
    """One cursor owner per target; normalShape=-1 restores Qt inheritance."""
    targetChanged = Signal()
    modeChanged = Signal()
    normalShapeChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._target = None
        self._window = None
        self._screen = None
        self._mode = ''
        self._normal_shape = -1
        # This callback must not reference self: Qt has already invalidated the
        # binding when destroyed fires. A live target still relinquishes the
        # cursor if its binding is explicitly removed before the item itself.
        lease = [None]
        self._lease = lease
        def release_cursor(*_):
            item = lease[0]
            if item is not None and isValid(item):
                item.unsetCursor()
            lease[0] = None
        self.destroyed.connect(release_cursor)

    @Property(QQuickItem, notify=targetChanged)
    def target(self):
        return self._target

    @target.setter
    def target(self, item):
        if self._target is item:
            return
        if self._target is not None and isValid(self._target):
            self._target.windowChanged.disconnect(self._window_changed)
            self._target.destroyed.disconnect(self._target_destroyed)
            self._target.unsetCursor()
        self._target = item
        self._lease[0] = item
        if item is not None:
            item.windowChanged.connect(self._window_changed)
            item.destroyed.connect(self._target_destroyed)
        self._window_changed(item.window() if item is not None else None)
        self.targetChanged.emit()

    @Property(str, notify=modeChanged)
    def mode(self):
        return self._mode

    @mode.setter
    def mode(self, value):
        if value == self._mode:
            return
        self._mode = value
        self._apply()
        self.modeChanged.emit()

    @Property(int, notify=normalShapeChanged)
    def normalShape(self):
        return self._normal_shape

    @normalShape.setter
    def normalShape(self, value):
        if value == self._normal_shape:
            return
        self._normal_shape = value
        self._apply()
        self.normalShapeChanged.emit()

    @Slot()
    def _target_destroyed(self, *_):
        self._target = None
        self._lease[0] = None
        self._window_changed(None)

    @Slot(QQuickWindow)
    def _window_changed(self, window):
        if self._window is not None and isValid(self._window):
            self._window.screenChanged.disconnect(self._screen_changed)
        self._window = window
        if window is not None:
            window.screenChanged.connect(self._screen_changed)
        self._screen_changed(window.screen() if window is not None else None)

    @Slot(QScreen)
    def _screen_changed(self, screen):
        if self._screen is not None and isValid(self._screen):
            self._screen.logicalDotsPerInchChanged.disconnect(self._apply)
        self._screen = screen
        if screen is not None:
            screen.logicalDotsPerInchChanged.connect(self._apply)
        self._apply()

    @Slot()
    @Slot(float)
    def _apply(self, *_):
        if self._target is None or not isValid(self._target):
            return
        if self._mode in ('razor', 'forbidden'):
            ratio = self._window.devicePixelRatio() if self._window is not None and isValid(self._window) else 1.0
            self._target.setCursor(razor_cursor(self._mode == 'forbidden', ratio))
        elif self._normal_shape >= 0:
            self._target.setCursor(QCursor(Qt.CursorShape(self._normal_shape)))
        else:
            self._target.unsetCursor()
