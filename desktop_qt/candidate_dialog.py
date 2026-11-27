"""Review one immutable OCR/alignment result and explicitly apply checked rows."""
from copy import deepcopy
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QHBoxLayout, QHeaderView, QLabel, QMenu, QMessageBox, QPlainTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from lyricsync.candidate_tracks import can_regroup_imported_seed
from lyricsync.editing import mapping_text
from .audio_preview import AudioPreview


def readable_time(value):
    if value is None:
        return '未打轴'
    seconds, ms = divmod(round(value * 1000), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    prefix = f'{hours}:{minutes:02d}' if hours else str(minutes)
    return f'{prefix}:{seconds:02d}.{ms:03d}'


def readable_range(start, end):
    return '未打轴' if start is None and end is None else readable_time(start) + ' – ' + readable_time(end)


def candidate_description(record, row, visuals=None):
    if row['text']:
        return row['text']
    if visuals is None:
        visuals = {v['id']: v['text'] for v in record['evidence']['visuals']}
    source = ' / '.join(visuals[i] for i in row['provenance']['visual_ids'] if i in visuals)
    return source or '待填写歌词'


class CandidateDialog(QDialog, AudioPreview):
    def __init__(self, bridge, record):
        super().__init__()
        self._init_preview()
        self.bridge, self.record = bridge, record
        self.document, self.epoch = bridge.document, bridge.epoch
        self.alive, self.request, self._preview_job = True, None, None
        self._bindings = {}
        self._targets = {m.id:m for m in self.document.main_timeline}
        self._positions = {identity:i for i, identity in enumerate(self._targets, 1)}
        self._locked = set(self.document.project.settings.get('manual_locked_ids', []))
        self._regroup = can_regroup_imported_seed(self.document, record, allow_visual_extras=True)
        self.setWindowTitle('核对并应用打轴时间' if record['source'] == 'vocal' else '核对并应用识别结果')
        self.resize(890, 510)
        root = QVBoxLayout(self)
        top = QHBoxLayout()
        root.addLayout(top)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        top.addWidget(self.status, 1)
        self.filter = QComboBox()
        self.filter.setObjectName('candidateStatusFilter')
        for title, value in [('全部句子','all'),('未关联主轴','unmapped'),('缺少时间','missing'),
                             ('时长需要核对','duration'),('时间变化较大','delta'),('识别不确定','confidence')]:
            self.filter.addItem(title,value)
        top.addWidget(self.filter)
        self.table = QTableWidget(len(record['rows']), 4)
        self.table.setObjectName('sourceCandidateTable')
        self.table.setHorizontalHeaderLabels(['☑  句号与歌词','时间对比','变动说明','试听'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setWordWrap(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        for col, size in [(1,215),(2,178),(3,52)]:
            header.resizeSection(col,size)
        self.table.verticalHeader().setMinimumSectionSize(49)
        visuals = {v['id']:v['text'] for v in record['evidence']['visuals']}
        regions = {}
        for visual in record['evidence']['visuals']:
            evidence = visual.get('evidence', {})
            regions[visual['id']] = next((r['name'] for r in evidence.get('roi_configuration', [])
                                          if r.get('id') == evidence.get('roi_owner') and r.get('name')), '')
        for i, row in enumerate(record['rows']):
            names = dict.fromkeys(regions[vid] for vid in row['provenance']['visual_ids'] if regions.get(vid))
            source = ' · '.join(names)
            item = QTableWidgetItem(f"{i+1}.  " + candidate_description(record,row,visuals) + ('  [' + source + ']' if source else ''))
            target = self._targets.get(row['sentence_id'])
            locked = target is not None and target.id in self._locked
            flags = item.flags() | Qt.ItemFlag.ItemIsUserCheckable
            item.setFlags(flags & ~Qt.ItemFlag.ItemIsUserCheckable if locked else flags)
            item.setData(Qt.ItemDataRole.UserRole, row['id'])
            timed = row['start'] is not None and row['end'] is not None
            safe = timed and not row.get('ignored') and not locked and (
                self._regroup or (target is None and not self._targets)
                or (target is not None and not self._protected(target)))
            item.setCheckState(Qt.CheckState.Checked if safe else Qt.CheckState.Unchecked)
            self.table.setItem(i,0,item)
            self._describe(i)
            play = QTableWidgetItem('▶' if timed else '—')
            play.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            play.setToolTip(f'试听第 {i+1} 句' if timed else '该句尚未定位')
            self.table.setItem(i,3,play)
        self.table.cellClicked.connect(lambda row,col:self.listen_row(row) if col==3 else None)
        self.table.resizeRowsToContents()
        root.addWidget(self.table,1)
        self.unmapped = QWidget()
        bindbar = QHBoxLayout(self.unmapped)
        bindbar.setContentsMargins(0,0,0,0)
        self.link_row = QPushButton('改为关联现有句…')
        self.link_row.setCheckable(True)
        bindbar.addWidget(self.link_row)
        self.target = QComboBox()
        self.target.setEditable(True)
        self.target.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.target.addItem('选择对应主轴句',None)
        for i,m in enumerate(self._targets.values(),1):
            self.target.addItem(f'{i}. {mapping_text(self.document.project,m) or "待填写歌词"}',m.id)
        self.target.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        bindbar.addWidget(self.target,1)
        self.bind_row = QPushButton('关联当前行')
        self.bind_row.clicked.connect(self.bind_selected)
        bindbar.addWidget(self.bind_row)
        self.link_row.toggled.connect(self.target.setVisible)
        self.link_row.toggled.connect(self.bind_row.setVisible)
        self.target.hide()
        self.bind_row.hide()
        self.table.itemSelectionChanged.connect(self._binding_visibility)
        self._binding_visibility()
        root.addWidget(self.unmapped)
        footer = QHBoxLayout()
        root.addLayout(footer)
        options = QPushButton('选择 ▾')
        menu = QMenu(options)
        menu.addAction('勾选可见句',lambda:self.check_visible(True))
        menu.addAction('取消勾选可见句',lambda:self.check_visible(False))
        menu.addAction('停止试听',self._stop_preview)
        self.replace_text = menu.addAction('应用时同时采用勾选行的文字')
        self.replace_text.setCheckable(True)
        self.replace_text.toggled.connect(self.changed)
        if record['source']=='vocal':
            menu.addAction('重跑所选句的打轴…', self.retry_selected)
        options.setMenu(menu)
        footer.addWidget(options)
        from lyricsync.translation_pool import pairing_label
        pair = QPushButton(('候选译文时间修复' if record['source']=='ocr' else pairing_label(self.document.project,record))+'…')
        pair.clicked.connect(self.pair)
        footer.addWidget(pair)
        footer.addStretch()
        close = QPushButton('关闭')
        close.clicked.connect(self.reject)
        footer.addWidget(close)
        self.apply = QPushButton()
        self.apply.setObjectName('applyCheckedCandidates')
        self.apply.setDefault(True)
        self.apply.clicked.connect(self.commit)
        footer.addWidget(self.apply)
        self.filter.currentIndexChanged.connect(self._filter_rows)
        self.table.itemChanged.connect(self.changed)
        self.finished.connect(self.close_review)
        from .native_theme import apply_dialog_theme
        apply_dialog_theme(self)
        self.changed()

    def _protected(self, target):
        return target is not None and (target.human_edited or target.confirmed or target.locked or target.id in self._locked)

    def _target(self, row):
        return self._targets.get(self._bindings.get(row['id'],row['sentence_id']))

    def _binding_visibility(self):
        self.link_row.setChecked(False)
        selected = self.table.selectionModel().selectedRows()
        self.unmapped.setVisible(bool(self._targets) and len(selected)==1 and
                                 self._target(self.record['rows'][selected[0].row()]) is None)

    def _describe(self, i):
        row = self.record['rows'][i]
        target = self._targets.get(self._bindings.get(row['id'], row['sentence_id']))
        before = readable_range(target.start,target.end) if target else '无对应句'
        after = readable_range(row['start'],row['end'])
        times = before + '\n→ ' + after
        if target and None not in (row['start'],row['end'],target.start,target.end):
            times += f"\n入点 {row['start']-target.start:+.2f} 秒 · 出点 {row['end']-target.end:+.2f} 秒"
        self.table.setItem(i,1,QTableWidgetItem(times))
        if target and target.id in self._locked:
            note = '已锁定'
        elif self._protected(target):
            note = '已手改或确认，默认保护\n勾选可更新时间'
        elif target:
            note = f'将更新第 {self._positions[target.id]} 句时间'
        else:
            note = '没有匹配到现有字幕' + ('（尚无译文）' if not row['provenance']['translation_ids'] else '')
            note += '\n将作为新句插入时间轴' if self.table.item(i,0).checkState()==Qt.CheckState.Checked else '\n勾选可作为新句加入'
        if row['start'] is None or row['end'] is None:
            note += '\n缺少识别时间'
        self.table.setItem(i,2,QTableWidgetItem(note))

    def chosen(self):
        return [i for i in range(self.table.rowCount()) if not self.table.isRowHidden(i)
                and self.table.item(i,0).checkState()==Qt.CheckState.Checked]

    def check_visible(self, checked):
        self.table.blockSignals(True)
        try:
            for i in range(self.table.rowCount()):
                item = self.table.item(i,0)
                if not self.table.isRowHidden(i) and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                    item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        finally:
            self.table.blockSignals(False)
        self.changed()

    def _filter_rows(self):
        mode = self.filter.currentData()
        for i,r in enumerate(self.record['rows']):
            target = self._targets.get(self._bindings.get(r['id'],r['sentence_id']))
            timed = r['start'] is not None and r['end'] is not None
            delta = timed and target and target.start is not None and target.end is not None and max(
                abs(r['start']-target.start),abs(r['end']-target.end))>.5
            show = (mode=='all' or mode=='unmapped' and not target or mode=='missing' and not timed
                    or mode=='duration' and timed and (r['end']-r['start']<.2 or r['end']-r['start']>25)
                    or mode=='delta' and delta or mode=='confidence' and (r.get('confidence') or 0)<.6)
            self.table.setRowHidden(i,not show)
        self.changed()

    def changed(self, *args):
        self.request = None
        if self._preview_job:
            self.bridge.jobs.cancel(self._preview_job)
        self._preview_job = None
        n = len(self.chosen())
        self.apply.setText(f'应用已勾选的 {n} 句')
        self.apply.setEnabled(self.alive and n > 0)
        targets = [self._target(r) for r in self.record['rows']]
        unmatched = targets.count(None)
        protected = sum(self._protected(t) for t in targets)
        total = len(targets)
        self.status.setText(f'当前字幕尚未改动。本轮共 {total} 句（可更新时间 {total-unmatched-protected} 句 · '
                            f'未匹配 {unmatched} 句 · 手改或锁定保护 {protected} 句），已勾选 {n} 句。')
        self.table.blockSignals(True)
        try:
            for i,t in enumerate(targets):
                if t is None:
                    self._describe(i)
        finally:
            self.table.blockSignals(False)

    def bind_selected(self):
        selected = self.table.selectionModel().selectedRows()
        pos = self.target.currentIndex()
        if not selected or pos < 1 or self.target.currentText()!=self.target.itemText(pos):
            self.status.setText('先点选需要关联的一行，再从列表选择对应的主轴句。')
            return
        if len(selected)!=1:
            self.status.setText('一次只能将一行关联到一个主轴句。')
            return
        i = selected[0].row()
        dest = self.target.currentData()
        if dest in self._locked:
            self.status.setText('所选主轴句已锁定。')
            return
        row = self.record['rows'][i]
        self._bindings[row['id']] = dest
        self._describe(i)
        self.table.item(i,0).setCheckState(Qt.CheckState.Checked)
        self._filter_rows()
        self._binding_visibility()

    def _current_session(self):
        return self.bridge.document is self.document and self.bridge.epoch==self.epoch

    def _selection(self):
        return (self.document.revision, self.bridge.selected, frozenset(self.bridge._multi_selected),
                self.bridge._candidate_source, tuple(self.chosen()), tuple(self._bindings.items()), self.replace_text.isChecked())

    def commit(self):
        if not self._current_session():
            self.status.setText('工程已切换，请关闭此窗并打开当前工程的识别结果。')
            return
        if self.bridge._editor_active or self.bridge._editor_composing:
            self.status.setText('请先结束当前文字编辑。')
            return
        checked = self.chosen()
        if not checked or self.request is not None:
            return
        rows = [self.record['rows'][i] for i in checked]
        ids = [r['id'] for r in rows]
        bindings = {i:self._bindings[i] for i in ids if i in self._bindings}
        state = self._selection()
        request = self.request = object()
        self.apply.setEnabled(False)
        self.status.setText('正在核对所选句、主轴、素材与画面帧…')
        regroup = self._regroup and len(checked)==len(self.record['rows']) and not bindings
        def finished(plan,error):
            if not self.alive or self.request is not request:
                return
            self._preview_job = None
            try:
                if error:
                    raise ValueError(str(error))
                if not self._current_session() or self._selection()!=state:
                    raise ValueError('工程、主轴或勾选行已改变，请重新核对并应用。')
                blocked = [e['blocked'] for e in plan['entries'] if e['blocked']]
                if blocked:
                    raise ValueError(blocked[0])
                if self.replace_text.isChecked():
                    answer = QMessageBox.question(self,'采用勾选行的文字',f'同时替换 {len(rows)} 句的文字与时间吗？可以整步撤销。')
                    if answer != QMessageBox.StandardButton.Yes:
                        self.changed()
                        return
                    if not self._current_session() or self.request is not request or self._selection()!=state:
                        raise ValueError('确认期间工程或勾选行已改变，请重新核对。')
                if self.bridge._editor_active or self.bridge._editor_composing:
                    raise ValueError('请先完成当前编辑，再应用所选句。')
                changed = self.bridge.candidate_apply(plan)
                self.bridge._message(f'已应用 {len(changed)} 句，可按 Ctrl+Z 撤销。')
                self.accept()
            except (ValueError,RuntimeError) as exc:
                self.changed()
                self.status.setText(str(exc))
        self._preview_job = self.bridge.candidate_preview(self.record['id'],ids,bindings,
            any(self._target(row) is None for row in rows),finished,apply_text=self.replace_text.isChecked(),explicit_visual_regroup=regroup)

    def pair(self):
        if not self._current_session():
            self.status.setText('工程已切换，请打开当前工程的配对工作台。')
            return
        if self.record['source'] != 'ocr':
            self.reject()
            self.bridge.switchCandidateSource('main')
            self.bridge.command('pairing.workbench',{})
            return
        self.changed()
        from lyricsync.candidate_tracks import pairing_material
        from .ocr_capsule_pairing_dialog import OcrCapsulePairingDialog
        from .timeline_views import current_candidate
        before = self._selection()
        document = deepcopy(self.document)
        index = deepcopy(self.bridge.media_controller.index)
        self.request = request = object()
        self.status.setText('正在载入本轮 OCR 识别结果与已有译文…')
        def loaded(result, error):
            if not self.alive or self.request is not request:
                return
            self.changed()
            if error or not self._current_session() or before!=self._selection() or not current_candidate(self.document,self.record):
                self.status.setText(str(error) if error else '工程已改变，请重新打开本轮识别结果。')
                return
            material, token = result
            self.reject()
            self.bridge.switchCandidateSource('main')
            desk = OcrCapsulePairingDialog(self.bridge, candidate=(self.record['id'], token, material))
            desk.exec()
        self._preview_job = self.bridge._start_job('核对配对所用画面', lambda cancel, progress:
            pairing_material(document,self.record['id'],index,cancel), loaded, foreground=False)

    def close_review(self,*args):
        self.alive = False
        self._stop_preview()
        self.changed()

    def listen_row(self, i):
        if not self._current_session():
            self.status.setText('工程已切换，请打开当前工程的结果。')
            return
        row = self.record['rows'][i]
        if row['start'] is None or row['end'] is None:
            self.status.setText('该句尚未定位。')
            return
        p = self.document.project
        path = p.inputs.get('audio') or p.inputs.get('video')
        if not path:
            self.status.setText('请先载入原媒体。')
            return
        start = max(0.,row['start']-.5)
        end = min(getattr(self.bridge.media_controller.index,'duration',row['end']+.5),row['end']+.5)
        if end <= start:
            self.status.setText('该句超出媒体范围。')
            return
        self._listen_audio(dict(source_path=path,offset_seconds=start,duration_seconds=end-start),False)

    def retry_selected(self):
        if not self._current_session():
            self.status.setText("工程已切换，请重新打开识别结果。"); return
        selected=self.chosen()
        if not selected:self.status.setText('请选择需要重跑的候选句。');return
        from copy import deepcopy
        source={row['id']:row for row in self.record['evidence']['lyrics']}
        rows=[]
        for index in selected:
            row=self.record['rows'][index];refs=row['provenance']['lyric_ids']
            text='\n'.join(source[i]['text'] for i in refs if i in source) or row['text']
            rows.append(dict(id=row.get('sentence_id') or row['id'],text=text,lyric_ids=list(refs)))
        dialog=QDialog(self);dialog.setWindowTitle('确认原词与局部音频范围');layout=QVBoxLayout(dialog)
        label=QPlainTextEdit('仅重跑下面所选原词，不改变现有主轴。请确认它们是演唱原词而非译文：\n'+'\n'.join(row['text'] for row in rows));label.setReadOnly(True);label.setMaximumHeight(180);layout.addWidget(label)
        start=QDoubleSpinBox();end=QDoubleSpinBox();duration=getattr(self.bridge.media_controller.index,'duration',0.)
        for field in (start,end):field.setRange(0,max(.001,duration));field.setDecimals(3);field.setSuffix(' 秒')
        times=[value for i in selected for value in (self.record['rows'][i]['start'],self.record['rows'][i]['end']) if isinstance(value,(float,int))]
        start.setValue(max(0,min(times)-.5) if times else 0);end.setValue(min(duration,max(times)+.5) if times else duration)
        layout.addWidget(QLabel('音频开始'));layout.addWidget(start);layout.addWidget(QLabel('音频结束'));layout.addWidget(end)
        confirm=QCheckBox('确认这些文字是实际演唱原词，使用上面的音频范围');layout.addWidget(confirm)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);layout.addWidget(buttons)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False);confirm.toggled.connect(buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        if end.value()<=start.value():self.status.setText('结束必须晚于开始，未启动重跑。');return
        self._stop_preview()
        self.bridge._configure_vocal(initial_options=dict(text_source='external',source_rows=deepcopy(rows),
            source_origin='用户选择的局部重跑原词',offset_seconds=start.value(),duration_seconds=end.value()-start.value()))
