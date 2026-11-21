"""Research-only NextFire timing adapter. Not a production model/catalog option.

The acoustic model is noncommercial CC-BY-NC-SA-4.0. No weights, converter,
third-party executable code or downloads are included here. Explicit caller-
supplied romanized readings are required; original Japanese is never discarded.
The small CTC dynamic program below is an independent implementation, not copied
from torchaudio, Yohane, or FA-Kara. See docs/DEV7_ALIGNMENT_COMPARISON_20261114.md.
"""
from __future__ import annotations

import math
import unicodedata

MODEL_REPO = "NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn"
MODEL_REVISION = "2ab2b5f46539ee284703c281f286b01d2410ee12"
MODEL_SHA256 = "4d1d6fbb543534ab32bcceac5f5a42052c7fde3d16a83cbbce858473d5735042"


def normalize_reading(reading: str) -> str:
    """Reject unconverted Japanese/unknown letters, rather than silently lose text.

    Spaces divide readings for review, not model labels (the author's inference
    flattens per-word/syllable tokens without introducing a delimiter token).
    Punctuation is discarded. Digits require an explicit spoken reading.
    """
    if not isinstance(reading, str):
        raise ValueError("An explicit romanized reading is required")
    value = unicodedata.normalize("NFKD", reading.casefold().replace("’", "'"))
    result = []
    for char in value:
        if "a" <= char <= "z" or char == "'":
            result.append(char)
        # Lyric elongation/pitch decorations are not phonetic labels. Keep
        # arbitrary symbols/letters strict (e.g. kaomoji still need review).
        elif char in "~↑↓♪♫" or unicodedata.combining(char) or char.isspace() or unicodedata.category(char).startswith("P"):
            continue
        else:
            raise ValueError("Reading contains an unconverted or unsupported character: " + repr(char))
    if not result:
        raise ValueError("Reading has no model labels")
    return "".join(result)


def ctc_token_spans(log_probs, token_ids, *, blank_id: int, max_backtrack_cells=4_000_000):
    """Maximum-likelihood CTC path, returning one span per *target occurrence*.

    No interpolation, blank-duration allocation, repeated-chorus text matching or
    fabricated timings. Scores describe a forced path, NOT probability that a
    lyric was sung. Production acceptance must include mismatch/omission tests.
    """
    import numpy as np

    probs = np.asarray(log_probs)
    targets = list(token_ids)
    if probs.ndim != 2 or probs.shape[0] < 1 or not np.isfinite(probs).all():
        raise ValueError("Expected finite frame-by-label log probabilities")
    if (not targets or type(blank_id) is not int or not 0 <= blank_id < probs.shape[1]
            or any(type(t) is not int or not 0 <= t < probs.shape[1] or t == blank_id for t in targets)):
        raise ValueError("Invalid CTC targets or blank label")
    frames, _ = probs.shape
    minimum = len(targets) + sum(a == b for a, b in zip(targets, targets[1:]))
    if frames < minimum:
        raise ValueError("Not enough audio frames for the supplied transcript")
    states = np.full(2 * len(targets) + 1, blank_id, dtype=np.int64)
    states[1::2] = targets
    allowed_skip = np.zeros(len(states), dtype=bool)
    allowed_skip[2:] = (states[2:] != blank_id) & (states[2:] != states[:-2])
    scores = np.full(len(states), -np.inf)
    scores[:2] = probs[0, states[:2]]
    def advance(previous, frame):
        step = np.full_like(previous, -np.inf)
        skip = np.full_like(previous, -np.inf)
        step[1:] = previous[:-1]
        skip[2:] = previous[:-2]
        skip[~allowed_skip] = -np.inf
        paths = np.stack((previous, step, skip))
        best = paths.argmax(axis=0)
        return paths[best, np.arange(len(states))] + probs[frame, states], best

    if type(max_backtrack_cells) is not int or max_backtrack_cells < len(states):
        raise ValueError('CTC traceback budget must hold at least one state row')
    # Exact global Viterbi path, including repeated lyric occurrences. Store
    # only rolling scores and bounded traceback blocks in RAM; score checkpoints
    # go to an automatically cleaned temporary file. Recompute each block once
    # in reverse. This changes memory use, never transcript/time association.
    import tempfile
    block = max(1, min(512, max_backtrack_cells // len(states) - 1))
    back = np.zeros((min(block, frames - 1) + 1, len(states)), dtype=np.uint8)
    path = np.empty(frames, dtype=np.int64)
    with tempfile.TemporaryFile() as checkpoints:
        checkpoints.write(scores.tobytes())
        for frame in range(1, frames):
            scores, _ = advance(scores, frame)
            if frame % block == 0:
                checkpoints.write(scores.tobytes())
        last = len(states) - 2 + int(scores[-1] > scores[-2])
        if not math.isfinite(float(scores[last])):
            raise ValueError("No complete CTC path for the transcript")
        end = frames - 1
        while end > 0:
            start = ((end - 1) // block) * block
            checkpoints.seek((start // block) * len(states) * 8)
            scores = np.frombuffer(checkpoints.read(len(states) * 8), dtype=np.float64).copy()
            for frame in range(start + 1, end + 1):
                scores, best = advance(scores, frame)
                back[frame - start] = best
            for frame in range(end, start, -1):
                path[frame] = last
                last -= int(back[frame - start, last])
            end = start
        path[0] = last
    spans = []
    for index, token in enumerate(targets):
        positions = np.flatnonzero(path == 2 * index + 1)
        if not len(positions):
            raise ValueError("CTC path omitted a target occurrence")
        spans.append({"token_id": token, "start_frame": int(positions[0]),
                      "end_frame": int(positions[-1] + 1),
                      "score": float(np.exp(probs[positions, token].mean()))})
    return spans


def align_reading_rows(log_probs, rows, vocabulary, *, blank_id: int, duration: float):
    """Keep stable row identity and original text alongside every explicit reading."""
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid audio duration")
    if not rows or any(not isinstance(row.get("id"), str) or not row["id"]
                       or not isinstance(row.get("text"), str) for row in rows):
        raise ValueError("Each row needs a stable ID and original text")
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate row IDs")
    readings = [normalize_reading(row["reading"]) for row in rows]
    try:
        ids = [vocabulary[char] for reading in readings for char in reading]
    except KeyError as exc:
        raise ValueError("Reading contains a label absent from the model vocabulary") from exc
    spans = ctc_token_spans(log_probs, ids, blank_id=blank_id)
    frame_seconds = duration / len(log_probs)
    result, offset = [], 0
    for row, reading in zip(rows, readings):
        selected = spans[offset:offset + len(reading)]
        offset += len(reading)
        result.append({"id": row["id"], "text": row["text"], "reading": row["reading"],
                       "model_reading": reading, "frame_seconds": frame_seconds,
                       "start_time": selected[0]["start_frame"] * frame_seconds,
                       "end_time": selected[-1]["end_frame"] * frame_seconds,
                       "path_score": min(span["score"] for span in selected),
                       "token_spans": selected,
                       "status": "research_only_forced_path_requires_review"})
    return result
