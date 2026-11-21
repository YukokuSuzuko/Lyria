"""Compare adjacent local reference text read-only; never treat it as ground truth.

Character equality establishes text correspondence only. Reference provenance,
its media timing, and Chinese/Japanese semantics require independent validation.
Reports omit raw lyrics and preserve all source file bytes.
"""
from difflib import SequenceMatcher
from hashlib import sha256
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lyricsync.model import Project, normalized
from lyricsync.subtitles import import_original, import_translation


def indexed_text(rows):
    parts = [normalized(row.text) for row in rows]
    positions = []
    owners = []
    cursor = 0
    for index, part in enumerate(parts):
        positions.append((cursor, cursor+len(part)))
        owners.extend([index]*len(part))
        cursor += len(part)
    return ''.join(parts), positions, owners


def compare(source, target):
    left, positions, source_owners = indexed_text(source)
    right, _, owners = indexed_text(target)
    matcher = SequenceMatcher(None, left, right, autojunk=False)
    correspondence = {}
    for block in matcher.get_matching_blocks():
        correspondence.update((block.a+offset, block.b+offset) for offset in range(block.size))
    result = []
    for index, (start, end) in enumerate(positions):
        matched = [correspondence[char] for char in range(start, end) if char in correspondence]
        target_indices = list(dict.fromkeys(owners[char] for char in matched))
        complete = bool(end-start) and len(matched) == end-start
        contiguous = complete and matched == list(range(matched[0], matched[-1]+1))
        result.append({'source_index': index, 'source_character_count': end-start,
                       'matched_character_count': len(matched),
                       'complete_contiguous_text_match': contiguous,
                       'target_indices': target_indices,
                       'source_time': [source[index].start, source[index].end],
                       'target_times': [[target[i].start, target[i].end] for i in target_indices]})
    return {'source_rows': len(source), 'target_rows': len(target),
            'source_characters': len(left), 'target_characters': len(right),
            'matched_characters': len(correspondence), 'rows': result,
            'edit_operations': [{'operation': op, 'source_characters': a1-a0,
                                  'target_characters': b1-b0,
                                  'source_indices': list(dict.fromkeys(source_owners[a0:a1])),
                                  'target_indices': list(dict.fromkeys(owners[b0:b1]))}
                                 for op, a0, a1, b0, b1 in matcher.get_opcodes()]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recovery', type=Path, required=True)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--translation-reference', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    paths = [args.recovery, args.reference]
    if args.translation_reference:
        paths.append(args.translation_reference)
    originals = {path: path.read_bytes() for path in paths}
    payload = json.loads(originals[args.recovery].decode('utf-8-sig'))
    project = Project.from_dict(payload.get('project', payload))
    lyrics, warnings = import_original(args.reference)
    report = {'kind': 'adjacent file text correspondence; unverified provenance and timing',
              'reference_is_not_imported_into_project': not project.inputs.get('ktv'),
              'reference_import_warning_count': len(warnings),
              'reference_vs_ocr': compare(lyrics, project.visuals),
              'ground_truth_available': False,
              'source_sha256': [sha256(data).hexdigest() for data in originals.values()]}
    indices = {visual.id: index for index, visual in enumerate(project.visuals)}
    saved_rows = {tid: row for row in project.mappings for tid in row.translation_ids}
    for pair in report['reference_vs_ocr']['rows']:
        index = pair['source_index']
        if index < len(project.translations):
            row = saved_rows.get(project.translations[index].id)
            pair['same_ordinal_translation_visual_indices'] = [indices.get(i) for i in row.visual_ids] if row else []
    if args.translation_reference:
        formal = import_translation(args.translation_reference)
        report['imported_translation_vs_adjacent_formal'] = compare(project.translations, formal)
    report['all_source_bytes_unchanged'] = all(path.read_bytes() == data for path, data in originals.items())
    report['limitations'] = [
        'Same-ordinal Chinese translation is an unverified correspondence, not semantic truth.',
        'Character matching may use surrounding order to distinguish repeated text.',
        'Reference timestamps are listed for inspection, not used to overwrite OCR boundaries.',
        'No result is applied to the original project or its recovery.',
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({
        'reference_rows': len(lyrics), 'ocr_rows': len(project.visuals),
        'complete_text_matches': sum(row['complete_contiguous_text_match'] for row in report['reference_vs_ocr']['rows']),
        'multi_observation_matches': sum(row['complete_contiguous_text_match'] and len(row['target_indices']) > 1
                                         for row in report['reference_vs_ocr']['rows']),
        'source_unchanged': report['all_source_bytes_unchanged'],
    }, indent=2))


if __name__ == '__main__':
    main()
