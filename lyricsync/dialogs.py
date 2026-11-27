"""Reentrant native dialogs are suppressed per type/title; no duplicate prompts."""
from tkinter import messagebox as _messages, filedialog as _files, colorchooser as _colors
import tkinter as tk
from contextlib import contextmanager
import sys
import time


@contextmanager
def center_native_dialog(parent):
    """Position this UI thread's common dialog on its owner at activation.

    Tk's parent option sets ownership but Windows may restore a file dialog's
    old screen coordinates. A short-lived CBT hook handles that native window;
    it is removed when the synchronous dialog call returns.
    """
    if sys.platform!='win32' or parent is None:
        yield;return
    import ctypes
    from ctypes import wintypes as w
    user=ctypes.WinDLL('user32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    hook_type=ctypes.WINFUNCTYPE(ctypes.c_ssize_t,ctypes.c_int,w.WPARAM,w.LPARAM)
    user.SetWindowsHookExW.argtypes=[ctypes.c_int,hook_type,w.HINSTANCE,w.DWORD]
    user.SetWindowsHookExW.restype=w.HANDLE
    user.CallNextHookEx.argtypes=[w.HANDLE,ctypes.c_int,w.WPARAM,w.LPARAM]
    user.CallNextHookEx.restype=ctypes.c_ssize_t
    user.UnhookWindowsHookEx.argtypes=[w.HANDLE]
    user.GetWindowRect.argtypes=[w.HWND,ctypes.POINTER(w.RECT)]
    user.GetClassNameW.argtypes=[w.HWND,w.LPWSTR,ctypes.c_int]
    user.GetAncestor.argtypes=[w.HWND,w.UINT];user.GetAncestor.restype=w.HWND
    user.SetWindowPos.argtypes=[w.HWND,w.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.UINT]
    user.MonitorFromWindow.argtypes=[w.HWND,w.DWORD];user.MonitorFromWindow.restype=w.HANDLE
    class MonitorInfo(ctypes.Structure):
        _fields_=[('cbSize',w.DWORD),('monitor',w.RECT),('work',w.RECT),('flags',w.DWORD)]
    user.GetMonitorInfoW.argtypes=[w.HANDLE,ctypes.POINTER(MonitorInfo)]
    owner=user.GetAncestor(parent.winfo_id(),2)
    positioned={};hook=None;timers={}
    timer_type=ctypes.WINFUNCTYPE(None,w.HWND,w.UINT,ctypes.c_size_t,w.DWORD)
    user.SetTimer.argtypes=[w.HWND,ctypes.c_size_t,w.UINT,timer_type];user.SetTimer.restype=ctypes.c_size_t
    user.KillTimer.argtypes=[w.HWND,ctypes.c_size_t]
    def position(width,height):
        bounds=w.RECT()
        if not user.GetWindowRect(owner,ctypes.byref(bounds)):return None
        x=(bounds.left+bounds.right-width)//2;y=(bounds.top+bounds.bottom-height)//2
        info=MonitorInfo();info.cbSize=ctypes.sizeof(info)
        if user.GetMonitorInfoW(user.MonitorFromWindow(owner,2),ctypes.byref(info)):
            x=max(info.work.left,min(info.work.right-width,x))
            y=max(info.work.top,min(info.work.bottom-height,y))
        return x,y
    def place(hwnd):
        dialog=w.RECT()
        if not user.GetWindowRect(hwnd,ctypes.byref(dialog)):return
        target=position(dialog.right-dialog.left,dialog.bottom-dialog.top)
        if target:user.SetWindowPos(hwnd,None,*target,0,0,0x0015)
    @timer_type
    def after_initial_resize(hwnd,message,timer_id,tick):
        user.KillTimer(None,timer_id)
        dialog=timers.pop(timer_id,None)
        if dialog:
            try:place(dialog)
            except Exception:pass
    @hook_type
    def on_activate(code,hwnd,param):
        if code==0 and hwnd in positioned and time.monotonic()-positioned[hwnd]<2:
            try:
                proposed=ctypes.cast(param,ctypes.POINTER(w.RECT)).contents
                current=w.RECT();user.GetWindowRect(hwnd,ctypes.byref(current))
                width=proposed.right-proposed.left;height=proposed.bottom-proposed.top
                if (width,height)!=(current.right-current.left,current.bottom-current.top):
                    target=position(width,height)
                    if target:
                        proposed.left,proposed.top=target
                        proposed.right=proposed.left+width;proposed.bottom=proposed.top+height
            except Exception:pass
        if code==5 and hwnd not in positioned:  # HCBT_ACTIVATE
            try:
                name=ctypes.create_unicode_buffer(64)
                user.GetClassNameW(hwnd,name,64)
                if name.value=='#32770':
                    place(hwnd);positioned[hwnd]=time.monotonic()
                    # Explorer restores its saved size just after activation.
                    timer=user.SetTimer(None,0,1000,after_initial_resize)
                    if timer:timers[timer]=hwnd
            except Exception:
                pass  # Positioning must never prevent opening a file or saving work.
        return user.CallNextHookEx(hook,code,hwnd,param)
    hook=user.SetWindowsHookExW(5,on_activate,None,kernel.GetCurrentThreadId())
    try:yield
    finally:
        for timer in timers:user.KillTimer(None,timer)
        if hook:user.UnhookWindowsHookEx(hook)


def center_window(window,parent):
    if not window.winfo_exists():return
    window.update_idletasks()
    width=max(window.winfo_reqwidth(),window.winfo_width())
    height=max(window.winfo_reqheight(),window.winfo_height())
    left,top,right,bottom=0,0,parent.winfo_screenwidth(),parent.winfo_screenheight()
    try:
        import ctypes
        from ctypes import wintypes
        class MonitorInfo(ctypes.Structure):
            _fields_=[('cbSize',wintypes.DWORD),('rcMonitor',wintypes.RECT),('rcWork',wintypes.RECT),('dwFlags',wintypes.DWORD)]
        user=ctypes.windll.user32
        user.MonitorFromWindow.argtypes=[wintypes.HWND,wintypes.DWORD];user.MonitorFromWindow.restype=wintypes.HANDLE
        monitor=user.MonitorFromWindow(parent.winfo_id(),2)
        user.GetMonitorInfoW.argtypes=[wintypes.HANDLE,ctypes.POINTER(MonitorInfo)]
        info=MonitorInfo();info.cbSize=ctypes.sizeof(info)
        if user.GetMonitorInfoW(monitor,ctypes.byref(info)):
            left,top,right,bottom=info.rcWork.left,info.rcWork.top,info.rcWork.right,info.rcWork.bottom
    except (AttributeError,OSError):pass
    width=min(width,right-left-24);height=min(height,bottom-top-48)
    x=max(left,min(right-width,parent.winfo_rootx()+(parent.winfo_width()-width)//2))
    y=max(top,min(bottom-height-32,parent.winfo_rooty()+(parent.winfo_height()-height)//2))
    window.geometry(f'{width}x{height}+{x}+{y}');window.deiconify();window.lift()


class SingleDialogs:
    def __init__(self,module):self.module=module;self.active=set()
    def __getattr__(self,name):
        call=getattr(self.module,name)
        def show(*args,**kwargs):
            if 'parent' not in kwargs and tk._default_root is not None:kwargs['parent']=tk._default_root
            key=(name,kwargs.get('title',args[0] if args else ''))
            if key in self.active:
                parent=kwargs.get('parent')
                if parent:parent.lift()
                return False if name.startswith('ask') and self.module is _messages else ''
            self.active.add(key)
            try:
                with center_native_dialog(kwargs.get('parent')):
                    return call(*args,**kwargs)
            finally:self.active.discard(key)
        return show


messagebox=SingleDialogs(_messages)
filedialog=SingleDialogs(_files)
colorchooser=SingleDialogs(_colors)
