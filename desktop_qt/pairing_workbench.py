"""Three-column local pairing bench. Every edit is explicit, atomic and undoable."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QTableWidget,QTableWidgetItem,
    QPushButton,QComboBox,QAbstractItemView,QListWidget,QListWidgetItem,QDialogButtonBox,QMessageBox,QTreeWidget,QTreeWidgetItem)
from lyricsync.pairing_graph import graph_snapshot, correction, _text, visual_groups


class PairingWorkbench(QDialog):
    def __init__(self,bridge,parent=None):
        super().__init__(parent);self.bridge=bridge;self.setWindowTitle('OCR / 原词 / 译文配对工作台')
        self.setObjectName('pairingWorkbench');self.resize(1080,650)
        layout=QVBoxLayout(self)
        intro=QLabel('左侧时间与 OCR 证据固定。选择原词/译文列进行滑移或分组；到下一人工锚点或显式段落停止。每步可撤销。')
        intro.setWordWrap(True);layout.addWidget(intro)
        self.table=QTableWidget(0,3);self.table.setObjectName('pairingThreeColumns')
        self.table.setHorizontalHeaderLabels(['时间段与 OCR 画面文字','演唱原词（独立实例）','译文 / 当前正文'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0,340);self.table.setColumnWidth(1,310);layout.addWidget(self.table,1)
        self.table.cellClicked.connect(self._selected)
        self.groups=QTreeWidget();self.groups.setObjectName('pairingVisualGroups')
        self.groups.setHeaderLabels(['\u53ea\u8bfb\u5173\u8054\u5206\u7ec4\uff08\u5b9e\u4f8b ID / M:N / \u72b6\u6001\uff09'])
        self.groups.setMaximumHeight(155);self.groups.setMinimumHeight(95)
        self.groups.itemClicked.connect(self._select_group);layout.addWidget(self.groups)
        controls=QHBoxLayout();self.column=QComboBox();self.column.addItem('原词列','source');self.column.addItem('译文列','translation');controls.addWidget(self.column)
        for name,label,callback in [
            ('pairingSlipDown','从此处向下留空一行',lambda:self._apply('slip_down')),
            ('pairingSlipUp','从此处向上移一行',lambda:self._apply('slip_up')),
            ('pairingMerge','合并当前两行关联',lambda:self._apply('merge_next')),
            ('pairingBind','绑定所选时间段到指定实例',self._bind)]:
            button=QPushButton(label);button.setObjectName(name);button.clicked.connect(callback);controls.addWidget(button)
        layout.addLayout(controls)
        bottom=QHBoxLayout()
        for name,label,callback in [('pairingNextDoubt','仅跳转到存疑处',self._next_doubt),
            ('pairingPreview','试听所选段 ±0.5秒',self._preview),
            ('pairingRestoreTray','恢复末个滑出项',lambda:self._apply('restore_tray')),
            ('pairingUndo','撤销上一步',self._undo)]:
            button=QPushButton(label);button.setObjectName(name);button.clicked.connect(callback);bottom.addWidget(button)
        close=QPushButton('关闭');close.clicked.connect(self.accept);bottom.addWidget(close);layout.addLayout(bottom)
        self.status=QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        self.refresh(bridge.selected)

    def refresh(self,selected=None):
        b=self.bridge;p=b.document.project
        self.identity=b.document.id;self.revision=b.document.revision
        self.graph=graph_snapshot(p,b.media_controller.index);self.ids=[r.id for r in p.mappings]
        self.table.setRowCount(len(self.ids));by_id={v.id:v for v in p.visuals}
        codes={}
        for d in self.graph['diagnostics']:codes.setdefault(d['cue_id'],[]).extend(d['codes'])
        labels={'uncertain_graph_edge':'\u5173\u8054\u8fb9\u5f85\u786e\u8ba4','low_confidence':'低置信','split_merge_or_unbound':'拆合/未绑定','unlocated':'未定位',
            'short_under_5_frames':'少于5帧','short_under_100ms':'极短片段','transition_noise':'过渡碎片',
            'boundary_short_gap':'边界短断裂','boundary_text_flicker':'同框文字抖动','boundary_uncertain':'边界存疑','suspected_local_slip':'连续映射疑似错位'}
        for i,(row,cue) in enumerate(zip(p.mappings,self.graph['cues'])):
            time='未定位' if row.start is None or row.end is None else f'{row.start:.3f}–{row.end:.3f} s'
            ocr='\n'.join(by_id[v].text for v in row.visual_ids if v in by_id)
            details=' · '.join(labels.get(x,x) for x in dict.fromkeys(codes.get(row.id,[])))
            values=[f'{i+1}. {time}\n{ocr}\n{details}',_text(p.lyrics,row.lyric_ids) or '（未绑定独立原词）',cue['text'] or '（留空）']
            for j,text in enumerate(values):
                item=QTableWidgetItem(text);item.setToolTip(text);self.table.setItem(i,j,item)
            self.table.setRowHeight(i,min(160,max(74,22*max(v.count('\n')+1 for v in values))))
        self.groups.clear()
        state_names={'manual_locked':'\u4eba\u5de5\u9501\u5b9a','auto_high':'\u81ea\u52a8\u9ad8\u7f6e\u4fe1','uncertain':'\u5f85\u786e\u8ba4'}
        for group in visual_groups(p):
            title=f"{group['id']} | {len(group['cue_ids'])} \u65f6\u95f4\u6bb5 / {len(group['visual_ids'])} OCR \u4e8b\u4ef6 <-> {len(group['lyric_ids'])} \u539f\u8bcd <-> {len(group['translation_ids'])} \u8bd1\u6587 | "+' / '.join(state_names[x] for x in group['states'])
            item=QTreeWidgetItem([title]);item.setData(0,Qt.ItemDataRole.UserRole,group['cue_ids']);self.groups.addTopLevelItem(item)
            for label,key in (('Cue','cue_ids'),('OCR','visual_ids'),('Source','lyric_ids'),('Translation','translation_ids')):
                child=QTreeWidgetItem([label+': '+(', '.join(group[key]) or '(0 / unbound)')]);child.setData(0,Qt.ItemDataRole.UserRole,group['cue_ids']);item.addChild(child)
            item.setToolTip(0,title+'\n'+'\n'.join(item.child(n).text(0) for n in range(item.childCount())))
        self.unbound_items=[];self._doubt_position=-1
        p.alignment_graph.sync_unbound(p.lyrics,p.translations,p.visuals)
        for edge in p.alignment_graph.unbound_edges:
            source=_text(p.lyrics,edge['lyric_ids']);translated=_text(p.translations,edge['translation_ids'])
            observed=_text(p.visuals,edge['visual_ids'])
            title='\u672a\u7ed1\u5b9a / '+state_names.get(edge['state'],edge['state'])+' | '+(source or translated or observed or edge['id'])
            item=QTreeWidgetItem([title]);item.setData(0,Qt.ItemDataRole.UserRole,[])
            details='\n'.join(key+': '+', '.join(edge[key]) for key in ('visual_ids','lyric_ids','translation_ids'))
            item.setToolTip(0,details);item.addChild(QTreeWidgetItem([details.replace('\n',' | ')]))
            self.groups.addTopLevelItem(item);self.unbound_items.append(item)
        if selected in self.ids:self.table.selectRow(self.ids.index(selected))
        elif self.ids:self.table.selectRow(0)
        self.status.setText(f"未绑定原词 {len(self.graph['unbound_lyrics'])} · 未绑定译文 {len(self.graph['unbound_translations'])} · 滑出暂存 {len(p.settings.get('pairing_unbound_tray',[]))}。滑移不会删除末项，可用恢复或撤销取回。")

    def _select_group(self,item,column):
        ids=item.data(0,Qt.ItemDataRole.UserRole) or []
        self.table.clearSelection()
        from PySide6.QtCore import QItemSelectionModel
        for identity in ids:
            if identity in self.ids:
                index=self.table.model().index(self.ids.index(identity),0)
                self.table.selectionModel().select(index,QItemSelectionModel.SelectionFlag.Select|QItemSelectionModel.SelectionFlag.Rows)

    def _selected(self,row,column):
        if column in (1,2):self.column.setCurrentIndex(column-1)

    def _guard(self):
        b=self.bridge
        if b.document.id!=self.identity or b.document.revision!=self.revision:
            self.refresh();raise ValueError('工程在工作台打开后发生变化，已刷新；请重新选择。')
        if b._candidate_job or b._task.get('running') or b._editor_active or b._editor_composing:
            raise ValueError('请先结束识别任务或文字输入，再调整配对。')
        if b._candidate_source!='main' or b._alignment_preview.active:raise ValueError('请先返回可编辑主轴。')

    def _chosen(self):
        return [self.ids[i.row()] for i in sorted(self.table.selectionModel().selectedRows(),key=lambda x:x.row())]

    def _apply(self,operation,node_ids=()):
        try:
            self._guard();ids=self._chosen()
            draft=correction(self.bridge.document.project,operation=operation,column=self.column.currentData(),cue_ids=ids,node_ids=node_ids)
            self._guard()
            self.bridge._adopt_project(draft,'人工配对：'+operation);self.bridge._changed()
            self.refresh(ids[0] if ids else None)
        except (ValueError,RuntimeError) as exc:self.status.setText(str(exc))

    def _bind(self):
        try:self._guard()
        except ValueError as exc:self.status.setText(str(exc));return
        rows=self.bridge.document.project.lyrics if self.column.currentData()=='source' else self.bridge.document.project.translations
        if not rows:self.status.setText('该列没有独立导入的文本实例；OCR 不会伪装成独立原词。');return
        dialog=QDialog(self);dialog.setWindowTitle('选择独立文本实例（支持多选）');dialog.resize(600,450);layout=QVBoxLayout(dialog)
        listing=QListWidget();listing.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for i,row in enumerate(rows):
            p=row.subtitle_metadata.get('paragraph_index');item=QListWidgetItem(f'{i+1}. '+(f'段落{p+1} · ' if p is not None else '')+row.text)
            item.setData(Qt.ItemDataRole.UserRole,row.id);listing.addItem(item)
        layout.addWidget(listing);buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec()==QDialog.DialogCode.Accepted:self._apply('bind',[x.data(Qt.ItemDataRole.UserRole) for x in listing.selectedItems()])

    def _next_doubt(self):
        indices=sorted({d['row'] for d in self.graph['diagnostics']})
        queue=[('cue',i) for i in indices]+[('unbound',item) for item in self.unbound_items]
        if not queue:
            self.status.setText('\u5f53\u524d\u6ca1\u6709\u89e6\u53d1\u8fd9\u4e9b\u8bca\u65ad\u89c4\u5219\uff0c\u4e0d\u4ee3\u8868\u5168\u90e8\u6b63\u786e\u3002');return
        self._doubt_position=(self._doubt_position+1)%len(queue);kind,target=queue[self._doubt_position]
        if kind=='cue':
            self.table.selectRow(target);self.table.scrollToItem(self.table.item(target,0))
            self.bridge.command('row.select',{'id':self.ids[target]})
        else:
            self.groups.setCurrentItem(target);self.groups.scrollToItem(target)
            self.status.setText(target.text(0)+' | '+target.toolTip(0).replace('\n','; '))

    def _preview(self):
        try:self._guard()
        except ValueError as exc:self.status.setText(str(exc));return
        ids=self._chosen();rows=[r for r in self.bridge.document.project.mappings if r.id in ids]
        if not rows or any(r.start is None or r.end is None for r in rows):self.status.setText('所选段尚无完整时间，不能试听。');return
        media=self.bridge.media_controller;index=media.index
        if index is None:return
        start=max(0.,min(r.start for r in rows)-.5);end=min(index.duration,max(r.end for r in rows)+.5)
        # MediaController's public loop API owns the stop boundary.
        try:media.play_range(*index.frame_span(start,end),loop=False)
        except (AttributeError,TypeError):media.seek_seconds(start);self.status.setText('已定位到提前0.5秒处，请用主播放器试听。')

    def _undo(self):
        try:
            self._guard();self.bridge._history(False);self.refresh()
        except (ValueError,RuntimeError) as exc:self.status.setText(str(exc))


def pairing_dialog(bridge, parent=None):
    """Route OCR to immutable evidence capsules; preserve audio/edit workspace."""
    project = bridge.document.project
    from lyricsync.capsule_pairing import sources
    pool_for_originals = (project.settings.get('untimed_translation_pool') is True
                          and not any(row.visual_ids for row in project.mappings)
                          and any(row['start'] is not None and row['end'] is not None for row in sources(project)))
    if bridge._entry_mode == "ocr" and not pool_for_originals:
        from .ocr_capsule_pairing_dialog import OcrCapsulePairingDialog
        return OcrCapsulePairingDialog(bridge, parent)
    from .capsule_pairing_dialog import CapsulePairingDialog
    return CapsulePairingDialog(bridge, parent)
