"""The no-ROI decision enters each intended flow directly, offscreen."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from pathlib import Path
from PySide6.QtCore import QMetaObject,QObject,QUrl
from PySide6.QtQml import QQmlApplicationEngine
from test_qt_startup_phase5 import application,find,click
from tools.qt_event_loop_wait import delay
from desktop_qt.controller import Bridge


def test_no_roi_two_direct_choices(application,tmp_path,monkeypatch):
    b=Bridge(storage_dir=tmp_path/'storage')
    b._startup_visible=False
    b.media_controller.metadata['media_kind']='video'
    b.media_controller._state.update(ready=True,mediaKind='video',width=640,height=360)
    b.document.project.video.update(width=640,height=360)
    b._refresh()
    calls=[]
    monkeypatch.setattr(b,'_auto_roi',lambda:calls.append('auto'))
    monkeypatch.setattr(b,'_run_ocr',lambda *a,**k:calls.append('ocr'))
    e=QQmlApplicationEngine();warnings=[]
    e.warnings.connect(lambda items:warnings.extend(i.toString() for i in items))
    e.rootContext().setContextProperty('bridge',b)
    e.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1]/'desktop_qt/qml/Main.qml')))
    assert e.rootObjects(),warnings
    w=e.rootObjects()[0]
    try:
        delay(150)
        assert find(w,'roiSetupHint').property('text')=='尚未设置字幕选区。可自动检测硬字幕位置，也可直接在画面上手动框选。'
        assert find(w,'separationScopeNotice').property('text')=='不需要歌词；默认处理整首音频，程序自动分段并合并，不需要手动切片。'
        assert find(w,'startSeparationOnly').property('text')=='仅人声分离'
        panel=find(w,'importRecognitionPanel')
        QMetaObject.invokeMethod(panel,'startOcr');delay(80)
        assert w.findChild(QObject,'ocrRangeRequiredDialog') is None and not find(w,'startOcrButton').isEnabled()
        assert find(w,'missingRegionHint').isVisible() and not calls
        click(w,find(w,'detectOcrRegions'));delay(80)
        assert calls==['auto']
        assert not find(w,'mainPreviewPanel').property('roiSelecting')
        QMetaObject.invokeMethod(panel,'startOcr');delay(80)
        click(w,find(w,'addOcrRegion'));delay(80)
        assert find(w,'mainPreviewPanel').property('roiSelecting')
        assert find(w,'roiSetupHint').property('text')=='请在画面上拖动鼠标框住硬字幕区域'
        assert calls==['auto']
        assert not warnings,warnings
    finally:
        w.setProperty('allowClose',True);w.close();b.shutdown();b.wait_for_shutdown(application);e.deleteLater();application.processEvents()
