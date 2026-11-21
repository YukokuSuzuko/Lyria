"""Actual Main.qml/QTest timeline input. Linux software rendering, not Windows acceptance.

Synthetic local video is decoded by the real player. Selection/move tests guard
transport calls; pointer previews must not touch the document before release.
"""
import os
from pathlib import Path
import time
import threading
from contextlib import contextmanager
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
import av
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt, QUrl, QEvent
from PySide6.QtGui import QWheelEvent
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from desktop_qt.controller import Bridge
from lyricsync.model import Mapping, Translation

ROOT = Path(__file__).resolve().parents[1]


def wait(app, condition, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return
        time.sleep(.005)
    assert condition()


def walk(item):
    yield item
    for child in item.childItems():
        yield from walk(child)


def find(window, name):
    return next((item for item in walk(window.contentItem()) if item.objectName() == name), None)


def point(item, x=None, y=None):
    return item.mapToScene(QPointF(item.width() / 2 if x is None else x,
                                  item.height() / 2 if y is None else y)).toPoint()


def click(window, item, x=None, y=None):
    assert item and item.isVisible() and item.isEnabled()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(item, x, y))
    QTest.qWait(40)


def drag(window, start, end, after_move=None):
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    for n in range(1, 9):
        QTest.mouseMove(window, start + (end - start) * (n / 8), 10)
    if after_move:
        after_move()
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    QTest.qWait(70)


def wheel(app, window, item, dy, modifiers=Qt.KeyboardModifier.NoModifier, y=None):
    pos = QPointF(point(item, y=y))
    event = QWheelEvent(pos, QPointF(window.mapToGlobal(pos.toPoint())), QPoint(), QPoint(0, dy),
                        Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False)
    app.sendEvent(window, event)
    QTest.qWait(50)


@pytest.fixture(scope='module')
def application():
    QQuickStyle.setStyle('Basic')
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope='module')
def timeline_video(tmp_path_factory):
    path = tmp_path_factory.mktemp('timeline-media') / 'timeline-local-synthetic.mp4'
    with av.open(str(path), 'w') as container:
        stream = container.add_stream('libx264', rate=30)
        stream.width = 64; stream.height = 36; stream.pix_fmt = 'yuv420p'
        stream.options = {'preset': 'ultrafast', 'threads': '1'}
        for _ in range(1200):
            frame = av.VideoFrame(64, 36, 'yuv420p')
            for plane in frame.planes:
                plane.update(bytes([100]) * plane.buffer_size)
            for packet in stream.encode(frame): container.mux(packet)
        for packet in stream.encode(): container.mux(packet)
    return path


@contextmanager
def timeline_session(application, storage_dir):
    """Own Qt/decoder lifetime from construction through setup assertions."""
    bridge = None
    engine = None
    window = None
    owned_job_threads = []
    try:
        bridge = Bridge(storage_dir=storage_dir)
        run_job = bridge.jobs.run
        def tracked_run(kind, function, **kwargs):
            def owned_work(cancel, progress):
                owned_job_threads.append(threading.current_thread())
                return function(cancel, progress)
            return run_job(kind, owned_work, **kwargs)
        bridge.jobs.run = tracked_run
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty('bridge', bridge)
        engine.load(QUrl.fromLocalFile(str(ROOT / 'desktop_qt/qml/Main.qml')))
        assert engine.rootObjects(), 'Main.qml did not create a root window'
        window = engine.rootObjects()[0]
        yield bridge, window
    finally:
        # Stop and join the decoder before destroying its Qt signal recipients.
        # This also runs when QML loading or any pre-yield fixture assertion fails.
        if bridge is not None:
            bridge.shutdown()
            bridge.wait_for_shutdown(application)
            worker = bridge.media_controller.worker
            wait(application, lambda: not worker.thread.is_alive(), timeout=10)
            worker.thread.join()
            wait(application, lambda: not bridge.jobs.active, timeout=10)
            # finished.emit may be handled before the Python worker returns.
            # Join only jobs launched by this fixture's Bridge, never unrelated threads.
            for thread in owned_job_threads:
                thread.join(10)
                assert not thread.is_alive(), 'Fixture job did not exit: ' + thread.name
        if window is not None:
            window.setProperty('allowClose', True)
            window.close()
        if engine is not None:
            destroyed = []
            engine.destroyed.connect(lambda: destroyed.append(True))
            engine.deleteLater()
            application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            wait(application, lambda: bool(destroyed))
        application.processEvents()


@pytest.fixture
def ui(application, timeline_video, tmp_path):
    with timeline_session(application, tmp_path / 'recovery') as (bridge, window):
        bridge.command('media.open', {'path': str(timeline_video)})
        wait(application, lambda: bridge.media['ready'] and bridge.media['displayedFrame'] == 1)
        p = bridge.document.project; idx = bridge.media_controller.index
        p.translations = [Translation('t' + str(n), '真实中文第' + str(n + 1) + '句') for n in range(100)]
        p.mappings = [Mapping('r' + str(n), ['t' + str(n)], start=idx.interval(n * 10 + 20, n * 10 + 27)[0],
                              end=idx.interval(n * 10 + 20, n * 10 + 27)[1]) for n in range(100)]
        bridge.selected = 'r40'; bridge.stage = 'review'; bridge._changed()
        timeline = find(window, 'frameTimeline')
        wait_for_timeline_viewport(window, timeline)
        timeline.forceActiveFocus()
        yield application, bridge, window, timeline


def wait_for_timeline_viewport(window, timeline, identity='r40', neighbor='r41', timeout=5):
    """Wait for painted, settled hit targets, not merely instantiated delegates.

    Initial workspace/layout/selection animations can begin after media readiness.
    A cached item's isVisible() does not mean its ancestors paint its click point.
    No scrolling/selection is forced here: a product reveal failure must time out.
    """
    from tools.qt_event_loop_wait import delay
    deadline = time.monotonic() + timeout
    stable_since = None
    previous = None
    diagnostics = {}
    while time.monotonic() < deadline:
        tracks = find(window, 'timelineTracks')
        workspace = find(window, 'makeScroll')
        review = find(window, 'reviewList')
        panel = find(window, 'reviewPanel')
        selected = find(window, 'cueRow_' + identity)
        rows = [find(window, 'timelineRow_' + key) for key in (identity, neighbor)]
        clips = [find(window, 'timelineClip_' + key) for key in (identity, neighbor)]
        ready = all(item is not None for item in [tracks, workspace, review, panel, selected, *rows, *clips])
        geometry = []
        if ready:
            for item, viewport in [(selected, review), *[(row, tracks) for row in rows]]:
                top = item.mapToItem(viewport, QPointF()).y()
                scene = item.mapToScene(QPointF())
                ready = ready and item.isVisible() and top >= -.5 and top + item.height() <= viewport.height() + .5
                ready = ready and scene.y() >= 0 and scene.y() + item.height() <= window.height()
                painted_top = item.mapToItem(workspace, QPointF()).y()
                ready = ready and painted_top >= -.5 and painted_top + item.height() <= workspace.height() + .5
                geometry.extend((top, item.height(), viewport.height(), scene.y()))
            # Clip centers are the actual mouse targets, including horizontal fit.
            for clip in clips:
                local = clip.mapToItem(tracks, QPointF(clip.width()/2, clip.height()/2))
                ready = ready and 0 <= local.x() <= tracks.width() and 0 <= local.y() <= tracks.height()
            geometry.extend((tracks.property('contentY'), review.property('contentY'),
                             timeline.property('viewFirst'), timeline.property('viewLast')))
            ready = ready and not panel.property('autoScrollRunning') and not selected.property('heightAnimating') and not timeline.property('folding')
        snapshot = tuple(round(value, 1) for value in geometry)
        diagnostics = {'ready': ready, 'geometry': snapshot, 'selected': identity, 'neighbor': neighbor}
        now = time.monotonic()
        if ready and snapshot == previous:
            if stable_since is None: stable_since = now
            if now - stable_since >= .12:
                return
        else:
            stable_since = now if ready else None
        previous = snapshot
        delay(15)
    raise AssertionError('Timeline hit targets did not settle within timeout: ' + repr(diagnostics))


def state_frames(bridge, identity):
    row = next(row for row in bridge.state['rows'] if row['id'] == identity)
    return row['firstFrame'], row['lastFrame']


def test_clip_select_move_preview_one_undo_and_no_transport(ui, monkeypatch):
    app, bridge, window, timeline = ui
    transport = []
    monkeypatch.setattr(bridge.media_controller, 'seek', lambda *a: transport.append(('seek', a)))
    monkeypatch.setattr(bridge.media_controller, 'pause', lambda *a: transport.append(('pause', a)))
    old_frame = bridge.media['requestedFrame']; before = bridge.document.snapshot()
    out = Path(os.environ.get('LYRIA_TIMELINE_EVIDENCE', str(ROOT.parent / 'validation/timeline-dev')))
    out.mkdir(parents=True, exist_ok=True)
    assert window.grabWindow().save(str(out / 'timeline-three-1400x940.png'))
    clip = find(window, 'timelineClip_r41'); assert clip
    click(window, clip)
    assert bridge.selected == 'r41' and bridge.document.snapshot() == before
    assert bridge.media['requestedFrame'] == old_frame and not transport
    old = state_frames(bridge, 'r41'); history = len(bridge.history.undo_stack)
    clip = find(window, 'timelineClip_r41'); start = point(clip)
    ruler = find(window, 'timelineRulerMouse')
    dx = round(4 * ruler.width() / timeline.property('span'))
    def preview():
        assert timeline.property('movingClip') and timeline.property('moveDelta') == 4
        assert state_frames(bridge, 'r41') == old
        assert len(bridge.history.undo_stack) == history
    drag(window, start, start + QPoint(dx, 0), preview)
    assert state_frames(bridge, 'r41') == (old[0] + 4, old[1] + 4)
    assert len(bridge.history.undo_stack) == history + 1
    assert bridge.media['requestedFrame'] == old_frame and not transport
    manual = (timeline.property('viewFirst'), timeline.property('viewLast'))
    assert bridge.command('history.undo', {})
    QTest.qWait(50)
    assert state_frames(bridge, 'r41') == old and bridge.selected == 'r41'
    assert (timeline.property('viewFirst'), timeline.property('viewLast')) == manual
    assert not transport


def test_hand_scrollbar_wheel_bounds_and_follow(ui, monkeypatch):
    app, bridge, window, timeline = ui
    transport = []
    monkeypatch.setattr(bridge.media_controller, 'seek', lambda *a: transport.append('seek'))
    monkeypatch.setattr(bridge.media_controller, 'pause', lambda *a: transport.append('pause'))
    old_frame = bridge.media['requestedFrame']; before = bridge.document.snapshot()
    span = timeline.property('span'); first = timeline.property('viewFirst')
    QTest.keyClick(window, Qt.Key.Key_H); QTest.qWait(50)
    assert timeline.property('tool') == 'hand'
    ruler = find(window, 'timelineRulerMouse')
    start = point(ruler, ruler.width() * .7, 40)
    drag(window, start, start - QPoint(150, 0))
    assert timeline.property('viewFirst') > first
    assert timeline.property('span') == span and timeline.property('followEnabled')
    assert bridge.document.snapshot() == before and not transport
    scroll = find(window, 'timelineHorizontalScroll')
    pos = scroll.property('position'); size = scroll.property('size')
    thumb = point(scroll, (pos + size / 2) * scroll.width(), scroll.height() / 2)
    drag(window, thumb, point(scroll, scroll.width() - 1, scroll.height() / 2))
    assert timeline.property('viewLast') == timeline.property('contentLast')
    assert timeline.property('span') == span and bridge.media['requestedFrame'] == old_frame
    row_top = find(window, 'timelineTracks').property('contentY')
    wheel(app, window, ruler, -120 * 1000, y=12)
    assert timeline.property('viewLast') == timeline.property('contentLast')
    wheel(app, window, ruler, 120 * 1000, y=12)
    assert timeline.property('viewFirst') == timeline.property('contentFirst')
    assert timeline.property('span') == span and not transport
    # Horizontal wheel must not change the user's vertical track position,
    # even when those clips are outside the new horizontal viewport.
    assert abs(find(window, 'timelineTracks').property('contentY') - row_top) < 1
    assert any(i.objectName().startswith('timelineRow_') and i.isVisible() for i in walk(window.contentItem()))
    assert timeline.property('followEnabled')
    click(window, find(window, 'followCurrentButton'))
    assert not timeline.property('followEnabled')
    click(window, find(window, 'followCurrentButton'))
    assert timeline.property('followEnabled')
    assert timeline.property('viewFirst') <= 420 < timeline.property('viewLast')


def test_three_five_all_virtualization_and_vertical_input(ui):
    app, bridge, window, timeline = ui
    tracks = find(window, 'timelineTracks')
    assert timeline.property('rowMode') == 0
    assert find(window, 'timelineRowMode') is None
    assert tracks.height() >= 90
    selected_row = find(window, 'timelineRow_r40')
    assert 0 <= selected_row.mapToItem(tracks, QPointF(0, 15)).y() <= tracks.height()
    assert find(window, 'timelineText_r40').property('text') == '真实中文第41句'
    for cap in (3, 5, 0):
        timeline.setProperty('rowMode', cap); QTest.qWait(300)
        if cap: assert tracks.height() <= cap * timeline.property('rowHeight')
    instantiated = [i for i in walk(window.contentItem()) if i.objectName().startswith('timelineRow_')]
    assert len(instantiated) < 20, len(instantiated)
    assert len(instantiated) < bridge.timelineRows.count
    outer = find(window, 'makeScroll')
    assert outer.property('contentHeight') is None
    QTest.qWait(60)
    old_y = tracks.property('contentY')
    wheel(app, window, tracks, -120, Qt.KeyboardModifier.ControlModifier)
    assert tracks.property('contentY') > old_y
    assert timeline.property('followEnabled')
    wheel(app, window, tracks, -120 * 1000, Qt.KeyboardModifier.ControlModifier)
    assert 0 <= tracks.property('contentY') <= tracks.property('contentHeight') - tracks.height() + 1
    out = Path(os.environ.get('LYRIA_TIMELINE_EVIDENCE', str(ROOT.parent / 'validation/timeline-dev')))
    out.mkdir(parents=True, exist_ok=True)
    outer = find(window, 'makeScroll')
    assert outer.property('contentHeight') is None
    QTest.qWait(60)
    assert window.grabWindow().save(str(out / 'timeline-all-scrolled-edge.png'))
    click(window, find(window, 'followCurrentButton'))
    for width, height in [(1400, 940), (760, 900)]:
        window.resize(width, height); QTest.qWait(250)
        # v0.23 keeps the whole workspace fixed; timeline must already be reachable.
        outer = find(window, 'makeScroll')
        assert outer.property('contentHeight') is None
        QTest.qWait(100)
        for name in ('timelineSelectTool', 'timelineHandTool', 'audioVisibilityToggle', 'manualTimingMode', 'followCurrentButton'):
            item = find(window, name); location = point(item)
            assert 0 <= location.x() <= window.width(), (name, location)
        out = Path(os.environ.get('LYRIA_TIMELINE_EVIDENCE', str(ROOT.parent / 'validation/timeline-dev')))
        out.mkdir(parents=True, exist_ok=True)
        assert window.grabWindow().save(str(out / f'timeline-{width}x{height}.png'))


def test_alt_zoom_and_editor_shortcut_guard(ui):
    app, bridge, window, timeline = ui
    old_frame = bridge.media['requestedFrame']; ruler = find(window, 'timelineRulerMouse')
    span = timeline.property('span')
    wheel(app, window, ruler, 120, Qt.KeyboardModifier.AltModifier)
    assert timeline.property('span') < span and bridge.media['requestedFrame'] == old_frame
    editor = find(window, 'chineseEditor_r40'); QTest.keyClick(window, Qt.Key.Key_F2); QTest.qWait(50)
    assert editor.hasActiveFocus() and not editor.property('readOnly')
    QTest.keyClick(window, Qt.Key.Key_H); QTest.qWait(50)
    assert timeline.property('tool') == 'select'
    assert 'h' in bridge.state['selection']['current']['chinese']
    timeline.forceActiveFocus(); QTest.keyClick(window, Qt.Key.Key_H); QTest.qWait(50)
    assert timeline.property('tool') == 'hand'
    QTest.keyClick(window, Qt.Key.Key_V); QTest.qWait(50)
    assert timeline.property('tool') == 'select'
    assert find(window, 'timelineRazorTool').isEnabled()
    QTest.keyClick(window, Qt.Key.Key_C); QTest.qWait(50)
    assert timeline.property('tool') == 'razor'


def test_cancel_drag_filter_hidden_selection_and_manual_view(ui, monkeypatch):
    app, bridge, window, timeline = ui
    transport = []
    monkeypatch.setattr(bridge.media_controller, 'seek', lambda *a: transport.append('seek'))
    monkeypatch.setattr(bridge.media_controller, 'pause', lambda *a: transport.append('pause'))
    # Confirmed rows remain on the unfiltered timeline and are selectable there.
    bridge.document.project.mappings[41].confirmed = True
    bridge._unconfirmed_only = True; bridge._changed(); QTest.qWait(100)
    assert all(row['id'] != 'r41' for row in bridge.model.items)
    clip = find(window, 'timelineClip_r41'); before_view = (timeline.property('viewFirst'), timeline.property('viewLast'))
    click(window, clip)
    assert bridge.selected == 'r41' and bridge.uiState['selectedVisibleIndex'] == -1
    assert (timeline.property('viewFirst'), timeline.property('viewLast')) == before_view
    before = bridge.document.snapshot(); history = len(bridge.history.undo_stack)
    clip = find(window, 'timelineClip_r41'); start = point(clip)
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(window, start + QPoint(55, 0), 40)
    assert timeline.property('movingClip')
    # Actual Escape event cancels the preview without committing a move.
    QTest.keyClick(window, Qt.Key.Key_Escape)
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(55, 0))
    assert not timeline.property('movingClip')
    assert bridge.document.snapshot() == before and len(bridge.history.undo_stack) == history
    # The window-active contract is the same route Main.qml binds to OS focus.
    clip = find(window, 'timelineClip_r41'); start = point(clip)
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(window, start + QPoint(55, 0), 40)
    timeline.setProperty('windowActive', False); QTest.qWait(40)
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(55, 0))
    assert not timeline.property('movingClip')
    assert bridge.document.snapshot() == before and not transport
    timeline.setProperty('windowActive', True)
    click(window, find(window, 'timelineZoomIn'))
    manual_span = timeline.property('span')
    assert timeline.property('manualView')
    bridge.command('timeline.select', {'id': 'r80'}); QTest.qWait(80)
    assert timeline.property('span') == manual_span
    # A hand pan disables subsequent horizontal follow entirely.
    timeline.forceActiveFocus(); QTest.keyClick(window, Qt.Key.Key_H)
    ruler = find(window, 'timelineRulerMouse'); start = point(ruler, ruler.width() / 2, 12)
    drag(window, start, start - QPoint(100, 0))
    manual_view = (timeline.property('viewFirst'), timeline.property('viewLast'))
    bridge.command('timeline.select', {'id': 'r20'}); QTest.qWait(80)
    assert (timeline.property('viewFirst'), timeline.property('viewLast')) == manual_view
    assert not transport


def test_wide_gap_current_priority_offscreen_hints_and_playing(ui, monkeypatch):
    app, bridge, window, timeline = ui
    p = bridge.document.project; idx = bridge.media_controller.index
    p.mappings = [p.mappings[0], p.mappings[40], p.mappings[99]]
    p.mappings[0].start, p.mappings[0].end = idx.interval(20, 27)
    p.mappings[1].start, p.mappings[1].end = idx.interval(600, 607)
    p.mappings[2].start, p.mappings[2].end = idx.interval(1000, 1007)
    bridge.selected = 'r40'; bridge._changed(); QTest.qWait(80)
    # dev4: follow recentres the current cue without changing the user's zoom.
    # Keep remote-neighbour hints and the playing edit assertions below intact.
    before_span = timeline.property('span')
    if timeline.property('followEnabled'): click(window, find(window, 'followCurrentButton'))
    click(window, find(window, 'followCurrentButton'))
    assert timeline.property('viewFirst') <= 600 < 608 <= timeline.property('viewLast')
    assert timeline.property('span') == before_span
    assert find(window, 'timelineOffscreen_r0').isVisible()
    assert find(window, 'timelineOffscreen_r99').isVisible()
    assert '◀' in find(window, 'timelineOffscreen_r0').property('text')
    assert '▶' in find(window, 'timelineOffscreen_r99').property('text')
    # v0.23 follows the transport head even in gaps. Disable it explicitly
    # before testing an edit of a remote cue during playback.
    click(window, find(window, 'followCurrentButton'))
    assert not timeline.property('followEnabled')
    bridge.command('media.play', {})
    wait(app, lambda: bridge.media['playing'])
    transport = []
    monkeypatch.setattr(bridge.media_controller, 'seek', lambda *a: transport.append('seek'))
    monkeypatch.setattr(bridge.media_controller, 'pause', lambda *a: transport.append('pause'))
    clip = find(window, 'timelineClip_r40'); click(window, clip)
    assert bridge.media['playing'] and not transport
    start = point(clip); ruler = find(window, 'timelineRulerMouse')
    dx = round(2 * ruler.width() / timeline.property('span'))
    drag(window, start, start + QPoint(dx, 0))
    assert bridge.media['playing'] and not transport
    assert state_frames(bridge, 'r40') == (602, 609)


def test_vertical_thumb_and_offscreen_row_select_without_seek(ui, monkeypatch):
    app, bridge, window, timeline = ui
    transport = []
    monkeypatch.setattr(bridge.media_controller, 'seek', lambda *a: transport.append('seek'))
    monkeypatch.setattr(bridge.media_controller, 'pause', lambda *a: transport.append('pause'))
    scroll = find(window, 'timelineVerticalScroll'); tracks = find(window, 'timelineTracks')
    old_y = tracks.property('contentY')
    position = scroll.property('position'); size = scroll.property('size')
    start = point(scroll, scroll.width() / 2, (position + size / 2) * scroll.height())
    drag(window, start, point(scroll, scroll.width() / 2, scroll.height() - 2))
    assert tracks.property('contentY') > old_y and timeline.property('followEnabled')
    before_view = (timeline.property('viewFirst'), timeline.property('viewLast'))
    hint = find(window, 'timelineOffscreen_r99'); assert hint and hint.isVisible()
    click(window, hint)
    assert bridge.selected == 'r99' and not transport
    assert (timeline.property('viewFirst'), timeline.property('viewLast')) == before_view
    # A row without timings is still selectable through the same visible text.
    row = bridge._row('r99'); row.start = row.end = None; bridge._changed(); QTest.qWait(80)
    bridge.command('timeline.select', {'id': 'r40'}); QTest.qWait(50)
    hint = find(window, 'timelineOffscreen_r99'); assert hint and hint.isVisible()
    assert '未打轴' in hint.property('text')
    click(window, hint)
    assert bridge.selected == 'r99' and not transport


def test_manual_create_mode_toggle_cancel_and_exact_drag(ui):
    app, bridge, window, timeline = ui
    click(window, find(window, 'timelineCreateMenuButton'))
    click(window, find(window, 'timelineCreateRangeMenuItem'))
    assert timeline.property('createMode')
    click(window, find(window, 'timelineCreateMenuButton'))
    click(window, find(window, 'timelineCreateRangeMenuItem'))
    assert not timeline.property('createMode')
    click(window, find(window, 'timelineCreateMenuButton'))
    click(window, find(window, 'timelineCreateRangeMenuItem'))
    ruler = find(window, 'timelineRulerMouse')
    start = point(ruler, ruler.width() * .12, 56)
    end = point(ruler, ruler.width() * .28, 56)
    before = bridge.document.snapshot()
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(window, end, 40)
    QTest.keyClick(window, Qt.Key.Key_Escape)
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    assert bridge.document.snapshot() == before
    first = round(timeline.property('viewFirst') + .12 * timeline.property('span'))
    last = round(timeline.property('viewFirst') + .28 * timeline.property('span'))
    frame = bridge.media['requestedFrame']; history = len(bridge.history.undo_stack)
    drag(window, start, end)
    assert state_frames(bridge, bridge.selected) == (first, last)
    assert bridge.state['selection']['current']['chinese'] == ''
    assert bridge.media['requestedFrame'] == frame
    assert len(bridge.history.undo_stack) == history + 1
    # D timing mode remains a separate paired in/out action.
    click(window, find(window, 'manualTimingMode'))
    QTest.keyClick(window, Qt.Key.Key_D); QTest.qWait(50)
    assert bridge.uiState['manualTiming']['startFrame'] == bridge.media['displayedFrame']


# Tool artwork is checked against actual rendered pixels and Qt's selected
# native window cursor. grabWindow() does not include the OS cursor: the cursor
# pixmap is saved separately, never composited into a purported UI screenshot.
def tool_visual_evidence():
    out = Path(os.environ.get('LYRIA_TOOL_VISUALS_EVIDENCE',
                              str(ROOT.parent / 'validation/timeline-tool-visuals-20261104')))
    out.mkdir(parents=True, exist_ok=True)
    return out


def move_pointer(window, position):
    QTest.mouseMove(window, position)
    QTest.qWait(35)
    assert QApplication.overrideCursor() is None
    return window.cursor()


def cursor_evidence(window, name, expected):
    import hashlib
    import json
    from desktop_qt.tool_cursor import razor_cursor
    cursor = window.cursor()
    out = tool_visual_evidence()
    report = {'shape': cursor.shape().name, 'hotspot': [cursor.hotSpot().x(), cursor.hotSpot().y()],
              'source': 'QQuickWindow.cursor after real QTest pointer event',
              'global_override': QApplication.overrideCursor() is not None}
    if expected in ('razor', 'forbidden'):
        assert cursor.shape() == Qt.CursorShape.BitmapCursor
        pixmap = cursor.pixmap()
        assert not pixmap.isNull() and cursor.hotSpot() == QPoint(8, 22)
        assert pixmap.toImage() == razor_cursor(expected == 'forbidden', window.devicePixelRatio()).pixmap().toImage()
        image = out / (name + '-native-cursor.png')
        assert pixmap.save(str(image))
        report.update({'size': [pixmap.width(), pixmap.height()], 'dpr': pixmap.devicePixelRatio(),
                       'cursor_sha256': hashlib.sha256(image.read_bytes()).hexdigest(), 'kind': expected})
    else:
        assert cursor.shape() == expected
        assert cursor.pixmap().isNull()
    (out / (name + '-cursor.json')).write_text(json.dumps(report, indent=2), encoding='utf8')
    return cursor


@pytest.mark.parametrize('dimensions', [(1400, 940), (760, 600)])
def test_tool_icons_render_and_fit_narrow_window(ui, dimensions):
    app, bridge, window, timeline = ui
    window.resize(*dimensions)
    QTest.qWait(260)
    outer = find(window, 'makeScroll')
    assert outer.property('contentHeight') is None
    QTest.qWait(100)
    from PySide6.QtCore import QRect
    shot = window.grabWindow()
    ratio = shot.width() / window.width()
    buttons = [find(window, 'timeline' + name + 'Tool') for name in ('Select', 'Razor', 'Hand')]
    last_right = -1
    for button, glyph in zip(buttons, ('tool-select', 'tool-razor', 'tool-hand')):
        assert button.isVisible() and button.property('glyph') == glyph
        icon = next(item for item in walk(button) if item.metaObject().className().startswith('QQuickImage')
                    and glyph in item.property('source').toString())
        assert icon.isVisible() and icon.property('sourceSize').width() > 0
        start = point(button, 0, 0)
        assert start.x() > last_right and start.x() + button.width() <= window.width()
        assert 0 <= start.y() < start.y() + button.height() <= window.height()
        last_right = start.x() + button.width()
        p = point(icon, 0, 0)
        rect = QRect(round(p.x() * ratio), round(p.y() * ratio),
                     round(icon.width() * ratio), round(icon.height() * ratio))
        crop = shot.copy(rect)
        # Actual dark lilac strokes occupy pixels inside the visible icon box.
        assert sum(crop.pixelColor(x, y).lightnessF() < .65
                   for x in range(crop.width()) for y in range(crop.height())) > 10
        assert crop.save(str(tool_visual_evidence() / f'{glyph}-{dimensions[0]}-render.png'))
    assert shot.save(str(tool_visual_evidence() / f'tool-icons-{dimensions[0]}x{dimensions[1]}.png'))
    for name, mode in [('Razor', 'razor'), ('Hand', 'hand'), ('Select', 'select')]:
        click(window, find(window, 'timeline' + name + 'Tool'))
        assert timeline.property('tool') == mode
        assert find(window, 'timeline' + name + 'Tool').property('checked')
    # The optional allocation action must also fit with all three new glyphs.
    click(window, find(window, 'timelineRazorTool'))
    click(window, find(window, 'timelineClip_r40'))
    QTest.qWait(230)
    controls = ['timelineSelectTool', 'timelineRazorTool', 'timelineHandTool',
                'timelineSnapToggle', 'timelineCreateMenuButton', 'audioVisibilityToggle',
                'allocateTextButton', 'timelineZoomOut', 'timelineZoomIn', 'manualTimingMode',
                'followCurrentButton', 'timelineFoldButton']
    rectangles = []
    for name in controls:
        control = find(window, name); assert control and control.isVisible()
        pos = point(control, 0, 0)
        assert 0 <= pos.x() and pos.x() + control.width() <= window.width()
        assert 0 <= pos.y() and pos.y() + control.height() <= window.height()
        rectangles.append((name, pos.x(), pos.y(), control.width(), control.height()))
    # Central transport is intentionally independent of the left editing tools.
    # Verify geometric exclusion rather than an obsolete enumeration order.
    for i, (name, x, y, width, height) in enumerate(rectangles):
        for other, xx, yy, ww, hh in rectangles[i+1:]:
            assert min(x+width,xx+ww)-max(x,xx) <= 1 or min(y+height,yy+hh)-max(y,yy) <= 1, (name,other)
    assert find(window,'timelinePlayPause') is None
    assert max(r[2]+r[4]/2 for r in rectangles)-min(r[2]+r[4]/2 for r in rectangles)<=3
    ordered = [find(window,n) for n in ('timelineHandTool','timelineSnapToggle','timelineCreateMenuButton')]
    for left, right in zip(ordered,ordered[1:]):
        assert point(left,0,0).x()+left.width() <= point(right,0,0).x()
        assert abs(point(left).y()-point(right).y()) <= 1
    assert window.grabWindow().save(str(tool_visual_evidence() / f'tool-icons-pending-{dimensions[0]}.png'))


@pytest.mark.parametrize('kind', ['valid', 'first', 'after_last', 'locked', 'single', 'blank', 'gap', 'label', 'ruler'])
def test_native_razor_allowed_and_forbidden_regions(ui, kind):
    app, bridge, window, timeline = ui
    row = bridge._row('r40')
    if kind == 'locked':
        bridge.document.project.settings['manual_locked_ids'] = ['r40']
    if kind == 'single':
        row.end = bridge.media_controller.index.interval(420, 420)[1]
    bridge._changed(); QTest.qWait(60)
    click(window, find(window, 'timelineRazorTool'))
    clip = find(window, 'timelineClip_r40')
    ruler = find(window, 'timelineRulerMouse')
    tracks = find(window, 'timelineTracks')
    row_item = find(window, 'timelineRow_r40')
    positions = {
        'valid': point(clip), 'first': point(clip, 1, 13),
        'after_last': point(clip, clip.width() + 3, 13),
        'locked': point(clip), 'single': point(clip, 1, 13),
        'blank': point(ruler, 5, 43), 'gap': point(row_item, 250, 28),
        'label': point(row_item, 25, 13), 'ruler': point(ruler, 250, 10),
    }
    move_pointer(window, positions[kind])
    cursor_evidence(window, 'razor-' + kind, 'razor' if kind == 'valid' else 'forbidden')
    assert window.grabWindow().save(str(tool_visual_evidence() / ('razor-' + kind + '-ui.png')))
    if kind != 'ruler':  # Ruler retains its existing scrub behavior in C.
        before = bridge.document.snapshot(); history = len(bridge.history.undo_stack)
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, positions[kind])
        QTest.qWait(50)
        if kind == 'valid':
            assert len(bridge.history.undo_stack) == history + 1
            assert len(bridge.document.project.mappings) == 101
        else:
            assert bridge.document.snapshot() == before and len(bridge.history.undo_stack) == history


def test_tool_cursor_restores_buttons_menu_editor_and_ime(ui):
    app, bridge, window, timeline = ui
    from PySide6.QtGui import QInputMethodEvent
    click(window, find(window, 'timelineRazorTool'))
    move_pointer(window, point(find(window, 'timelineClip_r40')))
    cursor_evidence(window, 'restore-start-razor', 'razor')
    button = find(window, 'timelineCreateMenuButton')
    move_pointer(window, point(button))
    cursor_evidence(window, 'restore-button', Qt.CursorShape.ArrowCursor)
    click(window, button)
    move_pointer(window, point(find(window, 'timelineCreateRangeMenuItem')))
    cursor_evidence(window, 'restore-menu', Qt.CursorShape.ArrowCursor)
    QTest.keyClick(window, Qt.Key.Key_Escape); QTest.qWait(50)
    timeline.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_F2); QTest.qWait(60)
    editor = find(window, 'chineseEditor_r40')
    assert editor.hasActiveFocus() and not editor.property('readOnly')
    move_pointer(window, point(editor, 30, 15))
    cursor_evidence(window, 'restore-editor', Qt.CursorShape.IBeamCursor)
    assert editor.cursor().shape() == Qt.CursorShape.IBeamCursor
    QTest.keyClick(window, Qt.Key.Key_End)
    for key in (Qt.Key.Key_C, Qt.Key.Key_H, Qt.Key.Key_V):
        QTest.keyClick(window, key)
    assert editor.property('text').endswith('chv') and timeline.property('tool') == 'razor'
    before = bridge.document.snapshot()
    app.sendEvent(editor, QInputMethodEvent('拼音', [])); QTest.qWait(30)
    assert editor.property('inputMethodComposing') and bridge.document.snapshot() == before
    move_pointer(window, point(editor, 31, 15))
    cursor_evidence(window, 'restore-ime', Qt.CursorShape.IBeamCursor)
    commit = QInputMethodEvent(); commit.setCommitString('中文')
    app.sendEvent(editor, commit); QTest.qWait(40)
    assert not editor.property('inputMethodComposing') and editor.property('text').endswith('chv中文')
    QTest.keyClick(window, Qt.Key.Key_Escape); QTest.qWait(50)
    move_pointer(window, point(find(window, 'timelineClip_r40')))
    cursor_evidence(window, 'restore-return-razor', 'razor')
    move_pointer(window, QPoint(12, 110))
    assert window.cursor().shape() != Qt.CursorShape.BitmapCursor
    assert QApplication.overrideCursor() is None


def test_native_hand_select_edge_and_repeated_razor_chain(ui, monkeypatch):
    app, bridge, window, timeline = ui
    transport = []
    monkeypatch.setattr(bridge.media_controller, 'seek', lambda *a: transport.append('seek'))
    monkeypatch.setattr(bridge.media_controller, 'pause', lambda *a: transport.append('pause'))
    before = bridge.document.snapshot(); history = len(bridge.history.undo_stack)
    timeline.forceActiveFocus(); QTest.keyClick(window, Qt.Key.Key_H); QTest.qWait(30)
    hand = find(window, 'timelineHandRowsMouse'); start = point(hand)
    move_pointer(window, start)
    cursor_evidence(window, 'chain-hand-open', Qt.CursorShape.OpenHandCursor)
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(window, start + QPoint(12, -10), 30)
    cursor_evidence(window, 'chain-hand-closed', Qt.CursorShape.ClosedHandCursor)
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(12, -10))
    move_pointer(window, start + QPoint(13, -10))
    cursor_evidence(window, 'chain-hand-released', Qt.CursorShape.OpenHandCursor)
    assert bridge.document.snapshot() == before and len(bridge.history.undo_stack) == history
    QTest.keyClick(window, Qt.Key.Key_V); QTest.qWait(40)
    clip = find(window, 'timelineClip_r40'); start = point(clip)
    move_pointer(window, start)
    cursor_evidence(window, 'chain-select', Qt.CursorShape.ArrowCursor)
    ruler = find(window, 'timelineRulerMouse')
    dx = round(2 * ruler.width() / timeline.property('span'))
    drag(window, start, start + QPoint(dx, 0))
    assert state_frames(bridge, 'r40') == (422, 429)
    assert bridge.command('history.undo', {}); QTest.qWait(60)
    edge = find(window, 'cueEdge_last'); start = point(edge)
    move_pointer(window, start)
    cursor_evidence(window, 'chain-edge', Qt.CursorShape.SizeHorCursor)
    drag(window, start, start + QPoint(dx, 0))
    assert state_frames(bridge, 'r40') == (420, 429)
    assert bridge.command('history.undo', {}); QTest.qWait(60)
    timeline.forceActiveFocus(); QTest.keyClick(window, Qt.Key.Key_C); QTest.qWait(40)
    for cut in (424, 426):
        identity = 'r40' if cut == 424 else bridge.document.project.mappings[41].id
        clip = find(window, 'timelineClip_' + identity)
        x = (cut - timeline.property('viewFirst')) / timeline.property('span') * ruler.width()
        pos = QPoint(point(ruler, x, 0).x(), point(clip).y())
        move_pointer(window, pos)
        cursor_evidence(window, 'chain-cut-' + str(cut), 'razor')
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
        QTest.qWait(60)
    assert len(bridge.document.project.mappings) == 102
    assert len(bridge.history.undo_stack) == history + 2 and not transport
    assert timeline.property('tool') == 'razor' and not timeline.property('movingClip')
    assert not timeline.property('editing') and not timeline.property('panning')


def test_tool_cursor_model_reset_hide_fold_and_rebind(ui):
    app, bridge, window, timeline = ui
    from desktop_qt.tool_cursor import ToolCursor
    from PySide6.QtQuick import QQuickItem
    from shiboken6 import delete, isValid
    click(window, find(window, 'timelineRazorTool'))
    clip = find(window, 'timelineClip_r40')
    move_pointer(window, point(clip))
    cursor_evidence(window, 'lifecycle-before-reset', 'razor')
    # This rebuild queues delegate retirement. A fixed wall-clock QTest wait
    # does not guarantee DeferredDelete has run; observe actual destruction.
    from tools.qt_event_loop_wait import wait_until
    bridge.document.project.mappings = bridge.document.project.mappings[40:43]
    bridge._changed()
    def retired():
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        return not isValid(clip)
    wait_until(retired, timeout_ms=3000)
    assert not isValid(clip)
    wait_for_timeline_viewport(window, timeline)
    move_pointer(window, point(find(window, 'timelineClip_r40')))
    cursor_evidence(window, 'lifecycle-after-reset', 'razor')
    for key, shape in [(Qt.Key.Key_H, Qt.CursorShape.OpenHandCursor),
                       (Qt.Key.Key_V, Qt.CursorShape.ArrowCursor)]:
        timeline.forceActiveFocus(); QTest.keyClick(window, key); QTest.qWait(40)
        move_pointer(window, point(find(window, 'timelineClip_r40')))
        assert window.cursor().shape() == shape
    QTest.keyClick(window, Qt.Key.Key_C); QTest.qWait(40)
    move_pointer(window, point(find(window, 'timelineClip_r40')))
    cursor_evidence(window, 'lifecycle-reentered-c', 'razor')
    timeline.setVisible(False); QTest.qWait(60)
    move_pointer(window, point(find(window, 'followCurrentButton')))
    assert window.cursor().shape() != Qt.CursorShape.BitmapCursor
    timeline.setVisible(True); QTest.qWait(60)
    click(window, find(window, 'timelineFoldButton')); QTest.qWait(250)
    move_pointer(window, point(find(window, 'timelineFoldButton')))
    cursor_evidence(window, 'lifecycle-folded', Qt.CursorShape.ArrowCursor)
    click(window, find(window, 'timelineFoldButton')); QTest.qWait(250)
    move_pointer(window, point(find(window, 'timelineClip_r40')))
    cursor_evidence(window, 'lifecycle-unfolded', 'razor')
    assert QApplication.overrideCursor() is None


def test_tool_cursor_native_target_and_binding_destruction(application):
    from desktop_qt.tool_cursor import ToolCursor
    from PySide6.QtQuick import QQuickItem, QQuickWindow
    from shiboken6 import delete
    # Separate native owner avoids a PySide 6.8 ownership-wrapper artifact when
    # shiboken.delete is used on Python-created children of a QML-owned window.
    # Real QML delegate deletion is exercised in the preceding test.
    owner = QQuickWindow()
    first, second = QQuickItem(owner.contentItem()), QQuickItem(owner.contentItem())
    binding = ToolCursor(); binding.mode = 'razor'; binding.target = first
    assert first.cursor().shape() == Qt.CursorShape.BitmapCursor
    binding.target = second
    assert first.cursor().shape() == Qt.CursorShape.ArrowCursor
    assert second.cursor().shape() == Qt.CursorShape.BitmapCursor
    delete(second)
    assert binding.target is None and binding._window is None and binding._screen is None
    binding.mode = 'forbidden'; binding.target = first
    assert first.cursor().shape() == Qt.CursorShape.BitmapCursor
    delete(binding)
    assert first.cursor().shape() == Qt.CursorShape.ArrowCursor
    delete(first)
    remaining = QQuickItem(owner.contentItem())
    bound = ToolCursor(); bound.mode = 'razor'; bound.target = remaining
    delete(owner)
    assert bound.target is None and bound._window is None and bound._screen is None
    delete(bound)
    assert QApplication.overrideCursor() is None


@pytest.mark.parametrize('tool', ['Select', 'Hand', 'Razor'])
@pytest.mark.parametrize('entry', ['F2', 'double_click'])
def test_tool_cursor_editor_entry_and_exit_for_each_tool(ui, tool, entry):
    app, bridge, window, timeline = ui
    click(window, find(window, 'timeline' + tool + 'Tool'))
    mode = timeline.property('tool')
    QTest.qWait(250)  # Settle the selected lyric row before pixel-positioned entry.
    editor = find(window, 'chineseEditor_r40')
    if entry == 'F2':
        QTest.keyClick(window, Qt.Key.Key_F2)
    else:
        QTest.mouseDClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         point(editor, 30, 15))
    QTest.qWait(50)
    assert editor.hasActiveFocus() and not editor.property('readOnly')
    move_pointer(window, point(editor, 31, 15))
    cursor_evidence(window, f'{tool}-{entry}-editing', Qt.CursorShape.IBeamCursor)
    QTest.keyClick(window, Qt.Key.Key_End); QTest.keyClick(window, Qt.Key.Key_X)
    assert editor.property('text').endswith('x') and timeline.property('tool') == mode
    QTest.keyClick(window, Qt.Key.Key_Escape); QTest.qWait(50)
    assert editor.property('readOnly') and not editor.hasActiveFocus()
    move_pointer(window, point(editor, 32, 15))
    cursor_evidence(window, f'{tool}-{entry}-readonly', Qt.CursorShape.ArrowCursor)
    assert timeline.property('tool') == mode


def test_viewport_wait_rejects_cached_offscreen_targets_without_forcing_reveal(ui):
    app, bridge, window, timeline = ui
    before = bridge.document.snapshot()
    timeline.setProperty('followEnabled', False)
    tracks = find(window, 'timelineTracks')
    tracks.setProperty('contentY', 0)
    with pytest.raises(AssertionError, match='did not settle within timeout'):
        wait_for_timeline_viewport(window, timeline, timeout=.25)
    assert tracks.property('contentY') == 0
    assert bridge.selected == 'r40' and bridge.document.snapshot() == before
