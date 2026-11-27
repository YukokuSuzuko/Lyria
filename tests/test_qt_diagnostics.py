"""Real Qt wiring for local diagnostics; not native Windows acceptance."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QObject, QTimer, QUrl, qWarning
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from desktop_qt.controller import Bridge
from desktop_qt.diagnostics import DiagnosticStore, RuntimeHooks, StartupFailure, show_startup_failure

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture(scope='module')
def application():
    # Match app_qt before the first QML engine caches platform controls.
    # Loading Windows controls here and switching to Basic in later suites
    # can crash Qt when an indeterminate progress bar starts animating.
    QQuickStyle.setStyle('Basic')
    return QApplication.instance() or QApplication([])

@pytest.fixture
def bridge(application,tmp_path):
    obj=Bridge(storage_dir=tmp_path/'storage')
    yield obj
    obj.shutdown()
    deadline=time.monotonic()+3
    while obj.media_controller.worker.thread.is_alive() and time.monotonic()<deadline:
        application.processEvents();time.sleep(.005)


def test_command_errors_are_durable_but_payloads_are_excluded(bridge,tmp_path):
    assert not bridge.command('SECRET unknown command', {'api_key':'SECRET','text':'PRIVATE LYRICS'})
    assert bridge.command('diagnostics.export', {'path':str(tmp_path/'diagnostics.json')})
    contents=(tmp_path/'diagnostics.json').read_text()
    assert 'command-failed' in contents and 'ValueError' in contents
    assert 'SECRET' not in contents and 'PRIVATE LYRICS' not in contents
    assert bridge.state['capabilities']['diagnostics.export']


def test_export_cancel_preserves_project_and_files(bridge,tmp_path):
    before=bridge.document.snapshot()
    with patch.object(QFileDialog,'getSaveFileName',return_value=('', '')):
        assert not bridge.command('diagnostics.export',{})
    assert bridge.document.snapshot()==before
    assert not list(tmp_path.glob('*.json'))


def test_export_failed_and_repeated_success_dont_change_document(bridge,tmp_path):
    before=bridge.document.snapshot()
    assert not bridge.command('diagnostics.export',{'path':str(tmp_path/'missing'/'bad.json')})
    assert '请选择可写入' in bridge.state['error']
    target=tmp_path/'report.json'
    assert bridge.command('diagnostics.export',{'path':str(target)})
    assert bridge.command('diagnostics.export',{'path':str(target)})
    assert bridge.document.snapshot()==before
    assert 'export-failed' in target.read_text()


def test_qt_warning_is_recorded_without_text(application,tmp_path):
    store=DiagnosticStore(tmp_path)
    hooks=RuntimeHooks(store)
    hooks.install_qt()
    try:qWarning('SECRET https://private.example /home/private/video.mp4')
    finally:hooks.restore()
    assert 'qt-warning' in store.path.read_text()
    assert 'SECRET' not in store.path.read_text()


def test_real_qml_has_working_export_button(application,bridge,tmp_path):
    engine=QQmlApplicationEngine()
    engine.rootContext().setContextProperty('bridge',bridge)
    engine.load(QUrl.fromLocalFile(str(ROOT/'desktop_qt/qml/Main.qml')))
    assert engine.rootObjects()
    window=engine.rootObjects()[0]
    window.setProperty('workspace','settings')
    application.processEvents()
    button=window.findChild(QObject,'exportDiagnosticsButton')
    assert button and button.property('enabled')
    target=tmp_path/'from-button.json'
    with patch.object(QFileDialog,'getSaveFileName',return_value=(str(target), '')):
        button.clicked.emit()
    assert json.loads(target.read_text())['kind']=='lyria-local-diagnostics'
    window.setProperty('allowClose',True)
    window.close()
    engine.deleteLater();application.processEvents()


def test_startup_dialog_export_cancel_then_save_then_close(application,tmp_path):
    store=DiagnosticStore(tmp_path/'storage')
    target=tmp_path/'startup.json'
    rounds=[]
    def respond():
        box=next(w for w in application.topLevelWidgets() if isinstance(w,QMessageBox) and w.isVisible())
        rounds.append(1)
        label='导出诊断日志…' if len(rounds)<3 else '关闭'
        next(b for b in box.buttons() if b.text()==label).click()
        if len(rounds)<3:QTimer.singleShot(25,respond)
    QTimer.singleShot(25,respond)
    with patch.object(QFileDialog,'getSaveFileName',side_effect=[('', ''),(str(target),'')]):
        show_startup_failure(StartupFailure(),store)
    assert len(rounds)==3
    assert json.loads(target.read_text())['kind']=='lyria-local-diagnostics'


def test_no_qml_root_uses_startup_dialog_and_nonzero_exit(tmp_path):
    # Separate interpreter avoids creating a second QApplication in pytest.
    script='''
import sys
from PySide6.QtQml import QQmlApplicationEngine
from desktop_qt import diagnostics
import app_qt
class BrokenEngine(QQmlApplicationEngine):
    def load(self, url):
        super().load('data:text/plain,import QtQuick; NotARealType {}')
app_qt_marker=[]
import PySide6.QtQml
PySide6.QtQml.QQmlApplicationEngine=BrokenEngine
diagnostics.show_startup_failure=lambda error,store: print('STARTUP_PROMPT', type(error).__name__)
raise SystemExit(app_qt.main(['--storage-dir',sys.argv[1]]))
'''
    result=subprocess.run([sys.executable,'-c',script,str(tmp_path)],cwd=ROOT,env=os.environ.copy(),capture_output=True,text=True,timeout=20)
    assert result.returncode==2, result.stdout+result.stderr
    assert 'STARTUP_PROMPT StartupFailure' in result.stdout
    assert any('qml-load-failed' in p.read_text() for p in (tmp_path/'diagnostics').glob('*.jsonl'))


def test_selfcheck_does_not_shadow_diagnostics_callback(tmp_path):
    script = '''
import sys
from PySide6.QtQml import QQmlApplicationEngine, QQmlError
from PySide6.QtCore import QTimer, QUrl
import PySide6.QtQml
class WarningEngine(QQmlApplicationEngine):
    def load(self, url):
        super().load(url)
        QTimer.singleShot(50, lambda:QQmlApplicationEngine.load(self, 'data:text/plain,import QtQuick; NotARealType {}'))
PySide6.QtQml.QQmlApplicationEngine=WarningEngine
import app_qt
raise SystemExit(app_qt.main(['--storage-dir',sys.argv[1],'--self-check',sys.argv[2],'--exit-after','150']))
'''
    result=subprocess.run([sys.executable,'-c',script,str(tmp_path/'storage'),str(tmp_path/'selfcheck.json')],cwd=ROOT,env=os.environ.copy(),capture_output=True,text=True,timeout=20)
    assert result.returncode==0, result.stdout+result.stderr
    assert json.loads((tmp_path/'selfcheck.json').read_text(encoding='utf-8'))['ok']
    logs=''.join(p.read_text() for p in (tmp_path/'storage'/'diagnostics').glob('*.jsonl'))
    assert 'qml-warning' in logs
    assert 'python-unhandled' not in logs and 'SECRET' not in logs
