"""Actual QML pointer gestures; synthetic local media, no user project/API.

Windows offscreen input evidence is not native cross-monitor acceptance.
"""
import pytest,time
from PySide6.QtCore import QPoint, QPointF, QEvent, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from lyricsync.model import Mapping, Translation
from test_qt_timeline_input import application as base_application, timeline_video, ui, find, point, click, wait, state_frames


@pytest.fixture(scope='module')
def application(base_application):
    # Windows offscreen does not discover installed fonts on this host. Register
    # them only in this test process; never install or redistribute fonts.
    from pathlib import Path
    import os
    from PySide6.QtGui import QFontDatabase
    directory = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
    for name in ('msyh.ttc', 'arial.ttf'):
        path = directory / name
        if path.is_file(): QFontDatabase.addApplicationFont(str(path))
    return base_application


@pytest.fixture
def snap_ui(ui):
    app, bridge, window, timeline = ui
    p = bridge.document.project; idx = bridge.media_controller.index
    spans = [('before', 41, 60), ('middle', 80, 90), ('moving', 101, 120), ('after', 201, 220), ('distant', 301, 310)]
    p.translations = [Translation(key, '合成字幕') for key, _, _ in spans]
    p.mappings = [Mapping(key, [key], start=idx.interval(first, last)[0], end=idx.interval(first, last)[1]) for key, first, last in spans]
    bridge.selected = 'moving'; bridge._changed()
    bridge.media_controller.seek(151)
    wait(app, lambda: bridge.media['displayedFrame'] == 151)
    bridge.selected = 'moving'; bridge._refresh()
    QTest.qWait(120)
    timeline.setProperty('followEnabled', False); timeline.setProperty('manualView', True)
    timeline.setProperty('viewFirst', 1); timeline.setProperty('viewLast', 401)
    QTest.qWait(30)
    yield ui


def hold(ui, delta, edge=None, modifiers=Qt.KeyboardModifier.NoModifier):
    app, bridge, window, timeline = ui
    clip = find(window, 'timelineClip_moving')
    assert clip and clip.isVisible()
    pixels = clip.width() / (state_frames(bridge, 'moving')[1] - state_frames(bridge, 'moving')[0] + 1)
    item = find(window, 'cueEdge_' + edge) if edge else clip
    start = point(item)
    end = start + QPoint(round(delta * pixels), 0)
    QTest.mousePress(window, Qt.MouseButton.LeftButton, modifiers, start)
    if modifiers == Qt.KeyboardModifier.NoModifier:
        QTest.mouseMove(window, end, 40)
    else:
        # QTest.mouseMove has no modifier argument and emits NoModifier even
        # after keyPress. Deliver the actual modified pointer event explicitly.
        event=QMouseEvent(QEvent.Type.MouseMove,QPointF(end),QPointF(window.mapToGlobal(end)),
                          Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,modifiers)
        event.setTimestamp(int(time.monotonic()*1000));app.sendEvent(window,event)
    QTest.qWait(30)
    return end


def release(ui, end, modifiers=Qt.KeyboardModifier.NoModifier):
    QTest.mouseRelease(ui[2], Qt.MouseButton.LeftButton, modifiers, end)
    QTest.qWait(60)


@pytest.mark.parametrize('edge,delta,expected', [
    (None, 49, (151, 170)),       # start to current playhead
    (None, 79, (181, 200)),       # end boundary to another cue start
    ('first', -39, (61, 120)),    # nonadjacent cue end, outside visible three rows
    ('last', 181, (101, 300)),    # nonadjacent cue start
    ('last', 29, (101, 150)),     # end at playhead boundary, inclusive last is F150
])
def test_pointer_move_and_trim_snap_to_playhead_and_all_cue_boundaries(snap_ui, edge, delta, expected):
    app, bridge, window, timeline = snap_ui
    before = bridge.document.project.to_dict(); history = len(bridge.history.undo_stack)
    end = hold(snap_ui, delta, edge)
    assert timeline.property('snapped'), (edge, delta)
    assert bridge.document.project.to_dict() == before, 'pointer preview must not commit'
    release(snap_ui, end)
    assert state_frames(bridge, 'moving') == expected
    assert bridge.media['displayedFrame'] == 151
    assert len(bridge.history.undo_stack) == history + 1
    bridge.command('history.undo', {})
    assert state_frames(bridge, 'moving') == (101, 120)
    bridge.command('history.redo', {})
    assert state_frames(bridge, 'moving') == expected
    after = bridge.document.project.to_dict()
    assert [m for m in after['mappings'] if m['id'] != 'moving'] == [m for m in before['mappings'] if m['id'] != 'moving']


@pytest.mark.parametrize('edge,delta,expected', [(None, 49, (150, 169)), ('first', -39, (62, 120)), ('last', 29, (101, 149))])
def test_switch_off_keeps_free_drag_for_move_and_both_edges(snap_ui, edge, delta, expected):
    click(snap_ui[2], find(snap_ui[2], 'timelineSnapToggle'))
    assert not snap_ui[3].property('snapEnabled')
    end = hold(snap_ui, delta, edge)
    assert not snap_ui[3].property('snapped')
    assert not find(snap_ui[2], 'timelineSnapGuide').isVisible()
    release(snap_ui, end)
    assert state_frames(snap_ui[1], 'moving') == expected


def test_alt_drag_temporarily_bypasses_without_changing_switch(snap_ui):
    window = snap_ui[2]
    QTest.keyPress(window, Qt.Key.Key_Alt)
    try:
        end = hold(snap_ui, 49, modifiers=Qt.KeyboardModifier.AltModifier)
        assert not snap_ui[3].property('snapped')
        release(snap_ui, end, Qt.KeyboardModifier.AltModifier)
    finally:
        QTest.keyRelease(window, Qt.Key.Key_Alt)
    assert state_frames(snap_ui[1], 'moving') == (150, 169)
    assert snap_ui[3].property('snapEnabled')


@pytest.mark.parametrize('first,last,delta,expected,snapped', [(91, 171, 49, (150, 169), False), (1, 1001, 46, (151, 170), True)])
def test_eight_logical_pixel_threshold_tracks_zoom(snap_ui, first, last, delta, expected, snapped):
    timeline = snap_ui[3]
    timeline.setProperty('viewFirst', first); timeline.setProperty('viewLast', last)
    QTest.qWait(40)
    end = hold(snap_ui, delta)
    assert timeline.property('snapped') is snapped
    release(snap_ui, end)
    assert state_frames(snap_ui[1], 'moving') == expected


def test_self_edges_never_pull_a_small_move_back(snap_ui):
    end = hold(snap_ui, 1)
    assert not snap_ui[3].property('snapped')
    release(snap_ui, end)
    assert state_frames(snap_ui[1], 'moving') == (102, 121)


def test_selected_peer_is_not_a_target_and_neither_peer_nor_group_moves(snap_ui):
    bridge = snap_ui[1]
    bridge._multi_selected = {'moving', 'after'}; bridge._refresh(False)
    QTest.qWait(20)
    end = hold(snap_ui, 79)
    assert not snap_ui[3].property('snapped')
    release(snap_ui, end)
    assert state_frames(bridge, 'moving') == (180, 199)
    assert state_frames(bridge, 'after') == (201, 220)


@pytest.mark.parametrize('locked_source', [False, True])
def test_locked_target_is_usable_but_locked_source_never_moves(snap_ui, locked_source):
    bridge = snap_ui[1]
    assert bridge.command('row.lock',{'id':'moving' if locked_source else 'after','value':True})
    QTest.qWait(25)
    history = len(bridge.history.undo_stack)
    end = hold(snap_ui, 79)
    release(snap_ui, end)
    assert state_frames(bridge, 'moving') == ((101, 120) if locked_source else (181, 200))
    assert state_frames(bridge, 'after') == (201, 220)
    assert len(bridge.history.undo_stack) == history + (0 if locked_source else 1)


@pytest.mark.parametrize('cancel', ['escape', 'focus', 'collapse'])
def test_cancel_clears_guide_without_commit(snap_ui, cancel):
    app, bridge, window, timeline = snap_ui
    before = bridge.document.project.to_dict(); history = len(bridge.history.undo_stack)
    end = hold(snap_ui, 49)
    assert find(window, 'timelineSnapGuide').isVisible()
    if cancel == 'escape':QTest.keyClick(window, Qt.Key.Key_Escape)
    elif cancel == 'focus':timeline.setProperty('windowActive', False)
    else:timeline.setProperty('collapsed', True)
    release(snap_ui, end)
    assert not timeline.property('snapped') and not find(window, 'timelineSnapGuide').isVisible()
    assert bridge.document.project.to_dict() == before
    assert len(bridge.history.undo_stack) == history


def test_switch_off_during_gesture_reverts_to_unsnapped_preview(snap_ui):
    end = hold(snap_ui, 49)
    assert snap_ui[3].property('moveDelta') == 50
    snap_ui[3].setProperty('snapEnabled', False)
    assert snap_ui[3].property('moveDelta') == 49 and not snap_ui[3].property('snapped')
    release(snap_ui, end)
    assert state_frames(snap_ui[1], 'moving') == (150, 169)


def test_nonuniform_pts_are_committed_from_frame_index(snap_ui):
    from lyricsync.frames import FrameIndex
    bridge = snap_ui[1]; stamps=[]; seconds=0.
    for n in range(1200):
        stamps.append(seconds);seconds += (.029, .033, .041)[n % 3]
    index = FrameIndex(stamps, seconds)
    frames = {m.id:state_frames(bridge, m.id) for m in bridge.document.project.mappings}
    bridge.media_controller.index = index
    bridge.document.project.video['duration'] = seconds
    for row in bridge.document.project.mappings:row.start,row.end = index.interval(*frames[row.id])
    bridge._changed();QTest.qWait(40)
    end=hold(snap_ui,49);release(snap_ui,end)
    moving=next(m for m in bridge.document.project.mappings if m.id=='moving')
    assert (moving.start,moving.end)==index.interval(151,170)


def test_guides_and_toggle_remain_readable_in_narrow_layout(snap_ui, tmp_path):
    from pathlib import Path
    import os,json
    app,bridge,window,timeline=snap_ui
    out=Path(os.environ.get('LYRIA_SNAP_EVIDENCE',str(tmp_path/'snap-evidence')));out.mkdir(parents=True,exist_ok=True)
    end=hold(snap_ui,49)
    assert find(window,'timelineSnapGuide').isVisible()
    assert window.grabWindow().save(str(out/'snap-wide.png'))
    release(snap_ui,end);bridge.command('history.undo',{})
    bridge._state['selection']['current']['pendingText'] = True
    bridge.stateChanged.emit(); QTest.qWait(40)
    window.resize(760,600);QTest.qWait(150)
    names=['timelineSelectTool','timelineHandTool','timelineRazorTool','timelineSnapToggle','audioVisibilityToggle','timelineZoomOut','timelineZoomIn','timelineCreateMenuButton','manualTimingMode','allocateTextButton']
    rects=[]
    for name in names:
        item=find(window,name);assert item and item.isVisible()
        pos=item.mapToItem(timeline, QPointF(0,0))
        rects.append((name,pos.x(),pos.y(),item.width(),item.height()))
        assert pos.x()>=0 and pos.x()+item.width()<=timeline.width()+1
    for i,(name,x,y,w,h) in enumerate(rects):
        for other,xx,yy,ww,hh in rects[i+1:]:
            assert min(x+w,xx+ww)-max(x,xx)<=.5 or min(y+h,yy+hh)-max(y,yy)<=.5,(name,other)
    assert window.grabWindow().save(str(out/'snap-narrow.png'))
    (out/'narrow-geometry.json').write_text(json.dumps(rects,indent=2),encoding='utf8')
