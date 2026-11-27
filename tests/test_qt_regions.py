"""Native dual-ROI contracts. Real Qt, worker lifecycle and bounded local FFmpeg.

OCR inference is mocked in lifecycle tests; separate review evidence uses the
actual local OCR engine. No provider, network, credentials or original edits.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from copy import deepcopy
from pathlib import Path
import hashlib
import subprocess
import threading
import time
from contextlib import contextmanager
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import Qt,QPoint,QPointF
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from desktop_qt.roi_dialog import RegionDialog,RoiCanvas
from test_qt_controller import application,bridge,wait_for,cue


@pytest.fixture
def region(bridge,tmp_path):
    video=tmp_path/'video.bin';video.write_bytes(b'local-video-v1')
    bridge._qc.update(inputPath=str(video),inputKind='hardsubbed',width=1920,height=1080,duration=5,previewSeconds=1.25,chineseRoi=None,sourceRoi=None)
    bridge._qc_preview=QImage(600,338,QImage.Format.Format_RGB32);bridge._qc_preview.fill(Qt.GlobalColor.black)
    dialog=RegionDialog(bridge);dialog.show()
    yield dialog
    dialog.reject()


def idle(application,bridge):wait_for(application,lambda:not bridge.jobs.active)


def test_source_pixel_edges_letterbox_resize_and_reverse_drag(application):
    canvas=RoiCanvas(1920,1080);canvas.resize(1000,700)
    rect=canvas.content_rect();assert rect.x()==pytest.approx(0,abs=1e-10) and rect.y()==pytest.approx(68.75)
    assert canvas.source_point(QPointF(10,10)) is None
    assert canvas.source_point(QPointF(-100,-100),clamp=True)==QPointF(0,0)
    assert canvas.source_point(rect.bottomRight())==QPointF(1920,1080)
    assert canvas.pixel_box(QPointF(100.8,90.2),QPointF(30.1,20.9))==[30,20,101,91]
    box=[123,256,1678,987]
    for w,h in ((1000,700),(401,800),(1600,400)):
        canvas.resize(w,h);display=canvas.display_box(box)
        assert canvas.pixel_box(canvas.source_point(display.topLeft()),canvas.source_point(display.bottomRight()))==box


def test_real_mouse_drag_numeric_controls_and_letterbox_start(region,application):
    canvas=region.canvas;region.resize(920,900);application.processEvents()
    rect=canvas.display_box([192,216,960,540]);start=rect.topLeft().toPoint();end=rect.bottomRight().toPoint()
    QTest.mousePress(canvas,Qt.MouseButton.LeftButton,pos=start);QTest.mouseMove(canvas,end);QTest.mouseRelease(canvas,Qt.MouseButton.LeftButton,pos=end)
    actual=[s.value() for s in region.controls['chinese']]
    # Integer widget positions can touch at most ceil(source/widget scale) pixels.
    assert all(abs(a-b)<=5 for a,b in zip(actual,[192,216,960,540]))
    before=actual[:];QTest.mousePress(canvas,Qt.MouseButton.LeftButton,pos=QPoint(1,1));QTest.mouseRelease(canvas,Qt.MouseButton.LeftButton,pos=end)
    if not canvas.content_rect().contains(QPointF(1,1)):assert [s.value() for s in region.controls['chinese']]==before
    region.controls['chinese'][0].setValue(199);assert canvas.boxes['chinese'][0]==199 and region.origins['chinese']=='user'


def test_candidates_require_assignment_then_confirmation(region,bridge,application,monkeypatch):
    import lyricsync.audit_service as service
    before=bridge.document.snapshot();main=threading.get_ident();threads=[]
    def candidates(path,*,settings,cancel):
        threads.append(threading.get_ident());assert settings['language']=='chi_sim+eng'
        return [{'box':[100,700,1700,900],'hits':5,'reason':'标题也可能入选','origin':'candidate','confirmed':False},
                {'box':[100,400,1700,600],'hits':3,'reason':'另一空间候选','confirmed':False}]
    monkeypatch.setattr(service,'propose_audit_regions',candidates)
    region.language.setText('chi_sim+eng');region.propose();idle(application,bridge)
    assert threads and threads[0]!=main and region.candidates.count()==2
    assert bridge._qc['chineseRoi'] is None and bridge._qc['sourceRoi'] is None
    region.candidates.setCurrentRow(0);region.assign_candidate('chinese');region.candidates.setCurrentRow(1);region.assign_candidate('source')
    assert bridge._qc['chineseRoi'] is None
    region.confirm();idle(application,bridge)
    assert not region.alive and bridge._qc['chineseRoi']=={'box':[100,700,1700,900],'origin':'candidate','confirmed':True}
    assert bridge._qc['sourceRoi']['box']==[100,400,1700,600]
    assert set(bridge._qc['sourceRoi'])=={'box','origin','confirmed'}
    assert bridge.document.snapshot()==before


def test_assignment_cancel_preserves_prior_rois_and_project(region,bridge):
    before=deepcopy(bridge._qc);document=bridge.document.snapshot()
    region.set_box('chinese',[10,20,300,400],'candidate');region.reject()
    assert bridge._qc==before and bridge.document.snapshot()==document


@pytest.mark.parametrize('outcome',['empty','failure'])
def test_empty_or_failure_never_confirms(region,bridge,application,monkeypatch,outcome):
    import lyricsync.audit_service as service
    def candidates(*args,**kwargs):
        if outcome=='failure':raise ValueError('controlled local OCR failure')
        return []
    monkeypatch.setattr(service,'propose_audit_regions',candidates);region.propose();idle(application,bridge)
    assert region.candidates.count()==0 and bridge._qc['chineseRoi'] is None
    assert ('未探测到' if outcome=='empty' else '失败') in region.status.text()
    assert region.detect.isEnabled()


@pytest.mark.parametrize('interrupt',['cancel','close','source','samepath','epoch','shutdown'])
def test_late_candidate_cannot_outlive_cancel_or_source(region,bridge,application,monkeypatch,tmp_path,interrupt):
    import lyricsync.audit_service as service
    entered=threading.Event();release=threading.Event()
    def candidates(*args,**kwargs):
        entered.set();assert release.wait(3)
        # Deliberately ignores cancel, matching last-recognize backend boundary.
        return [{'box':[100,100,400,300],'hits':7,'reason':'late'}]
    monkeypatch.setattr(service,'propose_audit_regions',candidates);region.propose();wait_for(application,entered.is_set)
    if interrupt=='cancel':region.cancel_pending()
    elif interrupt=='close':region.reject()
    elif interrupt=='source':
        other=tmp_path/'other.bin';other.write_bytes(b'other');bridge._qc['inputPath']=str(other);bridge._refresh(False)
    elif interrupt=='samepath':Path(bridge._qc['inputPath']).write_bytes(b'source-has-changed');bridge._refresh(False)
    elif interrupt=='epoch':bridge.epoch+=1;bridge._refresh(False)
    else:bridge.shutdown()
    release.set();idle(application,bridge)
    assert region.candidates.count()==0 and bridge._qc['chineseRoi'] is None
    if interrupt=='cancel':assert region.alive and region.detect.isEnabled()


def test_content_changed_with_preserved_stat_is_rejected_by_worker_hash(region,bridge,application,monkeypatch):
    import lyricsync.audit_service as service
    path=Path(bridge._qc['inputPath']);original=path.stat()
    def candidates(*args,**kwargs):
        path.write_bytes(b'local-video-v2');os.utime(path,ns=(original.st_atime_ns,original.st_mtime_ns))
        return [{'box':[1,1,9,9]}]
    monkeypatch.setattr(service,'propose_audit_regions',candidates);region.propose();idle(application,bridge)
    assert region.candidates.count()==0 and '发生变动' in region.status.text()
    assert bridge._qc['chineseRoi'] is None


def test_overlapping_or_zero_regions_cannot_confirm(region,bridge):
    region.set_box('chinese',[0,0,100,100],'user');region.set_box('source',[50,50,150,150],'user');region.confirm()
    assert region.alive and not bridge.jobs.active and '重叠' in region.status.text()
    region.set_box('chinese',[0,0,0,100],'user');region.confirm();assert '有效的中文区域' in region.status.text()
    assert bridge._qc['chineseRoi'] is None


def test_preview_late_result_dropped_after_dialog_close(region,bridge,application,monkeypatch):
    entered=threading.Event();release=threading.Event();old=bridge._qc_preview
    def frame(path,stamp,cancel):
        entered.set();assert release.wait(3);image=QImage(600,338,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.red)
        return {'width':1920,'height':1080,'duration':5},image,3.2
    monkeypatch.setattr(bridge,'_qc_preview_frame',frame);region.load_frame();wait_for(application,entered.is_set);region.reject();release.set();idle(application,bridge)
    assert bridge._qc_preview is old and bridge._qc['previewSeconds']==1.25


def test_qc_roi_command_raw_uses_real_render_and_candidate_temp_cleanup(bridge,application,tmp_path,monkeypatch):
    from lyricsync.audit_service import ffmpeg_executable
    import lyricsync.audit_service as service
    import desktop_qt.dialogs as dialogs
    from lyricsync.vision import Video
    video=tmp_path/'raw.mp4';ass=tmp_path/'overlay.ass'
    subprocess.run([ffmpeg_executable(),'-v','error','-f','lavfi','-i','color=black:s=320x180:r=10:d=1','-c:v','libx264','-pix_fmt','yuv420p',str(video)],check=True)
    ass.write_text('[Script Info]\nScriptType: v4.00+\nPlayResX: 320\nPlayResY: 180\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,DejaVu Sans,30,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\nDialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,ACTUAL ASS\n',encoding='utf-8')
    before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (video,ass)};document=bridge.document.snapshot();shown=[]
    bridge._qc.update(inputPath=str(video),width=320,height=180,duration=1)
    monkeypatch.setattr(dialogs,'confirm_regions',lambda b:shown.append((b._qc_preview.copy(),deepcopy(b._qc))))
    assert bridge.command('qc.roi',{'mode':'temporary','assPath':str(ass)})
    idle(application,bridge);assert shown and shown[0][1]['roiPreviewKind']=='temporary_render_precheck'
    image=shown[0][0];assert any(image.pixelColor(x,y).red()>100 for y in range(image.height()) for x in range(image.width()))
    seen=[];result=[]
    def propose(path,**kwargs):
        assert Path(path).is_file() and str(path)!=str(video);seen.append(path)
        assert Video(path).info['width']==320
        return [{'box':[10,20,200,70],'hits':1,'confirmed':False}]
    monkeypatch.setattr(service,'propose_audit_regions',propose)
    bridge.qc_propose_regions('eng',lambda value,error:result.append((value,error)));idle(application,bridge)
    assert result[0][1] is None and result[0][0][0]['confirmed'] is False
    assert seen and not Path(seen[0]).exists()
    assert before=={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (video,ass)} and bridge.document.snapshot()==document
    assert not bridge.command('qc.start',{'mode':'final'})  # current raw ROI cannot silently become hard-sub ROI


def test_split_refuses_ime_composition_without_mutation(bridge):
    cue(bridge);bridge.command('marker.add',{'frame':10});bridge.command('editor.activity',{'id':'m','active':True,'composing':True})
    before=bridge.document.snapshot();assert not bridge.command('row.split',{'id':'m','frame':10,'offsetUtf16':2})
    assert bridge.document.snapshot()==before and '输入法' in bridge.state['error']


@pytest.mark.parametrize('role',['video','ass'])
def test_start_rechecks_confirmed_roi_input_hash_before_ocr(bridge,application,tmp_path,monkeypatch,role):
    import lyricsync.audit_service as service
    cue(bridge);video=tmp_path/'video.mp4';video.write_bytes(b'original video');ass=tmp_path/'sub.ass';ass.write_text('original ASS')
    bridge._qc.update(inputPath=str(video),inputKind='raw',assPath=str(ass),chineseRoi={'box':[0,0,100,100],'confirmed':True,'origin':'user'},
                      roiInputVersions=service.fingerprint_inputs({'video':video,'ass':ass}))
    called=[];monkeypatch.setattr(service,'run_audit',lambda *a,**k:called.append(True))
    (video if role=='video' else ass).write_bytes(b'replaced content')
    assert bridge.command('qc.start',{'api':False,'mode':'temporary'});idle(application,bridge)
    assert not called and '重新载入并确认识别区域' in bridge.state['error'] and not bridge._qc['completed']


def test_raw_dimension_change_rejected_not_silently_remapped(bridge,application,tmp_path,monkeypatch):
    import lyricsync.audit_service as service
    import lyricsync.vision as vision
    video=tmp_path/'raw';video.write_bytes(b'raw');ass=tmp_path/'sub.ass';ass.write_bytes(b'ass');rendered=tmp_path/'rotated';rendered.write_bytes(b'rendered')
    bridge._qc.update(inputPath=str(video),inputKind='raw',assPath=str(ass));seen=[]
    @contextmanager
    def render(*args,**kwargs):yield rendered,{'kind':'temporary_render_precheck'}
    class Video:
        def __init__(self,path):self.info={'width':1080 if str(path)==str(rendered) else 1920,'height':1920 if str(path)==str(rendered) else 1080}
    monkeypatch.setattr(service,'rendered_input',render);monkeypatch.setattr(vision,'Video',Video)
    monkeypatch.setattr(service,'propose_audit_regions',lambda *a,**k:pytest.fail('must not OCR wrong coordinate system'))
    bridge.qc_propose_regions('eng',lambda value,error:seen.append((value,error)));idle(application,bridge)
    assert seen[0][0] is None and '分辨率或方向' in str(seen[0][1])


def test_small_available_client_scrolls_body_keeps_footer_reachable(region,application):
    from PySide6.QtWidgets import QDialogButtonBox
    region.resize(800,540);application.processEvents()
    assert region.width()==800 and region.height()==540
    ok=region.buttons.button(QDialogButtonBox.StandardButton.Ok)
    cancel=region.buttons.button(QDialogButtonBox.StandardButton.Cancel)
    for button in (ok,cancel):
        bottom=button.mapTo(region,button.rect().bottomRight()).y()
        assert 0<bottom<540 and button.isVisible()
    assert region.scroll.verticalScrollBar().maximum()>0
    region.scroll.verticalScrollBar().setValue(region.scroll.verticalScrollBar().maximum());application.processEvents()
    assert ok.mapTo(region,ok.rect().bottomRight()).y()<540
    assert region.canvas.source_point(region.canvas.content_rect().bottomRight())==QPointF(1920,1080)


@pytest.mark.parametrize('failure',[False,True])
def test_qc_new_source_clears_old_report_actions_only_after_success(bridge,application,tmp_path,monkeypatch,failure):
    old_report=tmp_path/'old-report.json';old_report.write_text('{"retained":"evidence"}')
    bridge._qc.update(inputPath='old-video',report={'cues':[{'id':'old'}]},cues=[{'cueId':'old'}],issues=[{'id':'old-issue'}],rescans=[{'id':'old-rescan'}],reportDirectory=str(tmp_path),lastRescan={'report':{}},completed=True)
    before=deepcopy(bridge._qc)
    def preview(*args):
        if failure:raise ValueError('controlled source read failure')
        image=QImage(320,180,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.black)
        return {'width':320,'height':180,'duration':1},image,.5
    monkeypatch.setattr(bridge,'_qc_preview_frame',preview)
    assert bridge.command('qc.open',{'path':str(tmp_path/'new-video')});idle(application,bridge)
    if failure:assert bridge._qc==before
    else:
        assert bridge._qc['cues']==[] and bridge._qc['issues']==[] and bridge._qc['rescans']==[]
        assert 'report' not in bridge._qc and 'reportDirectory' not in bridge._qc and 'lastRescan' not in bridge._qc
        assert not bridge.command('qc.rescan',{'cueId':'old'})
    assert old_report.read_text()=='{"retained":"evidence"}'


def test_cancelled_mode_preparation_restores_prior_report_and_roi(region,bridge,application,monkeypatch,tmp_path):
    import lyricsync.audit_service as service
    ass=tmp_path/'new.ass';ass.write_bytes(b'subtitle')
    bridge._qc.update(report={'cues':[{'id':'old'}]},cues=[{'cueId':'old'}],completed=True)
    before=deepcopy(bridge._qc)
    @contextmanager
    def cancelled(*args,**kwargs):raise InterruptedError('controlled render cancel');yield
    monkeypatch.setattr(service,'rendered_input',cancelled)
    bridge._prepare_qc_regions({'mode':'temporary','assPath':str(ass)});idle(application,bridge)
    assert bridge._qc==before


def test_hidden_initial_boxes_require_chinese_then_source(region,bridge):
    from PySide6.QtWidgets import QDialogButtonBox
    ok=region.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert region.canvas.boxes=={} and not region.show_regions.isChecked() and not ok.isEnabled()
    region.role.setCurrentIndex(1)
    assert region.role.currentData()=='chinese' and '先框选中文' in region.status.text()
    region.set_box('source',[0,0,300,100],'user')
    assert 'source' not in region.selected
    region.set_box('chinese',[0,100,300,200],'user')
    assert region.show_regions.isChecked() and list(region.canvas.boxes)==['chinese']
    assert not ok.isEnabled()
    region.set_box('source',[0,0,300,100],'user')
    assert ok.isEnabled()  # touching edges are not overlapping area
    region.show_regions.setChecked(False)
    assert region.canvas.boxes=={} and ok.isEnabled()
    assert bridge._qc['chineseRoi'] is None


def test_overlap_immediate_disable_then_separation_reenables(region):
    from PySide6.QtWidgets import QDialogButtonBox
    ok=region.buttons.button(QDialogButtonBox.StandardButton.Ok)
    region.set_box('chinese',[0,100,300,200],'user')
    region.set_box('source',[0,50,300,150],'user')
    assert '重叠' in region.status.text() and not ok.isEnabled()
    region.controls['source'][3].setValue(100)
    assert ok.isEnabled()
    region.preview_box('source',[0,50,300,101])
    assert '重叠' in region.status.text() and not ok.isEnabled()
    region.preview_box('source',[0,50,300,100])
    assert ok.isEnabled()


def test_drag_timeline_reads_actual_pts_and_preserves_pixel_rois(region,bridge,application,monkeypatch):
    seen=[]
    region.set_box('chinese',[17,700,1800,900],'user');region.set_box('source',[17,400,1800,600],'user')
    before=deepcopy(region.canvas.boxes)
    def frame(path,stamp,cancel):
        seen.append(stamp)
        image=QImage(600,338,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.blue)
        return {'width':1920,'height':1080,'duration':5},image,3.2
    monkeypatch.setattr(bridge,'_qc_preview_frame',frame)
    region.timeline.setValue(3217);region.timeline.sliderReleased.emit();idle(application,bridge)
    assert seen==[3.217] and region.timeline.value()==3200 and region.seek.value()==3.2
    assert '3.2' in region.actual.text() and region.canvas.boxes==before
    assert region.canvas.image.pixelColor(0,0)==Qt.GlobalColor.blue
    assert bridge._qc['chineseRoi'] is None


def test_timeline_real_video_decodes_and_preserves_regions(bridge,application):
    from lyricsync.vision import Video
    path=Path(__file__).parents[1]/'examples/synthetic_burned_zh.mp4'
    info,image,stamp=bridge._qc_preview_frame(str(path),.2,lambda:False)
    bridge._qc.update(inputPath=str(path),inputKind='hardsubbed',width=info['width'],height=info['height'],duration=info['duration'],previewSeconds=stamp,chineseRoi=None,sourceRoi=None)
    bridge._qc_preview=image
    dialog=RegionDialog(bridge);dialog.show()
    try:
        w,h=info['width'],info['height']
        dialog.set_box('chinese',[10,h*3//4,w-10,h-10],'user')
        dialog.set_box('source',[10,h//2,w-10,h*2//3],'user')
        before=deepcopy(dialog.canvas.boxes)
        target=min(info['duration']-.2,2.317)
        dialog.timeline.setValue(round(target*1000));dialog.timeline.sliderReleased.emit();idle(application,bridge)
        _,expected,actual=bridge._qc_preview_frame(str(path),round(target*1000)/1000,lambda:False)
        assert dialog.canvas.image==expected and dialog.seek.value()==pytest.approx(actual,abs=.0005)
        assert dialog.canvas.boxes==before and bridge._qc['chineseRoi'] is None
        assert '画面时间' in dialog.actual.text()
    finally:dialog.reject()
