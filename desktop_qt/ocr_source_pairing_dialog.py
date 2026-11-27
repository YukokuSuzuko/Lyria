"""OCR evidence and translated rows share a scroll surface; imported lyrics are an independent pool."""
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt, QEvent, QTimer
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QDialog, QFileDialog,
    QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QMenu, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QFrame,
    QGridLayout, QPlainTextEdit)

from lyricsync.ocr_correspondence import reference_lyrics
from lyricsync.ocr_source_pairing import (edit_source_pair, fill_ocr_placeholders, imported_translations,
    link_ids, source_links, source_text, suggest_source_links)
from lyricsync.model import valid_time

ROLE = Qt.ItemDataRole.UserRole


class SourcePool(QListWidget):
    def selected_ids(self):
        return [self.item(i).data(ROLE) for i in range(self.count()) if self.item(i).isSelected() and not self.item(i).isHidden()
                and self.item(i).flags() & Qt.ItemFlag.ItemIsEnabled]

    def mousePressEvent(self, event):
        before = self.selected_ids()
        item = self.itemAt(event.position().toPoint())
        retain = (item is not None and item.isSelected() and not event.modifiers())
        super().mousePressEvent(event)
        if (event.button() == Qt.MouseButton.LeftButton and item is not None
                and item.flags() & Qt.ItemFlag.ItemIsEnabled):
            self.window().arm_drag(event.globalPosition().toPoint(), before if retain else self.selected_ids())


def label(text, name):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setObjectName(name)
    return widget


def frame(name):
    widget = QFrame()
    widget.setObjectName(name)
    return widget


DESK_STYLE = """
QDialog#ocrSourcePairingDialog { background: #f6f4fa; }
QFrame#ocrSubbar {
    background: #ffffff; border: 0; border-bottom: 1px solid #e6e0f2;
}
QFrame#syncPanel, QFrame#sourcePanel {
    background: #ffffff; border: 1px solid #dfd8eb; border-radius: 10px;
}
QFrame#panelHead {
    background: #faf8fd; border: 0; border-bottom: 1px solid #ebe5f5;
    border-top-left-radius: 9px; border-top-right-radius: 9px;
}
QFrame#poolFooter {
    background: #faf8fd; border: 0; border-top: 1px solid #ebe5f5;
    border-bottom-left-radius: 9px; border-bottom-right-radius: 9px;
}
QFrame#carbonLineV { background: #e6e0f2; border: 0; min-width: 1px; max-width: 1px; }
QLabel#heading, QLabel#translation { font-size: 13px; font-weight: 600; color: #29213a; }
QLabel#stamp, QLabel#statistics, QLabel#note { color: #736985; font-size: 11.5px; }
QLabel#stamp { color: #8c829e; font-size: 11px; }
QLabel#ocrReadOnlyPill {
    color: #5e3b9e; background: #f0eafa; border: 1px solid #dfd2f3;
    border-radius: 11px; padding: 0px 9px; font-size: 11px; font-weight: 600;
}
QLabel#ocrHeadPill {
    color: #6b6182; background: #efebf8; border-radius: 10px;
    padding: 0px 8px; font-size: 11px; font-weight: 600;
}
QLabel#ocrCountPill {
    color: #1e7a56; background: #e6f5ee; border: 1px solid #bee6d2;
    border-radius: 10px; padding: 0px 8px; font-size: 11px; font-weight: 600;
}
QLabel#badge {
    color: #7c7392; background: #f2eff9; border: 1px solid #e5dff2;
    border-radius: 10px; padding: 0px 8px; font-size: 11px; font-weight: 600;
}
QLabel#badge[paired="true"] { color: #1e7a56; background: #e6f5ee; border-color: #bee6d2; }
QLabel#badge[empty="true"] { color: #a299b5; background: #f6f4fa; border-color: #ece7f4; }
QLabel#badge[current="true"] { color: #ffffff; background: #63419c; border-color: #63419c; }
QFrame#evidenceCell {
    background: #faf8fd; border: 0; border-right: 1px solid #efeaf7; border-bottom: 1px solid #efeaf7;
}
QFrame#targetCell {
    background: #ffffff; border: 0; border-bottom: 1px solid #efeaf7;
}
QFrame#evidenceCell[current="true"] { background: #f6f2fd; }
QFrame#targetCell[current="true"] {
    background: #f5f0fc; border-left: 3px solid #63419c;
}
QLabel#dropZone {
    color: #8c829e; background: #fcfbfe; border: 1px dashed #d7cce9;
    border-radius: 6px; padding: 5px 8px; font-size: 11.5px;
}
QLabel#dropZone[current="true"] {
    color: #5e3b9e; background: #faf7ff; border: 1.5px dashed #8e6bc9; font-weight: 500;
}
QFrame#pairedMember {
    background: #ffffff; border: 1px solid #ddd3ee; border-radius: 6px;
}
QTableWidget#ocrSourcePairingTable, QListWidget#ocrImportedSources {
    border: none; background: transparent;
}
QTableWidget#ocrSourcePairingTable::item { padding: 0px; margin: 0px; border: none; }
QListWidget#ocrImportedSources { padding: 4px 6px; }
QListWidget#ocrImportedSources::item {
    background: #ffffff; border: 1px solid #e2dbe9; border-radius: 7px; padding: 7px 9px; color: #2e263a;
}
QListWidget#ocrImportedSources::item:hover { background: #faf8fe; border-color: #cfc1e8; }
QListWidget#ocrImportedSources::item:selected {
    background: #f1ebfc; border: 2px solid #7c58c2; color: #29213a;
}
QListWidget#ocrImportedSources::item:disabled {
    color: #9a92aa; background: #f8f7fb; border-color: #ece8f3;
}
QPushButton[small="true"] {
    background: transparent; border: 1px solid transparent; border-radius: 4px;
    padding: 1px 6px; min-height: 16px; color: #63419c; font-size: 11.5px;
}
QPushButton[small="true"]:hover { background: #f2ecfa; }
QPushButton#ocrPairSelected:enabled, QPushButton#ocrPairApply:enabled {
    background: #5e3b9e; color: #ffffff; border: 1px solid #5e3b9e; font-weight: 600;
}
QPushButton#ocrPairSelected:enabled:hover, QPushButton#ocrPairApply:enabled:hover {
    background: #4f2f8a; border-color: #4f2f8a;
}
QPushButton#ocrPairAuto {
    background: #f0eafa; color: #5e3b9e; border: 1px solid #d9ccf0; font-weight: 600;
}
QPushButton#ocrPairAuto:hover { background: #e8def9; border-color: #c6b4ea; }
"""


def mark_current(widget, active):
    widget.setProperty('current', active)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class Targets(QTableWidget):
    def __init__(self, desk):
        super().__init__(0, 2, desk)
        self.horizontalHeader().hide()
        self.verticalHeader().hide()
        self.horizontalHeader().setStretchLastSection(True)
        self.setShowGrid(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setColumnWidth(0, int(self.viewport().width() * .41))
        self.fit_rows()

    def fit_rows(self, rows=None):
        for row in range(self.rowCount()) if rows is None else rows:
            heights = []
            for col in (0, 1):
                cell = self.cellWidget(row, col)
                if cell:
                    cell.ensurePolished()
                    layout = cell.layout()
                    layout.invalidate()
                    width = max(60, self.columnWidth(col) - 4)
                    height = layout.totalHeightForWidth(width)
                    heights.append(max(layout.minimumSize().height(), height if height >= 0 else layout.sizeHint().height()))
            if heights:
                self.setRowHeight(row, max(heights) + 6)


class OcrSourcePairingDialog(QDialog):
    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.identity, self.revision = bridge.document.id, bridge.document.revision
        self.original = self.draft = deepcopy(bridge.document.project)
        self.past, self.future, self.hints = [], [], {}
        self.setObjectName('ocrSourcePairingDialog')
        from lyricsync.translation_pool import pairing_label
        self.setWindowTitle(pairing_label(self.draft, entry_mode='ocr'))
        geometry = self.screen().availableGeometry()
        self.resize(min(1160, geometry.width() - 24), min(720, geometry.height() - 24))
        from PySide6.QtWidgets import QSizePolicy
        from . import carbon_widgets as c
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        subbar = frame('ocrSubbar')
        tools = QHBoxLayout(subbar)
        tools.setContentsMargins(16, 8, 16, 8)
        tools.setSpacing(8)
        layout.addWidget(subbar)
        hint = label('对照左侧画面识别结果，双击、回车或拖拽右侧原文即可配给对应译文（支持多句合并）。', 'note')
        hint.setWordWrap(True)
        tools.addWidget(hint, 1)
        from PySide6.QtGui import QIcon
        from PySide6.QtCore import QSize
        auto_btn = self.button(tools, '一键按 OCR 配对', self.auto_pair, 'ocrPairAuto')
        auto_btn.setIcon(QIcon(str(Path(__file__).parent / 'assets' / 'icons' / 'spark.svg')))
        auto_btn.setIconSize(QSize(14, 14))
        self.clear_button = self.button(tools, '清空配对', self.clear_pairs, 'ocrPairClear')
        vline = frame('carbonLineV')
        vline.setFixedHeight(18)
        tools.addWidget(vline)
        self.undo = self.button(tools, '撤销', lambda: self.history(False), 'ocrPairUndo')
        self.redo = self.button(tools, '重做', lambda: self.history(True), 'ocrPairRedo')
        self.undo.setProperty('styleRole', 'quiet')
        self.redo.setProperty('styleRole', 'quiet')
        columns = QHBoxLayout()
        columns.setContentsMargins(14, 11, 14, 11)
        columns.setSpacing(12)
        layout.addLayout(columns, 1)
        sync_panel = frame('syncPanel')
        left = QVBoxLayout(sync_panel)
        left.setContentsMargins(1, 1, 1, 1)
        left.setSpacing(0)
        columns.addWidget(sync_panel, 65)
        sync_head_bar = frame('panelHead')
        sync_head_bar.setFixedHeight(40)
        heads = QGridLayout(sync_head_bar)
        heads.setContentsMargins(12, 0, 12, 0)
        heads.setColumnStretch(0, 41)
        heads.setColumnStretch(1, 59)
        left.addWidget(sync_head_bar)
        evidence_head = QHBoxLayout()
        evidence_head.setSpacing(6)
        evidence_head.addWidget(label('OCR 识别参考', 'heading'))
        ev_pill = label('只读', 'ocrHeadPill')
        ev_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ev_pill.setFixedHeight(20)
        ev_pill.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        evidence_head.addWidget(ev_pill)
        evidence_head.addStretch()
        heads.addLayout(evidence_head, 0, 0)
        targets_head = QHBoxLayout()
        targets_head.setSpacing(6)
        targets_head.addWidget(label('译文 · 配对目标', 'heading'))
        self.paired_count = label('', 'ocrCountPill')
        self.paired_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.paired_count.setFixedHeight(20)
        self.paired_count.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        targets_head.addWidget(self.paired_count)
        targets_head.addStretch()
        self.next_button = self.button(targets_head, '跳到下一条待配 ↓', self.next_pending, 'ocrPairNext')
        self.next_button.setProperty('styleRole', 'link')
        self.import_translation = self.button(targets_head, '导入译文…', self.import_translations, 'ocrPairImportTranslation')
        self.import_translation.setProperty('styleRole', 'link')
        heads.addLayout(targets_head, 0, 1)
        self.table = Targets(self)
        self.table.setObjectName('ocrSourcePairingTable')
        self.table.setHorizontalHeaderLabels(['OCR 识别参考（只读）', '译文 · 配对目标'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.menu_at)
        left.addWidget(self.table, 1)
        source_panel = frame('sourcePanel')
        right = QVBoxLayout(source_panel)
        right.setContentsMargins(1, 1, 1, 1)
        right.setSpacing(0)
        columns.addWidget(source_panel, 35)
        source_head_bar = frame('panelHead')
        source_head_bar.setFixedHeight(40)
        source_header = QHBoxLayout(source_head_bar)
        source_header.setContentsMargins(12, 0, 10, 0)
        source_header.setSpacing(6)
        source_header.addWidget(label('导入原文', 'heading'))
        self.pool_count = label('', 'ocrHeadPill')
        self.pool_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pool_count.setFixedHeight(20)
        self.pool_count.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        source_header.addWidget(self.pool_count)
        source_header.addStretch()
        self.hide_used = QCheckBox('隐藏已配')
        self.hide_used.setObjectName('ocrHideUsedSources')
        source_header.addWidget(self.hide_used)
        import_src_btn = self.button(source_header, '导入原文…', self.import_sources, 'ocrPairImportSources')
        import_src_btn.setProperty('styleRole', 'link')
        right.addWidget(source_head_bar)
        self.pool = SourcePool(self)
        self.pool.setObjectName('ocrImportedSources')
        self.pool.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.pool.setSpacing(4)
        self.pool.setWordWrap(True)
        self.pool.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right.addWidget(self.pool, 1)
        pool_bar = frame('poolFooter')
        pool_actions = QHBoxLayout(pool_bar)
        pool_actions.setContentsMargins(10, 8, 10, 8)
        pool_actions.setSpacing(8)
        guide = label('双击单句；Ctrl / Shift 多选后按 Enter', 'note')
        guide.setWordWrap(True)
        pool_actions.addWidget(guide, 1)
        self.bind_button = self.button(pool_actions, '配给当前目标', self.bind_selected, 'ocrPairSelected')
        self.bind_button.setProperty('styleRole', 'primary')
        right.addWidget(pool_bar)
        self.drag_origin = None
        self.drag_ids = []
        self.dragging = False
        self.active_row = -1
        self.editor = None
        self.hide_used.toggled.connect(self.filter_pool)
        self.pool.itemSelectionChanged.connect(self.update_bind_button)
        self.table.currentCellChanged.connect(self.update_target)
        self.pool.itemDoubleClicked.connect(lambda item: self.bind(self.target(), [item.data(ROLE)]))
        self.pool.installEventFilter(self)
        self.table.installEventFilter(self)
        footer_bar = frame('carbonFooter')
        footer = QHBoxLayout(footer_bar)
        footer.setContentsMargins(16, 9, 16, 9)
        footer.setSpacing(8)
        layout.addWidget(footer_bar)
        self.status = label('', 'statistics')
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        draft_note = label('本窗口为独立草案 · 应用到主轴后可一次撤销', 'note')
        footer.addWidget(draft_note)
        self.back_to_draft = self.button(footer, '继续编辑', self.keep_draft, 'ocrPairKeepDraft')
        self.discard = self.button(footer, '放弃并关闭', self.reject, 'ocrPairDiscard')
        self.back_to_draft.hide()
        self.discard.hide()
        self.button(footer, '关闭', self.reject, 'ocrPairClose')
        self.apply_button = self.button(footer, '应用到主轴', self.apply, 'ocrPairApply')
        self.apply_button.setProperty('styleRole', 'primary')
        for key, redo in [(QKeySequence.StandardKey.Undo, False), (QKeySequence.StandardKey.Redo, True)]:
            shortcut = QShortcut(key, self)
            shortcut.activated.connect(lambda r=redo: self.history(r))
        c.theme(self)
        self.setStyleSheet(self.styleSheet() + DESK_STYLE)
        self.refresh()

    def button(self, layout, label, action, name):
        b = QPushButton(label, self)
        b.setObjectName(name)
        b.setAutoDefault(False)
        b.setDefault(False)
        b.installEventFilter(self)
        b.clicked.connect(action)
        layout.addWidget(b)
        return b

    def target(self):
        item = self.table.item(self.table.currentRow(), 1)
        return item.data(ROLE) if item else None

    def update_bind_button(self):
        count = len(self.pool.selected_ids())
        target = self.target()
        row = self.table.currentRow() + 1
        if not target:
            caption = '请选择译文'
        elif count > 1:
            caption = f'合并 {count} 句配给 #{row:02}'
        else:
            caption = f'配给 #{row:02}'
        self.bind_button.setText(caption)
        self.bind_button.setEnabled(self.editor is None and bool(target and count))

    def update_target(self, row=None, *_):
        if row is None:
            row = self.table.currentRow()
        if self.editor is not None and row != self.edit_row:
            self.table.setCurrentCell(self.edit_row, 1)
            return
        for i in {self.active_row, row}:
            if i < 0:
                continue
            middle = self.table.cellWidget(i, 1)
            evidence = self.table.cellWidget(i, 0)
            if not middle or not evidence:
                continue
            active = i == row and bool(self.target())
            mark_current(middle, active)
            mark_current(evidence, active)
            badge = middle.findChild(QLabel, 'badge')
            badge.setText('当前目标' if active else badge.property('restingText'))
            mark_current(badge, active)
            zone = middle.findChild(QLabel, 'dropZone')
            if zone:
                mark_current(zone, active)
                zone.setText('← 双击右栏、按 Enter 或拖入原文' if active else '点击设为目标，或拖入原文')
        self.active_row = row
        self.update_bind_button()

    def next_pending(self):
        links = source_links(self.draft)
        current = self.table.currentRow()
        for step in range(1, self.table.rowCount() + 1):
            row = (current + step) % self.table.rowCount()
            item = self.table.item(row, 1)
            if item.data(ROLE) and item.data(ROLE) not in links:
                self.table.setCurrentCell(row, 1)
                self.table.scrollToItem(item)
                return

    def clear_pairs(self):
        result = self.draft
        try:
            for tid in source_links(result):
                result = edit_source_pair(result, 'unpair', translation_id=tid)
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        if self.stage(result):
            self.refresh()

    def row_widgets(self, row, left_text, span, tid, translated, members):
        evidence = frame('evidenceCell')
        box = QVBoxLayout(evidence)
        box.setContentsMargins(9, 6, 7, 7)
        box.setSpacing(3)
        head, separator, body = left_text.partition('\n')
        has_stamp = separator and head.endswith(' s') and '–' in head
        box.addWidget(label(f'{row+1:02}   {head if has_stamp else span.strip()}', 'stamp'))
        text = label(body if has_stamp else left_text, 'note')
        text.setWordWrap(True)
        box.addWidget(text)
        box.addStretch()
        self.table.setCellWidget(row, 0, evidence)

        cell = frame('targetCell')
        box = QVBoxLayout(cell)
        box.setContentsMargins(8, 6, 8, 7)
        box.setSpacing(4)
        head = QHBoxLayout()
        text = label(translated if tid else '（此条无对应译文）', 'translation' if tid else 'note')
        text.setWordWrap(True)
        head.addWidget(text, 1)
        from PySide6.QtWidgets import QSizePolicy
        state = '✓ 已配' if members else '待配' if tid else '无译文'
        badge = label(state, 'badge')
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedHeight(20)
        badge.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        badge.setProperty('restingText', state)
        badge.setProperty('paired', bool(members))
        badge.setProperty('empty', not tid)
        head.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        box.addLayout(head)
        for sid, number, source in members:
            member = frame('pairedMember')
            bar = QHBoxLayout(member)
            bar.setSpacing(3)
            bar.setContentsMargins(4, 3, 3, 3)
            bar.addWidget(label(f'L{number:02}', 'stamp'))
            original = label(source, 'note')
            original.setWordWrap(True)
            bar.addWidget(original, 1)
            revise = self.button(bar, '修订', lambda _=False,t=tid,s=sid: self.start_edit(t,s), 'ocrEditMember')
            remove = self.button(bar, '×', lambda _=False,t=tid,s=sid: self.change_member(t,s,'unpair'), 'ocrUnlinkMember')
            for button, verb in [(revise, '修订'), (remove, '解除')]:
                button.setProperty('small', True)
                button.setMinimumHeight(button.fontMetrics().height() + 6)
                button.setAccessibleName(f'{verb} L{number:02}')
            box.addWidget(member)
        if tid and not members:
            zone = label('点击设为目标，或拖入原文', 'dropZone')
            zone.setWordWrap(True)
            box.addWidget(zone)
            if tid in self.hints:
                hint = label(self.hints[tid], 'note')
                hint.setWordWrap(True)
                box.addWidget(hint)
        self.table.setCellWidget(row, 1, cell)

    def start_edit(self, tid, sid):
        if self.editor is not None:
            self.editor.setFocus()
            return
        row = next(i for i in range(self.table.rowCount()) if self.table.item(i, 1).data(ROLE) == tid)
        self.table.setCurrentCell(row, 1)
        lyric = next(s for s in reference_lyrics(self.draft) if s.id == sid)
        box = self.table.cellWidget(row, 1).layout()
        editor = QPlainTextEdit(source_text(self.draft, lyric))
        editor.setObjectName('ocrSourceRevision')
        editor.setAccessibleName('修订此条已配原文')
        editor.setFixedHeight(max(54, editor.fontMetrics().lineSpacing() * 2 + 12))
        self.edit_row = row
        self.editor = editor
        # Keep unsaved typing on screen until Save, Cancel revision or dialog discard.
        exempt = {'ocrPairClose', 'ocrPairKeepDraft', 'ocrPairDiscard'}
        self.edit_controls = [(button, button.isEnabled()) for button in self.findChildren(QPushButton)
                              if button.objectName() not in exempt]
        for button, _ in self.edit_controls:
            button.setEnabled(False)
        self.pool.setEnabled(False)
        box.addWidget(editor)
        actions = QHBoxLayout()
        number = next(i for i, s in enumerate(reference_lyrics(self.draft), 1) if s.id == sid)
        actions.addWidget(label(f'修订 L{number:02} 原文', 'note'), 1)
        self.button(actions, '取消修订', lambda: self.refresh(tid), 'ocrCancelRevision')
        self.button(actions, '保存修订', lambda: self.finish_edit(tid, sid), 'ocrSaveRevision')
        box.addLayout(actions)
        # show() makes newly inserted children participate in Qt layout measurement immediately.
        editor.show()
        for i in range(actions.count()):
            actions.itemAt(i).widget().show()
        self.table.fit_rows([row])
        QTimer.singleShot(0, self.table, lambda: self.table.fit_rows([row]))
        self.table.scrollToItem(self.table.item(row, 1))
        editor.setFocus()
        editor.selectAll()
        self.table.setCurrentCell(row, 1)
        self.update_target()

    def finish_edit(self, tid, sid):
        try:
            self.stage(edit_source_pair(self.draft, 'edit', translation_id=tid, source_id=sid,
                                        text=self.editor.toPlainText()))
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.refresh(tid)

    def refresh(self, preferred=None, advance=False):
        tid = preferred or self.target()
        p = self.draft
        trans = {t.id:t for t in imported_translations(p)}
        visuals = {v.id:v for v in p.visuals}
        refs = {s.id:s for s in reference_lyrics(p)}
        links = source_links(p)
        used = {s for ids in links.values() for s in link_ids(ids)}
        rows, shown_t, shown_v = [], set(), set()
        def stamp(start, end):
            return f'{start:.3f}–{end:.3f} s\n' if valid_time(start, end) else ''
        for cue in sorted(p.mappings, key=lambda c: float('inf') if c.start is None else c.start):
            vs = [visuals[k] for k in cue.visual_ids if k in visuals and k not in shown_v]
            ts = [k for k in cue.translation_ids if k in trans and k not in shown_t]
            shown_v.update(v.id for v in vs)
            shown_t.update(ts)
            if vs or ts:
                for i,k in enumerate(ts or [None]):
                    left = '\n'.join(stamp(v.start,v.end)+v.text for v in vs) if i == 0 else ''
                    rows.append((left, k, stamp(cue.start,cue.end),cue.start))
        rows.extend((stamp(v.start,v.end)+v.text,None,'',v.start) for v in visuals.values() if v.id not in shown_v)
        rows.extend(('',k,'',None) for k in trans if k not in shown_t)
        rows.sort(key=lambda row: float('inf') if row[3] is None else row[3])
        numbers = {sid:i for i,sid in enumerate(refs,1)}
        if self.editor is not None:
            for control, enabled in self.edit_controls:
                control.setEnabled(enabled)
            self.pool.setEnabled(True)
        self.editor = None
        self.table.setRowCount(len(rows))
        for i,(left,k,span,_) in enumerate(rows):
            target = span+trans[k].text if k else '（无对应译文）'
            ids = link_ids(links.get(k))
            if ids:
                target += '\n已配：' + ' ｜ '.join(f'L{numbers[s]:02} {source_text(p,refs[s])}' for s in ids)
            elif k:
                target += '\n' + self.hints.get(k, '待配')
            for j,label in enumerate((left,target)):
                item = QTableWidgetItem(label)
                flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                if j and k:
                    flags |= Qt.ItemFlag.ItemIsDropEnabled
                item.setFlags(flags)
                item.setData(ROLE,k if j else None)
                self.table.setItem(i,j,item)
            members = [(sid, numbers[sid], source_text(p, refs[sid])) for sid in ids]
            self.row_widgets(i, left, span, k, trans[k].text if k else '', members)
        self.table.fit_rows()
        QTimer.singleShot(0, self.table, self.table.fit_rows)
        owners = {sid: i+1 for i, row in enumerate(rows) for sid in link_ids(links.get(row[1]))}
        self.pool.clear()
        for i,s in enumerate(refs.values(),1):
            state = f'✓ 已配给 #{owners[s.id]:02}' if s.id in owners else '待配'
            item = QListWidgetItem(f'L{i:02}　{source_text(p,s)}\n　　　{state}')
            item.setData(ROLE,s.id)
            if s.id in used:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.pool.addItem(item)
        self.filter_pool()
        ids = [record[1] for record in rows]
        idx = ids.index(tid) if tid in ids and tid else -1
        if advance or idx < 0:
            idx = next((i for i in [*range(idx+1,len(ids)),*range(0,idx+1)] if ids[i] and ids[i] not in links),idx)
        if idx >= 0:
            self.table.setCurrentCell(idx,1)
        self.update_target()
        self.paired_count.setText(f'已配 {len(links)} / {len(trans)}')
        self.pool_count.setText(f'剩 {len(refs)-len(used)} / {len(refs)}')
        self.clear_button.setEnabled(bool(links))
        self.next_button.setVisible(bool(trans))
        self.undo.setEnabled(bool(self.past))
        self.redo.setEnabled(bool(self.future))
        self.apply_button.setEnabled(bool(self.past))
        self.import_translation.setVisible(not trans)
        self.status.setText(f'已配 {len(links)} / {len(trans)} 条译文 · 剩余 {len(refs)-len(used)} 条原文')

    def filter_pool(self):
        for i in range(self.pool.count()):
            item = self.pool.item(i)
            hidden = self.hide_used.isChecked() and not bool(item.flags() & Qt.ItemFlag.ItemIsEnabled)
            item.setHidden(hidden)
            if hidden:
                item.setSelected(False)

    def stage(self, draft):
        if draft is self.draft or draft.to_dict() == self.draft.to_dict():
            return False
        self.past.append(self.draft)
        self.future.clear()
        self.draft = draft
        self.hints = {}
        return True

    def history(self, redo):
        if self.editor is not None:
            return
        src, dst = (self.future,self.past) if redo else (self.past,self.future)
        if src:
            dst.append(self.draft)
            self.draft = src.pop()
            self.hints = {}
            self.refresh()

    def bind(self, tid, ids):
        if self.editor is not None or not tid or not ids:
            return False
        try:
            if self.stage(edit_source_pair(self.draft,'pair',translation_id=tid,source_ids=ids)):
                self.refresh(tid,advance=True)
                last = max((i for i in range(self.pool.count()) if self.pool.item(i).data(ROLE) in ids), default=-1)
                for i in range(last+1,self.pool.count()):
                    if self.pool.item(i).flags() & Qt.ItemFlag.ItemIsEnabled:
                        self.pool.setCurrentRow(i)
                        break
                return True
        except ValueError as exc:
            self.status.setText(str(exc))
        return False

    def bind_selected(self):
        self.bind(self.target(), self.pool.selected_ids())

    def arm_drag(self, origin, ids):
        if ids:
            self.drag_origin, self.drag_ids, self.dragging = origin, ids, False
            QApplication.instance().installEventFilter(self)

    def end_drag(self):
        if self.drag_origin is not None:
            QApplication.instance().removeEventFilter(self)
            self.pool.viewport().unsetCursor()
            self.table.viewport().unsetCursor()
        self.drag_origin, self.drag_ids, self.dragging = None, [], False

    def track_drag(self, event):
        position = event.globalPosition().toPoint()
        release = event.type() == QEvent.Type.MouseButtonRelease
        if release and event.button() != Qt.MouseButton.LeftButton:
            return False
        if not self.dragging:
            self.dragging = (position-self.drag_origin).manhattanLength() >= QApplication.startDragDistance()
        if not self.dragging:
            if release: self.end_drag()
            return False
        view = self.table.viewport()
        local = view.mapFromGlobal(position)
        item = self.table.itemAt(local) if view.rect().contains(local) else None
        tid = item.data(ROLE) if item and item.column() == 1 else None
        if tid:
            self.table.setCurrentCell(item.row(), 1)
        if release:
            ids = self.drag_ids
            self.end_drag()
            if tid: self.bind(tid, ids)
        else:
            cursor = Qt.CursorShape.DragCopyCursor if tid else Qt.CursorShape.ForbiddenCursor
            self.pool.viewport().setCursor(cursor)
            view.setCursor(cursor)
        event.accept()
        return True

    def eventFilter(self, obj, event):
        if getattr(self,'drag_origin',None) is not None:
            if obj is self and event.type() in (QEvent.Type.WindowDeactivate,QEvent.Type.Hide):
                self.end_drag()
            if event.type() in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
                if self.track_drag(event):
                    return True
            elif event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self.end_drag()
                return True
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter):
            if obj is self.pool or obj is self.table:
                self.bind_selected()
            return True
        return super().eventFilter(obj,event)

    def keep_draft(self):
        self.back_to_draft.hide()
        self.discard.hide()
        if self.editor is not None:
            self.editor.setFocus()
        else:
            self.refresh()
            self.pool.setFocus()

    def confirm_close(self):
        if not self.past and self.editor is None:
            return False
        self.status.setText('有尚未应用到主轴的配对草案。')
        self.back_to_draft.show()
        self.discard.show()
        self.back_to_draft.setFocus()
        return True

    def done(self, result):
        self.end_drag()
        super().done(result)

    def closeEvent(self, event):
        self.end_drag()
        if self.confirm_close():
            event.ignore()
        else:
            super().closeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.confirm_close():
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter):
            event.accept()
            return
        super().keyPressEvent(event)

    def auto_pair(self):
        result,hints = suggest_source_links(self.draft)
        self.stage(result)
        self.hints = hints
        self.refresh()

    def menu_at(self, pos):
        if self.editor is not None:
            return
        item = self.table.itemAt(pos)
        if item:
            self.table.setCurrentCell(item.row(),1)
            self.manage()

    def manage(self):
        tid = self.target()
        ids = link_ids(source_links(self.draft).get(tid))
        if not ids:
            self.status.setText('请选择已配对的译文。')
            return
        refs = {s.id:s for s in reference_lyrics(self.draft)}
        menu = QMenu(self)
        numbers = {sid:i for i,sid in enumerate(refs,1)}
        for sid in ids:
            member = menu.addMenu(f'L{numbers[sid]:02}　{source_text(self.draft,refs[sid])}')
            for label,mode in [('修订此句','edit'),('解除此句','unpair')]:
                member.addAction(label,lambda checked=False,s=sid,m=mode:self.change_member(tid,s,m))
        from PySide6.QtGui import QCursor
        menu.exec(QCursor.pos())

    def change_member(self, tid, sid, mode):
        text = ''
        if mode == 'edit':
            lyric = next(s for s in reference_lyrics(self.draft) if s.id==sid)
            text,ok=QInputDialog.getMultiLineText(self,'修订原文','仅修改此条已配原文的显示文字',source_text(self.draft,lyric))
            if not ok:
                return
        try:
            if self.stage(edit_source_pair(self.draft,mode,translation_id=tid,source_id=sid,text=text)):
                self.refresh(tid)
        except ValueError as exc:
            self.status.setText(str(exc))

    def import_sources(self):
        from lyricsync.subtitles import import_original
        path,_ = QFileDialog.getOpenFileName(self,'导入原文','','字幕或文本 (*.txt *.srt *.lrc *.ass *.ssa)')
        if path:
            try:
                incoming,_ = import_original(path)
                draft = deepcopy(self.draft)
                known = {s.id for s in draft.lyrics}
                draft.lyrics.extend(s for s in incoming if s.id not in known)
                draft.alignment_graph.sync_unbound(draft.lyrics,draft.translations,draft.visuals)
                self.stage(draft)
                self.refresh()
            except (ValueError,OSError) as exc:
                self.status.setText(str(exc))

    def import_translations(self):
        from lyricsync.translation_pool import import_translation_pool,update_translation_pool
        path,_ = QFileDialog.getOpenFileName(self,'导入译文','','字幕或文本 (*.txt *.srt *.lrc *.ass *.ssa)')
        if path:
            try:
                incoming = import_translation_pool(path)
                aligned = fill_ocr_placeholders(self.draft,incoming)
                self.stage(aligned if aligned is not None else update_translation_pool(self.draft,incoming))
                self.refresh()
            except (ValueError,OSError) as exc:
                self.status.setText(str(exc))

    def apply(self):
        if self.editor is not None:
            self.status.setText('请先保存或取消当前原文修订。')
            self.editor.setFocus()
            return
        b = self.bridge
        if (b.document.id,b.document.revision) != (self.identity,self.revision):
            self.status.setText('工程已变化，请重新打开配对台。')
            return
        if (b._candidate_source != 'main' or b._alignment_preview.active or b._candidate_job
                or b._task.get('running') or b._editor_active or b._editor_composing):
            self.status.setText('请先返回可编辑主轴并结束当前任务。')
            return
        if self.past and b._adopt_project(self.draft,'应用 OCR 原文配对草案'):
            b._changed()
            self.accept()
