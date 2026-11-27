"""Retain default QtGui collection except the unused Qt Virtual Keyboard plugin.
The Windows platform/native IME plugins stay intact. Windows testing is pending.
"""
import importlib.metadata
from PyInstaller.utils.hooks.qt import add_qt6_dependencies

if importlib.metadata.version('PyInstaller') != '6.16.0' or importlib.metadata.version('PySide6') != '6.8.3':
    raise RuntimeError('Review the QtGui plugin policy before changing pinned versions')
hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
def is_unused_qtgui_plugin(entry):
    source, _destination = entry
    normalized = str(source).replace('\\', '/').lower()
    return (normalized.endswith('/platforminputcontexts/qtvirtualkeyboardplugin.dll')
            or normalized.endswith('/imageformats/qpdf.dll'))
binaries = [entry for entry in binaries if not is_unused_qtgui_plugin(entry)]
