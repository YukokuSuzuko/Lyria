"""Real offscreen mouse sequences; no native GPU acceptance is implied."""
import json
import os
from pathlib import Path
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt, QObject, QUrl
from PySide6.QtGui import QFontDatabase
from PySide6.QtQml import QQmlComponent, qmlEngine, qmlContext
from PySide6.QtTest import QTest
from tools.qt_event_loop_wait import delay, wait_until
from test_qt_preview_visibility import application, ui, install_cues, present, find


@pytest.fixture(scope='module', autouse=True)
def chinese_fonts(application):
    font=Path('C:/Windows/Fonts/msyh.ttc')
    if font.exists():
        assert QFontDatabase.addApplicationFont(str(font)) >= 0


def center(item):
    return item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint()


def move_steps(ui, start, end):
    count=max(abs(end.x()-start.x()),abs(end.y()-start.y()))
    for n in range(1,count+1):
        pos=start+(end-start)* (n/count)
        QTest.mouseMove(ui.window,pos,1)
        delay(2)


def settled(ui):
    wait_until(lambda:ui.bridge._layout_revision==ui.bridge.document.revision)
    delay(35)


@pytest.mark.parametrize('axis', ['x','y'])
@pytest.mark.parametrize('text', ['字幕拖动验证','日本語の歌詞\n双语字幕验证'])
def test_snap_release_regrab_small_steps_and_pause(ui,tmp_path,axis,text):
    project=install_cues(ui,resolution=(640,360),entries=[(0.,2.,text)])
    project.settings['font']='Microsoft YaHei UI';ui.bridge._changed();settled(ui)
    present(ui,1)
    label=find(ui,'previewSubtitle_0');start=center(label)
    video=find(ui,'videoPreview');rect=video.property('contentRect')
    target=video.mapToScene(rect.center()).toPoint()
    before=ui.bridge.document.snapshot();font=project.settings['font_size']
    QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,start)
    move_steps(ui,start,target);delay(100)
    assert ui.panel.property('subtitleSnapX') and ui.panel.property('subtitleSnapY')
    assert ui.bridge.document.snapshot()==before
    snapped=(label.x(),label.y())
    QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,target)
    samples=[]
    for _ in range(30):
        row=find(ui,'previewSubtitle_0');samples.append((row.x(),row.y()));delay(10)
    assert all(abs(x-snapped[0])<1.5 and abs(y-snapped[1])<1.5 for x,y in samples)
    settled(ui)
    # A second gesture starts from the committed snapped position, not the old anchor.
    row=find(ui,'previewSubtitle_0');start=center(row)
    QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,start)
    delta=QPoint(40,0) if axis=='x' else QPoint(0,40)
    positions=[]
    for n in range(1,41):
        QTest.mouseMove(ui.window,start+delta*(n/40),2);delay(5)
        positions.append(ui.panel.property('subtitleDragX' if axis=='x' else 'subtitleDragY'))
        if n in (8,20,40):delay(100)
    assert positions[-1]>38,positions
    assert not ui.panel.property('subtitleSnapX' if axis=='x' else 'subtitleSnapY')
    last=(row.x(),row.y());delay(150)
    assert (row.x(),row.y())==last
    QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,start+delta)
    settled(ui);row=find(ui,'previewSubtitle_0')
    assert abs(row.x()-last[0])<1.5 and abs(row.y()-last[1])<1.5
    assert project.settings['font_size']==font
    out=Path(os.environ.get('LYRIA_013_PREVIEW_EVIDENCE') or os.environ.get('LYRIA_VISUAL_EVIDENCE') or tmp_path);out.mkdir(parents=True,exist_ok=True)
    suffix=axis+('-bilingual' if '\n' in text else '-single')
    ui.window.grabWindow().save(str(out/('drag-'+suffix+'.png')))
    (out/('drag-'+suffix+'.json')).write_text(json.dumps(dict(snapSamples=samples,smallSteps=positions)),encoding='utf-8')


@pytest.mark.parametrize('cancel', ['resize','inactive','roi','cue'])
def test_drag_canceled_without_commit(ui,cancel):
    install_cues(ui,resolution=(640,360),entries=[(0.,.5,'第一句'),(.5,2.,'第二句')])
    present(ui,1);before=ui.bridge.document.snapshot()
    start=center(find(ui,'previewSubtitle_0'))
    QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,start)
    move_steps(ui,start,start+QPoint(25,-25))
    assert ui.panel.property('subtitleDragging')
    if cancel=='resize':ui.window.resize(800,600)
    elif cancel=='inactive':ui.panel.setProperty('windowActive',False)
    elif cancel=='roi':ui.panel.setProperty('roiSelecting',True)
    else:present(ui,8)
    delay(80)
    assert not ui.panel.property('subtitleDragging')
    assert ui.panel.property('subtitleDragX')==0 and ui.panel.property('subtitleDragY')==0
    QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,start+QPoint(25,-25));delay(100)
    assert ui.bridge.document.snapshot()==before


def test_roi_hint_manual_selection_cancel_and_completion(ui,tmp_path):
    from test_qt_preview_visibility import enter_roi_import
    install_cues(ui,resolution=(640,360));present(ui,1)
    enter_roi_import(ui)
    hint=find(ui,'roiSetupHint');button=find(ui,'selectRoiButton')
    assert hint.isVisible() and hint.property('text')=='尚未设置字幕选区。可自动检测硬字幕位置，也可直接在画面上手动框选。'
    assert button.property('tip')=='设置选区' and button.property('glyph')=='roi-settings'
    assert button.property('text') == '' and not button.property('checked')
    # Hint is outside the image, never covering a lyric or interfering with drag.
    stage=find(ui,'videoStage')
    assert hint.mapToScene(QPointF(0,hint.height())).y() <= stage.mapToScene(QPointF()).y()
    out=Path(os.environ.get('LYRIA_013_PREVIEW_EVIDENCE') or os.environ.get('LYRIA_VISUAL_EVIDENCE') or tmp_path);out.mkdir(parents=True,exist_ok=True)
    ui.window.grabWindow().save(str(out/'roi-needs-selection.png'))
    QTest.mouseClick(ui.window,Qt.LeftButton,Qt.NoModifier,center(button));delay(40)
    dialog=ui.window.findChild(QObject,'regionSetupDialog')
    assert dialog.property('visible')
    QTest.keyClick(ui.window,Qt.Key_Escape);delay(30)
    assert not dialog.property('visible') and hint.isVisible()
    QTest.mouseClick(ui.window,Qt.LeftButton,Qt.NoModifier,center(button));delay(30)
    manual=find(ui,'manualRoiChoice')
    QTest.mouseClick(ui.window,Qt.LeftButton,Qt.NoModifier,center(manual));delay(30)
    assert ui.panel.property('roiSelecting') and '拖动鼠标框住' in hint.property('text')
    a=center(stage)+QPoint(-90,40);b=a+QPoint(180,70)
    QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,a)
    move_steps(ui,a,b)
    QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,b);delay(60)
    assert ui.bridge.state['roi'] and not hint.isVisible()
    assert not ui.panel.property('roiSelecting') and not button.property('checked')
    out=Path(os.environ.get('LYRIA_013_PREVIEW_EVIDENCE') or os.environ.get('LYRIA_VISUAL_EVIDENCE') or tmp_path);out.mkdir(parents=True,exist_ok=True)
    ui.window.grabWindow().save(str(out/'roi-completed.png'))


@pytest.fixture
def underlying_pointer_probe(ui):
    # A lower sibling proves rejected presses really propagate, rather than
    # merely checking that the subtitle handler did not update the document.
    stage=find(ui,'videoStage')
    component=QQmlComponent(qmlEngine(stage))
    component.setData(b"""import QtQuick
MouseArea {
    anchors.fill: parent; z: -1
    property int presses: 0
    onPressed: presses += 1
}""",QUrl())
    probe=component.create(qmlContext(stage))
    assert probe is not None, component.errors()
    probe.setParentItem(stage)
    yield probe
    probe.deleteLater();delay(10)


def test_blank_press_propagates_below_preview_receiver(ui,underlying_pointer_probe):
    install_cues(ui,resolution=(640,360));present(ui,1)
    stage=find(ui,'videoStage');blank=stage.mapToScene(QPointF(30,30)).toPoint()
    before=ui.bridge.document.snapshot()
    QTest.mouseClick(ui.window,Qt.LeftButton,Qt.NoModifier,blank);delay(20)
    assert underlying_pointer_probe.property('presses')==1
    assert not ui.panel.property('subtitleDragging')
    assert ui.bridge.document.snapshot()==before


def test_disable_preview_cancels_drag_and_hidden_row_does_not_capture(ui,underlying_pointer_probe):
    install_cues(ui,resolution=(640,360));present(ui,1)
    start=center(find(ui,'previewSubtitle_0'))
    QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,start)
    move_steps(ui,start,start+QPoint(25,-25))
    assert ui.panel.property('subtitleDragging')
    assert ui.bridge.command('settings.set',{'key':'subtitlePreview','value':False})
    delay(30)
    assert find(ui,'previewSubtitle_0') is None
    assert not ui.panel.property('subtitleDragging')
    assert ui.panel.property('subtitleDragX')==0 and ui.panel.property('subtitleDragY')==0
    before=ui.bridge.document.snapshot()
    QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,start+QPoint(25,-25))
    QTest.mouseClick(ui.window,Qt.LeftButton,Qt.NoModifier,start);delay(30)
    assert underlying_pointer_probe.property('presses')==1
    assert ui.bridge.document.snapshot()==before
    # Re-enabling is usable; cancellation must not leave a permanent pointer lock.
    assert ui.bridge.command('settings.set',{'key':'subtitlePreview','value':True})
    settled(ui);start=center(find(ui,'previewSubtitle_0'))
    QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,start)
    assert ui.panel.property('subtitleDragging')
    QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,start)


def test_roi_gesture_over_subtitle_is_not_stolen(ui,underlying_pointer_probe):
    from test_qt_preview_visibility import enter_roi_import
    install_cues(ui,resolution=(640,360));present(ui,1)
    enter_roi_import(ui)
    start=center(find(ui,'previewSubtitle_0'))
    settings=dict(ui.bridge.document.project.settings)
    ui.panel.setProperty('roiSelecting',True);delay(20)
    assert not find(ui,'subtitleDragArea').property('enabled')
    QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,start)
    move_steps(ui,start,start+QPoint(-100,-45))
    assert not ui.panel.property('subtitleDragging')
    QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,start+QPoint(-100,-45));delay(30)
    assert ui.bridge.state['roi'] and not ui.panel.property('roiSelecting')
    assert underlying_pointer_probe.property('presses')==0
    after=dict(ui.bridge.document.project.settings);after.pop('roi',None);settings.pop('roi',None)
    assert after==settings
