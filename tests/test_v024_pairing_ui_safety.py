import os
os.environ['QT_QPA_PLATFORM']='offscreen'
os.environ['QT_QUICK_BACKEND']='software'
from copy import deepcopy
from PySide6.QtTest import QTest
from test_qt_review_selection import application,ui,find
from test_v023_review_interaction import evaluate
from lyricsync.model import Lyric
from desktop_qt.review_actions import select
from desktop_qt.timeline_views import archive_main

def test_ctrl_deselect_keeps_primary_in_remaining_selection(ui):
    b=ui.bridge
    select(b,{'id':'r41','ctrl':True})
    assert b.selected=='r41' and b._multi_selected=={'r40','r41'}
    select(b,{'id':'r41','ctrl':True})
    assert b.selected=='r40' and b._multi_selected=={'r40'}

def test_evidence_region_stable_and_long_button_elides(ui):
    b=ui.bridge
    b.document.project.lyrics=[Lyric('source','reference',None,None)]
    b.document.project.mappings[40].lyric_ids=['source'];b._changed();QTest.qWait(80)
    panel=find(ui.window,'reviewEvidencePanel')
    assert panel.isVisible();height=panel.height()
    b.command('row.select',{'id':'r41'});QTest.qWait(80)
    assert panel.isVisible() and panel.height()==height
    name='很长的说话人名字' * 7
    assert b.command('review.assignSpeaker',{'name':name});QTest.qWait(80)
    field=find(ui.window,'currentSpeakerInput');badge=find(ui.window,'rowSpeaker_r41')
    assert field.property('text')==name and field.property('clip')
    assert field.property('contentWidth')>field.width()
    assert badge.property('text')==name and evaluate(badge,'children[0].truncated')[0]

def test_paused_empty_follow_and_audio_only_toggle(ui):
    t=find(ui.window,'frameTimeline')
    ui.bridge.document.project.mappings=[];ui.bridge._changed()
    evaluate(t,'media=({playing:false,displayedFrame:350,totalFrames:1500}); view(1,101); followEnabled=true; pagePlayhead()')
    assert t.property('viewFirst')<=350<t.property('viewLast')
    evaluate(t,'followEnabled=false; view(1,101); pagePlayhead()')
    assert t.property('viewFirst')==1
    ui.bridge.command('view.layout',{'timelineVisible':False})
    QTest.qWait(50)
    assert find(ui.window,'audioFollowCurrentButton').isVisible()

def test_readonly_history_and_close_target_copy(ui):
    b=ui.bridge;b.history.push(b.document.project,'test')
    b._refresh(False);assert b.state['history']['canUndo']
    b._candidate_project=deepcopy(b.document.project);b._candidate_source='ocr';b._refresh(False)
    assert not b.state['history']['canUndo'] and not b.state['history']['canRedo']
    b._continuation=lambda:None;b._show_close();QTest.qWait(40)
    assert b._close_dialog['switching'] and b._close_dialog['exportTarget']=='主时间轴'
    assert '主时间轴' in find(ui.window,'closeDialogMessage').property('text')
    assert '切换工程' in find(ui.window,'closeDiscard').property('text')
    b.document.mark_saved(b.document.revision); b._show_close(); QTest.qWait(40)
    assert find(ui.window,'closeSave').property('text')=='工程已保存'
    assert not find(ui.window,'closeSave').isEnabled()
    assert '不导出并' in find(ui.window,'closeDiscard').property('text')
    b._resolve_close('cancel');b._candidate_source='main';b._refresh(False)

def test_visible_adjacent_merge_not_storage_adjacent(ui):
    b=ui.bridge;p=b.document.project
    order=deepcopy(p.mappings)
    p.mappings=[order[0],order[2],order[1]]+order[3:];b._changed()
    first=[row['id'] for row in b._review_order(b._all_rows)[:3]]
    assert first==['r0','r1','r2']
    assert b.command('row.merge',{'id':'r0'})
    merged=next(row for row in p.mappings if row.id.startswith('merge-')) if p is b.document.project else next(row for row in b.document.project.mappings if row.id.startswith('merge-'))
    assert set(merged.translation_ids)=={'t0','t1'}
    assert any(row.id=='r2' for row in b.document.project.mappings)

def test_archive_full_preserves_all_and_does_not_recommend_save_as():
    from lyricsync.model import Project
    p=Project()
    for n in range(8):archive_main(p,str(n))
    before=deepcopy(p.to_dict())
    import pytest
    with pytest.raises(ValueError,match='新建工程'):archive_main(p,'ninth')
    assert p.to_dict()==before
def test_paused_nonempty_keeps_explicit_view_and_cue_priority(ui):
    t=find(ui.window,'frameTimeline');b=ui.bridge
    evaluate(t,'media=({playing:false,displayedFrame:1,totalFrames:1500}); followEnabled=true')
    QTest.qWait(100)  # Let initial media/viewport fitting settle before manual view.
    evaluate(t,'view(350,450); pagePlayhead()')
    QTest.qWait(50)
    assert (t.property('viewFirst'),t.property('viewLast'))==(350,450)
    b.command('timeline.select',{'id':'r60'});QTest.qWait(60)
    assert t.property('viewFirst')<=601<t.property('viewLast')
    before=(t.property('viewFirst'),t.property('viewLast'))
    evaluate(t,'pagePlayhead()');QTest.qWait(30)
    assert (t.property('viewFirst'),t.property('viewLast'))==before