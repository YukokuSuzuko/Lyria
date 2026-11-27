"""Offscreen QML contracts for subtitle fallback and ruler boundaries.

This is not evidence of Windows GPU, hardware video, or visual performance.
"""
import os
import time
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication
from PySide6.QtQuickControls2 import QQuickStyle
from desktop_qt.controller import Bridge
from lyricsync.model import Mapping, Translation


def wait(app, predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


def find(item, name):
    if item.objectName() == name:
        return item
    for child in item.childItems():
        found = find(child, name)
        if found is not None:
            return found
    return None


def test_subtitle_fallback_and_ruler_edges(tmp_path):
    app = QApplication.instance() or QApplication([])
    QQuickStyle.setStyle('Basic')
    bridge = Bridge(storage_dir=tmp_path)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('bridge', bridge)
    engine.load(QUrl.fromLocalFile(str(Path('desktop_qt/qml/Main.qml').resolve())))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    project = bridge.document.project
    project.video = {'duration': 3.0, 'width': 640, 'height': 360}
    project.translations = [Translation('t', '预览回退测试')]
    project.mappings = [Mapping('m', ['t'], start=.5, end=1.5)]
    bridge.selected = 'm'
    bridge.stage = 'review'
    bridge._changed()
    bridge.layout_timer.stop()
    bridge._layout_revision = -1
    bridge._layout_error = '模拟排版失败'
    bridge.media_controller._state.update(ready=True, displayedFrame=1, positionSeconds=.6, totalFrames=90)
    bridge.mediaChanged.emit()
    try:
        wait(app, lambda: find(window.contentItem(), 'subtitleFallback') is not None)
        fallback = find(window.contentItem(), 'subtitleFallback')
        wait(app, lambda: fallback.property('visible'))
        assert fallback.property('text') == '预览回退测试'
        bridge.command('settings.set', {'key': 'subtitlePreview', 'value': False})
        wait(app, lambda: not fallback.property('visible'))
        bridge.command('settings.set', {'key': 'subtitlePreview', 'value': True})
        wait(app, lambda: fallback.property('visible'))
        # Ruler end labels must be fully hidden rather than clipped.
        window.setProperty('stage', 'review')
        wait(app, lambda: find(window.contentItem(), 'timelineTickLabel_0') is not None)
        assert not find(window.contentItem(), 'timelineTickLabel_0').property('visible')
        assert find(window.contentItem(), 'timelineTickLabel_1').property('visible')
        assert not find(window.contentItem(), 'timelineTickLabel_10').property('visible')
    finally:
        window.close()
        bridge.shutdown()


def test_manual_timing_shortcut_pairs_frames(tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from lyricsync.frames import FrameIndex
    app = QApplication.instance() or QApplication([])
    bridge = Bridge(storage_dir=tmp_path)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('bridge', bridge)
    engine.load(QUrl.fromLocalFile(str(Path('desktop_qt/qml/Main.qml').resolve())))
    window = engine.rootObjects()[0]
    bridge.document.project.video = {'duration': 3.0, 'width': 640, 'height': 360}
    bridge.media_controller.index = FrameIndex([n*.1 for n in range(30)], 3.0)
    bridge.media_controller._state.update(ready=True, displayedFrame=3, requestedFrame=3, totalFrames=30, positionSeconds=.2)
    bridge.stage='review'; bridge._refresh()
    bridge.command('timeline.manualMode', {'enabled': True})
    try:
        wait(app, lambda: find(window.contentItem(), 'manualTimingMode') is not None)
        window.requestActivate(); QTest.qWait(100)
        QTest.keyClick(window, Qt.Key.Key_D); app.processEvents()
        wait(app, lambda: bridge.state['manualTiming']['startFrame'] == 3)
        bridge.media_controller._state.update(displayedFrame=6, requestedFrame=6, positionSeconds=.5)
        bridge.mediaChanged.emit(); QTest.keyClick(window, Qt.Key.Key_D)
        wait(app, lambda: len(bridge.document.project.mappings) == 1)
        cue=bridge.document.project.mappings[0]
        assert (cue.start, cue.end) == pytest.approx((.2,.6))
        # Dedicated create mode drags over an empty lane without seeking.
        from PySide6.QtCore import QPointF
        timeline=find(window.contentItem(), 'frameTimeline')
        timeline.setProperty('createMode', True)
        ruler=find(window.contentItem(), 'timelineRulerMouse')
        x1=ruler.width()*.5; x2=ruler.width()*.75
        start=ruler.mapToScene(QPointF(x1, 45)).toPoint()
        end=ruler.mapToScene(QPointF(x2, 45)).toPoint()
        QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        QTest.mouseMove(window, end, 30)
        QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
        wait(app, lambda: len(bridge.document.project.mappings) == 2)
        assert bridge.media_controller.snapshot()['displayedFrame'] == 6
    finally:
        window.close(); bridge.shutdown()


def test_roi_choice_opens_manual_selector_without_default_box(tmp_path):
    from PySide6.QtCore import Qt, QPointF
    from PySide6.QtTest import QTest
    app=QApplication.instance() or QApplication([])
    bridge=Bridge(storage_dir=tmp_path)
    engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('bridge',bridge)
    engine.load(QUrl.fromLocalFile(str(Path('desktop_qt/qml/Main.qml').resolve())))
    window=engine.rootObjects()[0]
    bridge.document.project.video={'width':640,'height':360,'duration':3.0}
    bridge.media_controller._state.update(ready=True,displayedFrame=1,requestedFrame=1,totalFrames=90)
    bridge._refresh()
    try:
        assert bridge.state['roi'] is None
        button=find(window.contentItem(),'selectRoiButton')
        wait(app,lambda:button is not None and button.isEnabled())
        point=button.mapToScene(QPointF(button.width()/2,button.height()/2)).toPoint()
        QTest.mouseClick(window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point)
        wait(app,lambda:find(window.contentItem(),'manualRoiChoice') is not None)
        manual=find(window.contentItem(),'manualRoiChoice')
        wait(app,lambda:manual.isVisible())
        point=manual.mapToScene(QPointF(manual.width()/2,manual.height()/2)).toPoint()
        QTest.mouseClick(window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point)
        panel=find(window.contentItem(),'mainPreviewPanel')
        wait(app,lambda:panel.property('roiSelecting'))
        assert bridge.state['roi'] is None
    finally:
        window.close();bridge.shutdown()


def test_ctrl_multiselect_and_context_adjacent_merge(tmp_path):
    from PySide6.QtCore import Qt, QPointF
    from PySide6.QtTest import QTest
    app=QApplication.instance() or QApplication([])
    bridge=Bridge(storage_dir=tmp_path)
    engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('bridge',bridge)
    engine.load(QUrl.fromLocalFile(str(Path('desktop_qt/qml/Main.qml').resolve())))
    window=engine.rootObjects()[0];p=bridge.document.project
    p.translations=[Translation(str(i),f'歌词{i}') for i in range(3)]
    p.mappings=[Mapping(str(i),[str(i)],start=float(i),end=float(i+1)) for i in range(3)]
    bridge.stage='review';bridge.selected='0';bridge._changed()
    def click(item):
        point=item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint()
        QTest.mouseClick(window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point)
        app.processEvents()
    try:
        wait(app,lambda:find(window.contentItem(),'lyricSelect_0') is not None and find(window.contentItem(),'lyricSelect_0').isVisible())
        assert find(window.contentItem(),'multiSelectMode') is None
        assert find(window.contentItem(),'mergeSelected') is None
        a=find(window.contentItem(),'lyricSelect_0');b=find(window.contentItem(),'lyricSelect_1')
        click(a)
        point=b.mapToScene(QPointF(b.width()/2,b.height()/2)).toPoint()
        QTest.mouseClick(window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier,point)
        wait(app,lambda:len(bridge.state['multiSelectedIds'])==2)
        context=find(window.contentItem(),'lyricContext_1')
        point=context.mapToScene(QPointF(15,15)).toPoint()
        QTest.mouseClick(window,Qt.MouseButton.RightButton,Qt.KeyboardModifier.NoModifier,point)
        wait(app,lambda:find(window.contentItem(),'lyricMergeAction_1') is not None and find(window.contentItem(),'lyricMergeAction_1').isVisible())
        merge=find(window.contentItem(),'lyricMergeAction_1');assert merge.isEnabled();click(merge)
        wait(app,lambda:len(bridge.document.project.mappings)==2)
        assert bridge.state['multiSelectedIds']==[]
    finally:
        window.close();bridge.shutdown()


def test_follow_current_toggle_preserves_manual_view(tmp_path):
    from PySide6.QtCore import Qt, QPointF
    from PySide6.QtTest import QTest
    from lyricsync.frames import FrameIndex
    app=QApplication.instance() or QApplication([]);bridge=Bridge(storage_dir=tmp_path)
    engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('bridge',bridge)
    engine.load(QUrl.fromLocalFile(str(Path('desktop_qt/qml/Main.qml').resolve())))
    window=engine.rootObjects()[0];p=bridge.document.project
    p.video={'duration':30};p.translations=[Translation('a','甲'),Translation('b','乙')]
    p.mappings=[Mapping('a',['a'],start=1,end=2),Mapping('b',['b'],start=20,end=21)]
    bridge.media_controller.index=FrameIndex([n*.1 for n in range(300)],30)
    bridge.media_controller._state.update(ready=True,displayedFrame=11,requestedFrame=11,totalFrames=300)
    bridge.selected='a';bridge.stage='review';bridge._changed()
    try:
        wait(app,lambda:find(window.contentItem(),'followCurrentButton') is not None and find(window.contentItem(),'followCurrentButton').isVisible())
        timeline=find(window.contentItem(),'frameTimeline');button=find(window.contentItem(),'followCurrentButton')
        point=button.mapToScene(QPointF(button.width()/2,button.height()/2)).toPoint()
        QTest.mouseClick(window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point)
        wait(app,lambda:not timeline.property('followEnabled'))
        before=(timeline.property('viewFirst'),timeline.property('viewLast'))
        bridge.selected='b';bridge._refresh(False);app.processEvents()
        assert (timeline.property('viewFirst'),timeline.property('viewLast'))==before
        QTest.mouseClick(window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point)
        wait(app,lambda:timeline.property('followEnabled'))
        assert timeline.property('viewLast')>=201
    finally:window.close();bridge.shutdown()



def test_ocr_ai_checkbox_is_hidden_and_stale_toggle_never_sends(tmp_path,monkeypatch):
    from PySide6.QtCore import Qt, QPointF
    from PySide6.QtTest import QTest
    app=QApplication.instance() or QApplication([]);bridge=Bridge(storage_dir=tmp_path)
    engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('bridge',bridge)
    engine.load(QUrl.fromLocalFile(str(Path('desktop_qt/qml/Main.qml').resolve())))
    window=engine.rootObjects()[0]
    calls=[];monkeypatch.setattr(bridge,'_api_alignment',lambda payload,**kwargs:calls.append(payload))
    def forbidden(*args,**kwargs):raise AssertionError('No real provider call in UI test')
    monkeypatch.setattr('urllib.request.OpenerDirector.open',forbidden)
    try:
        wait(app,lambda:find(window.contentItem(),'ocrAutoAiCheckBox') is not None)
        notice=find(window.contentItem(),'ocrManualAiNotice')
        assert '仅运行本地 OCR' in notice.property('text') and '不自动发送 AI 请求' in notice.property('text')
        checkbox=find(window.contentItem(),'ocrAutoAiCheckBox')
        assert not checkbox.property('checked')
        before=(bridge.document.project.to_dict(),bridge.document.revision)
        assert not checkbox.isVisible()
        # Emulate stale state without claiming a hidden control is user-clickable.
        checkbox.setProperty('checked',True)
        app.processEvents()
        assert not checkbox.isVisible()
        assert not bridge.command('alignment.batch',{})
        assert (bridge.document.project.to_dict(),bridge.document.revision)==before
        assert calls==[] and not bridge.document.project.api_records
        assert find(window.contentItem(),'importPairingStructureButton') is None
    finally:window.close();bridge.shutdown()


def test_drag_last_edge_snaps_to_next_cue(tmp_path):
    from PySide6.QtCore import Qt, QPointF
    from PySide6.QtTest import QTest
    from lyricsync.frames import FrameIndex
    app=QApplication.instance() or QApplication([]);bridge=Bridge(storage_dir=tmp_path)
    engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('bridge',bridge)
    engine.load(QUrl.fromLocalFile(str(Path('desktop_qt/qml/Main.qml').resolve())))
    window=engine.rootObjects()[0];p=bridge.document.project
    p.video={'duration':10};p.translations=[Translation('a','甲'),Translation('b','乙'),Translation('c','丙')]
    p.mappings=[Mapping('a',['a'],start=1,end=2),Mapping('b',['b'],start=3,end=4),Mapping('c',['c'],start=5,end=6)]
    bridge.media_controller.index=FrameIndex([n*.1 for n in range(100)],10)
    bridge.media_controller._state.update(ready=True,displayedFrame=31,requestedFrame=31,totalFrames=100)
    bridge.selected='b';bridge.stage='review';bridge._changed()
    try:
        wait(app,lambda:find(window.contentItem(),'cueEdge_last') is not None and find(window.contentItem(),'cueEdge_last').isVisible())
        edge=find(window.contentItem(),'cueEdge_last');ruler=find(window.contentItem(),'timelineRulerMouse');timeline=find(window.contentItem(),'frameTimeline')
        first=timeline.property('viewFirst');span=timeline.property('viewLast')-first
        start=edge.mapToScene(QPointF(edge.width()/2,edge.height()/2)).toPoint()
        # PR-style magnetism is eight logical pixels, not a fixed frame gap.
        # Aim two pixels before exclusive boundary F51, so inclusive end is F50.
        end=ruler.mapToScene(QPointF((51-first)/span*ruler.width()-2,edge.mapToItem(ruler,QPointF(0,edge.height()/2)).y())).toPoint()
        QTest.mousePress(window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start)
        for n in range(1,7):QTest.mouseMove(window,start+(end-start)*n/6,20)
        QTest.mouseRelease(window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,end)
        wait(app,lambda:bridge.state['selection']['current']['lastFrame']==50)
        assert bridge.state['selection']['next']['firstFrame']==51
    finally:window.close();bridge.shutdown()


def test_drag_subtitle_changes_global_position_and_not_playhead(tmp_path):
    import subprocess
    from PySide6.QtCore import Qt, QPointF
    from PySide6.QtTest import QTest
    from lyricsync.audit_service import ffmpeg_executable
    app=QApplication.instance() or QApplication([]);bridge=Bridge(storage_dir=tmp_path/'data')
    engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('bridge',bridge)
    engine.load(QUrl.fromLocalFile(str(Path('desktop_qt/qml/Main.qml').resolve())))
    window=engine.rootObjects()[0];video=tmp_path/'preview-drag.mp4'
    subprocess.run([ffmpeg_executable(),'-loglevel','error','-f','lavfi','-i','testsrc2=size=320x180:rate=10:duration=3','-an','-c:v','libx264','-pix_fmt','yuv420p',str(video)],check=True)
    try:
        assert bridge.command('media.open',{'path':str(video)})
        wait(app,lambda:bridge.media['ready'] and bridge.media['displayedFrame'] == 1,timeout=15)
        p=bridge.document.project;p.translations=[Translation('a','拖动这句')]
        p.mappings=[Mapping('a',['a'],start=0,end=2)];p.settings.update(subtitle_position='custom',subtitle_height_percent=70,font_size=16)
        bridge.selected='a';bridge.stage='review';bridge._changed()
        wait(app,lambda:bool(bridge.media['subtitleRows']) and find(window.contentItem(),'previewSubtitle_0') is not None,timeout=10)
        label=find(window.contentItem(),'previewSubtitle_0');before=bridge.media['displayedFrame']
        pos=label.mapToScene(QPointF(label.width()/2,label.height()/2)).toPoint()
        QTest.mousePress(window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,pos)
        QTest.mouseMove(window,pos+__import__('PySide6').QtCore.QPoint(25,-12),40)
        QTest.mouseRelease(window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,pos+__import__('PySide6').QtCore.QPoint(25,-12))
        wait(app,lambda:abs(p.settings.get('subtitle_horizontal_percent',50)-50)>1)
        assert p.settings['subtitle_height_percent']<70
        assert bridge.media['displayedFrame']==before
        assert bridge.command('history.undo',{})
    finally:window.close();bridge.shutdown()
