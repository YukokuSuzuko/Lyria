"""Windows-only client frame policy for an opaque Qt Quick window.

Keep the native caption/thick-frame styles DWM uses for transitions, while
WM_NCCALCSIZE makes the opaque QML title strip the entire client area. No
system-wide animation/security settings, transparency or manual DPI math.
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes
from PySide6.QtCore import QAbstractNativeEventFilter

GWL_STYLE=-16
WS_CAPTION=0x00C00000
WS_THICKFRAME=0x00040000
WS_SYSMENU=0x00080000
WS_MINIMIZEBOX=0x00020000
WS_MAXIMIZEBOX=0x00010000
FRAME_STYLES=WS_CAPTION|WS_THICKFRAME|WS_SYSMENU|WS_MINIMIZEBOX|WS_MAXIMIZEBOX

def native_frame_style(style):
    return int(style)|FRAME_STYLES

class RECT(ctypes.Structure):
    _fields_=[('left',wintypes.LONG),('top',wintypes.LONG),('right',wintypes.LONG),('bottom',wintypes.LONG)]
class MONITORINFO(ctypes.Structure):
    _fields_=[('cbSize',wintypes.DWORD),('rcMonitor',RECT),('rcWork',RECT),('dwFlags',wintypes.DWORD)]
class MSG(ctypes.Structure):
    _fields_=[('hwnd',wintypes.HWND),('message',wintypes.UINT),('wParam',wintypes.WPARAM),('lParam',wintypes.LPARAM),('time',wintypes.DWORD),('pt',wintypes.POINT)]

class WindowsClientFrame(QAbstractNativeEventFilter):
    def __init__(self):
        super().__init__();self.hwnd=0
        self.user32=ctypes.WinDLL('user32',use_last_error=True)
        self.get_style=getattr(self.user32,'GetWindowLongPtrW',self.user32.GetWindowLongW)
        self.set_style=getattr(self.user32,'SetWindowLongPtrW',self.user32.SetWindowLongW)
        self.get_style.argtypes=[wintypes.HWND,ctypes.c_int];self.get_style.restype=ctypes.c_ssize_t
        self.set_style.argtypes=[wintypes.HWND,ctypes.c_int,ctypes.c_ssize_t];self.set_style.restype=ctypes.c_ssize_t
        self.user32.SetWindowPos.argtypes=[wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT]
        self.user32.IsZoomed.argtypes=[wintypes.HWND];self.user32.IsZoomed.restype=wintypes.BOOL
        self.user32.MonitorFromWindow.argtypes=[wintypes.HWND,wintypes.DWORD];self.user32.MonitorFromWindow.restype=wintypes.HMONITOR
        self.user32.GetMonitorInfoW.argtypes=[wintypes.HMONITOR,ctypes.POINTER(MONITORINFO)]
        self.user32.PostMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]

    def apply(self,hwnd):
        self.hwnd=int(hwnd)
        style=self.get_style(self.hwnd,GWL_STYLE)
        wanted=native_frame_style(style)
        if style!=wanted:
            ctypes.set_last_error(0)
            previous=self.set_style(self.hwnd,GWL_STYLE,wanted)
            if not previous and ctypes.get_last_error():return False
            # Recompute non-client area, but never move, resize, activate or reorder.
            if not self.user32.SetWindowPos(self.hwnd,None,0,0,0,0,0x0001|0x0002|0x0004|0x0010|0x0020):return False
        return True

    def system_command(self,command):
        return bool(self.hwnd and self.user32.PostMessageW(self.hwnd,0x0112,command,0))

    def nativeEventFilter(self,event_type,message):
        if event_type not in (b'windows_generic_MSG',b'windows_dispatcher_MSG') or not self.hwnd:return False,0
        msg=MSG.from_address(int(message))
        if int(msg.hwnd or 0)!=self.hwnd or msg.message!=0x0083:return False,0
        # lParam starts with the first proposed RECT for either NCCALCSIZE form.
        if not msg.lParam:return False,0
        if self.user32.IsZoomed(self.hwnd):
            info=MONITORINFO();info.cbSize=ctypes.sizeof(info)
            monitor=self.user32.MonitorFromWindow(self.hwnd,2)
            if self.user32.GetMonitorInfoW(monitor,ctypes.byref(info)):
                rect=RECT.from_address(msg.lParam)
                rect.left,rect.top,rect.right,rect.bottom=info.rcWork.left,info.rcWork.top,info.rcWork.right,info.rcWork.bottom
        return True,0
