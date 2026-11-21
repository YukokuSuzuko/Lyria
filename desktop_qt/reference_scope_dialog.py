"""Explicit, read-only selection of unlinked source evidence for one AI run."""
from PySide6.QtWidgets import QDialog,QVBoxLayout,QFormLayout,QSpinBox,QTextEdit,QDialogButtonBox
from .api_review_dialogs import label
from .native_theme import apply_dialog_theme
from lyricsync.api_alignment_batches import (reference_scope, reference_scope_size,
                                             MAX_UNLINKED_REFERENCE_CHARS)


class ReferenceScopeDialog(QDialog):
    def __init__(self, project):
        super().__init__()
        self.project=project
        self.rows=reference_scope(project)
        self.reference_ids=None
        self.setWindowTitle('选择本次发送的原文参考')
        self.setObjectName('referenceScopeDialog')
        self.resize(620,480)
        root=QVBoxLayout(self)
        root.addWidget(label('原文参考过长，请明确选择本次发送的连续段落。这里只限制参考文字，不会把原文与字幕按行号配对，也不删除工程内其他原文。已关联或时间重叠的证据仍保留。确认后继续当前 AI 请求，可能产生费用。'))
        form=QFormLayout();root.addLayout(form)
        self.first=QSpinBox();self.last=QSpinBox()
        for box in (self.first,self.last):box.setRange(1,max(1,len(self.rows)))
        self.first.setValue(1);self.last.setValue(len(self.rows))
        form.addRow('参考原文开始行',self.first);form.addRow('参考原文结束行',self.last)
        self.summary=label();root.addWidget(self.summary)
        self.preview=QTextEdit();self.preview.setReadOnly(True);root.addWidget(self.preview)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('使用此参考范围')
        self.buttons.accepted.connect(self.accept);self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self.first.valueChanged.connect(self.refresh);self.last.valueChanged.connect(self.refresh)
        self.refresh();apply_dialog_theme(self)

    def refresh(self,*args):
        rows=self.rows[self.first.value()-1:self.last.value()]
        size=reference_scope_size(rows)
        valid=bool(rows) and size<=MAX_UNLINKED_REFERENCE_CHARS
        self.summary.setText(f'已选 {len(rows)} 行，含结构信息共 {size} / {MAX_UNLINKED_REFERENCE_CHARS} 字符。'+
                             ('' if valid else '请缩小范围；未发送任何请求。'))
        preview='\n'.join(f'{n}. {row.text}' for n,row in enumerate(rows,self.first.value()))
        # Large rejected selections remain bounded in UI, not silently truncated in a request.
        self.preview.setPlainText(preview[:16000]+('\n[预览较长，余下文字未显示；当前范围尚不能发送。]' if len(preview)>16000 else ''))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(valid)

    def accept(self):
        ids=[row.id for row in self.rows[self.first.value()-1:self.last.value()]]
        try:
            rows=reference_scope(self.project,ids)
        except ValueError as exc:
            self.summary.setText(str(exc));return
        if reference_scope_size(rows)>MAX_UNLINKED_REFERENCE_CHARS:return
        self.reference_ids=ids
        super().accept()


def request_reference_scope(project,payload):
    if 'referenceIds' in payload:
        rows=reference_scope(project,payload['referenceIds'])
        if reference_scope_size(rows)>MAX_UNLINKED_REFERENCE_CHARS:
            raise ValueError('所选原文参考超过安全范围，请重新选择。')
        return [row.id for row in rows]
    if reference_scope_size(reference_scope(project))<=MAX_UNLINKED_REFERENCE_CHARS:
        return None
    dialog=ReferenceScopeDialog(project)
    return dialog.reference_ids if dialog.exec()==QDialog.DialogCode.Accepted else False
