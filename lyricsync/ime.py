"""Read Windows IME composition state without replacing Tk's window procedure."""
import sys


def composition_active(widget):
    if sys.platform != 'win32':
        return False
    import ctypes
    from ctypes import wintypes
    imm = ctypes.WinDLL('imm32', use_last_error=True)
    user = ctypes.WinDLL('user32', use_last_error=True)
    imm.ImmGetContext.argtypes = [wintypes.HWND]
    imm.ImmGetContext.restype = wintypes.HANDLE
    imm.ImmReleaseContext.argtypes = [wintypes.HWND, wintypes.HANDLE]
    imm.ImmGetCompositionStringW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    imm.ImmGetCompositionStringW.restype = ctypes.c_long
    user.GetFocus.restype = wintypes.HWND
    # GetFocus catches the actual Tk text child; the widget is a fallback for
    # composition callbacks immediately before focus changes.
    for hwnd in {user.GetFocus(), widget.winfo_id()} - {None, 0}:
        context = imm.ImmGetContext(hwnd)
        if context:
            try:
                if imm.ImmGetCompositionStringW(context, 8, None, 0) > 0:  # GCS_COMPSTR
                    return True
            finally:
                imm.ImmReleaseContext(hwnd, context)
    return False
