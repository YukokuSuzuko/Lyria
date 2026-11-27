"""Pixel checks use actual capture dimensions, not misleading offscreen DPR metadata."""
from PySide6.QtCore import QPointF


def assert_painted_editor_text(window, editor):
    image=window.grabWindow();scale=image.width()/window.width()
    position=editor.mapToScene(QPointF(0,0))
    box=(max(0,round(position.x()*scale)),max(0,round(position.y()*scale)),
         min(image.width(),round((position.x()+editor.width())*scale)),
         min(image.height(),round((position.y()+editor.height())*scale)))
    area=max(0,box[2]-box[0])*max(0,box[3]-box[1]);dark=0
    for y in range(box[1],box[3]):
        for x in range(box[0],box[2]):
            color=image.pixelColor(x,y)
            dark+=max(color.red(),color.green(),color.blue())<120
    # Reject both missing glyphs and an entirely black broken layer surface.
    assert 20*scale*scale<=dark<=area*.2,(editor.objectName(),box,dark,area)
    return dict(box=box,darkGlyphPixels=dark,pixelArea=area)
