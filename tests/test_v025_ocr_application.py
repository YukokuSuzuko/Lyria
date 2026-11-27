"""Explicit OCR regrouping of a translation seed with one extra timed visual."""
from copy import deepcopy

import pytest
from lyricsync.candidate_tracks import (capture_source, candidate_from_project, install_candidate,
    can_regroup_imported_seed, prepare_apply, apply_plan)
from lyricsync.model import Project, Mapping, Translation, Visual
from lyricsync.project_document import ProjectDocument, write_document, load_document
from lyricsync.frames import FrameIndex
from test_qt_entry_initialization_dev5 import bridge as bridge


def make_case():
    translations = [Translation(f't{i}', f'译文 {i}') for i in range(36)]
    project = Project(translations=translations, mappings=[
        Mapping('seed-' + t.id, [t.id], method='import') for t in translations])
    document = ProjectDocument(project)
    index = FrameIndex([i / 30 for i in range(1200)], 40.)
    capture = capture_source(document, 'ocr', index)
    generated = Project.from_dict(project.to_dict())
    generated.visuals = [Visual(f'v{i}', f'原文 {i}', i, i + .8, [10,10,80,30], .9, 4) for i in range(37)]
    ids = iter(translations)
    generated.mappings = [Mapping('visual-' + v.id, [] if i == 21 else [next(ids).id],
                                  visual_ids=[v.id], start=v.start, end=v.end)
                          for i,v in enumerate(generated.visuals)]
    record = candidate_from_project(capture, generated)
    install_candidate(document, record)
    return document, record, index


def test_extra_is_explicit_and_atomic(bridge, tmp_path):
    document, record, index = make_case()
    bridge.document = document
    bridge.media_controller.index = index
    assert not can_regroup_imported_seed(document, record)
    assert can_regroup_imported_seed(document, record, allow_visual_extras=True)
    original = deepcopy(document.project.to_dict())
    ids = [r['id'] for r in record['rows']]
    plan = prepare_apply(document, record['id'], ids, frame_index=index, explicit_visual_regroup=True)
    assert not any(entry['blocked'] for entry in plan['entries'])
    assert document.project.to_dict() == original
    bridge.candidate_apply(plan)
    after = deepcopy(document.project.to_dict())
    rows = document.main_timeline
    assert len(rows) == 37 and all(r.start is not None for r in rows)
    assert rows[21].text_override == '' and rows[21].visual_ids == ['v21'] and not rows[21].translation_ids
    assert [i for row in rows for i in row.translation_ids] == [t['id'] for t in original['translations']]
    path = tmp_path/'applied.lyria'
    write_document(document.snapshot(explicit_save=True), path)
    reopened=load_document(path)
    assert reopened.project.to_dict() == after and reopened.source_candidates == [record]
    assert bridge.command('history.undo', {}) and document.project.to_dict() == original
    assert bridge.command('history.redo', {}) and document.project.to_dict() == after


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'duplicate_evidence', 'many_extra', 'edited', 'stale', 'partial'])
def test_incomplete_or_protected_seeds_cannot_be_replaced(damage):
    doc, record, index = make_case()
    record = doc.source_candidates[0]
    ids = [r['id'] for r in record['rows']]
    if damage == 'missing':
        record['rows'][0]['provenance']['translation_ids'] = []
    if damage == 'duplicate':
        record['rows'][0]['provenance']['translation_ids'] = ['t1']
    if damage == 'duplicate_evidence':
        record['evidence']['translations'].append(deepcopy(record['evidence']['translations'][0]))
    if damage == 'many_extra':
        for number in range(4):
            extra=deepcopy(record['rows'][21])
            extra['id']=f'extra-{number}'
            record['rows'].append(extra)
            ids.append(extra['id'])
    if damage == 'edited':
        doc.main_timeline[0].human_edited = True
    if damage == 'partial':
        ids.pop()
    if damage == 'stale':
        plan = prepare_apply(doc, record['id'], ids, frame_index=index, explicit_visual_regroup=True)
        doc.main_timeline[0].locked=True
        with pytest.raises(ValueError):
            apply_plan(doc, None, plan, frame_index=index)
    else:
        with pytest.raises(ValueError):
            prepare_apply(doc, record['id'], ids, frame_index=index, explicit_visual_regroup=True)


def test_main_comparison_needs_an_explicit_choice(bridge, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    from desktop_qt.candidate_dialog import CandidateDialog
    doc, record, index = make_case()
    bridge.document=doc
    bridge.media_controller.index=index
    before=deepcopy(doc.project.to_dict())
    opened=[]
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *a: ('', False))
    monkeypatch.setattr(CandidateDialog, 'exec', lambda self: opened.append(self.record['id']))
    bridge.compareCandidates()
    assert not opened
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *a: (a[3][0], True))
    bridge.compareCandidates()
    assert opened == [record['id']] and doc.project.to_dict() == before
