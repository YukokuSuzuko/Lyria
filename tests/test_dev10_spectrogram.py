"""dev10 cache/timing invariants. Offscreen tests are not native acceptance."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import threading
import time
import numpy as np
import pytest
from lyricsync.spectrogram import AudioSource, RATE, TileCache, RasterTileCache, time_grid_key


def source():
    t = np.arange(RATE) / RATE
    values = (np.sin(2 * np.pi * 1000 * t) * (t > .5)).astype(np.float32)
    return AudioSource(None, values, 1., 1.)


def test_fft_cache_keys_actual_times_not_only_nominal_frame_coordinates():
    pcm = source()
    cache = TileCache()
    a = cache.get(pcm, ('same-step', 0), np.linspace(.1, .2, 256))
    b = cache.get(pcm, ('same-step', 0), np.linspace(.6, .7, 256))
    assert cache.computations == 2
    assert not a.any() and b.max() > .9
    assert cache.get(pcm, ('same-step', 0), np.linspace(.6, .7, 256)) is b


def test_time_grid_key_checks_interior_vfr_times():
    a = np.array([.1, .2, .3])
    b = np.array([.1, .25, .3])
    assert time_grid_key(a) != time_grid_key(b)
    assert time_grid_key(a) == time_grid_key(a.tolist())


def test_raster_cache_bounded_lru_source_and_oversized_bypass():
    cache = RasterTileCache(18)
    pcm = source()
    def render(): return np.zeros((2, 2, 3), dtype=np.uint8)
    a = cache.get(pcm, 'a', render)
    assert cache.get(pcm, 'a', render) is a
    assert cache.computations == 1 and cache.hits == 1
    cache.get(pcm, 'b', render)
    assert list(cache.tiles) == ['b'] and cache.bytes == 12
    cache.get(pcm, 'large', lambda: np.zeros((4, 4, 3), dtype=np.uint8))
    assert list(cache.tiles) == ['b'] and cache.bytes <= 18
    cache.get(source(), 'c', render)
    assert list(cache.tiles) == ['c'] and cache.bytes == 12
    assert not a.flags.writeable


def test_cancelled_raster_never_installs_partial_tile():
    cache = RasterTileCache()
    checks = []
    def cancelled():
        checks.append(1)
        return len(checks) > 1
    with pytest.raises(InterruptedError):
        cache.get(source(), 'a', lambda: np.zeros((2, 2, 3)), cancelled)
    assert cache.bytes == 0 and not cache.tiles and cache.computations == 0


def test_worker_reuses_rasters_and_fft_and_invalidates_display_parameters(monkeypatch):
    import desktop_qt.spectrogram_item as native
    monkeypatch.setattr(native.AudioSource, 'decode', lambda *a: source())
    condition = threading.Condition()
    results = {}
    def receive(seq, image, offset, metrics, error):
        with condition:
            results[seq] = (image, metrics, error)
            condition.notify_all()
    worker = native._Worker(receive)
    def request(seq, height=80, gain=0, window=512, mode='spectrum', path='one', shift=0):
        r = dict(sequence=seq, source=(path, 1., 0, 0), step=.1,
                 width=256, height=height, offset=0, gain=gain, preset='detail',
                 window=window, secondsPerColumn=.001, mode=mode,
                 tiles=[(i, np.linspace(.1, .2, 256) + i*.2 + shift) for i in range(3)])
        worker.request(r)
        with condition:
            assert condition.wait_for(lambda: seq in results, timeout=5)
        image, metrics, error = results[seq]
        assert not error and not image.isNull()
        return metrics
    try:
        first = request(1)
        same = request(2)
        assert same['rasterTiles'] == first['rasterTiles'] == 1
        assert same['rasterCacheHits'] == 1
        taller = request(3, height=100)
        assert taller['rasterTiles'] == 2
        # Prefetch may finish after delivery, so measure FFT reuse after the
        # repeat has reached a stable cache, not from the first delivery.
        stable = request(4, height=100)
        original = request(5)
        assert original['rasterTiles'] == 2
        assert original['fftTiles'] == stable['fftTiles']
        gain = request(6, gain=3)
        assert gain['rasterTiles'] == 3
        assert gain['fftTiles'] == original['fftTiles']
        longer = request(7, window=1024)
        assert longer['rasterTiles'] == 4
        assert longer['fftTiles'] > gain['fftTiles']
        changed_grid = request(8, shift=.1)
        assert changed_grid['rasterTiles'] == 5
        new_source = request(9, path='two')
        assert new_source['rasterTiles'] == 1 and new_source['rasterCacheHits'] == 0
        wave = request(10, path='two', mode='waveform')
        wave_same = request(11, path='two', mode='waveform')
        assert wave_same['rasterTiles'] == wave['rasterTiles']
        assert wave_same['rasterCacheHits'] == wave['rasterCacheHits'] + 1
    finally:
        worker.stop()
        worker.thread.join(5)
    assert not worker.thread.is_alive()


def test_same_source_controls_keep_time_mapped_image_but_source_switch_clears():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QImage
    from desktop_qt.spectrogram_item import SpectrogramItem
    app = QApplication.instance() or QApplication([])
    item = SpectrogramItem()
    item._path = 'same'
    item._duration = 1.
    item._active = True
    def install():
        item._image = QImage(4, 4, QImage.Format.Format_RGB888)
        item._image.fill(0xff55aa)
        item._image_signature = item._signature()
        item._metrics = dict(imageFrameStep=item._span/max(1,item.width()))
    try:
        install()
        item._gain = 3
        item._schedule()
        assert not item._image.isNull() and item._status == 'updating'
        item._preset = 'balanced'
        item._schedule()
        assert not item._image.isNull()
        item._first = 100
        item._span = 200
        item._schedule()
        assert item._image.isNull()  # v0.22: never stretch an old zoom raster
        for attr, value in [('_source_revision', 1), ('_source_offset', .5),
                            ('_mode', 'waveform'), ('_path', 'new')]:
            install()
            setattr(item, attr, value)
            item._schedule()
            assert item._image.isNull(), attr
    finally:
        item._timer.stop()
        item.deleteLater()