import itertools
import math
import numpy as np
import pytest

from lyricsync.experimental_nextfire_alignment import (
    align_reading_rows, ctc_token_spans, normalize_reading,
)


def test_reading_never_silently_drops_japanese_or_digits():
    assert normalize_reading("Boku-ra, mō ichido！") == "bokuramoichido"
    for value in ["僕ら", "hi日", "123", "", "  !", "🙂"]:
        with pytest.raises(ValueError):
            normalize_reading(value)


def test_repeated_targets_keep_distinct_occurrences():
    emission = np.log(np.array([[.01, .98, .01], [.98, .01, .01], [.01, .98, .01]]))
    spans = ctc_token_spans(emission, [1, 1], blank_id=0)
    assert [(s['start_frame'], s['end_frame']) for s in spans] == [(0, 1), (2, 3)]
    with pytest.raises(ValueError, match="Not enough"):
        ctc_token_spans(emission[:2], [1, 1], blank_id=0)


def test_independent_dp_matches_exhaustive_ctc_paths():
    # Exhaustive oracle, not another copy of the dynamic program.
    rng = np.random.default_rng(18)
    for targets in ([1], [1, 2], [1, 1], [2, 1, 2]):
        values = rng.random((5, 3))
        values /= values.sum(axis=1, keepdims=True)
        logs = np.log(values)
        best_score, best = -math.inf, None
        for path in itertools.product(range(3), repeat=5):
            collapsed = [k for i, k in enumerate(path) if (not i or k != path[i-1]) and k]
            score = sum(logs[t, p] for t, p in enumerate(path))
            if collapsed == targets and score > best_score:
                best_score, best = score, path
        spans = ctc_token_spans(logs, targets, blank_id=0)
        rebuilt = [0] * 5
        for s in spans:
            rebuilt[s['start_frame']:s['end_frame']] = [s['token_id']] * (s['end_frame'] - s['start_frame'])
        assert tuple(rebuilt) == best


def test_row_boundaries_preserve_original_and_repeat_ids():
    logs = np.log([[.01, .99], [.99, .01], [.01, .99]])
    rows = [dict(id='a', text='あ', reading='a'), dict(id='b', text='あ', reading='a')]
    result = align_reading_rows(logs, rows, {'a': 1}, blank_id=0, duration=.3)
    assert [r['id'] for r in result] == ['a', 'b']
    assert [r['text'] for r in result] == ['あ', 'あ']
    assert result[0]['start_time'] == 0
    assert result[1]['end_time'] == pytest.approx(.3)
    with pytest.raises(ValueError, match='Duplicate'):
        align_reading_rows(logs, rows[:1] * 2, {'a': 1}, blank_id=0, duration=.3)


@pytest.mark.parametrize('values,targets,blank', [([[float('nan'), 0]], [1], 0), ([[0, 0]], [0], 0), ([[0, 0]], [], 0)])
def test_invalid_emissions_and_targets_rejected(values, targets, blank):
    with pytest.raises(ValueError):
        ctc_token_spans(values, targets, blank_id=blank)