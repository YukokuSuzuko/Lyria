"""Frequency-detail preservation, not vocal identity or human acceptance."""
import numpy as np
import pytest

from lyricsync.spectrogram import (AudioSource, RATE, colourize, frequency_row_values,
                                  frequency_rows, stft_columns)


def test_short_panel_preserves_narrow_high_frequency_ridges():
    # A narrow ridge can fall between dev8's sampled display-row centres.
    # Sweep physical tones rather than injecting fabricated display pixels.
    times = np.arange(RATE // 4) / RATE
    ratios = []
    for frequency in np.arange(6200, 9801, 50):
        pcm = (.1 * np.sin(2 * np.pi * frequency * times)).astype(np.float32)
        source = AudioSource(None, pcm, .25, 1.)
        magnitudes = stft_columns(source, [.125], window=1024)
        tall = colourize(magnitudes, 1., 1024, gamma=1., floor_db=-80, ceiling_db=0)
        short = colourize(magnitudes, 1., 96, gamma=1., floor_db=-80, ceiling_db=0)
        ratios.append(float(short.max()) / max(1., float(tall.max())))
    assert min(ratios) >= .95, f'narrow ridge lost during vertical resize: {min(ratios):.4f}'


def test_vertical_reduction_keeps_silence_and_flat_background_unchanged():
    for level in (0., 1e-7, .003, .1):
        values = np.full((8, 1025), level, dtype=np.float32)
        short = colourize(values, 1., 96)
        tall = colourize(values, 1., 1024)
        assert np.all(short == tall[0, 0])


def test_frequency_reduction_does_not_mix_neighbouring_time_columns():
    values = np.zeros((5, 1025), dtype=np.float32)
    values[2, 810] = .3
    image = colourize(values, 1., 96)
    assert image[:, 2].any()
    assert not image[:, [0, 1, 3, 4]].any()


def test_frequency_magnification_retains_interpolation_and_cached_input():
    rng = np.random.default_rng(59)
    values = rng.uniform(0, 1, (4, 1025)).astype(np.float32)
    before = values.copy()
    bins = frequency_rows(4096) * 2048 / RATE
    low = np.floor(bins).astype(int)
    expected = values[:, low] * (1 - (bins - low)) + values[:, low + 1] * (bins - low)
    np.testing.assert_array_equal(frequency_row_values(values, 4096), expected)
    np.testing.assert_array_equal(values, before)
