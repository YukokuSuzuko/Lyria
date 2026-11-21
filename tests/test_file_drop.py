"""Portable mocked drop tests; not evidence of Windows delivery/IME acceptance."""
import ctypes
import gc
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
import weakref

from lyricsync import file_drop


class FakeRoot:
    def __init__(self):
        self.pending = []
        self.bindings = []
        self.unbound = []

    def update_idletasks(self):
        pass

    def winfo_id(self):
        return 0x100000023

    def bind(self, sequence, callback, add=None):
        self.bindings.append((sequence, callback, add))
        return 'drop-destroy-binding'

    def unbind(self, sequence, identifier):
        self.unbound.append((sequence, identifier))
        self.bindings = []

    def after(self, delay, callback, *args):
        assert delay == 0
        self.pending.append((callback, args))

    def drain(self):
        while self.pending:
            callback, args = self.pending.pop(0)
            callback(*args)


class FakeAPI:
    callback_type = ctypes.CFUNCTYPE(
        ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)

    def __init__(self):
        self.original = 0x100000987
        self.current = self.original
        self.replacements = []
        self.accepted = []
        self.forwarded = []
        self.finished = []
        self.dropped = ['C:\\歌词\\夜の歌 🎵.mp4', 'C:\\歌词\\中文.txt']

    def window(self, widget_id):
        return widget_id + 2

    def procedure(self, handle):
        return self.current

    def replace(self, handle, address):
        previous, self.current = self.current, address
        self.replacements.append((handle, address))
        return previous

    def accept(self, handle, enabled):
        self.accepted.append((handle, enabled))

    def paths(self, drop):
        return list(self.dropped)

    def finish(self, drop):
        self.finished.append(drop)

    def forward(self, *args):
        self.forwarded.append(args)
        return 0x123456789


class NativeFileDropTests(unittest.TestCase):
    def setUp(self):
        self.root = FakeRoot()
        self.api = FakeAPI()
        self.received = []
        self.errors = []
        self.instances = []

    def create(self):
        with patch.object(file_drop.sys, 'platform', 'win32'), \
                patch.object(file_drop, '_Win32API', return_value=self.api):
            adapter = file_drop.NativeFileDrop(self.root, self.received.append, self.errors.append)
        self.instances.append(adapter)
        return adapter

    def tearDown(self):
        for adapter in self.instances:
            adapter.close()
            file_drop._LIVE_ADAPTERS.pop(id(adapter), None)

    def test_non_windows_never_loads_dll_or_changes_tk(self):
        with patch.object(file_drop.sys, 'platform', 'linux'), \
                patch.object(file_drop, '_Win32API') as factory:
            adapter = file_drop.NativeFileDrop(self.root, self.received.append)
        self.assertFalse(adapter.available)
        self.assertIn('选择文件', adapter.unavailable_reason)
        self.assertEqual(self.root.bindings, [])
        factory.assert_not_called()
        adapter.close()

    def test_initialization_failure_is_optional_and_reports_asynchronously(self):
        with patch.object(file_drop.sys, 'platform', 'win32'), \
                patch.object(file_drop, '_Win32API', side_effect=OSError('setup unavailable')):
            adapter = file_drop.NativeFileDrop(self.root, self.received.append, self.errors.append)
        self.assertFalse(adapter.available)
        self.assertEqual(self.errors, [])
        self.root.drain()
        self.assertIn('选择文件', self.errors[0])
        self.assertIn('setup unavailable', self.errors[0])

    def test_unicode_delivery_is_deferred_and_hdrop_finished_once(self):
        adapter = self.create()
        drop = 0x100000456
        result = adapter._callback(adapter._handle, file_drop.WM_DROPFILES, drop, 0)
        self.assertEqual(result, 0)
        self.assertEqual(self.received, [])
        self.assertEqual(self.api.finished, [drop])
        self.root.drain()
        self.assertEqual(self.received, [self.api.dropped])
        self.assertEqual(self.api.forwarded, [])

    def test_all_non_drop_messages_including_ime_forward_unchanged(self):
        adapter = self.create()
        handle = adapter._handle
        for message in (0x000F, 0x0100, 0x010D, 0x010F, 0x0281, 0x0282):
            self.assertEqual(adapter._callback(handle, message, 0x123456789, -123), 0x123456789)
            self.assertEqual(self.api.forwarded[-1],
                             (self.api.original, handle, message, 0x123456789, -123))
        self.assertEqual(self.api.finished, [])

    def test_read_failure_frees_drop_and_reports_without_native_exception(self):
        adapter = self.create()
        self.api.paths = Mock(side_effect=ValueError('bad native paths'))
        self.assertEqual(adapter._callback(adapter._handle, file_drop.WM_DROPFILES, 99, 0), 0)
        self.assertEqual(self.api.finished, [99])
        self.root.drain()
        self.assertEqual(self.received, [])
        self.assertIn('bad native paths', self.errors[0])

    def test_close_restores_original_pointer_and_only_our_destroy_binding(self):
        adapter = self.create()
        handle = adapter._handle
        self.assertEqual(self.root.bindings[0][2], '+')
        adapter.close()
        adapter.close()
        self.assertFalse(adapter.available)
        self.assertEqual(self.api.current, self.api.original)
        self.assertEqual(self.api.accepted, [(handle, True), (handle, False)])
        self.assertEqual(self.root.unbound, [('<Destroy>', 'drop-destroy-binding')])
        self.assertNotIn(id(adapter), file_drop._LIVE_ADAPTERS)

    def test_child_destroy_is_ignored_but_root_destroy_closes(self):
        adapter = self.create()
        adapter._on_destroy(SimpleNamespace(widget=object()))
        self.assertTrue(adapter.available)
        adapter._on_destroy(SimpleNamespace(widget=self.root))
        self.assertFalse(adapter.available)
        self.assertEqual(self.api.current, self.api.original)

    def test_queued_drop_is_not_delivered_after_close(self):
        adapter = self.create()
        adapter._callback(adapter._handle, file_drop.WM_DROPFILES, 99, 0)
        adapter.close()
        self.root.drain()
        self.assertEqual(self.received, [])

    def test_native_destroy_also_restores_without_a_tk_destroy_event(self):
        adapter = self.create()
        handle = adapter._handle
        self.assertEqual(adapter._callback(handle, file_drop.WM_NCDESTROY, 0, 0), 0x123456789)
        self.assertFalse(adapter.available)
        self.assertEqual(self.api.current, self.api.original)
        self.assertEqual(self.api.forwarded[-1],
                         (self.api.original, handle, file_drop.WM_NCDESTROY, 0, 0))
        self.assertNotIn(id(adapter), file_drop._LIVE_ADAPTERS)

    def test_setup_failure_after_subclass_install_restores_the_original(self):
        self.api.accept = Mock(side_effect=[OSError('cannot enable drops'), None])
        adapter = self.create()
        self.assertFalse(adapter.available)
        self.assertEqual(self.api.current, self.api.original)
        self.assertNotIn(id(adapter), file_drop._LIVE_ADAPTERS)
        self.root.drain()
        self.assertIn('cannot enable drops', self.errors[-1])

    def test_later_subclass_is_not_clobbered_and_callback_stays_pinned(self):
        adapter = self.create()
        handle = adapter._handle
        later_procedure = 0x100000DDD
        self.api.current = later_procedure
        adapter.close()
        self.assertEqual(self.api.current, later_procedure)
        self.assertIn(id(adapter), file_drop._LIVE_ADAPTERS)
        self.assertEqual(adapter._callback(handle, 0x010F, 123, 4), 0x123456789)
        adapter._callback(handle, file_drop.WM_DROPFILES, 98, 0)
        self.assertEqual(self.api.finished, [98])
        self.assertEqual(self.root.pending, [])
        adapter._callback(handle, file_drop.WM_NCDESTROY, 0, 0)
        self.assertNotIn(id(adapter), file_drop._LIVE_ADAPTERS)
        self.assertIn(adapter._callback, file_drop._RETIRED_CALLBACKS)

    def test_restoration_failure_keeps_native_callback_alive(self):
        adapter = self.create()
        self.api.replace = Mock(side_effect=OSError('cannot restore'))
        adapter.close()
        self.assertIn(id(adapter), file_drop._LIVE_ADAPTERS)
        self.assertFalse(adapter.available)
        self.root.drain()
        self.assertIn('cannot restore', self.errors[-1])

    def test_retired_thunk_does_not_retain_app_or_root(self):
        adapter = self.create()
        reference = weakref.ref(adapter)
        adapter.close()
        self.instances.clear()
        del adapter
        gc.collect()
        self.assertIsNone(reference())

    def test_application_callback_failure_reports_asynchronously(self):
        adapter = self.create()
        adapter.on_paths = Mock(side_effect=ValueError('cancelled before commit'))
        adapter._callback(adapter._handle, file_drop.WM_DROPFILES, 99, 0)
        self.root.drain()
        self.assertIn('cancelled before commit', self.errors[0])

    def test_dead_tk_interpreter_does_not_prevent_dragfinish(self):
        adapter = self.create()
        self.root.after = Mock(side_effect=file_drop.tk.TclError('application destroyed'))
        self.assertEqual(adapter._callback(adapter._handle, file_drop.WM_DROPFILES, 55, 0), 0)
        self.assertEqual(self.api.finished, [55])

    def test_native_dll_boundary_uses_pointer_sized_types(self):
        user = SimpleNamespace(**{name: Mock() for name in (
            'GetWindowLongPtrW', 'SetWindowLongPtrW', 'GetWindowLongW', 'SetWindowLongW',
            'GetAncestor', 'IsWindow', 'CallWindowProcW')})
        shell = SimpleNamespace(**{name: Mock() for name in ('DragAcceptFiles', 'DragQueryFileW', 'DragFinish')})
        with patch.object(ctypes, 'WinDLL', side_effect=[user, shell], create=True), \
                patch.object(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE, create=True):
            api = file_drop._Win32API()
        pointer_size = ctypes.sizeof(ctypes.c_void_p)
        self.assertEqual(ctypes.sizeof(api._set.restype), pointer_size)
        self.assertEqual(ctypes.sizeof(api._set.argtypes[2]), pointer_size)
        self.assertEqual(ctypes.sizeof(user.CallWindowProcW.restype), pointer_size)
        self.assertEqual(ctypes.sizeof(user.CallWindowProcW.argtypes[3]), pointer_size)
        self.assertEqual(ctypes.sizeof(user.CallWindowProcW.argtypes[4]), pointer_size)
        self.assertIs(shell.DragQueryFileW.argtypes[2], ctypes.c_wchar_p)
        self.assertIsNone(shell.DragFinish.restype)
        self.assertEqual(file_drop._pointer(-1), (1 << (pointer_size * 8)) - 1)

    def test_drag_query_reads_full_unicode_paths_and_rejects_truncation(self):
        paths = ['C:\\歌曲\\夜の歌 🎵.mp4', 'D:\\中文 原文\\字幕.ass']

        def query(drop, index, buffer, length):
            if index == 0xFFFFFFFF:
                return len(paths)
            if buffer is not None:
                buffer.value = paths[index]
            return len(paths[index])

        api = file_drop._Win32API.__new__(file_drop._Win32API)
        api.shell = SimpleNamespace(DragQueryFileW=Mock(side_effect=query))
        self.assertEqual(api.paths(42), paths)
        api.shell.DragQueryFileW = Mock(side_effect=[1, 10, 3])
        with self.assertRaisesRegex(OSError, '完整'):
            api.paths(42)

    def test_native_empty_or_unreasonable_drop_is_readable_error(self):
        api = file_drop._Win32API.__new__(file_drop._Win32API)
        api.shell = SimpleNamespace(DragQueryFileW=Mock(return_value=0))
        with self.assertRaisesRegex(ValueError, '未检测到要导入的文件'):
            api.paths(42)
        api.shell.DragQueryFileW.return_value = 257
        with self.assertRaisesRegex(ValueError, '过多'):
            api.paths(42)


class RoleResolutionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = object()
        self.error_patch = patch.object(file_drop.messagebox, 'showerror')
        self.error = self.error_patch.start()
        self.addCleanup(self.error_patch.stop)

    def file(self, name):
        path = Path(self.folder.name) / name
        path.touch()
        return str(path.resolve())

    def test_video_and_explicit_text_roles_use_existing_backend(self):
        video, original, chinese = [self.file(name) for name in ('夜.mp4', 'あ.ass', '中文.txt')]
        with patch.object(file_drop, '_RoleDialog') as dialog:
            dialog.return_value.show.return_value = {original: 'ktv', chinese: 'translation'}
            result = file_drop.resolve_dropped_files(self.root, [video, original, chinese, video])
        self.assertEqual(result, {'video': video, 'ktv': original, 'translation': chinese})
        self.assertEqual(len(dialog.call_args.args[1]['ambiguous']), 2)
        self.error.assert_not_called()

    def test_chinese_filename_is_not_silently_assigned(self):
        chinese = self.file('中文译文.txt')
        with patch.object(file_drop, '_RoleDialog') as dialog:
            dialog.return_value.show.return_value = {chinese: 'ktv'}
            result = file_drop.resolve_dropped_files(self.root, [chinese])
        self.assertEqual(result, {'ktv': chinese})
        self.assertEqual(dialog.call_count, 1)

    def test_cancel_returns_nothing_and_does_not_revalidate_or_commit(self):
        text = self.file('原文.txt')
        with patch.object(file_drop, '_RoleDialog') as dialog, \
                patch.object(file_drop, 'resolve_plan') as resolve:
            dialog.return_value.show.return_value = None
            self.assertIsNone(file_drop.resolve_dropped_files(self.root, [text]))
        resolve.assert_not_called()
        self.error.assert_not_called()
        self.assertTrue(Path(text).is_file())

    def test_missing_role_or_conflicting_choices_are_readable_not_guessed(self):
        first, second = self.file('a.txt'), self.file('b.ass')
        with patch.object(file_drop, '_RoleDialog') as dialog:
            dialog.return_value.show.return_value = {}
            self.assertIsNone(file_drop.resolve_dropped_files(self.root, [first]))
            self.assertIn('请分别指定每个文本文件是演唱原文还是中文译文', self.error.call_args.args[1])
            dialog.return_value.show.return_value = {first: 'ktv', second: 'ktv'}
            self.assertIsNone(file_drop.resolve_dropped_files(self.root, [first, second]))
            self.assertIn('重复', self.error.call_args.args[1])

    def test_more_than_two_text_files_rejected_without_impossible_modal(self):
        files = [self.file(name) for name in ('a.txt', 'b.txt', 'c.ass')]
        with patch.object(file_drop, '_RoleDialog') as dialog:
            self.assertIsNone(file_drop.resolve_dropped_files(self.root, files))
        dialog.assert_not_called()
        self.assertIn('各只能', self.error.call_args.args[1])

    def test_project_is_alone_and_needs_no_role_prompt(self):
        project = self.file('旧任务.lyricsync.json')
        with patch.object(file_drop, '_RoleDialog') as dialog:
            self.assertEqual(file_drop.resolve_dropped_files(self.root, [project]), {'project': project})
            self.assertIsNone(file_drop.resolve_dropped_files(self.root, [project, self.file('a.txt')]))
        dialog.assert_not_called()
        self.assertIn('单独', self.error.call_args.args[1])

    def test_multiple_video_and_unsupported_types_are_readable(self):
        first, second = self.file('a.mp4'), self.file('b.mkv')
        self.assertIsNone(file_drop.resolve_dropped_files(self.root, [first, second]))
        self.assertIn('同一类型只能导入一个文件', self.error.call_args.args[1])
        self.assertIsNone(file_drop.resolve_dropped_files(self.root, [self.file('no.exe')]))
        self.assertIn('不支持', self.error.call_args.args[1])

    def test_file_disappearing_during_role_choice_is_not_committed(self):
        text = self.file('中文.txt')

        def select():
            Path(text).unlink()
            return {text: 'translation'}

        with patch.object(file_drop, '_RoleDialog') as dialog:
            dialog.return_value.show.side_effect = select
            self.assertIsNone(file_drop.resolve_dropped_files(self.root, [text]))
        self.assertIn('文件不存在或无法读取', self.error.call_args.args[1])

    def test_second_drop_while_modal_is_open_does_not_commit_behind_it(self):
        text = self.file('中文.txt')
        video = self.file('video.mp4')

        def choose():
            self.assertIsNone(file_drop.resolve_dropped_files(self.root, [video]))
            return None

        with patch.object(file_drop, '_RoleDialog') as dialog:
            dialog.return_value.show.side_effect = choose
            self.assertIsNone(file_drop.resolve_dropped_files(self.root, [text]))
        self.assertEqual(file_drop.resolve_dropped_files(self.root, [video]), {'video': video})

    def test_closed_modal_returns_none_and_clears_reentry_guard(self):
        text = self.file('中文.txt')
        with patch.object(file_drop, '_RoleDialog', side_effect=file_drop.tk.TclError('closed')):
            self.assertIsNone(file_drop.resolve_dropped_files(self.root, [text]))
        self.assertNotIn(id(self.root), file_drop._ACTIVE_RESOLUTIONS)

    def test_dialog_starts_with_no_default_and_can_cancel_without_mutation(self):
        text = self.file('中文.txt')
        plan = file_drop.plan_files([text])

        class Variable:
            def __init__(self, master, value):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        widgets = ('Frame', 'Label', 'LabelFrame', 'Radiobutton', 'Button')
        patches = [patch.object(file_drop.ttk, name) for name in widgets]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        with patch.object(file_drop.tk, 'Toplevel'), patch.object(file_drop.tk, 'StringVar', Variable):
            dialog = file_drop._RoleDialog(self.root, plan)
        self.assertEqual(dialog.variables[text].get(), '')
        dialog._selection_changed()
        dialog.confirm.configure.assert_called_with(state='disabled')
        dialog.accept()
        self.assertIsNone(dialog.result)
        dialog.window.destroy.assert_not_called()
        dialog.variables[text].set('translation')
        dialog._selection_changed()
        dialog.confirm.configure.assert_called_with(state='normal')
        dialog.accept()
        self.assertEqual(dialog.result, {text: 'translation'})
        dialog.cancel()
        self.assertIsNone(dialog.result)


if __name__ == '__main__':
    unittest.main()
