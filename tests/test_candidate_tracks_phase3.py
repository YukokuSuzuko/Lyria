"""Phase 3 candidate safety contracts, independent of Qt and real models."""
from copy import deepcopy
from pathlib import Path

import pytest

from lyricsync.candidate_tracks import (
    capture_source, candidate_from_project, install_candidate, view_rows,
    prepare_apply, apply_plan, set_ignored, verify_provenance, read_candidates,
    MAX_RECORDS,
)
from lyricsync.editing import History
from lyricsync.model import Project, Mapping, Translation
from lyricsync.project_document import ProjectDocument, write_document, load_document, _validate_snapshot


def document():
    project = Project(translations=[Translation('t1','chorus'),Translation('t2','chorus')],
                      mappings=[Mapping('one',['t1'],start=1,end=2),Mapping('two',['t2'],start=3,end=4)])
    return ProjectDocument(project=project)


def candidate(doc, source='ocr', change=None):
    snapshot = capture_source(doc,source)
    generated = Project.from_dict(deepcopy(snapshot['project']))
    generated.mappings[0].start,generated.mappings[0].end = 1.2,2.2
    generated.mappings[1].start,generated.mappings[1].end = 3.2,4.2
    if change:
        change(generated)
    record = candidate_from_project(snapshot,generated)
    install_candidate(doc,record)
    return record


def test_generation_switch_and_late_arrival_never_overwrite():
    doc = document()
    before = deepcopy(doc.project.to_dict())
    snap = capture_source(doc,'ocr')
    generated = Project.from_dict(deepcopy(snap['project']))
    generated.mappings[0].start = 1.2
    doc.project.mappings[0].text_override = 'edited while running'
    doc.touch()
    edited = deepcopy(doc.project.to_dict())
    record = candidate_from_project(snap,generated)
    install_candidate(doc,record)
    for source in ('main','ocr','vocal','ocr'):
        view_rows(doc,source)
    assert doc.project.to_dict() == edited
    plan = prepare_apply(doc,record['id'],['one','two'])
    assert plan['entries'][0]['blocked']
    assert not plan['entries'][1]['blocked']
    assert before != edited


def test_exact_ids_survive_reorder_and_duplicate_text():
    doc = document()
    record = candidate(doc)
    doc.project.mappings.reverse()
    history = History()
    apply_plan(doc,history,prepare_apply(doc,record['id'],['one']))
    assert [(r.id,r.start) for r in doc.main_timeline] == [('two',3),('one',1.2)]
    assert len(history.undo_stack) == 1


def test_batch_is_atomic_and_preserves_redo():
    doc = document()
    record = candidate(doc)
    history = History()
    history.redo_stack = [('sentinel',{})]
    plan = prepare_apply(doc,record['id'],['one','two'])
    doc.project.settings['manual_locked_ids'] = ['two']
    before = deepcopy(doc.project.to_dict())
    revision = doc.revision
    with pytest.raises(ValueError):
        apply_plan(doc,history,plan)
    assert doc.project.to_dict() == before
    assert doc.revision == revision
    assert not history.undo_stack
    assert history.redo_stack == [('sentinel',{})]


def test_apply_and_ignore_share_undo_without_losing_candidates():
    doc = document()
    record = candidate(doc)
    history = History()
    set_ignored(doc,history,record['id'],['two'])
    assert view_rows(doc,'ocr')[1]['ignored']
    plan = prepare_apply(doc,record['id'],['two'])
    assert plan['entries'][0]['blocked']
    restored,label = history.restore(doc.project,validator=doc.validate_project)
    doc.replace_project(restored)
    assert not view_rows(doc,'ocr')[1]['ignored']
    assert doc.source_candidates[0]['id'] == record['id']
    restored,label = history.restore(doc.project,redo=True,validator=doc.validate_project)
    doc.replace_project(restored)
    assert view_rows(doc,'ocr')[1]['ignored']
    set_ignored(doc,history,record['id'],['two'],False)
    apply_plan(doc,history,prepare_apply(doc,record['id'],['one','two']))
    restored,label = history.restore(doc.project,validator=doc.validate_project)
    doc.replace_project(restored)
    assert doc.main_timeline[0].start == 1
    restored,label = history.restore(doc.project,redo=True,validator=doc.validate_project)
    doc.replace_project(restored)
    assert doc.main_timeline[0].start == 1.2


def test_unbound_rows_require_explicit_binding_or_new_confirmation():
    doc = document()
    record = candidate(doc,change=lambda p:setattr(p.mappings[0],'id','unbound'))
    assert record['rows'][0]['sentence_id'] is None
    assert prepare_apply(doc,record['id'],['unbound'])['entries'][0]['blocked']
    assert view_rows(doc,'ocr')[0]['status'] == 'missing'
    history = History()
    plan = prepare_apply(doc,record['id'],['unbound'],bindings={'unbound':'one'})
    apply_plan(doc,history,plan)
    assert len(doc.main_timeline) == 2
    assert doc.main_timeline[0].start == 1.2


def test_empty_main_explicit_new_and_undo():
    doc = ProjectDocument()
    snap = capture_source(doc,'ocr')
    generated = Project(mappings=[Mapping('raw',[],start=2,end=3,text_override='new line')])
    record = candidate_from_project(snap,generated)
    install_candidate(doc,record)
    history = History()
    with pytest.raises(ValueError):
        apply_plan(doc,history,prepare_apply(doc,record['id'],['raw']))
    result = apply_plan(doc,history,prepare_apply(doc,record['id'],['raw'],add_unmapped=True))
    assert result == [doc.main_timeline[0].id]
    assert doc.main_timeline[0].text_override == 'new line'
    restored,_ = history.restore(doc.project,validator=doc.validate_project)
    doc.replace_project(restored)
    assert not doc.main_timeline
    assert doc.source_candidates


def test_split_deleted_target_never_maps_by_lineage_or_text():
    doc = document()
    record = candidate(doc)
    doc.project.mappings[0].id = 'split-one'
    doc.project.mappings[0].lineage_ids = ['one']
    plan = prepare_apply(doc,record['id'],['one'])
    assert plan['entries'][0]['blocked']
    assert any(row['status']=='orphaned' for row in view_rows(doc,'ocr'))


def test_source_content_path_clock_and_late_stat_checks(tmp_path):
    doc = document()
    path = tmp_path/'media.bin'
    path.write_bytes(b'abcd')
    doc.project.inputs['video'] = str(path)
    record = candidate(doc)
    plan = prepare_apply(doc,record['id'],['one'])
    path.write_bytes(b'changed')
    with pytest.raises(ValueError):
        apply_plan(doc,History(),plan)
    assert prepare_apply(doc,record['id'],['one'])['entries'][0]['blocked']
    path.write_bytes(b'abcd')
    replacement = tmp_path/'same.bin'
    replacement.write_bytes(b'abcd')
    doc.project.inputs['video'] = str(replacement)
    assert prepare_apply(doc,record['id'],['one'])['entries'][0]['blocked']
    doc.project.inputs['video'] = str(path)
    doc.project.timebase['frame_coordinates'] = 'one-based-10ms-editing-grid'
    assert prepare_apply(doc,record['id'],['one'])['entries'][0]['blocked']


def test_apply_does_not_hash_and_candidate_view_does_no_io(monkeypatch):
    doc = document()
    record = candidate(doc)
    token = verify_provenance(doc)
    plan = prepare_apply(doc,record['id'],['one'],current_provenance=token)
    def forbidden(*args,**kwargs):
        raise AssertionError('unexpected hash')
    monkeypatch.setattr('lyricsync.candidate_tracks.verify_provenance',forbidden)
    view_rows(doc,'ocr')
    apply_plan(doc,History(),plan)


def test_save_reopen_legacy_ai_field_and_invalid_optional_metadata(tmp_path):
    doc = document()
    record = candidate(doc)
    set_ignored(doc,History(),record['id'],['one'])
    target = tmp_path/'session.lyria'
    write_document(doc.snapshot(explicit_save=True),target)
    reopened = load_document(target)
    assert reopened.source_candidates == doc.source_candidates
    assert view_rows(reopened,'ocr')[0]['ignored']
    assert reopened.alignment_candidate is None
    bad = reopened.snapshot()
    bad['source_candidates'][0]['rows'][0]['start'] = float('nan')
    loaded = _validate_snapshot(bad)
    assert loaded.project.to_dict() == doc.project.to_dict()
    assert loaded.source_candidate_warning
    legacy = doc.snapshot()
    legacy.pop('source_candidates')
    assert _validate_snapshot(legacy).source_candidates == []


def test_decisions_are_strictly_validated():
    doc = document()
    doc.project.settings['candidate_review_decisions'] = {'bad':False}
    with pytest.raises(ValueError):
        doc.validate_project(doc.project)


def test_record_bounds_and_install_rejection_are_atomic():
    doc = document()
    record = candidate(doc)
    broken = deepcopy(record)
    broken['rows'][0]['extra_secret'] = 'must not persist'
    before = deepcopy(doc.source_candidates)
    with pytest.raises(ValueError):
        install_candidate(doc,broken)
    assert doc.source_candidates == before
    assert read_candidates([record]*(MAX_RECORDS+1))[1]


def test_manually_locked_target_and_duplicate_bindings():
    doc = document()
    record = candidate(doc)
    doc.project.settings['manual_locked_ids'] = ['one']
    assert prepare_apply(doc,record['id'],['one'])['entries'][0]['blocked']
    doc.project.settings['manual_locked_ids'] = []
    plan = prepare_apply(doc,record['id'],['one','two'],bindings={'one':'two'})
    assert plan['entries'][1]['blocked']


def test_tampered_preview_and_invalid_new_timings_rejected():
    doc = document()
    record = candidate(doc)
    plan = prepare_apply(doc,record['id'],['one'])
    plan['entries'][0]['after']['start'] = 0
    with pytest.raises(ValueError):
        apply_plan(doc,History(),plan)
    record = deepcopy(record)
    record['rows'][0]['end'] = -1
    assert read_candidates([record])[1]



def test_evidence_reconstruction_and_changed_source_rematch_block(tmp_path):
    from lyricsync.candidate_tracks import candidate_project, verify_candidate_source
    from lyricsync.model import Visual
    doc = document()
    source = tmp_path/'source.txt'
    source.write_text('original')
    doc.project.inputs['translation'] = str(source)
    def add_evidence(project):
        project.visuals = [Visual('visual','OCR text',1.2,2.2,[0,0,10,10],.9,2,
                                  evidence={'api_key':'must be stripped','frame':1})]
        project.mappings[0].visual_ids = ['visual']
    record = candidate(doc,change=add_evidence)
    detached = candidate_project(record,doc.project)
    assert detached.visuals[0].id == 'visual'
    assert detached.visuals[0].evidence == {'frame':1}
    assert not doc.project.visuals
    assert detached.mappings[0].visual_ids == ['visual']
    verify_candidate_source(doc,record)
    source.write_text('changed')
    with pytest.raises(ValueError):
        verify_candidate_source(doc,record)


def test_evidence_foreign_reference_is_rejected():
    doc = document()
    record = candidate(doc)
    record['rows'][0]['provenance']['visual_ids'] = ['not-real']
    assert read_candidates([record])[1]



def test_text_adoption_is_explicit_and_conflicting_evidence_is_preserved():
    doc = document()
    def change(project):
        project.translations[0].text = 'OCR corrected'
    record = candidate(doc,change=change)
    plan = prepare_apply(doc,record['id'],['one'])
    assert not plan['apply_text']
    assert plan['entries'][0]['after']['text'] == 'chorus'
    plan = prepare_apply(doc,record['id'],['one'],apply_text=True)
    assert plan['entries'][0]['after']['text'] == 'OCR corrected'
    apply_plan(doc,History(),plan)
    assert doc.main_timeline[0].text_override == 'OCR corrected'
    assert doc.project.translations[0].text == 'chorus'
    assert any(t.text=='OCR corrected' and t.id!='t1' for t in doc.project.translations)
    assert any(record['id'] in note for note in doc.main_timeline[0].reasons)


def test_candidate_capacity_never_silently_discards_other_source():
    doc = document()
    candidate(doc,'vocal')
    for _ in range(MAX_RECORDS-1):
        candidate(doc)
    before = deepcopy(doc.source_candidates)
    with pytest.raises(ValueError,match='历史识别结果已达 12 份上限'):
        candidate(doc)
    assert doc.source_candidates == before
    assert doc.source_candidates[0]['source'] == 'vocal'


def test_verification_can_cancel(tmp_path):
    doc = document()
    source = tmp_path/'source'
    source.write_bytes(b'file')
    doc.project.inputs['video'] = str(source)
    with pytest.raises(InterruptedError):
        capture_source(doc,'ocr',cancel=lambda:True)


def test_duplicate_target_and_small_type_corruption_are_optional():
    doc = document()
    record = candidate(doc)
    record['rows'][1]['sentence_id'] = record['rows'][0]['sentence_id']
    assert read_candidates([record])[1]
    for field, bad in (('evidence',{'lyrics':[None],'translations':[],'visuals':[]}),
                       ('provenance',{'files':{'video':None},'clock':None}),
                       ('rows',[None])):
        record = deepcopy(doc.source_candidates[0])
        record[field] = bad
        snapshot = doc.snapshot()
        snapshot['source_candidates'] = [record]
        loaded = _validate_snapshot(snapshot)
        assert loaded.source_candidate_warning
        assert loaded.project.to_dict() == doc.project.to_dict()


def test_frame_signature_fast_path_mutation_deepcopy_pickle(monkeypatch):
    import pickle
    from lyricsync.frames import FrameIndex
    index = FrameIndex([0.,1.,2.],3.)
    original = index.candidate_signature()
    assert isinstance(index.stamps,list)
    assert deepcopy(index).candidate_signature() == original
    assert pickle.loads(pickle.dumps(index)).candidate_signature() == original
    mutations = [lambda i:i.stamps.__setitem__(0,.1),
                 lambda i:i.stamps.__setitem__(slice(0,1),[.1]),
                 lambda i:i.stamps.__delitem__(0),
                 lambda i:i.stamps.append(2.5),
                 lambda i:i.stamps.extend([2.5]),
                 lambda i:i.stamps.insert(1,.5),
                 lambda i:i.stamps.pop(),
                 lambda i:i.stamps.clear(),
                 lambda i:i.stamps.remove(1.),
                 lambda i:i.stamps.reverse(),
                 lambda i:i.stamps.sort(),
                 lambda i:i.stamps.__iadd__([2.5]),
                 lambda i:i.stamps.__imul__(1),
                 lambda i:setattr(i,'stamps',[0.,1.,2.]),
                 lambda i:setattr(i,'duration',4.)]
    for mutate in mutations:
        changed = deepcopy(index)
        mutate(changed)
        with pytest.raises(ValueError,match='重新校验'):
            changed.candidate_signature()
    index.stamps[0] = .1
    assert index.candidate_signature(refresh=True) != original
    def forbidden(*args,**kwargs):
        raise AssertionError('fast path must not hash')
    monkeypatch.setattr('lyricsync.frames.hashlib.sha256',forbidden)
    assert index.candidate_signature()


def test_lazy_virtual_signature_does_not_materialize_stamps():
    from lyricsync.virtual_timeline import VirtualFrameIndex
    index = VirtualFrameIndex(86400)
    assert not isinstance(index.stamps,list)
    assert index.candidate_signature() == deepcopy(index).candidate_signature()
    doc = document()
    doc.project.timebase['frame_coordinates'] = 'one-based-10ms-editing-grid'
    token = verify_provenance(doc,index)
    assert token['clock']['frames']
    index.stamps.count -= 1
    with pytest.raises(ValueError):
        index.candidate_signature()



def test_explicit_candidate_cleanup_is_atomic_and_keeps_applied_main():
    from lyricsync.candidate_tracks import remove_candidates, candidate_source_status
    doc = document()
    oldest = candidate(doc)
    vocal = candidate(doc,'vocal')
    newest = candidate(doc)
    history = History()
    apply_plan(doc,history,prepare_apply(doc,oldest['id'],['one']))
    main = deepcopy(doc.project.to_dict())
    revision = doc.revision
    with pytest.raises(ValueError):
        remove_candidates(doc,[oldest['id'],'missing-id'])
    assert doc.revision == revision
    assert remove_candidates(doc,[oldest['id']]) == 1
    assert [r['id'] for r in doc.source_candidates] == [vocal['id'],newest['id']]
    assert doc.project.to_dict() == main
    assert len(history.undo_stack) == 1
    assert candidate_source_status(doc,newest) == ''
    with pytest.raises(ValueError):
        prepare_apply(doc,oldest['id'],['one'])



@pytest.mark.parametrize('count',[3000,10000])
def test_large_projection_scans_evidence_once_per_operation(count):
    from lyricsync.candidate_tracks import _project_index, _fingerprint
    class CountedList(list):
        traversals = 0
        items = 0
        def __iter__(self):
            type(self).traversals += 1
            for item in super().__iter__():
                type(self).items += 1
                yield item
    doc = ProjectDocument(project=Project(
        translations=[Translation('t'+str(i),'line '+str(i)) for i in range(count)],
        mappings=[Mapping('m'+str(i),['t'+str(i)],start=float(i),end=i+.5) for i in range(count)]))
    snapshot = capture_source(doc,'ocr')
    generated = Project.from_dict(deepcopy(snapshot['project']))
    record = candidate_from_project(snapshot,generated)
    install_candidate(doc,record)
    for table in ('translations','lyrics','visuals'):
        setattr(doc.project,table,CountedList(getattr(doc.project,table)))
    CountedList.traversals = CountedList.items = 0
    assert len(view_rows(doc,'ocr')) == count
    assert CountedList.traversals == 3
    assert CountedList.items == count
    CountedList.traversals = CountedList.items = 0
    plan = prepare_apply(doc,record['id'],['m'+str(i) for i in range(count)])
    assert not any(row['blocked'] for row in plan['entries'])
    assert CountedList.traversals == 3
    assert CountedList.items == count


def test_indexed_fingerprint_preserves_legacy_table_order():
    from lyricsync.candidate_tracks import _project_index, _fingerprint, _digest
    from lyricsync.editing import mapping_text
    doc = document()
    row = doc.main_timeline[0]
    row.translation_ids = ['t2','t1','t2']
    from dataclasses import asdict
    legacy_row = asdict(row)  # Public legacy Mapping fields, not graph adapter binding.
    legacy_row.pop('speaker_name')  # Version 1 stored only the optional speaker ID.
    legacy_row.pop('alignment_tokens')  # Optional v0.22 evidence absent in legacy fingerprints.
    legacy_row.pop('text_role')  # Field did not exist in saved version-1 baselines.
    legacy_row.pop('style_id');legacy_row.pop('style_overrides')
    def legacy_record(item):
        data=deepcopy(item.__dict__);data.pop('subtitle_metadata',None);return data
    old = _digest({'row':legacy_row,'text':mapping_text(doc.project,row),
                   'translations':[legacy_record(t) for t in doc.project.translations if t.id in row.translation_ids],
                   'lyrics':[legacy_record(r) for r in doc.project.lyrics if r.id in row.lyric_ids],
                   'visuals':[r.__dict__ for r in doc.project.visuals if r.id in row.visual_ids]})
    assert _fingerprint(doc.project,row,_project_index(doc.project)) == old



def test_prepare_can_cancel_without_mutating_document():
    doc = document()
    record = candidate(doc)
    before = doc.snapshot()
    with pytest.raises(InterruptedError):
        prepare_apply(doc,record['id'],['one','two'],cancel=lambda:True)
    assert doc.snapshot() == before



def test_automatic_edit_protection_is_not_a_permanent_manual_lock():
    doc = document()
    doc.main_timeline[0].locked = True
    doc.main_timeline[0].human_edited = True
    doc.main_timeline[0].text_override = 'my edited lyric'
    record = candidate(doc)
    plan = prepare_apply(doc,record['id'],['one'])
    assert not plan['entries'][0]['blocked']
    assert not plan['apply_text']
    apply_plan(doc,History(),plan)
    assert doc.main_timeline[0].text_override == 'my edited lyric'
    assert doc.main_timeline[0].locked
    assert doc.main_timeline[0].start == 1.2


def test_protected_row_changed_after_generation_still_requires_new_review():
    doc = document()
    doc.main_timeline[0].locked = True
    record = candidate(doc)
    doc.main_timeline[0].text_override = 'later edit'
    assert prepare_apply(doc,record['id'],['one'])['entries'][0]['blocked']
