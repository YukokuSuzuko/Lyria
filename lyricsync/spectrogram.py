"""Viewport STFT tiles. No Qt, no whole-song image, no 8 kHz spectrum input.

A float32 mono 22.05 kHz temporary spool bounds RAM for long media. libswresample
performs the anti-alias filtering/downmix. Cache values are uncoloured spectra,
so vertical resize/contrast changes never repeat the FFT. All callers run this
module off the GUI thread. Temporary PCM is deleted when the source is closed.
"""
from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass, field
import hashlib
import math
import tempfile
import time

import numpy as np

RATE = 22050
WINDOW = 512
FFT = 2048
TILE_WIDTH = 256
MAX_FREQUENCY = 10000
# Bump when the sampling / calibration or STFT convention changes.
SPECTRUM_VERSION = 4
CALIBRATION_CENTRES = 16384
NEAR_SILENCE_PEAK = 1e-4  # -80 dBFS: do not auto-amplify near-zero decode noise.
DISPLAY_PRESETS = {
    'physical': dict(quantile=False, floor_db=-65., ceiling_db=-5., gamma=1.15, mapping='db'),
    'detail': dict(quantile=True, floor_db=-65., ceiling_db=0., gamma=1.8, mapping='soft_knee', log_scale=9.),
    'balanced': dict(quantile=True, floor_db=-60., ceiling_db=0., gamma=2., mapping='soft_knee', log_scale=9.),
    'contrast': dict(quantile=True, floor_db=-55., ceiling_db=0., gamma=2.2, mapping='soft_knee', log_scale=9.),
    'vocal': dict(quantile=True, floor_db=-60., ceiling_db=0., gamma=2., mapping='soft_knee', log_scale=9.),
    'onset': dict(quantile=True, floor_db=-60., ceiling_db=0., gamma=2., mapping='soft_knee', log_scale=9.),
    'harmonic': dict(quantile=True, floor_db=-65., ceiling_db=0., gamma=1.8, mapping='soft_knee', log_scale=9.),
}


def fft_samples(window):
    return 1024 if window in (256,384) else FFT




@dataclass
class AudioSource:
    file: object
    samples: np.ndarray
    duration: float
    reference: float
    path: str = ''
    calibrations: dict = field(default_factory=dict, repr=False)

    @classmethod
    def decode(cls, path, duration, cancel=lambda: False):
        import av
        if not math.isfinite(duration) or not 0 < duration <= 86400:
            raise ValueError('频谱仅支持 24 小时以内的媒体')
        spool = tempfile.TemporaryFile(prefix='lyria-spectrum-')
        count = 0
        peak = 0.
        try:
            with av.open(str(path)) as container:
                if not container.streams.audio:
                    raise ValueError('当前媒体没有音轨')
                stream = container.streams.audio[0]
                origin = (float(container.start_time / av.time_base) if container.start_time is not None
                          else float((stream.start_time or 0) * stream.time_base))
                resampler = av.AudioResampler(format='fltp', layout='mono', rate=RATE)
                limit = math.ceil(duration * RATE)

                def consume(frame):
                    nonlocal count, peak
                    values = np.nan_to_num(frame.to_ndarray().reshape(-1).astype('<f4'), copy=False, nan=0., posinf=0., neginf=0.)
                    offset = round((float(frame.pts * frame.time_base) - origin) * RATE) if frame.pts is not None else count
                    # Retain gaps/stream offset relative to media PTS, never slide
                    # late audio to t=0. Trim overlap and negative preroll.
                    start = max(0, offset, count)
                    values = values[max(0, start - offset):max(0, limit - offset)]
                    if not len(values) or start >= limit:
                        return
                    if start > count:
                        spool.seek(start * 4)  # sparse zero-filled gap
                    spool.write(values.tobytes())
                    count = start + len(values)
                    peak = max(peak, float(np.max(np.abs(values))))

                for frame in container.decode(stream):
                    if cancel():
                        raise InterruptedError('频谱解码已取消')
                    for output in resampler.resample(frame):
                        consume(output)
                for output in resampler.resample(None):
                    consume(output)
            count = max(1, count)
            spool.truncate(count * 4)
            spool.flush()
            samples = np.memmap(spool, dtype='<f4', mode='r', shape=(count,))
            # One immutable whole-song amplitude reference. This deliberately
            # is NOT column/tile auto gain. Silence still has reference 1.
            return cls(spool, samples, duration, peak if peak >= NEAR_SILENCE_PEAK else 1., str(path))
        except BaseException:
            spool.close()
            raise

    def close(self):
        if isinstance(self.samples, np.memmap):
            self.samples._mmap.close()
        if self.file is not None:
            self.file.close()


class _ChunkSamples:
    """Absolute-sample indexed PCM backed by bounded RAM and a compact spool."""
    def __init__(self, owner):
        self.owner = owner
    def __len__(self):
        return max(1, math.ceil(self.owner.duration * RATE))
    def __getitem__(self, key):
        if isinstance(key, slice):
            indices = np.arange(*key.indices(len(self)), dtype=np.int64)
        else:
            indices = np.asarray(key, dtype=np.int64)
        out = np.zeros(indices.shape, dtype=np.float32)
        blocks = indices // self.owner.block_samples
        for block in np.unique(blocks):
            mask = blocks == block
            pcm = self.owner.block(int(block))
            offset = indices[mask] - int(block) * self.owner.block_samples
            valid = (offset >= 0) & (offset < len(pcm))
            part = np.zeros(len(offset), dtype=np.float32)
            part[valid] = pcm[offset[valid]]
            out[mask] = part
        return out


class ProgressiveAudioSource(AudioSource):
    """Seek and decode the requested 8-second PCM blocks before any global scan.

    The spool is append-only in *decoded block order*, not media-offset order,
    so viewing an hour into a long file cannot allocate an hour of zeroes.
    Global sample indices/PTS are retained. No tile-local auto normalization.
    A fixed 0 dBFS provisional reference is replaced only after global scan.
    """
    block_samples = RATE * 8
    def __init__(self, path, duration, cancel=lambda: False):
        if not math.isfinite(duration) or not 0 < duration <= 86400:
            raise ValueError('Spectrum supports media up to 24 hours')
        super().__init__(tempfile.TemporaryFile(prefix='lyria-spectrum-'), None, duration, 1., str(path))
        self.samples = _ChunkSamples(self)
        self.cancel = cancel
        self.locations = {}
        self.memory = OrderedDict()
        self.memory_bytes = 0
        self.peak = 0.
        self.complete = False
        self.decode_seconds = 0.
    def block(self, number):
        if self.cancel():
            raise InterruptedError('Spectrum viewport changed')
        if number < 0 or number * self.block_samples >= len(self.samples):
            return np.zeros(self.block_samples, dtype=np.float32)
        if number in self.memory:
            self.memory.move_to_end(number)
            return self.memory[number]
        if number in self.locations:
            offset, size = self.locations[number]
            self.file.seek(offset)
            pcm = np.frombuffer(self.file.read(size), dtype='<f4')
        else:
            pcm = self._decode_block(number)
            # Only completed blocks are published; interrupted reads are retried.
            self.file.seek(0, 2)
            self.locations[number] = (self.file.tell(), pcm.nbytes)
            self.file.write(pcm.tobytes())
            self.peak = max(self.peak, float(np.max(np.abs(pcm))))
        while self.memory and self.memory_bytes + pcm.nbytes > 32 * 1024 * 1024:
            _, old = self.memory.popitem(last=False)
            self.memory_bytes -= old.nbytes
        self.memory[number] = pcm
        self.memory_bytes += pcm.nbytes
        return pcm
    def _decode_block(self, number):
        import av
        started = time.perf_counter()
        start = number * self.block_samples
        stop = min(len(self.samples), start + self.block_samples)
        pcm = np.zeros(stop - start, dtype='<f4')
        with av.open(self.path) as container:
            if not container.streams.audio:
                raise ValueError('Media has no audio stream')
            stream = container.streams.audio[0]
            origin = (float(container.start_time / av.time_base) if container.start_time is not None
                      else float((stream.start_time or 0) * stream.time_base))
            # Decode pre-roll for codec/resampler state; output PTS, not seek
            # destination, determines placement. Never shift a late stream.
            seek_seconds = max(0., start / RATE - 2.)
            if seek_seconds:
                container.seek(int((origin + seek_seconds) / stream.time_base), stream=stream, backward=True)
            resampler = av.AudioResampler(format='fltp', layout='mono', rate=RATE)
            reached = False
            def consume(frame):
                nonlocal reached
                if frame.pts is None:
                    raise ValueError('Seeked spectrum audio requires timestamped samples')
                offset = round((float(frame.pts * frame.time_base) - origin) * RATE)
                values = np.nan_to_num(frame.to_ndarray().reshape(-1).astype('<f4'), copy=False, nan=0., posinf=0., neginf=0.)
                lo, hi = max(start, offset), min(stop, offset + len(values))
                if hi > lo:
                    pcm[lo-start:hi-start] = values[lo-offset:hi-offset]
                reached = offset + len(values) >= stop
            first_input = True
            for frame in container.decode(stream):
                if first_input and seek_seconds and frame.pts is not None:
                    # libswresample starts a new polyphase filter at seek. Align
                    # its first input to a common integer source/output grid,
                    # otherwise 48k->22.05k seeks introduce fractional-sample drift.
                    rate = frame.sample_rate
                    quantum = rate // math.gcd(rate, RATE)
                    absolute = round((float(frame.pts * frame.time_base) - origin) * rate)
                    trim = (-absolute) % quantum
                    if trim >= frame.samples:
                        continue
                    if trim:
                        values = frame.to_ndarray()
                        stride = 1 if frame.format.is_planar else len(frame.layout.channels)
                        cut = av.AudioFrame.from_ndarray(np.ascontiguousarray(values[:, trim * stride:]), format=frame.format.name, layout=frame.layout.name)
                        cut.sample_rate = rate
                        cut.time_base = frame.time_base
                        cut.pts = frame.pts + round(trim / rate / frame.time_base)
                        frame = cut
                first_input = False
                if self.cancel():
                    raise InterruptedError('Spectrum viewport changed')
                for output in resampler.resample(frame):
                    consume(output)
                if reached:
                    break
            if not reached:
                for output in resampler.resample(None):
                    consume(output)
        self.decode_seconds += time.perf_counter() - started
        return pcm
    def prepare_global(self):
        for block in range(math.ceil(len(self.samples) / self.block_samples)):
            self.block(block)
            # Idle work yields between bounded chunks; a viewport supersedes it.
            time.sleep(.001)
        self.reference = self.peak if self.peak >= NEAR_SILENCE_PEAK else 1.
        self.complete = True
    def close(self):
        self.memory.clear()
        self.file.close()


def stft_columns(source, seconds, window=WINDOW, fft=None):
    """One independently centred Hann-window FFT per requested physical column.

    Fractional-sample centres use linear interpolation, avoiding repeated columns
    even at sub-sample zoom. At 2..5ms/column the 512/22050s window overlaps by
    78.5..91.4%; 87.5% is not claimed for the whole interval.
    """
    times = np.asarray(seconds, dtype=np.float64)
    fft = fft_samples(window) if fft is None else fft
    if window not in (256, 384, 512, 768, 1024) or fft not in (1024, 2048) or fft < window:
        raise ValueError('不支持的频谱分析窗口参数')
    taper = np.hanning(window).astype(np.float32)
    output = np.empty((len(times), fft // 2 + 1), dtype=np.float32)
    offsets = np.arange(window) - window / 2
    n = len(source.samples)
    for start in range(0, len(times), 64):
        positions = times[start:start + 64, None] * RATE + offsets
        indices = np.floor(positions).astype(np.int64)
        fraction = (positions - indices).astype(np.float32)
        a = np.asarray(source.samples[np.clip(indices, 0, n - 1)])
        b = np.asarray(source.samples[np.clip(indices + 1, 0, n - 1)])
        a = np.where((indices >= 0) & (indices < n), a, 0.)
        b = np.where((indices + 1 >= 0) & (indices + 1 < n), b, 0.)
        values = (a * (1 - fraction) + b * fraction) * taper
        output[start:start + len(values)] = np.abs(np.fft.rfft(values, n=fft, axis=1)) * (2 / taper.sum())
    return output


@dataclass(frozen=True)
class Calibration:
    reference: float
    sampled_quantile: float
    centres: int
    fallback: str
    version: int = SPECTRUM_VERSION
    active_centres: int = 0
    policy: str = "whole_source"


def global_calibration(source, window=WINDOW, cancel=lambda: False, *, active_only=False):
    """Approximate whole-source q99.5, never screen/column/tile normalization.

    Uniform time strata cover the entire declared media duration, including
    gaps/silence, with 200 <= f < 4000 Hz equally weighted linear FFT bins.
    At most 16,384 centres (nominal 100 ms) and a 4,096-bin log histogram bound
    memory/work even for long video. Histogram resolution is ~0.0323 dB; sparse
    events can still be missed by time sampling. This is not an exact quantile.
    Calibration is immutable for a decoded source/window/version and is only
    published after completion. GUI callers must execute this in their worker.
    """
    fft = fft_samples(window)
    key = (SPECTRUM_VERSION, RATE, window, fft, CALIBRATION_CENTRES, bool(active_only))
    if key in source.calibrations:
        return source.calibrations[key]
    count = max(1, min(CALIBRATION_CENTRES, math.ceil(source.duration * 10)))
    histogram = np.zeros(4096, dtype=np.int64)
    frequencies = np.fft.rfftfreq(fft, 1 / RATE)
    band = (frequencies >= 200) & (frequencies < 4000)
    reference = max(NEAR_SILENCE_PEAK, source.reference)
    active_count = 0
    for start in range(0, count, 64):
        if cancel():
            raise InterruptedError('频谱校准已取消')
        centres = (np.arange(start, min(start + 64, count)) + .5) / count * source.duration
        values = stft_columns(source, centres, window=window)[:, band]
        if active_only:
            # RMS of actual local PCM, not weighted display brightness or VAD.
            offsets=np.arange(window)-window//2
            positions=(centres[:,None]*RATE).astype(np.int64)+offsets
            valid=(positions>=0)&(positions<len(source.samples))
            pcm=np.where(valid,source.samples[np.clip(positions,0,len(source.samples)-1)],0.)
            active=np.sqrt(np.mean(pcm.astype(np.float64)**2,axis=1))>max(1e-4,source.reference*10**(-50/20))
            values=values[active]
        active_count += len(values)
        db = 20 * np.log10(np.maximum(values / reference, 1e-12))
        bins = np.clip(np.floor((db + 120) / 132 * 4096), 0, 4095).astype(np.int32)
        histogram += np.bincount(bins.ravel(), minlength=4096)
    if cancel():
        raise InterruptedError('频谱校准已取消')
    if not histogram.sum():
        result=Calibration(reference,0.,count,'no_active_frames',active_centres=0,policy='active_pcm_rms')
        source.calibrations[key]=result
        return result
    index = int(np.searchsorted(histogram.cumsum(), math.ceil(histogram.sum() * .995)))
    quantile = reference * 10 ** ((-120 + (index + .5) * 132 / 4096) / 20)
    # A tiny/sparse quantile must not raise silence/noise to full brightness.
    # Limit normalization to 24 dB versus the immutable full-source peak.
    fallback = 'near_silence_or_sparse' if quantile < NEAR_SILENCE_PEAK else ''
    chosen = reference if fallback else max(quantile, reference * 10 ** (-24 / 20))
    if not fallback and chosen > quantile:
        fallback = 'gain_limited_24db'
    result = Calibration(chosen, quantile, count, fallback, active_centres=active_count, policy="active_pcm_rms" if active_only else "whole_source")
    source.calibrations[key] = result
    return result


def frequency_position(y, max_frequency=MAX_FREQUENCY, axis='legacy'):
    """Descending Hz at normalized y. No assumption that a band is voice-only."""
    y = np.asarray(y, dtype=np.float64)
    maximum = float(max_frequency)
    if maximum not in (5500., 8000., 10000., 11025.):
        raise ValueError('Unsupported spectrum frequency range')
    if axis == 'linear':
        return (1 - y) * maximum
    if axis not in ('legacy', 'speech', 'balanced'):
        raise ValueError('Unsupported spectrum frequency axis')
    upper = .46 if axis == 'balanced' else .25 if axis == 'speech' else .32
    return np.where(y <= upper, maximum - y / upper * (maximum - 4000),
                    (1 - y) / (1 - upper) * 4000)


def frequency_rows(height, max_frequency=MAX_FREQUENCY, axis='legacy'):
    return frequency_position((np.arange(height, dtype=np.float64) + .5) / max(1, height),
                              max_frequency, axis)


def frequency_row_values(magnitudes, height, max_frequency=MAX_FREQUENCY, axis='legacy'):
    """Project FFT magnitudes into pixels without dropping narrow ridges.

    At low panel heights one pixel covers several FFT bins, especially above
    4 kHz. Centre-only interpolation can miss a complete harmonic. Take the
    maximum of *all* bin centres inside each pixel's frequency interval and
    the interpolated interval edges. For sub-bin pixels interpolate at the
    centre instead; magnification does not invent extra frequency detail.

    This is a display peak envelope, not integrated band energy, frequency
    weighting, vocal detection, or a modification to the audio/FFT cache.
    Columns remain independent, so reduction never broadens an onset in time.
    """
    height = max(1, int(height))
    fft = (magnitudes.shape[1] - 1) * 2
    centres = frequency_rows(height, max_frequency, axis) * fft / RATE
    y = np.arange(height + 1, dtype=np.float64) / height
    edges = frequency_position(y, max_frequency, axis) * fft / RATE

    def interpolate(bins):
        bins = np.clip(bins, 0, magnitudes.shape[1] - 1)
        low = np.floor(bins).astype(int)
        high = np.minimum(low + 1, magnitudes.shape[1] - 1)
        fraction = bins - low
        return magnitudes[:, low] * (1 - fraction) + magnitudes[:, high] * fraction

    values = interpolate(centres)
    reduced = np.flatnonzero(edges[:-1] - edges[1:] > 1.)
    if not len(reduced):
        return values
    boundaries = interpolate(edges)
    for row in reduced:
        start = max(0, math.ceil(edges[row + 1]))
        stop = min(magnitudes.shape[1], math.floor(edges[row]) + 1)
        peak = np.maximum(boundaries[:, row], boundaries[:, row + 1])
        if stop > start:
            peak = np.maximum(peak, magnitudes[:, start:stop].max(axis=1))
        values[:, row] = np.maximum(values[:, row], peak)
    return values


def palette(purple=False):
    positions = [0, .22, .50, .78, 1]
    colours = ([[0, 0, 0], [20, 8, 40], [111, 39, 218], [228, 128, 231], [245, 232, 255]]
               if purple else [[0, 0, 0], [4, 14, 42], [0, 148, 198], [130, 255, 209], [255, 255, 255]])
    x = np.linspace(0, 1, 256)
    return np.stack([np.interp(x, positions, np.asarray(colours)[:, i]) for i in range(3)], axis=1).astype(np.uint8)


def perceptual_lightness(rgb):
    """CIE L* from sRGB/D65, for palette validation and controlled comparisons."""
    srgb = np.asarray(rgb, dtype=np.float64) / 255.
    linear = np.where(srgb <= .04045, srgb / 12.92, ((srgb + .055) / 1.055) ** 2.4)
    y = linear @ np.array([.2126, .7152, .0722])
    return np.where(y > (6/29)**3, 116 * np.cbrt(y) - 16, (29/3)**3 * y)


def visual_frequency_weights(bin_count, tilt_db_oct=0.):
    """Static perceptual display EQ, never source separation or PCM processing.

    Below 100Hz a smooth -12dB shelf rolloff; 110Hz..3.5kHz slope;
    capped high-frequency gain above 3.5kHz, gently reduced after 7kHz.
    Zero disables the entire weighting for an equal-dBFS comparison.
    """
    if not tilt_db_oct:return np.ones(bin_count)
    f=np.linspace(0.,RATE/2,bin_count)
    tilt=float(tilt_db_oct)*np.log2(np.clip(f,110.,3500.)/1000.)
    low=-12./(1.+(f/100.)**4)
    high=-2.*np.maximum(0.,np.log2(np.maximum(f,7000.)/7000.))
    return 10.**((tilt+low+high)/20.)


def brightness_transfer(normalized, gain_db=0., gamma=2., log_scale=9., knee_db=12.):
    """Dark-background log curve with a differentiable, asymptotic 12dB knee.

    Below -25dB, gamma>=1.8 gives <8% intensity; above the knee the
    derivative remains positive rather than clipping strong bass to a plateau.
    """
    x=np.maximum(normalized,0.)*10.**(gain_db/20.)
    raw=(np.log1p(log_scale*x)/np.log1p(log_scale))**gamma
    knee=(np.log1p(log_scale*10.**(-knee_db/20.))/np.log1p(log_scale))**gamma
    return np.where(raw<=knee,raw,knee+(1-knee)*np.tanh((raw-knee)/(1-knee)))


def colourize(magnitudes, reference, height, gain_db=0., gamma=1.32, purple=False,
              floor_db=-65., ceiling_db=-5., *, mapping='db', log_scale=100.,
              black_level=0., tilt_db_oct=0., max_frequency=MAX_FREQUENCY, axis='legacy'):
    # Weight bins before peak projection; the immutable FFT cache is untouched.
    weighted = (magnitudes * visual_frequency_weights(magnitudes.shape[1], tilt_db_oct)
                if tilt_db_oct else magnitudes)
    values = frequency_row_values(weighted, height, max_frequency, axis)
    normalized = values / max(1e-12, reference)
    if mapping == 'soft_knee':
        intensity = brightness_transfer(normalized,gain_db,gamma,log_scale)
    elif mapping == 'soft_log':
        if not math.isfinite(log_scale) or log_scale <= 0:
            raise ValueError('Invalid logarithmic scale')
        intensity = np.log1p(np.maximum(normalized, 0.) * log_scale * 10 ** (gain_db / 20.)) / np.log1p(log_scale)
        intensity = np.clip(intensity, 0, 1) ** gamma
    elif mapping == 'db':
        db = 20 * np.log10(np.maximum(normalized, 1e-12)) + gain_db
        if not math.isfinite(floor_db) or not math.isfinite(ceiling_db) or ceiling_db <= floor_db:
            raise ValueError('频谱显示范围无效')
        intensity = np.clip((db - floor_db) / (ceiling_db - floor_db), 0, 1) ** gamma
    else:
        raise ValueError('Invalid spectrum brightness mapping')
    if not math.isfinite(black_level) or not 0 <= black_level <= .3:
        raise ValueError('Invalid spectrum black level')
    intensity = np.clip((intensity - black_level) / (1 - black_level), 0, 1)
    return np.ascontiguousarray(palette(purple)[np.rint(intensity * 255).astype(np.uint8)].transpose(1, 0, 2))


def time_grid_key(seconds):
    """Fingerprint the actual time mapping, not just its nominal frame step.

    A VFR index/controller can change column timestamps without changing the
    source or viewport coordinates. Such requests must not reuse old pixels.
    """
    times = np.ascontiguousarray(seconds, dtype='<f8')
    return (times.shape, hashlib.blake2b(times.tobytes(), digest_size=16).digest())


class RasterTileCache:
    """Separate bounded display cache; no PCM/FFT recompute on a raster hit.

    Callers include exact timestamps and *all* display parameters in the key.
    Source identity is checked separately. Oversized tiles are returned without
    caching, so a high-DPI panel cannot exceed the configured cache bound.
    """
    def __init__(self, max_bytes=16 * 1024 * 1024):
        self.max_bytes = max(0, int(max_bytes))
        self.bytes = 0
        self.tiles = OrderedDict()
        self.source = None
        self.computations = 0
        self.hits = 0

    def get(self, source, key, render, cancel=lambda: False):
        if cancel():
            raise InterruptedError('Spectrum viewport changed')
        if source is not self.source:
            self.tiles.clear()
            self.bytes = 0
            self.source = source
        if key in self.tiles:
            self.hits += 1
            self.tiles.move_to_end(key)
            return self.tiles[key]
        rgb = np.ascontiguousarray(render(), dtype=np.uint8)
        if cancel():
            raise InterruptedError('Spectrum viewport changed')
        self.computations += 1
        if rgb.nbytes <= self.max_bytes:
            while self.bytes + rgb.nbytes > self.max_bytes and self.tiles:
                _, old = self.tiles.popitem(last=False)
                self.bytes -= old.nbytes
            rgb.setflags(write=False)
            self.tiles[key] = rgb
            self.bytes += rgb.nbytes
        return rgb

class TileCache:
    """LRU bounded by bytes, including FFT cache across height-only resizes."""
    def __init__(self, max_bytes=32 * 1024 * 1024):
        self.max_bytes = max_bytes
        self.bytes = 0
        self.tiles = OrderedDict()
        self.computations = 0
        self.hits = 0
        self.source = None

    def get(self, source, key, seconds, cancel=lambda: False, window=WINDOW):
        if source is not self.source:
            self.tiles.clear()
            self.bytes = 0
            self.source = source
        key = (SPECTRUM_VERSION, window, fft_samples(window), key, time_grid_key(seconds))
        if key in self.tiles:
            self.hits += 1
            self.tiles.move_to_end(key)
            return self.tiles[key]
        if cancel():
            raise InterruptedError('频谱视图已更新')
        values = stft_columns(source, seconds, window=window)
        if cancel():
            raise InterruptedError('频谱视图已更新')
        self.computations += 1
        self.tiles[key] = values
        self.bytes += values.nbytes
        while self.bytes > self.max_bytes and len(self.tiles) > 1:
            _, removed = self.tiles.popitem(last=False)
            self.bytes -= removed.nbytes
        return values


class LodTileCache:
    """64-sample base STFT and power-of-two max-pool pyramid, bounded LRU.

    Coarse levels are reductions of ALL fine-grid FFTs, not separate sparse
    STFTs. Every retained ancestor summarizes every underlying sample centre.
    A budget eviction may require recomputation; no unbounded 100% claim.
    """
    BASE_HOP=64
    def __init__(self,max_bytes=32*1024*1024):
        self.max_bytes=max_bytes;self.tiles=OrderedDict();self.bytes=0
        self.computations=0;self.hits=0;self.source=None;self.last_hop=64
    @staticmethod
    def choose_hop(seconds):
        spacing=float(np.min(np.diff(seconds)))*RATE if len(seconds)>1 else 64.
        return 64*2**max(0,min(20,math.floor(math.log2(max(64.,spacing)/64.))))
    def _block(self,source,window,level,index,cancel):
        if cancel():raise InterruptedError('Spectrum viewport changed')
        key=(window,fft_samples(window),level,index)
        if key in self.tiles:
            self.hits+=1;self.tiles.move_to_end(key);return self.tiles[key]
        if level==0:
            times=(np.arange(index*TILE_WIDTH,(index+1)*TILE_WIDTH)+.5)*64/RATE
            values=stft_columns(source,times,window);self.computations+=1
        else:
            # Copy reductions before recursively fetching can evict children.
            left=self._block(source,window,level-1,index*2,cancel).reshape(128,2,-1).max(axis=1)
            right=self._block(source,window,level-1,index*2+1,cancel).reshape(128,2,-1).max(axis=1)
            values=np.concatenate((left,right))
        if cancel():raise InterruptedError('Spectrum viewport changed')
        while self.tiles and self.bytes+values.nbytes>self.max_bytes:
            _,old=self.tiles.popitem(last=False);self.bytes-=old.nbytes
        if values.nbytes<=self.max_bytes:self.tiles[key]=values;self.bytes+=values.nbytes
        return values
    def get(self,source,key,seconds,cancel=lambda:False,window=WINDOW):
        times=np.asarray(seconds,dtype=np.float64)
        if not len(times) or not np.isfinite(times).all() or (len(times)>1 and np.any(np.diff(times)<=0)):
            raise ValueError('Invalid spectrum sampling times')
        if self.source is not source:self.source=source;self.tiles.clear();self.bytes=0
        hop=self.choose_hop(times);self.last_hop=hop;level=int(math.log2(hop/64))
        def rows(indices):
            out=np.empty((len(indices),fft_samples(window)//2+1),dtype=np.float32)
            blocks=np.floor_divide(indices,TILE_WIDTH)
            for number in np.unique(blocks):
                mask=blocks==number
                out[mask]=self._block(source,window,level,int(number),cancel)[indices[mask]-number*TILE_WIDTH]
            return out
        pos=times*RATE/hop-.5;lo=np.floor(pos).astype(np.int64);f=(pos-lo).astype(np.float32)[:,None]
        values=rows(lo)*(1-f)+rows(lo+1)*f
        if len(times)<2:return values
        edges=np.r_[times[0]-(times[1]-times[0])/2,(times[:-1]+times[1:])/2,times[-1]+(times[-1]-times[-2])/2]
        # Include each overlapping pooled cell; unlike point decimation, a short
        # onset cannot disappear between screen columns. Width is an envelope.
        # Gather pooled cells in one NumPy batch instead of re-entering the
        # Python cache traversal once per screen column (256 calls per tile).
        wide = np.flatnonzero(np.diff(edges) >= 64 / RATE)
        if len(wide):
            starts = np.floor(edges[wide] * RATE / hop).astype(np.int64)
            stops = np.ceil(edges[wide + 1] * RATE / hop).astype(np.int64)
            lengths = stops - starts
            offsets = np.r_[0, np.cumsum(lengths)]
            indices = np.repeat(starts, lengths) + np.arange(offsets[-1]) - np.repeat(offsets[:-1], lengths)
            pooled = np.maximum.reduceat(rows(indices), offsets[:-1], axis=0)
            values[wide] = np.maximum(values[wide], pooled)
        return values


def waveform_rgb(source, seconds, height, cancel=lambda: False):
    """Actual decoded PCM at viewport resolution, never interpolated overview bins.

    Each physical column contains the min/max of samples in its time interval.
    Below one sample per column, interpolate adjacent real samples and connect
    their pixel coverage. This is a 22.05 kHz display, not original-rate PCM.
    Out-of-source positions are silent; no edge-value extrapolation.
    """
    times = np.asarray(seconds, dtype=np.float64)
    height = max(1, int(height))
    if len(times) < 2 or not np.isfinite(times).all() or np.any(np.diff(times) <= 0):
        raise ValueError('波形时间轴范围异常')
    edges = np.empty(len(times) + 1)
    edges[1:-1] = (times[:-1] + times[1:]) / 2
    edges[0] = times[0] - (times[1] - times[0]) / 2
    edges[-1] = times[-1] + (times[-1] - times[-2]) / 2
    n = len(source.samples)
    # Boundary interpolation connects genuine adjacent PCM samples, including
    # narrow columns without an integer sample. Combine with *all* interior
    # samples so a one-sample transient is never replaced by the interpolant.
    positions = edges * RATE
    index = np.floor(positions).astype(np.int64)
    fraction = positions - index
    a = np.where((index >= 0) & (index < n), source.samples[np.clip(index, 0, n - 1)], 0.)
    b = np.where((index + 1 >= 0) & (index + 1 < n), source.samples[np.clip(index + 1, 0, n - 1)], 0.)
    boundary = a * (1 - fraction) + b * fraction
    low = np.minimum(boundary[:-1], boundary[1:])
    high = np.maximum(boundary[:-1], boundary[1:])
    for i in range(len(times)):
        if i % 64 == 0 and cancel():
            raise InterruptedError('波形渲染已取消')
        start = max(0, math.ceil(edges[i] * RATE))
        stop = min(n, math.ceil(edges[i + 1] * RATE))
        if stop > start:
            chunk = source.samples[start:stop]
            low[i] = min(low[i], float(np.min(chunk)))
            high[i] = max(high[i], float(np.max(chunk)))
    top = np.floor((1 - np.clip(high, -1, 1)) * (height - 1) / 2).astype(int)
    bottom = np.ceil((1 - np.clip(low, -1, 1)) * (height - 1) / 2).astype(int)
    rows = np.arange(height)[:, None]
    rgb = np.zeros((height, len(times), 3), dtype=np.uint8)
    rgb[height // 2, :, :] = (52, 68, 86)
    rgb[(rows >= top) & (rows <= bottom)] = (201, 166, 250)
    return rgb
