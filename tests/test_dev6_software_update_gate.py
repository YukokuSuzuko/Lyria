import sys
import urllib.request
from lyricsync.software_update import startup_status


def test_unconfigured_release_is_not_latest_or_installable(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No network calls are allowed without a trusted release channel')
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    state = startup_status()
    assert state.state == 'not_configured'
    assert not state.can_check and not state.can_download and not state.can_install
    assert '不代表当前已是最新版' in state.detail


def test_update_dialog_is_plain_honest_and_inert():
    from PySide6.QtWidgets import QApplication, QPushButton, QLabel
    from PySide6.QtCore import Qt
    from desktop_qt.software_update_dialog import SoftwareUpdateDialog
    app = QApplication.instance() or QApplication([])
    dialog = SoftwareUpdateDialog()
    assert not dialog.findChild(QPushButton, 'checkUpdateUnavailable').isEnabled()
    for label in dialog.findChildren(QLabel):
        assert label.textFormat() == Qt.TextFormat.PlainText
    assert '签名' in dialog.findChild(QLabel, 'updateStatusDetail').text()
    dialog.close()
