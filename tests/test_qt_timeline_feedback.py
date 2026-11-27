"""Bidirectional wheel input and page-follow behavior on the real timeline."""
import time
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QAccessible, QWheelEvent, QPointingDevice, QInputDevice
from PySide6.QtTest import QTest
from test_qt_timeline_input import application, timeline_video, ui, find, point, click, wait

_devices = []  # Keep registered Qt input devices alive until all windows close.

def send_wheel(ui, delta, *, horizontal=False, pixels=False, alt=True, control=False):
    app, bridge, window, timeline = ui
    ruler=find(window,'timelineRulerMouse'); pos=point(ruler, ruler.width()*.5, 12)
    vector=QPoint(delta,0) if horizontal else QPoint(0,delta)
    device=QPointingDevice('Test touchpad', 991, QInputDevice.DeviceType.TouchPad,
        QPointingDevice.PointerType.Finger, QInputDevice.Capability.Position|QInputDevice.Capability.Scroll, 2, 0) if pixels else QPointingDevice.primaryPointingDevice()
    if pixels:_devices.append(device)
    event=QWheelEvent(QPointF(pos), QPointF(window.mapToGlobal(pos)),
        vector if pixels else QPoint(), QPoint() if pixels else vector,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier if control else Qt.KeyboardModifier.AltModifier if alt else Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollBegin if pixels else Qt.ScrollPhase.NoScrollPhase, False, Qt.MouseEventSource.MouseEventSynthesizedBySystem if pixels else Qt.MouseEventSource.MouseEventNotSynthesized, device)
    event.setTimestamp(int(time.monotonic()*1000))
    QTest.mouseMove(window,pos)
    app.sendEvent(window,event); QTest.qWait(30)


def test_alt_zoom_direction_changes_for_both_wheel_axes_and_trackpads(ui):
    app,bridge,window,timeline=ui
    for horizontal,pixels,step in [(False,False,120),(True,False,120),(False,True,40),(True,True,40)]:
        for first in (-step,step):
            for delta in (first,-first,first,-first):
                before=timeline.property('span')
                send_wheel(ui,delta,horizontal=horizontal,pixels=pixels)
                after=timeline.property('span')
                assert (after>before) if delta<0 else (after<before), (horizontal,pixels,delta,before,after)
    before=timeline.property('span');send_wheel(ui,0)
    assert timeline.property('span')==before


def test_plain_wheel_pans_and_control_wheel_scrolls_tracks(ui):
    app,bridge,window,timeline=ui
    timeline.setProperty('rowMode',0)
    # Sample pointer coordinates only once the row-mode resize has settled.
    # A delayed mouseMove can otherwise deliver the wheel to the old header.
    wait(app,lambda:not timeline.property('folding'));QTest.qWait(30)
    tracks=find(window,'timelineTracks')
    first,top,span=timeline.property('viewFirst'),tracks.property('contentY'),timeline.property('span')
    send_wheel(ui,-120,alt=False)
    assert timeline.property('viewFirst')>first
    assert tracks.property('contentY')==top
    assert timeline.property('span')==span
    first=timeline.property('viewFirst')
    send_wheel(ui,-120,alt=False,control=True)
    assert tracks.property('contentY')>top
    assert timeline.property('viewFirst')==first
    send_wheel(ui,120,alt=False,control=True)
    assert abs(tracks.property('contentY')-top)<=1  # Flickable rounds fractional viewport geometry.
    send_wheel(ui,120,alt=False)
    assert timeline.property('span')==span


def test_timeline_icons_keep_shortcuts_and_accessible_names(ui):
    app,bridge,window,timeline=ui
    for name,key,tool in [('timelineSelectTool','V','select'),('timelineHandTool','H','hand'),('timelineRazorTool','C','razor')]:
        button=find(window,name)
        assert button.property('text')==''
        assert key in button.property('tip')
        assert key in QAccessible.queryAccessibleInterface(button).text(QAccessible.Text.Name)
        click(window,button)
        assert timeline.property('tool')==tool


def test_follow_off_keeps_view_when_playhead_leaves_and_respects_manual_pan(ui):
    app,bridge,window,timeline=ui
    timeline.setProperty('followEnabled',False)
    timeline.setProperty('viewFirst',400);timeline.setProperty('viewLast',450)
    def frame(n,playing=True):
        bridge.media_controller._state.update(displayedFrame=n,playing=playing)
        bridge.mediaChanged.emit();QTest.qWait(40)
    frame(425);frame(449)
    assert (timeline.property('viewFirst'),timeline.property('viewLast'))==(400,450)
    frame(450)
    assert (timeline.property('viewFirst'),timeline.property('viewLast'))==(400,450)
    frame(500);frame(499)
    assert (timeline.property('viewFirst'),timeline.property('viewLast'))==(400,450)
    frame(100,False)
    assert timeline.property('viewFirst')==400
    from PySide6.QtQml import QQmlExpression,qmlContext
    frame(460)
    QQmlExpression(qmlContext(timeline),timeline,'panTo(700, true)').evaluate()
    frame(461);frame(462)
    assert timeline.property('viewFirst')==700
    frame(725);frame(750)
    assert timeline.property('viewFirst')==700


def test_five_row_playback_pages_at_bottom_but_manual_edit_keeps_view(ui):
    from PySide6.QtQml import QQmlExpression,qmlContext
    app,bridge,window,timeline=ui
    timeline.setProperty('followEnabled',False);timeline.setProperty('rowMode',0)
    tracks=find(window,'timelineTracks');QTest.qWait(60)
    row_height=timeline.property('rowHeight')
    def evaluate(code):return QQmlExpression(qmlContext(timeline),timeline,code).evaluate()
    evaluate('clampRows(0)')
    bridge.media_controller._state['playing']=True;bridge.mediaChanged.emit()
    for n in range(8):
        bridge.selected='r'+str(n);bridge._refresh(False);QTest.qWait(20)
    top=tracks.property('contentY')
    assert top>0 and top<=7*row_height
    assert top+tracks.height()>7*row_height+row_height
    timeline.setProperty('rowFollowSuspended',True);evaluate('clampRows(30 * rowHeight)')
    saved=tracks.property('contentY')
    bridge.selected='r8';bridge._refresh(False);QTest.qWait(30)
    assert tracks.property('contentY')==saved
    bridge.media_controller._state['playing']=False;bridge.mediaChanged.emit()
    bridge.selected='r9';bridge._refresh(False);QTest.qWait(30)
    assert tracks.property('contentY')==saved
