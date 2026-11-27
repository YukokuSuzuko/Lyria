"""Collect Lyria's Basic-style QML closure, not all installed Qt QML modules.
Uses a custom hook override and a version-pinned PyInstaller private helper.
Linux mock/static checks are not an actual PyInstaller or Windows build.
"""
from pathlib import Path
import importlib.metadata
import importlib.util
import re
from PyInstaller.utils.hooks.qt import add_qt6_dependencies, pyside6_library_info

if importlib.metadata.version('PyInstaller') != '6.16.0' or importlib.metadata.version('PySide6') != '6.8.3':
    raise RuntimeError('Review Lyria QML policy before changing the audited toolchain versions')

policy_spec = importlib.util.spec_from_file_location('_lyria_qml_policy', Path(__file__).with_name('lyria_qml_policy.py'))
policy = importlib.util.module_from_spec(policy_spec)
policy_spec.loader.exec_module(policy)
hiddenimports, binaries, datas = add_qt6_dependencies(__file__)


def is_unused_quick3d_profiler(entry):
    # QtQml also collects qmltooling, independently of its QML import tree.
    # This one plugin links Qt6Quick3DUtils.dll in the pinned Windows wheel.
    # Remove the unused ingress now, before dependency analysis, not a final DLL.
    source, _destination = entry
    return str(source).replace('\\', '/').lower().endswith('/qmltooling/qmldbg_quick3dprofiler.dll')


binaries = [entry for entry in binaries if not is_unused_quick3d_profiler(entry)]
location = pyside6_library_info.location
qml_location = location.get('QmlImportsPath') or location.get('Qml2ImportsPath')
if not qml_location:
    raise RuntimeError('Required Qt QML root is missing')
qml_root = Path(qml_location).resolve()
if not qml_root.is_dir():
    raise RuntimeError('Required Qt QML root is missing')
qmldirs = {p.parent.relative_to(qml_root).as_posix(): p for p in qml_root.rglob('qmldir')}
missing = policy.ALLOWED_MODULE_DIRS.difference(qmldirs)
if missing:
    raise RuntimeError('Missing required QML module directories: ' + ', '.join(sorted(missing)))
dest_root = Path(pyside6_library_info.qt_rel_dir) / 'qml'
for module in sorted(policy.ALLOWED_MODULE_DIRS):
    qmldir = qmldirs[module]
    # This is intentionally version-pinned to the helper reviewed in 6.16.0.
    module_binaries, module_datas = pyside6_library_info._process_qml_plugin(qmldir)
    # Fail closed if dependency validation silently discarded a required module.
    plugin_names = [m[1] + '.dll' for line in qmldir.read_text(encoding='utf-8').splitlines()
                    if (m := re.fullmatch(r'(?:optional\s+)?plugin\s+(\w+)', line.strip()))]
    found_names = {Path(p).name for p in module_binaries}
    if not plugin_names or not set(plugin_names).issubset(found_names) or not module_datas:
        raise RuntimeError('Required QML plugin failed dependency validation: ' + module)
    for path in module_binaries:
        path = Path(path).resolve()
        if not path.is_file():
            raise RuntimeError('Required QML plugin file is missing: ' + str(path))
        relative = path.relative_to(qml_root)
        if policy.owning_module(relative.as_posix(), qmldirs) != module:
            raise RuntimeError('QML plugin escaped its module: ' + str(path))
        binaries.append((str(path), str(dest_root / relative.parent)))
    # Expand directories ourselves. Passing a parent directory through as data
    # would let PyInstaller recursively reintroduce excluded child modules.
    for item in module_datas:
        item = Path(item)
        paths = [item] if item.is_file() else (p for p in item.rglob('*') if p.is_file())
        for path in paths:
            path = path.resolve()
            relative = path.relative_to(qml_root)
            if policy.owning_module(relative.as_posix(), qmldirs) != module or not policy.is_runtime_member(relative.as_posix()):
                continue
            if path.suffix.lower() in {'.dll', '.pyd', '.exe'}:
                raise RuntimeError('Unexpected executable data in required QML module: ' + str(path))
            datas.append((str(path), str(dest_root / relative.parent)))
