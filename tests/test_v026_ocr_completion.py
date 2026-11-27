"""Exercise the normal OCR command, detached matching job, result install and navigation."""
from copy import deepcopy

import pytest

from desktop_qt.candidate_dialog import CandidateDialog
from lyricsync.model import Mapping, Visual
from test_qt_entry_initialization_dev5 import bridge, ready, settled


def run_scan(controller, tmp_path, monkeypatch, words):
    video = tmp_path / 'controlled.mp4'
    video.write_bytes(b'fixture for a controlled scan')
    controller.document.project.inputs['video'] = str(video)
    controller.document.project.settings['roi'] = [0, 0, 100, 30]
    captured = [Visual(f'v{i}', word, .2 + i, .8 + i, [0, 0, 80, 25], .98, 5)
                for i, word in enumerate(words)]
    monkeypatch.setattr('lyricsync.pipeline.scan', lambda *a, **kw: (captured, {'width': 320, 'height': 180, 'duration': 4.}))
    opened = []
    monkeypatch.setattr(CandidateDialog, 'open', lambda dialog: opened.append(dialog.record['id']))
    monkeypatch.setattr('desktop_qt.dialogs.ocr_settings', lambda *a, **kw: True)
    assert controller.command('ocr.start', {})
    settled(controller)
    return opened


def test_normal_ocr_retained_candidate_routes_to_exact_result(bridge, tmp_path, monkeypatch):
    ready(bridge)
    bridge.document.project.mappings = [Mapping('kept', [], text_override='手改原词', start=.1, end=.9, human_edited=True)]
    bridge._changed()
    before = deepcopy(bridge.document.main_timeline[0])
    opened = run_scan(bridge, tmp_path, monkeypatch, ['画面字幕'])
    assert bridge.document.main_timeline[0] == before
    assert bridge.document.source_candidates, bridge.state.get('error')
    assert bridge.stage == 'review'
    assert opened == [bridge.document.source_candidates[-1]['id']]


def test_extra_first_ocr_31_translations_32_visuals_applies_and_undoes(bridge, tmp_path):
    from lyricsync.model import Project, Translation
    from lyricsync.candidate_tracks import capture_source, candidate_from_project
    from lyricsync.frames import FrameIndex
    from lyricsync.project_document import load_document, write_document
    ready(bridge)
    p = bridge.document.project
    p.translations = [Translation(f't{i}', f'译文 {i}') for i in range(31)]
    p.mappings = [Mapping(f'seed-{t.id}', [t.id], method='import') for t in p.translations]
    bridge._entry_accept_import()
    bridge.media_controller.index = FrameIndex([i / 25 for i in range(825)], 33.)
    before = deepcopy(p.to_dict())
    idx = bridge.media_controller.index

    def work(cancel, progress):
        cap = capture_source(bridge.document, 'ocr', idx)
        generated = Project.from_dict(before)
        generated.visuals = [Visual(f'v{i}', f'原文 {i}', i, i+.8, [10, 10, 80, 30], .9, 4) for i in range(32)]
        ts = iter(p.translations)
        generated.mappings = [Mapping('visual-'+v.id, [] if i==12 else [next(ts).id], visual_ids=[v.id],
                                      start=v.start, end=v.end) for i, v in enumerate(generated.visuals)]
        return candidate_from_project(cap, generated)

    bridge._start_candidate_job('OCR', 'ocr', work, lambda record, error: bridge._candidate_install(record))
    settled(bridge)
    assert len(bridge.document.main_timeline) == 32
    extra = bridge.document.main_timeline[12]
    assert extra.visual_ids == ['v12'] and extra.translation_ids == [] and extra.text_override == ''
    assert [i for r in bridge.document.main_timeline for i in r.translation_ids] == [t.id for t in p.translations]
    after = deepcopy(p.to_dict())
    file = tmp_path / 'checked.lyria'
    write_document(bridge.document.snapshot(explicit_save=True), file)
    assert load_document(file).project.to_dict() == after
    assert bridge.command('history.undo', {}) and bridge.document.project.to_dict() == before
    assert bridge.command('history.redo', {}) and bridge.document.project.to_dict() == after
