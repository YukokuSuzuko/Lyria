"""Optional Windows file drops and non-mutating import-role selection.

NativeFileDrop must be created/closed on Tk's UI thread. It does not replace Tk's
message handling: only WM_DROPFILES is consumed, and every other message (IME
included) is forwarded to the original procedure. Keep file pickers available;
this adapter is deliberately unavailable on other platforms or setup failure.

resolve_dropped_files only returns paths. The caller must separately confirm
replacement of unsaved work and commit the import through the existing backend.
Actual Explorer delivery and Windows IME require Windows desktop acceptance.
"""
from __future__ import annotations

import ctypes
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
import weakref

from .dialogs import center_window, messagebox
from .importing import plan_files, resolve_plan


WM_DROPFILES = 0x0233
WM_NCDESTROY = 0x0082
GWLP_WNDPROC = -4
GA_ROOT = 2

# A native callback must outlive every native reference, including a later
# subclass that chains to ours. Never free one merely because close was called.
_LIVE_ADAPTERS = {}
# At native destruction a thunk may still be returning into Windows. Retain the
# thunk (not the app/root) until process exit rather than free executable memory
# beneath that return. Normally there is just one main-window thunk per app.
_RETIRED_CALLBACKS = []
_ACTIVE_RESOLUTIONS = set()


def _pointer(value):
    """Normalize signed LONG_PTR results without truncating a 64-bit address."""
    return ctypes.c_void_p(value).value or 0


class _Win32API:
    """Typed Win32 boundary, isolated so portable tests need no native DLLs."""

    def __init__(self):
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.shell = ctypes.WinDLL('shell32', use_last_error=True)
        hwnd = ctypes.c_void_p
        uint = ctypes.c_uint
        pointer_int = ctypes.c_ssize_t
        self.callback_type = ctypes.WINFUNCTYPE(
            pointer_int, hwnd, uint, ctypes.c_size_t, pointer_int)
        suffix = 'PtrW' if ctypes.sizeof(hwnd) == 8 else 'W'
        self._get = getattr(self.user, 'GetWindowLong' + suffix)
        self._set = getattr(self.user, 'SetWindowLong' + suffix)
        self._get.argtypes = [hwnd, ctypes.c_int]
        self._get.restype = pointer_int
        self._set.argtypes = [hwnd, ctypes.c_int, pointer_int]
        self._set.restype = pointer_int
        self.user.GetAncestor.argtypes = [hwnd, uint]
        self.user.GetAncestor.restype = hwnd
        self.user.IsWindow.argtypes = [hwnd]
        self.user.IsWindow.restype = ctypes.c_int
        self.user.CallWindowProcW.argtypes = [hwnd, hwnd, uint, ctypes.c_size_t, pointer_int]
        self.user.CallWindowProcW.restype = pointer_int
        self.shell.DragAcceptFiles.argtypes = [hwnd, ctypes.c_int]
        self.shell.DragAcceptFiles.restype = None
        self.shell.DragQueryFileW.argtypes = [hwnd, uint, ctypes.c_wchar_p, uint]
        self.shell.DragQueryFileW.restype = uint
        self.shell.DragFinish.argtypes = [hwnd]
        self.shell.DragFinish.restype = None

    def window(self, widget_id):
        handle = self.user.GetAncestor(widget_id, GA_ROOT)
        if not handle or not self.user.IsWindow(handle):
            raise OSError('无法初始化文件拖放窗口')
        return handle

    def procedure(self, handle):
        ctypes.set_last_error(0)
        previous = self._get(handle, GWLP_WNDPROC)
        if not previous:
            raise OSError(ctypes.get_last_error(), '无法绑定窗口文件拖放功能')
        return _pointer(previous)

    def replace(self, handle, address):
        ctypes.set_last_error(0)
        previous = self._set(handle, GWLP_WNDPROC, ctypes.c_ssize_t(address).value)
        if not previous:
            raise OSError(ctypes.get_last_error(), '窗口文件拖放功能初始化失败')
        return _pointer(previous)

    def accept(self, handle, enabled):
        self.shell.DragAcceptFiles(handle, bool(enabled))

    def forward(self, previous, handle, message, wparam, lparam):
        return self.user.CallWindowProcW(previous, handle, message, wparam, lparam)

    def paths(self, drop):
        count = self.shell.DragQueryFileW(drop, 0xFFFFFFFF, None, 0)
        if not count:
            raise ValueError('未检测到要导入的文件')
        if count > 256:
            raise ValueError('一次拖入的文件过多，请只选择视频、原文和中文译文')
        paths = []
        for index in range(count):
            length = self.shell.DragQueryFileW(drop, index, None, 0)
            if not 0 < length <= 32767:
                raise ValueError('拖入文件的路径无效或过长')
            buffer = ctypes.create_unicode_buffer(length + 1)
            written = self.shell.DragQueryFileW(drop, index, buffer, length + 1)
            if written != length:
                raise OSError('无法完整读取拖入文件的路径')
            paths.append(buffer.value)
        return paths

    def finish(self, drop):
        self.shell.DragFinish(drop)


class NativeFileDrop:
    """Receive native Unicode path lists through ``root.after(0, callback)``.

    ``on_error`` receives a readable string, also deferred to Tk. ``available``
    is false after close or when unsupported. No project/input state is touched.
    Closing is idempotent and does not remove any other Tk Destroy bindings.
    """

    def __init__(self, root, on_paths, on_error=None):
        self.root = root
        self.on_paths = on_paths
        self.on_error = on_error
        self.available = False
        self.unavailable_reason = ''
        self._closed = False
        self._api = None
        self._handle = None
        self._previous = None
        self._callback = None
        self._address = None
        self._destroy_binding = None
        self._retired = False
        if sys.platform != 'win32':
            self.unavailable_reason = '此平台不支持系统文件拖入，请使用选择文件按钮。'
            return
        try:
            self._api = _Win32API()
            root.update_idletasks()
            self._handle = self._api.window(root.winfo_id())
            # Assign the previous proc before installation: even if a message
            # arrives during SetWindowLongPtr, forwarding is already possible.
            self._previous = self._api.procedure(self._handle)
            reference = weakref.ref(self)

            def dispatch(*args):
                adapter = reference()
                # While any live HWND can call this, _LIVE_ADAPTERS pins it.
                return adapter._window_proc(*args) if adapter is not None else 0

            self._callback = self._api.callback_type(dispatch)
            self._address = ctypes.cast(self._callback, ctypes.c_void_p).value
            self._destroy_binding = root.bind('<Destroy>', self._on_destroy, add='+')
            self._previous = self._api.replace(self._handle, self._address)
            _LIVE_ADAPTERS[id(self)] = self
            self._api.accept(self._handle, True)
            self.available = True
        except Exception as exc:
            self.unavailable_reason = f'系统文件拖入不可用，请使用选择文件按钮。\n{exc}'
            self.close()
            self._report(self.unavailable_reason)

    def _defer(self, callback, *args):
        try:
            self.root.after(0, callback, *args)
        except Exception:
            pass  # A destroyed interpreter must not escape a native callback.

    def _report(self, text):
        if self.on_error is not None:
            self._defer(self.on_error, text)

    def _deliver(self, paths):
        if self._closed or not self.available:
            return
        try:
            self.on_paths(paths)
        except Exception as exc:
            self._report(f'无法导入拖入的文件：{exc}')

    def _window_proc(self, handle, message, wparam, lparam):
        # No Python exception may unwind through a ctypes callback into Tk.
        if message == WM_DROPFILES:
            try:
                if not self._closed:
                    paths = self._api.paths(wparam)
                    self._defer(self._deliver, paths)
            except Exception as exc:
                self._report(f'无法读取拖入的文件：{exc}')
            finally:
                try:
                    self._api.finish(wparam)
                except Exception as exc:
                    self._report(f'无法释放拖入文件的数据：{exc}')
            return 0
        if message == WM_NCDESTROY:
            self.close()  # Also cover native teardown without a Tk Destroy event.
        try:
            # Never swallow IME, focus, keyboard, paint, or lifecycle messages.
            result = self._api.forward(self._previous, handle, message, wparam, lparam)
        except Exception as exc:
            self._report(f'窗口消息转发失败：{exc}')
            result = 0
        finally:
            if message == WM_NCDESTROY:
                self.available = False
                self._closed = True
                self._handle = None
                _LIVE_ADAPTERS.pop(id(self), None)
                self._retire()
        return result

    def _retire(self):
        if not self._retired and self._callback is not None:
            # The thunk closes over only a weakref, not the app/root. Retaining
            # it is safe even when destruction happens inside the thunk itself.
            _RETIRED_CALLBACKS.append(self._callback)
            self._retired = True

    def _on_destroy(self, event):
        if event.widget is self.root:
            self.close()

    def close(self):
        """Disable drops and restore our subclass only if still chain owner."""
        self.available = False
        self._closed = True
        if self._destroy_binding:
            try:
                self.root.unbind('<Destroy>', self._destroy_binding)
            except (tk.TclError, RuntimeError):
                pass
            self._destroy_binding = None
        if self._api is None or self._handle is None or id(self) not in _LIVE_ADAPTERS:
            return
        try:
            self._api.accept(self._handle, False)
            if self._api.procedure(self._handle) == self._address:
                self._api.replace(self._handle, self._previous)
                _LIVE_ADAPTERS.pop(id(self), None)
                self._handle = None
                self._retire()
            # Another component subclassed after us. Do not cut it out of the
            # chain: remain pinned and forward until its WM_NCDESTROY arrives.
        except Exception as exc:
            # If restoration failed, freeing this callback would crash Windows.
            # Keep it pinned and forwarding; file selection still works.
            self._report(f'文件拖入功能已停用，请改用选择文件按钮。\n{exc}')


class _RoleDialog:
    """Two explicit, initially unselected roles; cancel returns no assignments."""

    def __init__(self, root, plan):
        self.root = root
        self.plan = plan
        self.result = None
        self.variables = {}
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.title('确认拖入文件用途')
        self.window.transient(root)
        self.window.resizable(False, False)
        self.window.protocol('WM_DELETE_WINDOW', self.cancel)
        self.window.bind('<Escape>', lambda _: self.cancel())
        body = ttk.Frame(self.window, padding=20)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='这些文本文件分别是什么？', style='Heading.TLabel').pack(anchor='w')
        ttk.Label(body, text='请为每个文件指定类型（演唱原文或中文译文）。',
                  wraplength=500).pack(anchor='w', pady=(5, 10))
        for item in plan['ambiguous']:
            path = item['path']
            card = ttk.LabelFrame(body, text=Path(path).name, padding=10)
            card.pack(fill='x', pady=5)
            ttk.Label(card, text=path, wraplength=480).pack(anchor='w', pady=(0, 6))
            variable = tk.StringVar(master=self.window, value='')
            self.variables[path] = variable
            row = ttk.Frame(card)
            row.pack(fill='x')
            for text, value in (('原文歌词', 'ktv'), ('中文译文', 'translation')):
                ttk.Radiobutton(row, text=text, value=value, variable=variable,
                                command=self._selection_changed).pack(side='left', padx=(0, 22))
        self.hint = tk.StringVar(master=self.window, value='请为每个文件选择对应类型。')
        ttk.Label(body, textvariable=self.hint, wraplength=500).pack(anchor='w', pady=(10, 0))
        buttons = ttk.Frame(body)
        buttons.pack(fill='x', pady=(16, 0))
        self.confirm = ttk.Button(buttons, text='使用这些文件', command=self.accept, state='disabled')
        self.confirm.pack(side='right')
        ttk.Button(buttons, text='取消', command=self.cancel).pack(side='right', padx=8)

    def _selection_changed(self):
        choices = [variable.get() for variable in self.variables.values()]
        complete = all(choices)
        distinct = len(choices) == len(set(choices))
        self.confirm.configure(state='normal' if complete and distinct else 'disabled')
        self.hint.set('原文和中文译文各只能选择一个文件。' if complete and not distinct else
                      '点击确认后导入所选文件。' if complete else '请为每个文件选择对应类型。')

    def accept(self):
        choices = {path: variable.get() for path, variable in self.variables.items()}
        try:
            resolve_plan(self.plan, choices)
        except (ValueError, OSError) as exc:
            messagebox.showerror('无法导入文件', str(exc), parent=self.window)
            return
        self.result = choices
        self.window.destroy()

    def cancel(self):
        self.result = None
        self.window.destroy()

    def show(self):
        previous_grab = self.root.grab_current()
        previous_focus = self.root.focus_get()
        try:
            center_window(self.window, self.root)
            self.window.wait_visibility()
            self.window.grab_set()
            self.window.focus_set()
            self.window.wait_window()
        finally:
            try:
                if self.window.winfo_exists():
                    self.window.destroy()
            except tk.TclError:
                pass
            for widget, method in ((previous_grab, 'grab_set'), (previous_focus, 'focus_set')):
                try:
                    if widget is not None and widget.winfo_exists():
                        getattr(widget, method)()
                except tk.TclError:
                    pass
        return self.result


def resolve_dropped_files(root, paths):
    """Return role→path assignments, or None on cancellation/validation error.

    No inputs, projects, history, or dirty flags are changed. Identical repeated
    paths are deduplicated by the backend; conflicting roles/types are rejected.
    A second drop during the role modal is ignored, never committed behind it.
    """
    key = id(root)
    if key in _ACTIVE_RESOLUTIONS:
        return None
    _ACTIVE_RESOLUTIONS.add(key)
    try:
        plan = plan_files(paths)
        if len(plan['ambiguous']) > 2:
            raise ValueError('原文和中文译文各只能导入一个文件，请减少拖入的文本文件。')
        choices = _RoleDialog(root, plan).show() if plan['ambiguous'] else {}
        if choices is None:
            return None
        return resolve_plan(plan, choices)
    except (ValueError, OSError) as exc:
        messagebox.showerror('无法导入文件', str(exc), parent=root)
        return None
    except tk.TclError:
        return None  # Parent/dialog was closed while waiting; no import commits.
    finally:
        _ACTIVE_RESOLUTIONS.discard(key)
