"""Cloud-safe Qt API lifecycle: actual jobs/dialogs, fake provider and KeyStore.

No network or operating-system credential APIs are permitted by these tests.
Native Qt controls run offscreen; this is not Windows Credential Manager or OS
IME acceptance. Synthetic ledger fixtures represent already spent requests.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from copy import deepcopy
from pathlib import Path
import threading
import time
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLineEdit, QMessageBox, QPushButton
from desktop_qt.controller import Bridge
from desktop_qt.dialogs import ApiSettingsDialog
from lyricsync import ai_config, api_client, credentials
from lyricsync.api import format_review_suggestions, review_signature
from lyricsync.model import Lyric, Mapping, Translation
from lyricsync.project_document import load_document
from lyricsync.storage import Cache

MOCK_KEY = 'unit-test-only-never-a-real-secret'


@pytest.fixture(scope='module')
def application():
    return QApplication.instance() or QApplication([])


def wait_for(application, predicate, timeout=6):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(.003)
    assert predicate(), 'timed out waiting for real Qt asynchronous completion'


@pytest.fixture(autouse=True)
def isolated_credentials_and_network(monkeypatch):
    import urllib.request
    store = {}; calls = []
    def forbidden(*args, **kwargs):
        raise AssertionError('test attempted real network or native credential access')
    monkeypatch.setattr(urllib.request.OpenerDirector, 'open', forbidden)
    monkeypatch.setattr(credentials.KeyStore, 'api', forbidden)
    monkeypatch.setattr(credentials.KeyStore, 'get', lambda self, scope: (calls.append(('get', scope)), store.get(scope, ''))[1])
    monkeypatch.setattr(credentials.KeyStore, 'save', lambda self, scope, key: (calls.append(('save', scope)), store.__setitem__(scope, key), True)[2])
    monkeypatch.setattr(credentials.KeyStore, 'delete', lambda self, scope: (calls.append(('delete', scope)), store.pop(scope, None)))
    monkeypatch.setattr(api_client, 'request', forbidden)
    monkeypatch.setattr(api_client, 'request_models', forbidden)
    return store, calls


@pytest.fixture
def bridges(application, tmp_path):
    objects = []
    def make(name):
        bridge = Bridge(storage_dir=tmp_path / name)
        objects.append(bridge)
        return bridge
    yield make
    for bridge in objects:
        bridge.shutdown()
    deadline = time.monotonic() + 3
    while any(b.media_controller.worker.thread.is_alive() or b.jobs.active for b in objects) and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(.003)


def seed_old_review(bridge):
    bridge.configure_api(dict(ai_config.DEFAULTS), MOCK_KEY, True)
    p = bridge.document.project
    p.lyrics = [Lyric('l', 'QA source', 0, 1)]
    p.translations = [Translation('t', 'QA可靠中文')]
    p.mappings = [Mapping('m', ['t'], ['l'], start=0, end=1)]
    bridge.selected = 'm'
    p.settings['api_suggestions'] = [{
        'mapping_id': 'm', 'status': 'processed',
        'context_signature': review_signature(p, p.mappings[0]),
        'suggestion': {'mappings': [{'lyric_ids': ['l'], 'translation_ids': ['t'],
            'mapping_ids': ['m'], 'current_chinese': ['QA可靠中文'],
            'reason': 'QA上次已完成的理由', 'reference_meaning': 'QA历史参考译意'}], 'corrections': []}}]
    bridge._changed()


def click_dialog(dialog, standard):
    box = dialog.findChild(QDialogButtonBox)
    assert box is not None and box.button(standard) is not None
    QTest.mouseClick(box.button(standard), Qt.MouseButton.LeftButton)


def assert_no_secret(bridge, path=None):
    assert MOCK_KEY not in str(bridge.state)
    assert MOCK_KEY not in str(bridge.document.project.to_dict())
    assert MOCK_KEY not in str(bridge._log)
    settings = bridge.storage_dir / 'ai_settings.json'
    if settings.exists():
        assert MOCK_KEY not in settings.read_text(encoding='utf-8')
    if path:
        assert MOCK_KEY.encode() not in Path(path).read_bytes()


def test_cancel_spent_request_preserves_history_save_close_reopen_without_sidecar(application, bridges, monkeypatch, tmp_path):
    from desktop_qt import dialogs
    bridge = bridges('original-cache')
    seed_old_review(bridge)
    started = threading.Event(); release = threading.Event(); shown = []; calls = []
    def blocked_provider(body, key, settings=None, **callbacks):
        calls.append(body)
        assert key == MOCK_KEY
        started.set()
        assert release.wait(4), 'test did not release mock provider'
        raise InterruptedError('mock cancellation checkpoint')
    monkeypatch.setattr(api_client, 'request', blocked_provider)
    monkeypatch.setattr(dialogs, 'show_text', lambda title, text, **kwargs: shown.append((title, text)))
    # F47 hides production dispatch; exercise retained cancellation/ledger internals.
    assert not bridge.command('api.review', {'id': ''})
    assert calls == []
    bridge._api_review({'id': ''})
    wait_for(application, started.is_set)
    task_id = bridge.state['task']['id']
    try:
        assert bridge.command('task.cancel', {})
        assert bridge.jobs.active[task_id].is_set()
    finally:
        release.set()
    wait_for(application, lambda: not bridge.state['task']['running'])
    assert len(calls) == 1
    assert len(bridge.document.project.api_records) == 1
    assert bridge.document.project.api_records[0]['status'] == 'cancelled'
    result = bridge.document.project.settings['api_suggestions'][0]
    assert result['status'] == 'pending' and result['previous_suggestion']['historical']
    assert bridge.document.project.settings['api_coverage']['processed'] == 0
    assert shown and '尚未核对' in shown[-1][1] and 'QA历史参考译意' not in shown[-1][1]
    target = tmp_path / 'cancelled-review.lyria'
    assert bridge.command('project.save', {'path': str(target)})
    wait_for(application, lambda: bridge._save_running is None)
    assert not bridge.state['project']['dirty']
    assert_no_secret(bridge, target)
    persisted_id = bridge.document.id
    assert not bridge.requestClose()  # Unexported subtitles remain an explicit warning.
    assert bridge.command('close.resolve', {'choice': 'discard'})
    assert bridge._shutdown

    reopened = bridges('fresh-no-sidecar')
    assert not (reopened.storage_dir / (persisted_id + '.api.cache.sqlite')).exists()
    assert reopened.command('project.open', {'path': str(target)})
    wait_for(application, lambda: not reopened.state['task']['running'] and reopened.document.id == persisted_id)
    restored = reopened.document.project
    assert len(restored.api_records) == 1 and restored.api_records[0]['status'] == 'cancelled'
    historical = format_review_suggestions(restored, restored.settings['api_suggestions'])
    assert '尚未核对' in historical and 'QA上次已完成的理由' not in historical
    assert restored.settings['api_suggestions'][0]['previous_suggestion']['historical']
    assert not reopened._api_key, 'project reopen must never restore a plaintext credential'
    reopened.configure_api(dict(ai_config.DEFAULTS), MOCK_KEY, True)
    monkeypatch.setattr(api_client, 'request', lambda *args, **kwargs: ('{"ok":true}', {}))
    assert reopened.command('api.test', {})
    wait_for(application, lambda: not reopened.state['task']['running'])
    assert len(reopened.document.project.api_records) == 2, 'portable ledger restore must not refund the cancelled attempt'
    assert reopened.document.project.api_records[0]['status'] == 'cancelled'
    assert 'QA历史参考译意' not in format_review_suggestions(restored, restored.settings['api_suggestions'])
    assert restored.settings['api_suggestions'][0]['previous_suggestion']['suggestion']['mappings'][0]['reference_meaning']=='QA历史参考译意'
    assert_no_secret(reopened)


def test_one_time_100_does_not_change_default_or_survive_document_change(application, bridges, monkeypatch, tmp_path):
    bridge = bridges('budget-session')
    bridge.configure_api(dict(ai_config.DEFAULTS), MOCK_KEY, True)
    seed = Cache(tmp_path / 'synthetic-spent-ledger.sqlite')
    try:
        for n in range(50):
            identity = seed.reserve_call('fixture-spent-' + str(n), 50)
            seed.finish_call(identity, 'success')
        bridge.document.project.api_records = seed.records()
    finally:
        seed.close()
    calls = []; budgets = []
    monkeypatch.setattr(api_client, 'request', lambda *args, **kwargs: (calls.append(1), ('{"ok":true}', {}))[1])
    monkeypatch.setattr(bridge, '_budget_dialog', lambda state: budgets.append(deepcopy(state)))
    assert bridge.command('api.test', {})
    wait_for(application, lambda: not bridge.state['task']['running'])
    assert calls == [] and budgets[-1]['used'] == budgets[-1]['limit'] == 50
    assert bridge.command('api.resume', {})
    wait_for(application, lambda: not bridge.state['task']['running'])
    assert len(calls) == 1 and len(bridge.document.project.api_records) == 51
    assert bridge.api_settings['api_max_calls'] == bridge.document.project.settings['api_max_calls'] == 50
    assert ai_config.load(bridge.storage_dir)['api_max_calls'] == 50
    assert bridge.command('api.test', {})
    wait_for(application, lambda: not bridge.state['task']['running'])
    assert len(calls) == 1 and budgets[-1]['used'] == 51 and budgets[-1]['limit'] == 50
    old_id = bridge.document.id
    assert bridge.command('project.new', {})
    assert bridge.command('close.resolve', {'choice': 'discard'})
    assert bridge.document.id != old_id and bridge._api_resume is None
    assert not bridge.command('api.resume', {})
    assert len(calls) == 1
    assert bridge.command('api.test', {})
    wait_for(application, lambda: not bridge.state['task']['running'])
    assert len(calls) == 2 and len(bridge.document.project.api_records) == 1
    assert bridge.api_settings['api_max_calls'] == 50


def test_native_settings_cancel_discards_unapplied_fields_and_clears_password(application, bridges, isolated_credentials_and_network):
    bridge = bridges('settings-cancel')
    bridge.configure_api(dict(ai_config.DEFAULTS), MOCK_KEY, True)
    before = bridge.document.snapshot(); settings = deepcopy(bridge.api_settings)
    dialog = ApiSettingsDialog(bridge); dialog.show(); application.processEvents()
    assert dialog.key.echoMode() == QLineEdit.EchoMode.Password
    dialog.provider.setCurrentIndex(1); dialog.url.setText('https://custom.invalid/v1')
    dialog.model.setCurrentText('never-saved-model'); dialog.key.setText('another-unit-test-only-key')
    click_dialog(dialog, QDialogButtonBox.StandardButton.Cancel)
    assert dialog.result() == QDialog.DialogCode.Rejected and not dialog.isVisible()
    assert dialog.key.text() == ''
    assert bridge.document.snapshot() == before and bridge.api_settings == settings
    assert bridge._api_key == MOCK_KEY
    assert isolated_credentials_and_network[1] == []
    assert_no_secret(bridge)


def test_native_blank_key_save_preserves_session_but_never_refills_password(application, bridges, isolated_credentials_and_network):
    bridge = bridges('blank-preserve')
    bridge.configure_api(dict(ai_config.DEFAULTS), MOCK_KEY, True)
    dialog = ApiSettingsDialog(bridge); dialog.show(); application.processEvents()
    assert dialog.key.text() == ''
    click_dialog(dialog, QDialogButtonBox.StandardButton.Save)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert bridge._api_key == MOCK_KEY and dialog.key.text() == ''
    second = ApiSettingsDialog(bridge)
    assert second.key.text() == ''
    second.reject()
    assert isolated_credentials_and_network[1] == []
    assert_no_secret(bridge)


def test_native_provider_switch_with_blank_key_cannot_send_old_provider_secret(application, bridges, monkeypatch):
    bridge = bridges('provider-scope')
    bridge.configure_api(dict(ai_config.DEFAULTS), MOCK_KEY, True)
    called = []
    monkeypatch.setattr(api_client, 'request', lambda *args, **kwargs: called.append(args))
    dialog = ApiSettingsDialog(bridge); dialog.show(); application.processEvents()
    dialog.provider.setCurrentIndex(1); dialog.name.setText('Unit test custom'); dialog.url.setText('https://custom.invalid/v1')
    dialog.model.setCurrentText('unit-model')
    click_dialog(dialog, QDialogButtonBox.StandardButton.Save)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert bridge._api_key == ''
    assert not bridge.command('api.test', {})
    assert not bridge.state['task']['running'] and called == []
    assert '当前服务商' in bridge.state['error']


def test_native_explicit_mock_keystore_save_load_never_exposes_secret(application, bridges, isolated_credentials_and_network):
    store, calls = isolated_credentials_and_network
    bridge = bridges('mock-keystore')
    dialog = ApiSettingsDialog(bridge); dialog.show(); application.processEvents()
    dialog.key.setText(MOCK_KEY); dialog.persist.setEnabled(True); dialog.persist.setChecked(True)
    click_dialog(dialog, QDialogButtonBox.StandardButton.Save)
    scope = ai_config.credential_scope(bridge.api_settings)
    assert calls == [('save', scope)] and store[scope] == MOCK_KEY
    assert dialog.key.text() == ''
    bridge._api_key = ''
    second = ApiSettingsDialog(bridge); second.show(); application.processEvents()
    button = next(b for b in second.findChildren(QPushButton) if b.text() == '载入 Windows 凭据')
    button.setEnabled(True)  # Explicitly exercise only the mocked native service on Linux.
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert calls[-1] == ('get', scope) and bridge._api_key == MOCK_KEY
    assert second.key.text() == '' and MOCK_KEY not in second.status.text()
    click_dialog(second, QDialogButtonBox.StandardButton.Cancel)
    assert_no_secret(bridge)


def test_native_delete_confirmation_defaults_to_no_and_no_is_non_destructive(application, bridges, isolated_credentials_and_network):
    bridge = bridges('delete-no')
    bridge.configure_api(dict(ai_config.DEFAULTS), MOCK_KEY, True)
    before = bridge.document.snapshot()
    dialog = ApiSettingsDialog(bridge); dialog.show(); application.processEvents()
    inspected = {}
    def inspect_and_reject():
        box = next((w for w in application.topLevelWidgets() if isinstance(w, QMessageBox) and w.isVisible()), None)
        if box is None:
            inspected['error'] = 'native confirmation not visible'
            dialog.reject()
            return
        inspected['default'] = box.standardButton(box.defaultButton())
        inspected['text'] = box.text()
        QTest.mouseClick(box.button(QMessageBox.StandardButton.No), Qt.MouseButton.LeftButton)
    QTimer.singleShot(0, inspect_and_reject)
    button = next(b for b in dialog.findChildren(QPushButton) if b.text() == '清除配置与密钥')
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert 'error' not in inspected
    assert inspected['default'] == QMessageBox.StandardButton.No, 'destructive credential deletion must default to No'
    assert bridge.document.snapshot() == before and bridge._api_key == MOCK_KEY
    assert isolated_credentials_and_network[1] == []
    assert dialog.isVisible()
    click_dialog(dialog, QDialogButtonBox.StandardButton.Cancel)

def test_official_provider_allows_custom_label_and_advanced_limits(application, bridges, isolated_credentials_and_network):
    from desktop_qt.dialogs import ApiSettingsDialog
    bridge=bridges('advanced-config')
    bridge.configure_api(dict(ai_config.DEFAULTS), MOCK_KEY, True)
    dialog=ApiSettingsDialog(bridge)
    try:
        assert dialog.name.isEnabled()
        dialog.name.setText('我的 DeepSeek')
        dialog.max_calls.setValue(80)
        dialog.retries.setValue(5)
        settings=dialog.values()
        assert settings['api_provider']=='deepseek'
        assert settings['api_provider_name']=='我的 DeepSeek'
        assert settings['api_base_url']=='https://api.deepseek.com'
        assert settings['api_max_calls']==80 and settings['api_retries']==5
        assert dialog.apply(False)
        assert bridge.api_settings['api_max_calls']==80
        assert bridge.api_settings['api_retries']==5
        assert ai_config.load(bridge.storage_dir)['api_provider_name']=='我的 DeepSeek'
    finally:dialog.reject()


def test_model_list_failure_keeps_manual_model_and_previous_list(application, bridges, monkeypatch):
    bridge = bridges('model-failure-retention')
    bridge.api_state['models'] = ['previously-listed-model']
    bridge.api_settings['api_model'] = 'manually-chosen-id'
    monkeypatch.setattr(bridge, '_api_check', lambda *args: None)
    def simulated_job(label, operation, completed, **kwargs):
        completed({'status': 'unavailable', 'models': [], 'manual_entry': True,
                   'message': 'synthetic unavailable'})
    monkeypatch.setattr(bridge, '_api_job', simulated_job)
    bridge._api_simple('api.models', {})
    assert bridge.api_state['models'] == ['previously-listed-model']
    assert bridge.api_settings['api_model'] == 'manually-chosen-id'
