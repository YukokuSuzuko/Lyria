from copy import deepcopy
from PySide6.QtCore import Qt, QPointF
from PySide6.QtTest import QTest
from test_qt_review_selection import application, ui, find, settle
from desktop_qt.review_actions import row_metadata, speaker_color
from lyricsync.model import Project, Mapping


def test_default_role_exists_without_name_or_export_mutation():
    p=Project();m=Mapping('a',[],start=1,end=2);p.mappings=[m]
    before=deepcopy(p.to_dict())
    info=row_metadata(p,p.mappings[0],'a'*17)
    assert p.mappings[0].speaker_id==''
    assert info['speakerDefault'] and info['speakerColor']==''
    assert info['cpsWarning'] and info['cps']==17
    assert not info['specialStyle']
    assert p.to_dict()==before
    assert speaker_color('actor')==speaker_color('actor')


def test_ctrl_shift_scope_batch_actor_undo_and_lock(ui):
    b=ui.bridge;p=b.document.project
    assert b.command('review.select',{'id':'r40'})
    assert b.command('review.select',{'id':'r42','ctrl':True})
    assert b._multi_selected=={'r40','r42'}
    assert b.command('review.select',{'id':'r45','shift':True})
    assert b._multi_selected=={'r42','r43','r44','r45'}
    before=deepcopy(p.to_dict())
    assert b.command('review.assignSpeaker',{'name':'Kotoha'})
    ids={r.id for r in p.mappings if r.speaker_name}
    assert ids==b._multi_selected
    assert all(r['speakerColor'] for r in b.state['rows'] if r['id'] in ids)
    assert b.command('history.undo',{})
    p=b.document.project
    assert p.to_dict()==before
    p.settings['manual_locked_ids']=['r43'];b._changed()
    before=deepcopy(p.to_dict())
    assert not b.command('review.assignSpeaker',{'name':'Hanon'})
    assert p.to_dict()==before


def test_batch_style_does_not_change_other_rows_or_default(ui):
    b=ui.bridge;p=b.document.project
    from lyricsync.speakers import default_style
    p.settings['subtitle_styles']={'default':{'name':'Base','style':default_style(p)},'special':{'name':'Top','style':default_style(p)}}
    p.settings['current_subtitle_style']='default';b._changed()
    b.command('review.select',{'id':'r40'})
    b.command('review.select',{'id':'r41','ctrl':True})
    assert b.command('review.assignStyle',{'id':'special'})
    assert {r.id for r in p.mappings if r.style_id=='special'}=={'r40','r41'}
    assert p.settings['current_subtitle_style']=='default'
    assert all(r['specialStyle'] for r in b.state['rows'] if r['id'] in ('r40','r41'))


def test_no_multi_button_and_empty_evidence_hidden(ui):
    assert find(ui.window,'multiSelectMode') is None
    assert find(ui.window,'mergeSelected') is None
    assert find(ui.window,'selectedSpeakerStyleSummary') is None
    assert not find(ui.window,'reviewEvidencePanel').isVisible()
    assert find(ui.window,'reviewFullWidthButton') is not None


def test_real_ctrl_click_preserves_text_and_selects_multiple(ui):
    b=ui.bridge;before=deepcopy(b.document.project.to_dict())
    from test_qt_review_selection import assert_selected_visible, contained
    assert_selected_visible(ui,'r40')
    a=find(ui.window,'lyricSelect_r40')
    QTest.mouseClick(ui.window,Qt.LeftButton,Qt.NoModifier,a.mapToScene(QPointF(20,15)).toPoint())
    from test_qt_review_selection import visible_rows
    targets=[identity for _,identity in visible_rows(ui) if identity!='r40' and contained(ui,identity)]
    assert targets
    target=targets[0]
    a=find(ui.window,'lyricSelect_'+target)
    assert a is not None
    QTest.mouseClick(ui.window,Qt.LeftButton,Qt.ControlModifier,a.mapToScene(QPointF(20,15)).toPoint())
    assert b._multi_selected=={'r40',target}
    assert b.document.project.to_dict()==before


def test_full_width_independent_toggle_edit_restore(ui,tmp_path):
    b=ui.bridge
    b._entry_mode='edit';b._refresh(False)
    snapshot=deepcopy(b.document.project.to_dict())
    button=find(ui.window,'reviewFullWidthButton')
    QTest.mouseClick(ui.window,Qt.LeftButton,Qt.NoModifier,button.mapToScene(QPointF(button.width()/2,button.height()/2)).toPoint())
    settle(ui.app,lambda:bool(ui.window.property('fullWidthReview')))
    full=find(ui.window,'fullWidthReviewWorkspace')
    assert full.isVisible()
    assert full.width()>ui.window.width()*.8
    assert b.document.project.to_dict()==snapshot
    text=find(ui.window,'fullTranslation_r40')
    assert text is not None
    QTest.mouseDClick(ui.window,Qt.LeftButton,Qt.NoModifier,text.mapToScene(QPointF(30,15)).toPoint())
    assert text.hasActiveFocus() and not text.property('readOnly')
    QTest.keyClick(ui.window,Qt.Key_End)

    for letter in ' edited':QTest.keyClick(ui.window,letter)
    assert b.state['rows'][40]['chinese'].endswith(' edited')
    QTest.keyClick(ui.window,Qt.Key_Escape)
    close=find(ui.window,'closeFullWidthReview')
    QTest.mouseClick(ui.window,Qt.LeftButton,Qt.NoModifier,close.mapToScene(QPointF(close.width()/2,close.height()/2)).toPoint())
    settle(ui.app,lambda:not ui.window.property('fullWidthReview'))
    assert find(ui.window,'reviewPanel').isVisible()
    assert not b._editor_active
    assert b.command('history.undo',{})
    assert b.document.project.to_dict()==snapshot


def test_confirmed_role_can_be_reassigned_without_unlocking_pairing(ui):
    b=ui.bridge;p=b.document.project
    p.mappings[40].confirmed=True;b._changed()
    assert b.command('review.assignSpeaker',{'name':'A'})
    assert b.command('review.assignSpeaker',{'name':'B'})
    assert b.state['selection']['current']['speakerName']=='B'
    assert p.mappings[40].confirmed
    assert b.command('review.assignSpeaker',{})
    assert p.mappings[40].speaker_id==''


def test_shortcut_assigns_and_advances_but_not_during_text_entry(ui):
    b=ui.bridge
    assert b.command('review.assignSpeaker',{'name':'A'})
    assert b.command('review.select',{'id':'r45'})
    assert b.command('review.select',{'id':'r41','ctrl':True})
    assert b._multi_selected=={'r45','r41'}
    assert b.command('review.speakerShortcut',{'number':1})
    assert b._multi_selected=={'r45','r41'} and b.state['rows'][45]['speakerName']=='A'
    assert b.selected=='r41'
    assert b.state['rows'][41]['speakerName']=='A'
    assert b.command('review.select',{'id':'r40'})
    assert b.command('review.select',{'id':'r41','ctrl':True})
    assert b.command('review.speakerShortcut',{'number':0})
    assert b.selected=='r41' and b._multi_selected=={'r40','r41'}
    assert not b.state['rows'][40]['speakerName'] and not b.state['rows'][41]['speakerName']
    before=deepcopy(b.document.project.to_dict())
    b._editor_active=True
    assert b.command('review.speakerShortcut',{'number':1})
    assert b.document.project.to_dict()==before and b.selected=='r41'
    b._editor_active=False


def test_capsule_creates_actor_on_enter_for_selected_rows(ui):
    b=ui.bridge
    b.command('review.select',{'id':'r40'})
    b.command('review.select',{'id':'r41','ctrl':True})
    capsule=find(ui.window,'visibleSpeakerAction')
    QTest.mouseClick(ui.window,Qt.LeftButton,Qt.NoModifier,capsule.mapToScene(QPointF(20,15)).toPoint())
    from test_qt_review_selection import walk
    field=next(i for i in walk(ui.window.contentItem()) if i.property('placeholderText')=='说话人' and i.isVisible())
    QTest.mouseClick(ui.window,Qt.LeftButton,Qt.NoModifier,field.mapToScene(QPointF(20,15)).toPoint())
    for letter in 'Singer':QTest.keyClick(ui.window,letter)
    QTest.keyClick(ui.window,Qt.Key_Return)
    assert {r['id'] for r in b.state['rows'] if r['speakerName']=='Singer'}=={'r40','r41'}


def test_full_width_ime_preedit_is_not_persisted(ui):
    from PySide6.QtGui import QInputMethodEvent
    b=ui.bridge;b._entry_mode='edit';b._refresh(False)
    ui.window.setProperty('fullWidthReview',True)
    settle(ui.app,lambda:find(ui.window,'fullTranslation_r40') is not None)
    field=find(ui.window,'fullTranslation_r40')
    QTest.mouseDClick(ui.window,Qt.LeftButton,Qt.NoModifier,field.mapToScene(QPointF(20,15)).toPoint())
    assert field.hasActiveFocus()
    before=b.state['rows'][40]['chinese']
    preedit=QInputMethodEvent('候选文字',[]);ui.app.sendEvent(field,preedit)
    assert b.state['rows'][40]['chinese']==before
    commit=QInputMethodEvent();commit.setCommitString('完成')
    ui.app.sendEvent(field,commit)
    assert '完成' in b.state['rows'][40]['chinese']
    assert '候选文字' not in b.state['rows'][40]['chinese']
    QTest.keyClick(ui.window,Qt.Key_Escape)
    assert not b._editor_composing


def test_non_ocr_review_cancel_stays_visible_without_layout_reflow(ui,monkeypatch):
    b=ui.bridge;b._entry_mode='audio';b._refresh(False)
    from test_qt_review_selection import walk
    def active_cancel():
        return next(x for x in walk(ui.window.contentItem()) if x.objectName()=='reviewCancelCandidateTask')
    button=active_cancel();assert not button.isVisible() and not button.isEnabled()
    panel=find(ui.window,'reviewPanel')
    QTest.qWait(220)
    before=(panel.height(), ui.view.height())
    calls=[];monkeypatch.setattr(b.jobs,'cancel',lambda identity:calls.append(identity))
    b._candidate_job='test-vocal';b._candidate_task=dict(running=True,source='vocal',label='很长的后台自动打轴任务说明'*5,progress=.4,cancelling=False)
    b._refresh(False);QTest.qWait(200)
    button=active_cancel();assert button.isEnabled()
    assert (panel.height(), ui.view.height())==before
    assert find(ui.window,'candidateSourceBar') is None
    QTest.mouseClick(ui.window,Qt.LeftButton,Qt.NoModifier,button.mapToScene(QPointF(button.width()/2,button.height()/2)).toPoint())
    assert calls==['test-vocal'] and b._candidate_task['cancelling']
    assert button.isVisible() and not button.isEnabled()
    b._candidate_job=None;b._candidate_task.update(running=False,cancelling=False);b._refresh(False)
    QTest.qWait(100)
    assert not active_cancel().isVisible() and not active_cancel().isEnabled()
    assert (panel.height(), ui.view.height())==before
