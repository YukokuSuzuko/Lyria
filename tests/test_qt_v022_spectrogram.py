"""Offscreen physical-pixel and repeated-interaction spectrum contracts."""
import json,time,wave
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage,QPainter
from PySide6.QtTest import QTest
from desktop_qt.spectrogram_item import SpectrogramItem
from lyricsync.spectrogram import RATE

@pytest.fixture
def surface(tmp_path):
    app=QApplication.instance() or QApplication([])
    def audio(name,at):
        path=tmp_path/name;pcm=np.zeros(RATE*3,np.int16);pcm[round(at*RATE)]=30000
        with wave.open(str(path),'wb') as w:w.setparams((1,2,RATE,0,'NONE','not compressed'));w.writeframes(pcm.tobytes())
        return path
    a=audio('a.wav',1.);b=audio('b.wav',2.)
    item=SpectrogramItem();item.setWidth(400);item.setHeight(100)
    item._span=250.;item._duration=3.;item._active=True;item._window=256;item._preset='physical';item._tilt=0.
    item.sourcePath=str(a)
    def ready():
        end=time.monotonic()+10
        while item.status!='ready' and time.monotonic()<end:app.processEvents();QTest.qWait(2)
        assert item.status=='ready',item.error
    def render():
        dpr=item.pixelRatio;image=QImage(round(item.width()*dpr),round(item.height()*dpr),QImage.Format.Format_RGB888);image.fill(0)
        painter=QPainter(image);painter.scale(dpr,dpr);item.paint(painter);painter.end()
        return np.frombuffer(image.bits(),dtype=np.uint8,count=image.sizeInBytes()).reshape(image.height(),image.bytesPerLine())[:,:image.width()*3].reshape(image.height(),image.width(),3).copy()
    ready()
    yield item,ready,render,b,tmp_path
    item._timer.stop();item._busy_timer.stop()
    if item._worker:item._worker.stop();item._worker.thread.join(5)
    item.deleteLater();app.processEvents()

@pytest.mark.parametrize('dpr',[1.,1.25,1.5,2.])
def test_pulse_position_pan_zoom_and_same_cycle_source_clear(surface,dpr):
    item,ready,render,b,path=surface;item.pixelRatio=dpr
    for first,span in [(1.,250.),(41.,180.),(71.,60.)]:
        item.firstFrame=first;item.frameSpan=span;ready();rgb=render()
        profile=rgb.astype(float).max(axis=2).mean(axis=0);x=np.arange(len(profile))+.5
        centroid=float((profile*x).sum()/profile.sum())
        expected=(101-first)/span*item.width()*dpr
        assert abs(centroid-expected)<=1.,(dpr,first,span,centroid,expected)
    item.sourcePath=str(b)
    assert not render().any()  # same event cycle, before processing worker replies


def test_60hz_pan_resize_zoom_and_fft_reuse(surface):
    item,ready,render,b,path=surface
    pan_black=resize_black=text_flashes=0
    for i in range(30):
        item.firstFrame=1.+i*.5
        pan_black+=not render().any();QTest.qWait(17)
    ready();fft=item.metrics['fftTiles']
    for i in range(30):
        item.setHeight(100+i*2)
        resize_black+=not render().any();text_flashes+=int(item.busyVisible);QTest.qWait(17)
    ready();height_fft=item.metrics['fftTiles'];assert height_fft==fft
    for i in range(15):item.frameSpan=250.-i*5;QTest.qWait(17)
    ready()
    result=dict(panSteps=30,resizeSteps=30,zoomSteps=15,panBlackFrames=int(pan_black),resizeBlackFrames=int(resize_black),resizeBusyFrames=text_flashes,fftBeforeHeight=fft,fftAfterHeight=height_fft,fftAfterZoom=item.metrics['fftTiles'])
    (path/'interaction-metrics.json').write_text(json.dumps(result),encoding='utf-8')
    assert pan_black==resize_black==text_flashes==0,result
