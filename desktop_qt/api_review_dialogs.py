"""Human-readable, explicitly invoked text-AI review adjuncts.

These dialogs do not obtain credentials, call providers, or mutate a document
except when the user explicitly invokes the existing alignment.accept command.
Range selection only produces IDs for the existing validated alignment service.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QLabel, QPushButton, QSpinBox, QTextEdit, QVBoxLayout, QCheckBox)

from lyricsync.api import format_review_suggestions, review_signature
from lyricsync.api_alignment import validate_plan, SEMANTIC_NOTICE
from lyricsync.editing import mapping_text
from lyricsync.model import valid_time
from .native_theme import apply_dialog_theme


def label(text=''):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    return widget


def show_nonmodal(bridge,dialog):
    """Retain auxiliary windows until closing; the main window stays usable."""
    if not hasattr(bridge,'_auxiliary_dialogs'):bridge._auxiliary_dialogs=[]
    bridge._auxiliary_dialogs.append(dialog)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.finished.connect(lambda *_:bridge._auxiliary_dialogs.remove(dialog) if dialog in bridge._auxiliary_dialogs else None)
    dialog.setModal(False);dialog.show()
    return dialog


def ordered_mappings(project,index=None):
    return sorted(project.mappings,key=lambda row:(index.first(row.start) if index is not None else row.start) if valid_time(row.start,row.end) else float('inf'))


def scope_from_numbers(project, first, last, index=None):
    """Full-timeline numbering; excluded locked/ignored rows remain context only."""
    if type(first) is not int or type(last) is not int or not 1 <= first <= last <= len(project.mappings):
        raise ValueError('请输入有效的连续句号范围。')
    if last-first+1 > 250:
        raise ValueError('单次最多选择 250 句，请缩小范围。')
    locked = set(project.settings.get('manual_locked_ids', []))
    ids = [row.id for row in ordered_mappings(project,index)[first-1:last] if not row.ignored and row.id not in locked]
    if not ids:
        raise ValueError('所选范围内没有可处理的字幕（已锁定或已忽略的句子不会参与 AI 核对）。')
    return {'firstNumber': first, 'lastNumber': last, 'mapping_ids': ids}


class AlignmentRangeDialog(QDialog):
    def __init__(self, project, selected_id='',index=None):
        super().__init__()
        self.project = project;self.index=index;self.ordered=ordered_mappings(project,index)
        self.scope = None
        self.setObjectName('alignmentRangeDialog')
        self.setWindowTitle('AI 跨句拆合 · 选择范围')
        self.resize(560, 330)
        root = QVBoxLayout(self)
        root.addWidget(label('选择要核对的连续字幕范围（单次最多 250 句）。已锁定和已忽略的句子不会修改，点击确认按钮后才会发起请求。'))
        form = QFormLayout(); root.addLayout(form)
        self.first = QSpinBox(); self.first.setObjectName('alignmentFirstNumber')
        self.last = QSpinBox(); self.last.setObjectName('alignmentLastNumber')
        for field in (self.first, self.last): field.setRange(1, max(1, len(project.mappings)))
        position = next((n for n,row in enumerate(self.ordered,1) if row.id==selected_id),1)
        self.first.setValue(position); self.last.setValue(min(len(project.mappings), position+249))
        form.addRow('起始句号', self.first)
        form.addRow('结束句号', self.last)
        self.summary = label(); self.summary.setObjectName('alignmentRangeSummary'); root.addWidget(self.summary)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('获取 AI 拆合建议')
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject); root.addWidget(self.buttons)
        self.first.valueChanged.connect(self.refresh); self.last.valueChanged.connect(self.refresh)
        self.refresh()
        apply_dialog_theme(self)

    def refresh(self, *args):
        try:
            scope = scope_from_numbers(self.project, self.first.value(), self.last.value(),self.index)
            count = scope['lastNumber']-scope['firstNumber']+1
            first = self.ordered[scope['firstNumber']-1]
            last = self.ordered[scope['lastNumber']-1]
            estimate = (len(scope['mapping_ids'])+14)//15
            self.summary.setText((f"预计分 {estimate} 批请求（跨批边界与长句会自动加批，重试亦计入调用次数）。\n已选 {count} 句：可处理 {len(scope['mapping_ids'])} 句，跳过 {count - len(scope['mapping_ids'])} 句。\n第 {scope['firstNumber']} 句：{mapping_text(self.project, first)}\n第 {scope['lastNumber']} 句：{mapping_text(self.project, last)}"


                                                                                                     ))
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        except ValueError as exc:
            self.summary.setText(str(exc)); self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

    def accept(self):
        try: self.scope = scope_from_numbers(self.project, self.first.value(), self.last.value(),self.index)
        except ValueError as exc: self.summary.setText(str(exc)); return
        super().accept()


def request_alignment_scope(project, payload, selected_id='',index=None):
    """Return a validated range, or None on cancel. No API/KeyStore access."""
    if not project.mappings:
        raise ValueError('当前工程暂无可核对的字幕。')
    if 'firstNumber' in payload or 'lastNumber' in payload:
        return scope_from_numbers(project, payload.get('firstNumber'), payload.get('lastNumber'),index)
    dialog = AlignmentRangeDialog(project, selected_id,index)
    return dialog.scope if dialog.exec() == QDialog.DialogCode.Accepted else None


def format_saved_reviews(project, payload, index=None, selected_id=''):
    if 'all' in payload and type(payload['all']) is not bool:
        raise ValueError('查看范围参数无效。')
    identity = None if payload.get('all', False) else payload.get('id', selected_id)
    if identity and identity not in {row.id for row in project.mappings}:
        raise ValueError('所选字幕已不存在。')
    results = project.settings.get('api_suggestions', [])
    if not isinstance(results, list): results = []
    selected = [r for r in results if isinstance(r,dict) and (not identity or r.get('mapping_id')==identity)]
    lines = ['以下为已保存的 AI 核对记录（只读预览，不会发起新请求）：',
             '历史记录仅供参考；如需修改请返回主列表处理。', '',
             format_review_suggestions(project, selected, index)]
    return '\n'.join(lines)


def show_saved_reviews(bridge, payload):
    body = format_saved_reviews(bridge.document.project, payload, bridge.media_controller.index, bridge.selected)
    dialog = QDialog(); dialog.setObjectName('savedAiReviewDialog')
    dialog.setWindowTitle('AI 核对记录'); dialog.resize(780, 600)
    root = QVBoxLayout(dialog)
    text = QTextEdit(); text.setObjectName('savedAiReviewText'); text.setReadOnly(True); text.setPlainText(body); root.addWidget(text)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject); root.addWidget(buttons)
    apply_dialog_theme(dialog)
    show_nonmodal(bridge,dialog)


def alignment_stale(project, plan, index):
    try:
        validate_plan(project,plan,index)
        return False
    except (ValueError, TypeError, AttributeError, KeyError, IndexError): return True


def format_alignment_plan(project, plan, index=None):
    """Resolve identity/evidence to readable text; no raw JSON in the preview."""
    rows = {row.id:(number,row) for number,row in enumerate(ordered_mappings(project,index),1)}
    lyrics = {row.id:row.text for row in project.lyrics}
    visuals = {row.id:row for row in project.visuals}
    summary = plan.get('summary', {})
    def identity(value):
        entry = rows.get(value)
        return f'第 {entry[0]} 句（ID：{value}）' if entry else f'已移除字幕（ID：{value}）'
    def timing(value):
        first,last = value.get('first_frame'),value.get('last_frame')
        if first is not None and last is not None: return f'F{first}–F{last}'
        start,end = value.get('start'),value.get('end')
        return f'{start}–{end} 秒' if valid_time(start,end) else '时间待核对'
    lines = [f"共 {summary.get('total_changes', 0)} 项调整 · 拆分 {summary.get('split_count', 0)} 项 · 合并 {summary.get('merge_count', 0)} 项",
             f'受影响句数：{summary.get("before_count",0)} → {summary.get("after_count",0)}', SEMANTIC_NOTICE,
             '仅拆分或合并中文字幕，不会改写译文用词；应用后仍请人工复核。',
             '注：仅支持有对应 OCR 识别结果的拆句与并句。', '']
    if plan.get('coverage'):
        from lyricsync.api_alignment_batches import coverage_message
        coverage=plan['coverage']
        lines[0:0]=[coverage_message(coverage),
            '未完成范围：'+('、'.join(identity(i) for i in coverage['missing_mapping_ids']) or '无'),
            '待核对分段边界：'+('；'.join(' / '.join(identity(i) for i in pair) for pair in coverage['pending_boundaries']) or '无'), '']
    if alignment_stale(project, plan, index): lines += ['字幕内容已变动，此建议已失效，请重新获取。', '']
    for change in plan.get('diff', []):
        kind = {'split':'拆分','merge':'合并'}.get(change.get('kind'),'拆合建议')
        lines += [kind+'：'+'、'.join(identity(i) for i in change.get('mapping_ids',[])), '说明：'+str(change.get('reason','')), '修改前：']
        for before in change.get('before',[]): lines += ['  '+identity(before.get('id'))+' · '+timing(before), '  中文：'+str(before.get('text',''))]
        lines.append('修改后：')
        for number,after in enumerate(change.get('after',[]),1):
            lines += [f"  第 {number} 段（ID：{after.get('id', '待生成')}） · "+timing(after), '  中文：'+str(after.get('text',''))]
            for identity_value in after.get('lyric_ids',[]):lines.append('  参考原文：'+lyrics.get(identity_value,'对应原文已删除'))
            for identity_value in after.get('visual_ids',[]):
                visual=visuals.get(identity_value)
                if visual is not None:lines.append(f'  OCR 识别结果（{visual.start:.3f}–{visual.end:.3f} 秒）：{visual.text}')
            if after.get('boundary_uncertain'): lines.append('  OCR 起止边界不够确定，请结合当前画面核对。')
        lines.append('')
    for issue in plan.get('response',{}).get('semantic_issues',[]):
        lines += ['语义疑点：'+('译文可能有出入' if issue.get('kind')=='possible_mismatch' else '识别信息不足'),
                  '涉及字幕：'+'、'.join(identity(i) for i in issue.get('mapping_ids',[]))]
        for value in issue.get('mapping_ids',[]):
            if value in rows: lines.append('当前中文：'+mapping_text(project,rows[value][1]))
        for value in issue.get('lyric_ids',[]): lines.append('参考原文：'+lyrics.get(value,'对应原文已删除'))
        for value in issue.get('visual_ids',[]):
            visual = visuals.get(value)
            if visual is None: lines.append('OCR 识别结果：对应记录已删除'); continue
            span = f'F{index.first(visual.start)}–F{index.last(visual.end)}' if index is not None and valid_time(visual.start,visual.end) else '帧号未知'
            lines.append(f'OCR 识别结果（{span}）：{visual.text}')
        lines.append('说明：'+str(issue.get('reason','')))
        lines.append('')
    if not plan.get('diff'): lines.append('无需拆句或并句；如有语义疑点，请参考上方提示人工核对。')
    return '\n'.join(lines)


class AlignmentPlanDialog(QDialog):
    def __init__(self, bridge, plan):
        super().__init__(); self.bridge=bridge; self.plan=plan
        self.setObjectName('alignmentPlanDialog'); self.setWindowTitle('AI 跨句拆合建议'); self.resize(800,640)
        root=QVBoxLayout(self)
        self.status=label(); self.status.setObjectName('alignmentPlanStatus'); root.addWidget(self.status)
        self.preview=QCheckBox('在主界面预览拆合效果（未应用）');self.preview.setObjectName('alignmentPreviewToggle');root.addWidget(self.preview)
        self.preview.toggled.connect(lambda enabled:bridge.command('alignment.preview',{'enabled':enabled}))
        self.text=QTextEdit(); self.text.setObjectName('alignmentPlanText'); self.text.setReadOnly(True); root.addWidget(self.text)
        buttons=QHBoxLayout(); root.addLayout(buttons)
        self.keep=QPushButton('应用到主轴'); self.keep.setObjectName('alignmentKeep')
        self.reject_button=QPushButton('放弃建议'); self.reject_button.setObjectName('alignmentReject')
        buttons.addWidget(self.keep); buttons.addWidget(self.reject_button)
        close=QPushButton('暂不处理（保留建议）');close.setObjectName('alignmentClose');close.clicked.connect(self.close);buttons.addWidget(close)
        self.keep.clicked.connect(self.apply)
        self.reject_button.clicked.connect(self.dismiss)
        self._format_context=None;self._cached_stale=True
        bridge.stateChanged.connect(self.refresh); self.finished.connect(self.cleanup); self.refresh()
        apply_dialog_theme(self)

    def refresh(self):
        context=(id(self.bridge.document.project),self.bridge.document.revision,id(self.bridge.media_controller.index))
        if context!=self._format_context:
            self._format_context=context
            self._cached_stale=alignment_stale(self.bridge.document.project,self.plan,self.bridge.media_controller.index)
            self.text.setPlainText(format_alignment_plan(self.bridge.document.project,self.plan,self.bridge.media_controller.index))
        current=self.bridge._alignment.get('plan') is self.plan
        stale=self._cached_stale or not current
        self.status.setText('此建议已被替换或清除，请关闭当前窗口。' if not current else '字幕已变动，当前建议已失效，请重新获取。' if stale else '请结合当前画面核对拆合结果，确认无误后再应用到主轴。')
        self.keep.setEnabled(bool(self.plan.get('diff')) and not stale)
        self.preview.setEnabled(bool(self.plan.get('diff')) and not stale)
        self.preview.blockSignals(True);self.preview.setChecked(self.bridge.state.get('alignmentPreview',{}).get('active',False));self.preview.blockSignals(False)

    def apply(self):
        if self.bridge._alignment.get('plan') is not self.plan:
            self.refresh();return
        if self.bridge.command('alignment.accept',{}): self.accept()
        else:
            self.refresh(); self.status.setText(self.bridge.state.get('error') or '未能应用建议，请检查字幕是否已被修改。')

    def dismiss(self):
        if self.bridge._alignment.get('plan') is self.plan:self.bridge.command('alignment.reject',{})
        self.reject()

    def cleanup(self,*args):
        try: self.bridge.stateChanged.disconnect(self.refresh)
        except RuntimeError: pass
        # Closing this explanation does not own the main-page projection lifetime.


def show_alignment_plan(bridge, plan):
    for dialog in list(getattr(bridge,'_auxiliary_dialogs',[])):
        if isinstance(dialog,AlignmentPlanDialog):
            if dialog.plan is plan:
                dialog.show();dialog.raise_();dialog.activateWindow();return dialog
            dialog.close()
    return show_nonmodal(bridge,AlignmentPlanDialog(bridge,plan))
