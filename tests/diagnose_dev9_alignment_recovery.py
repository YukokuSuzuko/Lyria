"""Read-only saved-candidate replay; reports counts only, no private lyric text."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from lyricsync.project_document import load_document
from lyricsync.candidate_tracks import capture_source, install_candidate, prepare_apply, apply_plan
from lyricsync.editing import History
from lyricsync.local_alignment import request_rows, rebind_source_candidate, alignment_result_summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('recovery', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    before_bytes = args.recovery.read_bytes()
    document = load_document(args.recovery, recover=True)
    before = deepcopy(document.snapshot())
    snapshot = capture_source(document, 'vocal')
    rows = request_rows(document, text_source='reference')
    report = []
    for candidate in document.source_candidates:
        if candidate['source'] != 'vocal':
            continue
        rebound = rebind_source_candidate(snapshot, candidate, rows)
        assert [(r['start'], r['end']) for r in rebound['rows']] == [(r['start'], r['end']) for r in candidate['rows']]
        assert [r['reasons'] for r in rebound['rows']] == [r['reasons'] for r in candidate['rows']]
        application = {'performed': False, 'reason': '没有完整明确的主轴身份关联'}
        if all(row['sentence_id'] for row in rebound['rows']):
            clone = deepcopy(document)
            before_project = deepcopy(clone.project.to_dict())
            install_candidate(clone, rebound)
            valid_ids = [row['id'] for row in rebound['rows'] if row['start'] is not None and row['end'] is not None]
            plan = prepare_apply(clone, rebound['id'], valid_ids)
            assert not any(entry['blocked'] for entry in plan['entries'])
            history = History()
            apply_plan(clone, history, plan)
            untimed = sum(row.start is None or row.end is None for row in clone.project.mappings)
            assert [row.id for row in clone.project.mappings] == [row.id for row in document.project.mappings]
            assert len(clone.project.mappings) == len(document.project.mappings)
            restored, _ = history.restore(clone.project, validator=clone.validate_project)
            assert restored.to_dict() == before_project
            application = dict(performed=True, detached_only=True, entry_initialization_authority=False,
                               explicit_apply_simulation=True, applied=len(valid_ids), remaining_untimed=untimed,
                               main_ids_preserved=True, undo_restored_project=True)
        report.append(dict(before_bound=sum(r['sentence_id'] is not None for r in candidate['rows']),
                           after_bound=sum(r['sentence_id'] is not None for r in rebound['rows']),
                           timing_and_reasons_preserved=True, detached_application=application,
                           **alignment_result_summary(rebound)))
    assert document.snapshot() == before
    assert args.recovery.read_bytes() == before_bytes
    data=dict(recovery_sha256=hashlib.sha256(before_bytes).hexdigest(), original_file_unchanged=True,
              project_unchanged=True, model_rerun=False, candidate_replay=report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(data, ensure_ascii=False))


if __name__ == '__main__':
    main()
