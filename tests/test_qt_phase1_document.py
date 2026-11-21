"""Phase-one integration: a document is the sole editable project owner."""
from copy import deepcopy
import os
import time
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication
from desktop_qt.controller import Bridge
from lyricsync.model import Project, Mapping, Translation
from lyricsync.frames import FrameIndex

@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])

@pytest.fixture
def bridge(app,tmp_path):
    bridge=Bridge(storage_dir=tmp_path/'storage')
    p=bridge.document.project
    p.video={'duration':10.0}
    p.translations=[Translation('t-a','Repeated chorus'),Translation('t-b','Repeated chorus')]
    p.mappings=[Mapping('cue-a',['t-a'],start=1.,end=2.),Mapping('cue-b',['t-b'],start=4.,end=5.)]
    bridge.media_controller.index=FrameIndex([n/10 for n in range(100)],10.)
    bridge.selected='cue-b';bridge._changed()
    yield bridge
    bridge.shutdown()
    deadline=time.monotonic()+3
    while bridge.media_controller.worker.thread.is_alive() and time.monotonic()<deadline:
        app.processEvents();time.sleep(.005)


def test_atomic_creation_publishes_only_complete_row(bridge):
    before=bridge.document.revision;events=[]
    bridge.stateChanged.connect(lambda:events.append(deepcopy(bridge.state['rows'])))
    depth=len(bridge.history.undo_stack)
    assert bridge.command('timeline.createRange',{'first':25,'last':30})
    created=bridge.selected
    assert bridge.document.revision==before+1
    assert len(bridge.history.undo_stack)==depth+1
    assert bridge.editor.project is bridge.document.project
    published=[next((r for r in rows if r['id']==created),None) for rows in events]
    assert published and all(r and not r['untimed'] and r['firstFrame']==25 and r['lastFrame']==30 for r in published)
    assert bridge.command('history.undo',{})
    assert [r.id for r in bridge.document.main_timeline]==['cue-a','cue-b']
    assert bridge.selected=='cue-b'
    assert bridge.command('history.redo',{})
    assert bridge.selected==created
    assert bridge.editor.project is bridge.document.project


def test_invalid_replacement_leaves_owner_and_history_unchanged(bridge):
    candidate=Project.from_dict(bridge.document.project.to_dict())
    old=bridge.document.project;snapshot=bridge.document.snapshot();depth=len(bridge.history.undo_stack)
    with pytest.raises(ValueError):
        candidate.mappings[1].id='cue-a'  # authoritative graph rejects before adoption
        bridge._adopt_project(candidate,'invalid test')
    assert bridge.document.project is old and bridge.editor.project is old
    assert bridge.document.snapshot()==snapshot
    assert len(bridge.history.undo_stack)==depth


def test_repeat_ids_lineage_and_history_do_not_use_text_as_identity(bridge):
    assert bridge.command('row.repeat',{'id':'cue-b'})
    repeated=bridge.document.project.mappings[-1]
    assert repeated.id not in ('cue-a','cue-b')
    assert repeated.lineage_ids==['cue-b']
    assert bridge.command('row.edit',{'id':repeated.id,'text':'Only the new repeat'})
    assert bridge.document.project.mappings[0].text_override is None
    assert bridge.document.project.mappings[1].text_override is None
    assert bridge.command('history.undo',{})
    assert bridge.document.project.mappings[-1].id==repeated.id
    assert bridge.command('history.undo',{})
    assert [r.id for r in bridge.document.project.mappings]==['cue-a','cue-b']
    assert bridge.command('history.redo',{})
    assert bridge.document.project.mappings[-1].id==repeated.id
    assert bridge.editor.project is bridge.document.project


def test_add_is_single_revision_and_undo_does_not_rebind_stale_editor(bridge):
    revision=bridge.document.revision
    assert bridge.command('row.add',{'after':'cue-a'})
    created=bridge.selected
    assert bridge.document.revision==revision+1
    assert bridge.command('history.undo',{})
    assert bridge.command('row.edit',{'id':'cue-b','text':'Edited after undo'})
    assert bridge.document.project.mappings[1].text_override=='Edited after undo'
    assert created not in [m.id for m in bridge.document.project.mappings]
    assert not bridge.history.redo_stack

def test_invalid_history_reference_preserves_both_stacks_and_selection(bridge):
    assert bridge.command('row.edit',{'id':'cue-b','text':'Current content'})
    bridge.history.undo_stack[-1][1]['mappings'][1]['translation_ids']=['missing-source']
    before=bridge.document.snapshot()
    undo=deepcopy(bridge.history.undo_stack);redo=deepcopy(bridge.history.redo_stack)
    selected=bridge.selected;restored_context=bridge.history.restored_context
    assert not bridge.command('history.undo',{})
    assert bridge.document.snapshot()==before
    assert bridge.history.undo_stack==undo and bridge.history.redo_stack==redo
    assert bridge.selected==selected and bridge.history.restored_context==restored_context
    assert bridge.editor.project is bridge.document.project
