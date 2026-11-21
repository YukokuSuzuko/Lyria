"""Layered Linux/offscreen Qt acceptance evidence, never Windows qualification.

Run from app/: python tools/validate_qt_cloud.py [--with-real-ocr]
Each run uses a fresh evidence directory, so an old successful QML summary can
never mask a new harness failure. Source changes during validation are reported
as an inconclusive snapshot rather than a final verified build.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def source_inventory():
    selected = set(ROOT.glob('*.py')) | set(ROOT.glob('requirements*.txt'))
    selected.update(ROOT.glob('*.spec'))
    selected.update(ROOT.glob('*.ps1'))
    for folder in ('lyricsync', 'desktop_qt', 'tests', 'tools'):
        selected.update(p for p in (ROOT/folder).rglob('*')
                        if p.is_file() and '__pycache__' not in p.parts
                        and (p.name=='qmldir' or p.suffix in ('.py', '.qml', '.js', '.svg', '.json')))
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(selected)}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-root',type=Path,default=ROOT.parent/'validation'/'integrated')
    parser.add_argument('--with-real-ocr',action='store_true',help='Run authorized local ONNX synthetic fixture; no API')
    parser.add_argument('--qc-report',type=Path,default=ROOT.parent/'reports'/'qc_recheck_e2e'/'report.json',
                        help='Existing actual local synthetic QC report for QML inspection; never invent results')
    args=parser.parse_args(argv)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    out=args.evidence_root.resolve()/stamp
    out.mkdir(parents=True,exist_ok=False)
    before=source_inventory()
    (out/'source-before.json').write_text(json.dumps(before,indent=2),encoding='utf8')
    env=os.environ.copy()
    env.update(QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',
               XDG_CACHE_HOME=str(out/'cache'),LYRIA_QML_EVIDENCE=str(out/'qml'),
               LYRIA_TIMELINE_EVIDENCE=str(out/'qml-timeline'),
               LYRIA_TOOL_VISUALS_EVIDENCE=str(out/'qml-tool-visuals'),
               LYRIA_HAND_PAN_EVIDENCE=str(out/'qml-hand-pan'),
               LYRIA_MOTION_EVIDENCE=str(out/'qml-motion'),
               LYRIA_REVIEW_MOTION_EVIDENCE=str(out/'qml-review-motion'),
               LYRIA_TIMELINE_FOLD_EVIDENCE=str(out/'qml-timeline-fold'),
               LYRIA_PREVIEW_EVIDENCE=str(out/'qml-preview'),
               LYRIA_CHROME_EVIDENCE=str(out/'qml-window-chrome'),
               LYRIA_RAZOR_EVIDENCE=str(out/'qml-razor'),
               LYRIA_EXPLICIT_EDIT_EVIDENCE=str(out/'qml-explicit-editing'),
               LYRIA_DIALOG_EVIDENCE=str(out/'native-dialogs'))
    results=[]

    def execute(name,arguments,timeout=300):
        started=time.monotonic()
        record={'name':name,'command':[sys.executable,*map(str,arguments)],
                'log':str(out/(name+'.log')),'status':'running'}
        with (out/(name+'.log')).open('wb') as stream:
            try:
                completed=subprocess.run(record['command'],cwd=ROOT,env=env,stdout=stream,
                    stderr=subprocess.STDOUT,timeout=timeout,check=False)
                record.update(exit_code=completed.returncode,
                              status='passed' if completed.returncode==0 else 'failed')
            except subprocess.TimeoutExpired:
                record.update(status='timeout',timeout_seconds=timeout)
        record['elapsed_seconds']=round(time.monotonic()-started,3)
        results.append(record)
        print(name+': '+record['status'],flush=True)
        return record

    execute('compile',['-m','compileall','-q','lyricsync','desktop_qt','app_qt.py'])
    backend=execute('backend',['tools/run_headless_tests.py','--json-report',out/'backend.json'])
    if (out/'backend.json').is_file():
        data=json.loads((out/'backend.json').read_text())
        backend['counts']={key:data[key] for key in ('discovered','desktop_excluded','run','passed')}
        backend['counts'].update(skipped=len(data['skipped']),failures=len(data['failures']),errors=len(data['errors']))
    input_tests={'qml-timeline-input':ROOT/'tests/test_qt_timeline_input.py',
                 'qml-hand-pan-input':ROOT/'tests/test_qt_hand_pan.py',
                 'qml-review-selection':ROOT/'tests/test_qt_review_selection.py',
                 'qml-preview-visibility':ROOT/'tests/test_qt_preview_visibility.py',
                 'qml-workspace-motion':ROOT/'tests/test_qt_workspace_motion.py',
                 'qml-window-chrome':ROOT/'tests/test_qt_window_chrome.py',
                 'qml-razor-input':ROOT/'tests/test_qt_razor_input.py',
                 'qml-explicit-editing':ROOT/'tests/test_qt_review_editing.py',
                 'qml-legacy-preview':ROOT/'tests/test_qml_preview_regression.py',
                 'export-formats':ROOT/'tests/test_srt_lrc_export.py',
                 'ai-pairing-013':ROOT/'tests/test_ai_pairing_013.py',
                 'payload-measurement':ROOT/'tests/test_measure_pairing_payload.py',
                 'playback-clock':ROOT/'tests/test_playback_clock.py',
                 'qc-performance':ROOT/'tests/test_qc_performance.py',
                 'style-fidelity-export':ROOT/'tests/test_style_fidelity_export.py',
                 'timeline-snap-math':ROOT/'tests/test_timeline_snap_math.py'}
    contract_tests=[p for p in sorted(ROOT.glob('tests/test_qt_*.py')) if p not in input_tests.values()]
    # unittest does not run module-level pytest functions. Fail visibly if a new
    # module has such tests but is absent from either explicit pytest route.
    assigned=set(contract_tests)|set(input_tests.values())
    standalone=[p for p in ROOT.glob('tests/test_*.py')
                if any(isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name.startswith('test_')
                       for node in ast.parse(p.read_text(encoding='utf8')).body)]
    unassigned=sorted(str(p.relative_to(ROOT)) for p in standalone if p not in assigned)
    results.append({'name':'pytest-discovery','status':'failed' if unassigned else 'passed',
                    'standalone_modules':len(standalone),'unassigned_modules':unassigned})
    contracts=execute('qt-contracts',['-m','pytest',*contract_tests,
                      '-q','--junitxml='+str(out/'qt-contracts.xml')])
    if (out/'qt-contracts.xml').is_file():
        suites=ET.parse(out/'qt-contracts.xml').getroot()
        suites=[suites] if suites.tag=='testsuite' else list(suites.findall('testsuite'))
        contracts['counts']={key:sum(int(s.get(key,'0')) for s in suites)
                             for key in ('tests','failures','errors','skipped')}
        if not contracts['counts']['tests']:contracts['status']='failed_no_tests'
    else:contracts['status']='failed_missing_evidence'
    contracts['fresh_visual_evidence']={name:bool(list((out/name).glob('*.png')))
        for name in ('native-dialogs','qml-review-motion','qml-timeline-fold')}
    contracts['fresh_motion_evidence']={name:(out/folder/name).is_file()
        for folder,name in (('qml-review-motion','review-auto-scroll.json'),
                            ('qml-timeline-fold','timeline-fold-motion.json'))}
    if contracts['status']=='passed' and not all([*contracts['fresh_visual_evidence'].values(),
                                                  *contracts['fresh_motion_evidence'].values()]):
        contracts['status']='failed_missing_evidence'
    qml=execute('qml-input',['desktop_qt/qml/tests/run_qml_scenarios.py'])
    summary=out/'qml'/'qa-summary.json'
    qml['fresh_summary_exists']=summary.is_file()
    if qml['status']=='passed' and not summary.is_file():qml['status']='failed_missing_evidence'
    elif summary.is_file():
        qml['scenario_status']=json.loads(summary.read_text(encoding='utf8')).get('status')
        if qml['scenario_status']!='passed':qml['status']='failed_scenario_evidence'
    input_results={}
    for name,test in input_tests.items():
        record=execute(name,['-m','pytest',test,'-q','--junitxml='+str(out/(name+'.xml'))])
        input_results[name]=record
        if (out/(name+'.xml')).is_file():
            suites=ET.parse(out/(name+'.xml')).getroot()
            suites=[suites] if suites.tag=='testsuite' else list(suites.findall('testsuite'))
            record['counts']={key:sum(int(s.get(key,'0')) for s in suites)
                              for key in ('tests','failures','errors','skipped')}
            if not record['counts']['tests']:record['status']='failed_no_tests'
        else:record['status']='failed_missing_evidence'
    for name,folder in (('qml-preview-visibility','qml-preview'),
                        ('qml-workspace-motion','qml-motion'),
                        ('qml-window-chrome','qml-window-chrome')):
        record=input_results[name]
        record['fresh_screenshots']=len(list((out/folder).glob('*.png')))
        if record['status']=='passed' and not record['fresh_screenshots']:
            record['status']='failed_missing_evidence'
    preview=input_results['qml-preview-visibility']
    preview_details=['preview-details-roi-eye-visible.png','preview-details-roi-eye-hidden.png',
                     'preview-details-roi-settings-dialog.png','preview-details-roi-manual-cancel.png',
                     'preview-details-roi-manual-committed.png']
    preview_details += ['preview-details-frames-'+size+'-'+kind+'.png'
                        for size in ('330x700','480x700','900x700','1400x500')
                        for kind in ('normal','long')]
    preview['fresh_visual_details']={name:(out/'qml-preview'/name).is_file()
                                     for name in preview_details}
    if preview['status']=='passed' and not all(preview['fresh_visual_details'].values()):
        preview['status']='failed_missing_evidence'
    timeline=input_results['qml-timeline-input']
    timeline['screenshots']={name:(out/'qml-timeline'/name).is_file()
                             for name in ('timeline-1400x940.png','timeline-760x900.png')}
    if timeline['status']=='passed' and not all(timeline['screenshots'].values()):
        timeline['status']='failed_missing_evidence'
    tool_files=['tool-icons-1400x940.png','tool-icons-760x600.png',
                'tool-icons-pending-1400.png','tool-icons-pending-760.png']
    for kind in ('valid','first','after_last','locked','single','blank','gap','label','ruler'):
        tool_files += ['razor-'+kind+suffix for suffix in
                       ('-cursor.json','-native-cursor.png','-ui.png')]
    tool_files += [name+'-cursor.json' for name in
                  ('restore-button','restore-menu','restore-editor','restore-ime',
                   'chain-hand-open','chain-hand-closed','chain-hand-released',
                   'chain-select','chain-edge','lifecycle-folded')]
    tool_files += [tool+'-'+entry+'-'+state+'-cursor.json'
                  for tool in ('Select','Razor','Hand')
                  for entry in ('F2','double_click') for state in ('editing','readonly')]
    timeline['fresh_tool_visual_evidence']={name:(out/'qml-tool-visuals'/name).is_file()
                                           for name in tool_files}
    if timeline['status']=='passed' and not all(timeline['fresh_tool_visual_evidence'].values()):
        timeline['status']='failed_missing_evidence'
    # The OS cursor is not part of grabWindow screenshots. Preserve and verify
    # the actual QQuickWindow.cursor pixmap separately, never a composited mock.
    timeline['native_cursor_evidence_valid']=True
    for path in (out/'qml-tool-visuals').glob('*-cursor.json'):
        data=json.loads(path.read_text(encoding='utf8'))
        valid=data.get('global_override') is False
        if data.get('kind') in ('razor','forbidden'):
            pixmap=path.with_name(path.name.replace('-cursor.json','-native-cursor.png'))
            valid=valid and data.get('shape')=='BitmapCursor' and data.get('hotspot')==[3,13]
            valid=valid and pixmap.is_file() and hashlib.sha256(pixmap.read_bytes()).hexdigest()==data.get('cursor_sha256')
        timeline['native_cursor_evidence_valid'] &= valid
    if timeline['status']=='passed' and not timeline['native_cursor_evidence_valid']:
        timeline['status']='failed_native_cursor_evidence'
    hand=input_results['qml-hand-pan-input']
    hand['fresh_evidence']={name:(out/'qml-hand-pan'/name).is_file()
        for name in ('hand-3-before.png','hand-3-after-empty-window.png',
                     'hand-5-after-empty-window.png','hand-0-after-empty-window.png',
                     'hand-3-state.json','hand-5-state.json','hand-0-state.json',
                     'hand-bounds-repeated.png','hand-short-0-rows.png',
                     'hand-narrow-760x600.png')}
    if hand['status']=='passed' and not all(hand['fresh_evidence'].values()):
        hand['status']='failed_missing_evidence'
    razor=input_results['qml-razor-input']
    razor['fresh_evidence']={name:(out/'qml-razor'/name).is_file()
        for name in ('ae-razor-pending.png','ae-razor-allocation.png','ae-razor-allocated.png',
                     'ae-razor-narrow-760x600.png','razor-input-state.json','razor-motion-state.json')}
    if razor['status']=='passed' and not all(razor['fresh_evidence'].values()):
        razor['status']='failed_missing_evidence'
    editing=input_results['qml-explicit-editing']
    editing['fresh_evidence']={name:(out/'qml-explicit-editing'/name).is_file()
        for name in ('double-click-editing.png','explicit-entry.json','ime-editing.json',
                     'ime-preedit-not-committed.png','f2-narrow-760x600-top.png',
                     'f2-narrow-760x600-bottom.png')}
    if editing['status']=='passed' and not all(editing['fresh_evidence'].values()):
        editing['status']='failed_missing_evidence'
    literal=execute('ass-literal-render',['tools/validate_ass_literal_render.py','--output',out/'ass-literal'])
    if (out/'ass-literal'/'result.json').is_file():
        literal['render_cases_passed']=json.loads((out/'ass-literal'/'result.json').read_text(encoding='utf8')).get('passed') is True
        if not literal['render_cases_passed']:literal['status']='failed_render_evidence'
    else:literal['status']='failed_missing_evidence'
    # These are actual QML/Qt event routes, not direct formatter or handler calls.
    # Fresh evidence is required after every later source change as well.
    for name,script in (('qml-ime-split','run_qml_ime_split.py'),
                        ('qml-menu-routes','run_qml_menu_routes.py')):
        scenario=execute(name,['desktop_qt/qml/tests/'+script,
                              '--video',ROOT/'examples/synthetic_mv.mp4','--output',out/name])
        evidence=out/name/'summary.json'
        if evidence.is_file():
            scenario['scenario_status']=json.loads(evidence.read_text(encoding='utf8')).get('status')
            if scenario['scenario_status']!='passed':scenario['status']='failed_scenario_evidence'
        else:scenario['status']='failed_missing_evidence'
    qc_ui=execute('qml-qc-input',['desktop_qt/qml/tests/run_qml_qc_report.py',
                                '--report',args.qc_report.resolve(),'--output',out/'qml-qc'])
    if (out/'qml-qc'/'summary.json').is_file():
        qc_ui['scenario_status']=json.loads((out/'qml-qc'/'summary.json').read_text(encoding='utf8')).get('status')
        if qc_ui['scenario_status']!='passed':qc_ui['status']='failed_scenario_evidence'
    else:qc_ui['status']='failed_missing_evidence'
    responsive=execute('qml-responsiveness',['desktop_qt/qml/tests/run_qml_responsiveness.py',
                        '--output',out/'responsiveness','--platform','offscreen','--software'])
    if (out/'responsiveness'/'responsiveness.json').is_file():
        diagnosis=json.loads((out/'responsiveness'/'responsiveness.json').read_text(encoding='utf8'))
        responsive['functional_assertions_status']=diagnosis.get('status')
        responsive['performance_acceptance']=False
        responsive['slow_points']=diagnosis.get('slow_points',[])
        if diagnosis.get('status')!='passed':responsive['status']='failed_scenario_evidence'
    else:responsive['status']='failed_missing_evidence'
    smoke=execute('qt-startup',['app_qt.py','--video','examples/synthetic_mv.mp4',
                  '--storage-dir',out/'smoke-recovery','--exit-after','3000',
                  '--screenshot',out/'startup.png','--self-check',out/'selfcheck.json'])
    if (out/'selfcheck.json').is_file():
        diagnosis=json.loads((out/'selfcheck.json').read_text())
        smoke['runtime_ok']=diagnosis.get('ok') is True
        if not smoke['runtime_ok']:smoke['status']='failed_runtime_evidence'
    else:smoke['status']='failed_missing_evidence'
    if args.with_real_ocr:
        execute('real-local-ocr',['tests/run_pipeline.py',out/'ocr'],timeout=1800)
        execute('qt-video-only-ocr',['tests/run_qt_video_only.py',out/'qt-video-only'],timeout=1800)
    after=source_inventory()
    changed=sorted(key for key in before.keys()|after.keys() if before.get(key)!=after.get(key))
    result={'created_utc':datetime.now(timezone.utc).isoformat(),
            'scope':'Linux/offscreen layered evidence; not Windows delivery or GPU/audio/native IME acceptance',
            'all_executed_layers_passed':all(r['status']=='passed' for r in results),
            'source_changed_during_run':changed,'final_source_snapshot_verified':not changed,
            'real_ocr_requested':args.with_real_ocr,'windows_acceptance_complete':False,
            'layers':results}
    (out/'source-after.json').write_text(json.dumps(after,indent=2),encoding='utf8')
    (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print('Evidence: '+str(out),flush=True)
    if not result['all_executed_layers_passed']:return 1
    return 3 if changed else 0


if __name__=='__main__':raise SystemExit(main())
