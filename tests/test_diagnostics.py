"""No-Qt diagnostic privacy, persistence and startup-boundary regressions."""
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desktop_qt import diagnostics as diag


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_exception_details_exclude_message_paths_and_source_lines(self):
        store = diag.DiagnosticStore(self.root)
        secret = 'sk-private-secret-example'
        try:
            raise PermissionError(13, secret + ' /home/private/song.txt https://private.example/api?token=' + secret)
        except Exception as exc:
            store.record('command-failed', exc)
        store.record('ui-error', source=diag.ROOT/'desktop_qt/controller.py', line=42)
        target = store.export(self.root/'report.json')
        content = target.read_text()
        for token in (secret, '/home/private', 'private.example', str(self.root)):
            self.assertNotIn(token, content)
            self.assertNotIn(token, store.path.read_text())
        report = json.loads(content)
        self.assertFalse(report['privacy']['automatic_upload'])
        self.assertTrue(any(e.get('error_type') == 'PermissionError' and e.get('errno') == 13 for e in report['events']))
        self.assertTrue(any(e.get('frames') == [{'source':'desktop_qt/controller.py','line':42}] for e in report['events']))

    def test_frozen_relative_locations_and_unknown_paths(self):
        self.assertEqual(diag.safe_source('desktop_qt/controller.py'), 'desktop_qt/controller.py')
        self.assertIsNone(diag.safe_source('/home/alice/private.py'))
        self.assertIsNone(diag.safe_source(diag.ROOT/'desktop_qt/user-secret.py'))
        self.assertIsNone(diag.safe_source('../desktop_qt/controller.py'))

    def test_export_requires_no_network(self):
        import socket
        store = diag.DiagnosticStore(self.root)
        with patch.object(socket, 'socket', side_effect=AssertionError('network is forbidden')):
            self.assertTrue(store.export(self.root/'offline.json').exists())

    def test_persistence_across_sessions(self):
        first = diag.DiagnosticStore(self.root)
        first.record('qml-load-failed')
        second = diag.DiagnosticStore(self.root)
        self.assertIn('qml-load-failed', [e['event'] for e in second.snapshot()])

    def test_rotation_is_bounded_and_preserves_unrelated_files(self):
        unrelated = self.root/'diagnostics'/'keep.txt'
        with patch.object(diag, 'MAX_LOG_BYTES', 256):
            store = diag.DiagnosticStore(self.root)
            unrelated.write_text('keep')
            for _ in range(100):
                store.record('ui-error')
            files = list(store.directory.glob('diagnostics-*.jsonl'))
            self.assertLessEqual(len(files), diag.MAX_LOG_FILES)
            self.assertTrue(all(p.stat().st_size <= 256 for p in files))
            self.assertEqual(unrelated.read_text(), 'keep')

    def test_corrupt_and_injected_log_fields_are_not_exported(self):
        store = diag.DiagnosticStore(self.root)
        with store.path.open('a') as stream:
            stream.write('not json\n')
            stream.write(json.dumps({'time':diag.now(),'event':'ui-error','message':'SECRET',
                'frames':[{'source':'/home/alice/secret.py','line':3}], 'token':'SECRET'})+'\n')
            stream.write(json.dumps({'time':'SECRET','event':'ui-error'})+'\n')
            stream.write(json.dumps({'time':diag.now(),'event':['SECRET']})+'\n')
        self.assertNotIn('SECRET', store.export(self.root/'result.json').read_text())
        self.assertNotIn('/home/alice', (self.root/'result.json').read_text())

    def test_unwritable_directory_falls_back_without_breaking_app(self):
        blocked = self.root/'blocked'
        blocked.write_text('file not directory')
        with patch.object(diag.tempfile, 'gettempdir', return_value=str(self.root/'temp')):
            store = diag.DiagnosticStore(blocked)
        self.assertTrue(store.persistent)
        self.assertTrue(store.fallback)
        self.assertTrue(store.path.exists())

    def test_unavailable_temp_does_not_block_primary_or_memory_fallback(self):
        with patch.object(diag.tempfile, 'gettempdir', side_effect=FileNotFoundError('no temp')):
            store = diag.DiagnosticStore(self.root)
            self.assertTrue(store.persistent)
            with patch.object(Path, 'mkdir', side_effect=PermissionError('blocked')):
                memory = diag.DiagnosticStore(self.root)
                self.assertFalse(memory.persistent)
                memory.record('ui-error')

    def test_missing_home_and_temp_still_allow_memory_export(self):
        with patch.object(diag, 'default_storage_dir', side_effect=RuntimeError('no home')), patch.object(diag.tempfile, 'gettempdir', side_effect=FileNotFoundError('no temp')):
            store = diag.DiagnosticStore()
        self.assertFalse(store.persistent)
        self.assertTrue(store.export(self.root/'memory.json').is_file())

    def test_all_disk_failures_still_allow_memory_export(self):
        with patch.object(Path, 'mkdir', side_effect=PermissionError('SECRET')):
            store = diag.DiagnosticStore(self.root)
        self.assertFalse(store.persistent)
        store.record('ui-error')
        report = json.loads(store.export(self.root/'memory.json').read_text())
        self.assertFalse(report['persistent_log_available'])
        self.assertIn('ui-error', [e['event'] for e in report['events']])

    def test_log_write_failure_still_keeps_event(self):
        store = diag.DiagnosticStore(self.root)
        with patch.object(Path, 'open', side_effect=OSError('disk full')):
            store.record('qml-load-failed')
        self.assertFalse(store.persistent)
        self.assertIn('qml-load-failed',[e['event'] for e in store.snapshot()])

    def test_export_failure_does_not_clobber_existing_file(self):
        store = diag.DiagnosticStore(self.root)
        target = self.root/'report.json'
        target.write_text('old report')
        with patch.object(diag.os, 'replace', side_effect=PermissionError('blocked')):
            with self.assertRaises(PermissionError):
                store.export(target)
        self.assertEqual(target.read_text(), 'old report')
        self.assertFalse(list(self.root.glob('.lyria-diagnostics-*.tmp')))

    def test_export_suffix_and_log_directory_guard(self):
        store = diag.DiagnosticStore(self.root)
        self.assertEqual(store.export(self.root/'report.txt').name, 'report.txt')
        with self.assertRaises(ValueError):
            store.export(store.directory/'export.json')

    def test_hooks_restore_and_never_forward_raw_error(self):
        store = diag.DiagnosticStore(self.root)
        hooks = diag.RuntimeHooks(store)
        old_sys, old_thread = sys.excepthook, threading.excepthook
        hooks.install()
        try:
            with patch.object(diag, 'safe_stderr') as stderr:
                hooks.python_error(ValueError, ValueError('SECRET'), None)
                hooks.thread_error(SimpleNamespace(exc_value=RuntimeError('SECRET')))
                self.assertNotIn('SECRET', str(stderr.call_args_list))
        finally:
            hooks.restore()
        self.assertIs(sys.excepthook, old_sys)
        self.assertIs(threading.excepthook, old_thread)
        self.assertNotIn('SECRET', store.path.read_text())

    def test_startup_boundary_reports_import_and_qml_failure(self):
        import app_qt
        for exc in (ModuleNotFoundError('SECRET missing library'), diag.StartupFailure()):
            with patch.object(app_qt, '_run_application', side_effect=exc), patch.object(diag, 'show_startup_failure') as prompt:
                result = app_qt.main(['--storage-dir', str(self.root)])
                self.assertEqual(result, 2)
                prompt.assert_called_once()
                self.assertNotIn('SECRET', diag.startup_summary(exc))
        self.assertTrue(any('startup-failed' in p.read_text() for p in (self.root/'diagnostics').glob('*.jsonl')))

    def test_startup_fallback_survives_no_stderr(self):
        store = diag.DiagnosticStore(self.root)
        with patch.dict(sys.modules, {'PySide6.QtWidgets':None}), patch.object(sys, 'stderr', None):
            if sys.platform == 'win32':
                import ctypes
                with patch.object(ctypes.windll.user32, 'MessageBoxW', return_value=1) as prompt:
                    diag.show_startup_failure(ValueError('SECRET'), store)
                prompt.assert_called_once()
                self.assertNotIn('SECRET', prompt.call_args.args[1])
            else:
                diag.show_startup_failure(ValueError('SECRET'), store)


if __name__ == '__main__':
    unittest.main()
