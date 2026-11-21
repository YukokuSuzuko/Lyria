"""Explicit raster fallback for Qt Quick software/offscreen platforms.

QImage remains a native object; no data URLs, image providers or JS image copies.
Normal Windows RHI presentation uses VideoOutput/QVideoSink instead. This path
exists so unsupported software scenegraphs do not silently show an empty video.
"""
from PySide6.QtCore import QRectF,Qt,Signal,Slot,Property
from PySide6.QtGui import QImage,QPainter
from PySide6.QtQuick import QQuickPaintedItem


class FrameSurface(QQuickPaintedItem):
    painted=Signal()
    def __init__(self,parent=None):
        super().__init__(parent);self._image=QImage();self._paint_count=0
        self.setOpaquePainting(False)

    @Slot(QImage)
    def setFrame(self,image):
        self._image=image;self.update()

    @Property(int,notify=painted)
    def paintCount(self):return self._paint_count

    def paint(self,painter):
        image=self._image
        if image.isNull() or self.width()<=0 or self.height()<=0:return
        scale=min(self.width()/image.width(),self.height()/image.height())
        w,h=image.width()*scale,image.height()*scale
        target=QRectF((self.width()-w)/2,(self.height()-h)/2,w,h)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,True)
        painter.drawImage(target,image)
        self._paint_count+=1;self.painted.emit()

_registered=False

def register_types():
    global _registered
    if not _registered:
        from PySide6.QtQml import qmlRegisterType
        from .tool_cursor import ToolCursor
        qmlRegisterType(FrameSurface,'Lyria',1,0,'FrameSurface')
        qmlRegisterType(ToolCursor,'Lyria',1,0,'ToolCursor');_registered=True
