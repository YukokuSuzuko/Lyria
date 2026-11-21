"""AI candidate projection is read-only, reversible, and shared by all views."""
from copy import deepcopy
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from desktop_qt.api_review_dialogs import show_alignment_plan
from lyricsync.api_alignment import make_plan
from lyricsync.project_document import write_document,load_document
from test_qt_api_review_workflows import application,bridges,no_external_io,seed,split_response


def prepared(bridges,monkeypatch):
    b=bridges();p=seed(b)
    plan=make_plan(p,b.media_controller.index,split_response(),['m1'])
    b._alignment={'plan':plan}
    b.media_controller._state.update(ready=True,displayedFrame=3,requestedFrame=3,positionSeconds=.08,playing=True)
    b._refresh()
    def forbidden(*a,**k):pytest.fail('preview must not seek, pause, or play media')
    for method in ('seek','pause','play'):monkeypatch.setattr(b.media_controller,method,forbidden)
    return b,plan


def test_toggle_projects_all_three_views_without_mutation_and_save_stays_original(bridges,monkeypatch,tmp_path):
    b,plan=prepared(bridges,monkeypatch);before=b.document.snapshot();transport=b.media_controller.snapshot()
    candidate_ids=[x['id'] for x in plan['diff'][0]['after']]+['m2','m3']
    for _ in range(8):
        assert b.command('alignment.preview',{'enabled':True}),b.state['error']
        assert [r['id'] for r in b.timeline_model.items]==candidate_ids
        assert [r['id'] for r in b.model.items]==candidate_ids
        assert b.media['subtitleText']=='丙丁'
        assert b.selected==candidate_ids[1]
        assert b.document.snapshot()==before and not b.history.undo_stack
        assert b.media_controller.snapshot()==transport
        assert all(row['locked'] for row in b.timeline_model.items)
        path=tmp_path/'original.lyria';write_document(b.document.snapshot(),path)
        assert load_document(path).project.to_dict()==b.document.project.to_dict()
        assert b.command('alignment.preview',{'enabled':False})
        assert [r['id'] for r in b.timeline_model.items]==['m1','m2','m3']
        assert b.media['subtitleText']=='甲乙丙丁' and b.selected=='m1'
        assert b.document.snapshot()==before and b.media_controller.snapshot()==transport
    monkeypatch.undo()


def test_preview_blocks_mutations_and_apply_is_one_undo_with_identical_ids(bridges,monkeypatch):
    b,plan=prepared(bridges,monkeypatch);before=b.document.project.to_dict();transport=b.media_controller.snapshot()
    assert b.command('alignment.preview',{'enabled':True})
    ids=[r['id'] for r in b.timeline_model.items]
    for name,payload in [('row.edit',{'text':'禁止写入'}),('timeline.razor',{'frame':3}),('row.deleteSelected',{'ids':[b.selected]}),('export.ass',{}),('textAllocation.apply',{}),('settings.subtitleStyle',{}),('row.multiToggle',{})]:
        assert not b.command(name,payload)
        assert b.document.project.to_dict()==before and not b.history.undo_stack
    assert b.command('alignment.accept',{})
    assert not b.state['alignmentPreview']['active']
    assert [m.id for m in b.document.project.mappings]==ids
    assert len(b.history.undo_stack)==1 and b.media_controller.snapshot()==transport
    after=b.document.project.to_dict()
    assert b.command('history.undo',{}) and b.document.project.to_dict()==before
    assert b.command('history.redo',{}) and b.document.project.to_dict()==after
    monkeypatch.undo()


def test_base_edit_expires_preview_and_cannot_apply_stale_plan(bridges,monkeypatch):
    b,plan=prepared(bridges,monkeypatch)
    assert b.command('alignment.preview',{'enabled':True})
    b.editor.set_text('m1','后来的修改')
    assert not b.state['alignmentPreview']['active']
    assert '过期' in b.state['status'] and b.media['subtitleText']=='后来的修改'
    before=b.document.snapshot();history=len(b.history.undo_stack)
    assert not b.command('alignment.accept',{})
    assert not b.command('alignment.preview',{'enabled':True})
    assert b.document.snapshot()==before and len(b.history.undo_stack)==history
    monkeypatch.undo()


def test_missing_and_tampered_structure_never_fabricate_candidate(bridges):
    b=bridges();seed(b);before=b.document.snapshot()
    assert not b.command('alignment.preview',{'enabled':True})
    plan=make_plan(b.document.project,b.media_controller.index,split_response(),['m1'])
    plan['diff'][0]['after'][0]['text']='伪造'
    b._alignment={'plan':plan}
    assert not b.command('alignment.preview',{'enabled':True})
    assert b.document.snapshot()==before and not b._alignment_preview.active


def test_auxiliary_window_is_nonmodal_and_close_preserves_candidate(bridges,monkeypatch):
    b,plan=prepared(bridges,monkeypatch);before=b.document.snapshot()
    dialog=show_alignment_plan(b,plan);QTest.qWait(30)
    assert show_alignment_plan(b,plan) is dialog
    assert len(b._auxiliary_dialogs)==1
    assert dialog.isVisible() and not dialog.isModal() and QApplication.activeModalWidget() is None
    dialog.preview.click();QTest.qWait(30)
    assert b._alignment_preview.active
    dialog.close();QTest.qWait(30)
    assert b._alignment_preview.active and not b._auxiliary_dialogs
    assert b.command('alignment.preview',{'enabled':False})
    assert b.state['alignmentPreview']['available']
    assert b.document.snapshot()==before
    monkeypatch.undo()


def test_async_candidate_layout_matches_text_and_preserves_frame(bridges,monkeypatch):
    from test_qt_api_review_workflows import wait_for
    b,plan=prepared(bridges,monkeypatch)
    b.document.project.video.update(width=640,height=360)
    # The plan is bound to project content, so rebuild after adding media dimensions.
    plan=make_plan(b.document.project,b.media_controller.index,split_response(),['m1'])
    b._alignment={'plan':plan};b._refresh()
    wait_for(QApplication.instance(),lambda:b._layout_revision==b.document.revision)
    for enabled,expected in [(True,'丙丁'),(False,'甲乙丙丁'),(True,'丙丁')]:
        assert b.command('alignment.preview',{'enabled':enabled})
        wait_for(QApplication.instance(),lambda:b._layout_revision==b.document.revision and not b.media['subtitleLayoutPending'])
        assert b.media['subtitleText']==expected
        assert b.media['subtitleRows'][0]['text']==expected
        assert b.media['displayedFrame']==3 and b.media['positionSeconds']==.08
    monkeypatch.undo()
