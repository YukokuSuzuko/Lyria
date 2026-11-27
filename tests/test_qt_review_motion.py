"""Real review delegate animation and interruption, without transport side effects."""
import json
import os
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtTest import QTest

# Reuse the actual Main.qml/140-row viewport fixture, not a mock QML component.
from test_qt_review_selection import (application, ui, find, walk, geometry,
                                      assert_selected_visible, contained, enter_lyric_editing)
from tools.qt_event_loop_wait import delay, wait_until


def row_snapshot(row):
    return {'height': row.height(), 'target': row.property('targetRowHeight'),
            'animating': row.property('heightAnimating')}


def test_real_selection_expansion_interrupt_and_off(ui, tmp_path):
    assert_selected_visible(ui, 'r40')
    old = find(ui.window, 'cueRow_r40')
    # Click a fully visible neighboring row, preserving the existing selection
    # and editing policy. Choose from geometry, never a cached offscreen item.
    candidates = [row for row in walk(ui.window.contentItem())
                  if row.objectName().startswith('cueRow_') and row != old
                  and contained(ui, row.property('cueId'))]
    assert candidates
    target = candidates[0]
    target_id = target.property('cueId')
    old_height, collapsed_height = old.height(), target.height()
    before = ui.bridge.document.snapshot()
    samples = []
    target.heightChanged.connect(lambda: samples.append(row_snapshot(target)))
    pos = target.mapToScene(QPointF(5, target.height() / 2)).toPoint()
    QTest.mouseClick(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    assert ui.bridge.selected == target_id
    # Existing row.select intentionally pauses/seeks once. Do not change that
    # policy: only animation-generated additional transport would be a bug.
    click_transport = list(ui.transport)
    assert [name for name, args in click_transport] == ['pause', 'seek']
    ui.transport.clear()
    # v0.22 compact rows stay equal-height on selection. Selection color still
    # animates; real text wrapping retains the independent height animation.
    assert not target.property('heightAnimating')
    assert target.height() == collapsed_height == target.property('targetRowHeight')
    assert old.height() == old_height
    initial_color=target.property('color')
    delay(40)
    assert target.property('color') != initial_color
    assert target.height() == collapsed_height
    # Reverse selection while the selection-color transition is in flight.
    assert ui.bridge.command('timeline.select', {'id': 'r40'})
    delay(20)
    assert ui.bridge.command('settings.set', {'key': 'motion', 'value': False})
    for row in (old, target):
        assert row.height() == row.property('targetRowHeight')
        assert not row.property('heightAnimating')
    assert old.height() == old_height and target.height() == collapsed_height
    assert_selected_visible(ui, 'r40')
    # The UI setting is the only deliberate persisted change. Selection/motion
    # does not write lyric text/timing or invoke playback/seek.
    assert ui.bridge.document.snapshot()['project']['translations'] == before['project']['translations']
    assert ui.bridge.document.snapshot()['project']['mappings'] == before['project']['mappings']
    assert not ui.transport
    assert ui.bridge.command('settings.set', {'key': 'motion', 'value': True})
    for identity in [target_id, 'r90', 'r139', 'r40']:
        assert ui.bridge.command('timeline.select', {'id': identity})
        assert_selected_visible(ui, identity)
    out = Path(os.environ.get('LYRIA_REVIEW_MOTION_EVIDENCE', str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    assert ui.window.grabWindow().save(str(out / 'review-motion-wide.png'))
    ui.window.resize(760, 900)
    assert_selected_visible(ui, 'r40')
    assert ui.window.grabWindow().save(str(out / 'review-motion-narrow.png'))
    (out / 'review-height-motion.json').write_text(json.dumps({'status': 'passed',
        'qtest_selected': target_id, 'samples': samples,
        'reversal_settles_immediately_when_motion_disabled': True,
        'intended_row_click_transport': click_transport,
        'additional_animation_transport': ui.transport, 'windows_acceptance': False}, indent=2))


def test_multiline_preedit_and_status_colors_settle(ui):
    assert_selected_visible(ui, 'r40')
    row = find(ui.window, 'cueRow_r40')
    editor = find(ui.window, 'chineseEditor_r40')
    badge = find(ui.window, 'cueStatus_r40')
    color_before = badge.property('color')
    assert ui.bridge.command('row.confirm', {'id': 'r40', 'value': True})
    assert badge.property('color') != badge.property('fill')
    delay(30)
    assert badge.property('color') != color_before
    assert badge.property('color') != badge.property('fill')
    assert ui.bridge.command('settings.set', {'key': 'motion', 'value': False})
    assert badge.property('color') == badge.property('fill')
    assert ui.bridge.command('row.confirm', {'id': 'r40', 'value': False})
    assert badge.property('color') == badge.property('fill')
    assert ui.bridge.command('settings.set', {'key': 'motion', 'value': True})
    original_height = row.height()
    assert ui.bridge.command('row.edit', {'id': 'r40', 'text': '第一行\n第二行\n第三行\n第四行'})
    assert row.property('heightAnimating')
    assert row.height() < row.property('targetRowHeight')
    wait_until(lambda: not row.property('heightAnimating'))
    assert row.height() > original_height
    assert_selected_visible(ui, 'r40')
    editor = enter_lyric_editing(ui)
    before = ui.bridge.document.snapshot()
    event = QInputMethodEvent('未提交\n输入法\n组合内容', [])
    ui.app.sendEvent(editor, event)
    assert editor.property('inputMethodComposing')
    assert row.height() == row.property('targetRowHeight')
    assert not row.property('heightAnimating')
    assert ui.bridge.document.snapshot() == before
    commit = QInputMethodEvent()
    commit.setCommitString('完整中文')
    ui.app.sendEvent(editor, commit)
    wait_until(lambda: not row.property('heightAnimating'))
    assert not editor.property('inputMethodComposing')
    assert '完整中文' in ui.bridge.state['selection']['current']['chinese']
    assert_selected_visible(ui, 'r40')
    assert not ui.transport


def test_explicit_auto_reveal_tweens_and_yields_to_manual_input(ui, tmp_path):
    import time
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtCore import QPoint

    assert_selected_visible(ui, 'r40')
    panel = ui.view.parentItem().parentItem().parentItem()
    start = ui.view.property('contentY')
    began = time.monotonic()
    samples = []
    ui.view.contentYChanged.connect(lambda: samples.append((time.monotonic() - began,
                                                          ui.view.property('contentY'))))
    assert ui.bridge.command('timeline.select', {'id': 'r90'})
    wait_until(lambda: panel.property('autoScrollRunning'))
    delay(40)
    middle = ui.view.property('contentY')
    assert middle > start
    assert panel.property('autoScrollRunning')
    wait_until(lambda: not panel.property('autoScrollRunning') and contained(ui, 'r90'))
    final = ui.view.property('contentY')
    assert start < middle < final
    assert len({round(value, 2) for _, value in samples}) >= 5
    assert samples[-1][0] >= .12
    # This guards the old instant positionViewAtIndex jump followed by a pause.
    jumps = [abs(b[1] - a[1]) for a, b in zip([(0, start)] + samples, samples)]
    assert max(jumps) < (final - start) * .7
    initial_samples = list(samples)

    # Newer selection replaces the old destination instead of queueing it.
    assert ui.bridge.command('timeline.select', {'id': 'r10'})
    wait_until(lambda: panel.property('autoScrollRunning'))
    delay(25)
    assert ui.bridge.command('timeline.select', {'id': 'r110'})
    wait_until(lambda: panel.property('autoScrollRunning'))
    delay(25)
    assert ui.bridge.command('settings.set', {'key': 'motion', 'value': False})
    assert not panel.property('autoScrollRunning')
    assert_selected_visible(ui, 'r110')
    assert ui.bridge.command('settings.set', {'key': 'motion', 'value': True})

    assert ui.bridge.command('timeline.select', {'id': 'r20'})
    wait_until(lambda: panel.property('autoScrollRunning'))
    delay(30)
    pos = ui.view.mapToScene(QPointF(ui.view.width() / 2, ui.view.height() / 2))
    event = QWheelEvent(pos, QPointF(ui.window.mapToGlobal(pos.toPoint())), QPoint(), QPoint(0, -120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    ui.app.sendEvent(ui.window, event)
    assert not panel.property('autoScrollRunning')
    assert not panel.property('followSelection')
    wait_until(lambda: not ui.view.property('moving'))
    delay(40)
    manual_y = ui.view.property('contentY')
    ui.bridge._refresh(False)
    delay(200)
    assert ui.view.property('contentY') == pytest.approx(manual_y, abs=.6)
    assert not contained(ui, 'r20'), 'Manual input must win over an unfinished reveal'
    assert ui.bridge.command('timeline.select', {'id': 'r20'})
    assert_selected_visible(ui, 'r20')
    assert not ui.transport
    out = Path(os.environ.get('LYRIA_REVIEW_MOTION_EVIDENCE', str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    (out / 'review-auto-scroll.json').write_text(json.dumps({'status': 'passed',
        'start': start, 'middle': middle, 'final': final, 'samples_seconds_content_y': initial_samples,
        'manual_wheel_cancels_reveal': True, 'new_selection_replaces_destination': True,
        'motion_off_stops_immediately': True, 'no_transport_calls': True}, indent=2))


def test_auto_reveal_resize_and_editor_caret_interrupt(ui):
    assert_selected_visible(ui, 'r40')
    panel = ui.view.parentItem().parentItem().parentItem()
    assert ui.bridge.command('timeline.select', {'id': 'r100'})
    wait_until(lambda: panel.property('autoScrollRunning'))
    delay(25)
    ui.window.resize(760, 900)
    assert_selected_visible(ui, 'r100')
    assert ui.bridge.command('timeline.select', {'id': 'r20'})
    wait_until(lambda: panel.property('autoScrollRunning'))
    delay(25)
    # A current delegate exists even outside the visible cache. Explicit editor
    # focus/caret input settles the reveal so the input never chases a moving row.
    editor = find(ui.window, 'chineseEditor_r20')
    assert editor is not None
    editor = enter_lyric_editing(ui)
    editor.setProperty('cursorPosition', 2)
    assert not panel.property('autoScrollRunning')
    assert_selected_visible(ui, 'r20')
    before = ui.bridge.document.snapshot()
    ui.app.sendEvent(editor, QInputMethodEvent('组合输入', []))
    assert editor.property('inputMethodComposing')
    assert not panel.property('autoScrollRunning')
    assert ui.bridge.document.snapshot() == before
    ui.app.sendEvent(editor, QInputMethodEvent())
    assert not ui.transport
