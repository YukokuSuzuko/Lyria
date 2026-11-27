from copy import deepcopy
from PySide6.QtCore import Qt, QPointF
from PySide6.QtTest import QTest
from PySide6.QtQml import QQmlExpression, qmlContext
from test_qt_review_selection import application, ui, find, settle, enter_lyric_editing


def evaluate(item,code):
    e=QQmlExpression(qmlContext(item),item,code);r=e.evaluate();assert not e.hasError(),e.error().toString();return r


def test_select_all_visible_clear_and_native_text(ui):
    panel=find(ui.window,'reviewPanel');panel.forceActiveFocus()
    QTest.keyClick(ui.window,Qt.Key_A,Qt.ControlModifier)
    assert ui.bridge._multi_selected=={r['id'] for r in ui.bridge.model.items}
    QTest.keyClick(ui.window,Qt.Key_Escape);assert not ui.bridge._multi_selected
    editor=enter_lyric_editing(ui)
    QTest.keyClick(ui.window,Qt.Key_A,Qt.ControlModifier)
    assert editor.property('selectedText')==editor.property('text')
    assert not ui.bridge._multi_selected
    QTest.keyClick(ui.window,Qt.Key_Escape)
    field=find(ui.window,'firstFrameField');field.forceActiveFocus()
    QTest.keyClick(ui.window,Qt.Key_A,Qt.ControlModifier)
    assert field.property('selectedText')==field.property('text')
    assert not ui.bridge._multi_selected


def test_alt_nine_batch_then_zero_single_and_advance(ui):
    b=ui.bridge
    for n in range(9):
        assert b.command('review.select',{'id':'r'+str(n)})
        assert b.command('review.assignSpeaker',{'name':'Role'+str(n+1)})
    b.command('review.select',{'id':'r40'});b.command('review.select',{'id':'r42','ctrl':True})
    before={r.id:r.speaker_name for r in b.document.project.mappings}
    find(ui.window,'reviewPanel').forceActiveFocus();QTest.keyClick(ui.window,Qt.Key_9,Qt.AltModifier)
    assert b.selected=='r42' and b._multi_selected=={'r40','r42'}
    assert b.document.project.mappings[40].speaker_name==b.document.project.mappings[42].speaker_name=='Role9'
    assert all(r.speaker_name==before[r.id] for r in b.document.project.mappings if r.id not in b._multi_selected)
    assert b.command('review.select',{'id':'r42'})
    QTest.keyClick(ui.window,Qt.Key_0,Qt.AltModifier)
    assert b.selected=='r43' and not b.document.project.mappings[42].speaker_name


def test_timing_digits_do_not_move_confirm(ui):
    panel=find(ui.window,'reviewPanel');button=find(ui.window,'confirmCurrent');strip=find(ui.window,'reviewTimingStrip')
    points=[]
    for last in (42,57,153,99999):
        r=ui.bridge.document.project.mappings[40];r.start=1;r.end=last/10
        ui.bridge._changed();QTest.qWait(60)
        points.append((button.mapToItem(panel,QPointF()).y(),strip.height()))
    assert max(y for y,h in points)-min(y for y,h in points)<.01,points
    assert all(h==30 for y,h in points),points


def test_playhead_follow_works_in_gaps_and_empty_timeline(ui):
    t=find(ui.window,'frameTimeline')
    evaluate(t,'view(1,101); followEnabled=true')
    evaluate(t,'media = ({playing:true,displayedFrame:250,totalFrames:1500})')
    evaluate(t,'pagePlayhead()');assert t.property('viewFirst')<=250<t.property('viewLast')
    ui.bridge.document.project.mappings=[];ui.bridge._changed()
    evaluate(t,'view(1,101); followEnabled=true; pagePlayhead()')
    assert t.property('viewFirst')<=250<t.property('viewLast')
    evaluate(t,'beginPan(20,20,false); dragPan(24,20); endPan()')
    assert t.property('followEnabled')
    evaluate(t,'view(1,101); pagePlayhead()');assert t.property('viewFirst')==1
    evaluate(t,'media = ({playing:true,displayedFrame:251,totalFrames:1500}); pagePlayhead()')
    assert t.property('viewFirst')<=251<t.property('viewLast')


def test_workspace_has_no_outer_scroll_and_inline_capsules(ui):
    ui.window.setWidth(1000);ui.window.setHeight(720);QTest.qWait(200)
    outer=find(ui.window,'makeScroll');content=find(ui.window,'verticalSplit')
    assert content.height()<=outer.height()+.1
    assert outer.property('contentHeight') is None
    b=ui.bridge;b.command('review.assignSpeaker',{'name':'Actor'})
    assert find(ui.window,'rowSpeaker_'+b.selected) is not None


def test_follow_reset_only_for_new_document(ui,tmp_path):
    t=find(ui.window,'frameTimeline');b=ui.bridge
    identity=b.document.id
    from lyricsync.project_document import write_document
    saved=tmp_path/'follow-load.lyria'
    write_document(b.document.snapshot(),saved)
    t.setProperty('followEnabled',False)
    b.command('view.layout',{'reviewVisible':False});b.command('view.layout',{'reviewVisible':True})
    QTest.qWait(40)
    assert b.document.id==identity and not t.property('followEnabled')
    b._new();QTest.qWait(60)
    assert b.document.id!=identity and t.property('followEnabled')
    t.setProperty('followEnabled',False)
    b.command('project.open',{'path':str(saved)})
    settle(ui.app,lambda:b.document.id==identity)
    assert t.property('followEnabled')


def test_full_width_edit_focus_and_selection(ui):
    ui.window.setProperty('fullWidthReview',True);QTest.qWait(100)
    panel=find(ui.window,'fullWidthReviewWorkspace');panel.forceActiveFocus()
    QTest.keyClick(ui.window,Qt.Key_A,Qt.ControlModifier)
    assert len(ui.bridge._multi_selected)==len(ui.bridge.model.items)
    QTest.keyClick(ui.window,Qt.Key_Escape);assert not ui.bridge._multi_selected
    QTest.keyClick(ui.window,Qt.Key_F2);QTest.qWait(80)
    editor=find(ui.window,'fullTranslation_'+ui.bridge.selected)
    assert editor.property('activeFocus') and editor.property('editing')
    QTest.keyClick(ui.window,Qt.Key_A,Qt.ControlModifier)
    assert editor.property('selectedText')==editor.property('text')
    assert not ui.bridge._multi_selected
    QTest.keyClick(ui.window,Qt.Key_Escape);assert not editor.property('editing')


def test_source_generation_resets_follow_but_seek_does_not(ui,tmp_path):
    b=ui.bridge;t=find(ui.window,'frameTimeline')
    t.setProperty('followEnabled',False)
    b.media_controller._state['requestedFrame']=12;b.mediaChanged.emit();QTest.qWait(40)
    assert not t.property('followEnabled')
    from test_dev4_spectrogram import encoded_audio
    first=encoded_audio(tmp_path/'first.wav',1)
    b.media_controller.open(str(first))
    settle(ui.app,lambda:b.media['ready'])
    assert t.property('followEnabled')
    t.setProperty('followEnabled',False)
    second=encoded_audio(tmp_path/'second.wav',1)
    b.media_controller.open(str(second))
    settle(ui.app,lambda:b.media['ready'] and b.media_controller.path==str(second))
    assert t.property('followEnabled')
    t.setProperty('followEnabled',False)
    b._refresh(False);ui.window.resize(1100,800);QTest.qWait(60)
    assert not t.property('followEnabled')
