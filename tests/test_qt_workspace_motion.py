"""Actual Main.qml motion, input ownership and menu interaction.

Uses the real local media worker and QTest events. Software/offscreen evidence is
not Windows GPU, native titlebar, audio-sync or visual smoothness acceptance.
"""
import hashlib
import json
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')

import pytest
from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop_qt.controller import Bridge
from lyricsync.model import Mapping, Translation
from tools.qt_event_loop_wait import delay, wait_until

ROOT = Path(__file__).resolve().parents[1]


def find(window, name):
    obj = window.findChild(QObject, name)
    if obj is None:
        def walk(item):
            if item.objectName() == name:
                return item
            for child in item.childItems():
                result = walk(child)
                if result is not None:
                    return result
            return None
        obj = walk(window.contentItem())
    assert obj is not None, name
    return obj


def click(window, item):
    assert item.isVisible() and item.isEnabled(), item.objectName()
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)


def settled(surface):
    return (not surface.property('animating') and surface.property('opacity') == 1
            and surface.property('entryOffset') == 0)


def menu_values(menu):
    return {key: menu.property(key) for key in
            ('visible', 'enabled', 'opacity', 'scale', 'closing', 'animating')}


@pytest.fixture(scope='module')
def application():
    QQuickStyle.setStyle('Basic')
    return QApplication.instance() or QApplication([])


@pytest.fixture
def ui(application, tmp_path, monkeypatch):
    out = Path(os.environ.get('LYRIA_MOTION_EVIDENCE', str(tmp_path / 'motion-evidence')))
    out.mkdir(parents=True, exist_ok=True)
    bridge = Bridge(storage_dir=tmp_path / 'recovery')
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(str(item) for item in items))
    engine.rootContext().setContextProperty('bridge', bridge)
    engine.load(QUrl.fromLocalFile(str(ROOT / 'desktop_qt/qml/Main.qml')))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    window.requestActivate()
    wait_until(lambda: settled(find(window, 'makeWorkspaceMotion')), timeout_ms=5000)
    delay(30)
    # Any accidental motion -> OCR coupling is a failure, never an API request.
    def forbidden_ocr():
        raise AssertionError('UI motion must never start OCR')
    monkeypatch.setattr(bridge, '_run_ocr', forbidden_ocr)
    yield bridge, window, out
    window.setProperty('allowClose', True)
    window.close()
    bridge.shutdown()
    engine.deleteLater()
    application.processEvents()
    assert not warnings, warnings


def enter_hidden_qc_for_internal_test(window):
    # F37 keeps QC implementation but removes the production entry.
    assert not find(window,'qcWorkspaceButton').isVisible()
    window.setProperty('workspace','qc')


def enter_settings_menu(window):
    click(window, find(window, 'settingsMenuButton'))
    wait_until(lambda: find(window, 'settingsMenu').property('opened'))
    click(window, find(window, 'settingsMenuAction'))


def return_from_hidden_workspace(window):
    # QC has no production switch. Preserve internal motion/focus coverage
    # without pretending the removed switch is available to the user.
    assert not find(window, 'makeWorkspaceButton').isVisible()
    window.setProperty('workspace', 'make')


def test_workspace_entry_interruption_and_motion_off(ui):
    bridge, window, out = ui
    make = find(window, 'makeWorkspaceMotion')
    qc = find(window, 'qcWorkspaceMotion')
    settings = find(window, 'settingsWorkspaceMotion')
    export = find(window, 'exportWorkspaceMotion')
    assert settled(make)
    samples = []
    for button, surface in [('qcWorkspaceButton', qc), ('settingsWorkspaceButton', settings),
                            ('makeWorkspaceButton', make), ('qcWorkspaceButton', qc)]:
        if button=='qcWorkspaceButton':enter_hidden_qc_for_internal_test(window)
        elif button=='settingsWorkspaceButton':enter_settings_menu(window)
        else:return_from_hidden_workspace(window)
        samples.append({'target': surface.objectName(), 'opacity': surface.opacity(),
                        'offset': surface.property('entryOffset')})
        assert surface.isVisible() and surface.isEnabled()
        assert surface.property('animating')
        assert 0 <= surface.opacity() < 1
        assert 0 < surface.property('entryOffset') <= 6
        # Layout coordinates stay anchored; the 6px movement is a Translate.
        assert surface.y() == 0 and surface.height() == find(window, 'workspaceArea').height()
        for other in (make, qc, settings, export):
            if other != surface:
                assert not other.isVisible() and not other.isEnabled()
                assert settled(other)
        delay(25)
    assert bridge.command('settings.set', {'key': 'motion', 'value': False})
    assert settled(qc), 'Disabling motion must synchronously finish a page entry'
    highlight = find(window, 'workspaceSwitchHighlight')
    qc_x = highlight.x()
    return_from_hidden_workspace(window)
    assert settled(make)
    assert not highlight.isVisible() and highlight.width()>0, 'The entire mode-switch group stays hidden'
    # Target bindings must survive disable/enable, not freeze at a previous tab.
    enter_hidden_qc_for_internal_test(window)
    assert settled(qc) and highlight.x() == qc_x
    assert bridge.command('settings.set', {'key': 'motion', 'value': True})
    return_from_hidden_workspace(window)
    delay(20)
    assert bridge.command('stage.set', {'stage': 'review'})
    assert make.property('animating') and make.property('entryOffset') > 0
    delay(20)
    click(window, find(window, 'exportStageButton'))
    assert export.property('animating') and not make.isEnabled()
    wait_until(lambda: settled(export))
    window.grabWindow().save(str(out / 'motion-export-wide.png'))
    for width, height in [(760, 600), (920, 900), (1400, 940)]:
        window.resize(width, height)
        enter_hidden_qc_for_internal_test(window)
        delay(20)
        enter_settings_menu(window)
        wait_until(lambda: settled(settings))
        assert settings.width() == find(window, 'workspaceArea').width()
        assert not find(window, 'qcWorkspaceButton').isVisible()
        for name in ('fileMenuButton','editMenuButton','subtitleMenuButton','viewMenuButton','toolsMenuButton','settingsMenuButton','helpMenuButton'):
            item=find(window,name); x=item.mapToScene(QPointF(0,0)).x()
            assert item.isVisible() and 0 <= x and x+item.width() <= window.width()
        assert window.grabWindow().save(str(out / f'motion-settings-{width}x{height}.png'))
    (out / 'workspace-entry.json').write_text(json.dumps({'status': 'passed', 'samples': samples,
        'qml_sha256': hashlib.sha256((ROOT / 'desktop_qt/qml/Main.qml').read_bytes()).hexdigest(),
        'actual_qtest_visible_navigation': True, 'qc_internal_only_hidden_entry': True, 'layout_geometry_fixed': True,
        'off_switch_synchronous': True, 'native_windows_acceptance': False}, indent=2))


def test_menu_enter_exit_escape_outside_and_reduced_motion(ui):
    bridge, window, out = ui
    menu = find(window, 'fileMenu')
    samples = []
    for n in range(3):
        click(window, find(window, 'fileMenuButton'))
        assert menu.property('visible') and not menu.property('closing')
        assert 0 <= menu.property('opacity') < 1 and .94 <= menu.property('scale') < 1
        samples.append(menu_values(menu))
        # Escape works during entry; exit rejects input immediately.
        QTest.keyClick(window, Qt.Key.Key_Escape)
        assert menu.property('closing') and not menu.property('enabled')
        delay(20)
        samples.append(menu_values(menu))
        if n == 0:
            click(window, find(window, 'fileMenuButton'))
            assert menu.property('reopenPending') or not menu.property('closing')
            wait_until(lambda: menu.property('visible') and not menu.property('closing'))
            assert menu.property('enabled')
            QTest.keyClick(window, Qt.Key.Key_Escape)
        if n == 1:
            bridge.command('settings.set', {'key': 'motion', 'value': False})
            assert menu.property('opacity') == 0 and not menu.property('animating')
        wait_until(lambda: not menu.property('visible'))
        bridge.command('settings.set', {'key': 'motion', 'value': True})
    # A second Escape cancels a queued reopen during the exit transition.
    click(window, find(window, 'fileMenuButton'))
    QTest.keyClick(window, Qt.Key.Key_Escape)
    delay(20)
    click(window, find(window, 'fileMenuButton'))
    assert menu.property('reopenPending')
    QTest.keyClick(window, Qt.Key.Key_Escape)
    assert not menu.property('reopenPending')
    wait_until(lambda: not menu.property('visible'))
    delay(150)
    assert not menu.property('visible')
    click(window, find(window, 'fileMenuButton'))
    delay(15)
    bridge.command('settings.set', {'key': 'motion', 'value': False})
    assert menu.property('opacity') == 1 and menu.property('scale') == 1
    assert not menu.property('animating') and menu.property('enabled')
    # Immediate visual settling must not leave an overlay that owns outside input.
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPointF(window.width() - 25, window.height() - 50).toPoint())
    wait_until(lambda: not menu.property('visible'))
    tools = find(window, 'settingsMenu')
    click(window, find(window, 'settingsMenuButton'))
    wait_until(lambda: tools.property('visible') and tools.property('opacity') == 1)
    click(window, find(window, 'settingsMenuAction'))
    assert window.property('workspace') == 'settings'
    wait_until(lambda: not tools.property('visible'))
    bridge.command('settings.set', {'key': 'motion', 'value': True})
    click(window, find(window, 'fileMenuButton'))
    wait_until(lambda: menu.property('opened'))
    assert window.grabWindow().save(str(out / 'motion-file-menu.png'))
    QTest.keyClick(window, Qt.Key.Key_Escape)
    wait_until(lambda: not menu.property('visible'))
    (out / 'menu-motion.json').write_text(json.dumps({'status': 'passed', 'samples': samples,
        'actual_escape_and_outside_dismissal': True, 'actual_settings_route': True,
        'off_switch_visually_settled_immediately': True,
        'popup_lifecycle_owned_by_qt': True}, indent=2))


def test_completion_button_toast_hidden_focus_and_decode_invariants(ui):
    bridge, window, out = ui
    assert bridge.command('media.open', {'path': str(ROOT / 'examples/synthetic_mv.mp4')})
    wait_until(lambda: bridge.media['ready'] and bridge.media['displayedFrame'] == 1, timeout_ms=20000)
    project = bridge.document.project
    index = bridge.media_controller.index
    project.translations = [Translation('motion-t1', '动效验收夹具第一句'),
                            Translation('motion-t2', '动效验收夹具第二句')]
    project.mappings = [Mapping('motion-c1', ['motion-t1'], start=index.interval(10, 30)[0], end=index.interval(10, 30)[1]),
                        Mapping('motion-c2', ['motion-t2'], start=index.interval(40, 60)[0], end=index.interval(40, 60)[1])]
    bridge.selected = 'motion-c1'
    bridge.stage = 'review'
    bridge._changed()
    delay(300)
    assert find(window, 'stageButton_import').property('completed')
    assert find(window, 'stageButton_import').property('text') == '导入 / 画面识别'
    assert window.findChild(QObject, 'stageButton_ocr') is None
    assert not bridge.state['stageCompletion']['ocr'], 'Manual rows are not OCR evidence'
    assert not bridge.document.source_candidates and not project.visuals
    review_step = find(window, 'stageButton_review')
    check = find(window, 'stageButton_reviewCheck')
    assert not review_step.property('completed') and check.opacity() == 0
    before = {key: bridge.media[key] for key in ('decoderSeeks', 'decodedFrames', 'displayedFrame')}
    revision = bridge.document.revision
    editor = find(window, 'chineseEditor_motion-c1')
    click(window, editor)
    assert not editor.hasActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_F2)
    assert editor.hasActiveFocus() and not editor.property('readOnly')
    enter_hidden_qc_for_internal_test(window)
    assert not editor.hasActiveFocus() and not editor.isVisible() and not editor.isEnabled()
    QTest.keyClick(window, Qt.Key.Key_Z)
    QTest.keyClick(window, Qt.Key.Key_Z)
    assert project.translations[0].text == '动效验收夹具第一句'
    assert bridge.document.revision == revision
    return_from_hidden_workspace(window)
    delay(200)
    click(window, find(window, 'reviewOperationsButton'))
    operations = find(window, 'reviewOperationsMenu')
    assert operations.property('visible')
    # Programmatic navigation while a menu is open must dismiss its hidden owner.
    window.setProperty('workspace', 'qc')
    assert operations.property('closing') and not operations.property('enabled')
    wait_until(lambda: not operations.property('visible'))
    return_from_hidden_workspace(window)
    wait_until(lambda: settled(find(window, 'makeWorkspaceMotion')))
    click(window, find(window, 'reviewAddBlankButton'))
    add_menu = find(window, 'reviewAddBlankMenu')
    assert add_menu.property('visible')
    window.setProperty('workspace', 'qc')
    wait_until(lambda: not add_menu.property('visible'))
    return_from_hidden_workspace(window)
    wait_until(lambda: settled(find(window, 'makeWorkspaceMotion')))
    assert bridge.command('row.confirm', {'id': 'motion-c1', 'value': True})
    delay(240)
    assert bridge.command('row.confirm', {'id': 'motion-c2', 'value': True})
    assert review_step.property('completed')
    assert check.opacity() < 1 and check.scale() < 1
    delay(30)
    bridge.command('settings.set', {'key': 'motion', 'value': False})
    assert check.opacity() == 1 and check.scale() == 1
    fill = find(window, 'confirmationProgressFill')
    assert fill.width() == fill.parentItem().width()
    assert bridge.command('row.confirm', {'id': 'motion-c2', 'value': False})
    assert check.opacity() == 0 and fill.width() == pytest.approx(fill.parentItem().width() / 2)
    bridge.command('settings.set', {'key': 'motion', 'value': True})
    window.setProperty('noticeText', '动效测试：提示可点击关闭')
    toast = find(window, 'noticeToast')
    delay(25)
    assert 0 < toast.opacity() < 1
    bridge.command('settings.set', {'key': 'motion', 'value': False})
    assert toast.opacity() == 1
    click(window, toast)
    assert toast.opacity() == 0 and not toast.isVisible()
    bridge.command('settings.set', {'key': 'motion', 'value': True})
    button = find(window, 'exportStageButton')
    point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    delay(20)
    assert .97 < button.scale() < 1
    bridge.command('settings.set', {'key': 'motion', 'value': False})
    assert button.scale() == .97
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    assert button.scale() == 1 and window.property('stage') == 'export'
    assert bridge.command('row.confirm', {'id': 'motion-c2', 'value': True})
    final_export = find(window, 'exportFinalButton')
    assert final_export.isEnabled() and final_export.property('emphasized')
    assert bridge.command('row.edit', {'id': 'motion-c1', 'text': '动效验收 {字面括号}'})
    assert bridge.command('row.confirm', {'id': 'motion-c1', 'value': True})
    compatibility = find(window, 'exportCompatibilityHint')
    assert compatibility.isVisible() and compatibility.property('text')
    assert final_export.isEnabled(), 'Compatibility guidance must not change export eligibility'
    assert {key: bridge.media[key] for key in before} == before
    assert not bridge.jobs.active
    assert window.grabWindow().save(str(out / 'motion-export-moderate-emphasis.png'))
    (out / 'motion-invariants.json').write_text(json.dumps({'status': 'passed',
        'manual_fixture_not_ocr_output': True, 'actual_check_animation': True,
        'hidden_editor_loses_focus_and_input': True, 'hidden_owner_menu_dismissed': True,
        'button_toast_progress_off_switch_immediate': True, 'paused_media_unchanged': before,
        'no_ocr_or_api_started': True, 'windows_acceptance': False}, ensure_ascii=False, indent=2))
