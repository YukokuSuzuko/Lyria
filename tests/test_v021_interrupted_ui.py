from copy import deepcopy
import threading
import pytest
from PySide6.QtWidgets import QMessageBox
from lyricsync.model import Mapping,Project
from lyricsync.candidate_tracks import capture_source,candidate_from_project
from test_qt_entry_initialization_dev5 import bridge,ready,settled,wait

@pytest.fixture(autouse=True)
def capture_application_offer(monkeypatch):
    from desktop_qt.candidate_workspace import CandidateWorkspace
    monkeypatch.setattr(CandidateWorkspace,'_offer_vocal_application',lambda self,record:setattr(self,'_test_offered_record',record))


@pytest.mark.parametrize('keep',[False,True])
@pytest.mark.parametrize('reason',['failed','cancelled'])
def test_interrupted_completion_explicit_recovery_never_auto_applies(bridge,monkeypatch,keep,reason):
    ready(bridge,'audio');before=bridge.document.project.to_dict();gate=threading.Event();entered=threading.Event()
    def choose(box):
        box._chosen=next(b for b in box.buttons() if ('保留已完成' in b.text())==keep)
        return 0
    monkeypatch.setattr(QMessageBox,'exec',choose)
    monkeypatch.setattr(QMessageBox,'clickedButton',lambda box:box._chosen)
    detached=deepcopy(bridge.document);index=deepcopy(bridge.media_controller.index)
    def work(cancel,progress):
        snapshot=capture_source(detached,'vocal',index);entered.set();assert gate.wait(5)
        generated=Project.from_dict(detached.project.to_dict())
        generated.mappings=[Mapping('a',[],text_override='fixture',start=.2,end=.8,reasons=['interrupted-run:'+reason]),Mapping('b',[],text_override='fixture',reasons=['interrupted-run:'+reason])]
        return candidate_from_project(snapshot,generated)
    bridge._start_candidate_job('interrupted fixture','vocal',work,lambda r,e:bridge._complete_vocal_candidate(r))
    wait(entered.is_set)
    if reason=='cancelled':bridge.cancelCandidateJob()
    gate.set();settled(bridge)
    assert bridge.document.project.to_dict()==before
    assert len(bridge.document.source_candidates)==int(keep)
    assert bridge._candidate_task['applied']==0
    assert bridge._candidate_task['resultStatus']==('retained' if keep else 'discarded')
    if keep:assert bridge.stage=='review' and bridge._candidate_source=='main'


def test_manifest_failure_cannot_rollback_retained_candidate(bridge,monkeypatch,tmp_path):
    import lyricsync.local_alignment as alignment
    ready(bridge,'audio')
    snapshot=capture_source(deepcopy(bridge.document),'vocal',deepcopy(bridge.media_controller.index))
    generated=Project.from_dict(bridge.document.project.to_dict())
    generated.mappings=[Mapping('a',[],text_override='fixture',start=.2,end=.8)]
    record=candidate_from_project(snapshot,generated)
    def broken(*args,**kwargs):raise OSError('controlled manifest failure')
    monkeypatch.setattr(alignment,'update_alignment_manifest',broken)
    bridge._entry_session['eligible']=False
    bridge._complete_vocal_candidate(record,cache_root=tmp_path)
    settled(bridge);wait(lambda:not bridge.jobs.live_threads())
    assert bridge.document.source_candidates
    assert bridge.stage=='review'
    assert not bridge.document.main_timeline
    assert bridge._candidate_task['resultStatus']=='retained'


def test_partial_install_failure_has_failed_status_and_manifest(bridge,monkeypatch,tmp_path):
    import lyricsync.local_alignment as alignment
    ready(bridge,'audio');seen=[]
    snapshot=capture_source(deepcopy(bridge.document),'vocal',deepcopy(bridge.media_controller.index))
    generated=Project.from_dict(bridge.document.project.to_dict())
    generated.mappings=[Mapping('a',[],text_override='fixture',start=.2,end=.8,reasons=['interrupted-run:failed'])]
    record=candidate_from_project(snapshot,generated)
    monkeypatch.setattr(QMessageBox,'exec',lambda box:0)
    monkeypatch.setattr(QMessageBox,'clickedButton',lambda box:next(b for b in box.buttons() if '保留已完成' in b.text()))
    monkeypatch.setattr(bridge,'_candidate_install',lambda *a,**k:(_ for _ in ()).throw(ValueError('full')))
    monkeypatch.setattr(alignment,'update_alignment_manifest',lambda *a,**k:seen.append(k))
    with pytest.raises(ValueError):bridge._complete_vocal_candidate(record,cache_root=tmp_path)
    wait(lambda:bool(seen));assert seen[0]['application']['reason']=='install_failed'
    assert bridge._candidate_task['resultStatus']=='failed'
    assert bridge._candidate_task['applied']==0 and not bridge.document.main_timeline
