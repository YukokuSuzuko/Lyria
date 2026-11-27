"""Frozen Windows software/offscreen regression, never native desktop acceptance.

Creates a new evidence directory, executes the backend, every Qt test, separately
collected pytest functions, actual QML scenarios and optional real local OCR.
No network service is called and the application uses isolated recovery storage.
"""
from __future__ import annotations
import argparse
import ast
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lyricsync import __version__
from tools.validate_qt_cloud import source_inventory


def _configure_process_error_mode():
    """Suppress Windows crash dialogs only in this runner and inherited children."""
    if sys.platform == 'win32':
        import ctypes
        # SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX.
        # This is process-local; it does not change the machine's WER settings.
        kernel = ctypes.windll.kernel32
        kernel.SetErrorMode(kernel.GetErrorMode() | 0x0001 | 0x0002 | 0x8000)


def _run_recorded_process(command, stream, env, record, publish, timeout):
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stream,
                               stderr=subprocess.STDOUT, creationflags=flags)
    record.update(pid=process.pid, started_utc=datetime.now(timezone.utc).isoformat())
    publish()
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Do not kill a Python/Qt process during native decoder cleanup. Keep its
        # exact PID visible and stop scheduling other work while it finishes.
        record.update(status='overdue_waiting_for_exit', timeout_seconds=timeout)
        publish()
        print(f'Waiting for {record["name"]} PID {process.pid} to exit safely', flush=True)
        code = process.wait()
    record.update(exit_code=code, finished_utc=datetime.now(timezone.utc).isoformat(),
                  status='passed' if code == 0 else 'failed')
    record['abnormal_exit'] = code < 0 or code >= 0x80000000
    publish()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--with-real-ocr', action='store_true')
    args = parser.parse_args(argv)
    _configure_process_error_mode()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    before = source_inventory()
    (out/'source-before.json').write_text(json.dumps(before, indent=2), encoding='utf8')
    env = os.environ.copy()
    env.update(PYTHONUTF8='1', QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software')
    if sys.platform == 'win32': env['QT_QPA_FONTDIR'] = str(Path(os.environ.get('WINDIR',r'C:\Windows'))/'Fonts')
    evidence = {'LYRIA_QML_EVIDENCE':'qml', 'LYRIA_TIMELINE_EVIDENCE':'timeline',
                'LYRIA_TOOL_VISUALS_EVIDENCE':'tool-visuals', 'LYRIA_HAND_PAN_EVIDENCE':'hand-pan',
                'LYRIA_MOTION_EVIDENCE':'motion', 'LYRIA_REVIEW_MOTION_EVIDENCE':'review-motion',
                'LYRIA_TIMELINE_FOLD_EVIDENCE':'timeline-fold', 'LYRIA_PREVIEW_EVIDENCE':'preview',
                'LYRIA_CHROME_EVIDENCE':'chrome', 'LYRIA_RAZOR_EVIDENCE':'razor',
                'LYRIA_EXPLICIT_EDIT_EVIDENCE':'explicit-editing', 'LYRIA_DIALOG_EVIDENCE':'dialogs',
                'LYRIA_013_PREVIEW_EVIDENCE':'preview-013', 'LYRIA_013_TIMELINE_EVIDENCE':'timeline-013',
                'LYRIA_PHASE4_UI_EVIDENCE':'phase4-ui', 'LYRIA_SPECTRUM_EVIDENCE':'phase4-spectrum'}
    env.update({key:str(out/value) for key,value in evidence.items()})
    stages=[]
    def run(name, arguments, timeout=900, records=None):
        start=time.monotonic()
        entry=['tools/pytest_process.py',*arguments[2:]] if list(arguments[:2])==['-m','pytest'] else arguments
        command=[sys.executable,*map(str,entry)]
        record={'name':name,'command':command,'status':'running'}
        (stages if records is None else records).append(record)
        def publish():
            (out/'running-results.json').write_text(json.dumps(stages,ensure_ascii=False,indent=2),encoding='utf8')
        with (out/(name+'.log')).open('wb') as stream:
            _run_recorded_process(command, stream, env, record, publish, timeout)
        record['elapsed_seconds']=round(time.monotonic()-start,3)
        xml=out/(name+'.xml')
        if xml.exists():
            document=ET.parse(xml).getroot()
            suites=[document] if document.tag=='testsuite' else list(document.findall('testsuite'))
            record['counts']={key:sum(int(s.get(key,'0')) for s in suites) for key in ('tests','failures','errors','skipped')}
            if not record['counts']['tests']:record['status']='failed_no_tests'
        publish()
        print(name+': '+record['status'],flush=True)
        if record.get('abnormal_exit'):
            raise SystemExit('Abnormal process exit; stopped without retry. See running-results.json for PID and timestamps.')
        return record
    run('compile',['-m','compileall','-q','lyricsync','desktop_qt','tests','tools','app_qt.py'])
    backend=run('backend',['tools/run_headless_tests.py','--json-report',out/'backend.json'])
    if (out/'backend.json').exists():
        data=json.loads((out/'backend.json').read_text(encoding='utf8'))
        backend['counts']={key:data[key] for key in ('discovered','desktop_excluded','run','passed')}
        backend['counts'].update({key:len(data[key]) for key in ('skipped','failures','errors')})
    qt=sorted(ROOT.glob('tests/test_qt_*.py'))
    standalone=[p for p in sorted(ROOT.glob('tests/test_*.py')) if p not in qt and
                any(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name.startswith('test_')
                    for n in ast.parse(p.read_text(encoding='utf8')).body)]
    (out/'pytest-inventory.json').write_text(json.dumps({'qt':[p.name for p in qt],
                   'standalone':[p.name for p in standalone]},indent=2),encoding='utf8')
    # Keep failures and Qt object lifetimes local to one test module. The list of
    # collected modules is unchanged; this is isolation, not a long-session soak.
    qt_record={'name':'qt','status':'running','execution':'sequential isolated modules','modules':[]}
    stages.append(qt_record)
    qt_start=time.monotonic()
    combined=ET.Element('testsuites')
    for path in qt:
        name='qt-'+path.stem
        run(name,['-m','pytest',path,'-v','-x','-o','faulthandler_timeout=60','--basetemp='+str(out/(name+'-tmp')),'--junitxml='+str(out/(name+'.xml'))],records=qt_record['modules'])
        document=ET.parse(out/(name+'.xml')).getroot()
        combined.extend([document] if document.tag=='testsuite' else list(document.findall('testsuite')))
    ET.ElementTree(combined).write(out/'qt.xml',encoding='utf-8',xml_declaration=True)
    qt_record['counts']={key:sum(module.get('counts',{}).get(key,0) for module in qt_record['modules'])
                         for key in ('tests','failures','errors','skipped')}
    qt_record['status']='passed' if qt_record['modules'] and all(module['status']=='passed' for module in qt_record['modules']) else 'failed'
    qt_record['elapsed_seconds']=round(time.monotonic()-qt_start,3)
    (out/'running-results.json').write_text(json.dumps(stages,ensure_ascii=False,indent=2),encoding='utf8')
    print('qt: '+qt_record['status'],flush=True)
    # Some conventionally named standalone files also instantiate Qt. Isolate
    # every module, not merely files whose names begin test_qt_.
    if standalone:
        group={'name':'standalone','status':'running','execution':'sequential isolated modules','modules':[]}
        stages.append(group); started=time.monotonic(); combined=ET.Element('testsuites')
        for path in standalone:
            name='standalone-'+path.stem
            run(name,['-m','pytest',path,'-v','-x','-o','faulthandler_timeout=60',
                      '--basetemp='+str(out/(name+'-tmp')),'--junitxml='+str(out/(name+'.xml'))],records=group['modules'])
            document=ET.parse(out/(name+'.xml')).getroot()
            combined.extend([document] if document.tag=='testsuite' else list(document.findall('testsuite')))
        ET.ElementTree(combined).write(out/'standalone.xml',encoding='utf-8',xml_declaration=True)
        group['counts']={key:sum(module.get('counts',{}).get(key,0) for module in group['modules'])
                         for key in ('tests','failures','errors','skipped')}
        group['status']='passed' if all(module['status']=='passed' for module in group['modules']) else 'failed'
        group['elapsed_seconds']=round(time.monotonic()-started,3)
        (out/'running-results.json').write_text(json.dumps(stages,ensure_ascii=False,indent=2),encoding='utf8')
        print('standalone: '+group['status'],flush=True)
    run('qml-input',['desktop_qt/qml/tests/run_qml_scenarios.py'])
    for label,script in (('qml-menu','run_qml_menu_routes.py'),('qml-ime','run_qml_ime_split.py')):
        run(label,['desktop_qt/qml/tests/'+script,'--video',ROOT/'examples/synthetic_mv.mp4','--output',out/label])
    run('startup',['app_qt.py','--video',ROOT/'examples/synthetic_mv.mp4','--storage-dir',out/'storage',
                   '--exit-after','4000','--screenshot',out/'startup.png','--self-check',out/'selfcheck.json'])
    if args.with_real_ocr:run('real-local-ocr',['tests/run_pipeline.py',out/'real-local-ocr'],timeout=1800)
    after=source_inventory()
    changed=sorted(k for k in before.keys()|after.keys() if before.get(k)!=after.get(k))
    (out/'source-after.json').write_text(json.dumps(after,indent=2),encoding='utf8')
    selfcheck=json.loads((out/'selfcheck.json').read_text(encoding='utf8')) if (out/'selfcheck.json').exists() else {}
    artifact_checks={'startup_image':(out/'startup.png').is_file(),
                     'actual_frame_selfcheck':selfcheck.get('ok') is True,
                     'version':selfcheck.get('app_version')==__version__}
    if args.with_real_ocr:
        metrics_path=out/'real-local-ocr/synthetic_metrics.json'
        metrics=json.loads(metrics_path.read_text(encoding='utf8')) if metrics_path.exists() else {}
        artifact_checks['synthetic_all_detected']=bool(metrics.get('expected')) and metrics.get('detected')==metrics.get('expected')
        artifact_checks['synthetic_mapping']=metrics.get('mapping_errors')==[]
    passed=all(s['status']=='passed' for s in stages) and all(artifact_checks.values()) and not changed
    result={'created_utc':datetime.now(timezone.utc).isoformat(),'app_version':__version__,
            'platform':platform.platform(),'source':str(ROOT),'status':'passed' if passed else 'failed',
            'scope':'Windows software/offscreen; not visible desktop, GPU, audio, real IME or online API acceptance',
            'source_unchanged':not changed,'changed_during_run':changed,
            'artifact_checks':artifact_checks,'stages':stages}
    (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print('Evidence: '+str(out),flush=True)
    return 0 if passed else 1

if __name__=='__main__':raise SystemExit(main())