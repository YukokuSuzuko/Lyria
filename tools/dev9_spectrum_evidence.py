"""Read-only same-PCM spectrum comparison; never launches foreground apps.

All images are diagnostic renderer output, not screenshots or Aegisub parity.
User audio is only read locally; no decoded PCM is persisted or packaged.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

import av
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lyricsync import spectrogram as current


def load_baseline(path):
    spec = importlib.util.spec_from_file_location('dev9_spectrum_baseline', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def digest(path):
    hasher = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def centre_values(magnitudes, height):
    bins = current.frequency_rows(height) * (magnitudes.shape[1] - 1) * 2 / current.RATE
    low = np.floor(bins).astype(int)
    return magnitudes[:, low] * (1 - (bins - low)) + magnitudes[:, low + 1] * (bins - low)


def brightness(rgb):
    return rgb @ np.array([.2126, .7152, .0722]) / 255


def metrics(rgb):
    light = brightness(rgb)
    upper = light[:round(rgb.shape[0] * .32)]
    return dict(mean_luminance=float(light.mean()),
                upper_band_median_luminance=float(np.median(upper)),
                upper_band_p95_luminance=float(np.percentile(upper, 95)),
                upper_band_p95_minus_median=float(np.percentile(upper, 95) - np.median(upper)),
                temporal_luminance_gradient_p95=float(np.percentile(np.abs(np.diff(light, axis=1)), 95)),
                saturated_pixels_fraction=float(np.mean(np.all(rgb == 255, axis=2))))


def comparison(path, panels, width, height, start, end, title):
    header, footer = 42, 30
    output = Image.new('RGB', (width + 76, (height + header + footer) * len(panels)), '#171923')
    draw = ImageDraw.Draw(output)
    for number, (label, rgb) in enumerate(panels):
        top = number * (height + header + footer)
        draw.text((76, top + 5), title, fill='white')
        draw.text((76, top + 22), label, fill='#D6CBF5')
        output.paste(Image.fromarray(rgb), (76, top + header))
        for frequency, position in [(10000, 0), (4000, .32), (0, 1)]:
            draw.text((3, top + header + int(position * (height - 12))), f'{frequency} Hz', fill='#C5C5C5')
        for second in np.linspace(start, end, 9):
            x = 76 + round((second - start) / (end - start) * (width - 1))
            draw.line((x, top + header + height, x, top + header + height + 4), fill='white')
            draw.text((min(x, width + 42), top + header + height + 6), f'{second:g}s', fill='white')
    output.save(path)


def synthetic_resize(baseline):
    times = np.arange(current.RATE // 4) / current.RATE
    ratios = {'dev8': [], 'dev9': []}
    for frequency in np.arange(6200, 9801, 50):
        pcm = (.1 * np.sin(2 * np.pi * frequency * times)).astype(np.float32)
        source = current.AudioSource(None, pcm, .25, 1.)
        magnitudes = current.stft_columns(source, [.125], window=1024)
        for name, module in [('dev8', baseline), ('dev9', current)]:
            tall = module.colourize(magnitudes, 1., 1024, gamma=1., floor_db=-80, ceiling_db=0)
            short = module.colourize(magnitudes, 1., 96, gamma=1., floor_db=-80, ceiling_db=0)
            ratios[name].append(float(short.max()) / max(1., float(tall.max())))
    result = {name: dict(minimum_peak_rgb_ratio=min(values),
                        median_peak_rgb_ratio=float(np.median(values)),
                        tones_below_95_percent=sum(r < .95 for r in values), tones=len(values))
              for name, values in ratios.items()}
    assert result['dev8']['minimum_peak_rgb_ratio'] < .95
    assert result['dev9']['minimum_peak_rgb_ratio'] >= .95
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--media', type=Path, required=True)
    parser.add_argument('--baseline-module', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--start', type=float, default=37)
    parser.add_argument('--end', type=float, default=45)
    parser.add_argument('--width', type=int, default=1200)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    baseline = load_baseline(args.baseline_module)
    before = digest(args.media)
    with av.open(str(args.media)) as container:
        duration = container.duration / av.time_base
    started = time.perf_counter()
    source = current.AudioSource.decode(args.media, duration)
    decode_seconds = time.perf_counter() - started
    rows = []
    peak_metrics = []
    try:
        seconds = args.start + (np.arange(args.width) + .5) * (args.end - args.start) / args.width
        for window in (512, 1024):
            magnitudes = current.stft_columns(source, seconds, window=window)
            calibration = current.global_calibration(source, window)
            for height in (96, 192, 384):
                peak = current.frequency_row_values(magnitudes, height)
                centres = centre_values(magnitudes, height)
                frequency = current.frequency_rows(height)
                # Count only visible-range signals. This reports lost peaks,
                # not transcription, vocal identity or annotated onset truth.
                eligible = (peak >= source.reference * 10 ** (-45 / 20)) & (frequency[None, :] >= 4000)
                loss = 20 * np.log10(np.maximum(centres, 1e-12) / np.maximum(peak, 1e-12))
                peak_metrics.append(dict(window=window, height=height,
                    eligible_upper_band_pixels=int(eligible.sum()),
                    dev8_underestimated_by_6db_fraction=float(np.mean(loss[eligible] < -6)) if eligible.any() else None,
                    dev8_loss_p05_db=float(np.percentile(loss[eligible], 5)) if eligible.any() else None))
                panels = []
                for name, preset in current.DISPLAY_PRESETS.items():
                    reference = calibration.reference if preset['quantile'] else source.reference
                    options = dict(gamma=preset['gamma'], purple=True,
                                   floor_db=preset['floor_db'], ceiling_db=preset['ceiling_db'])
                    old = baseline.colourize(magnitudes, reference, height, **options)
                    new = current.colourize(magnitudes, reference, height, **options)
                    for version, rgb in [('dev8', old), ('dev9', new)]:
                        rows.append(dict(window=window, height=height, preset=name, version=version,
                                         reference=reference, **metrics(rgb)))
                    if name == 'detail':
                        panels.extend([('dev8 / detail / row-centre interpolation', old),
                                       ('dev9 / detail / per-pixel frequency peak envelope', new)])
                        Image.fromarray(old).save(args.out / f'dev8-detail-{window}-{height}.png')
                        Image.fromarray(new).save(args.out / f'dev9-detail-{window}-{height}.png')
                        # Controlled investigation only; never enabled in product.
                        # A +6 dB high shelf weights instruments/noise as well.
                        frequencies = np.fft.rfftfreq(current.FFT, 1 / current.RATE)
                        shelf = np.clip((frequencies - 1000) / 3000, 0, 1) * 6
                        weighted = current.colourize(magnitudes * 10 ** (shelf[None, :] / 20), reference, height, **options)
                        rows.append(dict(window=window, height=height, preset=name, version='experimental_high_shelf_6db',
                                         reference=reference, **metrics(weighted)))
                        panels.append(('experiment only / same peaks, +6 dB above 4 kHz (NOT enabled)', weighted))
                    elif height == 192:
                        panels.extend([(f'dev8 / {name}', old), (f'dev9 / {name}', new)])
                comparison(args.out / f'comparison-{window}-{height}.png', panels, args.width, height,
                           args.start, args.end, f'0030 original mix / {window}-sample Hann / {height}px / same PCM, time, palette')
        source_samples = len(source.samples)
        source_reference = source.reference
    finally:
        source.close()
    after = digest(args.media)
    report = dict(scope='Local headless renderer diagnostic; not Windows foreground acceptance or Aegisub comparison.',
        input_path=str(args.media), input_sha256_before=before, input_sha256_after=after,
        original_unchanged=before == after, source='first audio stream, original mix; not separated vocals',
        decoded_sample_rate=current.RATE, decoded_samples=source_samples,
        duration_seconds=duration, full_source_peak_reference=source_reference,
        decode_seconds=decode_seconds, start_seconds=args.start, end_seconds=args.end,
        physical_columns=args.width, seconds_per_column=(args.end-args.start)/args.width,
        frequency_map='0..4kHz: lower68%; 4..10kHz: upper32%', fft_size=current.FFT,
        baseline_module=str(args.baseline_module), baseline_module_sha256=digest(args.baseline_module),
        current_module_sha256=digest(Path(current.__file__)),
        caveats=['No recoverable Aegisub original screenshot; equal-parameter GUI comparison unverified.',
                 'No vocal identity/phoneme/onset ground truth. Brightness and peak retention are display metrics only.',
                 'Peak envelope can increase textured background; broad-band noise is not removed.',
                 '+6 dB shelf is experimental evidence only, not enabled; it also raises high-frequency accompaniment/noise.',
                 'Both FFT windows have a time/frequency tradeoff; no timing accuracy claim from a brighter image.'],
        synthetic_narrow_ridge_resize=synthetic_resize(baseline),
        peak_retention=peak_metrics, luminance=rows)
    (args.out / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    assert before == after, 'Read-only input changed during measurement'
    print(json.dumps({'report':str(args.out / 'report.json'), 'unchanged':before == after,
                      'decodedSamples':source_samples, 'imageCount':len(list(args.out.glob('*.png')))}, indent=2))


if __name__ == '__main__':
    main()
