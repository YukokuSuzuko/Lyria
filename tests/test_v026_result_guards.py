"""Ordinary completion failures and commits that race a changing editor session."""
from copy import deepcopy
from threading import Event

import pytest

from desktop_qt.candidate_dialog import CandidateDialog
from lyricsync.frames import FrameIndex
from lyricsync.model import Lyric, Mapping, Project, Translation, Visual
from test_qt_entry_initialization_dev5 import bridge, ready, settled, wait
from test_v026_ocr_completion import run_scan


@pytest.mark.parametrize('condition',['timed','manual','locked'])
def test_existing_main_opens_result_without_mutating_any_cue(bridge,tmp_path,monkeypatch,condition):
    ready(bridge)
    row = Mapping('keep',[],text_override='手调文本')
    if condition == 'timed':
        row.start,row.end=.33,.83
    elif condition == 'manual':
        row.human_edited=True
    else:
        bridge.document.project.settings['manual_locked_ids']=[row.id]
    bridge.document.project.mappings=[row]
    bridge._changed()
    before=deepcopy(row)
    ids=run_scan(bridge,tmp_path,monkeypatch,['字幕'])
    assert bridge.document.main_timeline==[before]
    assert len(ids)==1 and ids[0]==bridge.document.source_candidates[-1]['id']
    assert bridge.stage=='review' and bridge._candidate_task['resultStatus']=='retained'


@pytest.mark.parametrize('condition,kind', [('no_record','empty'),('no_visuals','empty'),
                                            ('install_failed','failed'),('load_failed','failed')])
def test_ordinary_empty_and_failure_have_visible_outcomes(bridge,tmp_path,monkeypatch,condition,kind):
    ready(bridge)
    seen=[]
    bridge.notice.connect(lambda level,message:seen.append(message))
    bridge.message.connect(seen.append)
    if condition=='no_record':
        monkeypatch.setattr('lyricsync.candidate_tracks.candidate_from_project',lambda *a:None)
    elif condition=='install_failed':
        def denied(*a,**kw):raise ValueError('synthetic storage failure')
        monkeypatch.setattr(bridge,'_candidate_install',denied)
    elif condition=='load_failed':
        def lost(record, *, on_result, **kw):
            on_result(dict(status='retained',reason='duplicate',applied=0,blocked=0,applied_timed=0))
        monkeypatch.setattr(bridge,'_candidate_install',lost)
    ids=run_scan(bridge,tmp_path,monkeypatch,[] if condition=='no_visuals' else ['歌词'])
    assert not ids and not bridge.document.main_timeline
    assert bridge._candidate_task['resultStatus']==kind and seen[-1] == bridge._candidate_task['label']
    assert not bridge._candidate_job and '正在' not in seen[-1]
    if kind=='empty':
        assert not bridge.document.source_candidates and bridge.stage=='import'
        assert bridge._candidate_task['scanCounts']['tracks']==int(condition=='no_record')


def test_saved_record_survives_comparison_window_load_failure(bridge,tmp_path,monkeypatch):
    ready(bridge)
    bridge.document.project.mappings=[Mapping('manual',[],text_override='Keep',start=.4,end=1.2)]
    bridge._changed()
    def fail(*a):raise RuntimeError('synthetic dialog loading error')
    monkeypatch.setattr(CandidateDialog,'__init__',fail)
    before=deepcopy(bridge.document.main_timeline)
    assert not run_scan(bridge,tmp_path,monkeypatch,['字幕'])
    assert bridge.document.main_timeline==before and len(bridge.document.source_candidates)==1
    assert bridge._candidate_task['resultStatus']=='failed' and '字幕菜单' in bridge.state['error']
    assert bridge.stage=='review' and bridge.state['candidateComparisonAvailable']


@pytest.mark.parametrize('change',['selection','checks','lock','edit','frame','session','closed'])
def test_racing_apply_preserves_contents_and_history(bridge,monkeypatch,change):
    from test_qt_source_candidates import candidate
    import desktop_qt.candidate_workspace as workspace
    ready(bridge)
    bridge.document.project.mappings=[Mapping('a',[],text_override='A',start=.2,end=1),
                                      Mapping('b',[],text_override='B',start=1.2,end=2)]
    bridge._changed(); bridge.selected='b'
    record=candidate(bridge)
    real=workspace.prepare_apply
    entered,release=Event(),Event()
    def deferred(*a,**kw):
        entered.set()
        assert release.wait(5)
        return real(*a,**kw)
    monkeypatch.setattr(workspace,'prepare_apply',deferred)
    dialog=CandidateDialog(bridge,record)
    dialog.commit()
    try:
        wait(entered.is_set)
        if change=='selection':bridge.command('review.select',{'id':'a'})
        elif change=='checks':dialog.check_visible(False)
        elif change=='lock':bridge.command('row.lock',{'id':'a','value':True})
        elif change=='edit':bridge._editor_active=True
        elif change=='frame':bridge.media_controller.index=FrameIndex([i*.095 for i in range(41)],4.)
        elif change=='session':bridge.epoch+=1
        elif change=='closed':dialog.reject()
        before=deepcopy(bridge.document.project.to_dict())
        depth=len(bridge.history.undo_stack)
    finally:release.set()
    settled(bridge)
    assert bridge.document.project.to_dict()==before and len(bridge.history.undo_stack)==depth
    bridge._editor_active=False
    dialog.reject()


def test_weak_reading_between_adjacent_originals_stays_unpaired_and_auditable():
    from lyricsync.ocr_correspondence import match_visual_project
    p=Project(lyrics=[Lyric('l1','Bright blue skies',None,None),Lyric('l2','Another day',None,None)],
              visuals=[Visual('v1','Bright blue skies',0,.8,[0,0,120,20],.97,6),
                       Visual('noise','X9',.85,1.15,[10,0,40,20],.70,3),
                       Visual('v2','Another day',1.2,1.9,[0,0,120,20],.97,6)],
              translations=[Translation('t1','蓝天'),Translation('t2','又一天')])
    match_visual_project(p)
    assert len(p.visuals)==3
    assert any(r.visual_ids==['noise'] and not r.translation_ids for r in p.mappings)
    assert all('noise' not in r.visual_ids for r in p.mappings if r.translation_ids)


def test_subframe_vfr_cue_displays_and_plays_its_containing_frame(bridge):
    ready(bridge)
    bridge.media_controller.index=FrameIndex([0.,.041,.138,.18,.251],.30)
    row=Mapping('sub',[],start=.052,end=.093,text_override='짧은')
    bridge.document.project.mappings=[row];bridge.selected='sub';bridge._changed()
    assert bridge.media_controller.index.frame_span(row.start,row.end)==(2,2)
    assert (bridge.state['rows'][0]['firstFrame'],bridge.state['rows'][0]['lastFrame'])==(2,2)


@pytest.mark.parametrize('outcome',['cancel','apply','locked_during_review'])
def test_candidate_to_translation_desk_is_detached_and_undoable(bridge,monkeypatch,outcome):
    from desktop_qt.ocr_capsule_pairing_dialog import OcrCapsulePairingDialog
    from lyricsync.candidate_tracks import capture_source,candidate_from_project
    ready(bridge)
    p=bridge.document.project
    p.translations=[Translation('tr','已有的译文')]
    p.mappings=[Mapping('seed',['tr'],method='import'),Mapping('locked',[],text_override='保留',start=2,end=3,locked=True)]
    bridge._changed()
    capture=capture_source(bridge.document,'ocr',bridge.media_controller.index)
    generated=Project.from_dict(p.to_dict())
    generated.visuals=[Visual('v','Scene lyrics',.2,.9,[1,2,100,25],.96,4)]
    generated.mappings=[Mapping('scan',['tr'],visual_ids=['v'],start=.2,end=.9)]
    record=candidate_from_project(capture,generated)
    bridge._candidate_install(record,initial_complete=False)
    before=deepcopy(p.to_dict())
    depth=len(bridge.history.undo_stack)
    desks=[]
    monkeypatch.setattr(OcrCapsulePairingDialog,'exec',lambda d:desks.append(d))
    compare=CandidateDialog(bridge,record)
    compare.pair();settled(bridge)
    assert len(desks)==1 and bridge.document.project.to_dict()==before
    desk=desks[0];session=desk.session
    anchor=session.anchors[0];piece=session.data['pieces'][0]
    assert session.text(piece)=='已有的译文' and anchor.text=='Scene lyrics'
    assert desk.change('source',ids=[anchor.id])
    assert desk.change('bind',ids=[piece['id']],target=session.data['groups'][0]['id'])
    if outcome=='cancel':
        desk.reject()
        assert bridge.document.project.to_dict()==before and len(bridge.history.undo_stack)==depth
    elif outcome=='locked_during_review':
        assert bridge.command('row.lock',{'id':'seed','value':True})
        current=deepcopy(bridge.document.project.to_dict());desk.apply()
        assert bridge.document.project.to_dict()==current and '变化' in desk.status.text()
        desk.reject()
    else:
        desk.apply()
        assert len(bridge.history.undo_stack)==depth+1
        assert any(m.visual_ids and m.start==.2 and m.end==.9 for m in bridge.document.main_timeline)
        assert any(m.id=='locked' and m.locked and m.start==2 for m in bridge.document.main_timeline)
        assert bridge.command('history.undo',{}) and bridge.document.project.to_dict()==before


def test_decoded_vfr_end_uses_first_absent_frame_pts(tmp_path,monkeypatch):
    import av
    import numpy as np
    from fractions import Fraction
    from lyricsync.vision import Video,Track,refine_track
    path=tmp_path/'variable.mkv'
    timestamps=[0,41,109,175,268,301]
    timebase=Fraction(1,1000)
    with av.open(str(path),'w') as container:
        stream=container.add_stream('ffv1', rate=25)
        stream.width=64;stream.height=32;stream.pix_fmt='yuv444p'
        stream.time_base=stream.codec_context.time_base=timebase
        stream.codec_context.thread_count=1
        for stamp in timestamps:
            image=np.full((32,64,3),255 if stamp in (109,175) else 0,dtype=np.uint8)
            frame=av.VideoFrame.from_ndarray(image,format='rgb24')
            frame.pts=stamp;frame.time_base=timebase
            for packet in stream.encode(frame):container.mux(packet)
        for packet in stream.encode():container.mux(packet)
    video=Video(path);index=FrameIndex.build(video)
    assert list(index.stamps)==pytest.approx([s/1000 for s in timestamps])
    track=Track(.109,.175,[0,0,64,32],['caption'],[.9],None,.109,.175)
    monkeypatch.setattr('lyricsync.vision.visibility_score',lambda template,frame,box:float(frame.mean()>128))
    start,end,edges,_=refine_track(video,track,.2,.62,lambda:False)
    assert (start,end)==pytest.approx((.109,.268))
    assert edges[-1]==pytest.approx((.175,.268)) and index.frame_span(start,end)==(3,4)
