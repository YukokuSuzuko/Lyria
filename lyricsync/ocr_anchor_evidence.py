"""Interpret immutable OCR tracks using their measured component footprints."""
from math import isfinite

from .model import normalized


def single_crop(settings):
    if not settings.get('ocr_multi_region'):
        return settings.get('roi')
    active = [r for r in settings.get('ocr_regions') or () if isinstance(r, dict) and r.get('enabled', True)]
    return active[0].get('box') if len(active) == 1 else None


def _box(value):
    return (isinstance(value, (list, tuple)) and len(value) == 4
            and all(isinstance(x, (int, float)) and isfinite(x) for x in value)
            and value[2] > value[0] and value[3] > value[1])


def _score(record, key='confidence'):
    value = record.get(key)
    return value if isinstance(value, (int, float)) and 0 <= value <= 1 else None


def _records(value):
    return value if isinstance(value, list) and all(isinstance(p, dict) for p in value) else []


def _edge_fragment(text, confidence, box, roi):
    if not _box(roi) or not _box(box) or confidence is None or not isinstance(text, str):
        return False
    x1, y1, x2, y2 = box
    left, top, right, bottom = roi
    height = bottom - top
    return (0 < len(normalized(text)) <= 3 and confidence < .75
            and y1 <= top + 1 and y2 >= bottom - 1
            and (x1 <= left + 1 or x2 >= right - 1) and x2 - x1 > 3 * height)


def _clipped_part(part, crop):
    return _edge_fragment(part.get('text'), _score(part), part.get('box'), crop)


def _multi(visual):
    return sum(isinstance(r, dict) and r.get('enabled', True) is True
               for r in _records(visual.evidence.get('roi_configuration'))) > 1


def clipped_noise(visual, roi):
    evidence = visual.evidence
    if _multi(visual):
        owner = evidence.get('roi_owner')
        roi = next((r.get('box') for r in _records(evidence.get('roi_configuration'))
                    if r.get('id') == owner and r.get('enabled', True)), None)
        if not _box(roi) or not _box(visual.box) or len(evidence.get('roi_ids', [])) > 1:
            return False
        key = normalized(visual.text)
        height = roi[3] - roi[1]
        if (key.isdecimal() and len(key) <= 3 and len(set(key)) == 1
                and visual.box[1] <= roi[1]+1 and visual.box[3]-visual.box[1] >= .35*height):
            observations = _records(evidence.get('components'))
            def cut_at_top(obs):
                parts = _records((obs.get('evidence') or {}).get('components'))
                return (len(parts) == 1 and _box(parts[0].get('box')) and parts[0]['box'][1] <= 1
                        and normalized(parts[0].get('text','')).isdecimal())
            if observations and all(cut_at_top(obs) for obs in observations):
                return True
    if not _edge_fragment(
            visual.text, _score(evidence, 'recognition_confidence'), visual.box, roi):
        return False
    crop = [0, 0, roi[2] - roi[0], roi[3] - roi[1]]
    observations = _records(evidence.get('components'))
    for observation in observations:
        details = observation.get('evidence')
        parts = _records(details.get('components')) if isinstance(details, dict) else []
        if len(parts) != 1 or not _clipped_part(parts[0], crop):
            return False
    return bool(observations)


def _covers(inner, relative, roi):
    if not _box(relative):
        return False
    left, top, right, bottom = [v + roi[i % 2] for i, v in enumerate(relative)]
    w = max(0, min(right, inner[2]) - max(left, inner[0]))
    h = max(0, min(bottom, inner[3]) - max(top, inner[1]))
    return w * h >= .7 * (inner[2] - inner[0]) * (inner[3] - inner[1])


def corroborates_component(outer, inner, roi):
    if not _box(roi) or not _box(inner.box) or _multi(outer):
        return False
    crop = [0, 0, roi[2] - roi[0], roi[3] - roi[1]]
    for observation in _records(outer.evidence.get('components')):
        evidence = observation.get('evidence')
        score = _score(observation)
        if (score is None or not isinstance(evidence, dict) or
                evidence.get('assembly') != 'whole_spatial_line_rerecognition'):
            continue
        parts = _records(evidence.get('components'))
        if not any(_clipped_part(p, crop) for p in parts):
            continue
        for part in parts:
            if not isinstance(part.get('text'), str) or normalized(part['text']) != normalized(inner.text):
                continue
            readings = [part, *_records(part.get('provenance'))]
            scores = [_score(p) for p in readings if isinstance(p.get('text'), str)
                      and normalized(p['text']) == normalized(inner.text)]
            if any(s is not None and s >= max(.8, score + .1) for s in scores):
                if _covers(inner.box, part.get('box'), roi):
                    return True
    return False


def has_cheer_evidence(visual):
    """Recognise a stylised exclamation for grouping without changing its transcript."""
    import re
    import unicodedata
    from .translation_alignment import interjection

    word = unicodedata.normalize('NFKC', visual.text)
    match = re.fullmatch(r'([ぁ-ゖァ-ヺ])[~〜ー]+([ぁ-ゖァ-ヺ]{1,4})[~〜ー]+!+', word)
    if not match or not interjection(match.group(2)):
        return False
    observations = _records(visual.evidence.get('components'))
    if not observations:
        return True  # Legacy tracks may only retain the two prolonged syllables.
    def parts(text):
        return re.sub(r'[~〜ー!！]', '', unicodedata.normalize('NFKC', text))
    voted = parts(word)
    winning = max((_score(o) or 0 for o in observations if parts(str(o.get('text','')))==voted), default=0)
    return any(isinstance(o.get('text'), str) and interjection(o['text'])
               and parts(o['text'])[1:] == voted[1:] and (_score(o) or 0) > winning
               for o in observations)


def observed_groups(visuals, roi, sample_fps):
    """Use the observed union only when both flanks confirm an isolated reading."""
    i = 0
    interval = 1.0 / max(.5, sample_fps if isinstance(sample_fps, (int, float)) and isfinite(sample_fps) else 3)
    while i < len(visuals):
        if i + 2 < len(visuals):
            left, middle, right = visuals[i:i + 3]
            score = _score(middle.evidence, 'recognition_confidence')
            frames = middle.evidence.get('frame_count')
            if (not _multi(middle) and isinstance(frames, int)
                    and 0 < frames <= 2 and score is not None and score >= .85
                    and 0 <= middle.start - left.end <= interval + .04
                    and 0 <= right.start - middle.end <= interval + .04
                    and corroborates_component(left, middle, roi)
                    and corroborates_component(right, middle, roi)):
                yield middle.text, [left, middle, right]
                i += 3
                continue
        yield visuals[i].text, [visuals[i]]
        i += 1
