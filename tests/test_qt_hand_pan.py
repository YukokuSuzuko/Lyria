"""Real Main.qml/QTest bidirectional H input, not direct JS handler calls.

Cloud offscreen/software evidence does not replace native Windows acceptance.
"""
import json
import hashlib
import os
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest

from test_qt_timeline_input import (
    ROOT, application, timeline_video, ui, find, point, click, drag, wheel, wait,
    state_frames,
)


def evidence():
    out = Path(os.environ.get('LYRIA_HAND_PAN_EVIDENCE',
                              str(ROOT.parent / 'validation/hand-pan-20261104')))
    out.mkdir(parents=True, exist_ok=True)
    return out


def reveal(window):
    workspace = find(window, 'makeScroll')
    assert workspace.property('contentItem') is None
    timeline = find(window, 'frameTimeline')
    QTest.qWait(50)
    assert 0 <= point(timeline, 0, 0).y()
    assert point(timeline, 0, timeline.height()).y() <= window.height()


def row_mode(window, timeline, mode):
    # Internal row caps still exercise virtualization; 0.13 users resize via the splitter.
    timeline.setProperty('rowMode', mode)
    QTest.qWait(250)
    assert timeline.property('rowMode') == mode
    reveal(window)


def hand(window, timeline):
    timeline.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_H)
    QTest.qWait(30)
    assert timeline.property('tool') == 'hand'
    assert find(window, 'timelineHandRowsMouse').property('enabled')


def view(window, timeline):
    return (timeline.property('viewFirst'), timeline.property('viewLast'),
            find(window, 'timelineTracks').property('contentY'))


def invariant(bridge, timeline):
    return {'document': bridge.document.snapshot(), 'selected': bridge.selected,
            'undo': len(bridge.history.undo_stack), 'redo': len(bridge.history.redo_stack),
            'span': timeline.property('span'),
            'media': {key: bridge.media[key] for key in
                      ('requestedFrame', 'displayedFrame', 'decoderSeeks', 'decodedFrames', 'playing')}}


def guard_transport(bridge, monkeypatch):
    calls = []
    monkeypatch.setattr(bridge.media_controller, 'seek', lambda *a, **kw: calls.append('seek'))
    monkeypatch.setattr(bridge.media_controller, 'pause', lambda *a, **kw: calls.append('pause'))
    return calls


@pytest.mark.parametrize('mode', [3, 5, 0])
def test_diagonal_hand_pan_exact_rows_and_empty_horizontal_window(ui, monkeypatch, mode):
    app, bridge, window, timeline = ui
    row_mode(window, timeline, mode)
    # dev4 follow/row caps preserve zoom. Establish the intended timing zoom
    # with a real control instead of relying on the old implicit fit. Leave
    # more than two screens of reachable media after the first diagonal drag.
    old_span = timeline.property('span')
    click(window, find(window, 'timelineZoomIn'))
    assert timeline.property('span') < old_span
    span = timeline.property('span')
    assert timeline.property('contentLast') - span - (timeline.property('viewFirst') + .55 * span) > 2 * span
    hand(window, timeline)
    tracks = find(window, 'timelineTracks')
    ruler = find(window, 'timelineRulerMouse')
    clip = find(window, 'timelineClip_r40')
    calls = guard_transport(bridge, monkeypatch)
    before = invariant(bridge, timeline)
    first, last, top = view(window, timeline)
    assert window.grabWindow().save(str(evidence() / f'hand-{mode}-before.png'))
    start = point(clip)
    delta = QPoint(-round(ruler.width() * .55), -45)
    samples = []
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    assert timeline.property('panning') and timeline.property('panRows')
    assert timeline.property('followEnabled')
    for n in range(1, 9):
        pos = start + delta * (n / 8)
        QTest.mouseMove(window, pos, 12)
        moved = pos - start
        assert tracks.property('contentY') == pytest.approx(top - moved.y(), abs=.01)
        expected = first - moved.x() / ruler.width() * before['span']
        assert timeline.property('viewFirst') == pytest.approx(expected, abs=.51)
        assert invariant(bridge, timeline) == before
        samples.append({'x': pos.x(), 'y': pos.y(), 'view': view(window, timeline)})
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + delta)
    QTest.qWait(60)
    assert not timeline.property('panning') and not timeline.property('panRows')
    assert view(window, timeline)[2] == top + 45 and not calls
    # Move much farther in time while retaining the user's rows. No implicit
    # revealRange may jump to the later lyric in either preview or release.
    start = point(tracks, tracks.width() * .7, tracks.height() / 2)
    before_empty = view(window, timeline)
    drag(window, start, start - QPoint(round(ruler.width() * 3), 15))
    assert view(window, timeline)[2] == before_empty[2] + 15
    assert timeline.property('viewFirst') > before_empty[0] + before['span'] * 2
    assert not any(bridge.timelineRows.get(i)['firstFrame'] < timeline.property('viewLast')
                   and bridge.timelineRows.get(i)['lastFrame'] >= timeline.property('viewFirst')
                   for i in range(int(tracks.property('contentY') // 30),
                                  min(100, int((tracks.property('contentY') + tracks.height()) // 30) + 1)))
    assert invariant(bridge, timeline) == before and not calls
    assert window.grabWindow().save(str(evidence() / f'hand-{mode}-after-empty-window.png'))
    (evidence() / f'hand-{mode}-state.json').write_text(json.dumps({
        'status': 'passed', 'mode': mode, 'source': 'Main.qml QTest mouse/key events with real local media worker',
        'qmlSha256': hashlib.sha256((ROOT / 'desktop_qt/qml/FrameTimeline.qml').read_bytes()).hexdigest(),
        'initialView': [first, last, top], 'samples': samples, 'finalView': view(window, timeline),
        'transportCalls': calls, 'documentSelectionUndoRedoMediaAndZoomUnchanged': True,
        'automaticRevealSuppressedDuringHandDrag': True, 'windowsAcceptance': False,
    }, ensure_ascii=False, indent=2), encoding='utf8')


@pytest.mark.parametrize('surface', ['label', 'empty_lane', 'row_gap'])
def test_hand_starts_on_labels_empty_lane_and_row_gaps(ui, monkeypatch, surface):
    app, bridge, window, timeline = ui
    hand(window, timeline)
    tracks = find(window, 'timelineTracks')
    top = tracks.property('contentY')
    calls = guard_transport(bridge, monkeypatch)
    before = invariant(bridge, timeline)
    positions = {'label': (25, 45), 'empty_lane': (tracks.width() - 40, 45),
                 'row_gap': (tracks.width() / 2, 29)}
    start = point(tracks, *positions[surface])
    drag(window, start, start + QPoint(35, 22))
    assert tracks.property('contentY') == top - 22
    assert invariant(bridge, timeline) == before and not calls


@pytest.mark.parametrize('mode', [3, 5, 0])
def test_ruler_stays_horizontal_even_when_pointer_crosses_into_rows(ui, mode):
    app, bridge, window, timeline = ui
    row_mode(window, timeline, mode)
    hand(window, timeline)
    ruler = find(window, 'timelineRulerMouse')
    initial = view(window, timeline)
    before = invariant(bridge, timeline)
    start = point(ruler, ruler.width() * .6, 12)
    drag(window, start, start + QPoint(-220, 120))
    assert timeline.property('viewFirst') > initial[0]
    assert view(window, timeline)[2] == initial[2]
    assert invariant(bridge, timeline) == before
    assert timeline.property('followEnabled')
    # A pure vertical drag of the ruler must not move either axis.
    unchanged = view(window, timeline)
    start = point(ruler, ruler.width() * .5, 12)
    drag(window, start, start + QPoint(0, 70))
    assert view(window, timeline) == unchanged


def test_both_axes_clamp_reverse_repeat_and_switch_row_modes(ui, monkeypatch):
    app, bridge, window, timeline = ui
    hand(window, timeline)
    tracks = find(window, 'timelineTracks')
    calls = guard_transport(bridge, monkeypatch)
    before = invariant(bridge, timeline)
    for mode in (3, 5, 0):
        row_mode(window, timeline, mode)
        before = invariant(bridge, timeline)
        start = point(tracks, tracks.width() / 2, tracks.height() / 2)
        drag(window, start, start + QPoint(100000, 100000))
        assert timeline.property('viewFirst') == timeline.property('contentFirst')
        assert tracks.property('contentY') == 0
        start = point(tracks, tracks.width() / 2, tracks.height() / 2)
        drag(window, start, start - QPoint(100000, 100000))
        assert timeline.property('viewLast') == timeline.property('contentLast')
        assert tracks.property('contentY') == 100 * 30 - tracks.height()
        assert invariant(bridge, timeline) == before and not calls
    # Reversing inside one gesture returns to the gesture's original position.
    start = point(tracks, tracks.width() / 2, tracks.height() / 2)
    initial = view(window, timeline)
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(window, start + QPoint(120, 60), 30)
    assert view(window, timeline)[2] == initial[2] - 60
    QTest.mouseMove(window, start, 30)
    assert view(window, timeline) == initial
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    for _ in range(4):
        start = point(tracks, tracks.width() / 2, tracks.height() / 2)
        original = view(window, timeline)
        drag(window, start, start + QPoint(80, 18))
        assert view(window, timeline)[2] == original[2] - 18
    assert invariant(bridge, timeline) == before and not calls
    # Changing the row-count setting while manually panned preserves top rows.
    top = tracks.property('contentY')
    row_mode(window, timeline, 3)
    assert tracks.property('contentY') == top
    assert window.grabWindow().save(str(evidence() / 'hand-bounds-repeated.png'))


@pytest.mark.parametrize('count', [0, 1, 2])
def test_empty_and_short_timeline_cannot_scroll_out_of_bounds(ui, count):
    app, bridge, window, timeline = ui
    bridge.document.project.mappings = bridge.document.project.mappings[:count]
    bridge.selected = 'r0' if count else ''
    bridge._changed()
    QTest.qWait(100)
    row_mode(window, timeline, 0)
    hand(window, timeline)
    tracks = find(window, 'timelineTracks')
    before = invariant(bridge, timeline)
    for delta in (QPoint(-300, -200), QPoint(300, 200)):
        start = point(tracks, tracks.width() / 2, tracks.height() - 8)
        drag(window, start, start + delta)
        assert tracks.property('contentY') == 0
        assert timeline.property('contentFirst') <= timeline.property('viewFirst')
        assert timeline.property('viewLast') <= timeline.property('contentLast')
        assert invariant(bridge, timeline) == before
    assert window.grabWindow().save(str(evidence() / f'hand-short-{count}-rows.png'))


@pytest.mark.parametrize('reason', ['escape', 'mouse_cancel', 'window_focus', 'item_focus', 'resize', 'height_resize', 'model_reset'])
def test_interrupted_grab_stops_and_next_gesture_has_fresh_origin(ui, reason):
    app, bridge, window, timeline = ui
    hand(window, timeline)
    tracks = find(window, 'timelineTracks')
    before = invariant(bridge, timeline)
    start = point(tracks, tracks.width() * .6, 45)
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(window, start - QPoint(90, 25), 30)
    assert timeline.property('panning')
    other = None
    if reason == 'escape':
        QTest.keyClick(window, Qt.Key.Key_Escape)
    elif reason == 'mouse_cancel':
        find(window, 'timelineHandRowsMouse').ungrabMouse()
    elif reason == 'window_focus':
        other = QQuickWindow()
        other.resize(200, 100)
        other.show()
        other.requestActivate()
        wait(app, lambda: other.isActive() and not window.isActive())
        assert not timeline.property('windowActive')
    elif reason == 'item_focus':
        find(window, 'followCurrentButton').forceActiveFocus()
    elif reason in ('resize', 'height_resize'):
        if reason == 'height_resize':
            window.resize(window.width(), 800)
        else:
            window.resize(1000, 850)
        QTest.qWait(150)
        reveal(window)
    else:
        # Match CueModel.replace's real reset completion contract: notify Qt,
        # then projectionReset after row/count notifications have completed.
        # Keep backing rows unchanged so this tests grab cancellation, not edits.
        before_reset = view(window, timeline)
        bridge.timelineRows.beginResetModel()
        bridge.timelineRows.endResetModel()
        bridge.timelineRows.projectionReset.emit(True)
        assert view(window, timeline) == before_reset
        for _ in range(25):
            QTest.qWait(2)
            assert view(window, timeline) == before_reset
    assert not timeline.property('panning')
    ended = view(window, timeline)
    QTest.mouseMove(window, start - QPoint(140, 50), 30)
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       start - QPoint(140, 50))
    QTest.qWait(40)
    assert view(window, timeline) == ended
    assert invariant(bridge, timeline) == before
    if other is not None:
        other.close()
        window.requestActivate()
        wait(app, window.isActive)
    hand(window, timeline)
    start = point(tracks, tracks.width() * .6, 45)
    drag(window, start, start + QPoint(50, 10))
    assert view(window, timeline)[2] == ended[2] - 10
    assert invariant(bridge, timeline) == before


def test_model_becoming_empty_ends_active_hand_grab_and_clamps_rows(ui):
    app, bridge, window, timeline = ui
    hand(window, timeline)
    tracks = find(window, 'timelineTracks')
    start = point(tracks, tracks.width() * .6, 45)
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(window, start - QPoint(40, 25), 30)
    assert timeline.property('panning')
    bridge.document.project.mappings = []
    bridge.selected = ''
    bridge._changed()
    QTest.qWait(60)
    assert not timeline.property('panning') and tracks.property('contentY') == 0
    unchanged = invariant(bridge, timeline)
    ended = view(window, timeline)
    QTest.mouseMove(window, start - QPoint(140, 50), 30)
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       start - QPoint(140, 50))
    QTest.qWait(30)
    assert view(window, timeline) == ended and invariant(bridge, timeline) == unchanged


def test_hand_during_playback_does_not_pause_seek_select_or_edit(ui, monkeypatch):
    app, bridge, window, timeline = ui
    bridge.command('media.play', {})
    wait(app, lambda: bridge.media['playing'])
    calls = guard_transport(bridge, monkeypatch)
    hand(window, timeline)
    tracks = find(window, 'timelineTracks')
    before = invariant(bridge, timeline)
    start = point(tracks, tracks.width() * .6, 45)
    drag(window, start, start - QPoint(60, 25))
    after = invariant(bridge, timeline)
    assert {key: value for key, value in after.items() if key != 'media'} == {
        key: value for key, value in before.items() if key != 'media'}
    assert bridge.media['playing'] and not calls


def test_wheel_scrollbars_follow_and_narrow_resize_remain_usable(ui, monkeypatch):
    app, bridge, window, timeline = ui
    hand(window, timeline)
    tracks = find(window, 'timelineTracks')
    calls = guard_transport(bridge, monkeypatch)
    before = invariant(bridge, timeline)
    start = point(tracks, tracks.width() / 2, 45)
    drag(window, start, start - QPoint(50, 40))
    first, last, top = view(window, timeline)
    wheel(app, window, tracks, -120, Qt.KeyboardModifier.ControlModifier)
    assert view(window, timeline) == (first, last, top + 60)
    wheel(app, window, tracks, -120)
    assert timeline.property('viewFirst') > first and timeline.property('span') == before['span']
    zoom_before = view(window, timeline)
    wheel(app, window, tracks, 120, Qt.KeyboardModifier.AltModifier)
    assert timeline.property('span') < before['span']
    assert tracks.property('contentY') == zoom_before[2]
    user_zoom = timeline.property('span')
    # Only the explicit Alt-wheel zoom may change this invariant. Following
    # later must retain it, not restore the pre-zoom span as dev3 used to do.
    before = {**before, 'span': user_zoom}
    # Vertical scrollbar is outside the fixed hand input surface.
    scroll = find(window, 'timelineVerticalScroll')
    pos, size = scroll.property('position'), scroll.property('size')
    start = point(scroll, scroll.width() / 2, (pos + size / 2) * scroll.height())
    old_y = tracks.property('contentY')
    drag(window, start, point(scroll, scroll.width() / 2, scroll.height() - 1))
    assert tracks.property('contentY') > old_y
    horizontal = find(window, 'timelineHorizontalScroll')
    pos, size = horizontal.property('position'), horizontal.property('size')
    start = point(horizontal, (pos + size / 2) * horizontal.width(), horizontal.height() / 2)
    drag(window, start, point(horizontal, 1, horizontal.height() / 2))
    assert timeline.property('viewFirst') == timeline.property('contentFirst')
    assert timeline.property('followEnabled')
    click(window, find(window, 'followCurrentButton'))
    assert not timeline.property('followEnabled')
    click(window, find(window, 'followCurrentButton'))
    assert timeline.property('followEnabled')
    assert timeline.property('span') == user_zoom
    assert timeline.property('viewFirst') <= 420 < timeline.property('viewLast')
    assert tracks.property('contentY') <= 40 * 30 < tracks.property('contentY') + tracks.height()
    window.resize(760, 600)
    QTest.qWait(250)
    reveal(window)
    hand(window, timeline)
    # At the minimum 760x600 window the fixed workspace retains one full
    # internally scrollable track, rather than overflowing an outer ScrollView.
    assert tracks.height() >= timeline.property('rowHeight')
    start = point(tracks, tracks.width() * .65, tracks.height()/2)
    old_y = tracks.property('contentY')
    drag(window, start, start - QPoint(45, 20))
    assert tracks.property('contentY') == old_y + 20
    assert timeline.property('span') == user_zoom
    assert invariant(bridge, timeline) == before and not calls
    assert window.grabWindow().save(str(evidence() / 'hand-narrow-760x600.png'))


def test_switch_back_to_v_move_edges_and_c_after_hand_pan(ui, monkeypatch):
    app, bridge, window, timeline = ui
    calls = guard_transport(bridge, monkeypatch)
    hand(window, timeline)
    tracks = find(window, 'timelineTracks')
    start = point(tracks, tracks.width() * .6, 45)
    drag(window, start, start - QPoint(45, 15))
    click(window, find(window, 'followCurrentButton'))
    timeline.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_V)
    QTest.qWait(30)
    assert not find(window, 'timelineHandRowsMouse').property('enabled'), {
        'tool': timeline.property('tool'), 'focus': window.activeFocusItem().objectName(),
        'textFocus': window.property('textFocus'), 'active': window.isActive(),
        'stage': window.property('stage'), 'workspace': window.property('workspace'),
    }
    clip = find(window, 'timelineClip_r41')
    click(window, clip)
    assert bridge.selected == 'r41'
    old = state_frames(bridge, 'r41')
    ruler = find(window, 'timelineRulerMouse')
    dx = round(2 * ruler.width() / timeline.property('span'))
    clip = find(window, 'timelineClip_r41')
    start = point(clip)
    drag(window, start, start + QPoint(dx, 0))
    assert state_frames(bridge, 'r41') == (old[0] + 2, old[1] + 2)
    assert bridge.command('history.undo', {})
    QTest.qWait(50)
    edge = find(window, 'cueEdge_last')
    edge_document = bridge.document.snapshot()
    edge_history = len(bridge.history.undo_stack)
    start = point(edge)
    # Edges preserve the pointer grab offset; move the handle by two frames.
    # The inclusive last frame is one frame before its drawn end boundary.
    end = start - QPoint(round(2 * ruler.width() / timeline.property('span')), 0)
    def edge_preview():
        assert timeline.property('editing'), {
            'edge': edge, 'start': start, 'end': end, 'tool': timeline.property('tool'),
            'moving': timeline.property('movingClip'), 'panning': timeline.property('panning'),
            'selected': bridge.selected, 'editId': timeline.property('editId'),
        }
        assert timeline.property('editId') == 'r41'
        assert bridge.document.snapshot() == edge_document
        assert len(bridge.history.undo_stack) == edge_history
        assert timeline.property('editFrame') == old[1] - 2, {
            'editFrame': timeline.property('editFrame'), 'edge': edge,
            'start': start, 'end': end, 'view': view(window, timeline),
        }
    drag(window, start, end, edge_preview)
    assert state_frames(bridge, 'r41') == (old[0], old[1] - 2)
    assert len(bridge.history.undo_stack) == edge_history + 1
    assert bridge.command('history.undo', {})
    QTest.qWait(50)
    timeline.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_C)
    QTest.qWait(30)
    clip = find(window, 'timelineClip_r41')
    before_text = bridge.state['selection']['current']['chinese']
    count = len(bridge.document.project.mappings)
    click(window, clip)
    assert len(bridge.document.project.mappings) == count + 1
    parts = bridge.document.project.mappings[41:43]
    assert parts[0].text_override == before_text and parts[1].text_override == ''
    assert not timeline.property('panning') and not calls
