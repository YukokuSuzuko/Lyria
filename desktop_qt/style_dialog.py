"""Independent project style definitions and reusable library with explicit scope."""
from copy import deepcopy
import uuid
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QListWidget, QLineEdit, QFontComboBox, QSpinBox, QCheckBox, QPushButton,
    QDialogButtonBox, QMessageBox, QFileDialog, QComboBox, QWidget)
from lyricsync import styles
from lyricsync.speakers import default_style, validate_style
from .native_theme import apply_dialog_theme


class StyleManagerDialog(QDialog):
    def __init__(self, bridge, *, batch=False):
        super().__init__()
        self.bridge = bridge
        self.project = bridge.document.project
        self.revision = bridge.document.revision
        self.profiles = styles.project_profiles(self.project)
        self.library_path = bridge.storage_dir / 'subtitle-style-library.json'
        self.library = styles.load_library(self.library_path)
        self.ids = [r.id for r in self.project.mappings if r.id in bridge._multi_selected] or ([bridge.selected] if bridge.selected else [])
        self.setWindowTitle('字幕样式管理'); self.resize(760, 560)
        self.setObjectName('styleManagerDialog')
        root = QVBoxLayout(self)
        note = QLabel('工程样式与可复用样式库分别保存。修改工程命名样式会联动所有引用句（含锁定句），个人覆盖属性保留；人物身份独立不变。')
        note.setWordWrap(True); root.addWidget(note)
        columns = QHBoxLayout(); root.addLayout(columns)
        self.project_list = QListWidget(); self.project_list.setObjectName('projectStyleList')
        self.library_list = QListWidget(); self.library_list.setObjectName('libraryStyleList')
        for title, widget in [('当前工程', self.project_list), ('可复用样式库', self.library_list)]:
            box = QVBoxLayout(); box.addWidget(QLabel(title)); box.addWidget(widget); columns.addLayout(box)
        actions = QHBoxLayout(); root.addLayout(actions)
        for text, callback in [('新建', self.create), ('复制', self.duplicate), ('修改', self.edit), ('删除', self.delete),
                               ('工程 → 样式库', self.to_library), ('样式库 → 工程', self.to_project), ('导入', self.import_file), ('导出', self.export_file)]:
            b = QPushButton(text); b.clicked.connect(lambda checked=False, f=callback: self.safe(f)); actions.addWidget(b)
        library_actions = QHBoxLayout(); root.addLayout(library_actions)
        for text, callback in [('修改库样式', self.edit_library), ('删除库样式', self.delete_library)]:
            button = QPushButton(text); button.clicked.connect(lambda checked=False, f=callback: self.safe(f)); library_actions.addWidget(button)
        self.scope = QComboBox(); self.scope.addItem(f'应用到当前所选 {len(self.ids)} 句', 'selected'); self.scope.addItem('应用到全部未锁定句子', 'all')
        self.keep_overrides = QCheckBox('保留所选句的个人样式覆盖（含旧版逐句样式）')
        self.keep_overrides.setObjectName('keepStyleOverrides')
        root.addWidget(self.keep_overrides)
        applyrow = QHBoxLayout(); applyrow.addWidget(self.scope)
        b = QPushButton('保存工程样式并应用'); b.setObjectName('applyManagedStyle'); b.clicked.connect(lambda: self.safe(self.apply)); applyrow.addWidget(b); root.addLayout(applyrow)
        if not self.ids and not batch: self.scope.setCurrentIndex(1)
        info = QLabel('支持字体、字号、主颜色、粗体与斜体。删除被引用样式后，句子保留当前外观并脱离该样式；可撤销。应用时默认清除个人覆盖，勾选上方选项可保留。ASS/SSA 导入仅提取这五项，不保证边框、阴影、位置和特效。此处导出为 Lyria 样式库 JSON。')
        info.setWordWrap(True); root.addWidget(info)
        footer = QHBoxLayout(); root.addLayout(footer)
        save_library = QPushButton('保存样式库'); save_library.clicked.connect(lambda: self.safe(self.save_library)); footer.addWidget(save_library)
        placement = QPushButton('字幕位置与排版…')
        placement.clicked.connect(lambda: self.safe(self.edit_placement))
        footer.addWidget(placement)
        buttons = QDialogButtonBox()
        buttons.addButton('保存工程样式', QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton('取消', QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(lambda: self.safe(self.save)); buttons.rejected.connect(self.reject); footer.addWidget(buttons)
        self.refresh(); apply_dialog_theme(self)

    def safe(self, callback):
        try: callback()
        except (ValueError, OSError, TypeError) as exc: QMessageBox.warning(self, '未完成', str(exc))

    def refresh(self):
        selected = self.selected(self.project_list)
        for widget, data in [(self.project_list, self.profiles), (self.library_list, self.library)]:
            widget.clear()
            for key, value in data.items():
                widget.addItem(value['name']); widget.item(widget.count()-1).setData(Qt.ItemDataRole.UserRole, key)
            widget.setCurrentRow(0)
        for i in range(self.project_list.count()):
            if self.project_list.item(i).data(Qt.ItemDataRole.UserRole) == selected: self.project_list.setCurrentRow(i)

    @staticmethod
    def selected(widget):
        return widget.currentItem().data(Qt.ItemDataRole.UserRole) if widget.currentItem() else ''

    def create(self):
        result = edit_style(self, {'name': '新样式', 'style': default_style(self.project)})
        if result: self.profiles['style-'+uuid.uuid4().hex] = result; self.refresh()

    def duplicate(self):
        key = self.selected(self.project_list)
        if not key: return
        value = deepcopy(self.profiles[key]); value['name'] = value['name'][:75] + ' 副本'
        self.profiles['style-'+uuid.uuid4().hex] = value; self.refresh()

    def edit(self):
        key = self.selected(self.project_list)
        if not key: return
        result = edit_style(self, self.profiles[key])
        if result: self.profiles[key] = result; self.refresh()

    def delete(self):
        key = self.selected(self.project_list)
        if not key: return
        if len(self.profiles) <= 1: raise ValueError('工程至少保留一套样式。')
        del self.profiles[key]; self.refresh()

    def to_library(self):
        key = self.selected(self.project_list)
        if key: self.library['style-'+uuid.uuid4().hex] = deepcopy(self.profiles[key]); self.refresh()

    def to_project(self):
        key = self.selected(self.library_list)
        if key: self.profiles['style-'+uuid.uuid4().hex] = deepcopy(self.library[key]); self.refresh()

    def edit_library(self):
        key = self.selected(self.library_list)
        if not key: return
        result = edit_style(self, self.library[key])
        if result: self.library[key] = result; self.refresh()

    def delete_library(self):
        key = self.selected(self.library_list)
        if key: del self.library[key]; self.refresh()

    def import_file(self):
        path, _ = QFileDialog.getOpenFileName(self, '从字幕或样式库导入', '', '样式文件 (*.ass *.ssa *.json)')
        if path:
            for value in styles.import_styles(path).values(): self.profiles['style-'+uuid.uuid4().hex] = value
            self.refresh()

    def export_file(self):
        path, _ = QFileDialog.getSaveFileName(self, '导出工程样式库', 'Lyria-styles.json', '样式库 (*.json)')
        if path: styles.save_library(path, self.profiles, self.bridge._protected_paths())

    def save_library(self):
        styles.save_library(self.library_path, self.library)
        QMessageBox.information(self, '已保存', '可复用样式库已保存。工程样式仍需单独保存。')

    def edit_placement(self):
        self.check_revision()
        from .dialogs import subtitle_style
        if subtitle_style(self.bridge,self):
            self.revision=self.bridge.document.revision

    def check_revision(self):
        if self.bridge.document.project is not self.project or self.bridge.document.revision != self.revision:
            raise ValueError('工程已改变，请关闭后重新打开样式管理。')

    def save(self):
        self.check_revision()
        styles.save_project_profiles(self.project, self.bridge.history, self.profiles, self.selected(self.project_list))
        self.bridge._changed(); self.accept()

    def apply(self):
        self.check_revision()
        ids = self.ids if self.scope.currentData() == 'selected' else [r.id for r in self.project.mappings if r.id not in self.project.settings.get('manual_locked_ids', [])]
        if not ids: raise ValueError('此范围没有可应用样式的句子。')
        locked = self.project.settings.get('manual_locked_ids', [])
        if any(i in locked for i in ids): raise ValueError('所选句包含锁定句，请先解锁。')
        policy = '保留个人覆盖属性' if self.keep_overrides.isChecked() else '清除个人覆盖属性'
        if QMessageBox.question(self, '确认应用范围', f'将当前命名样式关联到 {len(ids)} 句，{policy}。说话人不变，可撤销。是否继续？') != QMessageBox.StandardButton.Yes: return
        identity = self.selected(self.project_list)
        styles.save_project_profiles(self.project, self.bridge.history, self.profiles, identity,
                                     apply_ids=ids, clear_overrides=not self.keep_overrides.isChecked())
        self.bridge._changed(); self.accept()


def edit_style(parent, value):
    dialog = QDialog(parent); dialog.setWindowTitle('编辑字幕样式'); layout = QVBoxLayout(dialog); form = QFormLayout(); layout.addLayout(form)
    name = QLineEdit(value['name']); name.setMaxLength(80)
    font = QFontComboBox(); font.setCurrentFont(QFont(value['style']['font']))
    size = QSpinBox(); size.setRange(8, 512); size.setValue(value['style']['size'])
    color = QLineEdit(value['style']['color']); color.setMaxLength(7)
    bold = QCheckBox('粗体'); bold.setChecked(value['style']['bold'])
    italic = QCheckBox('斜体'); italic.setChecked(value['style']['italic'])
    for label, widget in [('名称',name),('字体',font),('字号',size),('主颜色 #RRGGBB',color),('',bold),('',italic)]: form.addRow(label,widget)
    result = None
    def accept():
        nonlocal result
        try:
            profile = {'name': name.text(), 'style': dict(font=font.currentFont().family(),size=size.value(),color=color.text(),bold=bold.isChecked(),italic=italic.isChecked())}
            result = styles.validate_profiles({'edit':profile})['edit']; dialog.accept()
        except ValueError as exc: QMessageBox.warning(dialog,'样式无效',str(exc))
    buttons=QDialogButtonBox(); buttons.addButton('确定', QDialogButtonBox.ButtonRole.AcceptRole); buttons.addButton('取消', QDialogButtonBox.ButtonRole.RejectRole); buttons.accepted.connect(accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
    apply_dialog_theme(dialog); dialog.exec(); return result


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
        if region is None: raise ValueError('选区已改变，请重新选择。')
        selected_style = region['style']
    identity = selected_style if selected_style in profiles else current
    result = edit_style(None, deepcopy(profiles[identity]))
    if result is None:
        return
    if (bridge.document.project is not project or bridge.document.revision != revision
            or ids is not None and selected_ids(bridge) != ids):
        raise ValueError('编辑期间工程或所选句已改变，请重新打开当前样式。')
    if ids is not None: guard(bridge, ids)
    profiles[identity] = result
    styles.save_project_profiles(project, bridge.history, profiles, current)
    bridge._changed()


def show_style_manager(bridge, *, batch=False):
    if bridge._editor_composing: raise ValueError('请先完成正在输入的文字。')
    bridge.history.end()
    return StyleManagerDialog(bridge,batch=batch).exec()
