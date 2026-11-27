"""Native Qt spectrum surface: GUI paints only; decode/FFT/LUT are background.

A latest-only request slot prevents resize/pan queues growing. A single worker
owns its PCM spool, 32 MiB FFT LRU and 16 MiB raster LRU. Current viewport
tiles precede adjacent tiles.
The QImage transfer is implicit-shared native memory, never a QML nested array.
"""
from __future__ import annotations
import math
import os
from collections import OrderedDict
import threading
import time
import weakref

import numpy as np
from PySide6.QtCore import Property, QRectF, Signal, Slot, QTimer, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtQuick import QQuickPaintedItem

from lyricsync.spectrogram import RATE, fft_samples, AudioSource, ProgressiveAudioSource, TileCache, LodTileCache, RasterTileCache, time_grid_key, TILE_WIDTH, colourize, global_calibration, DISPLAY_PRESETS, SPECTRUM_VERSION, waveform_rgb

_workers = weakref.WeakSet()


class _Worker:
    def __init__(self, deliver, controller=None):
        self.deliver = deliver
        self.controller_ref = weakref.ref(controller) if controller is not None else lambda: None
        self.condition = threading.Condition()
        self.latest = None
        self.closed = False
        self.thread = threading.Thread(target=self.run, name='Lyria-spectrum', daemon=True)
        _workers.add(self)
        self.thread.start()

    def request(self, request):
        with self.condition:
            self.latest = request
            self.condition.notify()

    def stop(self):
        with self.condition:
            self.closed = True
            self.condition.notify()

    def stale(self, request, source_only=False):
        with self.condition:
            return self.closed or (self.latest is not None and
                (self.latest.get('inactive', False) or self.latest['source'] != request['source'] if source_only else self.latest['sequence'] != request['sequence']))

    def run(self):
        source = None
        source_key = None
        cache = LodTileCache()
        raster_cache = RasterTileCache()
        viewport_spectra=OrderedDict();viewport_bytes=0
        seen = -1
        failed_source = None
        failed_error = ''
        try:
            while True:
                with self.condition:
                    while not self.closed and (self.latest is None or (self.latest['sequence'], self.latest.get('pending', False)) == seen):
                        self.condition.wait()
                    if self.closed:
                        return
                    request = self.latest
                    seen = (request['sequence'], request.get('pending', False))
                started = time.perf_counter()
                if request.get('inactive') or request.get('pending'):
                    continue
                try:
                    if source_key != request['source']:
                        if source:
                            source.close()
                        source = None
                        source_key = None
                        cache = LodTileCache()
                        raster_cache = RasterTileCache()
                        viewport_spectra.clear();viewport_bytes=0
                        if not request['source'][0]:
                            continue
                        if failed_source == request['source']:
                            raise ValueError(failed_error)
                        source = (ProgressiveAudioSource(request['source'][0], request['source'][1], lambda: self.stale(request))
                                  if request.get('progressiveDecode') else
                                  AudioSource.decode(request['source'][0], request['source'][1], lambda: self.stale(request, True)))
                        source_key = request['source']
                    if self.stale(request):
                        continue
                    if isinstance(source, ProgressiveAudioSource):
                        source.cancel = lambda: self.stale(request)
                    provisional = isinstance(source, ProgressiveAudioSource) and not source.complete
                    preset = DISPLAY_PRESETS[request['preset']]
                    calibration = (global_calibration(source, request['window'], lambda: self.stale(request), active_only=request.get('activeCalibration',False))
                                   if not provisional and preset['quantile'] and request.get('mode', 'spectrum') == 'spectrum' else None)
                    reference = calibration.reference if calibration else source.reference
                    if self.stale(request):
                        continue
                    tiles = []
                    last_partial = 0.
                    # The inner tiles are visible; first/last are prefetch only.
                    for tile_number, (index, seconds) in enumerate(request['tiles'][1:-1]):
                        key = (request['step'], index)
                        mode = request.get('mode', 'spectrum')
                        raster_key = (SPECTRUM_VERSION, key, time_grid_key(seconds), mode,
                                      request['window'], request['height'], request['gain'],
                                      reference, preset['gamma'], preset['floor_db'], preset['ceiling_db'],
                                      preset.get('mapping','db'), preset.get('log_scale',100.),
                                      request.get('black',0.), request.get('tilt',0.),
                                      request.get('frequency',10000), request.get('axis','legacy'))
                        def render():
                            if mode == 'waveform':
                                return waveform_rgb(source, seconds, request['height'], lambda: self.stale(request))
                            nonlocal viewport_bytes
                            spectrum_key=(request['window'],time_grid_key(seconds))
                            if spectrum_key in viewport_spectra:
                                values=viewport_spectra[spectrum_key];viewport_spectra.move_to_end(spectrum_key)
                            else:
                                values = cache.get(source, key, seconds, lambda: self.stale(request), window=request['window'])
                                while viewport_spectra and viewport_bytes+values.nbytes>32*1024*1024:
                                    _,old=viewport_spectra.popitem(last=False);viewport_bytes-=old.nbytes
                                if values.nbytes<=32*1024*1024:
                                    viewport_spectra[spectrum_key]=values;viewport_bytes+=values.nbytes
                            return colourize(values, reference, request['height'], request['gain'], preset['gamma'], purple=True, floor_db=preset['floor_db'], ceiling_db=preset['ceiling_db'],
                                             mapping=preset.get('mapping','db'), log_scale=preset.get('log_scale',100.),
                                             black_level=request.get('black',0.), tilt_db_oct=request.get('tilt',0.),
                                             max_frequency=request.get('frequency',10000), axis=request.get('axis','legacy'))
                        tiles.append(raster_cache.get(source, raster_key, render, lambda: self.stale(request)))
                        # Send only the finished tile, not a huge transparent
                        # full-viewport image. GUI overlays it at its real frame
                        # coordinates above the previous completed viewport.
                        now = time.perf_counter()
                        if (request.get('progressive') and not self.stale(request)
                                and (not tile_number or now - last_partial >= .025)):
                            pixels = tiles[-1]
                            patch = QImage(pixels.data, pixels.shape[1], pixels.shape[0],
                                           pixels.strides[0], QImage.Format.Format_RGB888).copy()
                            self.deliver(request['sequence'], patch, 0.,
                                dict(partial=True, renderComplete=False, calibrationPending=provisional,
                                     completedTiles=tile_number+1, totalTiles=len(request['tiles'])-2,
                                     imageFirstFrame=request.get('imageFirstFrame', 1.) + tile_number*TILE_WIDTH*request['step'],
                                     imageFrameStep=request['step'], imageSource=request['source'],
                                     imageSignature=request.get('signature')), '')
                            last_partial = now
                    rgb = np.ascontiguousarray(np.concatenate(tiles, axis=1))
                    image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()
                    metrics = dict(renderComplete=True, calibrationPending=provisional,
                                   decodedBlocks=len(source.locations) if isinstance(source, ProgressiveAudioSource) else 0,
                                   decodeSeconds=getattr(source, 'decode_seconds', 0.), renderSeconds=time.perf_counter()-started, cacheBytes=cache.bytes,
                                   fftTiles=cache.computations, cacheHits=cache.hits, viewportSpectrumBytes=viewport_bytes,
                                   rasterCacheBytes=raster_cache.bytes, rasterTiles=raster_cache.computations,
                                   rasterCacheHits=raster_cache.hits,
                                   physicalColumns=request['width'], secondsPerColumn=request['secondsPerColumn'],
                                   sampleRate=22050, windowSamples=request['window'], fftSamples=fft_samples(request['window']),
                                   frequencyReduction='pixel_band_peak' if request.get('mode', 'spectrum') == 'spectrum' else 'not_applicable',
                                   frequencyWeighting='visual_tilt_only', tiltDbPerOctave=request.get('tilt',0.),
                                   brightnessMapping=preset.get('mapping','db'), logScale=preset.get('log_scale',100.),
                                   blackLevel=request.get('black',0.), frequencyMaximum=request.get('frequency',10000),
                                   frequencyAxis=request.get('axis','legacy'), fixedHopSamples=cache.last_hop,
                                   timeReduction='fixed_grid_interpolation_and_peak_pool', paletteVersion='lilac_soft_highlight_v2',
                                   displayPreset=request['preset'], gainDb=request['gain'],
                                   referenceAmplitude=reference, referenceKind=('active_pcm_sampled_q995' if request.get('activeCalibration') else 'whole_source_sampled_q995') if calibration else ('provisional_fixed_0dbfs' if provisional else 'whole_source_peak'),
                                   calibrationCentres=calibration.centres if calibration else 0,
                                   calibrationActiveCentres=calibration.active_centres if calibration else 0,
                                   calibrationFallback=calibration.fallback if calibration else '',
                                   spectrumVersion=SPECTRUM_VERSION, displayMode=request.get('mode', 'spectrum'),
                                   imageFirstFrame=request.get('imageFirstFrame', 1.), imageFrameStep=request['step'],
                                   imageSource=request['source'], imageSignature=request.get('signature'))
                    if not self.stale(request):
                        self.deliver(request['sequence'], image, request['offset'], metrics, '')
                    if provisional and not self.stale(request):
                        source.prepare_global()
                        # Revisit the same request once with immutable full-source
                        # calibration. FFT data remain valid; raster key includes reference.
                        seen = -1
                except InterruptedError:
                    continue
                except RuntimeError:
                    if self.closed:
                        return
                    try:
                        self.deliver(request['sequence'], QImage(), 0., {}, '频谱渲染已停止')
                    except RuntimeError:
                        return
                except Exception as exc:
                    if source is None:
                        failed_source = request['source']
                        failed_error = str(exc)
                    try:
                        self.deliver(request['sequence'], QImage(), 0., {}, str(exc))
                    except RuntimeError:
                        return
        finally:
            if source:
                source.close()


def shutdown_workers(timeout=0., controller=None):
    """Cancel only this bridge's workers, then poll without blocking the GUI."""
    workers = [worker for worker in _workers if controller is None or worker.controller_ref() is controller]
    for worker in workers:
        worker.stop()
    for worker in workers:
        worker.thread.join(timeout)
    return all(not worker.thread.is_alive() for worker in workers)


class SpectrogramItem(QQuickPaintedItem):
    changed = Signal()
    statusChanged = Signal()
    _rendered = Signal(int, QImage, float, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._controller = None
        self._path = ''
        self._duration = 0.
        self._source_offset = 0.
        self._source_revision = 0
        self._vocal = False
        self._last_submit = -1.
        self._busy_visible = False
        self._first = 1.
        self._span = 300.
        self._dpr = 1.
        self._gain = 0.
        self._preset = 'detail'
        self._window = 384
        self._black = 0.
        self._tilt = 3.
        self._frequency = 10000
        self._axis = 'balanced'
        self._mode = 'spectrum'
        self._image_signature = None
        self._active = False
        self._sequence = 0
        self._image = QImage()
        self._partial_tiles = []
        self._partial_sequence = None
        self._partial_signature = None
        self._offset = 0.
        self._metrics = {}
        self._status = 'idle'
        self._error = ''
        self._worker = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._submit)
        self._busy_timer = QTimer(self)
        self._busy_timer.setSingleShot(True)
        self._busy_timer.setInterval(100)
        self._busy_timer.timeout.connect(self._show_busy)
        self.widthChanged.connect(self._schedule)
        self.heightChanged.connect(self._schedule_resize)
        self._rendered.connect(self._accept, Qt.ConnectionType.QueuedConnection)
        self.setOpaquePainting(True)

    def _set(self, name, value):
        if name == "_path" and value:value=os.path.normcase(os.path.realpath(value))
        if getattr(self, name) != value:
            setattr(self, name, value)
            self.changed.emit()
            self._schedule()

    controller = Property('QObject*', lambda s: s._controller, lambda s,v: s._set('_controller',v), notify=changed)
    sourcePath = Property(str, lambda s: s._path, lambda s,v: s._set('_path',v), notify=changed)
    sourceRevision = Property(int, lambda s: s._source_revision, lambda s,v: s._set('_source_revision',v), notify=changed)
    durationSeconds = Property(float, lambda s: s._duration, lambda s,v: s._set('_duration',v), notify=changed)
    sourceOffsetSeconds = Property(float, lambda s: s._source_offset, lambda s,v: s._set('_source_offset',v), notify=changed)
    firstFrame = Property(float, lambda s: s._first, lambda s,v: s._set('_first',v), notify=changed)
    frameSpan = Property(float, lambda s: s._span, lambda s,v: s._set('_span',v), notify=changed)
    pixelRatio = Property(float, lambda s: s._dpr, lambda s,v: s._set('_dpr',v), notify=changed)
    gainDb = Property(float, lambda s: s._gain, lambda s,v: s._set('_gain',v), notify=changed)
    displayPreset = Property(str, lambda s: s._preset, lambda s,v: s._set('_preset',v if v in DISPLAY_PRESETS else 'detail'), notify=changed)
    windowSamples = Property(int, lambda s: s._window, lambda s,v: s._set('_window',v if v in (256,384,512,768,1024) else 384), notify=changed)
    blackLevel = Property(float, lambda s: s._black, lambda s,v: s._set('_black',max(0.,min(.3,v))), notify=changed)
    tiltDbPerOctave = Property(float, lambda s: s._tilt, lambda s,v: s._set('_tilt',3. if v else 0.), notify=changed)
    frequencyMaximum = Property(int, lambda s: s._frequency, lambda s,v: s._set('_frequency',v if v in (5500,8000,10000,11025) else 10000), notify=changed)
    frequencyAxis = Property(str, lambda s: s._axis, lambda s,v: s._set('_axis',v if v in ('legacy','speech','linear','balanced') else 'legacy'), notify=changed)
    displayMode = Property(str, lambda s: s._mode, lambda s,v: s._set('_mode', 'waveform' if v == 'waveform' else 'spectrum'), notify=changed)
    active = Property(bool, lambda s: s._active, lambda s,v: s._set('_active',v), notify=changed)
    status = Property(str, lambda s: s._status, notify=statusChanged)
    error = Property(str, lambda s: s._error, notify=statusChanged)
    metrics = Property('QVariantMap', lambda s: s._metrics, notify=statusChanged)

    vocalSource = Property(bool, lambda s:s._vocal, lambda s,v:s._set('_vocal',bool(v)), notify=changed)
    busyVisible = Property(bool, lambda s:s._busy_visible, notify=statusChanged)

    def _show_busy(self):
        self._busy_visible = self._status in ('loading','updating') and self._image.isNull() and not self._partial_tiles
        self.statusChanged.emit()

    def _source_key(self):
        return (self._path, self._duration, self._source_revision, self._source_offset, RATE, SPECTRUM_VERSION, self._vocal)

    def _signature(self):
        return (self._path, self._duration, self._source_revision, self._source_offset, (self._mode,self._vocal), self._gain, self._preset, self._window, self._black, self._tilt, self._frequency, self._axis)

    def _can_reuse_image(self):
        # Display settings may be stale while updating, but source identity,
        # source offset and display mode may never be stale. paint() remaps the
        # old frame extent; it does not stretch it into an unrelated time span.
        return (self._image_signature is not None
                and self._image_signature[:5] == self._signature()[:5]
                and math.isclose(self._metrics.get('imageFrameStep',-1.),self._span/max(1,math.ceil(self.width()*max(1.,min(4.,self._dpr)))),rel_tol=1e-12))

    @Slot()
    def _schedule_resize(self):
        self._schedule()
        # Leading-edge + 16ms coalescing is shared with all interactions.

    @Slot()
    def _schedule(self):
        self._sequence += 1
        # Keep exact-time same-scale previews during same-source pan/height.
        # It is explicitly marked updating, never accepted as final precision.
        if not self._active or not self._can_reuse_image():
            self._image = QImage()
        if (not self._active or self._partial_signature is None or self._partial_signature[:5] != self._signature()[:5]
                or any(not math.isclose(m.get('imageFrameStep',-1.),self._span/max(1,math.ceil(self.width()*max(1.,min(4.,self._dpr)))),rel_tol=1e-12) for _,m in self._partial_tiles)):
            self._partial_tiles = []
        self._status = ('updating' if not self._image.isNull() or self._partial_tiles else 'loading') if self._active and self._path and self._duration > 0 else 'idle'
        self._error = ''
        self.statusChanged.emit()
        self.update()
        # Cancel an old source/viewport immediately, not after the debounce.
        # The queued signal is still checked by sequence on the GUI thread.
        if self._worker:
            self._worker.request(dict(sequence=self._sequence, source=self._source_key(), pending=True, inactive=not self._active))
        if not self._busy_timer.isActive() and not self._busy_visible:
            self._busy_timer.start()
        elapsed=time.monotonic()-self._last_submit
        if elapsed>=.016:
            self._timer.stop();self._submit()
        elif not self._timer.isActive():
            self._timer.start(max(1,math.ceil((.016-elapsed)*1000)))

    @Slot()
    def _submit(self):
        self._last_submit=time.monotonic()
        if not self._path or self._duration <= 0:
            if self._worker:
                self._worker.request(dict(sequence=self._sequence, source=('', 0.)))
            self._status = 'idle'
            self.statusChanged.emit()
            return
        if not self._active or self.width() < 1 or self.height() < 1:
            if self._worker:
                self._worker.request(dict(sequence=self._sequence, source=self._source_key(), inactive=True))
            return
        dpr = max(1., min(4., self._dpr))
        width = max(1, math.ceil(self.width() * dpr))
        height = max(1, math.ceil(self.height() * dpr))
        # Pixel-aligned, globally anchored tiles permit reuse across viewport pans.
        step = self._span / width
        start_column = (self._first - 1) / step
        left = math.floor(start_column / TILE_WIDTH)
        right = math.ceil((start_column + width) / TILE_WIDTH)
        tile_count = right - left + 2
        count = tile_count * TILE_WIDTH
        first = 1 + (left - 1) * TILE_WIDTH * step
        if self._controller is not None and hasattr(self._controller, 'spectrumColumnTimes'):
            times = self._controller.spectrumColumnTimes(first, count * step, count)
        else:
            # Only isolated/unindexed audio harnesses use the constant-grid fallback.
            total = max(1., self._duration * 100)
            times = (first - 1 + (np.arange(count) + .5) * step) * self._duration / total
        times = np.asarray(times, dtype=np.float64) - self._source_offset
        if len(times) != count or not np.isfinite(times).all():
            self._status = 'error'
            self._error = '频谱时间轴范围异常'
            self.statusChanged.emit()
            return
        request = dict(sequence=self._sequence, progressive=True, progressiveDecode=True, source=self._source_key(), width=width,
                       height=height, step=step, offset=start_column-left*TILE_WIDTH,
                       mode=self._mode, signature=self._signature(), imageFirstFrame=1 + left*TILE_WIDTH*step,
                       activeCalibration=self._vocal, gain=max(-12., min(12., self._gain)), preset=self._preset, window=self._window,
                       black=self._black, tilt=self._tilt, frequency=self._frequency, axis=self._axis,
                       secondsPerColumn=float(np.max(np.diff(times))) if len(times)>1 else 0.,
                       tiles=[(left-1+i,times[i*TILE_WIDTH:(i+1)*TILE_WIDTH]) for i in range(tile_count)])
        if self._worker is None:
            self._worker = _Worker(self._rendered.emit, self._controller)
            worker = self._worker
            self.destroyed.connect(lambda: worker.stop())
        self._status = 'updating' if not self._image.isNull() or self._partial_tiles else 'loading'
        self._error = ''
        self.statusChanged.emit()
        self._worker.request(request)

    @Slot(int, QImage, float, object, str)
    def _accept(self, sequence, image, offset, metrics, error):
        if sequence != self._sequence or not self._active:
            return
        if metrics.get('imageSignature') is not None and metrics['imageSignature'] != self._signature():return
        if metrics.get('partial') and not error:
            signature = metrics.get('imageSignature')
            if signature != self._signature():
                return
            if self._partial_sequence != sequence:
                self._partial_tiles = []
                self._partial_sequence = sequence
            self._partial_signature = signature
            self._partial_tiles.append((image, metrics))
            self._metrics = {**self._metrics, 'completedTiles': metrics['completedTiles'],
                             'totalTiles': metrics['totalTiles'], 'renderComplete': False,
                             'calibrationPending': metrics.get('calibrationPending', False)}
            self._status = 'updating'
            self.statusChanged.emit()
            self.update()
            return
        self._partial_tiles = []
        self._partial_sequence = None
        self._partial_signature = None
        self._image = image
        self._offset = offset
        self._metrics = metrics
        self._image_signature = metrics.get('imageSignature')
        self._error = error
        self._status = 'error' if error else 'ready'
        self._busy_timer.stop();self._busy_visible=False
        self.statusChanged.emit()
        self.update()

    def paint(self, painter):
        painter.fillRect(self.boundingRect(), Qt.GlobalColor.black)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        dpr = max(1., min(4., self._dpr))
        scale = self.width() / max(1e-9, self._span)
        def draw(image, metrics):
            if image.isNull(): return
            expected=self._span/max(1,math.ceil(self.width()*dpr))
            if not math.isclose(metrics.get('imageFrameStep',expected),expected,rel_tol=1e-12):return
            first = metrics.get('imageFirstFrame', self._first - self._offset*self._span/max(1,self.width()*dpr))
            step = metrics.get('imageFrameStep', self._span/max(1,self.width()*dpr))
            painter.drawImage(QRectF((first-self._first)*scale, 0,
                                    image.width()*step*scale, self.height()), image)
        # Only old pixels with the same source identity survive _schedule().
        # Missing new tiles do not wipe correctly mapped previous pixels.
        draw(self._image, self._metrics)
        for image, metrics in self._partial_tiles:
            draw(image, metrics)


_registered = False


def register_types():
    global _registered
    if not _registered:
        from PySide6.QtQml import qmlRegisterType
        qmlRegisterType(SpectrogramItem, 'Lyria', 1, 0, 'SpectrogramSurface')
        _registered = True
