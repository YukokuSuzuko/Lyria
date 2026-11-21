"""Moonlit Lilac for the small QWidget adjuncts to the Qt Quick UI.

Apply to each dialog, never QApplication: native host colours must not make
light fields unreadable, and styling must not alter the QML/media pipeline.
Keep these shared colours aligned with qml/Theme.qml. Qt retains keyboard
focus, button roles, control behaviour and password echo modes.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtCore import QObject, QEvent
from PySide6.QtWidgets import QDialog, QFileDialog, QWidget


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
QLabel, QCheckBox, QRadioButton, QGroupBox { color: @ink; }
QLabel:disabled, QCheckBox:disabled, QRadioButton:disabled,
QGroupBox:disabled { color: @label; }
QGroupBox {
    border: 1px solid @line; border-radius: 8px;
    margin-top: 12px; padding: 14px 10px 10px;
}
QTabWidget#modelWorkflowTabs::pane { border: 0; }
QTabWidget#modelWorkflowTabs QTabBar::tab { background: transparent; color: @label; border: 0; border-bottom: 2px solid transparent; padding: 8px 10px; }
QTabWidget#modelWorkflowTabs QTabBar::tab:selected { color: @lilacDark; border-bottom: 2px solid @lilac; }
QFrame#modelReadinessCard { background: @paper; border: 1px solid @line; border-radius: 8px; }
QFrame#apiAdvancedBody { border: 1px solid @line; border-radius: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox {
    background: @paper; color: @ink; border: 1px solid @line;
    border-radius: 6px; padding: 5px 7px;
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
    border: 1px solid @line; border-radius: 6px;
    selection-background-color: @emphasis; selection-color: @ink;
}
QAbstractItemView:focus { border-color: #AA91CA; }
QAbstractItemView::item { padding: 5px 7px; }
QAbstractItemView::item:hover { background: @lilacSoft; }
QAbstractItemView::item:selected { background: @emphasis; color: @ink; }
QAbstractItemView::item:selected:hover { background: @emphasisHover; }
QAbstractItemView:disabled { background: @slab; color: @label; }
QPushButton {
    background: @paper; color: @ink; border: 1px solid @line;
    border-radius: 6px; padding: 7px 13px;
}
QPushButton:default { background: @emphasis; border-color: @emphasisBorder; }
QPushButton:hover { background: @lilacSoft; border-color: @emphasisBorder; }
QPushButton:focus { border-color: #AA91CA; }
QPushButton:pressed { background: @emphasisPressed; border-color: @emphasisBorder; }
QPushButton:disabled { background: @slab; color: @label; border-color: @line; }
QToolTip {
    background: @paper; color: @ink; border: 1px solid @emphasisBorder;
    border-radius: 5px; padding: 6px 9px;
}
"""
for _name, _color in COLORS.items():
    DIALOG_STYLE = DIALOG_STYLE.replace('@' + _name + ';', _color + ';')
# Reuse the shipped QML chevrons. Partially styled spin boxes otherwise lose
# their native arrow glyphs with some Qt styles, despite retaining click areas.
for _direction in ('up', 'down'):
    _icon = Path(__file__).with_name('assets') / 'icons' / f'chev-{_direction}.svg'
    DIALOG_STYLE = DIALOG_STYLE.replace('@' + _direction + 'Icon', _icon.as_posix().replace('"', '\\"'))


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
    # Accent is used by newer Qt native styles for check/radio indicators.
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


class AppDialogTheme(QObject):
    """Style only app-owned QWidget dialogs, including static messages and input prompts."""
    def eventFilter(self, obj, event):
        if event.type()==QEvent.Type.Show and isinstance(obj,QDialog) and not isinstance(obj,QFileDialog) and not obj.property('lyriaThemed'):
            apply_dialog_theme(obj)
        return False


def apply_dialog_theme(dialog: QDialog) -> None:
    """Call after building content. No application state, I/O or signal changes.

    Qt style sheets do not propagate the parent's explicit palette reliably to
    native subcontrols or reparented scroll contents. Set each existing child's
    palette too, rather than changing QApplication's global propagation flags.
    """
    dialog.setProperty('lyriaThemed',True)
    palette = dialog_palette()
    dialog.setPalette(palette)
    for widget in dialog.findChildren(QWidget):
        widget.setPalette(palette)
    font = QFont(dialog.font())
    font.setFamily('Microsoft YaHei UI' if os.name == 'nt' else 'Noto Sans CJK SC')
    dialog.setFont(font)
    dialog.setStyleSheet(DIALOG_STYLE)
