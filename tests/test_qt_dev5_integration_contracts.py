"""Cross-branch UI contracts using the actual controller and normalizer.

Only isolated local settings are written. Socket connection attempts are blocked.
These are Windows/offscreen interaction checks, not foreground visual acceptance.
"""
import socket
import warnings
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox
from tests.test_qt_startup_phase5 import application, ui, find, click
from tools.qt_event_loop_wait import delay, wait_until
from desktop_qt.controller import Bridge
from desktop_qt.dialogs import ApiSettingsDialog
from lyricsync import ai_config


@pytest.fixture(autouse=True)
def no_connections(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Integration UI must not connect to any network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


@pytest.mark.parametrize("mode", ["audio", "ocr", "edit"])
def test_real_startup_card_enters_same_editor_without_implicit_work(ui, mode):
    bridge, window, warnings = ui
    original = bridge.document.id
    delay(900)
    click(window, find(window, "startupEntry_" + mode))
    wait_until(lambda: not bridge.state["startupVisible"])
    assert bridge._entry_mode == mode
    assert bridge.document.id == original  # F41: mode selection preserves identity.
    assert not find(window, "startupPage").isVisible()
    assert not bridge.jobs.active
    assert not bridge.document.source_candidates
    assert not bridge.document.main_timeline
    assert bridge.document.project.api_records == []
    assert bridge.stage == "import"
    assert not warnings


def test_real_api_dialog_normalize_save_and_reopen_all_advanced_fields(application, tmp_path):
    home = tmp_path / "isolated-api-settings"
    bridge = Bridge(storage_dir=home)
    reopened = None
    dialog = ApiSettingsDialog(bridge)
    try:
        dialog.show(); delay(60)
        dialog.advanced.setChecked(True)
        dialog.name.setText("保留的官方服务名称")
        dialog.model.setCurrentText("deepseek-flash")
        dialog.transport.setCurrentIndex(dialog.transport.findData("sse"))
        dialog.thinking.setCurrentIndex(dialog.thinking.findData("disabled"))
        dialog.reasoning.setCurrentIndex(dialog.reasoning.findData("low"))
        dialog.idle_timeout.setValue(25)
        dialog.total_timeout.setValue(90)
        expected = dict(api_transport="sse", api_thinking="disabled", api_reasoning_effort="low",
                        api_idle_timeout_seconds=25, api_total_timeout_seconds=90)
        # Do not stub normalized or Bridge.configure_api: this is the integration seam.
        assert {k: dialog.values().get(k) for k in expected} == expected
        QTest.mouseClick(dialog._api_buttons.button(QDialogButtonBox.StandardButton.Save), Qt.LeftButton)
        delay(30)
        assert not dialog.isVisible()
        with warnings.catch_warnings(record=True) as repeated_close:
            warnings.simplefilter("always")
            dialog.reject(); dialog.reject()
        assert not repeated_close, [str(item.message) for item in repeated_close]
        saved = ai_config.load(home)
        assert {k: saved.get(k) for k in expected} == expected
        assert saved["api_provider_name"] == "保留的官方服务名称"
        assert saved["api_model"] == "deepseek-flash"
        assert "api_key" not in saved
        assert bridge.document.project.api_records == []
        reopened = Bridge(storage_dir=home)
        second = ApiSettingsDialog(reopened)
        try:
            assert {k: second.values().get(k) for k in expected} == expected
            assert second.name.text() == "保留的官方服务名称"
            second.model.setCurrentText("manual-model-remains")
            reopened.api_state.update(models=[], status="Synthetic list failure; no request was sent")
            second.refresh_status()
            assert second.model.currentText() == "manual-model-remains"
        finally:
            second.reject()
    finally:
        dialog.reject()
        bridge.shutdown(); bridge.wait_for_shutdown(application)
        if reopened is not None:
            reopened.shutdown(); reopened.wait_for_shutdown(application)
