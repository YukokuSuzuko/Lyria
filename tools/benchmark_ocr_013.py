"""Local-only same-input OCR benchmark. Raw output is private; do not package it."""
import argparse
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch

import cv2
import pytesseract
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lyricsync import vision


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * fraction
    low = int(position)
    return ordered[low] + (ordered[min(low + 1, len(ordered) - 1)] - ordered[low]) * (position - low)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resources', type=Path, required=True)
    parser.add_argument('--frames', type=Path, required=True)
    parser.add_argument('--frame-ids', type=int, nargs='+', required=True)
    parser.add_argument('--chinese-roi', type=int, nargs=4, default=[333, 833, 1590, 936])
    parser.add_argument('--source-roi', type=int, nargs=4, default=[298, 950, 1666, 1071])
    parser.add_argument('--chinese-language', default='chi_sim')
    parser.add_argument('--source-language', default='jpn+eng')
    parser.add_argument('--devices', nargs='+', choices=['cpu', 'directml'], default=['cpu', 'directml'])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--candidate-order', nargs='+', default=['tess11', 'rapid-cpu', 'rapid-directml'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    valid = {'tess11', 'tess7', 'tess6'} | {'rapid-' + device for device in args.devices}
    if any(name not in valid for name in args.candidate_order):
        parser.error('candidate-order contains an unsupported or disabled candidate')
    if args.output.exists():
        parser.error('output already exists; choose a new evidence file')
    vision.resources = lambda: args.resources
    roles = [('chinese', args.chinese_language, args.chinese_roi),
             ('source', args.source_language, args.source_roi)]
    inputs = {}
    for number in args.frame_ids:
        path = args.frames / f'frame-{number:09}.png'
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f'Unreadable frame {number}')
        for _, _, (left, top, right, bottom) in roles:
            if not 0 <= left < right <= image.shape[1] or not 0 <= top < bottom <= image.shape[0]:
                raise ValueError('ROI outside image')
        inputs[number] = (image, hashlib.sha256(path.read_bytes()).hexdigest())
    result = {'configuration': {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
              'timing_scope': 'Frames preloaded; inference includes provider preprocessing. Initialization separate. Candidate order rotates by round; frames reverse on alternate rounds. No claim of whole-machine idle.',
              'initializations': [], 'rows': [], 'rounds': [], 'mixed_rounds': []}
    providers = {}
    for role, language, _ in roles:
        for candidate in args.candidate_order:
            started = time.perf_counter()
            record = {'role': role, 'candidate': candidate}
            try:
                provider = (vision.TesseractOCR(language=language) if candidate.startswith('tess') else
                            vision.RapidOCRProvider(language=language, references=(), device=candidate.split('-')[1]))
                providers[role, candidate] = provider
                record['signature'] = provider.signature
            except Exception as exc:
                record['error'] = f'{type(exc).__name__}: {exc}'
            record['seconds'] = time.perf_counter() - started
            result['initializations'].append(record)

    def recognize(role, candidate, number, repeat, phase):
        provider = providers.get((role, candidate))
        row = {'role': role, 'candidate': candidate, 'frame': number, 'repeat': repeat,
               'phase': phase, 'sha256': inputs[number][1]}
        started = time.perf_counter()
        try:
            if provider is None:
                raise RuntimeError('Provider initialization failed; no fallback')
            box = next(box for name, _, box in roles if name == role)
            left, top, right, bottom = box
            original = pytesseract.image_to_data
            def call(*positional, **keyword):
                keyword['config'] = keyword['config'].replace('--psm 11', '--psm ' + candidate[4:])
                return original(*positional, **keyword)
            context = patch.object(pytesseract, 'image_to_data', call) if candidate.startswith('tess') else nullcontext()
            with context:
                detections = provider.recognize(inputs[number][0][top:bottom, left:right])
            row['text'] = '\n'.join(item.text for item in detections)
        except Exception as exc:
            row['error'] = f'{type(exc).__name__}: {exc}'
        row['seconds'] = time.perf_counter() - started
        result['rows'].append(row)

    for repeat in range(args.repeats):
        order = args.candidate_order[repeat % len(args.candidate_order):] + args.candidate_order[:repeat % len(args.candidate_order)]
        numbers = args.frame_ids if repeat % 2 == 0 else list(reversed(args.frame_ids))
        for candidate in order:
            started = time.perf_counter()
            for number in numbers:
                for role, _, _ in roles:
                    recognize(role, candidate, number, repeat, 'comparison')
            result['rounds'].append({'repeat': repeat, 'candidate': candidate, 'seconds': time.perf_counter() - started})
        for device in args.devices:
            if 'rapid-' + device not in args.candidate_order or 'tess11' not in args.candidate_order:
                continue
            started = time.perf_counter()
            for number in numbers:
                recognize('chinese', 'rapid-' + device, number, repeat, 'mixed-' + device)
                recognize('source', 'tess11', number, repeat, 'mixed-' + device)
            result['mixed_rounds'].append({'repeat': repeat, 'device': device, 'seconds': time.perf_counter() - started})
    result['summary'] = []
    for role, _, _ in roles:
        for candidate in args.candidate_order:
            rows = [row for row in result['rows'] if row['role'] == role and row['candidate'] == candidate and row['phase'] == 'comparison']
            values = [row['seconds'] for row in rows if 'error' not in row]
            changed = [number for number in args.frame_ids if len({row.get('text') for row in rows if row['frame'] == number and 'error' not in row}) > 1]
            result['summary'].append({'role': role, 'candidate': candidate, 'successful_calls': len(values),
                                     'errors': len(rows) - len(values), 'p50_seconds': percentile(values, .5),
                                     'p95_seconds': percentile(values, .95), 'changed_output_frames': changed})
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'summary': result['summary'], 'mixed_rounds': result['mixed_rounds']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
