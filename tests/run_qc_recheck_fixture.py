"""Regenerate real local QC + append-only recheck evidence from bundled fixtures.

Run from any directory:
    python /path/to/app/tests/run_qc_recheck_fixture.py NEW_OUTPUT_DIRECTORY

Uses actual bundled ONNX models, PyAV and local FFmpeg/libass; no OCR mocks and
no provider calls. Synthetic review confirmations are test-fixture authoring,
NEVER automatic approval of a user's video or translations. The default includes
an actual OCR preparation pass via run_pipeline.synthetic, then a full dual-ROI
QC and F45–F125 recheck. It can take several minutes; local does not mean fast.
No existing outputs are overwritten. For relocated/reset workspaces, regenerate:
old reports contain absolute evidence/input paths and are not portable live jobs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(output):
    from run_pipeline import synthetic
    from lyricsync.audit_service import run_audit, rescan_audit, verify_input_versions
    from lyricsync.project_document import ProjectDocument, write_document
    from lyricsync.storage import load_project, atomic_json

    out = Path(output).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=False)
    examples = ROOT / 'examples'
    protected = [examples / name for name in ('synthetic_mv.mp4', 'synthetic_ktv.ass',
                  'synthetic_zh.txt', 'synthetic_bilingual.txt', 'truth.json')]
    before = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    summary = {
        'created_utc': datetime.now(timezone.utc).isoformat(), 'status': 'running',
        'fixture': 'bundled original synthetic clip; actual ONNX/FFmpeg; no API',
        'scope': 'local rendered precheck, not final-file or Windows acceptance',
        'environment': {'python': sys.version.split()[0], 'platform': platform.platform()},
        'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                          for name in ('lyricsync/audit.py', 'lyricsync/audit_service.py',
                                       'tests/run_qc_recheck_fixture.py')},
        'fixture_input_hashes': before,
        'portability': 'Report input/evidence paths are absolute. Regenerate after relocation; do not rewrite hashes to adopt old evidence.',
        'no_user_approval': 'Only the bundled synthetic preparation fixture is truth-confirmed by this test harness; QC candidates remain unreviewed.',
    }
    atomic_json(out / 'acceptance_summary.json', summary)
    try:
        # Guard provider entry points only. OCR, rendering, decoding, matching,
        # actual PTS and saved PNGs all run unmodified against real local data.
        forbidden = AssertionError('This synthetic local-QC harness must never call a provider')
        with patch('lyricsync.api_client.request', side_effect=forbidden), \
             patch('lyricsync.pipeline.suggest', side_effect=forbidden):
            prepared = out / 'prepared-fixture'
            started = time.monotonic()
            synthetic(prepared)
            summary['preparation_seconds'] = time.monotonic() - started
            project = load_project(prepared / 'synthetic_reviewed.lyricsync.json')
            project.settings['api_enabled'] = False
            document = ProjectDocument(project=project)
            project_path = out / 'current-project.lyria'
            write_document(document.snapshot(explicit_save=True), project_path)
            chinese = {'box': [0, 150, 960, 300], 'origin': 'user', 'confirmed': True}
            source = {'box': [0, 300, 960, 500], 'origin': 'user', 'confirmed': True}
            settings = dict(engine='rapidocr', sample_fps=3, chinese_language='chi_sim',
                            source_language='jpn+eng', audit_use_current_project=True,
                            api_enabled=False)
            progress = lambda p, m: print(f'{p:.0%} {m}', flush=True)
            started = time.monotonic()
            parent = run_audit(examples / 'synthetic_mv.mp4', examples / 'synthetic_zh.txt', out,
                               mode='local', chinese_roi=chinese, source_roi=source,
                               ass_path=prepared / 'synthetic_reviewed_中文.ass', input_kind='raw',
                               project_path=project_path, ktv_path=examples / 'synthetic_ktv.ass',
                               settings=settings, progress=progress)
            summary['full_seconds'] = time.monotonic() - started
            before_cues = json.dumps({key: parent.get(key, []) for key in
                                     ('cues', 'source_cues', 'unobserved_cues')}, ensure_ascii=False, sort_keys=True)
            selected = next(cue for cue in parent['cues'] if cue['text'].strip() == '请留在我身边')
            started = time.monotonic()
            result = rescan_audit(parent, out, first_frame=45, last_frame=125,
                                  cue_ids=[selected['id']], chinese_roi=chinese, source_roi=source,
                                  chinese_language='chi_sim', source_language='jpn+eng',
                                  settings=settings, progress=progress)
            summary['rescan_seconds'] = time.monotonic() - started
            child = result['report']
            unchanged = before_cues == json.dumps({key: parent.get(key, []) for key in
                                                  ('cues', 'source_cues', 'unobserved_cues')},
                                                  ensure_ascii=False, sort_keys=True)
            assert unchanged, 'Local recheck silently changed parent observations'
            assert parent['baseline_policy']['use_current_project']
            assert set(parent['input_versions']) == {'video', 'translation', 'ass', 'project', 'ktv'}
            verify_input_versions(parent)
            assert result['rescan']['status'] == 'needs_review'
            assert result['rescan']['counts_as_pass'] is False
            assert child['scope_detail']['first_frame'] == 45 and child['scope_detail']['last_frame'] == 125
            for report in (parent, child):
                assert not report['coverage']['complete_video_verified']
                assert report['coverage']['semantic_checked'] == 0
                assert report['coverage']['manual_reviewed'] == report['coverage']['manual_skipped'] == 0
                assert report['api']['text']['status'] == 'disabled'
                assert report['stage']['temporary_file_retained'] is False
                assert all(c['content_status'] == 'ocr_completeness_unverified'
                           for c in report['cues'] + report['source_cues'])
            fields = ('id', 'text', 'first_frame', 'last_frame', 'boundary_status',
                      'content_status', 'scope_relation', 'ocr_text_variants')
            summary.update(status='passed', parent_observations_unchanged=unchanged,
                           parent_scope=parent['scope_detail'], parent_versions=sorted(parent['input_versions']),
                           parent_coverage=parent['coverage'], baseline_policy=parent['baseline_policy'],
                           rescan=result['rescan'], child_coverage=child['coverage'],
                           child_cues=[{k: c.get(k) for k in fields} for c in child['cues']],
                           child_source_cues=[{k: c.get(k) for k in fields} for c in child['source_cues']],
                           child_issues=child['issues'],
                           report_relative_paths=['report.json', 'report.html',
                                                  str(Path(result['rescan']['report_path']).relative_to(out))])
    except BaseException as exc:
        summary.update(status='failed', error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        after = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
        summary['original_fixture_bytes_unchanged'] = before == after
        summary['finished_utc'] = datetime.now(timezone.utc).isoformat()
        if before != after:
            summary.update(status='failed', error='Bundled original fixture bytes changed')
        atomic_json(out / 'acceptance_summary.json', summary)
    assert summary['original_fixture_bytes_unchanged']
    print(json.dumps({'status': summary['status'], 'output': str(out),
                      'full_seconds': summary['full_seconds'], 'rescan_seconds': summary['rescan_seconds'],
                      'parent_observations_unchanged': summary['parent_observations_unchanged']},
                     ensure_ascii=False, indent=2), flush=True)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='New, nonexistent output directory (never overwritten)')
    run(parser.parse_args().output)
