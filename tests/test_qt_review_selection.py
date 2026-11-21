"""Real Main.qml review viewport regression, including reset and manual scrolling.

Checks delegate geometry in the ListView viewport, not merely cached objects.
Linux offscreen rendering is not Windows/IME platform acceptance.
"""
import os
from pathlib import Path
import time
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QInputMethodEvent, QWheelEvent
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication
from desktop_qt.controller import Bridge
from lyricsync.frames import FrameIndex
from lyricsync.model import Mapping, Translation

ROOT = Path(__file__).resolve().parents[1]


def walk(item):
    yield item
    for child in item.childItems():
        yield from walk(child)


def find(window, name):
    return next((i for i in walk(window.contentItem()) if i.objectName() == name), None)


def settle(app, condition, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            # Layout/animation can still move an initially visible cached item.
            QTest.qWait(180)
            if condition():
                return
        QTest.qWait(10)
    assert condition()


def enter_lyric_editing(ui):
    """Use the supported F2 entry, never manufacture TextArea focus."""
    QTest.keyClick(ui.window, Qt.Key.Key_F2)
    editor = find(ui.window, 'chineseEditor_' + ui.bridge.selected)
    settle(ui.app, lambda: editor is not None and editor.hasActiveFocus() and not editor.property('readOnly'))
    return editor


def geometry(ui, identity=None):
    row = find(ui.window, 'cueRow_' + (identity or ui.bridge.selected))
    if row is None:
        return None
    return row.mapToItem(ui.view, QPointF()).y(), row.height()


def contained(ui, identity=None):
    bounds = geometry(ui, identity)
    if bounds is None:
        return False
    top, height = bounds
    return top >= -.6 and top + height <= ui.view.height() + .6


def assert_selected_visible(ui, identity):
    assert ui.bridge.selected == identity
    settle(ui.app, lambda: contained(ui, identity))
    assert not ui.transport


def wheel(ui, steps):
    pos = ui.view.mapToScene(QPointF(ui.view.width() / 2, ui.view.height() / 2))
    event = QWheelEvent(pos, QPointF(ui.window.mapToGlobal(pos.toPoint())), QPoint(), QPoint(0, steps * 120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    event.setTimestamp(int(time.monotonic() * 1000))
    QTest.mouseMove(ui.window, pos.toPoint())
    ui.app.sendEvent(ui.window, event)
    QTest.qWait(20)
    settle(ui.app, lambda: not ui.view.property('moving'))


def visible_rows(ui):
    result = []
    for row in walk(ui.window.contentItem()):
        if row.objectName().startswith('cueRow_'):
            top = row.mapToItem(ui.view, QPointF()).y()
            if top < ui.view.height() and top + row.height() > 0:
                result.append((top, row.objectName().removeprefix('cueRow_')))
    return sorted(result)


@pytest.fixture(scope='module')
def application():
    QQuickStyle.setStyle('Basic')
    return QApplication.instance() or QApplication([])


@pytest.fixture
def ui(application, tmp_path, monkeypatch):
    bridge = engine = window = None
    try:
        bridge = Bridge(storage_dir=tmp_path / 'recovery')
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty('bridge', bridge)
        engine.load(QUrl.fromLocalFile(str(ROOT / 'desktop_qt/qml/Main.qml')))
        assert engine.rootObjects()
        window = engine.rootObjects()[0]
        # Loading after the empty import page settles reproduces the old bug:
        # selected r40 existed, but its viewport y was 364 in a 171px ListView.
        QTest.qWait(200)
        index = FrameIndex([n / 10 for n in range(1500)], 150.)
        bridge.media_controller.index = index
        project = bridge.document.project
        project.settings['frame_markers'] = [405]
        project.translations = [Translation('t' + str(n), '真实中文第' + str(n + 1) + '句') for n in range(140)]
        project.mappings = [Mapping('r' + str(n), ['t' + str(n)], start=n, end=n + .9) for n in range(140)]
        bridge.selected = 'r40'
        bridge.stage = 'review'
        transport = []
        monkeypatch.setattr(bridge.media_controller, 'seek', lambda *args: transport.append(('seek', args)))
        monkeypatch.setattr(bridge.media_controller, 'pause', lambda *args: transport.append(('pause', args)))
        bridge._changed()
        result = SimpleNamespace(app=application, bridge=bridge, window=window,
                                 view=find(window, 'reviewList'), transport=transport)
        yield result
    finally:
        monkeypatch.undo()
        if bridge is not None:
            bridge.shutdown()
            bridge.wait_for_shutdown(application)
            assert bridge.shutdown_complete
            assert not bridge.media_controller.worker.thread.is_alive()
            assert not bridge.jobs.live_threads()
        if window is not None:
            window.setProperty('allowClose', True)
            window.close()
        if engine is not None:
            from PySide6.QtCore import QEvent
            destroyed=[]
            engine.destroyed.connect(lambda: destroyed.append(True))
            engine.deleteLater()
            application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            from tools.qt_event_loop_wait import wait_until
            wait_until(lambda: bool(destroyed))
        application.processEvents()


def test_loaded_selection_visible_after_layout_and_resize(ui):
    assert_selected_visible(ui, 'r40')
    for width, height in [(760, 900), (1600, 1100), (1100, 800), (1400, 940)]:
        ui.window.resize(width, height)
        assert_selected_visible(ui, 'r40')
    # Cached delegates can exist outside the viewport; the regression assertion
    # above deliberately checks full geometric containment instead.
    assert len([i for i in walk(ui.window.contentItem()) if i.objectName().startswith('cueRow_')]) < 40


def test_variable_heights_selection_expansion_and_last_row(ui):
    assert_selected_visible(ui, 'r40')
    for n in range(140):
        ui.bridge.document.project.translations[n].text += '\n第二行' * (n % 3)
    ui.bridge._changed()
    for identity in ['r93', 'r138', 'r139', 'r4', 'r0', 'r40']:
        assert ui.bridge.command('timeline.select', {'id': identity})
        assert_selected_visible(ui, identity)
        for width, height in [(760, 900), (1400, 940)]:
            ui.window.resize(width, height)
            assert_selected_visible(ui, identity)


def test_edit_add_split_delete_and_history_keep_selected_cue(ui):
    assert_selected_visible(ui, 'r40')
    editor = enter_lyric_editing(ui)
    QTest.keyClick(ui.window, Qt.Key.Key_End)

    for letter in ' edited':
        QTest.keyClick(ui.window, letter)
    assert ui.bridge.state['selection']['current']['chinese'].endswith(' edited')
    assert_selected_visible(ui, 'r40')
    assert ui.bridge.command('history.undo', {})
    assert_selected_visible(ui, 'r40')
    for command, payload in [('row.add', {'after': 'r40'}),
                             ('row.split', {'id': 'r40', 'frame': 405, 'offsetUtf16': 2}),
                             ('row.delete', {'id': 'r40'})]:
        assert ui.bridge.command(command, payload), ui.bridge.state['error']
        chosen = ui.bridge.selected
        assert chosen != 'r0'
        assert_selected_visible(ui, chosen)
        assert ui.bridge.command('history.undo', {})
        assert_selected_visible(ui, 'r40')
        assert ui.bridge.command('history.redo', {})
        assert_selected_visible(ui, chosen)
        assert ui.bridge.command('history.undo', {})
        assert_selected_visible(ui, 'r40')


def test_manual_wheel_survives_updates_and_reset_until_new_selection(ui):
    assert_selected_visible(ui, 'r40')
    for _ in range(3):
        wheel(ui, -6)
    assert not contained(ui, 'r40')
    before = visible_rows(ui)
    assert before
    anchor_y, anchor_id = before[0]
    assert int(anchor_id[1:]) > 40
    for _ in range(5):
        ui.bridge.mediaChanged.emit()
        ui.bridge._refresh(False)
    assert ui.bridge.command('row.edit', {'id': 'r40', 'text': '修改当前中文但不打断手动浏览'})
    QTest.qWait(250)
    assert visible_rows(ui) == before
    # A filter reset removes earlier rows but leaves selection and the user's
    # visible anchor intact, even though both numerical indices change.
    for row in ui.bridge.document.project.mappings[:10]:
        row.confirmed = True
    ui.bridge._changed()
    assert ui.bridge.command('view.filter', {'unconfirmedOnly': True})
    settle(ui.app, lambda: geometry(ui, anchor_id) is not None and
           abs(geometry(ui, anchor_id)[0] - anchor_y) < .6)
    assert ui.bridge.selected == 'r40' and not contained(ui, 'r40')
    # An explicit same-ID selection is different from an ordinary state update.
    assert ui.bridge.command('timeline.select', {'id': 'r40'})
    assert_selected_visible(ui, 'r40')
    assert ui.bridge.command('timeline.select', {'id': 'r90'})
    assert_selected_visible(ui, 'r90')


def test_manual_scrollbar_not_overridden_by_layout(ui):
    assert_selected_visible(ui, 'r40')
    bar = find(ui.window, 'reviewScrollBar')
    pos, size = bar.property('position'), bar.property('size')
    start = bar.mapToScene(QPointF(bar.width() / 2, (pos + size / 2) * bar.height())).toPoint()
    end = bar.mapToScene(QPointF(bar.width() / 2, bar.height() * .8)).toPoint()
    QTest.mousePress(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    for n in range(1, 9):
        QTest.mouseMove(ui.window, start + (end - start) * (n / 8), 10)
    QTest.mouseRelease(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    QTest.qWait(150)
    assert not contained(ui, 'r40')
    top = visible_rows(ui)[0]
    ui.window.resize(1450, 940)
    ui.bridge._refresh(False)
    QTest.qWait(300)
    assert visible_rows(ui)[0][1] == top[1]
    assert abs(visible_rows(ui)[0][0] - top[0]) < .6
    assert ui.bridge.command('timeline.select', {'id': 'r41'})
    assert_selected_visible(ui, 'r41')


def test_composition_stays_uncommitted_then_reveals_final_height(ui):
    assert_selected_visible(ui, 'r40')
    editor = enter_lyric_editing(ui)
    before = ui.bridge.document.snapshot()
    event = QInputMethodEvent('正在输入', [])
    ui.app.sendEvent(editor, event)
    QTest.qWait(50)
    assert editor.property('inputMethodComposing')
    assert ui.bridge.document.snapshot() == before
    committed = QInputMethodEvent()
    committed.setCommitString('完整中文')
    ui.app.sendEvent(editor, committed)
    QTest.qWait(50)
    assert not editor.property('inputMethodComposing')
    assert '完整中文' in ui.bridge.state['selection']['current']['chinese']
    assert_selected_visible(ui, 'r40')


def test_viewport_corrections_converge_and_playback_ticks_do_not_relayout(ui):
    assert_selected_visible(ui, 'r40')
    panel = ui.view.parentItem().parentItem().parentItem()
    signal = panel.metaObject().method(panel.metaObject().indexOfSignal('syncingViewportChanged()'))
    changes = QSignalSpy(panel, signal)
    assert changes.isValid()
    for n in range(50):
        ui.bridge.mediaChanged.emit()
        ui.bridge._refresh(False)
        ui.app.processEvents()
    QTest.qWait(300)
    assert changes.count() == 0
    assert_selected_visible(ui, 'r40')
    ui.window.resize(760, 900)
    assert_selected_visible(ui, 'r40')
    after_resize = changes.count()
    assert 0 < after_resize < 100
    QTest.qWait(300)
    assert changes.count() == after_resize


def test_oversized_translation_preserves_scrolled_portion_while_editing(ui):
    assert_selected_visible(ui, 'r40')
    assert ui.bridge.command('row.edit', {'id': 'r40', 'text': '\n'.join('长译文第' + str(n) + '行' for n in range(90))})
    settle(ui.app, lambda: geometry(ui) is not None and geometry(ui)[1] > ui.view.height() * 3)
    # The row cannot fit. The reveal correction must converge and then allow
    # reading/editing a middle portion instead of repeatedly jumping to its head.
    for _ in range(3):
        wheel(ui, -3)
    top, height = geometry(ui)
    assert top < -100 and top + height > ui.view.height()
    editor = enter_lyric_editing(ui)
    editor.setProperty('cursorPosition', 45)
    QTest.keyClick(ui.window, Qt.Key.Key_X)
    QTest.qWait(300)
    assert ui.bridge.selected == 'r40'
    assert abs(geometry(ui)[0] - top) < .6
    assert 'x' in ui.bridge.state['selection']['current']['chinese']
    assert not ui.transport
