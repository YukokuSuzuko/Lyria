"""Honest update status; no faux availability or unsafe execution controls."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout
from lyricsync.software_update import startup_status
from .native_theme import apply_dialog_theme


class SoftwareUpdateDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('软件更新')
        self.setObjectName('softwareUpdateDialog')
        self.setMinimumWidth(380)
        self.resize(520, 250)
        status = startup_status()
        root = QVBoxLayout(self)
        for name, text in (('updateCurrentVersion', '当前版本：Lyria ' + status.current_version),
                           ('updateStatusTitle', status.title), ('updateStatusDetail', status.detail),
                           ('updateConsentNotice', '发布渠道就绪后，应先展示版本与更新内容，再由你同意下载安装。检查到更新本身不代表安装授权。')):
            label = QLabel(text)
            label.setObjectName(name)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            root.addWidget(label)
        check = QPushButton('检查更新（发布渠道未就绪）')
        check.setObjectName('checkUpdateUnavailable')
        check.setEnabled(status.can_check)
        root.addWidget(check)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        root.addWidget(box)
        apply_dialog_theme(self)
