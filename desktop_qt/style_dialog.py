"""Aegisub-inspired symmetric two-column subtitle style manager & real-time workbench."""
from copy import deepcopy
import json
from pathlib import Path
import uuid

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFontComboBox,
    QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPushButton, QSpinBox, QStyledItemDelegate, QStyle, QVBoxLayout, QWidget,
)

from lyricsync import styles
from lyricsync.speakers import default_style
from lyricsync.storage import atomic_json
from . import carbon_widgets as c


_ICONS_DIR = Path(__file__).with_name('assets') / 'icons'
_STYLE_META_ROLE = Qt.ItemDataRole.UserRole + 1


class _StyleItemDelegate(QStyledItemDelegate):
    """Paint a crisp colour swatch + subtle font/size hint while keeping item.text() pure."""

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        hint.setHeight(max(30, hint.height() + 4))
        return hint

    def paint(self, painter: QPainter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        rect = option.rect.adjusted(2, 1, -2, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected:
            painter.setPen(QPen(QColor('#CBB5E7'), 1.0))
            painter.setBrush(QColor('#E7DDF7'))
            painter.drawRoundedRect(QRectF(rect), 6, 6)
        elif hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#F4EFFB'))
            painter.drawRoundedRect(QRectF(rect), 6, 6)

        meta = index.data(_STYLE_META_ROLE) or {}
        color = QColor(meta.get('color') or '#FFFFFF')
        if not color.isValid():
            color = QColor('#FFFFFF')

        # Colour dot on left
        dot_x, dot_y, dot_d = rect.left() + 9, rect.center().y() - 5, 10
        painter.setPen(QPen(QColor('#9D8EB8'), 1.0))
        painter.setBrush(color)
        painter.drawEllipse(QRectF(dot_x, dot_y, dot_d, dot_d))

        # Right-aligned compact font/size hint
        hint_text = ''
        if meta:
            hint_text = f"{meta.get('font', '')} · {meta.get('size', '')}"
        hint_w = 0
        if hint_text:
            hfont = QFont(option.font)
            hfont.setPixelSize(11)
            painter.setFont(hfont)
            fm = painter.fontMetrics()
            max_hint_w = max(72, int(rect.width() * 0.44))
            elided_hint = fm.elidedText(hint_text, Qt.TextElideMode.ElideRight, max_hint_w)
            hint_w = fm.horizontalAdvance(elided_hint) + 12
            painter.setPen(QColor('#6E6285' if selected else '#8A7FA0'))
            painter.drawText(
                rect.adjusted(0, 0, -8, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                elided_hint,
            )

        # Style name on left
        name = str(index.data(Qt.ItemDataRole.DisplayRole) or '')
        nfont = QFont(option.font)
        nfont.setWeight(QFont.Weight.DemiBold if selected else QFont.Weight.Normal)
        painter.setFont(nfont)
        painter.setPen(QColor('#29213A'))
        name_rect = rect.adjusted(dot_d + 16, 0, -(hint_w + 6), 0)
        elided_name = painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, max(24, name_rect.width()))
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided_name)
        painter.restore()


class StyleManagerDialog(QDialog):
    def __init__(self, bridge, *, batch=False):
        super().__init__()
        self.bridge = bridge
        self.project = bridge.document.project
        self.revision = bridge.document.revision
        self.profiles = styles.project_profiles(self.project)
        self.library_path = bridge.storage_dir / 'subtitle-style-library.json'
        self.catalogs_path = bridge.storage_dir / 'subtitle-style-library-catalogs.json'
        self.library = styles.load_library(self.library_path)
        self._extra_catalogs = self._load_extra_catalogs()
        self._current_catalog = 'Default'
        self.ids = [r.id for r in self.project.mappings if r.id in bridge._multi_selected] or ([bridge.selected] if bridge.selected else [])

        self.setWindowTitle('字幕样式管理器')
        self.setObjectName('styleManagerDialog')
        avail = self.screen().availableGeometry() if self.screen() else None
        w = 840 if not avail else min(860, max(740, avail.width() - 40))
        h = 584 if not avail else min(600, max(500, avail.height() - 40))
        self.resize(w, h)

        root = c.layout(self)
        body = QWidget()
        body.setObjectName('carbonBody')
        stack = c.layout(body, margins=(18, 12, 18, 12), space=10)
        root.addWidget(body, 1)

        # --- Top Aegisub-style Catalog Selector Band ---
        cat_card = c.card(stack)
        cat_row = c.band(cat_card, (14, 8, 14, 8), 8)
        cat_row.addWidget(c.label('可用样式库目录', 'title'))
        self.catalog_combo = QComboBox()
        self.catalog_combo.setObjectName('styleCatalogCombo')
        self._rebuild_catalog_combo()
        self.catalog_combo.currentTextChanged.connect(self._on_catalog_changed)
        cat_row.addWidget(self.catalog_combo, 1)
        self.new_catalog_btn = c.button('新建目录', lambda: self.safe(self.new_catalog), name='newStyleCatalog')
        self.del_catalog_btn = c.button('删除目录', lambda: self.safe(self.delete_catalog), name='deleteStyleCatalog')
        cat_row.addWidget(self.new_catalog_btn)
        cat_row.addWidget(self.del_catalog_btn)

        # --- Symmetric Two-Column Workbench (Library | Current Project) ---
        columns = QHBoxLayout()
        columns.setSpacing(12)
        stack.addLayout(columns, 1)

        self.library_list = QListWidget()
        self.library_list.setObjectName('libraryStyleList')
        self.project_list = QListWidget()
        self.project_list.setObjectName('projectStyleList')
        for lst in (self.library_list, self.project_list):
            lst.setItemDelegate(_StyleItemDelegate(lst))
            lst.setMouseTracking(True)

        self.library_list.itemDoubleClicked.connect(lambda *_: self.safe(self.edit_library))
        self.project_list.itemDoubleClicked.connect(lambda *_: self.safe(self.edit))

        self.library_count = c.label('', 'count')
        self.project_count = c.label('', 'count')

        def build_order_strip(target_kind: str) -> QVBoxLayout:
            col = QVBoxLayout()
            col.setSpacing(5)
            specs = [
                ('arrow-up.svg', '上移', lambda: self._move_item(target_kind, -1)),
                ('arrow-down.svg', '下移', lambda: self._move_item(target_kind, 1)),
                ('arrow-top.svg', '置顶', lambda: self._move_item(target_kind, 'top')),
                ('arrow-bottom.svg', '置底', lambda: self._move_item(target_kind, 'bottom')),
                ('sort-az.svg', '按名称排序', lambda: self._sort_items(target_kind)),
            ]
            for icon_name, tip, slot in specs:
                btn = QPushButton()
                btn.setObjectName(f'{target_kind}Order_{icon_name[:-4]}')
                btn.setIcon(QIcon(str(_ICONS_DIR / icon_name)))
                btn.setIconSize(QSize(14, 14))
                btn.setToolTip(tip)
                btn.setAccessibleName(tip)
                btn.setFixedSize(28, 28)
                btn.setStyleSheet('QPushButton { padding: 0px; }')
                btn.setAutoDefault(False)
                btn.clicked.connect(lambda _=False, f=slot: self.safe(f))
                col.addWidget(btn)
            col.addStretch(1)
            return col

        # Left column: Reusable Library
        lib_card = QFrame()
        lib_card.setObjectName('carbonCard')
        columns.addWidget(lib_card, 1)
        lib_col = c.layout(lib_card)
        lib_head = c.band(lib_col, (14, 8, 14, 8), 8)
        lib_head.addWidget(c.label('样式库', 'title'))
        lib_head.addWidget(self.library_count)
        lib_head.addStretch(1)
        c.line(lib_col)

        lib_mid = QHBoxLayout()
        lib_mid.setContentsMargins(12, 10, 12, 8)
        lib_mid.setSpacing(8)
        lib_mid.addWidget(self.library_list, 1)
        lib_mid.addLayout(build_order_strip('library'))
        lib_col.addLayout(lib_mid, 1)

        lib_transfer = c.band(lib_col, (12, 2, 12, 6), 6)
        to_proj_btn = c.button('复制到当前工程 →', lambda: self.safe(self.to_project), 'soft', 'copyToProjectBtn')
        lib_transfer.addWidget(to_proj_btn, 1)

        lib_crud = c.band(lib_col, (12, 2, 12, 12), 6)
        for text, cb in [
            ('新建', self.create_library),
            ('编辑', self.edit_library),
            ('复制', self.duplicate_library),
            ('删除', self.delete_library),
        ]:
            lib_crud.addWidget(c.button(text, lambda _=False, f=cb: self.safe(f)), 1)

        # Right column: Current Project
        proj_card = QFrame()
        proj_card.setObjectName('carbonCard')
        columns.addWidget(proj_card, 1)
        proj_col = c.layout(proj_card)
        proj_head = c.band(proj_col, (14, 8, 14, 8), 8)
        proj_head.addWidget(c.label('当前工程', 'title'))
        proj_head.addWidget(self.project_count)
        proj_head.addStretch(1)
        c.line(proj_col)

        proj_mid = QHBoxLayout()
        proj_mid.setContentsMargins(12, 10, 12, 8)
        proj_mid.setSpacing(8)
        proj_mid.addWidget(self.project_list, 1)
        proj_mid.addLayout(build_order_strip('project'))
        proj_col.addLayout(proj_mid, 1)

        proj_transfer = c.band(proj_col, (12, 2, 12, 6), 6)
        proj_transfer.addWidget(c.button('← 复制到样式库', lambda: self.safe(self.to_library), 'soft', 'copyToLibraryBtn'), 2)
        proj_transfer.addWidget(c.button('从脚本导入…', lambda: self.safe(self.import_file)), 1)
        proj_transfer.addWidget(c.button('导出…', lambda: self.safe(self.export_file)), 1)

        proj_crud = c.band(proj_col, (12, 2, 12, 12), 6)
        for text, cb in [
            ('新建', self.create),
            ('编辑', self.edit),
            ('复制', self.duplicate),
            ('删除', self.delete),
        ]:
            proj_crud.addWidget(c.button(text, lambda _=False, f=cb: self.safe(f)), 1)

        # --- Compact Apply Bar ---
        apply_card = c.card(stack)
        apply_row = c.band(apply_card, (14, 8, 14, 8), 10)
        self.keep_overrides = QCheckBox('保留单句样式微调')
        self.keep_overrides.setObjectName('keepStyleOverrides')
        self.keep_overrides.setToolTip('勾选后保留各句已单独修改的颜色或字号；取消则统一应用所选样式。')
        self.scope = QComboBox()
        self.scope.addItem(f'应用到所选 {len(self.ids)} 句', 'selected')
        self.scope.addItem('应用到全部未锁定句', 'all')
        if not self.ids and not batch:
            self.scope.setCurrentIndex(1)
        apply_btn = c.button('应用到句子', lambda: self.safe(self.apply), 'soft', 'applyManagedStyle')
        placement_btn = c.button('字幕位置与排版…', lambda: self.safe(self.edit_placement))
        apply_row.addWidget(self.keep_overrides)
        apply_row.addStretch(1)
        apply_row.addWidget(self.scope)
        apply_row.addWidget(apply_btn)
        apply_row.addWidget(placement_btn)

        # --- Footer ---
        footer = c.footer(root)
        save_library_btn = c.button('保存样式库', lambda: self.safe(self.save_library))
        footer.addWidget(save_library_btn)
        footer.addWidget(c.label('双击任意样式可直接编辑', 'muted'), 1)
        buttons = QDialogButtonBox()
        ok_btn = buttons.addButton('保存工程样式', QDialogButtonBox.ButtonRole.AcceptRole)
        ok_btn.setProperty('styleRole', 'primary')
        buttons.addButton('取消', QDialogButtonBox.ButtonRole.RejectRole)
        buttons.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        buttons.accepted.connect(lambda: self.safe(self.save))
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)

        self.refresh()
        self._sync_catalog_buttons()
        c.theme(self)

    def _load_extra_catalogs(self) -> dict[str, dict]:
        try:
            raw = json.loads(self.catalogs_path.read_text('utf-8'))
            if isinstance(raw, dict):
                return {str(k): styles.validate_profiles(v) if isinstance(v, dict) else {} for k, v in raw.items() if str(k) != 'Default'}
        except (OSError, ValueError, TypeError):
            pass
        return {}

    def _rebuild_catalog_combo(self):
        self.catalog_combo.blockSignals(True)
        self.catalog_combo.clear()
        self.catalog_combo.addItem('Default')
        for name in sorted(self._extra_catalogs):
            self.catalog_combo.addItem(name)
        idx = max(0, self.catalog_combo.findText(self._current_catalog))
        self.catalog_combo.setCurrentIndex(idx)
        self.catalog_combo.blockSignals(False)

    def _sync_catalog_buttons(self):
        self.del_catalog_btn.setEnabled(self._current_catalog != 'Default')

    def _on_catalog_changed(self, name: str):
        if not name or name == self._current_catalog:
            return
        if self._current_catalog == 'Default':
            self._default_library_snapshot = deepcopy(self.library)
        else:
            self._extra_catalogs[self._current_catalog] = deepcopy(self.library)
        self._current_catalog = name
        if name == 'Default':
            self.library = deepcopy(getattr(self, '_default_library_snapshot', styles.load_library(self.library_path)))
        else:
            self.library = deepcopy(self._extra_catalogs.get(name, {}))
        self._sync_catalog_buttons()
        self.refresh()

    def new_catalog(self):
        name, ok = QInputDialog.getText(self, '新建样式库目录', '请输入新目录名称：')
        name = name.strip()
        if not ok or not name:
            return
        if name == 'Default' or name in self._extra_catalogs:
            raise ValueError(f'目录「{name}」已存在。')
        self._extra_catalogs[name] = {}
        self._rebuild_catalog_combo()
        self.catalog_combo.setCurrentText(name)

    def delete_catalog(self):
        if self._current_catalog == 'Default':
            raise ValueError('默认样式库目录不能删除。')
        name = self._current_catalog
        if QMessageBox.question(self, '删除样式库目录', f'确定删除样式库目录「{name}」吗？',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._extra_catalogs.pop(name, None)
        self._current_catalog = 'Default'
        self.library = deepcopy(getattr(self, '_default_library_snapshot', styles.load_library(self.library_path)))
        self._rebuild_catalog_combo()
        self._sync_catalog_buttons()
        self.refresh()

    def safe(self, callback):
        try:
            callback()
        except (ValueError, OSError, TypeError) as exc:
            QMessageBox.warning(self, '未完成', str(exc))

    def refresh(self):
        sel_proj = self.selected(self.project_list)
        sel_lib = self.selected(self.library_list)
        for widget, data, prev in [
            (self.project_list, self.profiles, sel_proj),
            (self.library_list, self.library, sel_lib),
        ]:
            widget.clear()
            for key, value in data.items():
                widget.addItem(value['name'])
                item = widget.item(widget.count() - 1)
                item.setData(Qt.ItemDataRole.UserRole, key)
                item.setData(_STYLE_META_ROLE, value.get('style', {}))
            if widget.count():
                widget.setCurrentRow(0)
            for i in range(widget.count()):
                if widget.item(i).data(Qt.ItemDataRole.UserRole) == prev:
                    widget.setCurrentRow(i)
                    break
        self.library_count.setText(f'{len(self.library)} 套')
        self.project_count.setText(f'{len(self.profiles)} 套')

    @staticmethod
    def selected(widget):
        return widget.currentItem().data(Qt.ItemDataRole.UserRole) if widget.currentItem() else ''

    def _move_item(self, target_kind: str, mode):
        mapping = self.profiles if target_kind == 'project' else self.library
        widget = self.project_list if target_kind == 'project' else self.library_list
        key = self.selected(widget)
        if not key or key not in mapping:
            return
        items = list(mapping.items())
        idx = next(i for i, (k, _) in enumerate(items) if k == key)
        if mode == 'top':
            new_idx = 0
        elif mode == 'bottom':
            new_idx = len(items) - 1
        else:
            new_idx = max(0, min(len(items) - 1, idx + int(mode)))
        if new_idx == idx:
            return
        entry = items.pop(idx)
        items.insert(new_idx, entry)
        mapping.clear()
        mapping.update(items)
        self.refresh()

    def _sort_items(self, target_kind: str):
        mapping = self.profiles if target_kind == 'project' else self.library
        items = sorted(mapping.items(), key=lambda kv: kv[1]['name'].casefold())
        mapping.clear()
        mapping.update(items)
        self.refresh()

    def create(self):
        result = edit_style(self, {'name': '新样式', 'style': default_style(self.project)}, bridge=self.bridge)
        if result:
            key = 'style-' + uuid.uuid4().hex
            self.profiles[key] = result
            self.refresh()
            self._select_key(self.project_list, key)

    def create_library(self):
        result = edit_style(self, {'name': '新库样式', 'style': default_style(self.project)}, bridge=self.bridge)
        if result:
            key = 'style-' + uuid.uuid4().hex
            self.library[key] = result
            self.refresh()
            self._select_key(self.library_list, key)

    @staticmethod
    def _select_key(widget, key):
        for i in range(widget.count()):
            if widget.item(i).data(Qt.ItemDataRole.UserRole) == key:
                widget.setCurrentRow(i)
                break

    def duplicate(self):
        key = self.selected(self.project_list)
        if not key:
            return
        value = deepcopy(self.profiles[key])
        value['name'] = value['name'][:75] + ' 副本'
        new_key = 'style-' + uuid.uuid4().hex
        self.profiles[new_key] = value
        self.refresh()
        self._select_key(self.project_list, new_key)

    def duplicate_library(self):
        key = self.selected(self.library_list)
        if not key:
            return
        value = deepcopy(self.library[key])
        value['name'] = value['name'][:75] + ' 副本'
        new_key = 'style-' + uuid.uuid4().hex
        self.library[new_key] = value
        self.refresh()
        self._select_key(self.library_list, new_key)

    def edit(self):
        key = self.selected(self.project_list)
        if not key:
            return
        result = edit_style(self, self.profiles[key], bridge=self.bridge)
        if result:
            self.profiles[key] = result
            self.refresh()

    def delete(self):
        key = self.selected(self.project_list)
        if not key:
            return
        if len(self.profiles) <= 1:
            raise ValueError('工程至少保留一套样式。')
        del self.profiles[key]
        self.refresh()

    def to_library(self):
        key = self.selected(self.project_list)
        if key:
            new_key = 'style-' + uuid.uuid4().hex
            self.library[new_key] = deepcopy(self.profiles[key])
            self.refresh()
            self._select_key(self.library_list, new_key)

    def to_project(self):
        key = self.selected(self.library_list)
        if key:
            new_key = 'style-' + uuid.uuid4().hex
            self.profiles[new_key] = deepcopy(self.library[key])
            self.refresh()
            self._select_key(self.project_list, new_key)

    def edit_library(self):
        key = self.selected(self.library_list)
        if not key:
            return
        result = edit_style(self, self.library[key], bridge=self.bridge)
        if result:
            self.library[key] = result
            self.refresh()

    def delete_library(self):
        key = self.selected(self.library_list)
        if key:
            del self.library[key]
            self.refresh()

    def import_file(self):
        path, _ = QFileDialog.getOpenFileName(self, '从字幕或样式库导入', '', '样式文件 (*.ass *.ssa *.json)')
        if path:
            for value in styles.import_styles(path).values():
                self.profiles['style-' + uuid.uuid4().hex] = value
            self.refresh()

    def export_file(self):
        path, _ = QFileDialog.getSaveFileName(self, '导出工程样式库', 'Lyria-styles.json', '样式库 (*.json)')
        if path:
            styles.save_library(path, self.profiles, self.bridge._protected_paths())

    def save_library(self):
        if self._current_catalog == 'Default':
            default_lib = self.library
        else:
            self._extra_catalogs[self._current_catalog] = deepcopy(self.library)
            default_lib = getattr(self, '_default_library_snapshot', styles.load_library(self.library_path))
        styles.save_library(self.library_path, default_lib)
        if self._extra_catalogs or self.catalogs_path.exists():
            atomic_json(self.catalogs_path, self._extra_catalogs)
        QMessageBox.information(self, '已保存', '可复用样式库已保存。工程样式仍需点击「保存工程样式」确认写入。')

    def edit_placement(self):
        self.check_revision()
        from .dialogs import subtitle_style
        if subtitle_style(self.bridge, self):
            self.revision = self.bridge.document.revision

    def check_revision(self):
        if self.bridge.document.project is not self.project or self.bridge.document.revision != self.revision:
            raise ValueError('工程已改变，请关闭后重新打开样式管理。')

    def save(self):
        self.check_revision()
        styles.save_project_profiles(self.project, self.bridge.history, self.profiles, self.selected(self.project_list))
        self.bridge._changed()
        self.accept()

    def apply(self):
        self.check_revision()
        ids = self.ids if self.scope.currentData() == 'selected' else [r.id for r in self.project.mappings if r.id not in self.project.settings.get('manual_locked_ids', [])]
        if not ids:
            raise ValueError('此范围没有可应用样式的句子。')
        locked = self.project.settings.get('manual_locked_ids', [])
        if any(i in locked for i in ids):
            raise ValueError('所选句包含锁定句，请先解锁。')
        policy = '保留个人微调' if self.keep_overrides.isChecked() else '清除个人微调'
        if QMessageBox.question(self, '确认应用范围', f'将当前命名样式关联到 {len(ids)} 句，{policy}。说话人不变，可撤销。是否继续？') != QMessageBox.StandardButton.Yes:
            return
        identity = self.selected(self.project_list)
        styles.save_project_profiles(
            self.project, self.bridge.history, self.profiles, identity,
            apply_ids=ids, clear_overrides=not self.keep_overrides.isChecked(),
        )
        self.bridge._changed()
        self.accept()


def edit_style(parent, value, *, bridge=None):
    from PySide6.QtWidgets import QColorDialog
    from .font_preview_widget import DEFAULT_SAMPLES, FontPreviewWidget
    dialog = QDialog(parent)
    dialog.setObjectName('editStyleWorkbenchDialog')
    dialog.setWindowTitle('编辑字幕样式')
    dialog.resize(980, 640)
    root = c.layout(dialog)
    body = QWidget(); body.setObjectName('carbonBody')
    outer = c.layout(body, False, (16, 14, 16, 14), 14)
    root.addWidget(body, 1)

    left_card = QFrame(); left_card.setObjectName('carbonCard')
    left_card.setMaximumWidth(316); left_card.setMinimumWidth(264)
    layout = c.layout(left_card, True, (14, 14, 14, 14), 10)
    c.header(layout, '样式参数', '字体 · 字号 · 主色')

    form_host = QWidget()
    form = QFormLayout(form_host)
    form.setContentsMargins(0, 8, 0, 4)
    form.setVerticalSpacing(10)
    layout.addWidget(form_host)

    name = QLineEdit(value['name']); name.setMaxLength(80)
    font = QFontComboBox(); font.setCurrentFont(QFont(value['style']['font']))
    size = QSpinBox(); size.setRange(8, 512); size.setValue(value['style']['size'])
    color = QLineEdit(value['style']['color']); color.setMaxLength(7)
    color_pick = QPushButton('取色')
    color_row = QHBoxLayout(); color_row.addWidget(color, 1); color_row.addWidget(color_pick)
    bold = QCheckBox('粗体'); bold.setChecked(value['style']['bold'])
    italic = QCheckBox('斜体'); italic.setChecked(value['style']['italic'])
    for label, widget in [('名称', name), ('字体', font), ('字号', size), ('主颜色', color_row), ('', bold), ('', italic)]:
        form.addRow(label, widget)

    hint = c.label('在右侧瀑布流中点击任意字体卡片即可实时同步。', 'muted', True)
    layout.addWidget(hint)
    layout.addStretch(1)

    sample_text = DEFAULT_SAMPLES['zh']
    storage_dir = bridge.storage_dir if bridge is not None else None
    if bridge is not None and bridge.selected:
        try:
            from lyricsync.editing import mapping_text
            sample_text = mapping_text(bridge.document.project, bridge._row(bridge.selected)).replace('\n', ' ') or sample_text
        except Exception:
            pass

    preview = FontPreviewWidget(
        dialog,
        initial_font=value['style']['font'],
        initial_sample=sample_text,
        initial_size=min(48, max(20, int(value['style']['size']))),
        initial_color=value['style']['color'],
        initial_bold=value['style']['bold'],
        initial_italic=value['style']['italic'],
        storage_dir=storage_dir,
        show_appearance_controls=False,
    )

    def sync_to_preview():
        preview.set_appearance(
            font=font.currentFont().family(),
            color=color.text(),
            bold=bold.isChecked(),
            italic=italic.isChecked(),
        )

    def on_preview_font_clicked(family):
        font.blockSignals(True)
        font.setCurrentFont(QFont(family))
        font.blockSignals(False)

    def pick_color():
        chosen = QColorDialog.getColor(QColor(color.text()), dialog, '选择字幕主颜色')
        if chosen.isValid():
            color.setText(chosen.name().upper())

    color_pick.clicked.connect(pick_color)
    font.currentFontChanged.connect(lambda _: sync_to_preview())
    color.textChanged.connect(lambda _: sync_to_preview())
    bold.toggled.connect(lambda _: sync_to_preview())
    italic.toggled.connect(lambda _: sync_to_preview())
    preview.fontSelected.connect(on_preview_font_clicked)

    result = None

    def accept():
        nonlocal result
        try:
            profile = {
                'name': name.text(),
                'style': dict(
                    font=font.currentFont().family(),
                    size=size.value(),
                    color=color.text(),
                    bold=bold.isChecked(),
                    italic=italic.isChecked(),
                ),
            }
            result = styles.validate_profiles({'edit': profile})['edit']
            dialog.accept()
        except ValueError as exc:
            QMessageBox.warning(dialog, '样式无效', str(exc))

    outer.addWidget(left_card)
    outer.addWidget(preview, 1)

    footer = c.footer(root)
    footer.addStretch(1)
    buttons = QDialogButtonBox()
    ok_btn = buttons.addButton('确定', QDialogButtonBox.ButtonRole.AcceptRole)
    ok_btn.setProperty('styleRole', 'primary')
    buttons.addButton('取消', QDialogButtonBox.ButtonRole.RejectRole)
    buttons.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    buttons.accepted.connect(accept)
    buttons.rejected.connect(dialog.reject)
    footer.addWidget(buttons)

    c.theme(dialog)
    dialog.exec()
    return result


def edit_current_style(bridge, region_id=None):
    from .review_actions import guard, selected_ids
    ids = selected_ids(bridge) if region_id is None else None
    project = bridge.document.project
    revision = bridge.document.revision
    profiles = styles.project_profiles(project)
    current = styles.current_style_id(project)
    if region_id is None:
        guard(bridge, ids)
        selected_style = bridge._row(bridge.selected).style_id
    else:
        if bridge.stage != 'import' or bridge._candidate_job or bridge._task.get('running'):
            raise ValueError('请在导入页空闲时编辑选区样式。')
        region = next((r for r in bridge.state['regionCards'] if r['id'] == region_id), None)
        if region is None:
            raise ValueError('选区已改变，请重新选择。')
        selected_style = region['style']
    identity = selected_style if selected_style in profiles else current
    result = edit_style(None, deepcopy(profiles[identity]), bridge=bridge)
    if result is None:
        return
    if (bridge.document.project is not project or bridge.document.revision != revision
            or ids is not None and selected_ids(bridge) != ids):
        raise ValueError('编辑期间工程或所选句已改变，请重新打开当前样式。')
    if ids is not None:
        guard(bridge, ids)
    profiles[identity] = result
    styles.save_project_profiles(project, bridge.history, profiles, current)
    bridge._changed()


def show_style_manager(bridge, *, batch=False):
    if bridge._editor_composing:
        raise ValueError('请先完成正在输入的文字。')
    bridge.history.end()
    return StyleManagerDialog(bridge, batch=batch).exec()
