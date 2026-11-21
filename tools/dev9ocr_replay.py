"""Read-only stored-observation A/B. Does not measure OCR or translation accuracy.

No project, video, model cache, or installed configuration is written. The report
contains indices, timing, hashes, and counts, not user lyrics or media contents.
"""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import argparse
import importlib.util
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lyricsync import matching
from lyricsync.model import Lyric, Project, stable_id
from lyricsync.subtitles import import_original


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def load_baseline(path):
    spec = importlib.util.spec_from_file_location('lyricsync._dev9ocr_baseline', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidate_view(project, reference_lyrics=None):
    view = deepcopy(project)
    # This detached diagnostic view reconstructs a fresh scan's matching input.
    # Never remove protections in the user's real project or saved recovery.
    view.mappings = []
    if reference_lyrics is not None:
        view.lyrics = deepcopy(reference_lyrics)
    elif not view.inputs.get('ktv'):
        view.lyrics.extend(Lyric(stable_id('ocr-source', v.id), v.text, v.start, v.end)
                           for v in view.visuals)
    return view


def mapping_summary(project):
    translation_index = {item.id: i for i, item in enumerate(project.translations)}
    lyric_index = {item.id: i for i, item in enumerate(project.lyrics)}
    visual_index = {item.id: i for i, item in enumerate(project.visuals)}
    return [{
        'translation_indices': [translation_index.get(i) for i in row.translation_ids],
        'lyric_indices': [lyric_index.get(i) for i in row.lyric_ids],
        'visual_indices': [visual_index.get(i) for i in row.visual_ids],
        'start': row.start, 'end': row.end, 'confidence': row.confidence,
        'confirmed': row.confirmed,
        'has_exact_text_evidence': any('精确匹配' in reason for reason in row.reasons),
        'has_no_semantics_warning': any('没有跨语言句意依据' in reason for reason in row.reasons),
        'has_gap_warning': any('较长空档' in reason for reason in row.reasons),
    } for row in project.mappings]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--reference', type=Path,
                        help='Optional explicitly selected reference, supplied equally to both diagnostic copies')
    parser.add_argument('projects', nargs='+', type=Path)
    args = parser.parse_args()
    baseline = load_baseline(args.baseline)
    reference_bytes = args.reference.read_bytes() if args.reference else None
    reference_lyrics = import_original(args.reference)[0] if args.reference else None
    report = {'kind': 'fixed stored OCR observations, matching-only A/B',
              'ground_truth_available': False,
              'baseline_sha256': sha256(args.baseline.read_bytes()).hexdigest(),
              'current_sha256': sha256(Path(matching.__file__).read_bytes()).hexdigest(),
              'explicit_diagnostic_reference_sha256': sha256(reference_bytes).hexdigest() if reference_bytes else None,
              'projects': []}
    for path in args.projects:
        original_bytes = path.read_bytes()
        payload = json.loads(original_bytes.decode('utf-8-sig'))
        project = Project.from_dict(payload.get('project', payload))
        original_rows = digest([asdict(row) for row in project.mappings])
        preserved = deepcopy(project)
        matching.match_project(preserved)
        item = {
            'recovery_sha256': sha256(original_bytes).hexdigest(),
            'counts': {name: len(getattr(project, name))
                       for name in ('lyrics', 'translations', 'visuals', 'mappings')},
            'explicit_references': sum(bool(t.reference) for t in project.translations),
            'timed_translations': sum(t.start is not None and t.end is not None for t in project.translations),
            'protected_rows': sum(row.locked or row.human_edited or bool(row.text_segments)
                                  or row.text_role != 'translation' for row in project.mappings),
            'protected_project_rows_unchanged': original_rows == digest([asdict(row) for row in preserved.mappings]),
            'fixed_settings': {key: project.settings.get(key) for key in
                               ('engine', 'roi', 'ocr_device', 'sample_fps', 'ocr_preprocess')},
            'results': {},
        }
        for name, module in (('dev8', baseline), ('dev9', matching)):
            view = candidate_view(project, reference_lyrics)
            before = digest([asdict(visual) for visual in view.visuals])
            module.match_project(view)
            item['results'][name] = {
                'rows': mapping_summary(view),
                'visual_observations_unchanged': before == digest([asdict(v) for v in view.visuals]),
                'visual_observations_sha256': before,
            }
        item['source_bytes_unchanged'] = path.read_bytes() == original_bytes
        report['projects'].append(item)
    report['limitations'] = [
        'Stored recognizer text and boundaries were held fixed; no recognizer was rerun.',
        'No character error rate, boundary accuracy, translation semantics, or full-song quality verdict.',
        'Fresh candidates are diagnostic copies; existing user rows retain all protections.',
        'An optional reference is supplied equally to both versions, never inferred or installed in the real project.',
        'Adding a reference changes inputs; compare versions only within the same report, not to the no-reference report.',
    ]
    report['reference_source_unchanged'] = (args.reference.read_bytes() == reference_bytes) if args.reference else None
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps([{**item['counts'], 'source_unchanged': item['source_bytes_unchanged'],
                       'protected_rows': item['protected_rows'],
                       'protected_project_rows_unchanged': item['protected_project_rows_unchanged'],
                       'comparison': {key: {'rows': len(value['rows']),
                                            'text_anchors': sum(row['has_exact_text_evidence'] for row in value['rows']),
                                            'uncertain_rows': sum(row['has_no_semantics_warning'] for row in value['rows']),
                                            'observations_unchanged': value['visual_observations_unchanged']}
                                      for key, value in item['results'].items()}}
                      for item in report['projects']], indent=2))


if __name__ == '__main__':
    main()
