from test_qt_source_candidates import bridge,candidate
from desktop_qt.candidate_dialog import CandidateDialog

def test_filter_clears_selection_and_excludes_hidden(bridge):
    record=candidate(bridge);record['rows'][1]['start']=None;record['rows'][1]['end']=None
    d=CandidateDialog(bridge,record)
    d.check_visible(False);d.filter.setCurrentIndex(d.filter.findData('missing'))
    assert d.chosen()==[]
    assert d.table.isRowHidden(0) and not d.table.isRowHidden(1)
    d.check_visible(True);assert d.chosen()==[1]
    d.reject()

def test_sentence_audition_has_half_second_bounds(bridge,monkeypatch):
    record=candidate(bridge);bridge.document.project.inputs['audio']='test-audio.wav'
    d=CandidateDialog(bridge,record);seen=[]
    monkeypatch.setattr(d,'_listen_audio',lambda request,vocals:seen.append(request))
    d.table.selectRow(0);d.listen_row(0)
    assert seen[0]['offset_seconds']==0
    assert seen[0]['duration_seconds']==1.5
    record['rows'][0]['start']=None;d.listen_row(0);assert len(seen)==1
    d.reject()


def test_checked_times_apply_atomically_without_tweaking_candidate(bridge):
    from copy import deepcopy
    from test_qt_source_candidates import wait
    record=candidate(bridge); saved=deepcopy(record); before=deepcopy(bridge.document.project.to_dict())
    d=CandidateDialog(bridge,record);d.check_visible(True)
    assert d.apply.isEnabled() and bridge.document.project.to_dict()==before
    d.commit();wait(lambda:not d.alive)
    assert record==saved and bridge.document.main_timeline[0].start==.4
    assert bridge.command('history.undo',{}) and bridge.document.project.to_dict()==before


def test_locked_after_open_blocks_entire_batch(bridge):
    from test_qt_source_candidates import wait
    record=candidate(bridge);d=CandidateDialog(bridge,record);d.check_visible(True)
    assert bridge.command('row.lock',{'id':'a','value':True})
    before=bridge.document.project.to_dict()
    d.commit();wait(lambda:d.request is None)
    assert bridge.document.project.to_dict()==before and '锁定' in d.status.text()
    d.reject()


def test_graph_edge_metadata_stales_preview_and_preserves_time_only_apply(bridge):
    from copy import deepcopy
    from lyricsync.candidate_tracks import capture_source,candidate_from_project,prepare_apply,apply_plan
    from lyricsync.model import Project
    graph=bridge.document.project.alignment_graph
    graph.visual_source_edges['a'].update(state='uncertain',confidence=.31,reasons=['independent evidence'])
    captured=capture_source(deepcopy(bridge.document),'ocr',bridge.media_controller.index)
    generated=Project.from_dict(bridge.document.project.to_dict());generated.mappings[0].start=.4
    record=candidate_from_project(captured,generated);bridge._candidate_install(record,initial_complete=False)
    plan=prepare_apply(bridge.document,record['id'],['a'],frame_index=bridge.media_controller.index)
    before=deepcopy(graph.visual_source_edges['a'])
    bridge.candidate_apply(plan)
    assert bridge.document.project.alignment_graph.visual_source_edges['a']==before
    assert bridge.command('history.undo',{})
    graph=bridge.document.project.alignment_graph;graph.visual_source_edges['a']['confidence']=.32
    import pytest
    with pytest.raises(ValueError):bridge.candidate_apply(plan)

def test_independent_edge_lock_blocks_candidate_without_unlocking(bridge):
    from lyricsync.candidate_tracks import prepare_apply
    record=candidate(bridge)
    graph=bridge.document.project.alignment_graph
    graph.source_translation_edges['a']['state']='manual_locked'
    plan=prepare_apply(bridge.document,record['id'],['a'],frame_index=bridge.media_controller.index,bindings={'a':'a'})
    assert plan['entries'][0]['blocked']
    assert graph.source_translation_edges['a']['state']=='manual_locked'
