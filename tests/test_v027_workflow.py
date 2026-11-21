"""Exercise mode switching and independent review-new save decisions through the Bridge."""
from copy import deepcopy
import pytest
from test_qt_entry_initialization_dev5 import bridge, settled
from lyricsync.model import Translation, Mapping
from lyricsync.project_document import write_document, load_document
from desktop_qt.ocr_capsule_pairing_dialog import OcrCapsulePairingDialog


def test_nondestructive_switch_retains_dirty_pool_and_stored_regions(bridge,monkeypatch,tmp_path):
    b=bridge
    assert b.command('project.newEntry',{'mode':'ocr'})
    p=b.document.project
    p.translations=[Translation('pool','未配对')]
    p.settings.update(ocr_regions=[{'id':'saved','box':[0,0,100,40],'enabled':False}],frame_markers=[{'id':'keep','frame':1}])
    b._changed()
    before=deepcopy(p.to_dict())
    identity=b.document.id
    history=list(b.history.undo_stack)
    monkeypatch.setattr('desktop_qt.dialogs.confirm_mode_switch',lambda:pytest.fail('empty switch is nondestructive'))
    assert b.command('editor.start',{'mode':'audio'})
    assert b.document.id==identity and p.to_dict()==before
    assert b.history.undo_stack[:-1]==history and len(b.history.undo_stack)==len(history)+1
    assert b.document.revision!=b.document.saved_revision and b.state['entryMode']=='audio'
    path=tmp_path/'mode.lyria'
    assert b.command('project.saveAs',{'path':str(path)})
    settled(b)
    assert load_document(path).entry_mode=='audio'
    assert b.command('project.open',{'path':str(path)})
    settled(b)
    assert b.document.project.to_dict()==before and b.state['entryMode']=='audio'
    b._task['running']=True
    assert not b.command('editor.start',{'mode':'edit'}) and b.state['entryMode']=='audio'
    b._task['running']=False


@pytest.mark.parametrize('choice',['cancel','cancel_path','failure','save','discard','clean'])
def test_only_review_new_asks_once(bridge,tmp_path,monkeypatch,choice):
    b=bridge
    b.command('project.newEntry',{'mode':'edit'})
    b.document.project.mappings=[Mapping('cue',[],text_override='保留',start=.1,end=.5)]
    b.stage='review';b._changed()
    if choice=='clean':b.document.mark_saved(b.document.revision)
    before=b.document
    seen=[]
    b.closeDialogRequested.connect(lambda details:seen.append(details))
    assert b.command('project.newFromReview',{})
    if choice!='clean':
        assert len(seen)==1 and seen[0]['newFromReview'] and b.document is before
        dest=tmp_path/'copy.lyria'
        monkeypatch.setattr(b,'_path',lambda *_a,**_k:'' if choice=='cancel_path' else str(dest))
        if choice=='failure':
            def fail(*_):raise OSError('synthetic failure')
            monkeypatch.setattr('desktop_qt.controller.write_document',fail)
        assert b.command('close.resolve',{'choice':choice if choice in ('discard','cancel') else 'save'})
        settled(b)
        if choice in ('failure','cancel','cancel_path'):
            assert b.document is before and b.stage=='review' and not b._startup_visible
            return
        if choice=='save':assert load_document(dest).project.mappings[0].id=='cue'
    assert len(seen)==(0 if choice=='clean' else 1) and b.document is not before
    assert b._startup_visible and not b.document.main_timeline
    assert b.command('project.newEntry',{'mode':'audio'}) and len(seen)==(0 if choice=='clean' else 1)


def test_right_import_candidate_draft_close_apply_reopen_and_undo(bridge,tmp_path):
    b=bridge
    b.command('project.newEntry',{'mode':'ocr'})
    b.document.project.mappings=[Mapping('keep',[],text_override='原词',text_role='source_import',start=.2,end=.8,human_edited=True)]
    b._changed()
    p=deepcopy(b.document.project.to_dict())
    d=OcrCapsulePairingDialog(b)
    d.load_pool([Translation('abc','中文')])
    assert b.document.project.to_dict()==p
    d.history(False);assert not d.session.project.translations
    d.history(True);assert d.session.project.translations
    d.reject()
    assert b.document.project.to_dict()==p
    d=OcrCapsulePairingDialog(b)
    d.load_pool([Translation('abc','中文')]);d.apply()
    assert [t.text for t in b.document.project.translations]==['中文']
    assert b.document.project.to_dict()['mappings']==p['mappings']
    path=tmp_path/'pool.lyria'
    write_document(b.document.snapshot(explicit_save=True),path)
    assert load_document(path).project.to_dict()==b.document.project.to_dict()
    assert b.command('history.undo',{}) and b.document.project.to_dict()==p

def test_review_rejects_roi_writes_and_preserves_disabled_card(bridge):
    b=bridge
    assert b.command('project.newEntry',{'mode':'ocr'})
    b.document.project.video.update(width=800,height=600,duration=2)
    p=b.document.project
    p.settings.update(ocr_multi_region=True,ocr_regions=[
        dict(id='saved',box=[40,300,520,440],enabled=False),
        dict(id='active',box=[100,150,600,230],enabled=True)])
    b._changed()
    assert b.command('stage.set',{'stage':'review'})
    before=deepcopy(p.to_dict())
    for name,payload in [('roi.set',dict(x=.1,y=.5,width=.7,height=.3)),('roi.multi',dict(action='remove',id='saved')),('roi.auto',{})]:
        assert not b.command(name,payload)
        assert p.to_dict()==before
    assert b.command('stage.set',{'stage':'import'})
    assert p.to_dict()==before and len(b.state['regionCards'])==2
    assert [(card['id'],card['enabled']) for card in b.state['regionCards']]==[('saved',False),('active',True)]
