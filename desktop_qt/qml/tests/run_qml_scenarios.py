#!/usr/bin/env python3
"""Actual offscreen QML input scenarios, separate from backend unit tests.

Run with the migration PySide6 environment. Creates a clearly labeled local
synthetic video and test-only document; never calls OCR, network API, or secrets.
Evidence defaults to migration/validation/qml; override LYRIA_QML_EVIDENCE.
This does not establish Windows GPU/audio/real OS IME or DPI acceptance.
"""
from pathlib import Path
import sys,time,subprocess,json,os,tempfile,atexit,hashlib
from datetime import datetime,timezone
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
if os.name != 'nt':
    inherited=os.environ.get('XDG_CACHE_HOME','')
    cache=Path(inherited) if inherited and Path(inherited).is_dir() and os.access(inherited,os.W_OK) else Path(tempfile.gettempdir())/'lyria-qml-cache'
    cache.mkdir(parents=True,exist_ok=True)
    os.environ['XDG_CACHE_HOME']=str(cache)
ROOT=Path(__file__).resolve().parents[3]
os.chdir(ROOT)
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject,QUrl,QPoint,Qt,QEvent
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtGui import QInputMethodEvent,QKeyEvent,QAccessible
sys.path.insert(0,str(Path.cwd()))
from desktop_qt.controller import Bridge
from lyricsync.model import Mapping,Translation,Lyric
from lyricsync.audit_service import ffmpeg_executable
out=Path(os.environ.get('LYRIA_QML_EVIDENCE',str(ROOT.parent/'validation/qml'))).resolve();out.mkdir(parents=True,exist_ok=True);video=out/'qa-actual-pts.mp4'
run_info={'status':'running','created_utc':datetime.now(timezone.utc).isoformat(),'harness_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'qml_source_sha256':hashlib.sha256(b''.join(x.name.encode()+x.read_bytes() for x in sorted((ROOT/'desktop_qt/qml').glob('*.qml')))).hexdigest()}
summary_path=out/'qa-summary.json';summary_path.write_text(json.dumps(run_info,indent=2));passed=False
def record_failure():
    if not passed:summary_path.write_text(json.dumps({**run_info,'status':'failed','finished_utc':datetime.now(timezone.utc).isoformat(),'detail':'Scenario process exited before all assertions completed; see qa.log.'},indent=2))
atexit.register(record_failure)
if not video.exists():subprocess.run([str(ffmpeg_executable()),'-loglevel','error','-f','lavfi','-i','testsrc2=size=640x360:rate=30000/1001','-frames:v','180','-c:v','libx264','-g','60','-pix_fmt','yuv420p',str(video)],check=True)
QQuickStyle.setStyle('Basic');app=QApplication([]);b=Bridge(storage_dir=out/'recovery');e=QQmlApplicationEngine();e.rootContext().setContextProperty('bridge',b);e.load(QUrl.fromLocalFile(str(Path('desktop_qt/qml/Main.qml').resolve())));assert e.rootObjects();w=e.rootObjects()[0]
def wait(fn=lambda:True,t=5000):
    until=time.monotonic()+t/1000
    while time.monotonic()<until:
        app.processEvents();time.sleep(.01)
        if fn():QTest.qWait(100);return
    raise AssertionError('timed out')
def find(name, item=None):
    item=item or w.contentItem()
    if item.objectName()==name:return item
    for child in item.childItems():
        got=find(name,child)
        if got:return got
    return None
def shot(name):
    QTest.qWait(200);assert w.grabWindow().save(str(out/name))
b.command('media.open',{'path':str(video)});wait(lambda:b.media['ready'] and b.media['displayedFrame']==1)
p=b.document.project
p.translations=[Translation('qt1','界面验收：第一句'),Translation('qt2','界面验收：字符👨‍👩‍👧‍👦与e\u0301完整边界'),Translation('qt3','界面验收：第三句，用于检查窄窗口与多行长文本的显示。')]
p.lyrics=[Lyric('ql1','UI validation: first line',0,1),Lyric('ql2','UI validation: grapheme boundaries',1,2),Lyric('ql3','UI validation: final line',2,3)]
idx=b.media_controller.index
p.mappings=[Mapping('qa1',['qt1'],['ql1'],start=idx.interval(13,42)[0],end=idx.interval(13,42)[1]),Mapping('qa2',['qt2'],['ql2'],start=idx.interval(43,96)[0],end=idx.interval(43,96)[1]),Mapping('qa3',['qt3'],['ql3'],start=idx.interval(105,151)[0],end=idx.interval(105,151)[1])]
b.selected='qa2';b.stage='review';b._changed();b.command('marker.add',{'frame':65});b.command('media.seek',{'frame':65});wait(lambda:b.media['displayedFrame']==65)
# Visible labels are complete; shortcut punctuation belongs to the tooltip,
# while screen readers distinguish start/end actions without relying on position.
for button_name,edge_label,key_label in [('firstSetCurrentButton','入点','['),('lastSetCurrentButton','出点',']')]:
    button=find(button_name);assert button and button.property('text')=='设为当前'
    assert edge_label in button.property('tip') and key_label in button.property('tip')
    interface=QAccessible.queryAccessibleInterface(button);assert interface is not None
    accessible=interface.text(QAccessible.Text.Name)
    assert edge_label in accessible and '实际播放头' in accessible,accessible
shot('review-1400x940.png')
assert w.findChild(QObject,'frameTimeline').property('visible')
# Direct editing through the actual QML TextArea signal, preserving original text.
editor=find('chineseEditor_qa2');assert editor
focus_original=b.model.items[1]['chinese'];print('MARKER', [(n,find('fixedMarker_65').property(n)) for n in ['frame','x','y','width']]);QTest.keyClick(w,Qt.Key.Key_F2);assert editor.hasActiveFocus() and not editor.property('readOnly');editor.setProperty('text','即时编辑：字符👨‍👩‍👧‍👦与e\u0301完整边界');wait(lambda:b.model.items[1]['chinese'].startswith('即时编辑'))
assert p.translations[1].text.startswith('界面验收')
# IME preedit must not reach the document; commit does.
before=b.model.items[1]['chinese'];pre=QInputMethodEvent('拼音输入中',[]);app.sendEvent(editor,pre);app.processEvents();assert b.model.items[1]['chinese']==before
commit=QInputMethodEvent();commit.setCommitString('测试');app.sendEvent(editor,commit);app.processEvents();assert '测试' in b.model.items[1]['chinese']
# Focused undo/redo must update the same TextArea, not recommit stale text.
committed=b.model.items[1]['chinese'];QTest.keyClick(w,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier);QTest.qWait(100)
assert b.model.items[1]['chinese']==before and editor.property('text')==before
QTest.keyClick(w,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier|Qt.KeyboardModifier.ShiftModifier);QTest.qWait(100)
assert b.model.items[1]['chinese']==committed and editor.property('text')==committed
# Guard manual locking semantics after sibling controller adoption.
assert not b.model.items[1]['locked'], 'first edit must not manually freeze'
b.command('row.lock',{'id':'qa2','value':True});QTest.qWait(80);assert editor.property('readOnly');b.command('row.lock',{'id':'qa2','value':False});QTest.qWait(80);assert editor.property('readOnly');QTest.keyClick(w,Qt.Key.Key_F2);assert not editor.property('readOnly')
# Drag a fixed marker copy to an actual TextArea character boundary.
editor.setProperty('cursorPosition',4);app.processEvents()
marker=find('fixedMarker_65');assert marker.property('x')>10
start=marker.mapToScene(QPoint(11,13));r=editor.property('cursorRectangle');target=editor.mapToScene(QPoint(60,int(editor.property('height')/2)));print('DRAG GEOMETRY',start,target,r,editor.property('cursorPosition'))
QTest.mousePress(w,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start.toPoint())
for k in range(1,16):
    pt=start+(target-start)*(k/15);QTest.mouseMove(w,pt.toPoint(),20)
QTest.qWait(150);print('DROP OFFSET',[(n,find('markerDrop_qa2').property(n)) for n in ['splitOffset','rawOffset','lastX','lastY','x','y','width','height']],editor.property('text'));shot('marker-drop-preview.png')
QTest.mouseRelease(w,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,target.toPoint());QTest.qWait(150)
print('DROP RESULT',b.state['rowCount'],b.state['error'])
assert b.state['rowCount']==4,'QML marker drag must split a real cue'
assert b.document.project.settings['frame_markers']==[65],'original fixed marker must survive'
assert b.model.items[1]['lastFrame']==64 and b.model.items[2]['firstFrame']==65
shot('marker-split-success.png')
assert b.command('history.undo',{});QTest.qWait(100);assert b.state['rowCount']==3
# Drag the same immutable frame outside the target cue: must reject with no edit.
b.command('row.select',{'id':'qa3'});QTest.qWait(100)
editor3=find('chineseEditor_qa3');marker=find('fixedMarker_65');start=marker.mapToScene(QPoint(11,13));target=editor3.mapToScene(QPoint(60,int(editor3.property('height')/2)))
before=b.document.snapshot();history_before=len(b.history.undo_stack)
QTest.mousePress(w,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start.toPoint())
for k in range(1,16):QTest.mouseMove(w,(start+(target-start)*(k/15)).toPoint(),20)
QTest.qWait(100);shot('marker-outside-preview.png')
QTest.mouseRelease(w,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,target.toPoint());QTest.qWait(100)
assert b.document.snapshot()==before and len(b.history.undo_stack)==history_before
assert '不在可切分范围' in b.state['error'] and '句首' in b.state['error'] and '不能切分' in b.state['error']
shot('marker-outside-rejected.png');b.command('error.clear',{});w.setProperty('noticeText','');b.command('row.select',{'id':'qa2'})
# Actual edge drag: snap to previous last+1, one undo, no neighbour mutation.
b.command('row.setBoundary',{'id':'qa2','edge':'first','frame':46});QTest.qWait(100)
edge=find('cueEdge_first');start=edge.mapToScene(QPoint(5,13));tl=w.findChild(QObject,'frameTimeline');span=tl.property('viewLast')-tl.property('viewFirst');ruler=find('timelineRulerMouse');dx=3*ruler.property('width')/span;target=start-QPoint(int(dx-2),0)
old_previous=(b.model.items[0]['firstFrame'],b.model.items[0]['lastFrame']);h=len(b.history.undo_stack)
QTest.mousePress(w,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start.toPoint())
for k in range(1,8):QTest.mouseMove(w,(start+(target-start)*(k/7)).toPoint(),25)
QTest.mouseRelease(w,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,target.toPoint());QTest.qWait(150)
assert b.model.items[1]['firstFrame']==43 and len(b.history.undo_stack)==h+1
assert (b.model.items[0]['firstFrame'],b.model.items[0]['lastFrame'])==old_previous
# Both actually overlapping rows are marked; no automatic time-axis movement.
b.command('row.setBoundary',{'id':'qa2','edge':'first','frame':40});QTest.qWait(100)
assert find('cueRow_qa1').property('overlap') and find('cueRow_qa2').property('overlap')
shot('review-overlap.png');b.command('history.undo',{});QTest.qWait(100)
# Keyboard boundary auto-repeat is one editing transaction, not playback stepping.
edge=find('cueEdge_first');edge.forceActiveFocus();QTest.qWait(80);h=len(b.history.undo_stack);old_first=b.model.items[1]['firstFrame'];old_playhead=b.media['requestedFrame']
QTest.keyPress(w,Qt.Key.Key_Right)
for _ in range(5):app.sendEvent(w,QKeyEvent(QEvent.Type.KeyPress,Qt.Key.Key_Right,Qt.KeyboardModifier.NoModifier,'',True,1));QTest.qWait(30)
QTest.keyRelease(w,Qt.Key.Key_Right);QTest.qWait(100)
assert b.model.items[1]['firstFrame']==old_first+6 and len(b.history.undo_stack)==h+1
assert b.media['requestedFrame']==old_playhead
b.command('history.undo',{});QTest.qWait(100);assert b.model.items[1]['firstFrame']==old_first
# Timing input captures its cue ID: playback and even a later explicit selection cannot retarget it.
for field_name,value,edge_name,other_id,other_frame in [('firstFrameField','44','first','qa3',110),('lastFrameField','99','last','qa1',20)]:
    b.command('row.select',{'id':'qa2'});QTest.qWait(100);field=find(field_name);field.forceActiveFocus();QTest.keyClick(w,Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier);
    for char in value:QTest.keyClick(w,Qt.Key(ord(char)))
    assert field.property('editingId')=='qa2'
    b.command('media.seek',{'frame':other_frame});wait(lambda:b.media['displayedFrame']==other_frame);assert b.state['selectedId']=='qa2'
    original_other=next(row for row in b.model.items if row['id']==other_id).copy();b.command('row.select',{'id':other_id});QTest.qWait(100);QTest.keyClick(w,Qt.Key.Key_Return);QTest.qWait(100)
    changed=next(row for row in b.model.items if row['id']=='qa2');unchanged=next(row for row in b.model.items if row['id']==other_id)
    assert changed['firstFrame' if edge_name=='first' else 'lastFrame']==int(value)
    assert unchanged['firstFrame']==original_other['firstFrame'] and unchanged['lastFrame']==original_other['lastFrame']
    b.command('history.undo',{});QTest.qWait(100)
b.command('row.select',{'id':'qa2'});QTest.qWait(100)
# Long-hold repeats; release and actual window hide/deactivation stop immediately.
w.requestActivate();QTest.qWait(100);button=find('transportForwardOne');point=button.mapToScene(QPoint(15,15));initial=b.media['requestedFrame']
QTest.mousePress(w,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point.toPoint());QTest.qWait(650)
assert b.media['requestedFrame']>=initial+3
QTest.mouseRelease(w,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point.toPoint());QTest.qWait(120);stopped=b.media['requestedFrame'];QTest.qWait(300);assert b.media['requestedFrame']==stopped
QTest.mousePress(w,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point.toPoint());QTest.qWait(450);w.hide();QTest.qWait(150);stopped=b.media['requestedFrame'];QTest.qWait(300);assert b.media['requestedFrame']==stopped
w.show();w.requestActivate();QTest.mouseRelease(w,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point.toPoint());QTest.qWait(100)
# Upper and lower scrub gestures finish at a real decoded target, then synchronize.
rail=find('upperSeekMouse');start=rail.mapToScene(QPoint(20,15));target=rail.mapToScene(QPoint(int(rail.property('width')*.68),15));QTest.mousePress(w,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start.toPoint())
for k in range(1,15):QTest.mouseMove(w,(start+(target-start)*(k/14)).toPoint(),15)
assert b.media['scrubbing']
QTest.mouseRelease(w,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,target.toPoint());expected=b.media['requestedFrame'];wait(lambda:not b.media['scrubbing'] and b.media['displayedFrame']==expected)
# The lower timeline also owns a scrub gesture, not a detached decorative cursor.
ruler=find('timelineRulerMouse');start=ruler.mapToScene(QPoint(30,10));target=ruler.mapToScene(QPoint(int(ruler.property('width')*.45),10));QTest.mousePress(w,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start.toPoint())
for k in range(1,10):QTest.mouseMove(w,(start+(target-start)*(k/9)).toPoint(),15)
QTest.mouseRelease(w,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,target.toPoint());expected=b.media['requestedFrame'];wait(lambda:not b.media['scrubbing'] and b.media['displayedFrame']==expected)
b.command('media.seek',{'frame':65});wait(lambda:b.media['displayedFrame']==65)
# Real confirm-next and unresolved filter preserve full-timeline numbering.
b.command('row.select',{'id':'qa2'});QTest.qWait(100);confirm=find('confirmAndNext');point=confirm.mapToScene(QPoint(int(confirm.property('width')/2),int(confirm.property('height')/2)));QTest.mouseClick(w,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point.toPoint());QTest.qWait(100)
assert b.state['selectedId']=='qa3' and b.model.items[1]['confirmed']
filter_control=find('unconfirmedFilter');point=filter_control.mapToScene(QPoint(7,14));QTest.mouseClick(w,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point.toPoint());QTest.qWait(100)
assert b.state['view']['unconfirmedOnly'] and [row['number'] for row in b.model.items]==[1,3]
b.command('media.seek',{'frame':65});wait(lambda:b.media['displayedFrame']==65);assert b.media['hiddenCueCount']==1 and '被筛选隐藏' in find('presentedFrameCaption').property('text');shot('review-hidden-cue.png')
QTest.mouseClick(w,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point.toPoint());QTest.qWait(100);b.command('row.confirm',{'id':'qa2','value':False});b.command('row.select',{'id':'qa2'})
# Wrapped/newline text: hit the actual second-line caret; keep emoji and combining clusters whole.
editor=find('chineseEditor_qa2');QTest.keyClick(w,Qt.Key.Key_F2);assert editor.hasActiveFocus() and not editor.property('readOnly');old_text=b.model.items[1]['chinese'];multiline='第一行：这是一段用于验收换行的中文。\n第二行：👨‍👩‍👧‍👦与e\u0301必须保持完整，后面保留足够文字。';editor.setProperty('text',multiline);QTest.qWait(150)
offset=len(multiline[:multiline.index('👨')].encode('utf-16-le'))//2
editor.setProperty('cursorPosition',offset);QTest.qWait(80);r=editor.property('cursorRectangle');target=editor.mapToScene(QPoint(int(r.x()),int(r.y()+r.height()/2)));marker=find('fixedMarker_65');start=marker.mapToScene(QPoint(11,13))
QTest.mousePress(w,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start.toPoint())
for k in range(1,18):QTest.mouseMove(w,(start+(target-start)*(k/17)).toPoint(),15)
QTest.qWait(80);actual_offset=find('markerDrop_qa2').property('splitOffset');assert actual_offset==b.graphemeBoundary(multiline,actual_offset);assert actual_offset>=multiline.index('\n')
shot('marker-multiline-preview.png');QTest.mouseRelease(w,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,target.toPoint());QTest.qWait(100)
assert b.state['rowCount']==4 and b.model.items[1]['chinese']+b.model.items[2]['chinese']==multiline
assert any('👨‍👩‍👧‍👦' in row['chinese'] for row in b.model.items) and any('e\u0301' in row['chinese'] for row in b.model.items)
b.command('history.undo',{});QTest.qWait(100);b.command('row.edit',{'id':'qa2','text':old_text});QTest.qWait(100)
# Close cancellation is a real modal interaction and must keep unsaved work.
assert b.state['project']['dirty'];w.close();QTest.qWait(120);assert w.isVisible() and w.findChild(QObject,'closeDialog').property('visible')
cancel=find('closeCancel');point=cancel.mapToScene(QPoint(int(cancel.property('width')/2),int(cancel.property('height')/2)));QTest.mouseClick(w,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point.toPoint());QTest.qWait(100);assert w.isVisible() and not w.findChild(QObject,'closeDialog').property('visible')
# Partial export exposes real omissions before any file is written, and requires explicit review.
b.command('row.add',{'after':'qa1'});omitted=b.state['selectedId'];assert b.state['selection']['current']['firstFrame']==0
b.command('row.edit',{'id':omitted,'text':'QA：尚未定时，不得悄悄省略'});b.command('stage.set',{'stage':'export'});QTest.qWait(150)
assert find('exportOmissions').property('visible') and not find('exportDraftButton').property('enabled')
skip=find('skipMissingTimes');point=skip.mapToScene(QPoint(7,14));QTest.mouseClick(w,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point.toPoint());QTest.qWait(80);assert not find('exportDraftButton').property('enabled')
ack=find('omissionAcknowledged');point=ack.mapToScene(QPoint(7,14));QTest.mouseClick(w,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point.toPoint());QTest.qWait(80);assert find('exportDraftButton').property('enabled');shot('export-omissions-before-write.png')
b.command('history.undo',{});b.command('history.undo',{});b.command('stage.set',{'stage':'review'});b.command('row.select',{'id':'qa2'});QTest.qWait(100)
# Actual closed/opened stage and workspace routing, no fake results.
for width,height in [(1100,720),(760,900),(1040,600),(1400,940)]:
    w.resize(width,height);QTest.qWait(150);shot(f'review-{width}x{height}.png')
    if width==760:
        scroll=w.findChild(QObject,'makeScroll');flick=scroll.property('contentItem');print('SCROLL',[(n,flick.property(n)) for n in ['contentHeight','height','contentY']],scroll.property('availableHeight'));flick.setProperty('contentY',max(0,flick.property('contentHeight')-scroll.property('availableHeight')));QTest.qWait(100);print('NEXT',find('deleteCurrentCueButton').mapToScene(QPoint(0,0)));shot('review-760x900-scrolled-bottom.png');assert 150<find('deleteCurrentCueButton').mapToScene(QPoint(0,0)).y()<850;flick.setProperty('contentY',0)
    s=w.findChild(QObject,'workspaceSwitch');assert s.property('x')+s.property('width') <= s.parent().property('width')+1
w.setProperty('workspace','qc');shot('qc-1400x940.png');assert b.state['qc']['issues']==[]
w.setProperty('workspace','settings');shot('settings-1400x940.png')
w.setProperty('workspace','make');b.command('stage.set',{'stage':'export'});shot('export-1400x940.png')
b.command('stage.set',{'stage':'ocr'});shot('ocr-1400x940.png');assert not w.findChild(QObject,'frameTimeline').property('visible')
b.command('stage.set',{'stage':'review'});w.showMaximized();QTest.qWait(150);w.showNormal();w.resize(1400,940);shot('restored-1400x940.png')
(out/'qa-summary.json').write_text(json.dumps({**run_info,'status':'passed','finished_utc':datetime.now(timezone.utc).isoformat(),'set_current_labels_and_accessible_names':True,'qml_loaded':True,'real_decoded_frames':b.media['totalFrames'],'actual_frame':b.media['displayedFrame'],'inline_edit_immediate':True,'original_translation_preserved':True,'ime_preedit_not_committed':True,'ime_commit_written':True,'no_automatic_manual_lock':True,'marker_drag_actual_split':True,'marker_original_retained':True,'split_undo_restored':True,'outside_marker_rejected_without_mutation':True,'narrow_scroll_bottom_captured':True,'edge_drag_snap_one_undo':True,'longhold_release_and_deactivation_stop':True,'scrub_final_actual_frame_presented':True,'lower_scrub_final_presented':True,'focused_undo_redo_synced':True,'manual_lock_readonly_toggle':True,'close_cancel_preserves_window':True,'confirm_next_actual_button':True,'filter_stable_numbering':True,'multiline_caret_split_preserves_graphemes':True,'timing_fields_target_id_stable_during_seek_and_selection':True,'boundary_key_repeat_single_undo_not_playhead':True,'both_overlap_rows_visible':True,'filtered_active_cue_notice_visible':True,'export_omissions_shown_before_write':True,'sizes':[[1100,720],[760,900],[1040,600],[1400,940]],'ocr_timeline_hidden':True,'qc_has_no_synthetic_results':True},ensure_ascii=False,indent=2))
passed=True
b.shutdown();app.processEvents();print('QML QA completed',out)
