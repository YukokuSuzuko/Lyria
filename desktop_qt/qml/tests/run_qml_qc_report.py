#!/usr/bin/env python3
"""Read a real existing local OCR report, exercise QML/native evidence UI on a copy.
No fabricated OCR, no API, no re-OCR; --report is mandatory. Original stays untouched.
"""
from pathlib import Path
import argparse,hashlib,json,os,sys,time
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT));os.chdir(ROOT)
p=argparse.ArgumentParser();p.add_argument('--report',required=True);p.add_argument('--output',required=True);p.add_argument('--platform');p.add_argument('--wait-style',choices=('cooperative','qt'),default='cooperative',help='qt reproduces historical busy QTest wait for diagnosis; same 10 s deadline');args=p.parse_args()
if args.platform:os.environ['QT_QPA_PLATFORM']=args.platform
if args.platform=='offscreen':os.environ['QT_QUICK_BACKEND']='software'
out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
if os.name!='nt':cache=out/'cache';cache.mkdir(exist_ok=True);os.environ['XDG_CACHE_HOME']=str(cache)
from PySide6.QtCore import QObject,QUrl,QPoint,Qt,QTimer,Slot,QMetaObject
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication,QDialog
from PySide6.QtTest import QTest
from desktop_qt.controller import Bridge
source=Path(args.report).resolve()
summary={'status':'running','created_utc':datetime.now(timezone.utc).isoformat(),'source_report':str(source),'wait_style':args.wait_style,'harness_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'qml_source_sha256':hashlib.sha256(b''.join(x.name.encode()+x.read_bytes() for x in sorted((ROOT/'desktop_qt/qml').glob('*.qml')))).hexdigest()}
(out/'summary.json').write_text(json.dumps(summary,indent=2))
if not source.is_file():
    summary.update(status='notrun',reason='Required actual local OCR report is missing; no OCR fixture fabricated.')
    (out/'summary.json').write_text(json.dumps(summary,indent=2));raise SystemExit(2)
before=source.read_bytes();report=json.loads(before);summary['source_sha256']=hashlib.sha256(before).hexdigest()
if not (report.get('cues') or report.get('source_cues')):
    summary.update(status='notrun',reason='Report has no observed OCR cues for this evidence scenario; no synthetic substitutes.')
    (out/'summary.json').write_text(json.dumps(summary,indent=2));raise SystemExit(2)
copy_dir=out/'report-copy';copy_dir.mkdir(exist_ok=True);(copy_dir/'report.json').write_bytes(before)
(out/'summary.json').write_text(json.dumps(summary,indent=2))
QQuickStyle.setStyle('Basic');app=QApplication([])
if os.name=='nt' and args.platform=='offscreen':
    from PySide6.QtGui import QFontDatabase,QFont
    font=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/msyh.ttc'
    if font.is_file():QFontDatabase.addApplicationFont(str(font));app.setFont(QFont('Microsoft YaHei UI',10))
class UiTestBridge(Bridge):
    @Slot(str,'QVariantMap',result=bool)
    def command(self,name,payload):
        if name=='qc.start':
            self.start_requests.append(dict(payload));return True
        return super().command(name,payload)
bridge=UiTestBridge(storage_dir=out/'recovery');bridge.start_requests=[];engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('bridge',bridge);engine.load(QUrl.fromLocalFile(str(ROOT/'desktop_qt/qml/Main.qml')));assert engine.rootObjects();window=engine.rootObjects()[0]
job_records=[];original_start_job=bridge._start_job
# Diagnostics only: record worker and GUI callback milestones, never substitute a result.
def tracked_start_job(kind,function,callback,**kwargs):
    record={'kind':kind,'dispatched':time.monotonic()};job_records.append(record)
    def work(cancel,progress):
        record['worker_started']=time.monotonic()
        try:return function(cancel,progress)
        finally:record['worker_finished']=time.monotonic()
    def completed(result,error):
        record['callback_started']=time.monotonic();record['worker_error']=type(error).__name__+': '+str(error) if error else ''
        try:return callback(result,error)
        finally:record['callback_finished']=time.monotonic()
    return original_start_job(kind,work,completed,**kwargs)
bridge._start_job=tracked_start_job

def diagnostic():
    files={}
    for name in ('report.json','report.html'):
        path=copy_dir/name
        files[name]={'exists':path.exists(),'bytes':path.stat().st_size,'mtime_ns':path.stat().st_mtime_ns} if path.exists() else {'exists':False}
    return {'task':dict(bridge._task),'status':bridge._status,'error':bridge._error_text,'handlers':list(bridge._handlers),
            'jobs':list(bridge.jobs.active),'raw_ui_cues':[{'id':c.get('cueId'),'reviewStatus':c.get('reviewStatus')} for c in bridge._qc.get('cues',[])],
            'job_milestones':job_records,'files':files}

def find(name,item=None):
    item=item or window.contentItem()
    if item.objectName()==name:return item
    for child in item.childItems():
        found=find(name,child)
        if found:return found
    return None
def click(item):
    assert item and item.property('enabled')
    point=item.mapToScene(QPoint(int(item.property('width')/2),int(item.property('height')/2)))
    listing=find('qcCueList')
    if listing and item.objectName().startswith(('qcInspect_','qcRescan_','qcSkip_','qcFrameChoice_')):
        bottom=listing.mapToScene(QPoint(0,int(listing.property('height')))).y()
        if point.y()>bottom-10:
            listing.setProperty('contentY',listing.property('contentY')+point.y()-bottom+30);app.processEvents()
            point=item.mapToScene(QPoint(int(item.property('width')/2),int(item.property('height')/2)))
    QTest.mouseClick(window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point.toPoint());QTest.qWait(80)
def all_text(item):
    return [item.property('text')] + [value for child in item.childItems() for value in all_text(child)]
def modal_capture(filename):
    result=[];timer=QTimer();timer.setInterval(40)
    def capture():
        dialog=app.activeModalWidget()
        if isinstance(dialog,QDialog) and dialog.isVisible():
            result.append(dialog.windowTitle());dialog.grab().save(str(out/filename));timer.stop();dialog.reject()
    timer.timeout.connect(capture);timer.start();return timer,result
try:
    bridge._display_qc_report(report,report['inputs']['video'],copy_dir)
    window.setProperty('workspace','qc');QTest.qWait(300)
    cues=bridge.state['qc']['cues'];target=next((c for c in cues if c.get('firstFrame') is None),cues[0]);identity=target['cueId']
    card=find('qcCue_'+identity);assert card,'Actual report cue must be visible'
    assert any(isinstance(t,str) and ('逐帧实验仍需人工核对' if bridge.state['qc'].get('sentenceMode') is False else '不是字幕出入点验证') in t for t in all_text(card))
    for raw,display in zip(report['cues'],cues):
        if 'overall_status' in raw:assert display['contentStatus']==raw['overall_status'],'UI must use bilingual overall status'
    selected=(target.get('evidenceFrames') or [])
    if len(selected)>1:
        click(find('qcFrameChoice_'+identity+'_1'))
        assert find('qcFrame_'+identity).property('source').toString()==selected[1]['url']
        for field,name in [('samples','qcSelectedChineseOcr_'),('source_samples','qcSelectedSourceOcr_')]:
            sample=next((s for s in target.get(field,[]) if s['frame']==selected[1]['frame']),None)
            shown=find(name+identity).property('text')
            assert (sample.get('text') or '此帧未识别到文字') in shown if sample else '没有该路 OCR 证据' in shown
        summary['selected_frame_ocr_matches_displayed_frame']=True
    assert window.grabWindow().save(str(out/'qc-real-report.png'))
    timer,captured=modal_capture('qc-real-evidence-dialog.png');click(find('qcInspect_'+identity));deadline=time.monotonic()+10
    while not captured and time.monotonic()<deadline:app.processEvents();QTest.qWait(20)
    timer.stop();assert captured,'Real evidence dialog did not appear'
    timer,captured=modal_capture('qc-rescan-cancelled-dialog.png');prior=len(bridge.state['qc'].get('rescans',[]));click(find('qcRescan_'+identity));deadline=time.monotonic()+5
    while not captured and time.monotonic()<deadline:app.processEvents();QTest.qWait(20)
    timer.stop();assert captured and len(bridge.state['qc'].get('rescans',[]))==prior and not bridge.state['task']['running']
    summary['review_before_click']=target['reviewStatus'];save_started=time.monotonic()
    click(find('qcSkip_'+identity));deadline=time.monotonic()+10;complete=False
    while time.monotonic()<deadline:
        app.processEvents()
        current=next(c for c in bridge.state['qc']['cues'] if c['cueId']==identity)
        card=find('qcCue_'+identity)
        complete=(not bridge.state['task']['running'] and current['reviewStatus']=='skipped' and card is not None and '已跳过 · 未通过' in all_text(card))
        if complete or bridge._error_text:break
        # PySide QTest.qWait can retain the GIL while its native event loop runs.
        # A real application's app.exec does not starve this Python worker that way.
        if args.wait_style=='qt':QTest.qWait(20)
        else:time.sleep(.005)
    summary['review_save_elapsed_ms']=round((time.monotonic()-save_started)*1000,3)
    summary['review_diagnostic']=diagnostic()
    assert complete,'Timed out or errored before worker AND QML skip state completed: '+json.dumps(summary['review_diagnostic'],ensure_ascii=False)
    updated=next(c for c in bridge.state['qc']['cues'] if c['cueId']==identity)
    saved=json.loads((copy_dir/'report.json').read_text(encoding='utf-8'))
    raw=next(c for c in saved['cues']+saved.get('source_cues',[]) if c['id']==identity)
    assert updated['reviewStatus']=='skipped' and raw.get('counts_as_pass') is False, (updated['reviewStatus'],raw.get('review_status'),raw.get('counts_as_pass'))
    assert updated['text']==target['text'] and updated['firstFrame']==target['firstFrame'] and updated['lastFrame']==target['lastFrame']
    assert saved['coverage']['complete_video_verified'] is False
    assert source.read_bytes()==before,'Original actual report must remain unchanged'
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        image=find('qcFrame_'+identity)
        if image is not None and image.property('evidenceReady'):break
        app.processEvents();time.sleep(.01)
    window.grabWindow().save(str(out/'qc-skipped-not-passed.png'))
    advanced=find('qcAdvancedStrategy');assert advanced and not advanced.property('checked')
    def start_without_execution():
        click(find('qcStartButton'))
        dialog=window.findChild(QObject,'qcNoApiDialog');assert dialog is not None
        assert QMetaObject.invokeMethod(dialog,'accept');app.processEvents()
    start_without_execution();assert bridge.start_requests[-1]['strategy']=='sentence'
    click(advanced);start_without_execution();assert bridge.start_requests[-1]['strategy']=='frame'
    assert len(bridge.start_requests)==2 and not bridge.jobs.active
    window.grabWindow().save(str(out/'qc-advanced-opt-in.png'))
    summary['strategy_route_dry_run']=['sentence','frame']
    # UI-only running-state checks. Do not launch or pretend to run inference.
    saved_task=dict(bridge._task)
    cancellation_labels=[]
    for is_frame in (False,True):
        advanced.setProperty('checked',is_frame)
        bridge._task={'running':True,'label':'高级逐帧质检（整首耗时未验证）' if is_frame else '快速逐句质检','progress':.4,'id':'ui-only-cancellation-copy'}
        bridge._refresh(False);app.processEvents();QTest.qWait(80)
        cancel_button=find('qcCancelTask');assert cancel_button and cancel_button.property('visible')
        expected='取消，仅保留已完成阶段' if is_frame else '取消并保留已查'
        assert cancel_button.property('text')==expected
        cancellation_labels.append(expected)
        if is_frame:assert any('当前轨道中间结果不保留' in t for t in all_text(window.contentItem()) if isinstance(t,str))
        assert window.grabWindow().save(str(out/('qc-frame-cancel-copy.png' if is_frame else 'qc-sentence-cancel-copy.png')))
    bridge._task=saved_task;bridge._refresh(False);app.processEvents()
    summary['cancellation_copy_ui_only']=cancellation_labels

    summary.update(status='passed',finished_utc=datetime.now(timezone.utc).isoformat(),cues=len(cues),unknown_boundary_displayed_as_unknown=target.get('firstFrame') is None,actual_evidence_dialog=True,rescan_cancel_no_job=True,skipped_not_pass=True,raw_ocr_and_times_unchanged=True,original_report_unchanged=True)
except BaseException as exc:summary.update(status='failed',error=type(exc).__name__+': '+str(exc),diagnostic=diagnostic());raise
finally:(out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));bridge.shutdown();app.processEvents();print('QC UI report:',out/'summary.json')
