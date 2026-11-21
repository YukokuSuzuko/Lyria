"""Fail-closed inventory checks for the Windows Qt candidate, not release approval.

source: inspect authorized input files only (works on Linux).
prebuild: require Windows x64/Python 3.12, installed dependency pins, load local
          ONNX models, exercise bundled FFmpeg/Tesseract, capture exact inputs.
postbuild: compare actual onedir files to that prebuild inventory, require native
           and QML artifacts, then exercise packaged FFmpeg/Tesseract on Windows.
No downloads, installation, license inference, or PATH executable fallback.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from fnmatch import fnmatchcase
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
import subprocess
import sys

MANIFEST_PATH = Path(__file__).with_name('portable_runtime_manifest.json')


def manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))


def issue(report, component, detail):
    report['errors'].append({'component': component, 'detail': detail})


def new_report(phase):
    return {'schema_version': 1, 'phase': phase, 'created_utc': datetime.now(timezone.utc).isoformat(),
            'contract_sha256': hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest(),
            'technical_ok': False, 'distribution_license_review_complete': False,
            'windows_acceptance_complete': False, 'errors': [], 'warnings': [],
            'files': [], 'build_inputs': [], 'packages': [], 'runtime_checks': {},
            'unverified_release_gates': manifest()['unverified_release_gates']}


def safe_file(root, relative, allow_empty=False):
    """A file cannot be supplied by a symlink outside the candidate/input tree."""
    root = Path(root).resolve()
    if Path(relative).is_absolute():
        raise ValueError('Expected a relative artifact path: ' + str(relative))
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise ValueError('Path escapes the selected tree: ' + str(relative))
    if not path.is_file() or (not allow_empty and path.stat().st_size == 0):
        raise ValueError('Missing, empty, or not a regular file: ' + str(relative))
    return path


def record(path, relative):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return {'path': str(relative).replace('\\', '/'), 'size': path.stat().st_size,
            'sha256': digest.hexdigest()}


def require_file(report, root, relative, allow_empty=False):
    try:
        return safe_file(root, relative, allow_empty=allow_empty)
    except (OSError, ValueError) as exc:
        issue(report, str(relative), str(exc))
        return None


def pe_x64_error(path):
    """Header/architecture sanity only, never a claim of executable validity."""
    try:
        with path.open('rb') as stream:
            header = stream.read(64)
            if len(header) != 64 or header[:2] != b'MZ':
                return 'Not a Windows PE image (missing DOS header)'
            offset = struct.unpack_from('<I', header, 60)[0]
            stream.seek(offset)
            pe = stream.read(26)
            if len(pe) != 26 or pe[:4] != b'PE\0\0':
                return 'Not a Windows PE image (missing PE header)'
            if struct.unpack_from('<H', pe, 4)[0] != 0x8664 or struct.unpack_from('<H', pe, 24)[0] != 0x20b:
                return 'Expected x86_64 PE32+ image'
    except (OSError, ValueError) as exc:
        return str(exc)
    return None


def source_audit(root):
    root = Path(root).resolve()
    spec = manifest()
    report = new_report('source')
    report['source_root'] = str(root)
    report['ffprobe_required'] = spec['ffprobe_required']
    for relative in spec['required_resources'] + spec['required_source_files'] + spec['external_files']:
        require_file(report, root, relative)
    # Build hooks are required and hashed, but must not become shipped runtime
    # resources just to make the source/prebuild evidence complete.
    for relative in spec['required_build_files']:
        checked = require_file(report, root, relative)
        if checked:
            report['build_inputs'].append(record(checked, relative))
    # Capture ALL source assets, Tesseract dependency DLLs, examples and historical
    # notices, not just the minimum named resources. Do not package caches/tests.
    for tree in spec['copied_trees'] + spec['external_trees']:
        base = root / tree
        if not base.is_dir():
            issue(report, tree, 'Missing required directory')
            continue
        for path in sorted(base.rglob('*')):
            if not path.is_file() or '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo'):
                continue
            relative = path.relative_to(root).as_posix()
            checked = require_file(report, root, relative)
            if checked:
                entry = record(checked, relative)
                entry['destination'] = ('_internal/' if tree in spec['copied_trees'] else '') + relative
                report['files'].append(entry)
    for relative in spec.get('internal_files', []):
        checked = require_file(report, root, relative)
        if checked:
            report['files'].append({**record(checked, relative), 'destination': '_internal/' + relative})
    for relative in spec['external_files']:
        checked = require_file(report, root, relative)
        if checked:
            report['files'].append({**record(checked, relative), 'destination': relative})
    for relative in spec['required_resources']:
        if relative.endswith('.exe'):
            path = root / relative
            if path.is_file():
                error = pe_x64_error(path)
                if error:
                    issue(report, relative, error)
    report['warnings'].append('Historical licenses/ files are unverified source inputs, not evidence of the current build versions or license compliance.')
    report['technical_ok'] = not report['errors']
    return report


def require_source_resources(root):
    report = source_audit(root)
    if not report['technical_ok']:
        raise RuntimeError('Portable input audit failed:\n' + '\n'.join(
            error['component'] + ': ' + error['detail'] for error in report['errors']))
    return report


def pins_from_file(path, seen=None):
    """Accept only exact pins and recursive -r includes; reject ambiguous input."""
    path = Path(path).resolve()
    seen = set() if seen is None else seen
    if path in seen:
        raise ValueError('Cyclic requirements include: ' + str(path))
    seen.add(path)
    pins = {}
    for raw in path.read_text(encoding='utf-8-sig').splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        if line.startswith('-r '):
            additions = pins_from_file(path.parent / line[3:].strip(), seen)
        else:
            match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([^\s;]+)', line)
            if not match:
                raise ValueError('Expected exact dependency pin: ' + line)
            additions = {normalize(match[1]): match[2]}
        for name, version in additions.items():
            if name in pins and pins[name] != version:
                raise ValueError('Conflicting dependency pin: ' + name)
            pins[name] = version
    seen.remove(path)
    return pins


def normalize(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def dependency_inventory(root, report, get_distribution=metadata.distribution):
    """Installed runtime dependency closure, exact metadata/notices, no license verdict."""
    from packaging.requirements import Requirement
    pins = pins_from_file(Path(root) / 'requirements.qt.txt')
    for name, expected in pins.items():
        try:
            actual = get_distribution(name).version
        except metadata.PackageNotFoundError:
            issue(report, name, 'Missing pinned build dependency ' + expected)
        else:
            if actual != expected:
                issue(report, name, f'Installed {actual}; required {expected}')
    try:
        get_distribution('onnxruntime')
    except metadata.PackageNotFoundError:
        pass
    else:
        issue(report, 'onnxruntime', 'CPU and DirectML wheels overlap the same import directory; remove CPU wheel from the isolated Windows build environment.')
    queue = list(manifest()['runtime_distributions'])
    visited = set()
    for name in queue:
        key = normalize(name)
        if key in visited:
            continue
        visited.add(key)
        try:
            dist = get_distribution(name)
        except metadata.PackageNotFoundError:
            issue(report, name, 'Missing runtime distribution')
            continue
        package = {'name': dist.metadata['Name'], 'version': dist.version,
                   'declared_license': dist.metadata.get('License-Expression') or dist.metadata.get('License'),
                   'metadata_files': [], 'notice_files': [], 'runtime_files': []}
        for relative in dist.files or []:
            relative = Path(str(relative))
            if relative.is_absolute() or '..' in relative.parts:
                continue
            is_metadata = any(part.endswith('.dist-info') for part in relative.parts)
            is_notice = bool(re.search(r'(^|[-_.])(license|licence|copying|notice|thirdpartynotices)', relative.name, re.I))
            is_runtime = ((key == 'av' and relative.parts[0] == 'av.libs') or
                          (key == 'onnxruntime-directml' and relative.suffix.lower() == '.dll'))
            if not (is_metadata or is_notice or is_runtime) or relative.suffix == '.pyc':
                continue
            path = Path(dist.locate_file(relative))
            if not path.is_file() or (is_notice and path.stat().st_size == 0):
                issue(report, name, 'Installed distribution lists a missing file or empty notice: ' + str(relative))
                continue
            entry = {**record(path, relative.as_posix()), 'source': str(path.resolve()),
                     'destination': '_internal/' + relative.as_posix()}
            if is_metadata:
                package['metadata_files'].append(entry)
            elif is_notice:
                package['notice_files'].append(entry)
            if is_runtime:
                package['runtime_files'].append(entry)
            if is_metadata and is_notice:
                package['notice_files'].append(entry)
        if not any(Path(f['path']).name == 'METADATA' for f in package['metadata_files']):
            issue(report, name, 'Missing installed wheel METADATA; cannot record actual version/provenance')
        if not package['notice_files']:
            report['warnings'].append(f'{package["name"]} {dist.version}: no license/notice file found in installed wheel records; release review must supply/verify applicable notices.')
        report['packages'].append(package)
        for text in dist.requires or []:
            req = Requirement(text)
            if req.marker and not req.marker.evaluate({'extra': ''}):
                continue
            queue.append(req.name)
            try:
                installed = get_distribution(req.name).version
                if installed not in req.specifier:
                    issue(report, name, f'Dependency {req} is not satisfied by {installed}')
            except metadata.PackageNotFoundError:
                issue(report, name, 'Missing dependency ' + str(req))
    return report['packages']


def collect_distribution_data(root):
    """Called by spec: copy exact installed metadata and notices, not stale copies."""
    report = new_report('collection')
    dependency_inventory(root, report)
    if report['errors']:
        raise RuntimeError('Dependency audit failed: ' + json.dumps(report['errors']))
    entries = {item['destination']: item for package in report['packages']
               for item in package['metadata_files'] + package['notice_files']}
    return [(item['source'], str(Path(destination).parent.relative_to('_internal')))
            for destination, item in sorted(entries.items())]


def runtime_binary_checks(resources, report, runner=subprocess.run, notices=None):
    """Use exact candidate binaries and model directory; no shell or PATH fallback."""
    resources = Path(resources).resolve()
    env = os.environ.copy()
    env['PATH'] = os.pathsep.join([str(resources / 'tesseract'), str(Path(env.get('SystemRoot', 'C:\\Windows')) / 'System32')])
    env['TESSDATA_PREFIX'] = str(resources / 'tesseract' / 'tessdata')
    commands = [('ffmpeg_version', resources / 'ffmpeg.exe', ['-hide_banner', '-version']),
                ('ffmpeg_subtitles', resources / 'ffmpeg.exe', ['-hide_banner', '-filters']),
                ('tesseract_version', resources / 'tesseract/tesseract.exe', ['--version'])]
    # The same manifest must govern file checks and actual model initialization.
    # Chinese QC defaults to chi_sim even when the project's source is Japanese.
    languages=sorted(Path(path).stem for path in manifest()['required_resources']
                     if path.startswith('resources/tesseract/tessdata/') and path.endswith('.traineddata'))
    for lang in languages:
        commands.append(('tesseract_load_' + lang, resources / 'tesseract/tesseract.exe',
                         ['stdin', 'stdout', '--tessdata-dir', str(resources / 'tesseract/tessdata'), '-l', lang, '--psm', '6']))
    for label, executable, args in commands:
        try:
            # A local blank PGM forces model initialization without user media.
            blank = b'P5\n16 16\n255\n' + b'\xff' * 256 if label.startswith('tesseract_load_') else None
            call_env=dict(env);call_cwd=resources;call_args=list(args)
            if label.startswith('tesseract_'):
                call_cwd=resources/'tesseract/tessdata';call_env['TESSDATA_PREFIX']='.'
                if '--tessdata-dir' in call_args:call_args[call_args.index('--tessdata-dir')+1]='.'
            completed = runner([str(executable), *call_args], input=blank, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=45, env=call_env, check=False,
                               cwd=str(call_cwd),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            output = completed.stdout.decode('utf-8', errors='replace')
            ok = completed.returncode == 0
            if label == 'ffmpeg_version' and notices is not None:
                notice = Path(notices) / 'FFmpeg-README.txt'
                version = re.search(r'^Version:\s*(\S+)', notice.read_text(encoding='utf-8-sig'), re.M)
                if not version or not re.search(r'^ffmpeg version ' + re.escape(version[1]) + r'(?:\s|$)', output, re.M):
                    issue(report, 'FFmpeg-README.txt', 'Bundled FFmpeg version does not match its historical notice; verify provenance and update notices from the actual authorized build.')
            if label == 'ffmpeg_subtitles':
                ok = ok and bool(re.search(r'^\s*\S+\s+subtitles\s', output, re.M))
            report['runtime_checks'][label] = {'ok': ok, 'returncode': completed.returncode, 'output': output}
            if not ok:
                issue(report, label, 'Bundled binary/model check failed; see runtime_checks output')
        except (OSError, subprocess.SubprocessError) as exc:
            issue(report, label, str(exc))


def prebuild_audit(root):
    report = source_audit(root)
    report['phase'] = 'prebuild'
    if sys.platform != 'win32' or struct.calcsize('P') != 8 or sys.version_info[:2] != (3, 12):
        issue(report, 'build_platform', 'Build requires Windows x86_64 with Python 3.12; Linux source checks are not a Windows build.')
    dependency_inventory(root, report)
    if not report['errors']:
        runtime_binary_checks(Path(root) / 'resources', report, notices=Path(root) / 'licenses')
        try:
            import av
            import onnxruntime as ort
            report['runtime_checks']['pyav_linked_versions'] = {key: list(value) for key, value in av.library_versions.items()}
            if 'DmlExecutionProvider' not in ort.get_available_providers():
                issue(report, 'DirectML', 'Installed runtime does not advertise DmlExecutionProvider')
            for relative in manifest()['required_resources']:
                if not relative.endswith('.onnx'):
                    continue
                session = ort.InferenceSession(str(Path(root) / relative), providers=['CPUExecutionProvider'])
                if '_rec_' in relative and not session.get_modelmeta().custom_metadata_map.get('character'):
                    issue(report, relative, 'Recognition model lacks embedded character dictionary; offline behavior is not established')
                report['runtime_checks'][relative] = {'cpu_model_loaded': True}
                del session
        except Exception as exc:
            issue(report, 'python_runtime_load', f'{type(exc).__name__}: {exc}')
    report['technical_ok'] = not report['errors']
    return report


def excluded_qt_reason(relative, spec):
    """Detect excluded Qt collection by actual path, including Windows spelling.

    Native names are checked anywhere in the portable tree so an alternate
    collection destination cannot hide them. QML paths use exact component
    boundaries; an allowed parent does not allow an excluded child module.
    This is a technical scope check, not a licensing classification.
    """
    path = PurePosixPath(str(relative).replace('\\', '/').casefold())
    for pattern in spec['excluded_qt_native_names']:
        if fnmatchcase(path.name, pattern.casefold()):
            return 'Excluded unused Qt native component was collected: ' + pattern
    parts = path.parts
    for index in range(len(parts) - 2):
        if parts[index:index + 2] != ('pyside6', 'qml'):
            continue
        qml_path = PurePosixPath(*parts[index + 2:])
        for module in spec['excluded_qt_qml_roots']:
            prefix = PurePosixPath(module.casefold()).parts
            if qml_path.parts[:len(prefix)] == prefix:
                return 'Excluded unused QML module tree was collected: ' + module
        if path.name == 'qmldir' and qml_path.parent.as_posix() not in {
                module.casefold() for module in spec['qml_modules']}:
            return 'QML module outside the reviewed Basic-style closure was collected: ' + qml_path.parent.as_posix()
    return None


def postbuild_audit(root, prebuild, run_binaries=False):
    root = Path(root).resolve()
    report = new_report('postbuild')
    report['portable_root'] = str(root)
    spec = manifest()
    contract = require_file(report, root, 'portable_runtime_manifest.json')
    if contract and record(contract, contract.name)['sha256'] != report['contract_sha256']:
        issue(report, 'portable_runtime_manifest.json', 'Packaged contract differs from the audited build contract')
    if prebuild.get('phase') != 'prebuild' or prebuild.get('technical_ok') is not True or prebuild.get('errors'):
        issue(report, 'prebuild_evidence', 'A successful prebuild report is required; source-only or failed reports cannot qualify the package.')
    if prebuild.get('contract_sha256') != report['contract_sha256']:
        issue(report, 'prebuild_evidence', 'Manifest changed since prebuild; rerun prebuild before auditing artifacts.')
    # The audit runs from the reviewed source checkout, not from the portable
    # application. Detect a changed/deleted hook or spec after prebuild without
    # requiring these build-only Python files inside the distributed package.
    build_inputs = {item['path']: item for item in prebuild.get('build_inputs', [])}
    report['build_inputs'] = prebuild.get('build_inputs', [])
    source_root = Path(__file__).resolve().parents[1]
    for relative in spec['required_build_files']:
        entry = build_inputs.get(relative)
        if not entry:
            issue(report, 'prebuild_evidence', 'Missing audited build input: ' + relative)
            continue
        current = require_file(report, source_root, relative)
        if current and record(current, relative)['sha256'] != entry.get('sha256'):
            issue(report, relative, 'Build input changed since prebuild; rebuild and audit with the reviewed hooks/spec.')
    report['packages'] = prebuild.get('packages', [])
    recorded = {normalize(package['name']) for package in report['packages']}
    for name in spec['runtime_distributions']:
        if normalize(name) not in recorded:
            issue(report, name, 'Missing runtime distribution in prebuild evidence')
    report['warnings'] += prebuild.get('warnings', [])
    expected = list(prebuild.get('files', []))
    source_paths = {item['path'] for item in expected}
    for relative in spec['required_resources'] + spec['required_source_files'] + spec['external_files']:
        if relative not in source_paths:
            issue(report, 'prebuild_evidence', 'Missing audited source file: ' + relative)
    for package in report['packages']:
        if not package.get('version') or not any(Path(item['path']).name == 'METADATA' for item in package['metadata_files']):
            issue(report, package['name'], 'Incomplete installed distribution metadata evidence')
    expected += [item for package in report['packages'] for item in package['metadata_files'] + package['notice_files'] + package.get('runtime_files', [])]
    # Independently require every critical resource even if evidence is incomplete.
    for relative in spec['required_resources']:
        require_file(report, root, '_internal/' + relative)
    for item in expected:
        destination = item['destination']
        path = require_file(report, root, destination, allow_empty=item['size'] == 0)
        if path and record(path, destination)['sha256'] != item['sha256']:
            issue(report, destination, 'Packaged file differs from audited source/build environment')
    for component, patterns in spec['native_artifacts'].items():
        found = [p for pattern in patterns for p in root.glob(pattern) if p.is_file()]
        if not found:
            issue(report, component, 'Missing required native component: ' + ' OR '.join(patterns))
        for path in found:
            checked = require_file(report, root, path.relative_to(root))
            if checked:
                error = pe_x64_error(checked)
                if error:
                    issue(report, component, f'{path.relative_to(root)}: {error}')
    for module in spec['qml_modules']:
        base = '_internal/PySide6/qml/' + module
        qmldir = require_file(report, root, base + '/qmldir')
        if qmldir:
            plugins = re.findall(r'^(?:optional\s+)?plugin\s+(\S+)(?:[ \t]+(\S+))?[ \t]*$', qmldir.read_text(encoding='utf-8'), re.M)
            if not plugins:
                issue(report, base, 'Expected a native QML plugin declaration')
            for name, directory in plugins:
                require_file(report, root, str(Path(base) / directory / (name + '.dll')))
    for relative in spec['required_package_data']:
        require_file(report, root, relative)
    for path in sorted(root.rglob('*')):
        if path.is_file() and path.name != 'portable-audit.json':
            reason = excluded_qt_reason(path.relative_to(root), spec)
            if reason:
                issue(report, path.relative_to(root).as_posix(), reason)
            checked = require_file(report, root, path.relative_to(root), allow_empty=True)
            if checked:
                if path.suffix.lower() in ('.dll', '.pyd', '.exe'):
                    error = pe_x64_error(checked)
                    if error:
                        issue(report, path.relative_to(root).as_posix(), error)
                report['files'].append(record(checked, path.relative_to(root)))
    report['qml_module_inventory'] = sorted(path.parent.relative_to(root).as_posix() for path in root.rglob('qmldir'))
    report['warnings'].append('QML/module inventory and excluded-component checks cover actual collected paths; passing them does not establish redistribution rights or complete the license review.')
    if run_binaries:
        if sys.platform != 'win32':
            issue(report, 'runtime_platform', 'Packaged Windows binaries may only be exercised on Windows.')
        elif not report['errors']:
            runtime_binary_checks(root / '_internal/resources', report, notices=root / 'licenses')
    else:
        report['warnings'].append('Packaged executable/model load checks were not run; this is a static artifact audit only.')
    report['technical_ok'] = not report['errors']
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('source', 'prebuild', 'postbuild'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--prebuild-report', type=Path)
    parser.add_argument('--run-binaries', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.phase == 'postbuild':
            if not args.prebuild_report:
                parser.error('postbuild requires --prebuild-report')
            report = postbuild_audit(args.root, json.loads(args.prebuild_report.read_text(encoding='utf-8-sig')), args.run_binaries)
        else:
            report = (source_audit if args.phase == 'source' else prebuild_audit)(args.root)
    except Exception as exc:
        report = new_report(args.phase)
        issue(report, 'audit_error', f'{type(exc).__name__}: {exc}')
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for error in report['errors']:
        print(error['component'] + ': ' + error['detail'], file=sys.stderr)
    print(f'{args.phase}: {"PASS" if report["technical_ok"] else "FAIL"}; license/Windows acceptance NOT approved. Report: {args.report}')
    return 0 if report['technical_ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
