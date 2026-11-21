"""Continuous property trajectories and rendered offscreen feedback regression.

These synthetic Windows/software checks are not native desktop or GPU acceptance.
"""
from copy import deepcopy
import json, os
from pathlib import Path
import pytest
from PySide6.QtCore import QPointF, QPoint, Qt
from PySide6.QtQml import QQmlExpression, qmlContext
from PySide6.QtTest import QTest
from test_qt_timeline_input import timeline_video, ui, find, point, click, drag, state_frames
from test_qt_timeline_snap import application, base_application
from test_qt_razor_input import cut_at
from lyricsync.editing import pending_text_links
from lyricsync.model import Mapping


def evaluate(t, code):
    expression=QQmlExpression(qmlContext(t),t,code)
    result=expression.evaluate()
    assert not expression.hasError(),expression.error().toString()
    return result


def output(tmp_path):
    p=Path(os.environ.get('LYRIA_TIMELINE_EVIDENCE',str(tmp_path)))
    p.mkdir(parents=True,exist_ok=True)
    return p


@pytest.mark.parametrize('follow',[False,True])
def test_every_edit_scroll_transition_stays_away_from_start(ui,follow,tmp_path):
    app,b,w,t=ui
    t.setProperty('followEnabled',follow);t.setProperty('manualView',True)
    t.setProperty('viewFirst',390);t.setProperty('viewLast',470);QTest.qWait(60)
    tracks=find(w,'timelineTracks');before_y=tracks.property('contentY')
    trace=[];resets=[];label=['start']
    def sample():
        trace.append(dict(step=label[0],first=t.property('viewFirst'),last=t.property('viewLast'),top=tracks.property('contentY'),selected=b.selected,rowCount=len(b.document.project.mappings),undo=[x[0] for x in b.history.undo_stack],restored=b.history.restored_context))
    tracks.contentYChanged.connect(sample);t.viewFirstChanged.connect(sample);t.viewLastChanged.connect(sample)
    b.timeline_model.modelReset.connect(lambda:resets.append('timeline'));b.model.modelReset.connect(lambda:resets.append('review'))
    def settle(name):
        label[0]=name
        for _ in range(30):sample();QTest.qWait(2)
    settle('baseline')
    for edge in (None,'first','last'):
        label[0]='move' if edge is None else 'trim-'+edge
        item=find(w,'timelineClip_r40') if edge is None else find(w,'cueEdge_'+edge)
        assert item is not None, {'edge':edge,'selected':b.selected,'current':t.property('current').toVariant(),
            'view':[t.property('viewFirst'),t.property('viewLast')],'top':tracks.property('contentY'),
            'height':tracks.height(),'trace':trace[-20:],'resets':resets}
        gesture={}
        history_before=len(b.history.undo_stack)
        start=point(item)
        drag(w,start,start+QPoint(35,0),lambda:gesture.update(moving=t.property('movingClip'),editing=t.property('editing'),delta=t.property('moveDelta'),frame=t.property('editFrame'),position=[start.x(),start.y()],size=[w.width(),w.height()],undo=[x[0] for x in b.history.undo_stack]))
        settle(label[0])
        if not (gesture.get('moving') or gesture.get('editing')):
            w.grabWindow().save(str(output(tmp_path)/'missing-gesture.png'))
            (output(tmp_path)/'missing-gesture.json').write_text(json.dumps({'gesture':gesture,'trace':trace},indent=2),encoding='utf-8')
        assert gesture.get('moving') or gesture.get('editing'),gesture
        assert len(b.history.undo_stack)==history_before+1,gesture
        assert b.command('history.undo',{});settle('undo-'+str(edge))
        assert b.command('history.redo',{});settle('redo-'+str(edge))
        assert b.command('history.undo',{});settle('restore-'+str(edge))
    click(w,find(w,'timelineZoomIn'));settle('zoom-in')
    click(w,find(w,'timelineZoomOut'));settle('zoom-out')
    # Changing chronological review order must not reset either model.
    assert b.command('timeline.move',{'id':'r40','delta':20});settle('reorder')
    assert b.command('history.undo',{});settle('undo-reorder')
    click(w,find(w,'timelineRazorTool'));cut_at(w,t,'r40',424);settle('cut')
    assert b.command('history.undo',{});settle('undo-cut')
    assert b.command('history.redo',{});settle('redo-cut')
    assert not resets
    assert all(v['first']>300 and v['top']>900 for v in trace),trace
    assert all(abs(v['top']-before_y)<=30.5 for v in trace),trace
    assert t.property('followEnabled')==follow
    (output(tmp_path)/('continuous-'+str(follow)+'.json')).write_text(json.dumps(trace,indent=2),encoding='utf-8')
    tracks.contentYChanged.disconnect(sample);t.viewFirstChanged.disconnect(sample);t.viewLastChanged.disconnect(sample)


def test_untimed_numbering_follows_logical_predecessor_without_rewriting(ui):
    app,b,w,t=ui;p=b.document.project
    p.mappings=[Mapping('lead',['t0']),Mapping('late',['t1'],start=10,end=11),Mapping('u1',['t2']),Mapping('u2',['t3']),Mapping('early',['t4'],start=2,end=3),Mapping('tail',['t5'])]
    b.selected='u1';before=deepcopy(p.to_dict());b._changed();QTest.qWait(60)
    assert [r['id'] for r in b.model.items]==['lead','early','tail','late','u1','u2']
    assert [r['number'] for r in b.timeline_model.items]==[1,4,5,6,2,3]
    assert p.to_dict()==before
    assert b.command('timeline.assignRange',{'id':'u1','first':330,'last':337})
    assert [m.id for m in p.mappings]==['lead','late','u1','u2','early','tail']
    assert [r['id'] for r in b.model.items]==['lead','early','tail','late','u1','u2']
    assert b.command('history.undo',{}) and b.document.project.to_dict()==before


@pytest.mark.parametrize('defer',['hidden','collapsed','interaction'])
def test_new_cue_reveals_when_deferred_surface_becomes_available(ui,defer):
    app,b,w,t=ui;t.setProperty('followEnabled',False);t.setProperty('manualView',True)
    if defer=='hidden':assert b.command('stage.set',{'stage':'ocr'})
    elif defer=='collapsed':t.setProperty('collapsed',True);QTest.qWait(230)
    else:t.setProperty('panning',True)
    assert b.command('timeline.createRange',{'first':1150,'last':1160})
    QTest.qWait(60);assert t.property('pendingCreatedId')==b.selected
    if defer=='hidden':assert b.command('stage.set',{'stage':'review'})
    elif defer=='collapsed':t.setProperty('collapsed',False)
    else:t.setProperty('panning',False)
    QTest.qWait(300)
    assert not t.property('pendingCreatedId')
    tracks=find(w,'timelineTracks');row=find(w,'timelineRow_'+b.selected)
    top=row.mapToItem(tracks,QPointF()).y()
    assert -.5<=top and top+row.height()<=tracks.height()+.5
    assert t.property('viewFirst')<=1150<t.property('viewLast')
    assert not t.property('followEnabled')


def test_keep_locked_cancel_and_stale_pair_are_atomic(ui):
    app,b,w,t=ui;click(w,find(w,'timelineRazorTool'));cut_at(w,t,'r40',424)
    action=dict(b.state['pendingTextAction']);before=b.document.project.to_dict();history=len(b.history.undo_stack)
    click(w,find(w,'reviewAllocateSplitText'));click(w,find(w,'allocationCancelButton'))
    assert b.document.project.to_dict()==before and len(b.history.undo_stack)==history
    assert b.command('row.lock',{'id':action['rightId'],'value':True})
    QTest.qWait(20);assert not find(w,'reviewKeepSplitText').isEnabled()
    locked=b.document.project.to_dict();assert not b.command('textAllocation.keep',b.state['pendingTextAction'])
    assert b.document.project.to_dict()==locked
    assert b.command('row.lock',{'id':action['rightId'],'value':False})
    assert not b.command('textAllocation.keep',action)
    current=dict(b.state['pendingTextAction']);assert b.command('textAllocation.keep',current)
    assert not b.command('textAllocation.keep',current)
    assert not pending_text_links(b.document.project)


def test_rendered_header_and_exact_marker_centers(ui,tmp_path):
    app,b,w,t=ui;out=output(tmp_path)
    assert b.command('settings.set',{'key':'subtitlePreview','value':False})
    t.setProperty('manualView',True);t.setProperty('followEnabled',False)
    t.setProperty('viewFirst',398);t.setProperty('viewLast',450)
    b.media_controller.seek(424)
    from test_qt_timeline_input import wait
    wait(app,lambda:b.media['displayedFrame']==424)
    assert b.command('timeline.select',{'id':'r40'})
    assert b.command('row.setBoundary',{'id':'r40','edge':'last','frame':429})
    assert b.command('marker.add',{'frame':424});QTest.qWait(80)
    head=find(w,'timelinePlayhead');diamond=find(w,'timelinePlayheadDiamond');ruler=find(w,'timelineRulerMouse')
    x=(424-t.property('viewFirst'))/t.property('span')*ruler.width()
    assert abs(head.x()+head.width()/2-x)<1e-6
    assert abs(diamond.x()+diamond.width()/2-head.width()/2)<1e-6
    guide=find(w,'timelineAlignmentGuide_430');tip=find(w,'timelineAlignmentTip_430')
    assert guide and guide.isVisible()
    assert abs(guide.x()+guide.width()/2-(430-t.property('viewFirst'))/t.property('span')*ruler.width())<1e-6
    assert abs(tip.x()+tip.width()/2-guide.width()/2)<1e-6
    create=find(w,'timelineCreateMenuButton')
    assert create.property('text')=='' and create.property('glyph')=='subtitle-add'
    assert create.property('tip')=='新建空白句'
    fold=find(w,'timelineFoldButton');contents=find(w,'timelineFoldContents')
    center=contents.mapToItem(fold,QPointF(contents.width()/2,contents.height()/2))
    assert abs(center.x()-fold.width()/2)<.1 and abs(center.y()-fold.height()/2)<.1
    for name in ('timelineFoldArrow','timelineFoldText'):
        child=find(w,name)
        assert abs(child.y()+child.height()/2-contents.height()/2)<.1
    names=['timelineSelectTool','timelineHandTool','timelineRazorTool','timelineSnapToggle','audioVisibilityToggle','timelineZoomOut','timelineZoomIn','timelineCreateMenuButton','followCurrentButton','timelineFoldButton']
    ys=[point(find(w,n)).y() for n in names]
    assert max(ys)-min(ys)<3,ys
    assert w.grabWindow().save(str(out/'wide.png'))
    # Render the actual dashed snap center at the inclusive end boundary.
    evaluate(t,'begin(current, "last", xFor(current.lastFrame + 1))')
    evaluate(t,'adjust(current, xFor(430.2), 0)');QTest.qWait(30)
    snap=find(w,'timelineSnapGuide');assert snap.isVisible()
    assert abs(snap.x()+snap.width()/2-(t.property('snapFrame')-t.property('viewFirst'))/t.property('span')*ruler.width())<1e-6
    assert w.grabWindow().save(str(out/'snap.png'));evaluate(t,'cancelEdgeEdit()')
    w.setWidth(1020);QTest.qWait(250)
    header=find(w,'timelineHeader');assert header.height()>=29
    for n in names:
        item=find(w,n);pos=item.mapToItem(t,QPointF())
        assert pos.x()>=0 and pos.x()+item.width()<=t.width()+1,(n,pos,item.width(),t.width())
    assert w.grabWindow().save(str(out/'narrow.png'))
    # The bounded v0.23 workspace must expose the timeline without outer scroll.
    outer=find(w,'makeScroll')
    assert outer.property('contentHeight') is None
    assert t.mapToItem(outer,QPointF()).y()+t.height()<=outer.height()+1
    QTest.qWait(80)
    assert w.grabWindow().save(str(out/'narrow-timeline.png'))
    click(w,find(w,'timelineRazorTool'));cut_at(w,t,'r40',424)
    assert find(w,'reviewPendingText').isVisible()
    w.setWidth(1400);QTest.qWait(250)
    assert w.grabWindow().save(str(out/'pending-review.png'))


def test_pending_playback_refresh_does_not_walk_mappings_and_old_selection_is_rejected(ui):
    app,b,w,t=ui;click(w,find(w,'timelineRazorTool'));cut_at(w,t,'r40',424)
    action=dict(b.state['pendingTextAction']);original=b.document.project.mappings
    class NoIteration(list):
        def __iter__(self):raise AssertionError('pending action walked mappings on playback refresh')
    graph=b.document.project.alignment_graph
    graph.timeline=NoIteration(original)
    try:
        for _ in range(10):b._refresh(False)
        assert b.state['pendingTextAction']==action
    finally:
        graph.timeline=original
    assert b.command('timeline.select',{'id':'r50'})
    before=b.document.project.to_dict();history=len(b.history.undo_stack)
    assert not b.command('textAllocation.keep',action)
    assert b.document.project.to_dict()==before and len(b.history.undo_stack)==history


def test_overlapping_subtitle_times_export_without_changing_style_or_positions(tmp_path):
    from lyricsync.model import Project, Translation
    from lyricsync.export import export_project
    p=Project(video={'width':1920,'height':1080,'duration':4})
    p.translations=[Translation('a','重叠前句'),Translation('b','重叠后句')]
    p.mappings=[Mapping('a',['a'],start=0,end=2,confirmed=True),Mapping('b',['b'],start=1,end=3,confirmed=True)]
    p.settings.update(font_size=52,subtitle_position='custom',subtitle_horizontal_percent=43,subtitle_height_percent=78)
    before=deepcopy(p.to_dict());destination=tmp_path/'overlapping.ass'
    export_project(p,destination)
    assert destination.read_text(encoding='utf-8-sig').count('Dialogue:')==2
    assert p.to_dict()==before


def test_single_cue_split_and_merge_projection_never_resets(application):
    from desktop_qt.controller import CueModel
    model=CueModel();model.replace([{'id':'source','cueId':'source'}])
    resets=[];model.modelReset.connect(lambda:resets.append(True))
    model.replace([{'id':'left','cueId':'left'},{'id':'right','cueId':'right'}])
    assert model.count==2 and not resets
    model.replace([{'id':'merged','cueId':'merged'}])
    assert model.count==1 and not resets


def test_all_twenty_candidate_splits_preserve_manual_view_but_new_document_starts_at_top(ui):
    from lyricsync.api_alignment import make_plan
    from lyricsync.editing import mapping_text, Editor
    from lyricsync.model import Visual, Project
    from lyricsync.project_document import ProjectDocument
    app,b,w,t=ui;p=b.document.project;index=b.media_controller.index
    p.mappings=p.mappings[30:50];p.visuals=[];operations=[]
    for row in p.mappings:
        first,last=index.first(row.start),index.last(row.end)
        midpoint=(first+last+1)//2;text=mapping_text(p,row);parts=[]
        for suffix,a,z,lo,hi in [('a',first,midpoint-1,0,2),('b',midpoint,last,2,len(text))]:
            identity=row.id+'-'+suffix;start,end=index.interval(a,z)
            p.visuals.append(Visual(identity,text[lo:hi],start,end,[],.99,2))
            parts.append({'spans':[{'mapping_id':row.id,'start':lo,'end':hi}],'lyric_ids':[],'visual_ids':[identity]})
        row.visual_ids=[row.id+'-a',row.id+'-b']
        operations.append({'kind':'split','mapping_ids':[row.id],'parts':parts,'confidence':.9,'reason':'Offline structural fixture','reference_meaning':''})
    b.selected='r40';b._changed();QTest.qWait(80)
    plan=make_plan(p,index,{'operations':operations,'semantic_issues':[]},[m.id for m in p.mappings])
    assert len(plan['diff'])==20
    b._alignment={'plan':plan};b._refresh(False)
    t.setProperty('followEnabled',False);t.setProperty('manualView',True)
    t.setProperty('viewFirst',398);t.setProperty('viewLast',450)
    evaluate(t,'clampRows(8 * rowHeight)');QTest.qWait(30)
    tracks=find(w,'timelineTracks');top=tracks.property('contentY');before=b.document.snapshot()
    for _ in range(4):
        for enabled,count in [(True,40),(False,20)]:
            assert b.command('alignment.preview',{'enabled':enabled}),b.state['error']
            # The synchronous return is before any event-loop/render turn.
            # Also sample subsequent turns to reject a delayed zero/restore flash.
            assert tracks.property('contentY')==top
            for _ in range(25):
                QTest.qWait(2);assert tracks.property('contentY')==top
            assert b.timeline_model.count==count
            assert not t.property('followEnabled')
            assert (t.property('viewFirst'),t.property('viewLast'))==(398,450)
            assert b.document.snapshot()==before
    b.autosave_timer.stop()
    replacement=ProjectDocument(project=Project.from_dict(p.to_dict()))
    b.document=replacement;b.editor=Editor(replacement.project,b.history,b._changed);b.selected=''
    b._refresh();QTest.qWait(50)
    assert tracks.property('contentY')==0
    assert t.property('followEnabled')  # v0.23 new document restores default follow
