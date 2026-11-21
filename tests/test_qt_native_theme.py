"""Actual adjunct render/input checks, with a hostile dark application palette.

Only synthetic text, images and credentials. No provider, operating-system
credential or media calls are allowed. Linux offscreen is not Windows/DPI/IME
acceptance. LYRIA_DIALOG_EVIDENCE optionally collects the real widget renders.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QObject, QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QLabel,
                               QLineEdit, QListWidget, QMessageBox, QPushButton, QStyle,
                               QStyleOptionSpinBox, QTextEdit, QToolTip)

from desktop_qt import api_review_dialogs, dialogs
from desktop_qt.native_theme import COLORS, DIALOG_STYLE, dialog_palette
from desktop_qt.roi_dialog import RegionDialog
from lyricsync import ai_config, api_client, credentials
from lyricsync.api_alignment import make_plan
from lyricsync.frames import FrameIndex
from lyricsync.model import Lyric, Mapping, Project, Translation, Visual


LONG_TEXT = '合成界面测试：保留用户中文、标点与 <b>字面文本</b>；这是需要人工复核的较长内容。'
FAKE_KEY = 'strictly-fake-native-theme-test-key'


def forbidden(*args, **kwargs):
    raise AssertionError('Theme tests must not call services or modify the document')


class ReadOnlyBridge(QObject):
    stateChanged = Signal()

    def __init__(self):
        super().__init__()
        project = Project()
        project.lyrics = [Lyric(f'l{n}', LONG_TEXT + str(n), 0, .4) for n in range(3)]
        project.visuals = [Visual(f'v{n}', LONG_TEXT + str(n), 0, .4, [], .9, 2) for n in range(3)]
        project.translations = [Translation('t', LONG_TEXT * 2)]
        project.mappings = [Mapping('m', ['t'], ['l0', 'l2'], ['v0', 'v2'], 0, .4)]
        self.document = SimpleNamespace(project=project,revision=0,id='readonly-theme-fixture')
        self._alignment = {}  # Production Bridge always owns the current candidate.
        self._alignment_preview=SimpleNamespace(active=False)
        self.api_settings = dict(ai_config.DEFAULTS)
        self.api_state = {'status': LONG_TEXT, 'models': []}
        self.state = {'error': ''}
        self.selected = 'm'
        self.media_controller = SimpleNamespace(index=FrameIndex([n * .04 for n in range(25)], 1))
        self._shutdown = False
        self.jobs = SimpleNamespace(active={}, cancel=forbidden)
        self._qc = {'width': 1920, 'height': 1080, 'duration': 1,
                    'inputKind': 'hardsubbed', 'previewSeconds': .2}
        self._qc_preview = QImage(640, 360, QImage.Format.Format_RGB32)
        self._qc_preview.fill(QColor('#453A56'))

    def api_text_consent(self,config):return False

    def _row(self):
        return self.document.project.mappings[0]

    def qc_region_context(self):
        return ('synthetic-theme-only',)

    command = configure_api = load_api_credential = delete_api_provider = forbidden
    qc_propose_regions = qc_preview_at = qc_confirm_regions = forbidden


@pytest.fixture(scope='module')
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request.OpenerDirector, 'open', forbidden)
    for name in ('api', 'get', 'save', 'delete'):
        monkeypatch.setattr(credentials.KeyStore, name, forbidden)
    monkeypatch.setattr(api_client, 'request', forbidden)
    monkeypatch.setattr(api_client, 'request_models', forbidden)


@pytest.fixture
def dark_host(application):
    original = QPalette(application.palette())
    palette = QPalette(original)
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
        for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.Button,
                     QPalette.ColorRole.ToolTipBase):
            palette.setColor(group, role, QColor('#171321'))
        for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                     QPalette.ColorRole.ButtonText, QPalette.ColorRole.ToolTipText):
            palette.setColor(group, role, QColor('#FAFAFA'))
        palette.setColor(group, QPalette.ColorRole.Highlight, QColor('#164BC9'))
        palette.setColor(group, QPalette.ColorRole.HighlightedText, QColor('#FFFFFF'))
    application.setPalette(palette)
    try:
        yield palette
        assert application.palette() == palette, 'adjunct styling must not alter the host palette'
    finally:
        QToolTip.hideText()
        application.setPalette(original)
        application.processEvents()


def capture(widget, name):
    image = widget.grab().toImage()
    assert not image.isNull() and image.width() > 0 and image.height() > 0
    destination = os.environ.get('LYRIA_DIALOG_EVIDENCE')
    if destination:
        root = Path(destination)
        root.mkdir(parents=True, exist_ok=True)
        assert image.save(str(root / (name + '.png')))
    # Assertions below address widget-local logical pixels. Preserve full
    # physical-resolution evidence, but normalize the returned assertion image
    # when QT_SCALE_FACTOR changes grab() dimensions.
    return image if image.size()==widget.size() else image.scaled(widget.size())


def assert_readable(dialog):
    assert dialog.styleSheet() == DIALOG_STYLE
    assert dialog.palette().color(QPalette.ColorRole.Window).name() == COLORS['canvas'].lower()
    for label in dialog.findChildren(QLabel):
        if '<b>' in label.text():
            assert label.textFormat() == Qt.TextFormat.PlainText
        color = COLORS['ink' if label.isEnabled() else 'label'].lower()
        if label.objectName() == 'ocrSamplingHint':
            # The approved OCR sampling explanation is deliberately secondary.
            # Keep this exception scoped to this named label, not every caption.
            color = COLORS['label'].lower()
            assert '3 帧/秒' in label.text() and '不提高单帧' in label.text()
            assert contrast(color, COLORS['canvas']) >= 4.5
        assert label.palette().color(QPalette.ColorRole.WindowText).name() == color
    for button in dialog.findChildren(QPushButton):
        if not button.isVisibleTo(dialog):
            continue
        top = button.mapTo(dialog, QPoint(0, 0))
        bottom = button.mapTo(dialog, button.rect().bottomRight())
        # Body buttons in ROI are intentionally scrollable; the footer is fixed.
        if dialog.objectName() != 'qcRegionDialog' or isinstance(button.parentWidget(), QDialogButtonBox):
            assert top.x() >= 0 and top.y() >= 0
            assert bottom.x() < dialog.width() and bottom.y() < dialog.height()
        assert button.width() >= button.sizeHint().width()
        assert button.height() >= button.sizeHint().height()


def contrast(first, second):
    def luminance(value):
        color = QColor(value)
        parts = [color.redF(), color.greenF(), color.blueF()]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in parts]
        return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))
    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + .05) / (dark + .05)


def test_shared_tokens_palette_and_text_contrast():
    source = (Path(__file__).parents[1] / 'desktop_qt/qml/Theme.qml').read_text()
    for name, value in COLORS.items():
        assert f'color {name}: "{value}"' in source
    assert '@' not in DIALOG_STYLE
    palette = dialog_palette()
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
        for foreground, background in ((QPalette.ColorRole.Text, QPalette.ColorRole.Base),
                                       (QPalette.ColorRole.WindowText, QPalette.ColorRole.Window),
                                       (QPalette.ColorRole.HighlightedText, QPalette.ColorRole.Highlight),
                                       (QPalette.ColorRole.ToolTipText, QPalette.ColorRole.ToolTipBase)):
            assert contrast(palette.color(group, foreground), palette.color(group, background)) >= 4.5


@pytest.mark.parametrize('kind,size', [
    ('api', (660, 570)), ('api', (820, 640)),
    ('region', (800, 540)), ('region', (920, 790)),
    ('range', (560, 330)), ('range', (720, 420)),
    ('plan', (800, 640)), ('plan', (960, 720)),
])
def test_real_dialog_dark_host_long_chinese_cancel_reopen(application, dark_host, kind, size):
    bridge = ReadOnlyBridge()
    before = deepcopy(bridge.document.project.to_dict())
    for attempt in range(2):
        if kind == 'api':
            dialog = dialogs.ApiSettingsDialog(bridge)
        elif kind == 'region':
            dialog = RegionDialog(bridge)
        elif kind == 'range':
            dialog = api_review_dialogs.AlignmentRangeDialog(bridge.document.project, 'm')
        else:
            plan = make_plan(bridge.document.project, bridge.media_controller.index, {
                'operations': [], 'semantic_issues': [{'mapping_ids': ['m'],
                    'lyric_ids': ['l0'], 'visual_ids': ['v0'], 'kind': 'uncertain',
                    'confidence': .5, 'reason': LONG_TEXT * 4, 'reference_meaning': ''}]}, ['m'])
            bridge._alignment = {'plan': plan}
            dialog = api_review_dialogs.AlignmentPlanDialog(bridge, plan)
        try:
            dialog.resize(*size)
            if kind == 'api' and attempt:
                dialog.provider.setCurrentIndex(1)
                dialog.name.setText(LONG_TEXT * 2)
                dialog.url.setText('https://synthetic-provider.invalid/' + 'long-path/' * 12)
                dialog.model.setCurrentText('synthetic-long-model-name-' * 6)
            dialog.show()
            application.processEvents()
            assert dialog.size().toTuple() == size
            assert_readable(dialog)
            image = capture(dialog, f'{kind}-dark-{size[0]}x{size[1]}-open{attempt + 1}')
            assert image.pixelColor(2, 2).name() == COLORS['canvas'].lower()
            if kind == 'api':
                assert dialog.key.echoMode() == QLineEdit.EchoMode.Password and not dialog.key.text()
                dialog.key.setText(FAKE_KEY)
                assert FAKE_KEY not in dialog.key.displayText()
                assert not dialog.enabled.isChecked()  # Key input never grants privacy consent.
            elif kind == 'region':
                assert dialog.scroll.widget().palette().color(QPalette.ColorRole.Window).name() == COLORS['canvas'].lower()
                dialog.scroll.verticalScrollBar().setValue(dialog.scroll.verticalScrollBar().maximum())
                application.processEvents()
                capture(dialog, f'region-dark-{size[0]}x{size[1]}-scrolled')
                dialog.set_box('chinese', [10, 20, 800, 300], 'user')
            elif kind == 'range':
                assert LONG_TEXT in dialog.summary.text()
            else:
                assert dialog.text.isReadOnly() and not dialog.keep.isEnabled()
                assert not dialog.preview.isEnabled()  # Valid no-change plans remain read-only.
                assert LONG_TEXT in dialog.text.toPlainText()
                assert not dialog._cached_stale and bridge._alignment['plan'] is plan
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
            application.processEvents()
            assert not dialog.isVisible() and dialog.result() == QDialog.DialogCode.Rejected
            if kind == 'plan':
                assert bridge._alignment['plan'] is plan  # Closing preserves the candidate.
            if kind == 'api':
                assert not dialog.key.text()
            if kind == 'region':
                assert not dialog.alive and not dialog.poll.isActive()
            assert bridge.document.project.to_dict() == before
        finally:
            if dialog.isVisible():
                dialog.reject()
            dialog.deleteLater()
            application.processEvents()


def test_spin_arrows_combo_popup_and_tooltip_remain_readable(application, dark_host):
    dialog = dialogs.ApiSettingsDialog(ReadOnlyBridge())
    try:
        dialog.show()
        dialog.advanced.setChecked(True)
        application.processEvents()
        spin = dialog.max_calls
        option = QStyleOptionSpinBox()
        spin.initStyleOption(option)
        up = spin.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option,
                                         QStyle.SubControl.SC_SpinBoxUp, spin)
        before = spin.value()
        QTest.mouseClick(spin, Qt.MouseButton.LeftButton, pos=up.center())
        assert spin.value() == before + 1
        spin_image = capture(spin, 'spin-native-chevron')
        # A real visible chevron, not a clickable but blank subcontrol.
        assert sum(spin_image.pixelColor(x, y).lightness() < 160
                   for x in range(up.left() + 2, up.right() - 2)
                   for y in range(up.top() + 2, up.bottom() - 2)) > 2
        dialog.provider.showPopup()
        application.processEvents()
        view = dialog.provider.view()
        assert view.palette().color(QPalette.ColorRole.HighlightedText).name() == COLORS['ink'].lower()
        capture(view.window(), 'provider-popup-dark')
        dialog.provider.hidePopup()
        application.processEvents()
        QTest.mouseMove(dialog.key, dialog.key.rect().center())
        application.processEvents()
        QToolTip.showText(dialog.key.mapToGlobal(QPoint(8, 8)), '合成提示：保留中文与原始证据', dialog.key)
        application.processEvents()
        tooltip = next(w for w in application.topLevelWidgets() if w.inherits('QTipLabel') and w.isVisible())
        tip_image = capture(tooltip, 'tooltip-dark')
        assert tip_image.pixelColor(3, 3).name() == COLORS['paper'].lower()
        assert tooltip.palette().color(QPalette.ColorRole.WindowText).name() == COLORS['ink'].lower()
        QToolTip.hideText()
        box = dialog.findChild(QDialogButtonBox)
        cancel = box.button(QDialogButtonBox.StandardButton.Cancel)
        cancel.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.mouseMove(cancel, cancel.rect().center())
        application.processEvents()
        hover = capture(cancel, 'button-keyboard-focus-hover')
        assert hover.pixelColor(4, hover.height() // 2).name() == COLORS['lilacSoft'].lower()
        assert hover.pixelColor(0, hover.height() // 2).name() == '#aa91ca'
        QTest.mousePress(cancel, Qt.MouseButton.LeftButton)
        pressed = capture(cancel, 'button-pressed')
        assert pressed.pixelColor(4, pressed.height() // 2).name() == COLORS['emphasisPressed'].lower()
        QTest.mouseRelease(cancel, Qt.MouseButton.LeftButton)
        assert not dialog.isVisible()
    finally:
        QToolTip.hideText()
        if dialog.isVisible():
            dialog.reject()


def test_nested_delete_confirmation_keeps_no_default_and_no_side_effects(application, dark_host):
    bridge = ReadOnlyBridge()
    dialog = dialogs.ApiSettingsDialog(bridge)
    dialog.show()
    application.processEvents()
    errors = []
    seen = []
    def decline():
        prompt = application.activeModalWidget()
        try:
            assert isinstance(prompt, QMessageBox)
            seen.append(prompt.windowTitle())
            assert prompt.defaultButton() == prompt.button(QMessageBox.StandardButton.No)
            assert prompt.text() == '确定清除已保存的密钥并恢复默认接口设置吗？'
            assert prompt.palette().color(QPalette.ColorRole.Window).name() == COLORS['canvas'].lower()
            assert prompt.findChild(QLabel, 'qt_msgbox_label').palette().color(QPalette.ColorRole.WindowText).name() == COLORS['ink'].lower()
            capture(prompt, 'delete-confirmation-dark-no-default')
        except BaseException as exc:
            errors.append(exc)
        finally:
            if isinstance(prompt, QMessageBox):
                QTest.mouseClick(prompt.button(QMessageBox.StandardButton.No), Qt.MouseButton.LeftButton)
    QTimer.singleShot(5, decline)
    try:
        dialog.delete()
        if errors:
            raise errors[0]
        assert seen and dialog.isVisible()
    finally:
        dialog.reject()


@pytest.mark.parametrize('action', ['return', 'escape', 'later', 'more', 'already-100'])
def test_budget_notice_defaults_to_later_and_only_explicit_more_resumes(application, dark_host, action):
    from desktop_qt.controller import Bridge
    resumed = []
    receiver = ReadOnlyBridge()
    receiver.document.id = 'synthetic-native-budget'
    receiver.document.revision = 7
    receiver.epoch = 11
    receiver._api_resume = lambda limit: resumed.append(limit)
    receiver._api_resume_context = (receiver.document.id, receiver.epoch, receiver.document.revision)
    receiver._resume_api = lambda: Bridge._resume_api(receiver)
    before = deepcopy(receiver.document.project.to_dict())
    errors = []
    seen = []
    def respond():
        prompt = application.activeModalWidget()
        try:
            assert isinstance(prompt, QMessageBox)
            seen.append(prompt.windowTitle())
            assert_readable(prompt)
            later = next(button for button in prompt.buttons() if button.text() == '暂不继续')
            more = next((button for button in prompt.buttons() if '100' in button.text()), None)
            assert prompt.defaultButton() is later and prompt.escapeButton() is later
            assert (more is None) == (action == 'already-100')
            capture(prompt, f'budget-notice-dark-{action}')
            if action == 'return':
                QTest.keyClick(prompt, Qt.Key.Key_Return)
            elif action == 'escape':
                QTest.keyClick(prompt, Qt.Key.Key_Escape)
            else:
                QTest.mouseClick(more if action == 'more' else later, Qt.MouseButton.LeftButton)
        except BaseException as exc:
            errors.append(exc)
            if isinstance(prompt, QDialog):
                prompt.reject()
    QTimer.singleShot(5, respond)
    result = {'message': '合成预算状态：调用已达上限，不代表任何真实费用。',
              'used': 100 if action == 'already-100' else 50,
              'limit': 100 if action == 'already-100' else 50}
    Bridge._budget_dialog(receiver, result)
    if errors:
        raise errors[0]
    assert seen and resumed == ([100] if action == 'more' else [])
    assert receiver.document.project.to_dict() == before
    assert receiver.api_settings['api_max_calls'] == 50
    if action == 'more':
        assert receiver._api_resume is None


@pytest.mark.parametrize('kind', ['text', 'pairing', 'rescan', 'ocr', 'subtitle',
                                 'qc-evidence', 'qc-rescan', 'warnings', 'saved-reviews'])
def test_actual_modal_helpers_cancel_without_changes(application, dark_host, kind):
    bridge = ReadOnlyBridge()
    before = deepcopy(bridge.document.project.to_dict())
    cue = {'id': 'synthetic-cue', 'text': LONG_TEXT, 'expected_text': LONG_TEXT,
           'first_frame': 1, 'last_frame': 10}
    calls = {
        'text': lambda: dialogs.show_text('合成只读长文本', (LONG_TEXT + '\n') * 60),
        'pairing': lambda: dialogs.edit_pairing(bridge),
        'rescan': lambda: dialogs.choose_rescan(bridge, 'm', {'visuals': [vars(v) for v in bridge.document.project.visuals]}),
        'ocr': lambda: dialogs.ocr_settings(bridge, ['CPUExecutionProvider', 'DmlExecutionProvider']),
        'subtitle': lambda: dialogs.subtitle_style(bridge),
        'qc-evidence': lambda: dialogs.inspect_qc_cue(bridge, cue),
        'qc-rescan': lambda: dialogs.qc_rescan_dialog(bridge, cue),
        'warnings': lambda: dialogs.review_import_warnings([LONG_TEXT] * 30),
        'saved-reviews': lambda: api_review_dialogs.show_saved_reviews(bridge, {'all': True}),
    }
    for attempt in range(2):
        seen = []
        errors = []
        timer = QTimer()
        timer.setInterval(5)
        def inspect_and_cancel():
            dialog = application.activeModalWidget()
            if kind=='saved-reviews':dialog=next(iter(getattr(bridge,'_auxiliary_dialogs',[])),None)
            if not isinstance(dialog, QDialog):
                return
            timer.stop()
            seen.append(dialog.windowTitle())
            try:
                assert_readable(dialog)
                for listing in dialog.findChildren(QListWidget):
                    assert listing.count() == 3
                    listing.item(0).setSelected(True)
                    listing.item(2).setSelected(True)
                    assert len(listing.selectedItems()) == 2
                    assert listing.palette().color(QPalette.ColorRole.Highlight).name() == COLORS['emphasis'].lower()
                    viewport = listing.viewport().grab().toImage()
                    item = listing.visualItemRect(listing.item(0))
                    actual = viewport.pixelColor(min(item.right(), viewport.width() - 1) - 8, item.top() + 3)
                    expected = QColor(COLORS['emphasis'])
                    # Native keyboard-focus shading may slightly tint the fill.
                    assert all(abs(a - b) <= 10 for a, b in zip(actual.getRgb()[:3], expected.getRgb()[:3]))
                if kind == 'text':
                    assert dialog.findChild(QTextEdit).toPlainText() == (LONG_TEXT + '\n') * 60
                capture(dialog, f'{kind}-modal-dark-open{attempt + 1}')
            except BaseException as exc:
                errors.append(exc)
            finally:
                dialog.reject()
        timer.timeout.connect(inspect_and_cancel)
        timer.start()
        try:
            calls[kind]()
            if kind=='saved-reviews':QTest.qWait(30)
        finally:
            timer.stop()
        if errors:
            raise errors[0]
        assert seen and bridge.document.project.to_dict() == before
