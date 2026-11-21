"""Explicit selected-cue manual actor dialog; style is edited independently."""
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QFormLayout,QLabel,QComboBox,QLineEdit,
    QDialogButtonBox,QMessageBox)
from .native_theme import apply_dialog_theme
from lyricsync.speakers import assign_speaker,clear_speaker,speaker_names,speaker_name


def show_speaker_dialog(bridge):
    project=bridge.document.project
    ids=[m.id for m in project.mappings if m.id in bridge._multi_selected] or ([bridge.selected] if bridge.selected else [])
    if not ids:raise ValueError('请先选择字幕。')
    if bridge._editor_composing:raise ValueError('请先完成正在输入的文字。')
    bridge.history.end()
    revision=bridge.document.revision
    dialog=QDialog();dialog.setWindowTitle('所选句的说话人');dialog.resize(480,280)
    dialog.setObjectName('speakerDialog')
    layout=QVBoxLayout(dialog)
    notice=QLabel(f'仅为已选择的 {len(ids)} 句设置人物身份；样式保持不变，可一次撤销。');notice.setWordWrap(True);layout.addWidget(notice)
    form=QFormLayout();layout.addLayout(form)
    names=speaker_names(project)
    speaker=QComboBox();speaker.setObjectName('speakerChoice')
    speaker.addItem('输入或选择说话人','');speaker.addItem('清除说话人（保留当前样式）','clear')
    for value in names:speaker.addItem(value,value)
    name=QLineEdit();name.setMaxLength(80);name.setObjectName('speakerName')
    name.setPlaceholderText('例如：主唱 A / 主唱 B / 合唱')
    form.addRow('说话人',speaker);form.addRow('名称',name)
    def load():
        value=speaker.currentData()
        name.setText(value if speaker.currentIndex()>1 else '')
        name.setEnabled(not speaker.currentData())
    speaker.currentIndexChanged.connect(load);load()
    selected_names={speaker_name(project,r) for r in project.mappings if r.id in ids}
    if len(selected_names)==1 and next(iter(selected_names)):speaker.setCurrentIndex(speaker.findData(next(iter(selected_names))))
    elif len(selected_names)>1:name.setPlaceholderText(f'混合（已选 {len(ids)} 句）')
    info=QLabel('同一说话人可使用多种命名样式，不同说话人也可共用样式。请在“样式管理”中设置字体、颜色等。说话人仅手动指定；工程与 ASS 保留，SRT/LRC 不保存。');info.setWordWrap(True);layout.addWidget(info)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText('应用到所选句');layout.addWidget(buttons)
    buttons.rejected.connect(dialog.reject)
    def apply():
        try:
            if bridge.document.revision!=revision or bridge.document.project is not project:raise ValueError('工程已改变，请关闭后重新选择范围。')
            if speaker.currentIndex()==1:clear_speaker(project,bridge.history,ids)
            else:assign_speaker(project,bridge.history,ids,name.text())
            bridge._changed();dialog.accept()
        except ValueError as exc:QMessageBox.warning(dialog,'无法应用',str(exc))
    buttons.accepted.connect(apply)
    apply_dialog_theme(dialog)
    return dialog.exec()
