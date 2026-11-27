"""dev6 real-PCM waveform and same-source preview regression, Windows offscreen."""
import numpy as np
import pytest
from lyricsync.spectrogram import AudioSource, RATE, waveform_rgb
from test_dev4_spectrogram import encoded_audio, evidence_dir
from test_qt_preview_visibility import application, find
from test_qt_phase1_preview_workspace import ui
from test_qt_preview_drag_gestures import chinese_fonts
from test_qt_dev4_spectrum_timeline import setup, evaluate
from tools.qt_event_loop_wait import wait_until, delay


def synthetic(samples):
    samples = np.array(samples, dtype=np.float32)
    return AudioSource(None, samples, len(samples) / RATE, 1.)


def test_peak_aggregation_preserves_single_sample_impulse():
    source = synthetic(np.zeros(RATE))
    source.samples[10000] = 1.
    source.samples[10001] = -1.
    image = waveform_rgb(source, (np.arange(100) + .5) / 100, 101)
    purple = np.all(image == (201, 166, 250), axis=2)
    assert purple[:, 45].all()  # both peaks in the same physical screen column
    assert not purple[0, 44] and not purple[-1, 46]


def test_sample_zoom_uses_real_samples_not_overview_and_zero_pads():
    source = synthetic([0., 1., 0., -1., 0.])
    image = waveform_rgb(source, np.linspace(-1, 6, 701) / RATE, 101)
    purple = np.all(image == (201, 166, 250), axis=2)
    assert purple[0, 200] and purple[-1, 400]
    assert purple[50, 0] and purple[50, -1]
    # Intermediate columns trace the actual adjacent sample interpolation.
    assert purple[25, 150] and purple[75, 350]
    assert not purple[0, 150]


def test_waveform_cancellation_and_invalid_times():
    source = synthetic([0., 1., 0.])
    with pytest.raises(InterruptedError):
        waveform_rgb(source, np.linspace(0, 1, 100), 100, lambda: True)
    with pytest.raises(ValueError):
        waveform_rgb(source, [1., 0.], 100)


def test_rapid_zoom_rejects_old_scale_then_accepts_atomic_final(ui, tmp_path):
    timeline, surface = setup(ui, tmp_path)
    snapshot = ui.bridge.document.snapshot()
    old = surface._image.cacheKey()
    sequence = surface._sequence
    for first, last in [(105, 160), (110, 130), (112, 120), (113, 118)]:
        evaluate(timeline, f'view({first},{last})')
        assert surface.property('status') == 'loading'
        assert surface._image.isNull()  # No stretched old-zoom raster.
    assert surface._sequence > sequence
    assert not find(ui, 'audioResolutionUpdating').isVisible()
    assert not find(ui, 'audioStatusMessage').isVisible()
    wait_until(lambda: surface.property('status') == 'ready', timeout_ms=10000)
    assert surface._image.cacheKey() != old
    assert not find(ui, 'audioResolutionUpdating').isVisible()
    assert surface._worker.latest['sequence'] == surface._sequence
    assert ui.bridge.document.snapshot() == snapshot
    delay(60)
    assert ui.window.grabWindow().save(str(evidence_dir(tmp_path) / 'dev6-spectrum-zoom-offscreen.png'))


@pytest.mark.parametrize("ratio", [1., 2.])
def test_waveform_mode_uses_pcm_worker_and_source_change_invalidates_preview(ui, tmp_path, ratio):
    timeline, surface = setup(ui, tmp_path)
    surface.setProperty('pixelRatio', ratio)
    ui.bridge.command('view.layout', {'audioMode': 'waveform'})
    wait_until(lambda: surface.property('status') == 'ready', timeout_ms=10000)
    assert surface.property('metrics')['displayMode'] == 'waveform'
    assert surface.property('metrics')['sampleRate'] == RATE
    assert not surface._image.isNull()
    # Much finer than the legacy overview's bins, without fabricated detail.
    evaluate(timeline, 'view(101,102)')
    wait_until(lambda: surface.property('status') == 'ready', timeout_ms=10000)
    assert surface.property('metrics')['secondsPerColumn'] < 2 / RATE
    delay(60)
    assert ui.window.grabWindow().save(str(evidence_dir(tmp_path) / f'dev6-waveform-near-sample-scale-{ratio:g}x-offscreen.png'))
    surface.setProperty('sourcePath', str(encoded_audio(tmp_path / 'replacement.wav', 2, 300)))
    assert surface._image.isNull()
    assert surface.property('status') == 'loading'


def test_retained_preview_paints_at_current_time_coordinates(application):
    from PySide6.QtGui import QImage, QPainter, QColor
    from desktop_qt.spectrogram_item import SpectrogramItem
    surface = SpectrogramItem()
    surface.setWidth(100); surface.setHeight(20)
    surface._image = QImage(100, 20, QImage.Format.Format_RGB32)
    surface._image.fill(QColor('black'))
    p = QPainter(surface._image); p.fillRect(30, 0, 10, 20, QColor('red')); p.end()
    surface._metrics = {'imageFirstFrame': 101., 'imageFrameStep': 1.}
    surface._first = 121.; surface._span = 100.
    target = QImage(100, 20, QImage.Format.Format_RGB32)
    p = QPainter(target); surface.paint(p); p.end()
    # Same-scale pan: source frame131..141 moves exactly to x10..20.
    assert target.pixelColor(10,10).red()==255
    assert target.pixelColor(19,10).red()==255
    assert target.pixelColor(9,10).red()==0
    assert target.pixelColor(20,10).red()==0
    # Changed scale must not stretch the same old bitmap to x20..40.
    surface._span=50.
    p=QPainter(target);surface.paint(p);p.end()
    assert all(target.pixelColor(x,10).red()==0 for x in range(100))
    surface._timer.stop()
