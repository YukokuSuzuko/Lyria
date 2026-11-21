"""Real filesystem/synthetic artifact audits, never execution of fake Windows files."""
from __future__ import annotations

import json
import ast
import importlib.metadata
from pathlib import Path
import re
import runpy
import struct
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from tools import audit_portable_runtime as audit
from tools.pyinstaller_hooks import lyria_qml_policy as qml_policy

ROOT = Path(__file__).resolve().parents[1]
MODEL_WORKERS = {
    'lyricsync/local_alignment_worker.py', 'lyricsync/local_models.py',
    'lyricsync/local_separation_worker.py', 'lyricsync/local_transcription_worker.py',
    'lyricsync/nextfire_worker.py', 'lyricsync/japanese_readings.py',
    'lyricsync/experimental_nextfire_alignment.py', 'lyricsync/dev9_long_audio.py',
    'lyricsync/alignment_romaji.py', 'lyricsync/sudachi_readings.py',
    'lyricsync/alignment_tokens.py', 'lyricsync/nextfire_tail_guard.py',
    'lyricsync/shared_runtime.py',
}
MODEL_DOCS = {
    'docs/LOCAL_ALIGNMENT.md', 'docs/UVR_MODEL_ATTRIBUTION.md',
    'docs/PHASE5_LOCAL_MODELS_20261112.md',
}


def assert_loose_worker_import_closure(root, loose_files):
    """Read/parse real worker files without importing Torch or running models.

    An external interpreter cannot import project helpers hidden inside PYZ.
    Follow sibling imports, including relative/project-qualified references;
    fail if any helper is absent from the loose data contract.
    """
    root = Path(root)
    for relative in sorted(loose_files):
        source = audit.safe_file(root, relative)
        tree = ast.parse(source.read_text(encoding='utf-8-sig'), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [(alias.name, False) for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [(node.module, bool(node.level))] if node.module else [(alias.name, True) for alias in node.names]
            else:
                continue
            for name, is_relative in modules:
                if name == 'lyricsync' or name.startswith('lyricsync.'):
                    raise AssertionError(f'{relative}: package-qualified import {name} needs a readable package, not PYZ')
                head = name.split('.')[0]
                peer = 'lyricsync/' + head + '.py'
                if is_relative or head.startswith('local_') or (root / peer).is_file():
                    if peer not in loose_files:
                        raise AssertionError(f'{relative}: local import {name} is not declared as readable loose data ({peer})')
                    audit.safe_file(root, peer)


def fake_pe(machine=0x8664):
    data = bytearray(256)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 60, 64)
    data[64:68] = b'PE\0\0'
    struct.pack_into('<H', data, 68, machine)
    struct.pack_into('<H', data, 88, 0x20b)
    return bytes(data)


def put(root, relative, data=b'synthetic fixture, not a runtime'):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class PortableResourceGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        self.spec = audit.manifest()
        for relative in self.spec['required_resources'] + self.spec['required_source_files'] + self.spec['external_files'] + self.spec.get('internal_files', []):
            put(self.source, relative, fake_pe() if relative.endswith('.exe') else b'fixture')
        # Every declared tree needs synthetic contents. Release evidence trees
        # are part of the copy contract too, not special-cased application data.
        for relative in self.spec['external_trees']:
            put(self.source, relative + '/synthetic-audit-fixture.txt')
        for relative in self.spec['required_build_files']:
            put(self.source, relative, (ROOT / relative).read_bytes())
        put(self.source, 'licenses/FFmpeg-README.txt', b'Version: test-build\n')

    def make_package(self):
        """Synthetic successful prebuild evidence; no fake binary is executed."""
        self.prebuild = audit.source_audit(self.source)
        self.assertTrue(self.prebuild['technical_ok'], self.prebuild['errors'])
        self.prebuild['phase'] = 'prebuild'
        self.package = self.base / 'portable'
        put(self.package, 'portable_runtime_manifest.json', audit.MANIFEST_PATH.read_bytes())
        for item in self.prebuild['files']:
            put(self.package, item['destination'], (self.source / item['path']).read_bytes())
        for name in self.spec['runtime_distributions']:
            relative = name + '-1.0.dist-info/METADATA'
            data = ('Name: ' + name + '\nVersion: 1.0\n').encode()
            path = put(self.package, '_internal/' + relative, data)
            entry = {**audit.record(path, relative), 'destination': '_internal/' + relative}
            self.prebuild['packages'].append({'name': name, 'version': '1.0', 'metadata_files': [entry], 'notice_files': [], 'runtime_files': []})
        for patterns in self.spec['native_artifacts'].values():
            put(self.package, patterns[0].replace('*', 'fixture'), fake_pe())
        for module in self.spec['qml_modules']:
            base = '_internal/PySide6/qml/' + module
            put(self.package, base + '/qmldir', b'module fixture\nplugin fixtureplugin\n')
            put(self.package, base + '/fixtureplugin.dll', fake_pe())
        for relative in self.spec['required_package_data']:
            put(self.package, relative)

    def check_package(self):
        return audit.postbuild_audit(self.package, self.prebuild)

    def test_external_alignment_scripts_are_audited_as_loose_internal_data(self):
        self.make_package()
        scripts=MODEL_WORKERS
        self.assertEqual(set(self.spec['internal_files']),scripts)
        for relative in scripts:
            with self.subTest(script=relative):
                path=self.package/'_internal'/relative
                original=path.read_bytes()
                path.write_bytes(b'wrong worker revision')
                report=self.check_package()
                self.assertFalse(report['technical_ok'])
                self.assertTrue(any(e['component']=='_internal/'+relative for e in report['errors']))
                path.write_bytes(original)

    def test_missing_each_external_worker_fails_source_gate(self):
        for relative in MODEL_WORKERS:
            with self.subTest(script=relative):
                path=self.source/relative;original=path.read_bytes();path.unlink()
                report=audit.source_audit(self.source)
                self.assertFalse(report['technical_ok'])
                self.assertTrue(any(error['component']==relative for error in report['errors']))
                path.write_bytes(original)

    def test_model_docs_attribution_are_mandatory_and_hash_checked(self):
        self.assertTrue(MODEL_DOCS <= set(self.spec['external_files']))
        self.make_package()
        for relative in MODEL_DOCS:
            with self.subTest(document=relative):
                path=self.source/relative;original=path.read_bytes();path.unlink()
                self.assertFalse(audit.source_audit(self.source)['technical_ok'])
                path.write_bytes(original)
                packaged=self.package/relative;packaged.write_bytes(b'changed attribution')
                self.assertFalse(self.check_package()['technical_ok'])
                packaged.write_bytes(original)

    def test_spec_collects_manifest_workers_and_model_docs_as_loose_files(self):
        # Execute only the two data-list expressions, not PyInstaller or workers.
        tree=ast.parse((ROOT/'LyriaQt.spec').read_text(encoding='utf-8'))
        nodes=[]
        for node in tree.body:
            if isinstance(node,ast.AugAssign) and isinstance(node.target,ast.Name) and node.target.id=='datas':
                strings={x.value for x in ast.walk(node) if isinstance(x,ast.Constant) and isinstance(x.value,str)}
                if 'internal_files' in strings or 'UVR_MODEL_ATTRIBUTION.md' in strings:nodes.append(node)
        self.assertEqual(len(nodes),2)
        scope={'root':self.source,'Path':Path,'manifest':audit.manifest,'datas':[]}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'<isolated spec data expressions>','exec'),scope)
        expected=MODEL_WORKERS | MODEL_DOCS
        collected={Path(src).relative_to(self.source).as_posix():Path(dest).as_posix() for src,dest in scope['datas']}
        self.assertEqual(set(collected),expected)
        for name in expected:
            self.assertEqual(collected[name],str(Path(name).parent).replace('\\','/'))
            audit.safe_file(self.source,name)

    def test_heavy_model_runtime_is_not_a_gui_dependency(self):
        tree=ast.parse((ROOT/'LyriaQt.spec').read_text(encoding='utf-8'))
        analysis=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='Analysis')
        excluded=ast.literal_eval(next(k.value for k in analysis.keywords if k.arg=='excludes'))
        self.assertTrue({'torch','transformers','librosa','nagisa','soynlp','pykakasi'} <= set(excluded))
        gui=set(self.spec['runtime_distributions']) | set(audit.pins_from_file(ROOT/'requirements.qt.txt'))
        self.assertFalse(gui & {'torch','transformers','onnxruntime-gpu','onnxruntime','audio-separator'})
        self.assertEqual(set(self.spec['internal_files']),MODEL_WORKERS)
        self.assertFalse(any(p.endswith(('.safetensors','.pt','.pth')) for p in self.spec['required_resources']))

    def test_separate_requirements_keep_verified_pins_and_ort_mutually_exclusive(self):
        pins=audit.pins_from_file(ROOT/'requirements.local-alignment.txt')
        self.assertEqual(pins,{'transformers':'5.22.2','numpy':'2.2.6','librosa':'0.11.0',
                               'jinja2':'3.1.6','nagisa':'0.3.1','soynlp':'0.0.493','pykakasi':'2.3.0'})
        text=(ROOT/'requirements.local-alignment.txt').read_text(encoding='utf-8')
        for pin in ('torch==2.9.1','onnxruntime-gpu==1.23.2','onnxruntime==1.23.2'):
            self.assertIn(pin,text)
        self.assertNotIn('onnxruntime',pins);self.assertNotIn('onnxruntime-gpu',pins)

    def test_worker_import_closure_rejects_helpers_only_present_in_pyz(self):
        for relative in MODEL_WORKERS:put(self.source,relative,b'# independent test module\n')
        worker='lyricsync/local_separation_worker.py'
        put(self.source,worker,b'from local_models import ModelManager\n')
        assert_loose_worker_import_closure(self.source,MODEL_WORKERS)
        for statement in ('from local_missing import helper','from .local_missing import helper',
                          'from lyricsync.local_models import ModelManager'):
            with self.subTest(statement=statement):
                put(self.source,worker,(statement+'\n').encode())
                with self.assertRaises(AssertionError):assert_loose_worker_import_closure(self.source,MODEL_WORKERS)

    def test_actual_external_worker_sources_have_closed_readable_imports(self):
        missing=[p for p in sorted(MODEL_WORKERS | MODEL_DOCS) if not (ROOT/p).is_file()]
        if missing:
            self.skipTest('Awaiting model-recovery integration; actual closure NOT verified: '+', '.join(missing))
        assert_loose_worker_import_closure(ROOT,set(self.spec['internal_files']))

    def test_complete_source_structure_passes_only_static_audit(self):
        report = audit.source_audit(self.source)
        self.assertTrue(report['technical_ok'], report['errors'])
        self.assertFalse(report['distribution_license_review_complete'])
        self.assertFalse(report['windows_acceptance_complete'])
        self.assertFalse(report['ffprobe_required'])
        self.assertEqual(report['runtime_checks'], {})

    def test_manifest_covers_actual_default_source_and_qc_languages(self):
        from lyricsync.model import Project
        required=set(Project().settings['language'].split('+'))|{'jpn_vert'}
        # Inspect the current default arguments without invoking the OCR/QC
        # pipeline, allocating a provider, or accepting a duplicated fixture.
        tree=ast.parse((ROOT/'lyricsync/audit_service.py').read_text(encoding='utf8'))
        for node in ast.walk(tree):
            if (isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
                    and isinstance(node.func.value,ast.Name) and node.func.value.id=='options'
                    and node.func.attr=='get' and len(node.args)>=2
                    and isinstance(node.args[0],ast.Constant)
                    and node.args[0].value in ('chinese_language','source_language')
                    and isinstance(node.args[1],ast.Constant)):
                required.update(node.args[1].value.split('+'))
        self.assertIn('chi_sim',required)
        bundled={Path(path).stem for path in self.spec['required_resources'] if path.endswith('.traineddata')}
        self.assertTrue(required<=bundled, required-bundled)

    def test_each_real_required_resource_is_mandatory(self):
        for relative in self.spec['required_resources']:
            with self.subTest(resource=relative):
                path = self.source / relative
                original = path.read_bytes()
                path.unlink()
                report = audit.source_audit(self.source)
                self.assertFalse(report['technical_ok'])
                self.assertTrue(any(e['component'] == relative for e in report['errors']))
                path.write_bytes(original)

    def test_build_hooks_and_spec_are_required_but_not_shipped_as_resources(self):
        report = audit.source_audit(self.source)
        self.assertEqual({item['path'] for item in report['build_inputs']}, set(self.spec['required_build_files']))
        self.assertFalse(set(self.spec['required_build_files']) & {item['path'] for item in report['files']})
        for relative in self.spec['required_build_files']:
            with self.subTest(build_input=relative):
                path = self.source / relative
                original = path.read_bytes()
                path.unlink()
                with self.assertRaisesRegex(RuntimeError, re.escape(relative)):
                    audit.require_source_resources(self.source)
                path.write_bytes(original)

    def test_postbuild_rejects_missing_or_changed_build_input_evidence(self):
        self.make_package()
        original = self.prebuild['build_inputs']
        self.prebuild['build_inputs'] = []
        self.assertTrue(any('Missing audited build input' in item['detail'] for item in self.check_package()['errors']))
        self.prebuild['build_inputs'] = [dict(item) for item in original]
        self.prebuild['build_inputs'][1]['sha256'] = 'stale'
        report = self.check_package()
        self.assertTrue(any(item['component'] == original[1]['path'] and 'changed since prebuild' in item['detail'] for item in report['errors']))

    def test_postbuild_rejects_each_excluded_qt_family_with_actual_filename(self):
        self.make_package()
        paths = ['_internal/other/' + name.replace('*', 'Fixture') for name in self.spec['excluded_qt_native_names']]
        paths += ['_internal/PySide6/qml/' + module + '/nested/leaked.qml' for module in self.spec['excluded_qt_qml_roots']]
        paths += ['_internal/PySide6/qml/QtQuick/Controls/Material/qmldir',
                  '_internal/PYSIDE6/QML/QTQUICK/VIRTUALKEYBOARD/leaked.qml']
        for relative in paths:
            with self.subTest(relative=relative):
                path = put(self.package, relative, fake_pe() if relative.lower().endswith(('.dll', '.pyd')) else b'fixture')
                report = self.check_package()
                self.assertFalse(report['technical_ok'])
                self.assertTrue(any(Path(item['component']) == Path(relative) and ('Excluded' in item['detail'] or 'outside the reviewed' in item['detail']) for item in report['errors']))
                path.unlink()

    def test_excluded_path_matching_is_windows_aware_and_boundary_exact(self):
        bad = [r'_internal\PySide6\qml\QtQuick\VirtualKeyboard\qmldir',
               r'_internal\PYSIDE6\QML\QTQUICK3D\Effects\qmldir',
               r'elsewhere\QT6QUICKTIMELINEBLENDTREES.DLL',
               r'elsewhere\qtvirtualkeyboardplugin.dll',
               r'elsewhere\QMLDBG_QUICK3DPROFILER.DLL']
        good = ['_internal/PySide6/qml/QtQuick/qmldir',
                '_internal/PySide6/qml/QtQuick/Controls/Basic/Dialog.qml',
                '_internal/PySide6/qml/QtQuick/Shapes/qmldir',
                '_internal/PySide6/qml/QtQuick/VirtualKeyboardNotes/readme.txt',
                'licenses/Qt6Charts.dll.LICENSE.txt',
                '_internal/PySide6/Qt6QuickShapes.dll',
                '_internal/PySide6/plugins/platforms/qwindows.dll',
                '_internal/PySide6/plugins/platforminputcontexts/nativeime.dll',
                '_internal/PySide6/plugins/qmltooling/qmldbg_quickprofiler.dll',
                '_internal/PySide6/plugins/qmltooling/qmldbg_debugger.dll']
        for relative in bad:
            self.assertIsNotNone(audit.excluded_qt_reason(relative, self.spec), relative)
        for relative in good:
            self.assertIsNone(audit.excluded_qt_reason(relative, self.spec), relative)

    def test_empty_model_directory_no_longer_passes(self):
        for path in (self.source / 'resources/models').iterdir():
            path.unlink()
        with self.assertRaisesRegex(RuntimeError, 'ch_PP-OCRv4_det_infer.onnx'):
            audit.require_source_resources(self.source)

    def test_empty_traineddata_is_rejected(self):
        put(self.source, 'resources/tesseract/tessdata/jpn_vert.traineddata', b'')
        self.assertFalse(audit.source_audit(self.source)['technical_ok'])

    def test_directory_named_ffmpeg_is_rejected(self):
        path = self.source / 'resources/ffmpeg.exe'
        path.unlink()
        path.mkdir()
        self.assertFalse(audit.source_audit(self.source)['technical_ok'])

    def test_wrong_architecture_or_text_executable_is_rejected(self):
        for data in (b'not executable', fake_pe(0x14c)):
            with self.subTest(data=data[:4]):
                put(self.source, 'resources/ffmpeg.exe', data)
                self.assertFalse(audit.source_audit(self.source)['technical_ok'])

    def test_traversal_cannot_satisfy_artifact(self):
        put(self.base, 'outside.txt')
        with self.assertRaisesRegex(ValueError, 'escapes'):
            audit.safe_file(self.source, '../outside.txt')

    def test_complete_synthetic_package_has_inventory_not_release_approval(self):
        self.make_package()
        report = self.check_package()
        self.assertTrue(report['technical_ok'], report['errors'])
        self.assertGreater(len(report['files']), 30)
        self.assertEqual(len(report['qml_module_inventory']), len(self.spec['qml_modules']) + 1)
        self.assertFalse(report['distribution_license_review_complete'])
        self.assertFalse(report['windows_acceptance_complete'])
        self.assertTrue(any('not run' in warning for warning in report['warnings']))

    def test_missing_each_native_family_fails_even_with_rest_present(self):
        self.make_package()
        for component, patterns in self.spec['native_artifacts'].items():
            with self.subTest(component=component):
                path = self.package / patterns[0].replace('*', 'fixture')
                path.unlink()
                report = self.check_package()
                self.assertFalse(report['technical_ok'])
                self.assertTrue(any(error['component'] == component for error in report['errors']))
                path.write_bytes(fake_pe())

    def test_resource_hash_change_and_stale_notice_are_detected(self):
        self.make_package()
        for relative in ('_internal/resources/models/japan_PP-OCRv4_rec_infer.onnx', 'licenses/FFmpeg-README.txt'):
            with self.subTest(relative=relative):
                path = self.package / relative
                original = path.read_bytes()
                path.write_bytes(b'changed')
                self.assertTrue(any(e['component'] == relative for e in self.check_package()['errors']))
                path.write_bytes(original)

    def test_empty_wheel_requested_marker_is_valid_but_hashed(self):
        self.make_package()
        relative = '_internal/regex-1.0.dist-info/REQUESTED'
        path = put(self.package, relative, b'')
        self.prebuild['packages'][0]['metadata_files'].append({**audit.record(path, relative), 'destination': relative})
        self.assertTrue(self.check_package()['technical_ok'])
        path.write_bytes(b'tampered')
        self.assertFalse(self.check_package()['technical_ok'])

    def test_missing_wheel_metadata_fails(self):
        self.make_package()
        (self.package / '_internal/regex-1.0.dist-info/METADATA').unlink()
        self.assertFalse(self.check_package()['technical_ok'])

    def test_missing_qml_plugin_or_dictionary_fails(self):
        self.make_package()
        for relative in ('_internal/PySide6/qml/QtQuick/Controls/Basic/fixtureplugin.dll', '_internal/rapidocr/models/ppocr_keys_v1.txt'):
            with self.subTest(relative=relative):
                path = self.package / relative
                original = path.read_bytes()
                path.unlink()
                self.assertFalse(self.check_package()['technical_ok'])
                path.write_bytes(original)

    def test_packaged_manifest_must_match_actual_build_contract(self):
        self.make_package()
        put(self.package, 'portable_runtime_manifest.json', b'{}')
        self.assertTrue(any(e['component'] == 'portable_runtime_manifest.json' for e in self.check_package()['errors']))

    def test_source_only_failed_or_changed_contract_evidence_rejected(self):
        self.make_package()
        for field, value in (('phase', 'source'), ('technical_ok', False), ('contract_sha256', 'old')):
            with self.subTest(field=field):
                original = self.prebuild[field]
                self.prebuild[field] = value
                self.assertFalse(self.check_package()['technical_ok'])
                self.prebuild[field] = original
        self.prebuild['packages'] = []
        self.assertFalse(self.check_package()['technical_ok'])

    def test_postbuild_compares_native_libraries_to_prebuild(self):
        self.make_package()
        relative = '_internal/av.libs/avcodecfixture.dll'
        path = self.package / relative
        self.prebuild['packages'][0]['runtime_files'] = [{**audit.record(path, relative), 'destination': relative}]
        changed = bytearray(path.read_bytes())
        changed[-1] = 1
        path.write_bytes(changed)
        self.assertTrue(any(error['component'] == relative for error in self.check_package()['errors']))

    def test_cli_reports_precise_missing_resources_and_nonzero_exit(self):
        (self.source / 'resources/tesseract/tessdata/jpn.traineddata').unlink()
        report = self.base / 'result.json'
        process = subprocess.run([sys.executable, str(ROOT / 'tools/audit_portable_runtime.py'), 'source', '--root', str(self.source), '--report', str(report)], capture_output=True, text=True)
        self.assertEqual(process.returncode, 1)
        self.assertIn('jpn.traineddata', process.stderr)
        self.assertFalse(json.loads(report.read_text(encoding='utf-8'))['technical_ok'])

    def test_cli_static_postbuild_checks_actual_synthetic_tree(self):
        self.make_package()
        evidence = put(self.base, 'prebuild.json', json.dumps(self.prebuild).encode())
        report = self.base / 'postbuild.json'
        process = subprocess.run([sys.executable, str(ROOT / 'tools/audit_portable_runtime.py'), 'postbuild', '--root', str(self.package), '--prebuild-report', str(evidence), '--report', str(report)], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(report.read_text(encoding='utf-8'))
        self.assertTrue(result['technical_ok'])
        self.assertEqual(result['runtime_checks'], {})
        self.assertFalse(result['windows_acceptance_complete'])

    def test_windows_binary_execution_is_rejected_on_other_platforms(self):
        self.make_package()
        with patch.object(audit.sys, 'platform', 'linux'):
            report = audit.postbuild_audit(self.package, self.prebuild, run_binaries=True)
        self.assertFalse(report['technical_ok'])
        self.assertTrue(any(error['component'] == 'runtime_platform' for error in report['errors']))
        self.assertEqual(report['runtime_checks'], {})

    def test_cli_malformed_evidence_fails_closed_with_report(self):
        evidence = put(self.base, 'invalid.json', b'{')
        report = self.base / 'result.json'
        process = subprocess.run([sys.executable, str(ROOT / 'tools/audit_portable_runtime.py'), 'postbuild', '--root', str(self.source), '--prebuild-report', str(evidence), '--report', str(report)], capture_output=True, text=True)
        self.assertEqual(process.returncode, 1)
        self.assertEqual(json.loads(report.read_text(encoding='utf-8'))['errors'][0]['component'], 'audit_error')

    def test_runtime_checks_use_exact_paths_and_load_all_languages(self):
        report = audit.new_report('test')
        calls = []
        def runner(command, **kwargs):
            calls.append((command, kwargs))
            output = b' ... subtitles V->V libass\n' if '-filters' in command else b'ffmpeg version test-build\n'
            return SimpleNamespace(returncode=0, stdout=output)
        audit.runtime_binary_checks(self.source / 'resources', report, runner, self.source / 'licenses')
        self.assertEqual(report['errors'], [])
        self.assertEqual(len(calls), 7)
        self.assertEqual({call[0][call[0].index('-l') + 1] for call in calls if '-l' in call[0]}, {'eng', 'jpn', 'jpn_vert', 'chi_sim'})
        for command, kwargs in calls:
            self.assertTrue(Path(command[0]).is_absolute())
            self.assertFalse(kwargs.get('shell', False))
            if 'tesseract' in Path(command[0]).name:
                self.assertEqual(kwargs['env']['TESSDATA_PREFIX'], '.')
                self.assertEqual(Path(kwargs['cwd']),self.source/'resources/tesseract/tessdata')
                if '--tessdata-dir' in command:self.assertEqual(command[command.index('--tessdata-dir')+1],'.')
            else:self.assertEqual(kwargs['env']['TESSDATA_PREFIX'],str(self.source/'resources/tesseract/tessdata'))

    def test_runtime_check_failure_and_mismatched_ffmpeg_notice_are_errors(self):
        report = audit.new_report('test')
        def runner(command, **kwargs):
            return SimpleNamespace(returncode=0, stdout=b'ffmpeg version different-build\n')
        audit.runtime_binary_checks(self.source / 'resources', report, runner, self.source / 'licenses')
        self.assertTrue(any(e['component'] == 'FFmpeg-README.txt' for e in report['errors']))
        self.assertTrue(any(e['component'] == 'ffmpeg_subtitles' for e in report['errors']))

    def test_spec_and_build_use_the_same_failclosed_contract(self):
        spec = (ROOT / 'LyriaQt.spec').read_text(encoding='utf-8')
        build = (ROOT / 'build_windows_qt.ps1').read_text(encoding='utf-8')
        self.assertIn('require_source_resources(root)', spec)
        self.assertIn('collect_distribution_data(root)', spec)
        self.assertIn("hookspath=[str(root / 'tools' / 'pyinstaller_hooks')]", spec)
        self.assertIn("excludes=['models/*.onnx']", spec)
        self.assertIn("av_libraries = Path(av_base) / 'av.libs'", spec)
        self.assertIn('audit_portable_runtime.py prebuild', build)
        self.assertIn('audit_portable_runtime.py postbuild', build)
        self.assertIn('--run-binaries', build)
        self.assertIn('Remove-Item -LiteralPath $stale', build)


class QtCollectionHookTests(unittest.TestCase):
    """Execute our hook code against fake helpers, never PyInstaller or Qt.

    These synthetic trees intentionally make parent data directories recurse
    into unwanted child modules. Every .dll is inert text and is never loaded.
    """
    hook_root = ROOT / 'tools/pyinstaller_hooks'

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'qml'
        self.fail_plugin = False
        self.fail_datas = False
        self.extra_binary = False
        self.escape_plugin = False
        for module in qml_policy.ALLOWED_MODULE_DIRS:
            put(self.root, module + '/qmldir', ('module ' + module.replace('/', '.') + '\nplugin fixtureplugin\n').encode())
            put(self.root, module + '/fixtureplugin.dll', b'inert plugin fixture')
            put(self.root, module + '/plugins.qmltypes')
        for name in ('TextArea', 'Dialog', 'Menu', 'Popup', 'SelectionRectangle'):
            put(self.root, 'QtQuick/Controls/Basic/' + name + '.qml', b'import QtQuick.Shapes\n')
        for module in audit.manifest()['excluded_qt_qml_roots'] + ['QtQuick/Controls/Material']:
            put(self.root, module + '/qmldir', b'module excluded\nplugin excludedplugin\n')
            put(self.root, module + '/excludedplugin.dll', b'inert excluded fixture')
            put(self.root, module + '/nested/Excluded.qml')
        put(self.root, 'QtQuick/designer/editor.metainfo')
        self.modules = {}
        for name in ('PyInstaller', 'PyInstaller.utils', 'PyInstaller.utils.hooks', 'PyInstaller.utils.hooks.qt'):
            self.modules[name] = ModuleType(name)
            self.modules[name].__path__ = []
        self.qt = self.modules['PyInstaller.utils.hooks.qt']
        self.qt.add_qt6_dependencies = lambda _: (['baseline-hidden'], [('baseline.dll', 'PySide6')], [('baseline.txt', 'PySide6')])
        self.qt.pyside6_library_info = SimpleNamespace(
            location={'QmlImportsPath': str(self.root)}, qt_rel_dir='PySide6',
            _process_qml_plugin=self.process_qml_plugin)

    def process_qml_plugin(self, qmldir):
        names = [match[1] + '.dll' for line in qmldir.read_text(encoding='utf-8').splitlines()
                 if (match := re.fullmatch(r'(?:optional\s+)?plugin\s+(\w+)', line.strip()))]
        binaries = [qmldir.parent / name for name in names]
        datas = [path for path in qmldir.parent.iterdir() if path.suffix != '.dll']
        if self.fail_plugin and qmldir.parent.name == 'Basic':
            binaries = []
        if self.fail_datas and qmldir.parent.name == 'Basic':
            return [], []
        if self.extra_binary and qmldir.parent == self.root / 'QtQuick':
            datas.append(put(self.root, 'QtQuick/unexpected.dll', b'inert unexpected fixture'))
        if self.escape_plugin and qmldir.parent == self.root / 'QtQuick':
            binaries.append(self.root / 'QtQuick/VirtualKeyboard/excludedplugin.dll')
        return binaries, datas

    def execute(self, name='hook-PySide6.QtQml.py', versions=None):
        versions = versions or {'PyInstaller': '6.16.0', 'PySide6': '6.8.3'}
        with patch.dict(sys.modules, self.modules), patch.object(importlib.metadata, 'version', side_effect=versions.__getitem__):
            return runpy.run_path(str(self.hook_root / name))

    def test_real_source_imports_style_pins_and_manifest_match_reviewed_policy(self):
        self.assertEqual(set(audit.manifest()['qml_modules']), qml_policy.ALLOWED_MODULE_DIRS)
        self.assertEqual(len(qml_policy.ALLOWED_MODULE_DIRS), 13)
        self.assertIn('QtQuick/Shapes', qml_policy.ALLOWED_MODULE_DIRS)
        for path in (ROOT / 'desktop_qt/qml').rglob('*.qml'):
            for module in re.findall(r'^\s*import\s+(Qt[A-Za-z0-9_.]+)', path.read_text(encoding='utf-8'), re.M):
                self.assertIn(module.replace('.', '/'), qml_policy.ALLOWED_MODULE_DIRS)
        self.assertIn("QQuickStyle.setStyle('Basic')", (ROOT / 'app_qt.py').read_text(encoding='utf-8'))
        pins = audit.pins_from_file(ROOT / 'requirements.qt.txt')
        self.assertEqual(pins['pyside6'], '6.8.3')
        self.assertEqual(pins['pyinstaller'], '6.16.0')

    def test_parent_selection_keeps_nearest_module_boundaries(self):
        module_dirs = set(qml_policy.ALLOWED_MODULE_DIRS) | {'QtQuick/VirtualKeyboard', 'QtQuick/Controls/Material'}
        selected = qml_policy.selected_files([
            'QtQuick/qmldir', 'QtQuick/Controls/Basic/Dialog.qml',
            'QtQuick/Controls/Material/Slider.qml', 'QtQuick/VirtualKeyboard/qmldir',
            'QtQuick/designer/editor.metainfo'], module_dirs)
        self.assertEqual(selected, ['QtQuick/Controls/Basic/Dialog.qml', 'QtQuick/qmldir'])
        self.assertEqual(qml_policy.owning_module('QtQuick/Controls/Basic/impl/qmldir', module_dirs), 'QtQuick/Controls/Basic/impl')

    def test_windows_relative_paths_work_but_drive_unc_and_traversal_fail(self):
        self.assertEqual(qml_policy.owning_module(r'QtQuick\Controls\Basic\Dialog.qml', qml_policy.ALLOWED_MODULE_DIRS), 'QtQuick/Controls/Basic')
        for value in ('../QtQuick/qmldir', r'QtQuick\..\qmldir', '/QtQuick/qmldir',
                      r'C:\QtQuick\qmldir', r'C:QtQuick\qmldir', r'\\server\share\qmldir'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                qml_policy.owning_module(value, qml_policy.ALLOWED_MODULE_DIRS)

    def test_hook_expands_data_files_without_reintroducing_excluded_children(self):
        result = self.execute()
        entries = result['binaries'] + result['datas']
        outputs = [Path(source) for source, _ in entries if source not in ('baseline.dll', 'baseline.txt')]
        self.assertTrue(all(path.is_file() for path in outputs))
        self.assertEqual(len(outputs), len(set(outputs)))
        self.assertFalse(any('designer' in path.parts or 'VirtualKeyboard' in path.parts or 'Material' in path.parts for path in outputs))
        for source, destination in entries:
            if source in ('baseline.dll', 'baseline.txt'):
                continue
            relative = Path(source).relative_to(self.root)
            self.assertEqual(Path(destination), Path('PySide6/qml') / relative.parent)
            self.assertIsNone(audit.excluded_qt_reason(Path('_internal') / destination / relative.name, audit.manifest()))
        for name in ('TextArea', 'Dialog', 'Menu', 'Popup', 'SelectionRectangle'):
            self.assertIn(self.root / ('QtQuick/Controls/Basic/' + name + '.qml'), outputs)
        self.assertIn(self.root / 'QtQuick/Shapes/fixtureplugin.dll', outputs)
        self.assertIn(self.root / 'QtMultimedia/fixtureplugin.dll', outputs)
        self.assertIn('baseline-hidden', result['hiddenimports'])

    def test_missing_required_module_is_rejected(self):
        (self.root / 'QtQuick/Controls/Basic/qmldir').unlink()
        with self.assertRaisesRegex(RuntimeError, 'Missing required QML.*Basic'):
            self.execute()

    def test_missing_required_plugin_or_rejected_dependency_is_rejected(self):
        for attribute in ('fail_plugin', 'fail_datas'):
            with self.subTest(attribute=attribute):
                setattr(self, attribute, True)
                with self.assertRaisesRegex(RuntimeError, 'Required QML plugin'):
                    self.execute()
                setattr(self, attribute, False)

    def test_nonexistent_plugin_returned_by_helper_is_rejected(self):
        (self.root / 'QtQuick/Controls/Basic/fixtureplugin.dll').unlink()
        with self.assertRaisesRegex(RuntimeError, 'plugin file is missing'):
            self.execute()

    def test_absent_plugin_declaration_is_rejected(self):
        put(self.root, 'QtQuick/Controls/Basic/qmldir', b'module QtQuick.Controls.Basic\n')
        with self.assertRaisesRegex(RuntimeError, 'Required QML plugin'):
            self.execute()

    def test_missing_qml_location_does_not_fall_back_to_current_directory(self):
        for location in ({}, {'QmlImportsPath': ''}, {'QmlImportsPath': str(self.root / 'absent')}):
            with self.subTest(location=location):
                self.qt.pyside6_library_info.location = location
                with self.assertRaisesRegex(RuntimeError, 'QML root is missing'):
                    self.execute()

    def test_qml2_import_location_remains_supported(self):
        self.qt.pyside6_library_info.location = {'Qml2ImportsPath': str(self.root)}
        self.assertTrue(self.execute()['binaries'])

    def test_executable_data_and_wrong_module_plugin_are_rejected(self):
        for attribute, message in (('extra_binary', 'Unexpected executable data'), ('escape_plugin', 'escaped its module')):
            with self.subTest(attribute=attribute):
                setattr(self, attribute, True)
                with self.assertRaisesRegex(RuntimeError, message):
                    self.execute()
                setattr(self, attribute, False)

    def test_both_hooks_reject_changed_pyside_or_pyinstaller_version(self):
        for hook in ('hook-PySide6.QtQml.py', 'hook-PySide6.QtGui.py'):
            for name, value in (('PySide6', '6.8.2'), ('PyInstaller', '6.17.0')):
                with self.subTest(hook=hook, package=name):
                    versions = {'PyInstaller': '6.16.0', 'PySide6': '6.8.3', name: value}
                    with self.assertRaisesRegex(RuntimeError, 'versions'):
                        self.execute(hook, versions)

    def test_qtgui_keeps_platform_native_input_svg_and_other_plugins(self):
        entries = [(r'C:\qt\plugins\platforminputcontexts\qtvirtualkeyboardplugin.dll', 'plugins'),
                   ('C:/Qt/plugins/platforminputcontexts/QTVIRTUALKEYBOARDPLUGIN.DLL', 'plugins'),
                   (r'C:\qt\plugins\platforminputcontexts\nativeinputplugin.dll', 'plugins'),
                   (r'C:\qt\plugins\platforms\qwindows.dll', 'plugins'),
                   (r'C:\qt\plugins\imageformats\qsvg.dll', 'plugins'),
                   (r'C:\qt\plugins\other\qtvirtualkeyboardplugin.dll', 'plugins')]
        self.qt.add_qt6_dependencies = lambda _: (['keep-hidden'], entries[:], [('keep-data', 'data')])
        result = self.execute('hook-PySide6.QtGui.py')
        self.assertEqual(result['binaries'], entries[2:])
        self.assertEqual(result['hiddenimports'], ['keep-hidden'])
        self.assertEqual(result['datas'], [('keep-data', 'data')])

    def test_qtqml_filters_only_quick3d_profiler_and_keeps_other_tooling(self):
        # Exact names from the pinned Windows wheel; keep all 11 other plugins.
        kept = ['debugger', 'inspector', 'local', 'messages', 'native',
                'nativedebugger', 'preview', 'profiler', 'quickprofiler', 'server', 'tcp']
        entries = [(r'C:\qt\plugins\qmltooling\qmldbg_quick3dprofiler.dll', 'plugins/qmltooling'),
                   ('C:/QT/PLUGINS/QMLTOOLING/QMLDBG_QUICK3DPROFILER.DLL', 'plugins/qmltooling')]
        entries += [('C:/qt/plugins/qmltooling/qmldbg_' + name + '.dll', 'plugins/qmltooling') for name in kept]
        entries += [('C:/qt/plugins/other/qmldbg_quick3dprofiler.dll', 'plugins/other'),
                    ('C:/qt/plugins/qmltooling/qmldbg_quick3dprofiler.dll.backup', 'plugins/qmltooling')]
        self.qt.add_qt6_dependencies = lambda _: (['keep-hidden'], entries[:], [('keep-data', 'data')])
        result = self.execute()
        self.assertEqual(result['binaries'][:len(entries) - 2], entries[2:])
        self.assertFalse(set(entries[:2]) & set(result['binaries']))
        self.assertEqual(result['hiddenimports'], ['keep-hidden'])
        self.assertIn(('keep-data', 'data'), result['datas'])
        # An alternate destination is retained by the narrow collection filter,
        # but is still rejected by the final artifact gate instead of hidden.
        self.assertIsNotNone(audit.excluded_qt_reason('elsewhere/qmldbg_quick3dprofiler.dll', audit.manifest()))

    def test_qmltooling_dependency_chain_drops_quick3d_ingress_before_collection(self):
        dependency_map = {
            'qmldbg_quick3dprofiler.dll': ['Qt6Qml.dll', 'Qt6Quick3DUtils.dll', 'Qt6Core.dll'],
            'qmldbg_quickprofiler.dll': ['Qt6Quick.dll', 'Qt6Qml.dll', 'Qt6Core.dll'],
            'qmldbg_debugger.dll': ['Qt6Qml.dll', 'Qt6Core.dll'],
        }
        entries = [('C:/qt/plugins/qmltooling/' + name, 'PySide6/plugins/qmltooling') for name in dependency_map]
        self.qt.add_qt6_dependencies = lambda _: ([], entries[:], [])
        self.assertIn('Qt6Quick3DUtils.dll', {name for deps in dependency_map.values() for name in deps})
        result = self.execute()
        retained = {Path(source).name for source, _ in result['binaries'] if '/qmltooling/' in source}
        self.assertEqual(retained, {'qmldbg_quickprofiler.dll', 'qmldbg_debugger.dll'})
        closure = {name for plugin in retained for name in dependency_map[plugin]}
        self.assertNotIn('Qt6Quick3DUtils.dll', closure)
        self.assertTrue({'Qt6Qml.dll', 'Qt6Quick.dll', 'Qt6Core.dll'} <= closure)


class DependencyInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        put(self.root, 'requirements.qt.txt', b'-r backend.txt\nregex==1.0\n')
        put(self.root, 'backend.txt', b'av==2.0\n')
        self.spec = {**audit.manifest(), 'runtime_distributions': ['av', 'regex']}
        self.patch = patch.object(audit, 'manifest', return_value=self.spec)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.distributions = {}
        for name, version in (('av', '2.0'), ('regex', '1.0')):
            meta = Path(f'{name}-{version}.dist-info/METADATA')
            notice = Path(f'{name}-{version}.dist-info/licenses/LICENSE.txt')
            put(self.root, meta, f'Name: {name}\nVersion: {version}\n'.encode())
            put(self.root, notice, b'Unmodified synthetic upstream notice')
            self.distributions[name] = SimpleNamespace(metadata={'Name': name, 'License': 'test declaration'}, version=version, requires=[], files=[meta, notice], locate_file=lambda p: self.root / p)

    def get_distribution(self, name):
        try:
            return self.distributions[audit.normalize(name)]
        except KeyError:
            raise audit.metadata.PackageNotFoundError(name)

    def inventory(self):
        report = audit.new_report('prebuild')
        audit.dependency_inventory(self.root, report, self.get_distribution)
        return report

    def test_pins_and_exact_installed_notice_inventory(self):
        report = self.inventory()
        self.assertEqual(report['errors'], [])
        self.assertEqual(len(report['packages']), 2)
        self.assertTrue(report['packages'][0]['notice_files'][0]['sha256'])
        self.assertFalse(report['distribution_license_review_complete'])

    def test_stale_installed_pin_cannot_pass(self):
        self.distributions['regex'].version = '0.9'
        self.assertTrue(any(e['component'] == 'regex' for e in self.inventory()['errors']))

    def test_missing_transitive_dependency_and_cpu_overlap_fail(self):
        self.distributions['av'].requires = ['missing-lib>=1']
        self.distributions['onnxruntime'] = self.distributions['av']
        components = {error['component'] for error in self.inventory()['errors']}
        self.assertIn('missing-lib', components)
        self.assertIn('onnxruntime', components)

    def test_missing_notices_remain_explicit_unresolved_release_review(self):
        self.distributions['regex'].files = self.distributions['regex'].files[:1]
        report = self.inventory()
        self.assertEqual(report['errors'], [])
        self.assertTrue(any('regex 1.0: no license/notice' in text for text in report['warnings']))
        self.assertFalse(report['distribution_license_review_complete'])

    def test_missing_recorded_notice_is_error_not_silently_skipped(self):
        (self.root / self.distributions['regex'].files[1]).unlink()
        self.assertTrue(any('missing file' in e['detail'] for e in self.inventory()['errors']))

    def test_recursive_conflicting_and_unpinned_requirements_rejected(self):
        for content in (b'-r requirements.qt.txt\n', b'regex==2.0\n', b'av>=1\n'):
            with self.subTest(content=content):
                put(self.root, 'backend.txt', content)
                with self.assertRaises(ValueError):
                    audit.pins_from_file(self.root / 'requirements.qt.txt')


if __name__ == '__main__':
    unittest.main()


class DistributionPrivacyContractTests(unittest.TestCase):
    def test_history_and_version_trees_are_not_required_distribution_inputs(self):
        contract=audit.manifest()
        self.assertEqual(set(contract['external_trees']),{'examples','licenses'})
        for name in contract['external_files']:
            self.assertNotIn('evidence',name.lower())
            self.assertNotIn('/requirements/',name)
            self.assertFalse(name.startswith('docs/v0.21/'))
        self.assertIn('docs/v0.22/DISTRIBUTION_STATUS.md',contract['external_files'])
        self.assertTrue(MODEL_WORKERS <= set(contract['internal_files']))
        self.assertTrue(MODEL_DOCS <= set(contract['external_files']))
        self.assertIn('resources/ffmpeg.exe',contract['required_resources'])

    def test_private_history_is_neither_required_nor_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            # Inert synthetic sentinel: never read a user's actual private file.
            private=root/'docs/dev7-alignment-evidence/latest-user-input-inventory.json'
            private.parent.mkdir(parents=True)
            private.write_text('{"private_sentinel":true}',encoding='utf8')
            report=audit.source_audit(root)
            self.assertFalse(any('dev7-alignment-evidence' in item['path'] for item in report['files']))
            self.assertFalse(any('dev7-alignment-evidence' in item['component'] for item in report['errors']))
            self.assertTrue(any('resources/ffmpeg.exe' in item['component'] for item in report['errors']))
            self.assertTrue(private.exists())
