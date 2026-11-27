"""Lyria's pinned PySide6 6.8.3 / PyInstaller 6.16.0 QML collection policy.
Path-selection code only; this does not load Qt or decide distribution rights.
Selecting a parent module does NOT select its child modules.
"""
from pathlib import PurePosixPath, PureWindowsPath


ALLOWED_MODULE_DIRS = frozenset({
    'QtQml', 'QtQml/Models', 'QtQml/WorkerScript',
    'QtQuick', 'QtQuick/Window', 'QtQuick/Layouts', 'QtQuick/Templates', 'QtQuick/Shapes',
    'QtQuick/Controls', 'QtQuick/Controls/impl',
    'QtQuick/Controls/Basic', 'QtQuick/Controls/Basic/impl',
    'QtMultimedia',
})
BUILTIN_QML_MODULES = frozenset({'QML'})


def relative_path(value):
    # The policy is also tested on Linux against Windows paths. Never interpret
    # backslashes, drive-relative names, or UNC paths as harmless POSIX names.
    text = str(value).replace('\\', '/')
    path = PurePosixPath(text)
    if path.is_absolute() or PureWindowsPath(text).drive or '..' in path.parts:
        raise ValueError('QML path must be relative and must not escape its root')
    return path


def owning_module(relative_file, module_dirs):
    path = relative_path(relative_file)
    for parent in path.parents:
        if parent.as_posix() in module_dirs:
            return parent.as_posix()
    return None


def is_runtime_member(relative_file):
    # Qt Creator/Designer editing metadata is not a runtime import.
    return 'designer' not in relative_path(relative_file).parts


def selected_files(relative_files, module_dirs):
    missing = ALLOWED_MODULE_DIRS.difference(module_dirs)
    if missing:
        raise RuntimeError('Missing required QML module directories: ' + ', '.join(sorted(missing)))
    return sorted(relative_path(name).as_posix() for name in relative_files
                  if owning_module(name, module_dirs) in ALLOWED_MODULE_DIRS and is_runtime_member(name))
