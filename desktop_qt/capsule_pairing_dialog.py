"""Three-column capsule pairing for non-OCR work; no translation generation."""
import json
from pathlib import Path
from .tool_cursor import razor_cursor
from PySide6.QtGui import QIcon
from PySide6.QtCore import Qt, QMimeData, QSize, QPointF
from PySide6.QtGui import QColor, QDrag, QPainter, QTextDocument, QAbstractTextDocumentLayout, QKeySequence, QShortcut
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QFileDialog, QMessageBox, QAbstractItemView, QStyledItemDelegate, QStyle, QPushButton, QToolButton, QMenu)
from lyricsync.capsule_pairing import state, sources, operate
from lyricsync.graphemes import utf16_to_codepoint

MIME = 'application/x-lyria-pairing-capsule-v022'

class CapsuleDelegate(QStyledItemDelegate):
    def document(self, text, width, font):
        doc = QTextDocument()
        doc.setDefaultFont(font)
        doc.setDocumentMargin(0)
        doc.setPlainText(text)
        doc.setTextWidth(max(40, width - 28))
        return doc

    def sizeHint(self, option, index):
        doc = self.document(index.data(), option.rect.width() or self.parent().viewport().width(), option.font)
        return QSize(180, max(54, int(doc.size().height()) + 26))

    def paint(self, painter, option, index):
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        rect = option.rect.adjusted(4, 3, -4, -3)
        painter.setPen(QColor('#B8A0D8' if selected else '#E4DEEF'))
        painter.setBrush(QColor('#EFE7FA' if selected else '#FBF9FE'))
        painter.drawRoundedRect(rect, 13, 13)
        painter.setClipRect(rect)
        doc = self.document(index.data(), option.rect.width(), option.font)
        painter.translate(option.rect.left()+14, option.rect.top()+13)
        ctx = QAbstractTextDocumentLayout.PaintContext()
        ctx.palette.setColor(ctx.palette.ColorRole.Text, QColor('#3F3452'))
        doc.documentLayout().draw(painter, ctx)
        painter.restore()

class CapsuleList(QListWidget):
    def __init__(self, owner, kind):
        super().__init__(owner)
        self.owner, self.kind = owner, kind
        self.setObjectName('capsule'+kind.title())
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(kind != 'middle')
        self.setAcceptDrops(kind == 'middle')
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop if kind == 'middle'
                            else QAbstractItemView.DragDropMode.DragOnly)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setItemDelegate(CapsuleDelegate(self))
        self.setSpacing(3)
        self.setWordWrap(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.menu)

    def identities(self):
        return [x.data(Qt.ItemDataRole.UserRole) for x in self.selectedItems()]

    def startDrag(self, actions):
        if not self.identities(): return
        data = {'document': self.owner.identity, 'revision': self.owner.revision,
                'kind': self.kind, 'ids': self.identities()}
        mime = QMimeData(); mime.setData(MIME, json.dumps(data).encode('utf8'))
        drag = QDrag(self); drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event):
        if self.kind == 'middle' and event.mimeData().hasFormat(MIME):
            event.acceptProposedAction()
        else: event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        try:
            data = json.loads(bytes(event.mimeData().data(MIME)))
            if data['document'] != self.owner.identity or data['revision'] != self.owner.revision:
                raise ValueError('工程已变化，请重新拖动。')
            if data['kind'] == 'source':
                item = self.itemAt(event.position().toPoint())
                kwargs = {}
                if getattr(self.owner, 'supports_anchor_append', False) and item is not None:
                    kwargs['target'] = item.data(Qt.ItemDataRole.UserRole)
                if not self.owner.change('source', ids=data['ids'], **kwargs):
                    event.ignore(); return
            elif data['kind'] == 'translation':
                item = self.itemAt(event.position().toPoint())
                if item is None: raise ValueError('请将译文拖到中间已放入的原词胶囊。')
                if not self.owner.change('bind', ids=data['ids'], target=item.data(Qt.ItemDataRole.UserRole)):
                    event.ignore(); return
            else: raise ValueError('不支持此拖动来源。')
            event.acceptProposedAction()
        except (ValueError, KeyError, TypeError) as exc:
            self.owner.status.setText(str(exc)); event.ignore()

    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if self.kind == 'translation' and self.owner.razor.isChecked() and item and event.button() == Qt.MouseButton.LeftButton:
            rect = self.visualItemRect(item)
            doc = self.itemDelegate().document(item.text(), rect.width(), self.font())
            point = QPointF(event.position().x()-rect.left()-14, event.position().y()-rect.top()-13)
            position = doc.documentLayout().hitTest(point, Qt.HitTestAccuracy.FuzzyHit)
            try:
                gap = utf16_to_codepoint(item.text(), max(0, position), snap=True)
                self.owner.change('split', ids=[item.data(Qt.ItemDataRole.UserRole)], offset=gap)
            except ValueError as exc:
                self.owner.status.setText(str(exc))
            event.accept(); return
        super().mousePressEvent(event)

    def menu(self, point):
        item = self.itemAt(point)
        if item and not item.isSelected():
            self.clearSelection(); item.setSelected(True)
        menu = QMenu(self)
        if self.kind == 'translation':
            action = menu.addAction('合并选中')
            action.setEnabled(len(self.selectedItems()) >= 2)
            action.triggered.connect(lambda: self.owner.change('merge', ids=self.identities()))
        elif self.kind == 'middle':
            if getattr(self.owner, 'supports_anchor_append', False):
                merge = menu.addAction('合并选中配对槽')
                merge.setEnabled(len(self.selectedItems()) >= 2)
                merge.triggered.connect(lambda: self.owner.change('merge_groups', ids=self.identities()))
            action = menu.addAction('解除配对 / 移回两侧')
            action.setEnabled(bool(self.selectedItems()))
            action.triggered.connect(lambda: self.owner.change('remove', ids=self.identities()))
            if not getattr(self.owner, 'supports_anchor_append', False):
                keep = menu.addAction('解除绑定（保留当前主轴文本）')
                keep.setEnabled(bool(self.selectedItems()))
                keep.triggered.connect(lambda: self.owner.change('detach_keep_text', ids=self.identities()))
        menu.addAction('撤销', lambda: self.owner.history(False))
        menu.addAction('重做', lambda: self.owner.history(True))
        menu.exec(self.viewport().mapToGlobal(point))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.doItemsLayout()

class CapsulePairingDialog(QDialog):
    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setWindowTitle('与翻译配对'); self.setObjectName('capsulePairingDialog')
        self.resize(1100, 700)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        heading = QLabel('与翻译配对'); heading.setStyleSheet('font-size:18px;font-weight:600;color:#63419C;')
        top.addWidget(heading); top.addStretch()
        self.razor = QToolButton(); self.razor.setText('剃刀'); self.razor.setCheckable(True)
        self.razor.setObjectName('translationRazor')
        icons = Path(__file__).parent / 'assets' / 'icons'
        icon = QIcon(str(icons / 'tool-razor.svg'))
        icon.addFile(str(icons / 'tool-razor-white.svg'), QSize(), QIcon.Mode.Normal, QIcon.State.On)
        self.razor.setIcon(icon)
        self.razor.setAccessibleName('译文剃刀')
        self.razor.setToolTip('点击译文字间切割，不改原文时间，可撤销。')
        self.razor.setStyleSheet('QToolButton { padding: 5px; border-radius: 6px; } QToolButton:checked { background: #7045A1; }')
        self.razor.toggled.connect(self._toggle_razor)
        top.addWidget(self.razor)
        for label, redo in [('撤销', False), ('重做', True)]:
            button = QPushButton(label); button.clicked.connect(lambda _=False,r=redo:self.history(r)); top.addWidget(button)
        close = QPushButton('关闭'); close.clicked.connect(self.accept); top.addWidget(close)
        layout.addLayout(top)
        intro=QLabel('先把左侧原文拖入中间，再把右侧已有译文拖到对应胶囊。Ctrl / Shift 多选；右键合并或解除配对。')
        intro.setWordWrap(True); layout.addWidget(intro)
        columns=QHBoxLayout()
        self.left=CapsuleList(self,'source'); self.middle=CapsuleList(self,'middle'); self.right=CapsuleList(self,'translation')
        for title,widget in [('原文',self.left),('配对区',self.middle),('翻译',self.right)]:
            col=QVBoxLayout();label=QLabel(title);label.setStyleSheet('font-weight:600;color:#756387;')
            header=QHBoxLayout()
            header.addWidget(label);header.addStretch()
            if widget is self.right:
                self.import_pool=QPushButton('导入翻译…')
                self.import_pool.setObjectName('importTranslationPool')
                self.import_pool.clicked.connect(self.choose_pool_file)
                header.addWidget(self.import_pool)
            col.addLayout(header);col.addWidget(widget,1)
            if widget is self.right:
                self.empty_pool=QPushButton('从文本或字幕文件导入翻译…',self.right.viewport())
                self.empty_pool.setObjectName('emptyTranslationImport')
                self.empty_pool.clicked.connect(self.choose_pool_file)
                self.right.viewport().installEventFilter(self)
            columns.addLayout(col,1)
        layout.addLayout(columns,1)
        self.status=QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        self.undoShortcut=QShortcut(QKeySequence.StandardKey.Undo,self);self.undoShortcut.activated.connect(lambda:self.history(False))
        self.redoShortcut=QShortcut(QKeySequence.StandardKey.Redo,self);self.redoShortcut.activated.connect(lambda:self.history(True))
        from .native_theme import apply_dialog_theme
        apply_dialog_theme(self)
        self.refresh()

    def eventFilter(self, watched, event):
        from PySide6.QtCore import QEvent
        if hasattr(self,'empty_pool') and watched is self.right.viewport() and event.type() in (QEvent.Type.Show,QEvent.Type.Resize):
            rect=watched.rect()
            self.empty_pool.adjustSize()
            self.empty_pool.move(max(0,(rect.width()-self.empty_pool.width())//2),
                                 max(0,(rect.height()-self.empty_pool.height())//2))
        return super().eventFilter(watched,event)

    def choose_pool_file(self):
        from lyricsync.translation_pool import import_translation_pool
        try:
            self.guard()
            identity,revision=self.identity,self.revision
            path,_=QFileDialog.getOpenFileName(self,'导入翻译到右栏','','文本或字幕 (*.txt *.srt *.lrc *.ass *.ssa)')
            if not path:return
            incoming=import_translation_pool(path)
            if not incoming:raise ValueError('所选文件没有可导入的译文。')
            current=self.session.project if hasattr(self,'session') else self.bridge.document.project
            existing={t.id:t.text for t in current.translations}
            if all(existing.get(t.id)==t.text for t in incoming):
                self.status.setText('这份文件的全部译文已经在译文池中。')
                return
            replace=False
            if self.right.count():
                box=QMessageBox(self);box.setWindowTitle('导入翻译');box.setText('选择如何载入右栏。已配对和主轴引用的译文会保留。')
                append=box.addButton('追加到末尾',QMessageBox.ButtonRole.AcceptRole)
                swap=box.addButton('仅替换未配对译文',QMessageBox.ButtonRole.ActionRole)
                box.addButton('取消',QMessageBox.ButtonRole.RejectRole)
                box.setDefaultButton(append);box.exec()
                if box.clickedButton() not in (append,swap):return
                replace=box.clickedButton() is swap
            self.guard()
            if (identity,revision)!=(self.identity,self.revision):raise ValueError('工程已变化，请重新选择文件。')
            self.load_pool(incoming,replace)
        except (OSError,ValueError,RuntimeError) as exc:
            self.status.setText(str(exc))

    def load_pool(self,incoming,replace=False):
        from lyricsync.translation_pool import update_translation_pool
        self.guard()
        draft=update_translation_pool(self.bridge.document.project,incoming,replace=replace)
        if self.bridge._adopt_project(draft,'从配对右栏导入译文'):
            self.bridge._changed()
        self.refresh()

    def guard(self):
        b=self.bridge
        if b.document.id != self.identity or b.document.revision != self.revision:
            self.refresh();raise ValueError('工程已变化，列表已刷新，请重新操作。')
        if b._candidate_source != 'main' or b._alignment_preview.active:
            raise ValueError('请先应用结果到可编辑主轴。')
        if b._candidate_job or b._task.get('running') or b._editor_active or b._editor_composing:
            raise ValueError('请先结束运行任务或文字输入。')

    def refresh(self):
        b=self.bridge;p=b.document.project
        self.identity,self.revision=b.document.id,b.document.revision
        try:
            st=state(p)
        except ValueError as exc:
            for listing in (self.left,self.middle,self.right):listing.clear()
            self.status.setText(str(exc));return
        source=sources(p);by_source={x['id']:x for x in source}
        pieces={x['id']:x for x in st['pieces']}
        used_source={g['cue_id'] for g in st['groups']}
        used_piece={i for g in st['groups'] for i in g['piece_ids']}
        for listing in (self.left,self.middle,self.right):listing.clear()
        def add(listing,text,identity):
            item=QListWidgetItem(text);item.setData(Qt.ItemDataRole.UserRole,identity)
            item.setToolTip(text);listing.addItem(item)
        for row in source:
            if row['id'] not in used_source:add(self.left,row['text'],row['id'])
        for piece in st['pieces']:
            if piece['id'] not in used_piece:add(self.right,piece['text'],piece['id'])
        for group in st['groups']:
            row=by_source.get(group['cue_id'])
            label = row['text'] if row else '原文行已移除，右键解除配对以回收译文'
            text=label+'\n\n'+ ('\n'.join(pieces[i]['text'] for i in group['piece_ids']) or '拖入对应译文')
            add(self.middle,text,group['id'])
        self.empty_pool.setVisible(self.right.count()==0)
        self.status.setText(('没有可配对的独立原词主轴，请先导入原词并建立可编辑时间轴。' if not source else
            f'待配对原文 {self.left.count()} · 待配对译文 {self.right.count()} · 配对区 {self.middle.count()}。原始文字与原词时间保留，每步可撤销。'))

    def change(self, action, **kwargs):
        try:
            self.guard()
            draft=operate(self.bridge.document.project,action,**kwargs)
            self.guard()
            self.bridge._adopt_project(draft,'与翻译配对：'+action)
            self.bridge._changed();self.refresh()
            return True
        except (ValueError,RuntimeError) as exc:
            self.status.setText(str(exc));return False

    def _toggle_razor(self, checked):
        viewport = self.right.viewport()
        if checked:
            viewport.setCursor(razor_cursor(False, viewport.devicePixelRatioF()))
        else:
            viewport.unsetCursor()

    def history(self, redo):
        try:
            self.guard();self.bridge._history(redo);self.refresh()
        except (ValueError,RuntimeError) as exc:self.status.setText(str(exc))
