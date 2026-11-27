"""Moonlit Lilac for the small QWidget adjuncts to the Qt Quick UI.

Apply to each dialog, never QApplication: native host colours must not make
light fields unreadable, and styling must not alter the QML/media pipeline.
Keep these shared colours aligned with qml/Theme.qml. Qt retains keyboard
focus, button roles, control behaviour and password echo modes.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QMessageBox,
    QProxyStyle,
    QPushButton,
    QStyle,
    QToolButton,
    QWidget,
)


COLORS = {
    'canvas': '#F7F5FB',
    'paper': '#FFFFFF',
    'slab': '#F8F6FC',
    'lilac': '#8562C5',
    'lilacDark': '#63419C',
    'ink': '#29213A',
    'label': '#736985',
    'line': '#EAE5F3',
    'lilacSoft': '#F1ECFB',
    'emphasis': '#E7DDF7',
    'emphasisHover': '#DED0F1',
    'emphasisPressed': '#D5C3EC',
    'emphasisBorder': '#CBB5E7',
}

# Avoid a solid purple active outline. A thin muted lilac focus border keeps
# keyboard position visible without turning every selected field into a badge.
DIALOG_STYLE = """
QDialog, QMessageBox { background: @canvas; color: @ink; }
QLabel, QCheckBox, QRadioButton, QGroupBox { color: @ink; outline: none; }
QLabel:disabled, QCheckBox:disabled, QRadioButton:disabled,
QGroupBox:disabled { color: @label; }
QGroupBox {
    background: @paper;
    border: 1px solid @line; border-radius: 10px;
    margin-top: 12px; padding: 14px 12px 12px;
}
QTabWidget#modelWorkflowTabs::pane { border: 0; }
QTabWidget#modelWorkflowTabs QTabBar::tab {
    background: transparent; color: @label; border: 0;
    border-bottom: 2px solid transparent; padding: 8px 10px; outline: none;
}
QTabWidget#modelWorkflowTabs QTabBar::tab:selected { color: @lilacDark; border-bottom: 2px solid @lilac; }
QFrame#modelReadinessCard { background: @paper; border: 1px solid @line; border-radius: 10px; }
QFrame#apiAdvancedBody { background: @paper; border: 1px solid @line; border-radius: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: @lilacDark; font-weight: 600; }
QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox {
    background: @paper; color: @ink; border: 1px solid @line;
    border-radius: 7px; padding: 5px 8px; outline: none;
    selection-background-color: @emphasis; selection-color: @ink;
}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover,
QAbstractSpinBox:hover, QComboBox:hover { border-color: @emphasisBorder; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QAbstractSpinBox:focus, QComboBox:focus { border-color: #AA91CA; }
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled,
QAbstractSpinBox:disabled, QComboBox:disabled {
    background: @slab; color: @label; border-color: @line;
}
QAbstractSpinBox { padding-right: 20px; }
QComboBox { padding-right: 22px; }
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
    width: 20px; border: none; background: transparent;
}
QAbstractSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; }
QAbstractSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; }
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover { background: @lilacSoft; }
QAbstractSpinBox::up-button:pressed, QAbstractSpinBox::down-button:pressed { background: @emphasis; }
QAbstractSpinBox::up-arrow { image: url("@upIcon"); width: 12px; height: 12px; }
QAbstractSpinBox::down-arrow { image: url("@downIcon"); width: 12px; height: 12px; }
QComboBox::drop-down { width: 22px; border: none; }
QComboBox::down-arrow { image: url("@downIcon"); width: 14px; height: 14px; }
QAbstractItemView {
    background: @paper; alternate-background-color: @slab; color: @ink;
    border: 1px solid @line; border-radius: 8px; outline: none;
    selection-background-color: @emphasis; selection-color: @ink;
}
QAbstractItemView:focus { outline: none; border: 2px solid #8562c5; }
QAbstractItemView::item { padding: 6px 8px; border-radius: 5px; }
QAbstractItemView::item:hover { background: @lilacSoft; }
QAbstractItemView::item:selected { background: @emphasis; color: @ink; }
QAbstractItemView::item:selected:hover { background: @emphasisHover; }
QAbstractItemView:disabled { background: @slab; color: @label; }
QPushButton {
    background: @paper; color: @ink; border: 1px solid @line;
    border-radius: 7px; padding: 6px 14px; outline: none;
}
QPushButton:default { background: @emphasis; border-color: @emphasisBorder; }
QPushButton:hover { background: @lilacSoft; border-color: @emphasisBorder; }
QPushButton:focus { border-color: #AA91CA; }
QPushButton:pressed { background: @emphasisPressed; border-color: @emphasisBorder; }
QPushButton:disabled { background: @slab; color: @label; border-color: @line; }
QPushButton[styleRole="primary"] {
    background: #5E3B9E; color: #FFFFFF; border: 1px solid #4F3088; font-weight: 600;
}
QPushButton[styleRole="primary"]:hover { background: #52318E; border-color: #462A7A; }
QPushButton[styleRole="primary"]:pressed { background: #452879; border-color: #3B2268; }
QPushButton[styleRole="primary"]:disabled { background: #C8BED8; color: #FFFFFF; border-color: #C8BED8; }
QPushButton[styleRole="soft"] {
    background: #F0EAFA; color: #5E3B9E; border: 1px solid #D9CCF0; font-weight: 600;
}
QPushButton[styleRole="soft"]:hover { background: #E7DDF8; border-color: #C5B2E9; }
QMessageBox QPushButton { min-height: 20px; }
QCheckBox { spacing: 7px; }
QCheckBox::indicator {
    width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid #C9BFE0; background: @paper;
}
QCheckBox::indicator:hover { border-color: #8562C5; }
QCheckBox::indicator:checked {
    background: #5E3B9E; border-color: #5E3B9E; image: url("@checkIcon");
}
QToolTip {
    background: @paper; color: @ink; border: 1px solid @emphasisBorder;
    border-radius: 6px; padding: 6px 10px;
}
"""
for _name, _color in COLORS.items():
    DIALOG_STYLE = DIALOG_STYLE.replace('@' + _name + ';', _color + ';')
# Reuse the shipped QML chevrons and white checkmark for consistent crisp icons.
for _direction in ('up', 'down'):
    _icon = Path(__file__).with_name('assets') / 'icons' / f'chev-{_direction}.svg'
    DIALOG_STYLE = DIALOG_STYLE.replace('@' + _direction + 'Icon', _icon.as_posix().replace('"', '\\"'))
_check_icon = Path(__file__).with_name('assets') / 'icons' / 'check-white.svg'
DIALOG_STYLE = DIALOG_STYLE.replace('@checkIcon', _check_icon.as_posix().replace('"', '\\"'))


class NoFocusRectStyle(QProxyStyle):
    """Strip legacy Windows dotted PE_FrameFocusRect boxes across QWidget dialogs."""

    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PrimitiveElement.PE_FrameFocusRect:
            return
        super().drawPrimitive(element, option, painter, widget)


def _ensure_no_focus_rect_style(app: QApplication | None = None) -> None:
    app = app or QApplication.instance()
    if app is None:
        return
    try:
        current = app.style()
        if isinstance(current, NoFocusRectStyle) or app.property('lyriaNoFocusStyle'):
            return
        app.setProperty('lyriaNoFocusStyle', True)
        app.setStyle(NoFocusRectStyle(current))
    except Exception:
        pass


def dialog_palette() -> QPalette:
    """Complete light palette, including inactive windows and disabled text."""
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: 'canvas',
        QPalette.ColorRole.WindowText: 'ink',
        QPalette.ColorRole.Base: 'paper',
        QPalette.ColorRole.AlternateBase: 'slab',
        QPalette.ColorRole.Text: 'ink',
        QPalette.ColorRole.Button: 'paper',
        QPalette.ColorRole.ButtonText: 'ink',
        QPalette.ColorRole.BrightText: 'ink',
        QPalette.ColorRole.Highlight: 'emphasis',
        QPalette.ColorRole.HighlightedText: 'ink',
        QPalette.ColorRole.ToolTipBase: 'paper',
        QPalette.ColorRole.ToolTipText: 'ink',
        QPalette.ColorRole.PlaceholderText: 'label',
        QPalette.ColorRole.Link: 'lilacDark',
        QPalette.ColorRole.LinkVisited: 'lilacDark',
        QPalette.ColorRole.Light: 'paper',
        QPalette.ColorRole.Midlight: 'slab',
        QPalette.ColorRole.Mid: 'emphasisBorder',
        QPalette.ColorRole.Dark: 'label',
        QPalette.ColorRole.Shadow: 'label',
    }
    if hasattr(QPalette.ColorRole, 'Accent'):
        roles[QPalette.ColorRole.Accent] = 'lilac'
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive,
                  QPalette.ColorGroup.Disabled):
        for role, color in roles.items():
            palette.setColor(group, role, QColor(COLORS[color]))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(COLORS['label']))
    for role in (QPalette.ColorRole.Base, QPalette.ColorRole.Button):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(COLORS['slab']))
    return palette


class _TitleCloseButton(QToolButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('lyriaDialogClose')
        self.setAccessibleName('关闭')
        self.setText('')
        self.setFixedSize(28, 26)
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.isDown():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor('#EBD8E0'))
            p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 6, 6)
            ink = QColor('#912F56')
        elif self.underMouse():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor('#F4E9EE'))
            p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 6, 6)
            ink = QColor('#A93E67')
        else:
            ink = QColor('#6E6582')
        cx, cy = self.width() / 2.0, self.height() / 2.0
        r = 4.2
        p.setPen(QPen(ink, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx - r, cy - r), QPointF(cx + r, cy + r))
        p.drawLine(QPointF(cx + r, cy - r), QPointF(cx - r, cy + r))


class _DialogOutlineFilter(QObject):
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Paint and isinstance(obj, QDialog):
            p = QPainter(obj)
            p.setPen(QPen(QColor('#DCD4EB'), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(0, 0, obj.width() - 1, obj.height() - 1)
        return False


class DialogTitle(QFrame):
    def __init__(self, dialog):
        from PySide6.QtWidgets import QHBoxLayout, QLabel
        super().__init__(dialog)
        self.setObjectName('lyriaDialogTitle')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(38)
        bar = QHBoxLayout(self)
        bar.setContentsMargins(12, 0, 8, 1)
        bar.setSpacing(0)
        bar.addSpacing(78)
        self._label = QLabel(dialog.windowTitle(), self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        font = QFont(self._label.font())
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        self._label.setFont(font)
        dialog.windowTitleChanged.connect(self._label.setText)
        bar.addWidget(self._label, 1)
        bar.addSpacing(50)
        close = _TitleCloseButton(self)
        close.clicked.connect(dialog.close)
        bar.addWidget(close)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, QColor('#F6F2FC'))
        grad.setColorAt(1.0, QColor('#EFEAF9'))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRect(1, 1, max(0, self.width() - 2), max(0, self.height() - 2))
        p.setPen(QPen(QColor('#E4DCFA'), 1))
        p.drawLine(1, self.height() - 1, max(1, self.width() - 2), self.height() - 1)
        # Left purple symmetric 4-point star badge (20x20) + brand mark
        bx, by, bs = 12.0, (self.height() - 20.0) / 2.0, 20.0
        bgrad = QLinearGradient(bx, by, bx + bs, by + bs)
        bgrad.setColorAt(0.0, QColor('#7E5BEF'))
        bgrad.setColorAt(1.0, QColor('#5B389E'))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bgrad)
        p.drawRoundedRect(QRectF(bx, by, bs, bs), 6, 6)
        cx, cy, r = bx + bs / 2.0, by + bs / 2.0, 5.4
        star = QPainterPath()
        star.moveTo(cx, cy - r)
        star.quadTo(cx, cy, cx + r, cy)
        star.quadTo(cx, cy, cx, cy + r)
        star.quadTo(cx, cy, cx - r, cy)
        star.quadTo(cx, cy, cx, cy - r)
        star.closeSubpath()
        p.setBrush(QColor('#FFFFFF'))
        p.drawPath(star)
        if self.width() >= 380:
            brand_font = QFont(self._label.font())
            brand_font.setPixelSize(11)
            brand_font.setWeight(QFont.Weight.DemiBold)
            p.setFont(brand_font)
            p.setPen(QColor('#6E6582'))
            p.drawText(QRectF(bx + bs + 6, 0, 52, self.height() - 1), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, 'Lyria')
        # Outer frameless dialog rim on top/left/right
        p.setPen(QPen(QColor('#DCD4EB'), 1))
        p.drawLine(0, 0, self.width() - 1, 0)
        p.drawLine(0, 0, 0, self.height() - 1)
        p.drawLine(self.width() - 1, 0, self.width() - 1, self.height() - 1)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()
            event.accept()
        else:
            super().mousePressEvent(event)


def _enable_win11_round_corners(dialog):
    if os.name != 'nt':
        return
    try:
        import ctypes
        hwnd = int(dialog.winId())
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), 33, ctypes.byref(pref), ctypes.sizeof(pref)
        )
    except Exception:
        pass


def _attach_dialog_owner(dialog: QDialog) -> None:
    """Bind parentless dialogs to the main Lyria QQuickWindow so Alt+Tab switches them together."""
    if dialog.parentWidget() is not None:
        return
    try:
        main_win = next(
            (w for w in QGuiApplication.topLevelWindows()
             if w.objectName() == 'lyriaMainWindow' and w.isVisible()),
            None,
        )
        if main_win is None:
            return
        if not dialog.testAttribute(Qt.WidgetAttribute.WA_WState_Created):
            dialog.winId()
        handle = dialog.windowHandle()
        if handle is not None and handle is not main_win:
            handle.setTransientParent(main_win)
        if os.name == 'nt':
            import ctypes
            dlg_hwnd = int(dialog.winId())
            main_hwnd = int(main_win.winId())
            if dlg_hwnd and main_hwnd and dlg_hwnd != main_hwnd:
                ctypes.windll.user32.SetWindowLongPtrW(
                    ctypes.c_void_p(dlg_hwnd), -8, ctypes.c_void_p(main_hwnd)
                )
    except Exception:
        pass


def _msgbox_badge_pixmap(icon: QMessageBox.Icon) -> QPixmap | None:
    if icon == QMessageBox.Icon.NoIcon:
        return None
    palette_map = {
        QMessageBox.Icon.Information: ('#EFEAF9', '#D6C7EF', '#5E3B9E', 'i'),
        QMessageBox.Icon.Question: ('#EFEAF9', '#D6C7EF', '#5E3B9E', '?'),
        QMessageBox.Icon.Warning: ('#FFF4E2', '#ECCB92', '#B26A11', '!'),
        QMessageBox.Icon.Critical: ('#FDEBF1', '#EAB7CA', '#A83B66', '×'),
    }
    bg, border, fg, glyph = palette_map.get(icon, ('#EFEAF9', '#D6C7EF', '#5E3B9E', 'i'))
    pix = QPixmap(36, 36)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(border), 1.2))
    p.setBrush(QColor(bg))
    p.drawRoundedRect(QRectF(1.5, 1.5, 33, 33), 10, 10)
    f = QFont('Microsoft YaHei UI' if os.name == 'nt' else 'Noto Sans CJK SC')
    f.setPixelSize(18)
    f.setWeight(QFont.Weight.Bold)
    p.setFont(f)
    p.setPen(QColor(fg))
    p.drawText(QRectF(0, 0, 36, 35), Qt.AlignmentFlag.AlignCenter, glyph)
    p.end()
    return pix


def _polish_message_box(box: QMessageBox) -> None:
    if box.property('lyriaMsgPolished'):
        return
    box.setProperty('lyriaMsgPolished', True)
    pix = _msgbox_badge_pixmap(box.icon())
    if pix is not None:
        box.setIconPixmap(pix)
    zh_map = {
        'OK': '确定',
        '&OK': '确定',
        'Cancel': '取消',
        '&Cancel': '取消',
        'Yes': '是',
        '&Yes': '是',
        'No': '否',
        '&No': '否',
        'Close': '关闭',
        '&Close': '关闭',
        'Save': '保存',
        '&Save': '保存',
        'Apply': '应用',
        '&Apply': '应用',
    }
    for btn in box.buttons():
        if isinstance(btn, QPushButton):
            txt = btn.text()
            if txt in zh_map:
                btn.setText(zh_map[txt])


def install_dialog_title(dialog):
    if dialog.property('lyriaTitleInstalled') or os.name != 'nt' or '--native-window-frame' in sys.argv:
        return
    dialog.setProperty('lyriaTitleInstalled', True)
    if dialog.windowFlags() & Qt.WindowType.FramelessWindowHint:
        return
    dialog.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
    if isinstance(dialog, QMessageBox):
        dialog.setMinimumSize(0, 0)
        dialog.setMaximumSize(16777215, 16777215)
    margins = dialog.contentsMargins()
    dialog.setContentsMargins(margins.left() + 1, margins.top() + 38, margins.right() + 1, margins.bottom() + 1)
    dialog._lyria_outline = _DialogOutlineFilter(dialog)
    dialog.installEventFilter(dialog._lyria_outline)
    dialog._lyria_title = DialogTitle(dialog)
    dialog._lyria_title.setGeometry(0, 0, dialog.width(), 38)
    dialog._lyria_title.show()


class TextSourceCombo(QComboBox):
    def showPopup(self):
        width = max((self.fontMetrics().horizontalAdvance(self.itemText(i)) for i in range(self.count())), default=0) + 44
        screen = self.screen()
        limit = screen.availableGeometry().width() - 16 if screen else width
        self.view().setMinimumWidth(min(limit, max(self.width(), width)))
        self.view().setWordWrap(width > limit)
        super().showPopup()


class AppDialogTheme(QObject):
    """Style only app-owned QWidget dialogs, including static messages and input prompts."""

    def __init__(self, parent=None):
        super().__init__(parent)
        _ensure_no_focus_rect_style(parent if isinstance(parent, QApplication) else None)

    def eventFilter(self, obj, event):
        if isinstance(obj, QDialog) and not isinstance(obj, QFileDialog):
            if event.type() == QEvent.Type.Polish and obj.isWindow():
                _ensure_no_focus_rect_style()
                install_dialog_title(obj)
            elif event.type() == QEvent.Type.Resize and hasattr(obj, '_lyria_title'):
                obj._lyria_title.setGeometry(0, 0, obj.width(), 38)
                obj._lyria_title.raise_()
        if event.type() == QEvent.Type.Show and isinstance(obj, QDialog) and not isinstance(obj, QFileDialog):
            _attach_dialog_owner(obj)
            if hasattr(obj, '_lyria_title'):
                _enable_win11_round_corners(obj)
            if not obj.property('lyriaThemed'):
                apply_dialog_theme(obj)
        return False


def apply_dialog_theme(dialog: QDialog) -> None:
    """Call after building content. No application state, I/O or signal changes.

    Qt style sheets do not propagate the parent's explicit palette reliably to
    native subcontrols or reparented scroll contents. Set each existing child's
    palette too, rather than changing QApplication's global propagation flags.
    """
    _ensure_no_focus_rect_style()
    dialog.setProperty('lyriaThemed', True)
    if isinstance(dialog, QMessageBox):
        _polish_message_box(dialog)
    palette = dialog_palette()
    dialog.setPalette(palette)
    for widget in dialog.findChildren(QWidget):
        if widget.objectName() == 'ocrSamplingHint':
            hint_pal = QPalette(palette)
            for grp in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
                hint_pal.setColor(grp, QPalette.ColorRole.WindowText, QColor(COLORS['label']))
            widget.setPalette(hint_pal)
        else:
            widget.setPalette(palette)
    font = QFont(dialog.font())
    font.setFamily('Microsoft YaHei UI' if os.name == 'nt' else 'Noto Sans CJK SC')
    dialog.setFont(font)
    dialog.setStyleSheet(DIALOG_STYLE)
    if isinstance(dialog, QMessageBox) and dialog.layout() is not None:
        dialog.setMinimumSize(0, 0)
        dialog.setMaximumSize(16777215, 16777215)
        for _btn in dialog.buttons():
            if isinstance(_btn, QPushButton):
                _btn.ensurePolished()
                _btn.setMinimumWidth(max(80, _btn.sizeHint().width() + 2))
        dialog.layout().invalidate()
        dialog.adjustSize()
    _attach_dialog_owner(dialog)
