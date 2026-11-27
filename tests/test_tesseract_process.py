import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lyricsync.tesseract_process import _TesseractProcesses, prepare_tesseract_processes


class TesseractProcessTests(unittest.TestCase):
    def test_probe_is_windowless_bounded_and_preserves_callers_options(self):
        wrapped = Mock()
        wrapped.check_output.return_value = b'version'
        proxy = _TesseractProcesses(wrapped)
        env = {'fixture': 'only'}
        self.assertEqual(proxy.check_output(['tesseract', '--version'], env=env, creationflags=4), b'version')
        options = wrapped.check_output.call_args.kwargs
        self.assertIs(options['env'], env)
        self.assertEqual(options['creationflags'], 4 | getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(options['timeout'], 10)
        proxy.Popen(['tesseract'], creationflags=4)
        self.assertEqual(wrapped.Popen.call_args.kwargs['creationflags'], options['creationflags'])

    def test_native_initialization_failure_retries_once(self):
        wrapped = Mock()
        error = subprocess.CalledProcessError(0xc0000142, ['tesseract', '--version'])
        wrapped.check_output.side_effect = [error, b'version']
        with patch('lyricsync.tesseract_process.time.sleep') as sleep:
            self.assertEqual(_TesseractProcesses(wrapped).check_output(['tesseract']), b'version')
        self.assertEqual(wrapped.check_output.call_count, 2)
        sleep.assert_called_once_with(.05)

    def test_persistent_native_failure_is_not_hidden(self):
        wrapped = Mock()
        error = subprocess.CalledProcessError(0xc0000142, ['tesseract'])
        wrapped.check_output.side_effect = error
        with patch('lyricsync.tesseract_process.time.sleep'), self.assertRaises(subprocess.CalledProcessError) as caught:
            _TesseractProcesses(wrapped).check_output(['tesseract'])
        self.assertIs(caught.exception, error)
        self.assertEqual(wrapped.check_output.call_count, 2)

    def test_other_errors_and_timeouts_are_not_retried(self):
        for error in [subprocess.CalledProcessError(1, ['tesseract']), subprocess.TimeoutExpired(['tesseract'], 3)]:
            with self.subTest(error=type(error).__name__):
                wrapped = Mock()
                wrapped.check_output.side_effect = error
                with self.assertRaises(type(error)):
                    _TesseractProcesses(wrapped).check_output(['tesseract'], timeout=3)
                self.assertEqual(wrapped.check_output.call_count, 1)
                self.assertEqual(wrapped.check_output.call_args.kwargs['timeout'], 3)

    @unittest.skipUnless(__import__('sys').platform == 'win32', 'Windows-specific adapter')
    def test_adapter_changes_only_the_pytesseract_reference_and_is_idempotent(self):
        original = subprocess
        internal = SimpleNamespace(subprocess=original)
        module = SimpleNamespace(pytesseract=internal)
        prepare_tesseract_processes(module)
        proxy = internal.subprocess
        prepare_tesseract_processes(module)
        self.assertIs(internal.subprocess, proxy)
        self.assertIs(proxy.wrapped, original)
        self.assertIs(subprocess, original)
        self.assertEqual(proxy.PIPE, subprocess.PIPE)
