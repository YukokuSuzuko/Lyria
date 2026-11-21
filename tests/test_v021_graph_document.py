"""Graph envelope migration, integrity and unchanged-old-file regressions."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from lyricsync.model import Project, Mapping, Translation
from lyricsync.project_document import ProjectDocument, _validate_snapshot, write_document, load_document


def document():
    return ProjectDocument(Project(translations=[Translation('t','原文')],
        mappings=[Mapping('m',['t'],start=1.,end=2.)]))


def legacy_snapshot():
    body=document().snapshot()
    body['format_version']=2
    body['project'].pop('alignment_graph',None)
    return body


def test_new_document_requires_graph_and_rejects_unknown_version():
    body=document().snapshot()
    assert body['format_version']==4
    assert isinstance(body['project']['alignment_graph'],dict)
    missing=deepcopy(body);missing['project'].pop('alignment_graph')
    with pytest.raises(ValueError):_validate_snapshot(missing)
    future=deepcopy(body);future['format_version']=5
    with pytest.raises(ValueError):_validate_snapshot(future)


def test_old_document_is_migrated_in_memory_without_editing_input(tmp_path):
    old=tmp_path/'old.lyria';old.write_text(json.dumps(legacy_snapshot()),encoding='utf-8')
    before=old.read_bytes();doc=load_document(old)
    assert old.read_bytes()==before
    assert doc.project.mappings[0].start==1.
    assert doc.snapshot()['format_version']==4
    new=tmp_path/'new.lyria';write_document(doc.snapshot(),new)
    assert old.read_bytes()==before
    assert load_document(new).project.to_dict()==doc.project.to_dict()


def test_graph_and_compatibility_mirror_conflict_is_rejected():
    body=document().snapshot()
    body['project']['mappings'][0]['start']=.5
    with pytest.raises(ValueError,match='disagree'):_validate_snapshot(body)


def test_unknown_graph_is_rejected_without_legacy_fallback():
    body=document().snapshot();body['project']['alignment_graph']['version']=999
    with pytest.raises(ValueError):_validate_snapshot(body)


def test_snapshots_and_restored_documents_do_not_share_mutable_graph():
    doc=document();before=doc.snapshot();restored=_validate_snapshot(before)
    restored.project.mappings[0].translation_ids.clear()
    restored.project.mappings[0].start=.5
    assert doc.project.mappings[0].translation_ids==['t']
    assert doc.project.mappings[0].start==1.
    assert before['project']['mappings'][0]['start']==1.


def test_legacy_checkpoint_rejects_bad_graph_before_touching_file_or_backup(tmp_path):
    from lyricsync.storage import save_project
    doc=document();target=tmp_path/'checkpoint.json'
    save_project(doc.project,target);before=target.read_bytes()
    doc.project.mappings[0].visual_ids=['missing']
    with pytest.raises(ValueError):save_project(doc.project,target)
    assert target.read_bytes()==before
    assert not target.with_name(target.name+'.bak').exists()


def test_checkpoint_save_reload_preserves_independent_edges(tmp_path):
    from lyricsync.storage import save_project,load_project
    doc=document();edge=doc.project.alignment_graph.source_translation_edges['m']
    edge.update(state='manual_locked',confidence=.72,reasons=['independent'])
    target=tmp_path/'checkpoint.json';save_project(doc.project,target)
    restored=load_project(target)
    assert restored.alignment_graph.source_translation_edges['m']==edge
