"""Actual AE timeline C/popup routes, literal offsets and state-only motion."""
from pathlib import Path
import json
import os
import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_qt_timeline_input import application, timeline_video, ui, find, point, click, wait
from lyricsync.editing import mapping_text, pending_text_links


def evidence(tmp_path):
    target=Path(os.environ.get('LYRIA_RAZOR_EVIDENCE',str(tmp_path)))
    target.mkdir(parents=True,exist_ok=True)
    return target


def pixel(window,item):
    shot=window.grabWindow();p=point(item,item.width()/2,3);ratio=shot.devicePixelRatio()
    return shot.pixelColor(round(p.x()*ratio),round(p.y()*ratio)).name()


def popup(window):return window.findChild(QObject,'textAllocationPopup')


def cut_at(window,timeline,identity,frame,wait_ms=40):
    clip=find(window,'timelineClip_'+identity);assert clip
    ruler=find(window,'timelineRulerMouse')
    x=(frame-timeline.property('viewFirst'))/timeline.property('span')*ruler.width()
    local_x=point(ruler,x,0).x()-point(clip,0,0).x()
    QTest.mouseClick(window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point(clip,local_x,13))
    QTest.qWait(wait_ms)


def caret_to(window,position):
    caret=find(window,'allocationTextCaret');assert caret
    caret.forceActiveFocus();QTest.keyClick(window,Qt.Key.Key_Home,Qt.KeyboardModifier.ControlModifier)
    for _ in range(position):QTest.keyClick(window,Qt.Key.Key_Right)
    return caret


def test_c_then_optional_nonmodal_allocation_cancel_and_undo(ui,monkeypatch,tmp_path):
    app,bridge,window,timeline=ui;calls=[]
    monkeypatch.setattr(bridge.media_controller,'seek',lambda *a:calls.append('seek'))
    monkeypatch.setattr(bridge.media_controller,'pause',lambda *a:calls.append('pause'))
    original=bridge.document.project.to_dict();frame=bridge.media['displayedFrame'];history=len(bridge.history.undo_stack)
    timeline.setProperty('manualView',True);timeline.setProperty('followEnabled',False)
    viewport=(timeline.property('viewFirst'),timeline.property('viewLast'))
    QTest.keyClick(window,Qt.Key.Key_C);QTest.qWait(30)
    cut_at(window,timeline,'r40',424)
    a,b=bridge.document.project.mappings[40:42]
    assert (mapping_text(bridge.document.project,a),mapping_text(bridge.document.project,b))==('真实中文第41句','')
    assert not QApplication.activeModalWidget() and not popup(window).property('visible')
    assert not calls and bridge.media['displayedFrame']==frame and len(bridge.history.undo_stack)==history+1
    assert timeline.property('tool')=='razor' and (timeline.property('viewFirst'),timeline.property('viewLast'))==viewport
    from tools.qt_event_loop_wait import wait_until
    # A busy process may defer the animation's first tick past a fixed sleep.
    # Wait for its real completion, retaining exact final opacity/color checks.
    wait_until(lambda: not find(window,'timelineClip_'+a.id).property('pendingAnimating')
               and find(window,'pendingClipMark_'+a.id).opacity()==1, timeout_ms=1500)
    assert find(window,'timelineClip_'+a.id).property('color').name()=='#8562c5'
    assert find(window,'pendingClipMark_'+a.id).property('color').name()=='#d99a42'
    assert find(window,'pendingClipMark_'+a.id).opacity()==1
    assert find(window,'pendingClipFill_'+b.id).property('color').name()=='#f5deb7'
    assert find(window,'pendingClipFill_'+b.id).opacity()==1
    assert find(window,'timelineText_'+b.id).property('text')=='待分配文字'
    assert find(window,'cueRow_'+a.id).property('color').name()=='#f3eefc'  # Review unchanged.
    out=evidence(tmp_path)
    assert window.grabWindow().save(str(out/'ae-razor-pending.png'))
    after_cut=bridge.document.project.to_dict()
    click(window,find(window,'allocateTextButton'));assert popup(window).property('visible')
    assert not popup(window).property('modal') and not find(window,'allocationApplyButton').isEnabled()
    click(window,find(window,'allocationCancelButton'))
    assert bridge.document.project.to_dict()==after_cut and len(bridge.history.undo_stack)==history+1
    click(window,find(window,'allocateTextButton'));caret=caret_to(window,4);QTest.qWait(30)
    assert caret.property('cursorPosition')==4 and find(window,'allocationApplyButton').isEnabled()
    assert find(window,'allocationLeftPreview').property('text')=='真实中文'
    assert find(window,'allocationRightPreview').property('text')=='第41句'
    text=caret.property('text')
    for key in (Qt.Key.Key_S,Qt.Key.Key_C,Qt.Key.Key_H,Qt.Key.Key_V):QTest.keyClick(window,key)
    assert caret.property('text')==text and bridge.document.project.to_dict()==after_cut
    # Autosave/status updates must not move the user's caret or invalidate it.
    for _ in range(3):bridge._refresh(False);QTest.qWait(30)
    assert caret.property('cursorPosition')==4 and find(window,'allocationApplyButton').isEnabled()
    QTest.qWait(650)
    assert caret.property('cursorPosition')==4 and find(window,'allocationApplyButton').isEnabled()
    assert timeline.property('tool')=='razor'
    assert window.grabWindow().save(str(out/'ae-razor-allocation.png'))
    click(window,find(window,'allocationApplyButton'))
    assert not bridge.state['textAllocation'] and not popup(window).property('visible')
    assert len(bridge.document.project.mappings)==101 and len(bridge.history.undo_stack)==history+2
    assert [mapping_text(bridge.document.project,m) for m in (a,b)]==['真实中文','第41句']
    assert not pending_text_links(bridge.document.project) and not calls
    QTest.qWait(230)
    assert window.grabWindow().save(str(out/'ae-razor-allocated.png'))
    (out/'razor-input-state.json').write_text(json.dumps({'source':'real Main.qml QTest clicks with synthetic local video','cutFrames':[[420,423],[424,427]],'placeholder':'待分配文字','afterAllocation':['真实中文','第41句'],'noModal':True,'transportCalls':calls,'caretSurvivedRefreshAndAutosave':True,'reviewColorsUnchanged':True},ensure_ascii=False,indent=2))
    assert bridge.command('history.undo',{}) and bridge.document.project.to_dict()==after_cut
    assert bridge.command('history.undo',{}) and bridge.document.project.to_dict()==original and bridge.selected=='r40'
    QTest.qWait(50)
    assert (timeline.property('viewFirst'),timeline.property('viewLast'))==viewport


def test_repeated_cuts_last_frame_and_unmodified_geometry(ui):
    app,bridge,window,timeline=ui
    original_height=timeline.height();click(window,find(window,'timelineRazorTool'))
    cut_at(window,timeline,'r40',424);a,b=bridge.document.project.mappings[40:42]
    cut_at(window,timeline,b.id,427);parts=bridge.document.project.mappings[40:43];idx=bridge.media_controller.index
    assert [(idx.first(m.start),idx.last(m.end)) for m in parts]==[(420,423),(424,426),(427,427)]
    assert ''.join(mapping_text(bridge.document.project,m) for m in parts)=='真实中文第41句'
    assert len(pending_text_links(bridge.document.project))==2
    assert not QApplication.activeModalWidget() and timeline.property('tool')=='razor'
    assert timeline.height()==original_height


def test_invalid_cut_guide_lock_endpoint_and_single_frame(ui):
    app,bridge,window,timeline=ui;click(window,find(window,'timelineRazorTool'))
    for kind in ('first','lock','single'):
        row=bridge._row('r40');idx=bridge.media_controller.index
        if kind=='lock':bridge.document.project.settings['manual_locked_ids']=['r40']
        if kind=='single':bridge.document.project.settings.pop('manual_locked_ids',None);row.end=idx.interval(420,420)[1]
        bridge._changed();QTest.qWait(30)
        before=bridge.document.snapshot();history=len(bridge.history.undo_stack)
        clip=find(window,'timelineClip_r40');x=1 if kind!='lock' else clip.width()/2
        QTest.mouseMove(window,point(clip,x,13));QTest.qWait(30)
        assert not timeline.property('razorHoverValid') and find(window,'razorCutGuide').isVisible()
        click(window,clip,x,13)
        assert bridge.document.snapshot()==before and len(bridge.history.undo_stack)==history


@pytest.mark.parametrize('source,steps,left,right',[
    ('甲\r\n乙丙',3,'甲\r\n乙','丙'),
    ('甲👩‍👩‍👧‍👦e\u0301\n乙丙',2,'甲👩‍👩‍👧‍👦','e\u0301\n乙丙'),
    ('甲\r乙丙',2,'甲\r','乙丙'),
])
def test_actual_caret_normalized_display_matches_literal_commit(ui,source,steps,left,right):
    app,bridge,window,timeline=ui
    bridge._row('r40').text_override=source;bridge._changed();QTest.qWait(40)
    click(window,find(window,'timelineRazorTool'));cut_at(window,timeline,'r40',424)
    a,b=bridge.document.project.mappings[40:42]
    click(window,find(window,'allocateTextButton'));caret=caret_to(window,steps);QTest.qWait(30)
    preview=bridge.allocationPreview(caret.property('cursorPosition'))
    assert preview['valid'] and (preview['left'],preview['right'])==(left,right)
    click(window,find(window,'allocationApplyButton'))
    assert (mapping_text(bridge.document.project,a),mapping_text(bridge.document.project,b))==(left,right)
    assert mapping_text(bridge.document.project,a)+mapping_text(bridge.document.project,b)==source


def test_orphan_after_delete_can_explicitly_complete_in_ae(ui):
    app,bridge,window,timeline=ui
    click(window,find(window,'timelineRazorTool'));cut_at(window,timeline,'r40',424)
    a,b=bridge.document.project.mappings[40:42]
    assert bridge.command('row.delete',{'id':a.id});assert bridge.command('row.edit',{'id':b.id,'text':'手动补全文字'})
    assert bridge.command('timeline.select',{'id':b.id});QTest.qWait(80)
    click(window,find(window,'allocateTextButton'))
    assert popup(window).property('visible') and not find(window,'allocationApplyButton').isEnabled()
    click(window,find(window,'allocationMarkDoneButton'))
    assert not pending_text_links(bridge.document.project) and not popup(window).property('visible')


def test_popup_selection_escape_fold_and_stale_target_do_not_mutate(ui):
    app,bridge,window,timeline=ui
    click(window,find(window,'timelineRazorTool'));cut_at(window,timeline,'r40',424)
    a,b=bridge.document.project.mappings[40:42];before=bridge.document.snapshot()
    click(window,find(window,'allocateTextButton'));caret=caret_to(window,2)
    QTest.keyClick(window,Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier);QTest.qWait(20)
    assert not find(window,'allocationApplyButton').isEnabled()
    QTest.keyClick(window,Qt.Key.Key_Escape);QTest.qWait(40)
    assert not popup(window).property('visible') and bridge.document.snapshot()==before
    click(window,find(window,'allocateTextButton'))
    timeline.setProperty('collapsed',True);QTest.qWait(40)
    assert not popup(window).property('visible') and bridge.document.snapshot()==before
    timeline.setProperty('collapsed',False);QTest.qWait(250)
    click(window,find(window,'allocateTextButton'))
    bridge.command('timeline.select',{'id':b.id});QTest.qWait(30)
    assert not bridge.state['textAllocation'] and not popup(window).property('visible') and bridge.document.snapshot()==before


def test_pending_fade_has_real_intermediates_and_motion_off_settles(ui,tmp_path):
    app,bridge,window,timeline=ui
    click(window,find(window,'timelineRazorTool'));cut_at(window,timeline,'r40',424,wait_ms=20)
    a,b=bridge.document.project.mappings[40:42];clip=find(window,'timelineClip_'+b.id)
    endpoints=(find(window,'pendingClipFill_'+b.id).property('color').name(),clip.property('color').name())
    samples=[];colors=[]
    for _ in range(12):
        samples.append(clip.property('pendingOpacity'));colors.append(pixel(window,clip));QTest.qWait(20)
    assert any(0<value<1 for value in samples),samples
    assert samples[-1]==1 and not clip.property('pendingAnimating')
    assert any(color not in endpoints for color in colors),colors
    entrance=list(samples);entrance_colors=list(colors)
    x,width,height=clip.x(),clip.width(),clip.height()
    assert bridge.command('textAllocation.begin',{'id':a.id})
    data=bridge.state['textAllocation'];assert bridge.command('textAllocation.apply',{**data,'offsetUtf16':4})
    assert clip.property('pendingDuration')==200
    samples=[];colors=[]
    for _ in range(12):samples.append(clip.property('pendingOpacity'));colors.append(pixel(window,clip));QTest.qWait(20)
    assert any(0<value<1 for value in samples) and samples[-1]==0,samples
    assert (clip.x(),clip.width(),clip.height())==(x,width,height)
    assert any(color not in endpoints for color in colors),colors
    (evidence(tmp_path)/'razor-motion-state.json').write_text(json.dumps({'durationMs':200,'easing':'OutCubic','entranceOpacity':entrance,'entrancePixels':entrance_colors,'exitOpacity':samples,'exitPixels':colors,'geometryUnchanged':True},indent=2))
    bridge.command('history.undo',{});QTest.qWait(20)
    bridge.command('settings.set',{'key':'motion','value':False});QTest.qWait(20)
    clip=find(window,'timelineClip_'+b.id)
    assert clip.property('pendingOpacity')==1 and not clip.property('pendingAnimating')


@pytest.mark.parametrize('hide',['fold','stage','workspace'])
def test_queued_open_cannot_escape_hidden_timeline(ui,hide):
    app,bridge,window,timeline=ui
    click(window,find(window,'timelineRazorTool'));cut_at(window,timeline,'r40',424)
    a,b=bridge.document.project.mappings[40:42];before=bridge.document.snapshot()
    assert bridge.command('textAllocation.begin',{'id':a.id})
    if hide=='fold':timeline.setProperty('collapsed',True)
    elif hide=='stage':bridge.command('stage.set',{'stage':'qc'})
    else:window.setProperty('workspace','settings')
    QTest.qWait(180)
    assert not popup(window).property('visible') and not bridge.state['textAllocation']
    assert bridge.document.snapshot()==before


def test_narrow_ae_popup_resize_controls_and_edge_geometry(ui,tmp_path):
    app,bridge,window,timeline=ui
    click(window,find(window,'timelineRazorTool'));cut_at(window,timeline,'r40',424)
    a,b=bridge.document.project.mappings[40:42]
    window.resize(760,600);QTest.qWait(200)
    outer=find(window,'makeScroll')
    assert outer.property('contentItem') is None
    QTest.qWait(70)
    action=find(window,'allocateTextButton');p=point(action)
    assert 0<=p.x()<=760 and 0<=p.y()<=600
    click(window,action);assert popup(window).property('visible')
    for name in ('allocationCancelButton','allocationApplyButton','allocationMarkDoneButton'):
        control=find(window,name);p=point(control)
        assert 0<=p.x()<=window.width() and 0<=p.y()<=window.height(),(name,p)
    assert window.grabWindow().save(str(evidence(tmp_path)/'ae-razor-narrow-760x600.png'))
    window.resize(1000,700);QTest.qWait(150)
    for name in ('allocationCancelButton','allocationApplyButton'):
        p=point(find(window,name));assert 0<=p.x()<=1000 and 0<=p.y()<=700
    click(window,find(window,'allocationCancelButton'))
    timeline.forceActiveFocus();QTest.keyClick(window,Qt.Key.Key_V);QTest.qWait(50)
    clip=find(window,'timelineClip_'+a.id)
    first,last=find(window,'cueEdge_first'),find(window,'cueEdge_last')
    assert first.width()==last.width()==10 and first.height()==last.height()==26
    assert first.x()==0 and last.x()==clip.width()-10 and clip.height()==26
