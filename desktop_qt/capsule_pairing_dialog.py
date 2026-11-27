"""Three-column capsule pairing for non-OCR work; no translation generation."""
import json
from copy import deepcopy
from uuid import uuid4
from PySide6.QtWidgets import QWidget, QInputDialog
from pathlib import Path
from .tool_cursor import razor_cursor
from . import carbon_widgets as c
from PySide6.QtWidgets import QFrame,QBoxLayout
from html import escape
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

    def row_document(self,index,width,font):
        doc=self.document(index.data(),width,font)
        data=index.data(Qt.UserRole+1)
        if data:
            n,lyric,time=data
            small='<span style="font-size:10px;color:#8f879a">'
            doc.setHtml(small+str(n).zfill(2)+'</span>&nbsp; '+escape(lyric)+
                ('<br/>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;'+small+escape(time)+'</span>' if time else ''))
        return doc

    def sizeHint(self, option, index):
        doc = self.row_document(index,option.rect.width() or self.parent().viewport().width(),option.font)
        pad = 14 if index.data(Qt.UserRole+1) else 26
        return QSize(180, max(44, int(doc.size().height()) + pad))

    def paint(self, painter, option, index):
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        rect = option.rect.adjusted(4, 3, -4, -3)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor('#8562C5' if selected else '#E4DEEF'))
        painter.setBrush(QColor('#F1EBFC' if selected else '#FFFFFF'))
        painter.drawRoundedRect(rect, 7, 7)
        painter.setClipRect(rect)
        doc = self.row_document(index,option.rect.width(),option.font)
        painter.translate(option.rect.left()+14,option.rect.top()+(6 if index.data(Qt.UserRole+1) else 13))
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
        self.setSpacing(1)
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
        from lyricsync.translation_pool import pairing_label
        self._draft=deepcopy(bridge.document.project)
        self._original=bridge.document
        self.identity,self.revision=bridge.document.id,bridge.document.revision
        self._undo=[];self._redo=[];self._waiting=False;self._dismissed=False
        self.finished.connect(lambda _:setattr(self,'_dismissed',True))
        title=pairing_label(self._draft)
        self.setWindowTitle(title); self.setObjectName('capsulePairingDialog')
        self.resize(1040,632)
        layout=c.layout(self)
        bar=QFrame();bar.setObjectName('pairToolbar');layout.addWidget(bar)
        top=self.top=c.layout(bar,False,(16,9,16,9),8)
        self.heading=c.label(title,'title');top.addWidget(self.heading)
        vline=QFrame();vline.setObjectName('carbonLineV');vline.setFixedSize(1,16);top.addWidget(vline)
        self.intro=c.Elide('先导入译文，再拖入中间配对；Ctrl / Shift 多选，右键合并或解除配对','muted')
        top.addWidget(self.intro,1)
        self.razor=QToolButton();self.razor.setCheckable(True);self.razor.setText('剃刀')
        self.razor.setObjectName('translationRazor')
        icons=Path(__file__).parent/'assets'/'icons'
        icon=QIcon(str(icons/'tool-razor.svg'))
        icon.addFile(str(icons/'tool-razor-white.svg'),QSize(),QIcon.Mode.Normal,QIcon.State.On)
        self.razor.setIcon(icon);self.razor.setAccessibleName('译文剃刀')
        self.razor.setToolTip('点击译文字间切割，不改原文时间，可撤销。');self.razor.toggled.connect(self._toggle_razor)
        if not hasattr(self,'session'):
            self.ai=c.button('AI 辅助配对',self.assist,'soft','pairingAiRequest')
            self.ai.setIcon(QIcon(str(icons/'spark.svg')));self.ai.setIconSize(QSize(14,14))
            self.ai.setToolTip('仅映射或拆合已有译文，需核对后应用。');top.addWidget(self.ai)
        self.history_buttons=[]
        for text,redo in [('撤销',False),('重做',True)]:
            action=c.button(text,lambda checked=False,r=redo:self.history(r),'quiet')
            self.history_buttons.append(action);top.addWidget(action)
        body=QWidget();body.setObjectName('carbonBody');layout.addWidget(body,1)
        columns=c.layout(body,False,(14,12,14,12),12)
        self.left=CapsuleList(self,'source');self.middle=CapsuleList(self,'middle');self.right=CapsuleList(self,'translation')
        self.counts=[];self.column_titles=[]
        for title,listing,weight in [('原文',self.left,100),('配对区',self.middle,128),('译文',self.right,100)]:
            frame=QFrame();frame.setObjectName('carbonCard');columns.addWidget(frame,weight)
            col=c.layout(frame);header=c.band(col,(12,0,10,0),7);header.parentWidget().setObjectName('pairCardHead');header.parentWidget().setFixedHeight(40)
            label=c.label(title,'title');self.column_titles.append(label);header.addWidget(label)
            count=c.label('','count');self.counts.append(count);header.addWidget(count);header.addStretch()
            if listing is self.left:header.addWidget(c.button('全部加入',self.add_all,'link'))
            if listing is self.middle:header.addWidget(c.label('右键可合并 / 解配','muted'))
            if listing is self.right:
                self.pending=c.label('待导入','muted');header.addWidget(self.pending)
                self.import_pool=c.button('导入…',self.choose_pool_file,'link','importTranslationPool')
                self.paste_header=c.button('粘贴',self.paste_words,'link')
                for item in (self.razor,self.import_pool,self.paste_header):header.addWidget(item)
            c.line(col);col.addWidget(listing,1)
        def empty_box(view,title,detail,kind,interactive):
            panel=QFrame(view.viewport());panel.setObjectName('pairEmpty')
            if not interactive:panel.setAttribute(Qt.WA_TransparentForMouseEvents)
            box=c.layout(panel,margins=(18,12,18,12),space=8);box.addStretch()
            box.addWidget(c.Glyph(kind),0,Qt.AlignHCenter);box.addWidget(c.label(title,'title'),0,Qt.AlignHCenter)
            note=c.label(detail,'muted',True);note.setAlignment(Qt.AlignCenter);box.addWidget(note)
            actions=QHBoxLayout();actions.setSpacing(6);actions.addStretch();box.addLayout(actions)
            box.addStretch();view.viewport().installEventFilter(self)
            return panel,actions
        self.middle_hint,_=empty_box(self.middle,'配对区为空','把左侧原文拖到这里，再将右侧译文拖到对应卡片上\n即可配对；也可点击左上角「全部加入」。','join',False)
        self.pool_hint,actions=empty_box(self.right,'尚未导入译文','支持 TXT、LRC、SRT、ASS 字幕文件，或\n直接将文件拖入此区域。','import',True)
        self.empty_actions=actions
        self.empty_pool=c.button('导入译文文件…',self.choose_pool_file,'primary','emptyTranslationImport')
        self.paste_pool=c.button('粘贴文本',self.paste_words)
        actions.addWidget(self.empty_pool);actions.addWidget(self.paste_pool);actions.addStretch()
        self.footer=c.footer(layout)
        self.status=c.label('','muted',True);self.footer.addWidget(self.status,1)
        self.footer.addWidget(c.button('关闭',self.reject))
        self.apply_button=c.button('应用到主轴',self.apply,'primary','pairingConfirmApply')
        self.footer.addWidget(self.apply_button)


        self.undoShortcut=QShortcut(QKeySequence.StandardKey.Undo,self);self.undoShortcut.activated.connect(lambda:self.history(False))
        self.redoShortcut=QShortcut(QKeySequence.StandardKey.Redo,self);self.redoShortcut.activated.connect(lambda:self.history(True))
        c.theme(self)
        self.setStyleSheet(self.styleSheet()+"""
            QFrame#pairToolbar {background:#ffffff;border:0;border-bottom:1px solid #e5dff2;}
            QFrame#carbonLineV {background:#e5dff2;border:0;min-width:1px;max-width:1px;}
            QWidget#pairCardHead {background:#faf8fd;border-top-left-radius:9px;border-top-right-radius:9px;}
            QFrame#pairEmpty {background:#faf9fd;border:1.5px dashed #dcd4eb;border-radius:8px;}
            QLabel[styleRole="count"] {background:#efeaf8;color:#63419c;border-radius:10px;padding:0px 7px;font-size:11px;font-weight:600;}
            QListWidget {background:transparent;border:0;padding:4px;}
            QListWidget::item {padding:0;}
            QToolButton#translationRazor {padding:2px 6px;border:1px solid transparent;border-radius:5px;color:#4f4663;}
            QToolButton#translationRazor:hover {background:#f2ecfa;}
            QToolButton#translationRazor:checked {background:#67458f;color:#ffffff;}
            """)
        self.refresh()

    def eventFilter(self, watched, event):
        from PySide6.QtCore import QEvent
        if event.type() in (QEvent.Type.Show,QEvent.Type.Resize):
            for listing,overlay in ((getattr(self,'right',None),getattr(self,'pool_hint',None)),
                                    (getattr(self,'middle',None),getattr(self,'middle_hint',None))):
                if overlay is not None and watched is listing.viewport():
                    overlay.setGeometry(watched.rect().adjusted(7,7,-7,-7))
                    if overlay is getattr(self,'pool_hint',None) and hasattr(self,'paste_pool'):
                        actions=self.empty_actions
                        needed=self.empty_pool.sizeHint().width()+self.paste_pool.sizeHint().width()+actions.spacing()+30
                        vertical=overlay.width()<needed
                        actions.setDirection(QBoxLayout.TopToBottom if vertical else QBoxLayout.LeftToRight)
                        for button in (self.empty_pool,self.paste_pool):
                            button.setMinimumWidth(button.sizeHint().width())
                            actions.setAlignment(button,Qt.AlignHCenter)
        return super().eventFilter(watched,event)

    def add_all(self):
        ids=[self.left.item(i).data(Qt.UserRole) for i in range(self.left.count()) if self.left.item(i).flags() & Qt.ItemIsEnabled]
        if ids:self.change('source',ids=ids)

    def paste_words(self):
        from lyricsync.model import Translation
        text,ok=QInputDialog.getMultiLineText(self,'粘贴译文','每行一句，保留原有译文。')
        if not ok:return
        try:
            rows=[Translation('paste-'+uuid4().hex,line.strip()) for line in text.splitlines() if line.strip()]
            if rows:self.load_pool(rows)
        except (ValueError,RuntimeError) as exc:self.status.setText(str(exc))

    def sync_columns(self,has_pool):
        stacks=(self.session.past,self.session.future) if hasattr(self,'session') else (self._undo,self._redo)
        for button,stack in zip(self.history_buttons,stacks):button.setEnabled(bool(stack))
        for label,listing in zip(self.counts,(self.left,self.middle,self.right)):label.setText(str(listing.count()))
        self.pool_hint.setVisible(not has_pool);self.pending.setVisible(not has_pool);self.razor.setVisible(has_pool)
        self.import_pool.setVisible(has_pool);self.paste_header.setVisible(has_pool)
        self.middle_hint.setVisible(not self.middle.count())

    def record(self,draft):
        if draft.to_dict()==self._draft.to_dict():return
        self._undo.append(self._draft);self._draft=draft;self._redo.clear()
        self.refresh()

    def apply(self):
        try:
            self.guard()
            if self._draft.to_dict()!=self.bridge.document.project.to_dict():
                self._draft.api_records=deepcopy(self.bridge.document.project.api_records)
                self.bridge._adopt_project(self._draft,'应用译文配对到主轴')
                self.bridge._changed()
            self.accept()
        except (ValueError,RuntimeError) as exc:self.status.setText(str(exc))

    def assist(self):
        try:
            self.guard();self.bridge._api_check()
            from .original_pairing_suggestions import request,materialize
            frozen=self._draft
            before=deepcopy(self.bridge.document.project.to_dict());before.pop('api_records',None)
            self._waiting=True;self.status.setText('正在请求配对建议。')
            def finished(response):
                self._waiting=False
                if self._dismissed:return
                try:
                    live=self.bridge.document
                    after=live.project.to_dict();after.pop('api_records',None)
                    if live is not self._original or after!=before or self._draft is not frozen:raise ValueError('工程或草案已变化，请重新请求。')
                    self.revision=live.revision
                    self._draft.api_records=deepcopy(live.project.api_records)
                    if isinstance(response,dict) and response.get('status'):raise ValueError(response.get('message') or '请求未完成。')
                    self.record(materialize(self._draft,response))
                    self.status.setText('配对建议已载入本窗口，核对后可应用到主轴。')
                except (ValueError,RuntimeError) as exc:self.status.setText(str(exc))
            finished._receive_terminal_status=True
            self.bridge._api_job('AI 辅助配对',lambda cache,key,settings,cancel,progress:request(frozen,cache,key,settings,cancel,progress),finished)
        except (ValueError,RuntimeError) as exc:self._waiting=False;self.status.setText(str(exc))

    def choose_pool_file(self):
        from lyricsync.translation_pool import import_translation_pool
        try:
            self.guard()
            identity,revision=self.identity,self.revision
            path,_=QFileDialog.getOpenFileName(self,'导入翻译到右栏','','文本或字幕 (*.txt *.srt *.lrc *.ass *.ssa)')
            if not path:return
            incoming=import_translation_pool(path)
            if not incoming:raise ValueError('所选文件没有可导入的译文。')
            current=self.session.project if hasattr(self,'session') else self._draft
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
        self.record(update_translation_pool(self._draft,incoming,replace=replace))

    def guard(self):
        b=self.bridge
        if self._waiting:raise ValueError('请等待当前配对建议请求结束。')
        if b.document is not self._original or b.document.id != self.identity or b.document.revision != self.revision:
            raise ValueError('工程已变化，请重新打开工作台。草案未应用。')
        if b._candidate_source != 'main' or b._alignment_preview.active:
            raise ValueError('请先应用结果到可编辑主轴。')
        if b._candidate_job or b._task.get('running') or b._editor_active or b._editor_composing:
            raise ValueError('请先结束运行任务或文字输入。')

    def refresh(self):
        p=self._draft
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
            item.setToolTip(text);listing.addItem(item);return item
        def clock(seconds):
            ms=round(seconds*1000);m,ms=divmod(ms,60000);sec,frac=divmod(ms,1000)
            return f'{m:02}:{sec:02}.{frac:03}'
        for n,row in enumerate(source,1):
            span=f"  {row['start']:.3f}–{row['end']:.3f} s" if row['start'] is not None and row['end'] is not None else ''
            if row['id'] not in used_source:
                item=add(self.left,f"{n:02d}  {row['text']}"+span,row['id'])
                timecode=f"{clock(row['start'])}–{clock(row['end'])}" if span else ''
                item.setData(Qt.UserRole+1,(n,row['text'],timecode))
        for piece in st['pieces']:
            if piece['id'] not in used_piece:add(self.right,piece['text'],piece['id'])
        for group in st['groups']:
            row=by_source.get(group['cue_id'])
            label = row['text'] if row else '原文行已移除，右键解除配对以回收译文'
            text=label+'\n\n'+ ('\n'.join(pieces[i]['text'] for i in group['piece_ids']) or '拖入对应译文')
            add(self.middle,text,group['id'])
        self.sync_columns(bool(p.translations))
        self.ai.setEnabled(bool(self.left.count() and self.right.count()))
        self.apply_button.setEnabled(self._draft.to_dict()!=self.bridge.document.project.to_dict())
        self.status.setText(('没有可配对的独立原词主轴，请先导入原词并建立可编辑时间轴。' if not source else
            f'待配对原文 {self.left.count()} · 待配对译文 {self.right.count()} · 配对区 {self.middle.count()}。原始文字与原词时间保留，每步可撤销。'))

    def change(self, action, **kwargs):
        try:
            self.guard()
            draft=operate(self._draft,action,**kwargs)
            self.guard()
            self.record(draft)
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
            self.guard()
            src,dest=(self._redo,self._undo) if redo else (self._undo,self._redo)
            if src:dest.append(self._draft);self._draft=src.pop();self.refresh()
        except (ValueError,RuntimeError) as exc:self.status.setText(str(exc))
