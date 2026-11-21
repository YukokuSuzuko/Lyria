"""Actual Main.qml/QTest timeline folding, without animating editing geometry.

Local synthetic video, real Qt scene and real media worker. Offscreen/software
screenshots are cloud regression evidence, not Windows smoothness acceptance.
"""
import hashlib
import json
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop_qt.controller import Bridge
from lyricsync.model import Mapping, Translation
from tools.qt_event_loop_wait import delay, wait_until

ROOT = Path(__file__).resolve().parents[1]


def walk(item):
    yield item
    for child in item.childItems():
        yield from walk(child)


def find(window, name):
    item = window.findChild(QObject, name)
    if item is None:
        item = next((i for i in walk(window.contentItem()) if i.objectName() == name), None)
    assert item is not None, name
    return item


def point(item, x=None, y=None):
    return item.mapToScene(QPointF(item.width() / 2 if x is None else x,
                                  item.height() / 2 if y is None else y)).toPoint()


def click(window, item):
    assert item.isVisible() and item.isEnabled(), item.objectName()
    pos = point(item)
    assert 0 <= pos.x() <= window.width() and 0 <= pos.y() <= window.height(), (item.objectName(), pos)
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)


def reveal(window):
    outer = find(window, 'makeScroll')
    assert outer.property('contentHeight') is None
    delay(25)


def settled(timeline, collapsed):
    return (timeline.property('collapsed') == collapsed and not timeline.property('folding')
            and timeline.implicitHeight() == (55 if collapsed else timeline.property('expandedHeight')))


def fold_sample(window):
    timeline = find(window, 'frameTimeline')
    content = find(window, 'timelineFoldContent')
    return {'collapsed': timeline.property('collapsed'), 'height': timeline.height(),
            'implicit_height': timeline.implicitHeight(), 'content_height': content.height(),
            'opacity': content.opacity(), 'arrow': find(window, 'timelineFoldArrow').rotation(),
            'content_enabled': content.isEnabled(), 'content_visible': content.isVisible()}


def view_snapshot(window):
    timeline = find(window, 'frameTimeline')
    return {**{key: timeline.property(key) for key in
            ('viewFirst', 'viewLast', 'followEnabled', 'manualView', 'tool', 'rowMode', 'createMode')},
            'content_y': find(window, 'timelineTracks').property('contentY'),
            'track_height': find(window, 'timelineTracks').height()}


@pytest.fixture(scope='module')
def application():
    QQuickStyle.setStyle('Basic')
    return QApplication.instance() or QApplication([])


@pytest.fixture
def ui(application, tmp_path, monkeypatch):
    out = Path(os.environ.get('LYRIA_TIMELINE_FOLD_EVIDENCE', str(tmp_path / 'fold-evidence')))
    out.mkdir(parents=True, exist_ok=True)
    bridge = Bridge(storage_dir=tmp_path / 'recovery')
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(str(i) for i in items))
    try:
        engine.rootContext().setContextProperty('bridge', bridge)
        engine.load(QUrl.fromLocalFile(str(ROOT / 'desktop_qt/qml/Main.qml')))
        assert engine.rootObjects(), warnings
        window = engine.rootObjects()[0]
        window.requestActivate()
        assert bridge.command('media.open', {'path': str(ROOT / 'examples/synthetic_mv.mp4')})
        wait_until(lambda: bridge.media['ready'] and bridge.media['displayedFrame'] == 1)
        project = bridge.document.project
        index = bridge.media_controller.index
        project.translations = [Translation(f'fold-t{i}', f'折叠测试第{i + 1}句') for i in range(45)]
        project.mappings = [Mapping(f'fold-c{i}', [f'fold-t{i}'], start=index.interval(10 + i * 6, 14 + i * 6)[0],
                                    end=index.interval(10 + i * 6, 14 + i * 6)[1]) for i in range(45)]
        bridge.selected = 'fold-c20'
        bridge.stage = 'review'
        bridge._changed()
        timeline = find(window, 'frameTimeline')
        wait_until(lambda: settled(timeline, False))
        delay(250)
        reveal(window)
        transport = []
        monkeypatch.setattr(bridge.media_controller, 'seek', lambda *a, **kw: transport.append(('seek', a, kw)))
        monkeypatch.setattr(bridge.media_controller, 'pause', lambda *a, **kw: transport.append(('pause', a, kw)))
        def forbidden_ocr():
            raise AssertionError('Folding must not start OCR')
        monkeypatch.setattr(bridge, '_run_ocr', forbidden_ocr)
        media_before = {key: bridge.media[key] for key in ('requestedFrame', 'displayedFrame', 'decoderSeeks', 'decodedFrames')}
        yield bridge, window, timeline, out
        assert {key: bridge.media[key] for key in media_before} == media_before
        assert not transport
        assert all(key.startswith('save-') for key in bridge.jobs.active), bridge.jobs.active  # prior _changed autosave may overlap
    finally:
        # Setup failures before yield must still drain native workers before owners die.
        bridge.shutdown()
        wait_until(lambda: bridge.shutdown_complete, timeout_ms=10000)
        for root in engine.rootObjects():
            root.setProperty('allowClose', True);root.close()
        engine.deleteLater();application.processEvents()
    assert not warnings, warnings


def test_partial_reverse_and_motion_off_synchronously_settle(ui):
    bridge, window, timeline, out = ui
    content = find(window, 'timelineFoldContent')
    arrow = find(window, 'timelineFoldArrow')
    button = find(window, 'timelineFoldButton')
    playhead = find(window, 'timelinePlayhead')
    before = bridge.document.snapshot()
    selected = bridge.selected
    rows = bridge.state['rows']
    view = view_snapshot(window)
    budget_before={k:timeline.property(k) for k in ('height','expandedLayoutHeight','expandedHeaderHeight','audioBudgetHeight','audioDisplayHeight','audioHeight','audioExpanded','folding','visible','collapsed')}
    playhead_x = playhead.x()
    samples = []
    assert content.clip() and timeline.clip()
    click(window, button)
    assert timeline.property('collapsed') and timeline.property('folding')
    assert not content.isEnabled(), 'Content gives up input before its exit fade finishes'
    delay(45)
    sample = fold_sample(window)
    samples.append(sample)
    assert 55 < sample['implicit_height'] < timeline.property('expandedHeight')
    assert 0 < sample['opacity'] < 1 and 0 < sample['arrow'] < 180
    assert sample['content_visible'] and sample['content_height'] > 0
    assert view_snapshot(window) == view and playhead.x() == playhead_x, (budget_before,{k:timeline.property(k) for k in budget_before})
    assert window.grabWindow().save(str(out / 'timeline-fold-mid-close-1400x940.png'))
    # Reverse a live fold using the actual control, not a property-only test.
    reversal_events = []
    def before_reverse():
        reversal_events.append(('released', fold_sample(window)))
    def after_reverse():
        reversal_events.append(('clicked', fold_sample(window)))
    button.released.connect(before_reverse)
    button.clicked.connect(after_reverse)
    try:
        click(window, button)
    finally:
        button.released.disconnect(before_reverse)
        button.clicked.disconnect(after_reverse)
    assert not timeline.property('collapsed') and content.isEnabled()
    # Sample on either side of the actual QML click handler. QTest may process
    # animation frames before returning, especially in the full Windows suite.
    # Event-local samples check continuity without depending on that delay.
    assert [event for event, _ in reversal_events] == ['released', 'clicked']
    reversal = reversal_events[0][1]
    reversed_sample = reversal_events[1][1]
    assert reversal['collapsed'] and not reversed_sample['collapsed']
    assert 0 < reversal['opacity'] < 1
    for key in ('opacity', 'arrow', 'implicit_height'):
        assert reversed_sample[key] == pytest.approx(reversal[key], abs=1e-6)
    delay(40)
    assert content.opacity() > reversal['opacity'] and arrow.rotation() < reversal['arrow']
    assert timeline.implicitHeight() > reversal['implicit_height']
    assert bridge.document.snapshot() == before and bridge.selected == selected
    assert bridge.command('settings.set', {'key': 'motion', 'value': False})
    assert settled(timeline, False) and content.opacity() == 1 and arrow.rotation() == 0
    # A synchronous target is observable without an event-loop tick.
    click(window, button)
    assert settled(timeline, True) and content.opacity() == 0 and arrow.rotation() == 180
    assert not content.isVisible() and not content.isEnabled()
    delay(250)
    assert timeline.height() == 55 and content.height() == 0
    assert window.grabWindow().save(str(out / 'timeline-fold-closed-1400x940.png'))
    click(window, button)
    assert settled(timeline, False) and content.opacity() == 1 and arrow.rotation() == 0
    bridge.command('settings.set', {'key': 'motion', 'value': True})
    for collapsed in (True, False, True, False):
        click(window, button)
        delay(35)
        assert timeline.property('folding')
        assert 0 < content.opacity() < 1
        bridge.command('settings.set', {'key': 'motion', 'value': False})
        assert settled(timeline, collapsed)
        assert content.opacity() == (0 if collapsed else 1)
        assert arrow.rotation() == (180 if collapsed else 0)
        samples.append(fold_sample(window))
        delay(230)
        assert settled(timeline, collapsed), 'No old animation may resume after disabling motion'
        bridge.command('settings.set', {'key': 'motion', 'value': True})
    assert view_snapshot(window) == view and playhead.x() == playhead_x
    # Changing the saved motion preference itself advances document revision.
    assert bridge.state['rows'] == rows and bridge.selected == selected
    (out / 'timeline-fold-motion.json').write_text(json.dumps({'status': 'passed', 'samples': samples,
        'qml_sha256': hashlib.sha256((ROOT / 'desktop_qt/qml/FrameTimeline.qml').read_bytes()).hexdigest(),
        'actual_qtest_fold_button': True, 'motion_values_off_synchronous': True,
        'actual_height_applied_by_next_qt_layout_pass': True,
        'view_selection_and_paused_decoder_unchanged': True, 'windows_acceptance': False}, indent=2))


def test_folding_cancels_hidden_drag_and_focus_without_mutation(ui):
    bridge, window, timeline, out = ui
    before = bridge.document.snapshot()
    history = len(bridge.history.undo_stack)
    # Follow preserves the user's overview zoom now. Explicitly zoom to make
    # the five-frame clip's body accessible between its two real 10px handles.
    click(window, find(window, 'timelineZoomIn'))
    wait_until(lambda: not timeline.property('folding'))
    reveal(window)
    clip = find(window, 'timelineClip_fold-c20')
    assert clip.width() >= 28
    start = point(clip)
    local = clip.mapFromScene(QPointF(start))
    assert 12 <= local.x() <= clip.width() - 12
    tracks = find(window, 'timelineTracks')
    in_tracks = tracks.mapFromScene(QPointF(start))
    assert 0 < in_tracks.y() < tracks.height()
    assert 0 < start.x() < window.width() and 0 < start.y() < window.height()
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(window, start + QPoint(40, 0), 25)
    assert timeline.property('movingClip')
    assert timeline.property('moveDelta') != 0
    timeline.setProperty('collapsed', True)
    assert not timeline.property('interacting')
    assert not find(window, 'timelineFoldContent').isEnabled()
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(40, 0))
    wait_until(lambda: settled(timeline, True))
    assert bridge.document.snapshot() == before and len(bridge.history.undo_stack) == history
    click(window, find(window, 'timelineFoldButton'))
    wait_until(lambda: settled(timeline, False))
    edge = find(window, 'cueEdge_first')
    edge.forceActiveFocus()
    assert edge.hasActiveFocus()
    click(window, find(window, 'timelineFoldButton'))
    assert not edge.hasActiveFocus() and not edge.isEnabled()
    assert find(window, 'timelineFoldButton').hasActiveFocus()
    assert not timeline.property('keyboardEditing')
    # The hidden tool does not receive a stale key, and Tab cannot land in it.
    for _ in range(8):
        QTest.keyClick(window, Qt.Key.Key_Tab)
        assert not any(i.hasActiveFocus() for i in walk(find(window, 'timelineFoldContent')))
    assert bridge.document.snapshot() == before and len(bridge.history.undo_stack) == history
    wait_until(lambda: settled(timeline, True))
    click(window, find(window, 'timelineFoldButton'))
    wait_until(lambda: settled(timeline, False))
    assert find(window, 'timelineRazorTool').isEnabled()
    # Both popup owners live inside the clipped content, so external collapse
    # must dismiss their separate Qt overlay rather than leave hidden input.
    for name, popup in [('timelineCreateMenuButton', find(window, 'timelineCreateMenu'))]:
        click(window, find(window, name))
        wait_until(lambda: popup.property('visible'))
        timeline.setProperty('collapsed', True)
        wait_until(lambda: not popup.property('visible'))
        wait_until(lambda: settled(timeline, True))
        click(window, find(window, 'timelineFoldButton'))
        wait_until(lambda: settled(timeline, False))
    assert bridge.document.snapshot() == before and len(bridge.history.undo_stack) == history
    assert window.grabWindow().save(str(out / 'timeline-fold-input-restored.png'))


def test_fold_row_modes_manual_view_and_small_window_bounds(ui):
    bridge, window, timeline, out = ui
    # Establish a manual view through the existing zoom/follow controls.
    click(window, find(window, 'timelineZoomIn'))
    click(window, find(window, 'followCurrentButton'))
    assert timeline.property('manualView') and not timeline.property('followEnabled')
    samples = []
    for width, height in [(760, 600), (1400, 940)]:
        window.resize(width, height)
        delay(100)
        for row_mode in (3, 5, 0):
            timeline.setProperty('rowMode', row_mode)
            bridge.command('timeline.manualMode', {'enabled': row_mode == 0})
            wait_until(lambda: settled(timeline, False))
            # Exercise row-mode -> window resize -> fold, not just startup's
            # original preferredHeight. A stale imperative preference must fail.
            window.resize(width + 40, height + 30)
            delay(60)
            window.resize(width, height)
            delay(60)
            reveal(window)
            tracks = find(window, 'timelineTracks')
            tracks.setProperty('contentY', 13 * timeline.property('rowHeight'))
            before = view_snapshot(window)
            expanded = timeline.height()
            assert timeline.height() >= timeline.property('minimumExpandedHeight')
            assert tracks.height() >= timeline.property('rowHeight')  # bounded 600px workspace may show one row
            assert tracks.mapToScene(QPointF(0,tracks.height())).y() <= window.height()
            if row_mode:
                assert tracks.height() <= row_mode * timeline.property('rowHeight')
            button = find(window, 'timelineFoldButton')
            for name in ('timelineSelectTool', 'timelineHandTool', 'audioVisibilityToggle', 'manualTimingMode'):
                item = find(window, name)
                location = point(item)
                assert 0 <= location.x() <= window.width() and 0 <= location.y() <= window.height(), (name, location)
            assert window.grabWindow().save(str(out / f'timeline-fold-open-{width}x{height}-rows-{row_mode}.png'))
            click(window, button)
            delay(45)
            samples.append({**fold_sample(window), 'window': [width, height], 'rows': row_mode})
            assert view_snapshot(window) == before
            wait_until(lambda: settled(timeline, True))
            delay(20)
            assert abs(timeline.height() - 55) < .001
            assert find(window, 'timelineFoldContent').height() == 0
            assert view_snapshot(window) == before
            reveal(window)
            assert window.grabWindow().save(str(out / f'timeline-fold-closed-{width}x{height}-rows-{row_mode}.png'))
            click(window, button)
            wait_until(lambda: settled(timeline, False))
            delay(20)
            reveal(window)
            assert view_snapshot(window) == before
            assert abs(timeline.height() - expanded) < 1
    (out / 'timeline-fold-bounds.json').write_text(json.dumps({'status': 'passed', 'samples': samples,
        'three_five_all_rows': True, 'manual_mode_height': True, 'manual_view_preserved': True,
        'window_sizes': [[760, 600], [1400, 940]], 'no_decode_or_ocr_triggered': True}, indent=2))


def test_hidden_timeline_dismisses_popup_owners(ui):
    bridge, window, timeline, out = ui
    before = bridge.document.snapshot()
    view = view_snapshot(window)
    for button_name, popup_name in [('timelineCreateMenuButton', 'timelineCreateMenu')]:
        button = find(window, button_name)
        popup = find(window, popup_name)
        click(window, button)
        wait_until(lambda: popup.property('visible'))
        # Programmatic navigation exercises visibility loss, without an outside
        # pointer press accidentally dismissing the popup before navigation.
        window.setProperty('workspace', 'qc')
        assert not timeline.isVisible() and not timeline.isEnabled()
        assert not button.isEnabled() and not find(window, 'timelineFoldContent').isEnabled()
        wait_until(lambda: not popup.property('visible'), timeout_ms=1000)
        assert not popup.property('opened')
        assert not any(i.hasActiveFocus() for i in walk(find(window, 'timelineFoldContent')))
        window.setProperty('workspace', 'make')
        wait_until(lambda: timeline.isVisible() and timeline.isEnabled())
        delay(200)
        reveal(window)
        assert not popup.property('visible'), 'Returning must not revive a hidden-owner popup'
    assert bridge.document.snapshot() == before and view_snapshot(window) == view
    assert window.grabWindow().save(str(out / 'timeline-hidden-popup-restored.png'))


def test_removed_split_drag_preserves_height_and_fold_layout_persistence(ui):
    bridge, window, timeline, out = ui
    window.resize(1600, 1200)
    delay(150)
    reveal(window)
    click(window, find(window, 'timelineZoomIn'))
    span = timeline.property('span')
    before = bridge.document.snapshot()
    selected = bridge.selected
    split = find(window, 'verticalSplit')
    restored_handle = window.findChild(QObject, 'timelineSplitHandle')
    assert restored_handle is not None and restored_handle.isVisible() and restored_handle.height() >= 6
    assert any(i.objectName() == 'timelineSplitHandle' for i in walk(window.contentItem()))
    # v0.22 restores the divider and persists its continuous layout changes.
    # Existing fold and row-capacity behavior must still round-trip.
    assert bridge.command('view.layout', {'topFraction': .72, 'audioHeight': 180.})
    saved_layout = dict(bridge.state['workspaceLayout'])
    layout_file = bridge.storage_dir / 'workspace-layout.json'
    assert json.loads(layout_file.read_text(encoding='utf-8')) == saved_layout
    assert find(window, 'playerSeekRail').isVisible()
    assert find(window, 'playerControlsResizeHandle').isVisible()

    def assert_bound_height():
        # This observable target is computed from the same public layout inputs
        # as the actual preferredHeight binding, not from the observed height.
        wait_until(lambda: abs(timeline.height() - split.property('requestedTimelineHeight')) < 1)
        assert timeline.property('span') == span
        assert bridge.document.snapshot() == before and bridge.selected == selected

    def fold_round_trip():
        delay(250)  # Settle row-mode/resize and Flickable bounds before the fold baseline.
        expanded = timeline.height()
        view = view_snapshot(window)
        click(window, find(window, 'timelineFoldButton'))
        wait_until(lambda: settled(timeline, True))
        delay(25)
        assert timeline.height() == 55
        click(window, find(window, 'timelineFoldButton'))
        wait_until(lambda: settled(timeline, False))
        delay(25)
        assert abs(timeline.height() - expanded) < 1
        assert view_snapshot(window) == view
        assert_bound_height()

    for delta in (-60, 40):
        reveal(window)
        start = point(restored_handle, restored_handle.width()/2, restored_handle.height()/2)
        assert 0 <= start.x() < window.width() and 0 <= start.y() < window.height()
        old_height = timeline.height()
        fraction = split.property('topFraction')
        QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        QTest.mouseMove(window, start + QPoint(0, delta), 30)
        delay(25)
        assert abs(timeline.height() - old_height + delta) < 2
        QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(0, delta))
        delay(60)
        assert abs(timeline.height() - old_height + delta) < 2
        assert split.property('timelineResized')
        assert split.property('topFraction') != fraction
        saved_layout = dict(bridge.state['workspaceLayout'])
        assert json.loads(layout_file.read_text(encoding='utf-8')) == saved_layout
        assert_bound_height()
        for row_mode in (3, 5, 0):
            timeline.setProperty('rowMode', row_mode)
            window.resize(1600, 1240)
            delay(80)
            reveal(window)
            assert_bound_height()
            fold_round_trip()
            window.resize(1600, 1200)
            delay(80)
            assert_bound_height()
    # A fresh controller reads the same persisted preferences. This is app
    # layout state, not a project edit or an undo entry.
    restored = Bridge(storage_dir=bridge.storage_dir)
    try:
        assert restored.state['workspaceLayout'] == saved_layout
    finally:
        restored.shutdown()
    assert bridge.document.snapshot() == before

    # Reset remains available through the actual menu, even though the
    # removed divider can no longer change this preference.
    click(window, find(window, 'viewMenuButton'))
    wait_until(lambda: find(window, 'viewMenu').property('opened'))
    click(window, find(window, 'resetWorkspaceLayout'))
    wait_until(lambda: not split.property('timelineResized'))
    for row_mode in (3, 5, 0):
        timeline.setProperty('rowMode', row_mode)
        wait_until(lambda: settled(timeline, False))
        delay(40)
        assert_bound_height()
        assert abs(timeline.height() - split.property('requestedTimelineHeight')) < 1
        fold_round_trip()
    assert json.loads(layout_file.read_text(encoding='utf-8')) == bridge.state['workspaceLayout']
    assert bridge.document.snapshot() == before and bridge.selected == selected
    assert window.grabWindow().save(str(out / 'timeline-removed-split-fold-restored.png'))
