"""Local-only labeled real-frame OCR comparison. Private crops/labels are not shipped.

Manifest: {"samples": [{"id": str, "image": path, "image_sha256": str,
"language": "chi"|"jpn"|"eng", "truth": str}], "truth_method": str}.
Only independently visually verified text should be supplied as truth. Empty
truth is a negative crop, not an omitted label. Whitespace is ignored; NFKC is
applied; punctuation, case and small kana remain scored. No reference text is
passed to OCR. Initialization and inference/preprocessing timing are separate.
"""
from pathlib import Path
import argparse
from dataclasses import asdict
import hashlib
import json
import statistics
import sys
import time
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def normalize(text):
    return ''.join(unicodedata.normalize('NFKC', text).split())


def edit_distance(reference, hypothesis):
    previous = list(range(len(hypothesis) + 1))
    for i, left in enumerate(reference):
        current = [i + 1]
        for j, right in enumerate(hypothesis):
            current.append(min(current[-1] + 1, previous[j + 1] + 1,
                               previous[j] + (left != right)))
        previous = current
    return previous[-1]


def summaries(rows):
    result = []
    for key in sorted({(r['device'], r['profile'], r['language']) for r in rows}):
        subset = [r for r in rows if (r['device'], r['profile'], r['language']) == key]
        successful = [r for r in subset if 'error' not in r]
        positive = [r for r in successful if r['truth_chars']]
        negative = [r for r in successful if not r['truth_chars']]
        chars = sum(r['truth_chars'] for r in positive)
        edits = sum(r['char_errors'] for r in positive)
        times = sorted(r['seconds'] for r in successful)
        result.append(dict(device=key[0], profile=key[1], language=key[2],
            calls=len(subset), errors=len(subset)-len(successful),
            unique_inputs=len({r['id'] for r in subset}), positive_calls=len(positive),
            truth_chars=chars, char_errors=edits, CER=edits/chars if chars else None,
            exact_lines=sum(r['exact'] for r in positive),
            missed_lines=sum(not r['detections'] for r in positive),
            negative_calls=len(negative), false_positive_calls=sum(bool(r['detections']) for r in negative),
            low_confidence_calls=sum(any(d['confidence'] < .85 for d in r['detections']) for r in successful),
            p50_seconds=statistics.median(times) if times else None,
            p95_seconds=times[min(len(times)-1, int((len(times)-1)*.95))] if times else None))
    return result


def main():
    import cv2
    from lyricsync import vision
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--resources', type=Path, default=ROOT/'resources')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--profiles', nargs='+', default=['ppocrv4:raw', 'ppocrv6:raw', 'ppocrv6:subtitle'])
    parser.add_argument('--devices', nargs='+', choices=['cpu', 'directml'], default=['cpu'])
    parser.add_argument('--repeats', type=int, default=2)
    args = parser.parse_args()
    if args.out.exists() or args.out.with_suffix('.jsonl').exists():
        parser.error('Use a new output path; previous evidence is never overwritten.')
    if args.repeats < 1:
        parser.error('repeats must be positive')
    manifest = json.loads(args.manifest.read_text('utf-8'))
    images = {}
    for sample in manifest['samples']:
        if not isinstance(sample['truth'], str):
            raise ValueError('Every crop requires an independently verified truth string.')
        path = Path(sample['image'])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != sample['image_sha256']:
            raise ValueError('Derived crop hash mismatch: ' + sample['id'])
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError('Unreadable crop: ' + str(path))
        images[sample['id']] = image
    vision.resources = lambda: args.resources
    languages = {s['language'] for s in manifest['samples']}
    language_codes = {'chi':'chi_sim', 'jpn':'jpn+eng', 'eng':'eng'}
    engines, initialization = {}, []
    for device in args.devices:
        for profile in args.profiles:
            model, preprocess = profile.split(':')
            for lang in sorted(languages):
                start = time.perf_counter()
                engine = vision.RapidOCRProvider(language=language_codes[lang], device=device,
                                                 model=model, preprocess=preprocess)
                initialization.append(dict(device=device, profile=profile, language=lang,
                    seconds=time.perf_counter()-start, signature=engine.signature))
                engines[device, profile, lang] = engine
    report = dict(manifest_sha256=hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
                  truth_method=manifest.get('truth_method'), selection=manifest.get('selection'),
                  config={k:str(v) if isinstance(v, Path) else v for k,v in vars(args).items()},
                  timing_scope='Preloaded lossless crops; calls include preprocessing/recovery. No whole-machine idle guarantee. Rotated profile order and reversed crop order on alternate rounds. Initialization separate. No warm-up excluded.',
                  initialization=initialization, rows=[])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    for repeat in range(args.repeats):
        profiles = args.profiles[repeat % len(args.profiles):] + args.profiles[:repeat % len(args.profiles)]
        samples = manifest['samples'] if repeat % 2 == 0 else list(reversed(manifest['samples']))
        for device in args.devices:
            for profile in profiles:
                for sample in samples:
                    row = dict(id=sample['id'], language=sample['language'], repeat=repeat,
                               device=device, profile=profile, image_sha256=sample['image_sha256'])
                    start = time.perf_counter()
                    try:
                        detections = engines[device, profile, sample['language']].recognize(images[sample['id']])
                        # Deterministic reading order for horizontally split regions.
                        detections.sort(key=lambda d: (d.box[1] // max(20, images[sample['id']].shape[0]), d.box[0]))
                        row['detections'] = [asdict(d) for d in detections]
                        actual = normalize(''.join(d.text for d in detections))
                        expected = normalize(sample['truth'])
                        row.update(truth_chars=len(expected), char_errors=edit_distance(expected, actual), exact=actual == expected)
                    except Exception as exc:
                        row['error'] = type(exc).__name__ + ': ' + str(exc)
                    row['seconds'] = time.perf_counter()-start
                    report['rows'].append(row)
                    with args.out.with_suffix('.jsonl').open('a', encoding='utf-8') as output:
                        output.write(json.dumps(row, ensure_ascii=False)+'\n')
                report['summary'] = summaries(report['rows'])
                args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
                print(device, profile, 'round', repeat, 'complete', flush=True)
    print(json.dumps(report['summary'], indent=2), flush=True)
    return int(any('error' in row for row in report['rows']))


if __name__ == '__main__':
    raise SystemExit(main())
