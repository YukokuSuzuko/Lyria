"""Windows offscreen, synthetic data only; no native desktop gesture claim."""
from pathlib import Path
import os
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from test_qt_timeline_input import timeline_video, ui, find, point, click, wait, state_frames, drag
from test_qt_timeline_snap import application, base_application
from desktop_qt.tool_cursor import razor_cursor
from lyricsync.project_document import load_document, write_document


def test_follow_defaults_on_and_keeps_user_choice_across_pages(ui):
    app,bridge,window,timeline=ui
    assert timeline.property('followEnabled') and find(window,'followCurrentButton').property('checked')
    original=bridge.document.snapshot()
    for desired in (False,True):
        click(window,find(window,'followCurrentButton'))
        assert timeline.property('followEnabled') is desired
        for stage in ('ocr','export','review'):
            assert bridge.command('stage.set',{'stage':stage})
            QTest.qWait(50)
            assert timeline.property('followEnabled') is desired
        for workspace in ('settings','qc','make'):
            window.setProperty('workspace',workspace);QTest.qWait(50)
            assert timeline.property('followEnabled') is desired
        assert find(window,'followCurrentButton').property('checked') is desired
    assert bridge.document.snapshot()==original


@pytest.mark.parametrize('ratio',[1.,1.5,2.])
def test_cursor_has_short_dashes_at_unchanged_cut_hotspot(application,ratio,tmp_path):
    out=Path(os.environ.get('LYRIA_RAZOR_FOLLOW_EVIDENCE',str(tmp_path)));out.mkdir(parents=True,exist_ok=True)
    for forbidden in (False,True):
        cursor=razor_cursor(forbidden,ratio)
        assert cursor.hotSpot()==QPoint(8,22)
        image=cursor.pixmap().toImage()
        assert image.save(str(out/f'cursor-{forbidden}-{ratio}.png'))
        # The short dash is attached to the native pointer at the cut x.
        samples=[image.pixelColor(round(8*ratio),round(y*ratio)).alpha()>100 for y in range(9,36)]
        assert sum(samples)>=12 and sum(a!=b for a,b in zip(samples,samples[1:]))>=7


@pytest.fixture
def untimed(ui):
    app,bridge,window,timeline=ui
    row=bridge._row('r40');row.start=row.end=None
    bridge._changed();QTest.qWait(80)
    return ui


def test_assign_existing_untimed_range_keeps_identity_text_order_and_one_undo(untimed,tmp_path):
    app,bridge,window,timeline=untimed
    before=bridge.document.project.to_dict();history=len(bridge.history.undo_stack)
    assert bridge.command('timeline.assignRange',{'id':'r40','first':430,'last':437})
    after=bridge.document.project.to_dict()
    assert len(after['mappings'])==100 and [m['id'] for m in after['mappings']]==[m['id'] for m in before['mappings']]
    assert state_frames(bridge,'r40')==(430,437) and bridge.selected=='r40'
    assert bridge._row('r40').translation_ids==['t40'] and after['translations']==before['translations']
    assert [m for m in after['mappings'] if m['id']!='r40']==[m for m in before['mappings'] if m['id']!='r40']
    assert len(bridge.history.undo_stack)==history+1
    assert bridge.command('history.undo',{}) and bridge.document.project.to_dict()==before
    assert bridge.command('history.redo',{}) and bridge.document.project.to_dict()==after
    # Canonical persisted project round trip, including stable ID and frame times.
    path=tmp_path/'synthetic.lyria';write_document(bridge.document.snapshot(),path)
    reopened=load_document(path)
    assert reopened.project.to_dict()==after


@pytest.mark.parametrize('kind',['timed','partial','locked','missing','inverted','outside'])
def test_assign_refuses_existing_or_conflicting_target_without_any_change(untimed,kind):
    app,bridge,window,timeline=untimed
    row=bridge._row('r40');payload={'id':'r40','first':430,'last':437}
    if kind=='timed':row.start,row.end=bridge.media_controller.index.interval(420,427)
    if kind=='partial':row.start=1.
    if kind=='locked':bridge.document.project.settings['manual_locked_ids']=['r40']
    if kind=='missing':payload['id']='removed-row'
    if kind=='inverted':payload['last']=payload['first']-1
    if kind=='outside':payload['last']=1201
    bridge._changed();before=bridge.document.project.to_dict();history=len(bridge.history.undo_stack)
    assert not bridge.command('timeline.assignRange',payload)
    assert bridge.document.project.to_dict()==before and len(bridge.history.undo_stack)==history


def untimed_drag_points(ui,first=425,last=438):
    app,bridge,window,timeline=ui
    timeline.setProperty('followEnabled',False);timeline.setProperty('manualView',True)
    timeline.setProperty('viewFirst',401);timeline.setProperty('viewLast',501)
    timeline.setProperty('createMode',True);QTest.qWait(50)
    lane=find(window,'timelineRow_r40');ruler=find(window,'timelineRulerMouse')
    y=point(lane,120,13).y()
    return [QPoint(point(ruler,(frame-401)/100*ruler.width(),0).x(),y) for frame in (first,last)]


@pytest.mark.parametrize('reverse',[False,True])
def test_real_drag_on_untimed_lane_assigns_that_same_row(untimed,reverse,tmp_path):
    app,bridge,window,timeline=untimed
    before=bridge.document.project.to_dict();history=len(bridge.history.undo_stack)
    start,end=untimed_drag_points(untimed)
    if reverse:start,end=end,start
    drag(window,start,end,lambda: pytest_assert_unchanged(bridge,before))
    assert len(bridge.document.project.mappings)==100
    assert bridge.selected=='r40' and state_frames(bridge,'r40')==(425,438)
    assert len(bridge.history.undo_stack)==history+1
    assert bridge.command('history.undo',{}) and bridge.document.project.to_dict()==before
    assert bridge.command('history.redo',{}) and state_frames(bridge,'r40')==(425,438)
    out=Path(os.environ.get('LYRIA_RAZOR_FOLLOW_EVIDENCE',str(tmp_path)));out.mkdir(parents=True,exist_ok=True)
    assert window.grabWindow().save(str(out/'untimed-assigned.png'))


def pytest_assert_unchanged(bridge,before):
    assert bridge.document.project.to_dict()==before


def test_menu_captures_target_id_even_if_selection_changes(untimed):
    app,bridge,window,timeline=untimed
    click(window,find(window,'timelineCreateMenuButton'))
    item=find(window,'timelineCreateAtPlayheadMenuItem')
    assert '为本句打轴' in item.property('text')
    assert bridge.command('timeline.select',{'id':'r41'})
    QTest.qWait(20)
    click(window,item)
    assert len(bridge.document.project.mappings)==100
    assert state_frames(bridge,'r40')==(1,90) and bridge.selected=='r40'
    assert state_frames(bridge,'r41')==(430,437)


@pytest.mark.parametrize('stale',['timed','locked','deleted'])
def test_drag_target_changed_before_release_never_overwrites_or_inserts(untimed,stale):
    app,bridge,window,timeline=untimed
    start,end=untimed_drag_points(untimed)
    QTest.mousePress(window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start)
    QTest.mouseMove(window,end,40)
    if stale=='timed':bridge.editor.set_time('r40',start=3.,end=4.)
    if stale=='locked':bridge.command('row.lock',{'id':'r40','value':True})
    if stale=='deleted':bridge.command('row.delete',{'id':'r40'})
    before=bridge.document.project.to_dict();history=len(bridge.history.undo_stack)
    QTest.mouseRelease(window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,end);QTest.qWait(50)
    assert bridge.document.project.to_dict()==before and len(bridge.history.undo_stack)==history


def test_explicit_new_blank_still_creates_a_distinct_row(untimed):
    app,bridge,window,timeline=untimed
    assert bridge.command('timeline.createRange',{'first':425,'last':438})
    assert len(bridge.document.project.mappings)==101
    assert bridge._row('r40').start is None and bridge._row('r40').end is None
    assert bridge.selected!='r40' and state_frames(bridge,bridge.selected)==(425,438)


@pytest.mark.parametrize('first,last',[(1,1),(1200,1200)])
def test_assign_single_first_and_last_frame(untimed,first,last):
    bridge=untimed[1]
    assert bridge.command('timeline.assignRange',{'id':'r40','first':first,'last':last})
    assert state_frames(bridge,'r40')==(first,last)


@pytest.mark.parametrize('width,height',[(1400,940),(1000,760),(760,600)])
def test_header_layout_preserves_controls_aspect_and_user_split(ui,width,height,tmp_path):
    import json
    app,bridge,window,timeline=ui
    desired={**bridge.state['workspaceLayout'],'previewFraction':.56,'topFraction':.72,'previewHeight':430.,'audioHeight':160.,'audioHeightExplicit':True,'audioMode':'waveform'}
    assert bridge.command('view.layout',desired)
    before=bridge.document.snapshot()
    window.resize(width,height);QTest.qWait(200)
    switch=find(window,'workspaceSwitch');header=switch.parentItem().parentItem()
    assert not find(window,'qcWorkspaceButton').isVisible(), 'F37 hides QC without deleting its implementation'
    assert not switch.isVisible(), 'dev8 hides the whole production/QC switch group'
    names=['stageButton_import','stageButton_review','exportStageButton']
    rects=[]
    for name in names:
        item=find(window,name);p=item.mapToItem(header,QPointF(0,0))
        assert item.isVisible() and 0<=p.y() and p.y()+item.height()<=header.height()+.5
        assert 0<=p.x() and p.x()+item.width()<=header.width()+.5
        rects.append((name,p.x(),p.y(),item.width(),item.height()))
    for i,(name,x,y,w,h) in enumerate(rects):
        for other,xx,yy,ww,hh in rects[i+1:]:
            assert min(x+w,xx+ww)-max(x,xx)<=.5 or min(y+h,yy+hh)-max(y,yy)<=.5,(name,other)
    video=find(window,'videoPreview');rect=video.property('contentRect')
    assert abs(rect.width()/rect.height()-64/36)<.001
    assert bridge.document.snapshot()==before and bridge.state['workspaceLayout']==desired
    out=Path(os.environ.get('LYRIA_RAZOR_FOLLOW_EVIDENCE',str(tmp_path)));out.mkdir(parents=True,exist_ok=True)
    (out/f'layout-{width}.json').write_text(json.dumps({'headerHeight':header.height(),'pictureHeight':rect.height(),'pictureWidth':rect.width(),'panelHeight':find(window,'mainPreviewPanel').height(),'controls':rects,'preferences':desired},indent=2),encoding='utf8')
    assert window.grabWindow().save(str(out/f'layout-{width}.png'))
