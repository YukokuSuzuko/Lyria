#!/usr/bin/env python3
"""Portable Lyria GUI responsiveness diagnostic; never a Windows acceptance substitute.

Windows/native: python desktop_qt/qml/tests/run_qml_responsiveness.py --output PATH
Cloud: add --platform offscreen --software. --video reads an existing video; without
it a clearly labeled, silent 29.97fps synthetic video is generated locally. No OCR,
API, user-project save, or input-file modification. A watchdog terminates only this
new diagnostic process if its own GUI becomes stuck past --timeout.
"""
from pathlib import Path
import argparse,hashlib,json,math,os,subprocess,sys,tempfile,threading,time
from datetime import datetime,timezone
from collections import deque
from itertools import islice
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT));os.chdir(ROOT)
parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--manual',action='store_true',help='Keep native window open for real user border/panel/scrub gestures; no automatic quit');parser.add_argument('--platform');parser.add_argument('--software',action='store_true');parser.add_argument('--video');parser.add_argument('--mute',action='store_true');parser.add_argument('--timeout',type=float,default=120)
args=parser.parse_args();out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
if args.platform:os.environ['QT_QPA_PLATFORM']=args.platform
if args.software:os.environ['QT_QUICK_BACKEND']='software'
if os.name!='nt':
    cache=out/'cache';cache.mkdir(exist_ok=True);os.environ['XDG_CACHE_HOME']=str(cache)
from PySide6.QtCore import QObject,QPoint,Qt,QTimer,QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from desktop_qt.controller import Bridge
from lyricsync.audit_service import ffmpeg_executable
from lyricsync.model import Mapping,Translation
from tools.qt_event_loop_wait import delay,wait_until
report={'status':'running','created_utc':datetime.now(timezone.utc).isoformat(),'platform':args.platform or 'native default','software_requested':args.software,'windows_acceptance':False,'performance_acceptance':False,'functional_assertions_only':True,'automatic_geometry_is_not_native_border_validation':True,'wait_strategy':'QEventLoop timers between QTest mouse inputs; no repeated QTest.qWait','harness_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'description':'GUI event-loop, continuous window/panel resize and scrub diagnostic; not GPU frame-time or audio-sync certification','qml_source_sha256':hashlib.sha256(b''.join(x.name.encode()+x.read_bytes() for x in sorted((ROOT/'desktop_qt/qml').glob('*.qml')))).hexdigest(),'phases':[]}
result_path=out/'responsiveness.json';result_path.write_text(json.dumps(report,ensure_ascii=False,indent=2));finished=threading.Event();began=time.monotonic()
def watchdog():
    if not finished.wait(args.timeout):
        report.update(status='timeout',finished_utc=datetime.now(timezone.utc).isoformat(),error='Diagnostic GUI did not finish; process-only watchdog expired.')
        result_path.write_text(json.dumps(report,ensure_ascii=False,indent=2));os._exit(3)
if not args.manual:threading.Thread(target=watchdog,daemon=True).start()
# Generate diagnostic input before a GUI exists: fixture encoding is test setup,
# never product GUI freeze or interaction latency.
fixture_started=time.monotonic();video=Path(args.video).resolve() if args.video else out/'diagnostic-29.97-silent.mp4'
if not args.manual and not args.video and not video.exists():
    subprocess.run([str(ffmpeg_executable()),'-loglevel','error','-f','lavfi','-i','testsrc2=size=640x360:rate=30000/1001','-frames:v','900','-c:v','libx264','-g','90','-pix_fmt','yuv420p',str(video)],check=True)
report['fixture_setup']={'milliseconds':round((time.monotonic()-fixture_started)*1000,3),'before_gui_creation':True,'excluded_from_interaction_heartbeat':True}
QQuickStyle.setStyle('Basic');app=QApplication([]);bridge=Bridge(storage_dir=out/'recovery');engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('bridge',bridge);engine.load(QUrl.fromLocalFile(str(ROOT/'desktop_qt/qml/Main.qml')))
assert engine.rootObjects(),'QML did not instantiate';window=engine.rootObjects()[0]
def render_metadata():
    api=window.rendererInterface().graphicsApi()
    return {'actual_platform':QGuiApplication.platformName(),
            'actual_graphics_api':getattr(api,'name',str(api)),
            'presentation_path':bridge.media.get('presentationPath','not_available_yet'),
            'actual_frame_presented':bridge.media_controller._state.get('displayedFrame',0),
            'QT_QUICK_BACKEND':os.environ.get('QT_QUICK_BACKEND',''),
            'QSG_RHI_BACKEND':os.environ.get('QSG_RHI_BACKEND',''),
            'QT_QPA_PLATFORM':os.environ.get('QT_QPA_PLATFORM',''),
            'software_frame_surface_enabled':bool(bridge.softwareRendering)}
report['rendering']=render_metadata()

phase=['startup'];heartbeat_tags=deque(maxlen=100000) if args.manual else []
heartbeat=deque(maxlen=100000) if args.manual else [];last_beat=[time.monotonic()];beat=QTimer();beat.setInterval(10)
def tick():
    if args.manual:
        live=bridge.media_controller._state
        phase[0]='manual_empty' if not live.get('ready') else 'manual_playing' if live.get('playing') else 'manual_loaded_paused'
    now=time.monotonic();elapsed=(now-last_beat[0])*1000;heartbeat.append(elapsed);heartbeat_tags.append((phase[0],elapsed));last_beat[0]=now
beat.timeout.connect(tick);beat.start()
opens=deque(maxlen=20000) if args.manual else [];requests=deque(maxlen=20000) if args.manual else [];presented=deque(maxlen=20000) if args.manual else []
original_open=bridge.media_controller.worker.open;original_seek=bridge.media_controller.worker.seek
def tracked_open(path,epoch):opens.append((phase[0],path,epoch));return original_open(path,epoch)
def tracked_seek(request):requests.append((phase[0],request.frame,time.monotonic()));return original_seek(request)
bridge.media_controller.worker.open=tracked_open;bridge.media_controller.worker.seek=tracked_seek
bridge.media_controller.presented.connect(lambda frame,seconds:presented.append((phase[0],frame,seconds,time.monotonic())))
def set_phase(name):
    tick();phase[0]=name
def wait(predicate,timeout=15):
    # QApplication.exec releases the GIL for Python media workers. Repeated
    # QTest.qWait retains it on the tested PySide build and inflates seek time.
    # Keep the same deadline and actual-state predicate, using a real Qt loop.
    wait_until(predicate,timeout_ms=round(timeout*1000))
def find(name,item=None):
    item=item or window.contentItem()
    if item.objectName()==name:return item
    for child in item.childItems():
        found=find(name,child)
        if found:return found
    return None
def stats(values):
    values=sorted(values)
    return {'samples':len(values),'p50_ms':round(values[len(values)//2],3) if values else None,'p95_ms':round(values[min(len(values)-1,int(len(values)*.95))],3) if values else None,'max_ms':round(max(values),3) if values else None,'over_100_ms':sum(v>100 for v in values),'over_1000_ms':sum(v>1000 for v in values)}
def gesture(item,dx,dy,steps=25):
    start=item.mapToScene(QPoint(max(1,int(item.property('width')/2)),max(1,int(item.property('height')/2))))
    QTest.mousePress(window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start.toPoint());cost=[]
    for n in range(1,steps+1):
        point=start+QPoint(round(dx*n/steps),round(dy*n/steps));begin=time.perf_counter();QTest.mouseMove(window,point.toPoint());app.processEvents();cost.append((time.perf_counter()-begin)*1000);delay(12)
    QTest.mouseRelease(window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point.toPoint());delay(40);return cost
if args.manual:
    set_phase('manual-native-recording');resize_events=deque(maxlen=20000);counts={'resize_events':0,'published_snapshots':0};published=[{}];snapshot_lock=threading.Lock();writer_stop=threading.Event()
    def publish():
        counts['published_snapshots']+=1
        # Primitive snapshots only, on the GUI thread. No per-event disk writes.
        state=bridge.media_controller._state
        snapshot=dict(rendering=render_metadata(),phase=phase[0],ready=state.get('ready'),width=window.width(),height=window.height(),requested_frame=state.get('requestedFrame'),actual_frame=state.get('displayedFrame'),playing=state.get('playing'),scrubbing=state.get('scrubbing'),decoder_seeks=state.get('decoderSeeks'),decoded_frames=state.get('decodedFrames'),source_open_count=len(opens),last_gui_heartbeat_monotonic=last_beat[0],counts=dict(counts),recent_resize_events=tuple(reversed(tuple(islice(reversed(resize_events),30)))),recent_presentations=tuple(reversed(tuple(islice(reversed(presented),30)))))
        with snapshot_lock:published[0]=snapshot
    def resized():
        counts['resize_events']+=1;resize_events.append((time.monotonic()-began,phase[0],window.width(),window.height()))
    window.widthChanged.connect(resized);window.heightChanged.connect(resized);publisher=QTimer();publisher.setInterval(250);publisher.timeout.connect(publish);publisher.start();publish()
    def background_writer():
        while not writer_stop.wait(2):
            with snapshot_lock:snapshot=dict(published[0])
            snapshot.update(status='manual_recording',utc=datetime.now(timezone.utc).isoformat(),gui_heartbeat_age_ms=round((time.monotonic()-last_beat[0])*1000,3))
            try:(out/'manual-live.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2))
            except OSError:pass
    threading.Thread(target=background_writer,daemon=True).start()
    report.update(manual=True,automatic_geometry_is_not_native_border_validation=True,instructions='User must actually drag native OS window edges, internal splitters, and seekbar in empty/loaded/playing states. Geometry signals alone do not identify the input source. No automatic quit; close the window to finish. Native titlebar appearance is not certified by this log.')
    result_path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    if args.video:bridge.command('media.open',{'path':str(Path(args.video).resolve())})
    if args.mute:bridge.command('media.mute',{'value':True})
    print('MANUAL RECORDING: perform real native window-border resize, panel resize, seekbar drag; close window to finish.',flush=True)
    print('Live worker-written log:',out/'manual-live.json',flush=True)
    try:
        app.exec()
        report.update(status='recorded_not_accepted',rendering=render_metadata(),finished_utc=datetime.now(timezone.utc).isoformat(),elapsed_seconds=round(time.monotonic()-began,3),gui_heartbeat=stats(heartbeat),heartbeat_by_state={name:stats([ms for tag,ms in heartbeat_tags if tag==name]) for name in sorted({tag for tag,ms in heartbeat_tags})},counts=counts,resize_events=list(resize_events),seek_requests=list(requests),presentations=list(presented),last_snapshot=dict(published[0]),windows_acceptance=False)
    finally:
        writer_stop.set();publisher.stop();beat.stop();finished.set();result_path.write_text(json.dumps(report,ensure_ascii=False,indent=2));bridge.shutdown();print('Manual evidence saved:',result_path)
    raise SystemExit(0)
try:
    bridge.command('stage.set',{'stage':'review'});window.requestActivate();delay(150)
    def resize_phase(label,playing=False):
        set_phase(label);start_beat=len(heartbeat);start_open=len(opens);start_request=len(requests);start_present=len(presented);before=bridge.media.copy();index_identity=id(bridge.media_controller.index);cost=[]
        for n in range(96):
            if n<32:t=n/31;width=1400-(1400-760)*t;height=940-(940-760)*t
            elif n<64:t=(n-32)/31;width=760+(1100-760)*t;height=760-(760-600)*t
            else:t=(n-64)/31;width=1100+(1400-1100)*t;height=600+(940-600)*t
            begin=time.perf_counter();window.resize(round(width),round(height));app.processEvents();cost.append((time.perf_counter()-begin)*1000);delay(12)
        window.resize(1400,940);delay(60)
        split_cost=gesture(find('mainSplitHandle'),90,0)+gesture(find('mainSplitHandle'),-90,0)+gesture(find('timelineSplitHandle'),0,-25)
        window.showMaximized();delay(40);window.showNormal();window.resize(1400,940);delay(100)
        after=bridge.media.copy();segment_requests=requests[start_request:];segment_frames=presented[start_present:]
        assert len(opens)==start_open and id(bridge.media_controller.index)==index_identity,'Resize must not reopen/reindex video'
        if not playing:
            assert after['decoderSeeks']==before['decoderSeeks'] and after['decodedFrames']==before['decodedFrames'],'Paused/empty resize must not decode the same frame repeatedly'
            assert not segment_requests,'Resize itself must not issue seek requests'
        else:
            assert after['displayedFrame']>before['displayedFrame'],'Playing video stopped presenting during resize'
            assert all(a[1]!=b[1] for a,b in zip(segment_requests,segment_requests[1:])),'Playing resize requested the same frame consecutively'
        assert find('mainPreviewPanel').property('width')>=330 and find('deleteCurrentCueButton').property('visible')
        entry={'name':label,'window_resize_dispatch':stats(cost),'panel_drag_dispatch':stats(split_cost),'gui_heartbeat':stats(heartbeat[start_beat:]),'reopens_or_reindexes':len(opens)-start_open,'seek_requests':len(segment_requests),'decoded_frames_delta':after['decodedFrames']-before['decodedFrames'],'decoder_seeks_delta':after['decoderSeeks']-before['decoderSeeks'],'actual_before':before['displayedFrame'],'actual_after':after['displayedFrame'],'presentations':len(segment_frames),'playing':playing}
        report['phases'].append(entry);set_phase(label+'_capture');window.grabWindow().save(str(out/(label+'.png')))
    resize_phase('empty_resize')
    assert not find('upperSeekMouse').property('enabled'),'Empty transport must not fake a seek'
    set_phase('loading_setup');assert bridge.command('media.open',{'path':str(video)});wait(lambda:bridge.media['ready'] and bridge.media['displayedFrame']==1,45)
    idx=bridge.media_controller.index;total=bridge.media['totalFrames'];middle=max(1,total//2);p=bridge.document.project
    p.translations=[Translation('diag-t1','诊断素材：前半段'),Translation('diag-t2','诊断素材：后半段')]
    p.mappings=[Mapping('diag-1',['diag-t1'],start=idx.interval(1,middle)[0],end=idx.interval(1,middle)[1]),Mapping('diag-2',['diag-t2'],start=idx.interval(middle+1,total)[0],end=idx.interval(middle+1,total)[1])]
    bridge.selected='diag-1';bridge.stage='review';bridge._changed();bridge.command('media.seek',{'frame':min(60,total)});wait(lambda:bridge.media['displayedFrame']==min(60,total));delay(100)
    resize_phase('loaded_paused_resize')
    def scrub_phase(label,start_playing=False):
        if start_playing:
            bridge.command('media.seek',{'frame':1});wait(lambda:bridge.media['displayedFrame']==1)
            if args.mute:bridge.command('media.mute',{'value':True})
            assert bridge.command('media.play',{}),bridge.state['error'];delay(150);assert bridge.media['playing']
        set_phase(label);start_beat=len(heartbeat);start_open=len(opens);start_present=len(presented);cost=[];rails=[]
        for item_name in ('upperSeekMouse','timelineRulerMouse'):
            item=find(item_name);start=item.mapToScene(QPoint(10,10));QTest.mousePress(window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start.toPoint())
            for n in range(80):
                fraction=.05+.9*(n/39 if n<40 else (79-n)/39);point=item.mapToScene(QPoint(round(item.property('width')*fraction),10));begin=time.perf_counter();QTest.mouseMove(window,point.toPoint());app.processEvents();cost.append((time.perf_counter()-begin)*1000);delay(12)
            QTest.mouseRelease(window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point.toPoint());target=bridge.media['requestedFrame'];released=time.perf_counter();wait(lambda:not bridge.media['scrubbing'] and bridge.media['displayedFrame']==target);latency=(time.perf_counter()-released)*1000
            assert bridge.media_controller.sink.videoFrame().isValid(),'Final target must reach a real video sink'
            rails.append({'rail':item_name,'final_target':target,'actual_final':bridge.media['displayedFrame'],'final_release_to_actual_ms':round(latency,3)})
        assert len(opens)==start_open,'Scrubbing must not rebuild the source index'
        report['phases'].append({'name':label,'started_playing':start_playing,'rails':rails,'scrub_dispatch':stats(cost),'gui_heartbeat':stats(heartbeat[start_beat:]),'reopens_or_reindexes':len(opens)-start_open,'presentations':len(presented)-start_present,'final_target':target,'actual_final':bridge.media['displayedFrame'],'final_release_to_actual_ms':round(latency,3)})
        set_phase(label+'_setup')
    scrub_phase('loaded_paused_scrub')
    bridge.command('media.seek',{'frame':1});wait(lambda:bridge.media['displayedFrame']==1)
    if args.mute:bridge.command('media.mute',{'value':True})
    assert bridge.command('media.play',{}),bridge.state['error'];delay(100);resize_phase('playing_resize',True);bridge.command('media.pause',{})
    scrub_phase('playing_scrub',True)
    set_phase('finalizing')
    interaction_names={p['name'] for p in report['phases']}
    report['gui_heartbeat_interaction']=stats([ms for name,ms in heartbeat_tags if name in interaction_names])
    report['gui_heartbeat_setup_capture']=stats([ms for name,ms in heartbeat_tags if name not in interaction_names])
    report['slow_points']=[{'phase':name,'heartbeat_ms':round(ms,3),'interaction':name in interaction_names} for name,ms in heartbeat_tags if ms>100]
    report['slow_points'] += [{'phase':p['name'],**r} for p in report['phases'] for r in p.get('rails',[]) if r['final_release_to_actual_ms']>100]
    report.update(status='passed',rendering=render_metadata(),finished_utc=datetime.now(timezone.utc).isoformat(),elapsed_seconds=round(time.monotonic()-began,3),source_video=str(video),frames=total,presentation_path=bridge.media.get('presentationPath'),audio_status=bridge.media.get('audioStatus'),gui_heartbeat_all=stats(heartbeat),native_titlebar_validation=False)
except BaseException as exc:
    report.update(status='failed',error=type(exc).__name__+': '+str(exc),finished_utc=datetime.now(timezone.utc).isoformat());raise
finally:
    beat.stop();finished.set();result_path.write_text(json.dumps(report,ensure_ascii=False,indent=2));bridge.shutdown();app.processEvents();print('Responsiveness report:',result_path)
