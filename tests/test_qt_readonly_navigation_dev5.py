"""Real integrated navigation dispatch preserves both main and candidate data."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from copy import deepcopy

import pytest
from PySide6.QtWidgets import QApplication
from desktop_qt.controller import Bridge
from desktop_qt.recent_projects import RecentProjects
from lyricsync.candidate_tracks import capture_source,candidate_from_project
from lyricsync.frames import FrameIndex
from lyricsync.model import Mapping,Project


@pytest.fixture
def bridge(tmp_path):
    app=QApplication.instance() or QApplication([])
    obj=Bridge(storage_dir=tmp_path/'state',startup=False)
    obj.document.project.video={'duration':4.0}
    obj.document.project.mappings=[Mapping('a',[],text_override='unsaved original',start=.2,end=1.),
                                   Mapping('b',[],text_override='second cue',start=1.2,end=2.)]
    obj.media_controller.index=FrameIndex([n*.1 for n in range(40)],4.)
    obj.selected='b';obj._changed()
    for source in ('ocr','vocal'):
        captured=capture_source(deepcopy(obj.document),source,obj.media_controller.index)
        generated=Project.from_dict(obj.document.project.to_dict())
        generated.mappings[0].start=.4
        obj._candidate_install(candidate_from_project(captured,generated))
    yield obj
    obj.shutdown();obj.wait_for_shutdown(app)


@pytest.mark.parametrize('source',['ocr','vocal'])
def test_home_resume_forget_preserve_main_candidates_and_history(bridge,tmp_path,source):
    bridge.switchCandidateSource(source)
    assert bridge.state['candidateReadOnly'] and bridge.state['candidateAvailable']
    forgotten=tmp_path/'forget-only-metadata.lyria';forgotten.write_bytes(b'not opened by this test')
    retained=tmp_path/'retain-in-list.lyria';retained.write_bytes(b'also not opened')
    bridge._recent_projects.remember(retained);bridge._recent_projects.remember(forgotten)
    before=bridge.document.snapshot();history=deepcopy(bridge.history.undo_stack)
    index=bridge.media_controller.index;selected=bridge.selected
    for _ in range(2):
        assert bridge.command('project.home',{})
        assert bridge.state['startupVisible'] and bridge.state['canResumeProject']
        assert bridge.document.snapshot()==before
        assert bridge.command('project.resume',{})
        assert not bridge.state['startupVisible']
        assert bridge.state['candidateSource']==source and bridge.state['candidateReadOnly']
        assert bridge.document.snapshot()==before
    assert bridge.command('project.home',{})
    assert bridge.command('project.recentForget',{'path':str(forgotten)})
    assert [r['path'] for r in bridge.state['recentProjects']]==[str(retained)]
    assert [r['path'] for r in RecentProjects(bridge.storage_dir).items]==[str(retained)]
    assert forgotten.read_bytes()==b'not opened by this test'
    assert retained.read_bytes()==b'also not opened'
    assert bridge.document.snapshot()==before and bridge.history.undo_stack==history
    assert bridge.selected==selected and bridge.media_controller.index is index
    assert bridge.state['candidateSource']==source and bridge.state['candidateReadOnly']


@pytest.mark.parametrize('source',['ocr','vocal'])
@pytest.mark.parametrize('command,payload',[('project.new',{}),
    ('project.newEntry',{'mode':'audio'}),('project.newEntry',{'mode':'ocr'}),
    ('project.newEntry',{'mode':'edit'})])
def test_new_from_readonly_source_cannot_bypass_dirty_confirmation(bridge,source,command,payload):
    bridge.switchCandidateSource(source)
    assert bridge.command('project.home',{})
    before=bridge.document.snapshot();identity=bridge.document.id
    result=bridge.command(command,payload)
    if command=='project.newEntry':assert result is False
    assert bridge.state['closeDialog']['visible'] and bridge.state['closeDialog']['dirty']
    assert bridge.document.snapshot()==before and bridge.document.id==identity
    assert bridge.command('close.resolve',{'choice':'cancel'})
    assert bridge.document.snapshot()==before and bridge.document.id==identity
    assert bridge.state['startupVisible'] and bridge.state['candidateSource']==source
    assert bridge.state['candidateReadOnly']


@pytest.mark.parametrize('source',['ocr','vocal'])
def test_navigation_allowlist_does_not_enable_candidate_edits(bridge,source):
    bridge.switchCandidateSource(source)
    before=bridge.document.snapshot()
    for command,payload in [('row.edit',{'id':'a','text':'must not replace'}),
                            ('history.undo',{}),('timeline.move',{'id':'a','delta':1}),
                            ('project.rematch',{}),('settings.set',{'key':'font_size','value':80}),
                            ('export.ass',{})]:
        assert not bridge.command(command,payload)
        assert bridge.document.snapshot()==before
