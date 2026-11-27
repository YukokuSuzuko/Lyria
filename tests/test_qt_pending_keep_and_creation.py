from copy import deepcopy
from pathlib import Path
import os
import pytest
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt, QPointF
from test_qt_timeline_input import timeline_video, ui, find, click, point, wait
from test_qt_timeline_snap import application, base_application
from test_qt_razor_input import cut_at
from lyricsync.editing import pending_text_links, mapping_text
from lyricsync.project_document import write_document,load_document


def chain(ui):
    app,b,w,t=ui
    click(w,find(w,'timelineRazorTool'))
    cut_at(w,t,'r40',424)
    a,c=b.document.project.mappings[40:42]
    cut_at(w,t,c.id,427)
    return b.document.project.mappings[40:43]


def test_keep_clears_only_shown_pair_without_confirming_or_changing_rows(ui,tmp_path):
    app,b,w,t=ui;a,c,d=chain(ui)
    assert b.command('timeline.select',{'id':d.id});QTest.qWait(40)
    action=b.state.get('pendingTextAction',{})
    assert (action.get('leftId'),action.get('rightId'))==(a.id,c.id)
    before=deepcopy(b.document.project.to_dict());history=len(b.history.undo_stack)
    assert find(w,'reviewPendingText').isVisible()
    click(w,find(w,'reviewKeepSplitText'))
    after=b.document.project.to_dict()
    assert after['mappings']==before['mappings'] and after['translations']==before['translations']
    assert pending_text_links(b.document.project)==[{'left':c.id,'right':d.id}]
    assert not any(m.confirmed for m in (a,c,d))
    assert len(b.history.undo_stack)==history+1 and t.property('tool')=='razor'
    assert b.state['pendingTextAction']['leftId']==c.id
    click(w,find(w,'reviewKeepSplitText'))
    assert not pending_text_links(b.document.project)
    assert not find(w,'reviewPendingText').isVisible()
    assert not b.command('row.confirm',{'id':d.id,'value':True})
    assert not b._row(d.id).confirmed
    assert b.command('history.undo',{}) and pending_text_links(b.document.project)==[{'left':c.id,'right':d.id}]
    assert b.command('history.undo',{}) and b.document.project.to_dict()==before
    assert b.command('history.redo',{}) and b.document.project.to_dict()==after
    path=tmp_path/'kept.lyria';write_document(b.document.snapshot(),path)
    assert load_document(path).project.to_dict()==after


def test_stale_keep_action_cannot_clear_a_different_pair(ui):
    app,b,w,t=ui;a,c,d=chain(ui)
    action=dict(b.state.get('pendingTextAction',{}));assert action.get('leftId')
    assert b.command('row.edit',{'id':a.id,'text':'新的内容'})
    before=b.document.project.to_dict();history=len(b.history.undo_stack)
    assert not b.command('textAllocation.keep',action)
    assert b.document.project.to_dict()==before and len(b.history.undo_stack)==history


@pytest.mark.parametrize('mode',[3,5,0])
@pytest.mark.parametrize('entry',['after-last','before-first','repeat','range','playhead','manual','split'])
def test_all_creation_routes_reveal_new_row_without_resetting_zoom(ui,mode,entry):
    app,b,w,t=ui
    t.setProperty('rowMode',mode);t.setProperty('followEnabled',False);t.setProperty('manualView',True)
    t.setProperty('viewFirst',398);t.setProperty('viewLast',449);QTest.qWait(60)
    before={m.id for m in b.document.project.mappings};span=t.property('span')
    if entry=='after-last':assert b.command('row.add',{'after':'r99'})
    elif entry=='before-first':assert b.command('row.add',{'before':'r0'})
    elif entry=='repeat':assert b.command('row.repeat',{'id':'r99'})
    elif entry=='range':assert b.command('timeline.createRange',{'first':1151,'last':1160})
    elif entry=='playhead':
        b.media_controller._state['displayedFrame']=1151;b.mediaChanged.emit()
        assert b.command('timeline.createAtPlayhead',{})
    elif entry=='manual':
        assert b.command('timeline.manualMode',{'enabled':True});QTest.qWait(230)
        b.media_controller._state['displayedFrame']=1151;b.mediaChanged.emit()
        assert b.command('timeline.manualMark',{})
        b.media_controller._state['displayedFrame']=1160;b.mediaChanged.emit()
        assert b.command('timeline.manualMark',{})
    else:
        assert b.command('marker.add',{'frame':1014})
        assert b.command('timeline.select',{'id':'r99'})
        assert b.command('row.split',{'id':'r99','frame':1014,'offsetUtf16':2})
    QTest.qWait(100)
    assert b.selected not in before
    tracks=find(w,'timelineTracks');idx=b.state['selectedTimelineIndex']
    top=idx*t.property('rowHeight')-tracks.property('contentY')
    assert top>=-.5 and top+t.property('rowHeight')<=tracks.height()+.5,(entry,mode,top,tracks.height())
    assert t.property('span')==span
    row=b.state['selection']['current']
    if row['firstFrame']:
        assert row['lastFrame']>=t.property('viewFirst') and row['firstFrame']<t.property('viewLast')
    else:assert (t.property('viewFirst'),t.property('viewLast'))==(398,449)
    assert not t.property('followEnabled')
    assert b.command('history.undo',{})
    assert {m.id for m in b.document.project.mappings}==before
