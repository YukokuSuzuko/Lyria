"""Explicit lyric edit entry in real Main.qml, using QTest and IME events.

Linux/offscreen event coverage does not claim Windows native IME acceptance.
"""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtTest import QTest

from test_qt_review_selection import (
    application, ui, find, walk, contained, assert_selected_visible,
    enter_lyric_editing, settle, wheel,
)

from test_qt_timeline_input import timeline_video, ui as media_ui


def point(item):
    return item.mapToScene(QPointF(min(40, item.width() / 2), item.height() / 2)).toPoint()


def click(ui, item):
    QTest.mouseClick(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(item))


def other_visible_row(ui):
    return next(item for item in walk(ui.window.contentItem())
                if item.objectName().startswith('cueRow_')
                and item.property('cueId') != ui.bridge.selected
                and contained(ui, item.property('cueId')))


def assert_not_editing(ui):
    assert not ui.window.property('textFocus')
    assert not ui.bridge._editor_active
    assert not ui.bridge._editor_composing


def test_single_click_selects_without_editing_or_changing_text(ui):
    assert_selected_visible(ui, 'r40')
    target = other_visible_row(ui)
    identity = target.property('cueId')
    editor = find(ui.window, 'chineseEditor_' + identity)
    before = ui.bridge.document.snapshot()
    click(ui, editor)
    assert ui.bridge.selected == identity
    assert editor.property('readOnly') and not editor.hasActiveFocus()
    assert_not_editing(ui)
    assert ui.bridge.document.snapshot() == before
    assert [name for name, _ in ui.transport] == ['pause', 'seek']
    QTest.keyClick(ui.window, Qt.Key.Key_X)
    assert ui.bridge.document.snapshot() == before


def test_double_click_enters_at_caret_without_duplicate_transport(ui, tmp_path):
    assert_selected_visible(ui, 'r40')
    target = other_visible_row(ui)
    identity = target.property('cueId')
    editor = find(ui.window, 'chineseEditor_' + identity)
    before = ui.bridge.document.snapshot()
    pos = point(editor)
    # QTest.mouseDClick generates the complete press/release/double-click
    # sequence. Entry must dispatch selection transport only once.
    QTest.mouseDClick(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    assert ui.bridge.selected == identity
    assert editor.hasActiveFocus() and not editor.property('readOnly')
    assert ui.window.property('textFocus') and ui.bridge._editor_active
    assert 0 <= editor.property('cursorPosition') <= editor.property('length')
    assert editor.property('cursorVisible')
    assert not target.property('heightAnimating')
    assert [name for name, _ in ui.transport] == ['pause', 'seek']
    assert ui.bridge.document.snapshot() == before
    QTest.keyClick(ui.window, Qt.Key.Key_End)
    for letter in ' edit':
        QTest.keyClick(ui.window, letter)
    assert ui.bridge.state['selection']['current']['chinese'].endswith(' edit')
    out = Path(os.environ.get('LYRIA_EXPLICIT_EDIT_EVIDENCE', str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    QTest.qWait(200)
    assert ui.window.grabWindow().save(str(out / 'double-click-editing.png'))
    (out / 'explicit-entry.json').write_text(json.dumps({
        'status': 'passed', 'selected_id': identity,
        'entry': 'QTest mouseDClick', 'transport_calls': ui.transport,
        'active_text_caret': True, 'windows_native_ime_verified': False,
    }, ensure_ascii=False, indent=2))


def test_f2_edits_current_row_after_manual_scroll_and_filter_reindex(ui):
    assert_selected_visible(ui, 'r40')
    for row in ui.bridge.document.project.mappings[:20]:
        row.confirmed = True
    ui.bridge._changed()
    assert ui.bridge.command('view.filter', {'unconfirmedOnly': True})
    assert ui.bridge.selected == 'r40' and ui.bridge.state['selectedVisibleIndex'] == 20
    for _ in range(3):
        wheel(ui, -6)
    assert not contained(ui, 'r40')
    before = ui.bridge.document.snapshot()
    editor = enter_lyric_editing(ui)
    assert editor.objectName() == 'chineseEditor_r40'
    assert_selected_visible(ui, 'r40')
    assert ui.bridge.document.snapshot() == before
    QTest.keyClick(ui.window, Qt.Key.Key_End)
    QTest.keyClick(ui.window, Qt.Key.Key_X)
    assert ui.bridge.state['selection']['current']['chinese'].endswith('x')
    assert not ui.transport


def test_f2_never_edits_first_visible_row_when_selection_is_filtered_out(ui):
    assert_selected_visible(ui, 'r40')
    assert ui.bridge.command('row.confirm', {'id': 'r40', 'value': True})
    assert ui.bridge.command('view.filter', {'unconfirmedOnly': True})
    assert ui.bridge.command('timeline.select', {'id': 'r40'})
    assert ui.bridge.state['selectedVisibleIndex'] == -1
    before = ui.bridge.document.snapshot()
    QTest.keyClick(ui.window, Qt.Key.Key_F2)
    assert ui.bridge.selected == 'r40'
    assert ui.bridge.document.snapshot() == before
    assert_not_editing(ui)
    assert '筛选隐藏' in ui.window.property('noticeText')
    assert not ui.transport


def test_locked_row_refuses_f2_and_double_click_without_unlocking(ui):
    assert_selected_visible(ui, 'r40')
    assert ui.bridge.command('row.lock', {'id': 'r40', 'value': True})
    before = ui.bridge.document.snapshot()
    QTest.keyClick(ui.window, Qt.Key.Key_F2)
    editor = find(ui.window, 'chineseEditor_r40')
    QTest.mouseDClick(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(editor))
    assert editor.property('readOnly') and not editor.hasActiveFocus()
    assert_not_editing(ui)
    assert '先解锁后再编辑' in ui.window.property('noticeText')
    assert ui.bridge.document.snapshot() == before


def test_editing_owns_text_keys_then_escape_restores_playback_and_frames(media_ui, monkeypatch):
    app, bridge, window, timeline = media_ui
    ui = SimpleNamespace(app=app, bridge=bridge, window=window,
                         view=find(window, 'reviewList'), transport=[])
    assert ui.bridge.media['ready']
    assert_selected_visible(ui, 'r40')
    monkeypatch.setattr(ui.bridge.media_controller, 'play', lambda: ui.transport.append(('play', ())))
    monkeypatch.setattr(ui.bridge.media_controller, 'step', lambda delta: ui.transport.append(('step', (delta,))))
    timeline = find(ui.window, 'frameTimeline')
    editor = enter_lyric_editing(ui)
    QTest.keyClick(ui.window, Qt.Key.Key_End)
    QTest.keyClick(ui.window, Qt.Key.Key_Space)
    for key in (Qt.Key.Key_H, Qt.Key.Key_C, Qt.Key.Key_V, Qt.Key.Key_M):
        QTest.keyClick(ui.window, key)
    text = editor.property('text')
    QTest.keyClick(ui.window, Qt.Key.Key_Left)
    assert editor.property('cursorPosition') == editor.property('length') - 1
    assert timeline.property('tool') == 'select'
    assert ui.bridge.state['selection']['current']['chinese'] == text
    assert text.endswith(' hcvm') and not ui.transport
    QTest.keyClick(ui.window, Qt.Key.Key_Escape)
    assert editor.property('readOnly') and not editor.hasActiveFocus()
    assert_not_editing(ui)
    QTest.keyClick(ui.window, Qt.Key.Key_Space)
    QTest.keyClick(ui.window, Qt.Key.Key_Left)
    QTest.keyClick(ui.window, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert ui.transport == [('play', ()), ('step', (-1,)), ('step', (5,))]
    QTest.keyClick(ui.window, Qt.Key.Key_H)
    assert timeline.property('tool') == 'hand'
    QTest.keyClick(ui.window, Qt.Key.Key_C)
    assert timeline.property('tool') == 'razor'
    QTest.keyClick(ui.window, Qt.Key.Key_V)
    assert timeline.property('tool') == 'select'


def test_focus_exit_and_row_change_release_editor_without_selection_reset(ui):
    assert_selected_visible(ui, 'r40')
    editor = enter_lyric_editing(ui)
    QTest.keyClick(ui.window, Qt.Key.Key_End)
    QTest.keyClick(ui.window, Qt.Key.Key_X)
    click(ui, find(ui.window, 'timelineSelectTool'))
    assert_not_editing(ui)
    assert editor.property('readOnly')
    assert ui.bridge.selected == 'r40'
    assert ui.bridge.state['selection']['current']['chinese'].endswith('x')
    QTest.keyClick(ui.window, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    assert ui.bridge.selected == 'r41'
    assert_not_editing(ui)
    assert [name for name, _ in ui.transport] == ['pause', 'seek']


def test_enter_inserts_newline_and_ctrl_enter_keeps_confirm_next(ui):
    assert_selected_visible(ui, 'r40')
    before = ui.bridge.document.snapshot()
    QTest.keyClick(ui.window, Qt.Key.Key_Return)
    assert_not_editing(ui)
    assert ui.bridge.document.snapshot() == before
    editor = enter_lyric_editing(ui)
    QTest.keyClick(ui.window, Qt.Key.Key_End)
    QTest.keyClick(ui.window, Qt.Key.Key_Return)
    QTest.keyClick(ui.window, Qt.Key.Key_X)
    assert ui.bridge.state['selection']['current']['chinese'].endswith('\nx')
    assert not ui.bridge.state['selection']['current']['confirmed']
    assert not ui.transport
    QTest.keyClick(ui.window, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert ui.bridge.selected == 'r41'
    assert next(row for row in ui.bridge.state['rows'] if row['cueId'] == 'r40')['confirmed']
    assert_not_editing(ui)
    assert [name for name, _ in ui.transport] == ['pause', 'seek']
    # Outside text entry, the original confirmation shortcut remains live.
    QTest.keyClick(ui.window, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert ui.bridge.selected == 'r42'


def test_preedit_is_not_saved_and_navigation_does_not_steal_ime_focus(ui, tmp_path):
    assert_selected_visible(ui, 'r40')
    editor = enter_lyric_editing(ui)
    before = ui.bridge.document.snapshot()
    ui.app.sendEvent(editor, QInputMethodEvent('组合中\n第二行', []))
    assert editor.property('inputMethodComposing') and ui.bridge._editor_composing
    assert ui.bridge.document.snapshot() == before
    row = find(ui.window, 'cueRow_r40')
    assert not row.property('heightAnimating')
    QTest.keyClick(ui.window, Qt.Key.Key_F2)
    QTest.keyClick(ui.window, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    assert ui.bridge.selected == 'r40' and editor.hasActiveFocus()
    assert ui.bridge.document.snapshot() == before and not ui.transport
    out = Path(os.environ.get('LYRIA_EXPLICIT_EDIT_EVIDENCE', str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    assert ui.window.grabWindow().save(str(out / 'ime-preedit-not-committed.png'))
    committed = QInputMethodEvent()
    committed.setCommitString('中文提交\n下一行')
    ui.app.sendEvent(editor, committed)
    assert not editor.property('inputMethodComposing') and not ui.bridge._editor_composing
    assert '中文提交\n下一行' in ui.bridge.state['selection']['current']['chinese']
    assert '组合中' not in ui.bridge.state['selection']['current']['chinese']
    (out / 'ime-editing.json').write_text(json.dumps({
        'status': 'passed', 'synthetic_qinputmethodevent': True,
        'preedit_not_saved': True, 'commit_saved': True,
        'f2_alt_down_preserve_focus_and_selection': True,
        'windows_native_ime_verified': False,
    }, ensure_ascii=False, indent=2))
    QTest.keyClick(ui.window, Qt.Key.Key_Escape)
    assert_not_editing(ui)


def test_f2_does_not_steal_other_text_field_or_hidden_workspace(ui):
    assert_selected_visible(ui, 'r40')
    field = find(ui.window, 'firstFrameField')
    click(ui, field)
    assert field.hasActiveFocus()
    QTest.keyClick(ui.window, Qt.Key.Key_F2)
    assert field.hasActiveFocus()
    assert find(ui.window, 'chineseEditor_r40').property('readOnly')
    ui.window.setProperty('workspace', 'qc')
    QTest.keyClick(ui.window, Qt.Key.Key_F2)
    assert not find(ui.window, 'chineseEditor_r40').hasActiveFocus()
    ui.window.setProperty('workspace', 'make')
    assert ui.bridge.command('stage.set', {'stage': 'import'})
    QTest.keyClick(ui.window, Qt.Key.Key_F2)
    assert not find(ui.window, 'chineseEditor_r40').hasActiveFocus()


def test_saved_caret_menu_split_survives_focus_exit_but_rejects_stale_text(ui):
    assert_selected_visible(ui, 'r40')
    editor = enter_lyric_editing(ui)
    editor.setProperty('cursorPosition', 2)
    before_count = len(ui.bridge.document.project.mappings)
    original_text = ui.bridge.state['selection']['current']['chinese']
    click(ui, find(ui.window, 'reviewOperationsButton'))
    QTest.qWait(200)
    assert editor.property('readOnly') and not editor.hasActiveFocus()
    click(ui, find(ui.window, 'splitAtSavedCursorAction'))
    assert len(ui.bridge.document.project.mappings) == before_count + 1
    rows = ui.bridge.state['rows']
    assert rows[40]['chinese'] + rows[41]['chinese'] == original_text
    assert rows[40]['lastFrame'] == 404 and rows[41]['firstFrame'] == 405
    assert ui.bridge.document.project.settings['frame_markers'] == [405]
    assert not ui.transport
    QTest.qWait(200)
    assert ui.bridge.command('history.undo', {})
    assert ui.bridge.selected == 'r40'
    assert ui.bridge.command('row.edit', {'id': 'r40', 'text': '更改后的译文，旧光标不能被复用'})
    before = ui.bridge.document.snapshot()
    click(ui, find(ui.window, 'reviewOperationsButton'))
    QTest.qWait(200)
    click(ui, find(ui.window, 'splitAtSavedCursorAction'))
    assert ui.bridge.document.snapshot() == before
    assert '将光标移到当前句' in ui.window.property('noticeText')


@pytest.mark.parametrize('height,at_bottom', [(900, True), (900, False), (600, True), (600, False)])
def test_f2_reveals_editor_caret_in_narrow_scrolled_workspace(ui, height, at_bottom, tmp_path):
    ui.window.resize(760, height)
    assert_selected_visible(ui, 'r40')
    assert find(ui.window, 'makeScroll').property('contentItem') is None
    viewport = find(ui.window, 'reviewList')
    from test_qt_timeline_input import wheel
    from tools.qt_event_loop_wait import wait_until
    wheel(ui.app, ui.window, viewport, -120 if at_bottom else 120)
    wait_until(lambda: not find(ui.window, 'reviewPanel').property('followSelection'))
    viewport.setProperty('contentY', viewport.property('contentHeight') - viewport.height() if at_bottom else 0)
    QTest.qWait(100)
    before = ui.bridge.document.snapshot()
    editor = enter_lyric_editing(ui)
    assert ui.bridge.document.snapshot() == before
    caret = editor.property('cursorRectangle')
    location = editor.mapToItem(viewport, QPointF(caret.x(), caret.y()))
    assert location.y() >= 0
    assert location.y() + caret.height() <= viewport.height()
    assert ui.bridge.selected == 'r40' and not ui.transport
    out = Path(os.environ.get('LYRIA_EXPLICIT_EDIT_EVIDENCE', str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    assert ui.window.grabWindow().save(str(out / f'f2-narrow-760x{height}-{ "bottom" if at_bottom else "top" }.png'))


def test_clicking_another_row_exits_editing_and_double_click_can_reenter(ui):
    assert_selected_visible(ui, 'r40')
    original = enter_lyric_editing(ui)
    QTest.keyClick(ui.window, Qt.Key.Key_End)
    QTest.keyClick(ui.window, Qt.Key.Key_X)
    target = other_visible_row(ui)
    identity = target.property('cueId')
    target_editor = find(ui.window, 'chineseEditor_' + identity)
    click(ui, target_editor)
    assert ui.bridge.selected == identity
    assert original.property('readOnly') and target_editor.property('readOnly')
    assert_not_editing(ui)
    assert [name for name, _ in ui.transport] == ['pause', 'seek']
    ui.transport.clear()
    QTest.mouseDClick(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(target_editor))
    assert target_editor.hasActiveFocus() and not target_editor.property('readOnly')
    assert original.property('readOnly') and not original.hasActiveFocus()
    assert not ui.transport, 'Editing the already selected lyric must not seek again'
    QTest.keyClick(ui.window, Qt.Key.Key_Escape)
    assert_not_editing(ui)
    assert ui.bridge.selected == identity
    assert next(row for row in ui.bridge.state['rows'] if row['cueId'] == 'r40')['chinese'].endswith('x')


def test_escape_and_ctrl_enter_do_not_save_or_confirm_ime_preedit(ui):
    assert_selected_visible(ui, 'r40')
    editor = enter_lyric_editing(ui)
    before = ui.bridge.document.snapshot()
    ui.app.sendEvent(editor, QInputMethodEvent('未完成候选', []))
    assert editor.property('inputMethodComposing')
    QTest.keyClick(ui.window, Qt.Key.Key_Escape)
    assert editor.hasActiveFocus()
    assert ui.bridge.document.snapshot() == before
    QTest.keyClick(ui.window, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert ui.bridge.selected == 'r40'
    assert ui.bridge.document.snapshot() == before
    assert not ui.transport
    # Cancel preedit through Qt, then Escape is an ordinary edit-mode exit.
    ui.app.sendEvent(editor, QInputMethodEvent())
    assert not editor.property('inputMethodComposing')
    QTest.keyClick(ui.window, Qt.Key.Key_Escape)
    assert_not_editing(ui)
