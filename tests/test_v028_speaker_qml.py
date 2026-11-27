import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtTest import QTest
from test_qt_review_selection import application, ui, find, settle


def test_packaged_probe_named_payload_and_review_save(application, tmp_path):
    from PySide6.QtTest import QTest
    from desktop_qt.ocr_workflow_probe import OcrWorkflowProbe
    from test_qt_timeline_input import timeline_session, wait

    with timeline_session(application, tmp_path/'profile') as (bridge, window):
        probe = OcrWorkflowProbe(application, bridge, window, tmp_path/'probe.json')
        probe.command('project.newEntry', mode='audio')
        for role, text in [('translation', '你好世界\n最后一句'), ('source', 'HELLO WORLD\nFINAL LINE')]:
            path = tmp_path / (role+'.txt')
            path.write_text(text, encoding='utf8')
            probe.command('input.import', role=role, poolOnly=(role=='translation'), path=str(path))
            wait(application, lambda: not bridge.jobs.active)
        for ready in probe.check_audio_review(probe.lookup('closeDialog')):
            QTest.qWait(20)
            wait(application, ready)
        assert [result['name'] for result in probe.results] == [
            'mixed_speakers_review_and_full_width', 'rejected_new_save_cannot_clear_later_save_as']


@pytest.mark.parametrize('full',[False,True])
def test_mixed_actor_explicit_commit_ime_and_scope_switch(ui,full):
    b,w=ui.bridge,ui.window
    if full: w.setProperty('fullWidthReview',True)
    field=find(w,'fullSpeakerInput' if full else 'currentSpeakerInput')
    settle(ui.app,lambda:field.isVisible())
    b.command('review.assignSpeaker',{'name':'主唱甲'})
    b.command('review.select',{'id':'r42','ctrl':True})
    assert field.property('text')=='' and field.property('placeholderText')=='混合（已选 2 句）'
    depth=len(b.history.undo_stack)
    field.forceActiveFocus()
    ui.app.sendEvent(field,QInputMethodEvent('zhong',[]))
    assert field.property('inputMethodComposing') and len(b.history.undo_stack)==depth
    b.command('review.select',{'id':'r44'})
    assert len(b.history.undo_stack)==depth and not field.hasActiveFocus()
    assert b.document.project.mappings[40].speaker_name=='主唱甲' and not b.document.project.mappings[44].speaker_name
    b.command('review.select',{'id':'r40'})
    b.command('review.select',{'id':'r42','ctrl':True})
    field.forceActiveFocus()
    ui.app.sendEvent(field,QInputMethodEvent('hechang',[]))
    event=QInputMethodEvent();event.setCommitString('合唱')
    ui.app.sendEvent(field,event)
    assert len(b.history.undo_stack)==depth
    QTest.keyClick(w,Qt.Key_Return)
    assert [r.speaker_name for r in b.document.main_timeline if r.id in ('r40','r42')]==['合唱','合唱']
    assert len(b.history.undo_stack)==depth+1
    QTest.keyClick(w,Qt.Key_Z,Qt.ControlModifier)
    assert [r.speaker_name for r in b.document.main_timeline if r.id in ('r40','r42')]==['主唱甲','']
    assert b.command('review.selectSameSpeaker',{'id':'r40'})
    assert b._multi_selected=={'r40'}

def test_prefix_tab_then_enter_and_reference_in_disabled_region(ui):
    b,w=ui.bridge,ui.window
    b.document.project.video.update(width=320,height=180)
    b.document.project.settings['ocr_regions']=[dict(id='r',box=[20,10,180,40],enabled=False,speaker_name='Conductor')]
    b._changed()
    field=find(w,'currentSpeakerInput')
    field.forceActiveFocus()
    QTest.keyClick(w,'C');QTest.keyClick(w,'o')
    QTest.keyClick(w,Qt.Key_Tab)
    assert field.property('text')=='Conductor' and not b.document.project.mappings[40].speaker_name
    QTest.keyClick(w,Qt.Key_Return)
    assert b.document.project.mappings[40].speaker_name=='Conductor'
    assert b.command('review.assignSpeaker',{})
    assert [o['name'] for o in b.state['reviewSpeakers']]==['Conductor']
    assert b.command('stage.set',{'stage':'import'})
    assert b.command('roi.multi',{'action':'speaker_name','id':'r','value':''})
    assert not b.state['reviewSpeakers'] and b.document.project.settings['ocr_regions'][0]['enabled'] is False
    assert b.command('history.undo',{})
    assert [o['name'] for o in b.state['reviewSpeakers']]==['Conductor']
