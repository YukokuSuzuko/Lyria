# Windows onedir candidate. Build on Windows only; not a verified distribution.
from pathlib import Path
import sys
import struct
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_dynamic_libs, get_package_paths

root = Path(SPECPATH)
sys.path.insert(0, str(root))
from tools.audit_portable_runtime import require_source_resources, collect_distribution_data, manifest
from lyricsync import __version__, __display_version__
from packaging.version import Version
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct,
    VarFileInfo, VarStruct,
)
release = Version(__version__)
numeric_version = tuple((release.release + (0, 0, 0))[:3]) + (release.dev or 0,)
windows_version = VSVersionInfo(
    ffi=FixedFileInfo(filevers=numeric_version, prodvers=numeric_version,
                     mask=0x3f, flags=0x2 if release.is_prerelease else 0,
                     OS=0x40004, fileType=0x1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable('040904B0', [
        StringStruct('FileDescription', 'Lyria lyric editor'),
        StringStruct('FileVersion', __display_version__),
        StringStruct('InternalName', 'Lyria'),
        StringStruct('OriginalFilename', 'Lyria.exe'),
        StringStruct('ProductName', 'Lyria'),
        StringStruct('ProductVersion', __display_version__),
    ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])],
)

# Reject incomplete offline resources even when PyInstaller is invoked directly.
require_source_resources(root)
if sys.platform != 'win32' or struct.calcsize('P') != 8 or sys.version_info[:2] != (3, 12):
    raise RuntimeError('The Qt portable candidate requires Windows x86_64 / Python 3.12.')
# Includes the pinned Noto CJK fallback; required_resources fails closed if absent.
datas = [(str(root / 'resources'), 'resources'),
         (str(root / 'desktop_qt' / 'qml'), 'desktop_qt/qml'),
         (str(root / 'desktop_qt' / 'assets'), 'desktop_qt/assets')]
# External isolated Python must read these scripts; PYZ modules are not enough.
# The same audited loose-file contract covers alignment, separation and ASR.
# Heavy inference dependencies and downloaded weights stay in the separate
# user-selected runtime/cache, never in the editor's bundled environment.
datas += [(str(root / name), str(Path(name).parent))
          for name in manifest()['internal_files']]
datas += [(str(root / 'requirements.local-alignment.txt'), '.')]
datas += [(str(root / 'docs' / name), 'docs') for name in (
    'LOCAL_ALIGNMENT.md', 'UVR_MODEL_ATTRIBUTION.md', 'PHASE5_LOCAL_MODELS_20261112.md')]
# Retain the packaged dictionary/config files. Exclude only duplicate ONNX models.
datas += collect_data_files('rapidocr', excludes=['models/*.onnx'])
datas += collect_distribution_data(root)
# PyAV's Windows wheels keep their FFmpeg DLLs beside av/, not inside it.
# Preserve this layout explicitly; collect_dynamic_libs('av') alone misses it.
av_base, _ = get_package_paths('av')
av_libraries = Path(av_base) / 'av.libs'
if not av_libraries.is_dir() or not list(av_libraries.glob('*.dll')):
    raise RuntimeError('Missing PyAV wheel av.libs DLLs; portable decoding cannot rely on PATH.')
datas += [(str(p), 'av.libs') for p in av_libraries.iterdir() if p.is_file()]
hidden = collect_submodules('rapidocr') + collect_submodules('av') + ['onnxruntime', 'pytesseract', 'av', 'regex',
    'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtQml', 'PySide6.QtQuick',
    'PySide6.QtQuickControls2', 'PySide6.QtSvg', 'PySide6.QtMultimedia', 'PySide6.QtWidgets']
from tools.reading_bundle_build import collect_reading_bundle
reading_datas, reading_binaries = collect_reading_bundle(os.environ.get('LYRIA_READING_BUNDLE'))
datas += reading_datas

# A build host can supply the same vetted Windows portable runtime DLLs used
# in the preceding release. Include only individually attested distributables.
redist = os.environ.get('LYRIA_WINDOWS_REDIST')
if redist:
    import json
    import hashlib
    for entry in json.loads((root / 'tools/windows_portable_redist.json').read_text('utf8')):
        filename = entry['name']
        if Path(filename).name != filename or not filename.lower().endswith('.dll'):
            raise ValueError('Invalid redistributable filename')
        source = Path(redist) / filename
        if source.stat().st_size != entry['bytes'] or hashlib.sha256(source.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('Windows redistributable differs from the audited runtime: ' + filename)
        datas.append((str(source), '.'))

a = Analysis([str(root / 'app_qt.py')], pathex=[str(root)],
    binaries=collect_dynamic_libs('onnxruntime') + reading_binaries, datas=datas, hiddenimports=hidden,
    # Pinned Qt collection policy; do not recursively collect every QML module.
    hookspath=[str(root / 'tools' / 'pyinstaller_hooks')], hooksconfig={}, runtime_hooks=[],
    excludes=['tkinter', 'PIL.ImageTk', 'PyQt5', 'PyQt6', 'PySide2', 'paddle', 'torch',
              'transformers', 'librosa', 'nagisa', 'soynlp', 'pykakasi',
              'openvino', 'pytest', 'matplotlib', 'IPython', 'notebook'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Lyria', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    disable_windowed_traceback=False, argv_emulation=False, version=windows_version,
    icon=str(root / 'desktop_qt' / 'assets' / 'app' / 'lyria.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Lyria')
