"""Synthetic real Qt completion tests, not model timing accuracy acceptance."""
from copy import deepcopy
import threading
import pytest
from lyricsync.model import Mapping, Project
from lyricsync.candidate_tracks import capture_source, candidate_from_project
from desktop_qt.diagnostics import clean_event
from test_qt_entry_initialization_dev5 import bridge, ready, settled, wait


@pytest.fixture(autouse=True)
def capture_application_offer(monkeypatch):
    from desktop_qt.candidate_workspace import CandidateWorkspace
    monkeypatch.setattr(CandidateWorkspace,'_offer_vocal_application',lambda self,record:setattr(self,'_test_offered_record',record))


def run_result(b, count=2, valid=2, gate=None, started=None):
    detached=deepcopy(b.document); index=deepcopy(b.media_controller.index)
    def work(cancel, progress):
        snapshot=capture_source(detached, 'vocal', index)
        if started: started.set()
        if gate: assert gate.wait(5)
        generated=Project.from_dict(detached.project.to_dict())
        if not generated.mappings:
            generated.mappings=[Mapping('cue-'+str(i), [], text_override='private lyrics') for i in range(count)]
        for i,row in enumerate(generated.mappings):
            row.start=.2+i if i<valid else None
            row.end=.8+i if i<valid else None
        return candidate_from_project(snapshot, generated)
    b._start_candidate_job('test alignment', 'vocal', work,
        lambda record,error: b._complete_vocal_candidate(record) if not error else None)


@pytest.mark.parametrize('valid,expected', [(0,'empty'),(1,'applied'),(2,'applied')])
def test_result_labels_distinguish_coverage_and_actual_writes(bridge,valid,expected):
    ready(bridge,'audio'); prior_stage=bridge.stage;run_result(bridge,valid=valid); settled(bridge)
    task=bridge._candidate_task
    assert task['resultStatus']==expected
    assert task['valid']==valid and task['missing']==2-valid
    assert task['appliedTimed']==valid
    assert task['applied']==(2 if valid else 0)
    assert bridge.stage==('review' if valid else prior_stage)
    assert len(bridge.document.main_timeline)==(2 if valid else 0)
    assert len(bridge.document.source_candidates)==1
    assert ('无有效打轴结果' in task['label']) == (valid==0)
    event=[e for e in bridge.diagnostics.snapshot() if e['event']=='alignment-result'][-1]
    assert event['alignment']['applied']==task['applied']
    assert event['alignment']['review'] == (bridge.stage=='review')
    assert 'private lyrics' not in str(event)


def test_existing_manual_edit_retained_and_reported_not_applied(bridge):
    ready(bridge,'audio')
    bridge.document.project.mappings=[Mapping('manual',[],text_override='my text',start=.1,end=.3,human_edited=True)]
    bridge._changed(); before=deepcopy(bridge.document.project.to_dict())
    run_result(bridge); settled(bridge)
    assert bridge.document.project.to_dict()==before
    assert bridge._candidate_task['resultStatus']=='retained'
    assert bridge._candidate_task['applied']==0
    assert '候选' in bridge._candidate_task['label']
    assert '实际写入主轴 0 句' in bridge._status


def test_changed_during_model_run_never_applies(bridge):
    ready(bridge,'audio');gate=threading.Event();started=threading.Event()
    run_result(bridge,gate=gate,started=started);wait(started.is_set)
    bridge.document.project.mappings=[Mapping('manual',[],text_override='keep',start=.1,end=.3,human_edited=True)]
    bridge._changed();before=deepcopy(bridge.document.project.to_dict());gate.set();settled(bridge)
    assert bridge.document.project.to_dict()==before
    assert bridge._candidate_task['applied']==0
    assert bridge._candidate_task['reason'] in ('stale_context','not_pristine')


def test_edited_main_has_no_automatic_application_verification(bridge,monkeypatch):
    import desktop_qt.entry_initialization as entry
    ready(bridge,'audio')
    bridge.document.project.mappings=[Mapping('manual',[],text_override='keep',start=.1,end=.2,human_edited=True)]
    bridge._changed()
    monkeypatch.setattr(entry,'prepare_apply',lambda *a,**kw:pytest.fail('automatic application is forbidden'))
    run_result(bridge);settled(bridge)
    assert bridge._candidate_task['resultStatus']=='retained'
    assert bridge._candidate_task['reason'] in ('stale_context','not_pristine')
    assert bridge.document.main_timeline[0].text_override=='keep'


def test_diagnostics_reject_untrusted_text_and_values():
    value=clean_event({'time':'2026-11-16T00:00:00+00:00','event':'alignment-result',
        'alignment':{'path':'C:/private/song.mp4','lyrics':'secret','reason':'secret',
                     'status':'private','valid':True,'applied':-1,'total':2,'review':True}})
    assert value['alignment']=={'total':2,'review':True}


def test_candidate_storage_failure_does_not_claim_completed(bridge,monkeypatch):
    ready(bridge,'audio')
    def fail(*a,**kw): raise ValueError('storage failed')
    monkeypatch.setattr(bridge,'_candidate_install',fail)
    run_result(bridge);settled(bridge)
    assert bridge._candidate_task['resultStatus']=='failed'
    assert bridge._candidate_task['reason']=='install_failed'
    assert not bridge.document.main_timeline
    assert bridge.stage!='review'


def test_first_missing_initial_result_is_selected_on_editable_main(bridge):
    ready(bridge,'audio');run_result(bridge,valid=1);settled(bridge)
    record=bridge.document.source_candidates[-1]
    assert bridge.selected==bridge.document.main_timeline[1].id
    assert bridge._candidate_source=='main'
    assert bridge.document.main_timeline[1].start is None
