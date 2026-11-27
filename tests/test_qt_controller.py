"""Headless Qt bridge contracts; not Windows GUI/IME/audio acceptance."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from pathlib import Path
import subprocess
import time
import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QVideoSink
from desktop_qt.controller import Bridge
from lyricsync.frames import FrameIndex
from lyricsync.model import Mapping, Translation
from lyricsync.project_document import load_document
from lyricsync.audit_service import ffmpeg_executable


@pytest.fixture(scope='module')
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def bridge(application,tmp_path):
    obj=Bridge(storage_dir=tmp_path/'recovery')
    yield obj
    obj.shutdown()
    deadline=time.monotonic()+3
    while obj.media_controller.worker.thread.is_alive() and time.monotonic()<deadline:
        application.processEvents();time.sleep(.005)


def wait_for(application,predicate,timeout=8):
    deadline=time.monotonic()+timeout
    while not predicate() and time.monotonic()<deadline:
        application.processEvents();time.sleep(.005)
    assert predicate(),'timed out waiting for actual Qt state'


def cue(bridge,text='你好世界'):
    p=bridge.document.project;p.video={'duration':3.0};p.translations=[Translation('t',text)]
    p.mappings=[Mapping('m',['t'],start=.5,end=1.5)]
    bridge.media_controller.index=FrameIndex([n*.1 for n in range(30)],3.)
    bridge.selected='m';bridge._changed()
    return p.mappings[0]


def test_empty_no_synthetic_data(bridge):
    assert bridge.state['rows']==[]
    assert bridge.state['qc']['issues']==[]
    assert not bridge.state['project']['dirty']
    assert bridge.state['settings']['subtitlePreview'] is True


def test_edit_is_immediate_confirmation_is_separate(bridge):
    row=cue(bridge)
    assert bridge.command('row.edit',{'id':'m','text':'改过的中文'})
    assert row.text_override=='改过的中文' and row.human_edited and not row.confirmed
    assert bridge.document.project.translations[0].text=='你好世界'
    assert bridge.command('row.confirm',{'id':'m','value':True})
    assert row.confirmed and row.text_override=='改过的中文'
    assert bridge.command('history.undo',{})
    assert not bridge.document.project.mappings[0].confirmed
    assert bridge.command('history.undo',{})
    assert bridge.model.items[0]['chinese']=='你好世界'


def test_marker_outside_rejected_without_mutation(bridge):
    row=cue(bridge);bridge.command('marker.add',{'frame':2})
    before=bridge.document.snapshot();undo=len(bridge.history.undo_stack)
    assert not bridge.command('row.split',{'id':'m','frame':2,'offsetUtf16':2})
    assert bridge.document.snapshot()==before
    assert len(bridge.history.undo_stack)==undo
    assert '超出可切分范围（F7–F15）' in bridge.state['error']
    assert '句首 F6 处无法切分' in bridge.state['error']


def test_graphemes_utf16_and_fixed_marker_split(bridge):
    cue(bridge,'你👨‍👩‍👧‍👦好e\u0301啊')
    assert bridge.graphemeBoundary('A😀B',2) in (1,3)
    assert bridge.graphemeBoundary('e\u0301x',1) in (0,2)
    assert bridge.command('marker.add',{'frame':10})
    assert not bridge.command('row.split',{'id':'m','frame':10,'offsetUtf16':3})
    assert bridge.command('row.split',{'id':'m','frame':10,'offsetUtf16':12})
    assert len(bridge.document.project.mappings)==2
    assert bridge.document.project.settings['frame_markers']==[10]
    assert bridge.model.items[0]['chinese']=='你👨‍👩‍👧‍👦'
    assert bridge.model.items[1]['chinese']=='好e\u0301啊'


def test_invalid_boundary_preserves_value(bridge):
    row=cue(bridge);before=(row.start,row.end)
    assert not bridge.command('row.setBoundary',{'id':'m','edge':'first','frame':20})
    assert (row.start,row.end)==before
    bridge.media_controller._state['displayedFrame']=8
    bridge.media_controller._state['requestedFrame']=24
    assert bridge.command('row.setCurrent',{'id':'m','edge':'first'})
    assert row.start==pytest.approx(.7)  # actual displayed, not pending target 24


def test_save_recovery_and_export_are_distinct(application,bridge,tmp_path):
    cue(bridge);revision=bridge.document.revision
    bridge._save(False)
    wait_for(application,lambda:bridge._save_running is None)
    assert bridge.state['project']['dirty']
    assert bridge.document.saved_revision!=revision
    recovery=list((tmp_path/'recovery').glob('*.recovery.lyria'))
    assert recovery and load_document(recovery[0]).revision==revision
    assert bridge.command('project.save',{'path':str(tmp_path/'project.lyria')})
    wait_for(application,lambda:bridge._save_running is None)
    assert not bridge.state['project']['dirty']
    assert bridge.state['project']['unexported']
    assert load_document(tmp_path/'project.lyria').saved_revision==revision


def test_new_project_clears_media_and_stale_state(bridge):
    cue(bridge);bridge.media_controller._state.update(ready=True,displayedFrame=8)
    bridge.command('project.new',{})
    assert bridge.state['closeDialog']['visible']
    assert bridge.command('close.resolve',{'choice':'discard'})
    assert bridge.state['rows']==[] and not bridge.media['ready']
    assert bridge.media['displayedFrame']==0


def test_row_model_updates_not_reset_for_text(bridge):
    cue(bridge);events=[];bridge.model.modelReset.connect(lambda:events.append('reset'))
    bridge.model.dataChanged.connect(lambda *args:events.append('change'))
    bridge.command('row.edit',{'id':'m','text':'保持稳定行'})
    assert events==['change']


def test_real_sink_exact_seek_tail_and_latest_request(application,bridge,tmp_path):
    video=tmp_path/'test.mp4'
    subprocess.run([ffmpeg_executable(),'-loglevel','error','-f','lavfi','-i','testsrc2=size=160x90:rate=30000/1001',
                    '-frames:v','45','-c:v','libx264','-g','30','-pix_fmt','yuv420p',str(video)],check=True)
    sink=QVideoSink();bridge.attachVideoSink(sink)
    assert bridge.command('media.open',{'path':str(video)})
    wait_for(application,lambda:bridge.media['displayedFrame']==1)
    assert sink.videoFrame().isValid() and bridge.media['totalFrames']==45
    for frame in (40,2,43,4,45):assert bridge.command('media.seek',{'frame':frame})
    wait_for(application,lambda:bridge.media['displayedFrame']==45)
    assert bridge.media['requestedFrame']==45
    assert bridge.media['positionSeconds']==pytest.approx(44*1001/30000)
    assert sink.videoFrame().startTime()==round(44*1001/30000*1e6)
    assert bridge.command('media.step',{'delta':-1})
    wait_for(application,lambda:bridge.media['displayedFrame']==44)
    assert bridge.command('media.step',{'delta':-5})
    wait_for(application,lambda:bridge.media['displayedFrame']==39)


def test_explicit_manual_lock_not_auto_protection(bridge):
    cue(bridge)
    bridge.command('row.edit',{'id':'m','text':'先修改'})
    assert bridge.model.items[0]['protected'] and not bridge.model.items[0]['locked']
    assert bridge.command('row.edit',{'id':'m','text':'继续输入'})
    assert bridge.command('row.lock',{'id':'m','value':True})
    assert not bridge.command('row.edit',{'id':'m','text':'不能修改'})
    assert bridge.model.items[0]['chinese']=='继续输入'
    assert bridge.command('row.lock',{'id':'m','value':False})
    assert bridge.command('row.edit',{'id':'m','text':'现在可以'})


def test_sequential_long_gop_reuses_decode_session(application,bridge,tmp_path):
    video=tmp_path/'long-gop.mp4'
    subprocess.run([ffmpeg_executable(),'-loglevel','error','-f','lavfi','-i','testsrc2=size=320x180:rate=60',
                    '-frames:v','180','-c:v','libx264','-g','180','-keyint_min','180','-sc_threshold','0','-pix_fmt','yuv420p',str(video)],check=True)
    bridge.command('media.open',{'path':str(video)})
    wait_for(application,lambda:bridge.media['displayedFrame']==1)
    begin=time.monotonic()
    for frame in range(2,62):
        assert bridge.command('media.seek',{'frame':frame})
        wait_for(application,lambda frame=frame:bridge.media['displayedFrame']==frame)
    elapsed=time.monotonic()-begin
    assert bridge.media['decoderSeeks']<=3,bridge.media
    assert bridge.media['decodedFrames']<80,bridge.media
    print({'sequential_60_frames_seconds':elapsed,'decoder_seeks':bridge.media['decoderSeeks'],'decoded_frames':bridge.media['decodedFrames']})


def test_counted_failure_marks_saved_document_dirty_and_portable(application,bridge,tmp_path,monkeypatch):
    from lyricsync import api
    from lyricsync.api_client import ApiRequestError
    from lyricsync.storage import Cache
    bridge.configure_api(bridge.api_settings,'not-a-real-unit-test-key',False)
    target=tmp_path/'portable.lyria';bridge._save(True,str(target))
    wait_for(application,lambda:bridge._save_running is None)
    assert not bridge.state['project']['dirty']
    def failed(cache,key,**kwargs):
        call=cache.reserve_call('mock-counted-failure',50);cache.finish_call(call,'failed')
        raise ApiRequestError('network_error')
    monkeypatch.setattr(api,'test_connection',failed)
    assert bridge.command('api.test',{})
    wait_for(application,lambda:not bridge.state['task']['running'])
    assert len(bridge.document.project.api_records)==1
    assert bridge.state['project']['dirty']
    before=bridge.document.project.api_records.copy()
    bridge.command('history.undo',{})
    assert bridge.document.project.api_records==before  # no refund through undo
    bridge._save(True,str(target));wait_for(application,lambda:bridge._save_running is None)
    portable=load_document(target)
    separate=Cache(tmp_path/'isolated-no-sidecar.sqlite')
    try:
        separate.restore_records(portable.project.api_records)
        assert len(separate.records())==1
    finally:separate.close()


def test_scrub_presents_intermediate_without_stale_final(application,bridge,monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QImage
    m=bridge.media_controller;m.index=FrameIndex([n*.01 for n in range(100)],1.)
    m._state.update(ready=True,displayedFrame=1,requestedFrame=1,totalFrames=100)
    picture=QImage(16,9,QImage.Format.Format_RGB888);picture.fill(0xff103040)
    def slow(request):
        QTimer.singleShot(60,lambda:m._present(request,picture,m.index.stamp(request.frame),{}))
    monkeypatch.setattr(m.worker,'seek',slow)
    m.scrub(2,begin=True)
    for frame in range(3,22):
        m.scrub(frame)
        deadline=time.monotonic()+.02
        while time.monotonic()<deadline:application.processEvents();time.sleep(.002)
        if frame==17:
            assert m.snapshot()['displayedFrame']>1
            assert m.snapshot()['requestedFrame']>m.snapshot()['displayedFrame']
    m.seek(60)  # a final strict seek invalidates all outstanding scrub results
    wait_for(application,lambda:m.snapshot()['displayedFrame']==60)
    deadline=time.monotonic()+.15
    while time.monotonic()<deadline:application.processEvents();time.sleep(.002)
    assert m.snapshot()['displayedFrame']==60
    assert m.snapshot()['requestedFrame']==60
    assert not m.snapshot()['scrubbing']


def test_vfr_index_and_final_frame_use_decoded_pts(application,bridge,tmp_path):
    video=tmp_path/'vfr.mp4'
    subprocess.run([ffmpeg_executable(),'-loglevel','error','-f','lavfi','-i','testsrc2=size=160x90:rate=60:duration=2',
                    '-vf',"select='if(lt(n,60),not(mod(n,2)),not(mod(n,3)))'",'-fps_mode','vfr',
                    '-c:v','libx264','-g','120','-pix_fmt','yuv420p',str(video)],check=True)
    import av
    with av.open(str(video)) as container:
        pts=[float(f.pts*f.time_base) for f in container.decode(video=0)]
    assert len({round(b-a,6) for a,b in zip(pts,pts[1:])})>1
    bridge.command('media.open',{'path':str(video)});wait_for(application,lambda:bridge.media['displayedFrame']==1)
    index=bridge.media_controller.index
    assert index.stamps==pytest.approx(pts)
    assert 'frame_stamps' not in bridge.document.project.video and 'frame_pts' not in bridge.document.project.video
    for frame in (len(pts),len(pts)-1,31,30,1):
        bridge.command('media.seek',{'frame':frame});wait_for(application,lambda frame=frame:bridge.media['displayedFrame']==frame)
        assert bridge.media['positionSeconds']==pytest.approx(pts[frame-1])


def test_async_layout_uses_export_positions(application,bridge):
    cue(bridge,'同一布局参数')
    bridge.document.project.video.update(width=1920,height=1080)
    bridge.document.project.settings.update(subtitle_position='custom',subtitle_height_percent=70,font_size=40)
    bridge._changed();bridge.media_controller._state['positionSeconds']=.6
    wait_for(application,lambda:bridge._layout_revision==bridge.document.revision)
    rows=bridge.media['subtitleRows']
    assert len(rows)==1 and rows[0]['text']=='同一布局参数'
    assert rows[0]['yNormalized']==pytest.approx(.7)
    assert rows[0]['fontSizeNormalized']==pytest.approx(40/1080)


def test_api_text_captions_literal(bridge):
    from desktop_qt.dialogs import PlainLabel
    from PySide6.QtCore import Qt
    label=PlainLabel('<img src="https://invalid.example/secret">')
    assert label.textFormat()==Qt.TextFormat.PlainText
    assert label.text().startswith('<img')


def test_confirm_next_preserves_order_and_does_not_move_on_failure(bridge):
    cue(bridge)
    p=bridge.document.project;p.translations.extend([Translation('t2','第二句'),Translation('t3','第三句')])
    p.mappings.extend([Mapping('m2',['t2'],start=1.5,end=2.,confirmed=True),Mapping('m3',['t3'])]);bridge._changed()
    assert bridge.command('row.confirmNext',{'id':'m'})
    assert bridge.selected=='m2'  # already confirmed rows are not silently skipped
    assert bridge.command('row.confirmNext',{'id':'m2'})
    assert bridge.selected=='m3'
    assert not bridge.command('row.confirmNext',{'id':'m3'})
    assert bridge.selected=='m3' and not p.mappings[2].confirmed


def test_filter_keeps_original_numbers_and_actual_frame_follow(bridge):
    cue(bridge)
    p=bridge.document.project;p.translations.extend([Translation('t2','第二句'),Translation('t3','第三句')])
    p.mappings.extend([Mapping('m2',['t2'],start=1.5,end=2.,confirmed=True),Mapping('m3',['t3'],start=2.,end=2.5)]);bridge._changed()
    assert bridge.command('view.filter',{'unconfirmedOnly':True})
    assert [r['number'] for r in bridge.model.items]==[1,3]
    bridge._frame_presented(22,2.1)
    assert bridge.selected=='m3'
    assert bridge.command('editor.activity',{'id':'m3','active':True,'composing':True})
    bridge._frame_presented(8,.7)
    assert bridge.selected=='m3'
    bridge.command('editor.activity',{'id':'old','active':False,'composing':False})
    assert bridge._editor_active
    bridge.command('editor.activity',{'id':'m3','active':False,'composing':False})
    bridge._frame_presented(8,.7)
    assert bridge.selected=='m'
    bridge._hold_cue_selection=True
    bridge._frame_presented(22,2.1)
    assert bridge.selected=='m'  # cue±5 padding must not steal the target row


def test_resume_context_does_not_survive_edit_or_project_change(bridge):
    called=[];bridge._set_api_resume(lambda limit:called.append(limit))
    bridge._changed()
    assert not bridge.command('api.resume',{})
    assert called==[]
    bridge._set_api_resume(lambda limit:called.append(limit));bridge._alignment={'plan':'old'};bridge._qc['issues']=[{'id':'old'}]
    bridge._new()
    assert bridge._api_resume is None and bridge._alignment=={} and bridge._qc['issues']==[]
    assert not bridge.command('api.resume',{})
    assert called==[]


def test_export_cannot_overwrite_saved_project_or_hardlink(application,bridge,tmp_path):
    cue(bridge);bridge.command('row.confirm',{'id':'m','value':True})
    path=tmp_path/'saved.lyria';bridge._save(True,str(path));wait_for(application,lambda:bridge._save_running is None)
    before=path.read_bytes()
    assert not bridge.command('export.ass',{'path':str(path),'overwrite':True})
    alias=tmp_path/'alias.ass';os.link(path,alias)
    assert not bridge.command('export.ass',{'path':str(alias),'overwrite':True})
    assert path.read_bytes()==before and alias.read_bytes()==before
    assert not bridge.command('ktv.convert',{'source':str(path),'target':str(alias),'overwrite':True})
    assert path.read_bytes()==before


def test_partial_export_is_never_marked_current_formal_export(application,bridge,tmp_path):
    cue(bridge)
    p=bridge.document.project;p.translations.append(Translation('unknown','缺时间'))
    p.mappings.append(Mapping('unknown',['unknown']))
    bridge._changed();old=bridge.document.exported_revision
    assert bridge.command('export.ass',{'path':str(tmp_path/'partial.ass'),'skipMissingTimes':True})
    wait_for(application,lambda:not bridge.state['task']['running'])
    assert (tmp_path/'partial.ass').is_file()
    assert (tmp_path/'partial_遗漏清单.json').is_file()
    assert bridge.document.exported_revision==old
    assert bridge.state['project']['unexported']


def test_omission_report_alias_cannot_overwrite_project(application,bridge,tmp_path):
    cue(bridge);project=tmp_path/'protected.lyria';bridge._save(True,str(project));wait_for(application,lambda:bridge._save_running is None)
    report=tmp_path/'partial_遗漏清单.json';os.link(project,report);before=project.read_bytes()
    assert not bridge.command('export.ass',{'path':str(tmp_path/'partial.ass'),'skipMissingTimes':True,'overwrite':True})
    assert project.read_bytes()==before and not (tmp_path/'partial.ass').exists()


def test_qc_report_status_is_actual_not_requested_mode(bridge,monkeypatch):
    captured=[];monkeypatch.setattr(bridge,'_budget_dialog',lambda budget:captured.append(budget))
    report={'inputs':{'video':'test.mp4'},'cues':[],'source_cues':[],'issues':[],
            'stage':{'kind':'temporary_render_precheck','label':'临时预检'},'coverage':{},
            'api':{'text':{'status':'budget_exhausted','budget':{'used':50,'limit':50,'status':'budget_exhausted'}}}}
    bridge._display_qc_report(report,'test.mp4',bridge.storage_dir/'qc')
    assert bridge.state['qc']['apiStatus']=='budget_exhausted'
    assert '语义核对暂停' in bridge.state['qc']['status']
    assert bridge.state['qc']['reportKind']=='临时预检'
    assert captured and bridge._api_resume is not None


def test_import_retains_hash_and_can_relocate_missing_source(application,bridge,tmp_path):
    import hashlib
    original=tmp_path/'translation.txt';original.write_text('你好\n再见\n',encoding='utf-8')
    digest=hashlib.sha256(original.read_bytes()).hexdigest()
    assert bridge.command('input.import',{'role':'translation','path':str(original)})
    wait_for(application,lambda:not bridge.state['task']['running'])
    assert bridge.document.project.settings['input_hashes']['translation']==digest
    moved=tmp_path/'moved.txt';original.rename(moved)
    assert bridge.command('project.relocate',{'paths':{'translation':str(moved)}})
    wait_for(application,lambda:not bridge.state['task']['running'])
    assert bridge.document.project.inputs['translation']==str(moved)
    assert bridge.model.items[0]['chinese']=='你好'


def test_qc_region_dialog_accepts_explicit_unset_regions(application,bridge):
    from PySide6.QtCore import QTimer
    from desktop_qt.dialogs import confirm_regions
    bridge._qc.update(width=1920,height=1080,duration=5,chineseRoi=None,sourceRoi=None)
    QTimer.singleShot(0,lambda:application.activeModalWidget().reject())
    confirm_regions(bridge)
    assert bridge._qc['chineseRoi'] is None


def test_save_failure_and_cancel_close_never_emit_quit(application,bridge,tmp_path,monkeypatch):
    import desktop_qt.controller as controller
    cue(bridge);bridge.path=str(tmp_path/'failed.lyria');quit_events=[];bridge.quitRequested.connect(lambda:quit_events.append(True))
    def fail(snapshot,path):raise OSError('controlled test failure')
    monkeypatch.setattr(controller,'write_document',fail)
    assert not bridge.requestClose()
    assert bridge.command('close.resolve',{'choice':'save'})
    wait_for(application,lambda:bridge._save_running is None)
    assert bridge.state['project']['dirty'] and quit_events==[]
    assert bridge.state['project']['autosaveStatus']=='保存失败'
    assert bridge.command('close.resolve',{'choice':'cancel'})
    assert quit_events==[] and not bridge.state['closeDialog']


def test_inflight_save_cannot_mark_newer_edit_saved(application,bridge,tmp_path,monkeypatch):
    import threading
    import desktop_qt.controller as controller
    cue(bridge);captured=bridge.document.revision;released=threading.Event();real=controller.write_document
    def delayed(snapshot,path):
        assert released.wait(4)
        return real(snapshot,path)
    monkeypatch.setattr(controller,'write_document',delayed)
    target=tmp_path/'inflight.lyria'
    try:
        assert bridge.command('project.save',{'path':str(target)})
        assert bridge.command('row.edit',{'id':'m','text':'保存开始后继续修改'})
        assert bridge.document.revision>captured
        released.set();wait_for(application,lambda:bridge._save_running is None)
        assert bridge.document.saved_revision==captured
        assert bridge.state['project']['dirty']
        assert load_document(target).project.mappings[0].text_override is None
    finally:released.set()


def test_qc_captures_current_chinese_and_original_baseline(application,bridge,tmp_path,monkeypatch):
    import lyricsync.audit_service as service
    from lyricsync.storage import load_project
    from lyricsync.editing import mapping_text
    cue(bridge,'导入原译文');bridge.command('row.edit',{'id':'m','text':'人工修订后的中文'})
    translation=tmp_path/'baseline.txt';translation.write_text('导入原译文\n',encoding='utf-8')
    source=tmp_path/'source.txt';source.write_text('Original lyric\n',encoding='utf-8')
    bridge.document.project.inputs.update(translation=str(translation),ktv=str(source))
    bridge._qc.update(inputPath=str(tmp_path/'video.mp4'),chineseRoi={'box':[0,0,100,100],'origin':'user','confirmed':True})
    seen={}
    def fake(video,reference,output,**kwargs):
        snapshot=load_document(kwargs['project_path']).project
        seen.update(current=mapping_text(snapshot,snapshot.mappings[0]),baseline=Path(reference).read_text(encoding='utf-8'),
                    use_current=kwargs['settings']['audit_use_current_project'],source=kwargs['ktv_path'])
        return {'inputs':{'video':video},'cues':[],'source_cues':[],'issues':[],'api':{'text':{'status':'disabled'}},'coverage':{},'stage':{'label':'本地测试'}}
    monkeypatch.setattr(service,'run_audit',fake)
    before=bridge.document.snapshot()
    assert bridge.command('qc.start',{'api':False})
    wait_for(application,lambda:not bridge.state['task']['running'])
    assert seen=={'current':'人工修订后的中文','baseline':'导入原译文\n','use_current':True,'source':str(source)}
    assert bridge.document.snapshot()==before


def test_qc_local_cancel_does_not_mutate_live_project(application,bridge,tmp_path,monkeypatch):
    import lyricsync.audit_service as service
    cue(bridge);translation=tmp_path/'ref.txt';translation.write_text('你好世界\n',encoding='utf-8')
    bridge.document.project.inputs['translation']=str(translation)
    bridge._qc.update(inputPath=str(tmp_path/'video.mp4'),chineseRoi={'box':[0,0,100,100],'origin':'user','confirmed':True})
    def cancelled(*args,**kwargs):raise InterruptedError('controlled QC cancellation')
    monkeypatch.setattr(service,'run_audit',cancelled)
    before=bridge.document.snapshot();assert bridge.command('qc.start',{'api':False})
    wait_for(application,lambda:not bridge.state['task']['running'])
    assert bridge.document.snapshot()==before
    assert not bridge.state['qc']['completed']


def test_focused_text_edits_form_one_undo_transaction(bridge):
    cue(bridge);before=len(bridge.history.undo_stack)
    bridge.command('editor.activity',{'id':'m','active':True,'composing':False})
    bridge.command('row.edit',{'id':'m','text':'一'})
    bridge.command('row.edit',{'id':'m','text':'一二'})
    bridge.command('row.edit',{'id':'m','text':'一二三'})
    assert len(bridge.history.undo_stack)==before+1
    bridge.command('history.undo',{})
    assert bridge.model.items[0]['chinese']=='你好世界'
    bridge.command('row.edit',{'id':'m','text':'新'})
    bridge.command('row.edit',{'id':'m','text':'新的组'})
    assert len(bridge.history.undo_stack)==before+1


def test_ime_commit_has_its_own_undo_boundary(bridge):
    cue(bridge)
    bridge.command('editor.activity',{'id':'m','active':True,'composing':False})
    bridge.command('row.edit',{'id':'m','text':'输入法前已生效正文'})
    bridge.command('editor.activity',{'id':'m','active':True,'composing':True})
    # Preedit never reaches the project; only the final Qt committed string does.
    assert bridge.model.items[0]['chinese']=='输入法前已生效正文'
    bridge.command('editor.activity',{'id':'m','active':True,'composing':False})
    bridge.command('row.edit',{'id':'m','text':'输入法前已生效正文提交中文'})
    bridge.command('history.undo',{})
    assert bridge.model.items[0]['chinese']=='输入法前已生效正文'
    bridge.command('history.undo',{})
    assert bridge.model.items[0]['chinese']=='你好世界'


def test_boundary_repeat_group_is_single_undo(bridge):
    row=cue(bridge);original=row.start
    bridge.command('history.begin',{'label':'边界按住微调'})
    for frame in (7,8,9):assert bridge.command('row.setBoundary',{'id':'m','edge':'first','frame':frame})
    bridge.command('history.end',{})
    bridge.command('history.undo',{})
    assert bridge.document.project.mappings[0].start==original


def test_same_path_changed_media_is_rejected_without_rewriting_document(bridge,tmp_path):
    row=cue(bridge);row.confirmed=True
    path=str(tmp_path/'same-video.mp4');bridge.document.project.inputs['video']=path
    bridge.document.project.video.update(content_hash='original-sha256',width=160,height=90)
    before=bridge.document.snapshot()
    media=bridge.media_controller;media.path=path
    index=FrameIndex([n*.1 for n in range(40)],4.)
    errors=[];bridge.message.connect(errors.append)
    media._accept_index(media.epoch,index,{'content_hash':'replacement-sha256','width':1920,'height':1080,'duration':4.,'has_audio':False,'fps':10.})
    assert bridge.document.snapshot()==before
    assert not bridge.media['ready'] and media.index is None and media.path==''
    assert '当前视频与工程原本使用的视频不一致' in errors[-1]
    assert bridge.document.project.mappings[0].confirmed


def test_ui_snapshot_excludes_duplicate_rows_and_keeps_immutable_selection(bridge):
    cue(bridge)
    assert 'rows' not in bridge.uiState and 'rowsRevision' not in bridge.uiState
    assert bridge.state['rows'][0]['chinese']=='你好世界'
    ui=bridge.uiState;ui['selection']['current']['chinese']='external mutation'
    assert bridge.uiState['selection']['current']['chinese']=='你好世界'
    class NoIteration(list):
        def __iter__(self):raise AssertionError('playhead-only refresh must not traverse mappings')
    graph=bridge.document.project.alignment_graph;timeline=graph.timeline
    graph.timeline=NoIteration(timeline)
    try:
        bridge._refresh(False)
        assert bridge.uiState['confirmedCount']==0
    finally:
        graph.timeline=timeline


def qc_report(tmp_path):
    return {'inputs':{'video':str(tmp_path/'evidence-video.mp4')},'cues':[],
            'source_cues':[],'unobserved_cues':[{'id':'unobserved','text':'','expected_text':'仅参考译文',
                'first_frame':None,'last_frame':None,'representative_frame':8,'start':None,'end':None,
                'boundary_status':'needs_review','content_status':'not_observed','evidence_frames':[]}],
            'issues':[],'coverage':{},'warnings':[],'scope':'仅测试片段','api':{'text':{'status':'disabled'}},
            'stage':{'label':'本地证据'}}


def test_qc_unobserved_review_is_persisted_without_becoming_pass(application,bridge,tmp_path):
    import json
    report=qc_report(tmp_path);directory=tmp_path/'qc-report'
    bridge._display_qc_report(report,report['inputs']['video'],directory)
    before=bridge.document.snapshot()
    shown=bridge.uiState['qc']['cues'][0]
    assert shown['text']=='' and shown['expected_text']=='仅参考译文'
    assert shown['firstFrame'] is None and shown['lastFrame'] is None
    assert shown['representativeFrame']==8
    assert bridge.command('qc.review',{'cueId':'unobserved','status':'skipped','note':'画面证据仍不清楚'})
    wait_for(application,lambda:not bridge.uiState['task']['running'])
    saved=json.loads((directory/'report.json').read_text(encoding='utf-8'))
    assert saved['unobserved_cues'][0]['review_status']=='skipped'
    assert saved['unobserved_cues'][0]['text']==''
    assert not saved['coverage']['complete_video_verified']
    assert bridge.document.snapshot()==before
    assert bridge.uiState['qc']['cues'][0]['reviewStatus']=='skipped'


def test_qc_local_rescan_keeps_parent_and_forwards_explicit_languages(application,bridge,tmp_path,monkeypatch):
    from copy import deepcopy
    import lyricsync.audit_service as service
    import desktop_qt.dialogs as dialogs
    report=qc_report(tmp_path);directory=tmp_path/'qc-report';seen={}
    bridge._display_qc_report(report,report['inputs']['video'],directory)
    original=bridge.document.snapshot()
    zh={'box':[0,50,100,100],'origin':'user','confirmed':True}
    source={'box':[0,0,100,50],'origin':'user','confirmed':True}
    def fake(parent,output,**kwargs):
        seen.update(kwargs);child=deepcopy(parent);child['unobserved_cues']=[]
        child['cues']=[{'id':'child','text':'新识别候选','first_frame':7,'last_frame':10}]
        entry={'id':'local-attempt','status':'needs_review','report_path':str(output/'local-attempt'/'report.json')}
        parent['rescans']=[entry]
        return {'parent_report':parent,'report':child,'rescan':entry}
    texts=[];monkeypatch.setattr(service,'rescan_audit',fake)
    monkeypatch.setattr(dialogs,'show_text',lambda title,text,**kwargs:texts.append(text))
    assert bridge.command('qc.rescan',{'cueId':'unobserved','firstFrame':4,'lastFrame':20,
        'chineseRoi':zh,'sourceRoi':source,'chineseLanguage':'chi_tra','sourceLanguage':'eng'})
    wait_for(application,lambda:not bridge.uiState['task']['running'])
    assert seen['first_frame']==4 and seen['last_frame']==20
    assert seen['chinese_language']=='chi_tra' and seen['source_language']=='eng'
    assert seen['chinese_roi']==zh and seen['source_roi']==source
    assert bridge.uiState['qc']['cues'][0]['text']==''
    assert bridge.uiState['qc']['rescans'][0]['status']=='needs_review'
    assert bridge.document.snapshot()==original
    assert '新识别候选' in texts[0] and '原质检报告保持不变' in texts[0]


def test_import_warning_review_cancel_does_not_acknowledge_or_confirm(bridge,monkeypatch):
    import desktop_qt.dialogs as dialogs
    row=cue(bridge);bridge.document.project.warnings=['实际解析警告 <img src="https://invalid.example/a">']
    before=bridge.document.snapshot();seen=[]
    monkeypatch.setattr(dialogs,'review_import_warnings',lambda warnings:(seen.extend(warnings),False)[1])
    assert bridge.command('project.reviewWarnings',{})
    assert bridge.document.snapshot()==before
    assert not row.confirmed and not bridge.document.project.settings.get('parser_acknowledged',False)
    assert seen==bridge.document.project.warnings
    monkeypatch.setattr(dialogs,'review_import_warnings',lambda warnings:True)
    assert bridge.command('project.reviewWarnings',{})
    assert bridge.document.project.settings['parser_acknowledged']
    assert not row.confirmed and bridge.document.project.warnings==seen
    assert bridge.uiState['export']['warningsAcknowledged']
    assert bridge.command('history.undo',{})
    assert not bridge.document.project.settings.get('parser_acknowledged',False)


@pytest.mark.parametrize('missing_video',[False,True])
def test_open_document_without_loadable_video_retires_previous_pts(application,bridge,tmp_path,missing_video):
    from lyricsync.project_document import ProjectDocument,write_document
    from lyricsync.model import Project
    from PySide6.QtGui import QImage
    cue(bridge);old_index=bridge.media_controller.index
    media=bridge.media_controller;media.path=str(tmp_path/'previous.mp4')
    media._state.update(ready=True,displayedFrame=8,requestedFrame=8,totalFrames=30)
    media._last_image=QImage(16,9,QImage.Format.Format_RGB888)
    replacement=ProjectDocument(project=Project())
    replacement.project.translations=[Translation('other-t','新文档')]
    replacement.project.mappings=[Mapping('other-m',['other-t'],start=.2,end=.8)]
    if missing_video:replacement.project.inputs['video']=str(tmp_path/'missing.mp4')
    target=tmp_path/'replacement.lyria';write_document(replacement.snapshot(explicit_save=True),target)
    assert bridge.command('project.open',{'path':str(target)})
    assert bridge.command('close.resolve',{'choice':'discard'})
    wait_for(application,lambda:bridge.document.id==replacement.id)
    assert media.index is None and old_index is not media.index
    assert media.path=='' and media._last_image is None and not bridge.media['ready']
    assert bridge.media['displayedFrame']==0 and bridge.media['totalFrames']==0
    assert bridge.uiState['selection']['current']['firstFrame']==0
    assert not bridge.command('media.seek',{'frame':8})
    assert bridge.document.project.mappings[0].start==.2


def test_real_long_gop_reverse_window_reuses_decoded_predecessors(application,bridge,tmp_path):
    video=tmp_path/'reverse-long-gop.mp4'
    subprocess.run([ffmpeg_executable(),'-loglevel','error','-f','lavfi','-i','testsrc2=size=320x180:rate=60',
                    '-frames:v','180','-c:v','libx264','-g','180','-keyint_min','180','-sc_threshold','0','-pix_fmt','yuv420p',str(video)],check=True)
    sink=QVideoSink();bridge.attachVideoSink(sink)
    assert bridge.command('media.open',{'path':str(video)})
    wait_for(application,lambda:bridge.media['displayedFrame']==1)
    bridge.command('media.seek',{'frame':150});wait_for(application,lambda:bridge.media['displayedFrame']==150)
    import av,hashlib
    from PySide6.QtGui import QImage
    expected={}
    with av.open(str(video)) as reference:
        for number,decoded in enumerate(reference.decode(video=0),1):
            if 90<=number<=149:expected[number]=hashlib.sha256(decoded.to_ndarray(format='rgb24').tobytes()).hexdigest()
    before=bridge.media['decoderSeeks']
    for frame in range(149,89,-1):
        assert bridge.command('media.seek',{'frame':frame})
        wait_for(application,lambda:bridge.media['displayedFrame']==frame)
        assert sink.videoFrame().startTime()==round(bridge.media_controller.index.stamp(frame)*1e6)
        actual=sink.videoFrame().toImage().convertToFormat(QImage.Format.Format_RGB888)
        assert hashlib.sha256(bytes(actual.constBits())).hexdigest()==expected[frame]
        assert bridge.media['cacheBytes']<=96*1024*1024
        assert bridge.media['nativeCacheBytes']<=32*1024*1024
        assert bridge.media['nativeCacheFrames']<=11
    assert bridge.media['decoderSeeks']-before<=6  # not 60 GOP re-decodes
    assert 0<bridge.media['nativeCachePeakBytes']<=32*1024*1024
    assert bridge.media['rgbCachePeakBytes']<=96*1024*1024


def test_play_waits_for_pending_exact_seek_before_starting_clock(bridge,monkeypatch):
    from PySide6.QtGui import QImage
    media=bridge.media_controller;media.index=FrameIndex([n*.04 for n in range(100)],4.)
    media.metadata={'has_audio':False};media._state.update(ready=True,displayedFrame=10,requestedFrame=10,totalFrames=100)
    requests=[];monkeypatch.setattr(media.worker,'seek',requests.append)
    assert bridge.command('media.seek',{'frame':80});final=requests[-1]
    assert bridge.command('media.play',{})
    assert media.snapshot()['playPending'] and not media.snapshot()['playing'] and not media.timer.isActive()
    image=QImage(8,8,QImage.Format.Format_RGB888);image.fill(0)
    media._present(final,image,media.index.stamp(80),{})
    assert media.snapshot()['playing'] and not media.snapshot()['playPending']
    assert media._clock_stamp==pytest.approx(3.16)
    assert media.snapshot()['displayedFrame']==80
    media._tick()
    assert media.snapshot()['requestedFrame']>=80  # never re-request old frame10 / wait for clock to catch up
    media.pause()


@pytest.mark.parametrize('cancel',['pause','toggle','seek','source'])
def test_pending_play_is_cancelled_without_late_autostart(bridge,monkeypatch,cancel):
    from PySide6.QtGui import QImage
    media=bridge.media_controller;media.index=FrameIndex([n*.04 for n in range(100)],4.)
    media.metadata={'has_audio':False};media._state.update(ready=True,displayedFrame=10,requestedFrame=10,totalFrames=100)
    requests=[];monkeypatch.setattr(media.worker,'seek',requests.append)
    bridge.command('media.seek',{'frame':80});old=requests[-1]
    bridge.command('media.play',{});assert media.snapshot()['playPending']
    if cancel=='pause':bridge.command('media.pause',{})
    elif cancel=='toggle':bridge.command('media.play',{})
    elif cancel=='seek':bridge.command('media.seek',{'frame':20})
    else:media.clear()
    image=QImage(8,8,QImage.Format.Format_RGB888);image.fill(0)
    media._present(old,image,3.16,{})
    assert not media.snapshot()['playing'] and not media.snapshot()['playPending']
    assert not media.timer.isActive()
    if cancel in ('seek','source'):assert media.snapshot()['displayedFrame']!=80

@pytest.mark.parametrize('fmt,expected',[('srt','00:00:00,500 --> 00:00:01,500'),('lrc','[00:00.50]')])
def test_text_subtitle_export_uses_existing_project_validation(application,bridge,tmp_path,fmt,expected):
    row=cue(bridge,'中文测试')
    row.confirmed=True
    bridge._changed()
    target=tmp_path/f'字幕.{fmt}'
    assert bridge.command('export.ass',{'path':str(target),'format':fmt})
    wait_for(application,lambda:target.exists() and not bridge._task['running'])
    assert expected in target.read_text(encoding='utf-8-sig')
    assert '中文测试' in target.read_text(encoding='utf-8-sig')

def test_confirm_current_does_not_pause_or_seek(application,bridge):
    row=cue(bridge,'确认当前句')
    bridge.media_controller._state.update(playing=True, displayedFrame=8, requestedFrame=8, positionSeconds=.7)
    before=bridge.media_controller.snapshot()
    assert bridge.command('row.confirm',{'id':row.id,'value':True})
    after=bridge.media_controller.snapshot()
    assert row.confirmed
    assert after['playing'] is True
    assert after['displayedFrame']==before['displayedFrame']
    assert after['requestedFrame']==before['requestedFrame']

@pytest.mark.parametrize('key,stored',[('playerRounded','player_rounded'),('playerGlass','player_glass')])
def test_player_visual_preferences_are_independent(bridge,key,stored):
    assert bridge.state['settings'][key] is True
    assert bridge.command('settings.set',{'key':key,'value':False})
    assert bridge.document.project.settings[stored] is False
    assert bridge.state['settings'][key] is False
    assert not bridge.command('settings.set',{'key':key,'value':'false'})
    assert bridge.document.project.settings[stored] is False

def test_insert_blank_before_or_after_preserves_order_and_undo(bridge):
    row=cue(bridge,'首句')
    assert bridge.command('row.add',{'before':row.id})
    first=bridge.document.project.mappings[0]
    assert first.id != row.id and bridge.document.project.mappings[1].id == row.id
    assert bridge.command('history.undo',{})
    assert [m.id for m in bridge.document.project.mappings] == [row.id]
    assert bridge.command('row.add',{'after':row.id})
    assert bridge.document.project.mappings[0].id == row.id
    assert len(bridge.document.project.mappings) == 2

def test_manual_timing_pairs_do_not_fill_between_pairs_or_stop(bridge):
    bridge.document.project.video={'duration':3.0}
    bridge.media_controller.index=FrameIndex([n*.1 for n in range(30)],3.)
    bridge.media_controller._state.update(ready=True,playing=True,displayedFrame=3,requestedFrame=3,positionSeconds=.2)
    assert bridge.command('timeline.manualMode',{'enabled':True})
    for frame in (3,6,10,15):
        bridge.media_controller._state['displayedFrame']=frame
        bridge.media_controller._state['requestedFrame']=frame
        assert bridge.command('timeline.manualMark',{})
        assert bridge.media_controller._state['playing'] is True
    rows=bridge.document.project.mappings
    assert len(rows)==2
    assert (rows[0].start,rows[0].end)==pytest.approx((.2,.6))
    assert (rows[1].start,rows[1].end)==pytest.approx((.9,1.5))
    assert rows[0].end < rows[1].start
    assert bridge.state['manualTiming']['startFrame']==0
    assert bridge.command('history.undo',{})
    assert len(bridge.document.project.mappings)==1


def test_manual_timing_reverse_and_cancel_preserve_existing_data(bridge):
    bridge.document.project.video={'duration':3.0}
    bridge.media_controller.index=FrameIndex([n*.1 for n in range(30)],3.)
    bridge.media_controller._state.update(ready=True,displayedFrame=10,requestedFrame=10,positionSeconds=.9)
    assert bridge.command('timeline.manualMode',{'enabled':True})
    assert bridge.command('timeline.manualMark',{})
    bridge.media_controller._state['displayedFrame']=5
    assert not bridge.command('timeline.manualMark',{})
    assert bridge.state['manualTiming']['startFrame']==10
    assert not bridge.document.project.mappings
    assert bridge.command('timeline.manualCancel',{})
    assert bridge.state['manualTiming']['startFrame']==0
    assert bridge.command('timeline.manualMode',{'enabled':False})
    assert not bridge.command('timeline.manualMark',{})

def test_delete_current_is_undoable_without_seeking(bridge):
    row=cue(bridge,'保留原始译文')
    bridge.media_controller._state.update(displayedFrame=8,requestedFrame=8,positionSeconds=.7)
    assert bridge.command('row.delete',{'id':row.id})
    assert not bridge.document.project.mappings
    assert bridge.document.project.translations[0].text=='保留原始译文'
    assert bridge.media_controller.snapshot()['displayedFrame']==8
    assert bridge.command('history.undo',{})
    assert bridge.document.project.mappings[0].id==row.id
    assert bridge.document.project.translations[0].text=='保留原始译文'


def test_delete_split_fragment_does_not_block_surviving_export(bridge,tmp_path):
    from lyricsync.editing import split_mapping
    from lyricsync.export import validate_project,export_project
    row=cue(bridge,'前半后半')
    a,b=split_mapping(bridge.document.project,row.id,2,1.0)
    a.confirmed=True;b.confirmed=True
    bridge.selected=b.id;bridge._changed()
    assert bridge.command('row.delete',{'id':b.id})
    p=bridge.document.project
    assert not validate_project(p)
    target=tmp_path/'single.srt';export_project(p,target)
    assert '前半' in target.read_text(encoding='utf-8-sig')
    assert '后半' not in target.read_text(encoding='utf-8-sig')
    assert bridge.command('history.undo',{})
    assert len(bridge.document.project.mappings)==2

def test_create_manual_range_and_default_duration_without_pause(bridge):
    bridge.document.project.video={'duration':10.0}
    bridge.media_controller.index=FrameIndex([n*.1 for n in range(100)],10.)
    bridge.media_controller._state.update(ready=True,playing=True,displayedFrame=11,requestedFrame=11,positionSeconds=1.)
    assert bridge.command('timeline.createRange',{'first':20,'last':29})
    assert (bridge.document.project.mappings[0].start,bridge.document.project.mappings[0].end)==pytest.approx((1.9,2.9))
    assert bridge.media_controller.snapshot()['playing']
    assert bridge.command('timeline.createAtPlayhead',{})
    assert (bridge.document.project.mappings[0].start,bridge.document.project.mappings[0].end)==pytest.approx((1.,4.))
    assert len(bridge.document.project.mappings)==2
    assert not bridge.command('timeline.createRange',{'first':90,'last':80})
    assert len(bridge.document.project.mappings)==2
    assert bridge.command('history.undo',{})
    assert len(bridge.document.project.mappings)==1

def test_ocr_requires_explicit_region_and_auto_roi_uses_candidate(application,bridge,monkeypatch):
    import lyricsync.vision as vision
    p=bridge.document.project
    p.inputs['video']='example.mp4';p.video={'width':320,'height':180,'duration':3.0}
    bridge.media_controller._state.update(ready=True,displayedFrame=1,totalFrames=30)
    assert bridge.state['roi'] is None
    assert not bridge.command('ocr.start',{})
    assert '选择' in bridge.state['error'] or '框选' in bridge.state['error']
    monkeypatch.setattr(vision,'make_ocr',lambda settings:object())
    monkeypatch.setattr(vision,'propose_regions',lambda path,provider,cancel:[
        {'box':[0,0,22,16],'hits':7,'examples':['TV'],'watermark':True},
        {'box':[20,60,300,130],'hits':5,'examples':['subtitle']}])
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import QListWidget, QDialogButtonBox
    choices=[]
    timer=QTimer();timer.setInterval(10)
    def confirm():
        modal=QApplication.activeModalWidget()
        listing=modal.findChild(QListWidget,'regionSuggestionChoices') if modal else None
        if listing:
            timer.stop()
            choices.extend(listing.item(i).checkState() for i in range(listing.count()))
            modal.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
    timer.timeout.connect(confirm);timer.start()
    assert bridge.command('roi.auto',{})
    wait_for(application,lambda:not bridge._task['running'])
    timer.stop()
    assert choices==[Qt.CheckState.Unchecked,Qt.CheckState.Checked]
    assert p.settings['roi']==[20,60,300,130]
    assert bridge.state['roi']['y']==pytest.approx(60/180)


def test_automatic_roi_does_not_overwrite_manual_change(application,bridge,monkeypatch):
    import lyricsync.vision as vision
    import threading
    p=bridge.document.project
    p.inputs['video']='example.mp4';p.video={'width':320,'height':180,'duration':3.0}
    bridge.media_controller._state.update(ready=True,displayedFrame=1,totalFrames=30)
    gate=threading.Event()
    monkeypatch.setattr(vision,'make_ocr',lambda settings:object())
    monkeypatch.setattr(vision,'propose_regions',lambda path,provider,cancel:(gate.wait(2),[{'box':[20,60,300,130]}])[1])
    assert bridge.command('roi.auto',{})
    assert bridge.command('roi.set',{'x':.1,'y':.3,'width':.8,'height':.4})
    expected=p.settings['roi'][:];gate.set()
    wait_for(application,lambda:not bridge._task['running'])
    assert p.settings['roi']==expected
    assert '视频或选区已变动' in bridge.state['error']

def scan_with_one_visible_line(snapshot, *args, **kwargs):
    from lyricsync.model import Visual
    visual = Visual('seen', '字幕', .6, 1.6, [30, 95, 145, 124], .94, 3)
    snapshot.visuals = [visual]
    snapshot.replace_timeline([Mapping('new', ['t'], visual_ids=[visual.id], start=.6, end=1.6)])


def test_ocr_candidate_does_not_auto_send_ai_or_mutate_main(application,bridge,monkeypatch,tmp_path):
    import lyricsync.pipeline as pipeline
    from lyricsync import ai_config
    media=tmp_path/'example.mp4';media.write_bytes(b'test media')
    p=bridge.document.project;p.inputs['video']=str(media);p.video={'width':320,'height':180,'duration':3.0}
    p.settings['roi']=[10,80,300,170];p.settings['api_auto_review']=True
    p.translations=[Translation('t','可靠中文')];p.mappings=[Mapping('m',['t'],start=.5,end=1.5)]
    bridge._api_key='mock-key';bridge._api_scope=ai_config.credential_scope(bridge.api_settings);bridge._api_text_consents.add(bridge._api_scope)
    monkeypatch.setattr(pipeline,'run',scan_with_one_visible_line)
    calls=[];monkeypatch.setattr(bridge,'_api_alignment',lambda payload,one=False,maximum=None:calls.append(payload))
    before=p.to_dict()
    assert bridge.command('ocr.start',{})
    wait_for(application,lambda:bridge._candidate_job is None and not bridge._task['running'] and not getattr(bridge,'_ocr_settings_pending',False))
    assert calls==[]
    assert p.to_dict()==before
    assert len(bridge.document.source_candidates)==1
    assert bridge.document.project.settings['api_auto_review'] is True
    assert bridge.document.project.mappings[0].id=='m'
    assert not bridge.document.project.mappings[0].confirmed


def test_ocr_does_not_send_alignment_when_disabled(application,bridge,monkeypatch,tmp_path):
    import lyricsync.pipeline as pipeline
    media=tmp_path/'example.mp4';media.write_bytes(b'test media')
    p=bridge.document.project;p.inputs['video']=str(media);p.video={'width':320,'height':180,'duration':3.0}
    p.settings['roi']=[10,80,300,170];p.settings['api_enabled']=False
    p.translations=[Translation('t','可靠中文')];p.mappings=[Mapping('m',['t'],start=.5,end=1.5)]
    monkeypatch.setattr(pipeline,'run',scan_with_one_visible_line)
    calls=[];monkeypatch.setattr(bridge,'_api_alignment',lambda *args,**kwargs:calls.append(args))
    before=p.to_dict()
    assert bridge.command('ocr.start',{})
    wait_for(application,lambda:bridge._candidate_job is None and not bridge._task['running'] and not getattr(bridge,'_ocr_settings_pending',False))
    assert len(bridge.document.source_candidates)==1
    assert p.to_dict()==before
    application.processEvents()
    assert calls==[]

def test_multiple_selection_merges_adjacent_and_rejects_gaps(bridge):
    p=bridge.document.project
    p.video={'duration':5.0};p.translations=[Translation(str(i),f'第{i}句') for i in range(3)]
    p.mappings=[Mapping(str(i),[str(i)],start=float(i),end=float(i+1)) for i in range(3)]
    bridge.selected='0';bridge._changed()
    assert bridge.command('row.multiToggle',{'id':'0','selected':True})
    assert bridge.command('row.multiToggle',{'id':'2','selected':True})
    ids=bridge.state['multiSelectedIds'];assert set(ids)=={'0','2'}
    assert not bridge.command('row.merge',{'id':'0','ids':ids})
    assert len(p.mappings)==3 and '相邻' in bridge.state['error']
    assert bridge.command('row.multiToggle',{'id':'1','selected':True})
    ids=bridge.state['multiSelectedIds']
    assert bridge.command('row.merge',{'id':'0','ids':ids})
    assert len(bridge.document.project.mappings)==1
    assert bridge.state['multiSelectedIds']==[]
    assert bridge.command('history.undo',{})
    assert len(bridge.document.project.mappings)==3

def test_split_later_cue_and_undo_keep_playhead_context(bridge):
    p=bridge.document.project;p.video={'duration':6}
    p.translations=[Translation('a','第一句'),Translation('b','第二句内容')]
    p.mappings=[Mapping('a',['a'],start=0,end=1),Mapping('b',['b'],start=2,end=4)]
    bridge.media_controller.index=FrameIndex([n*.1 for n in range(60)],6.)
    bridge.media_controller._state.update(ready=True,displayedFrame=31,requestedFrame=31,positionSeconds=3.)
    bridge.selected='b';bridge._changed();bridge.command('marker.add',{'frame':31})
    seen=[];bridge.stateChanged.connect(lambda:seen.append(bridge.state.get('selectedId')))
    assert bridge.command('row.split',{'id':'b','frame':31,'offsetUtf16':2})
    assert bridge.state['selectedId'] in {m.id for m in p.mappings[1:]}
    assert 'a' not in seen
    assert bridge.media_controller.snapshot()['displayedFrame']==31
    assert bridge.command('history.undo',{})
    assert bridge.state['selectedId']=='b'
    assert bridge.media_controller.snapshot()['displayedFrame']==31

def test_dragging_subtitle_updates_export_position_and_undo(bridge):
    from lyricsync.export import layout_entries,placement_options
    cue(bridge,'可拖动字幕')
    p=bridge.document.project;p.video.update(width=1000,height=500)
    p.settings.update(subtitle_position='custom',subtitle_height_percent=75,subtitle_horizontal_percent=50,font_size=20)
    bridge._changed()
    assert bridge.command('subtitle.move',{'dx':.1,'dy':-.1})
    assert p.settings['subtitle_horizontal_percent']==pytest.approx(60)
    assert p.settings['subtitle_height_percent']==pytest.approx(65)
    layout=layout_entries([(0.,1.,'可拖动字幕')],(1000,500),20,**placement_options(p))
    assert layout[0]['x']==600 and layout[0]['y']==325
    assert bridge.command('history.undo',{})
    assert bridge.document.project.settings['subtitle_horizontal_percent']==50
    assert bridge.document.project.settings['subtitle_height_percent']==75

def test_dragging_top_aligned_subtitle_preserves_top_anchor(bridge):
    from lyricsync.export import layout_entries,placement_options
    cue(bridge,'顶部歌词')
    p=bridge.document.project;p.video.update(width=1000,height=500)
    p.settings.update(subtitle_position='top',font_size=20)
    before=layout_entries([(0.,1.,'顶部歌词')],(1000,500),20,**placement_options(p))[0]
    assert before['alignment']==8
    assert bridge.command('subtitle.move',{'dx':.1,'dy':.1})
    after=layout_entries([(0.,1.,'顶部歌词')],(1000,500),20,**placement_options(p))[0]
    assert after['alignment']==8
    assert after['x']==before['x']+100
    assert after['y']==before['y']+50


@pytest.fixture(autouse=True)
def accepted_settings_for_downstream_ocr_contracts(monkeypatch):
    # These older suites exercise work AFTER a user's settings acceptance.
    # Actual modal acceptance/cancel/reentrancy is covered in dev8 entry tests.
    monkeypatch.setattr('desktop_qt.dialogs.ocr_settings', lambda *args, **kwargs: True)
