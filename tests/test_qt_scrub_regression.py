"""Strict Qt scrub acceptance regressions, with controlled delayed frame delivery.

This is not a decoder throughput benchmark or a Windows mouse/DPI acceptance
suite. A deterministic 60 ms frame return, against a 20 ms scrub input stream,
reproduces the previous starvation bug without relying on machine performance.
Keep the assertions after fixes; do not replace them with mere request counters.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')

import time
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtMultimedia import QVideoSink
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop_qt.controller import Bridge
from lyricsync.frames import FrameIndex


@pytest.fixture(scope='module')
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def delayed_media(application, tmp_path):
    bridge = Bridge(storage_dir=tmp_path / 'recovery')
    media = bridge.media_controller
    media.index = FrameIndex([n * .04 for n in range(150)], 6.0)
    media._state.update(ready=True, loaded=True, displayedFrame=1, requestedFrame=1,
                        totalFrames=150, positionSeconds=0.)
    media.metadata = {'has_audio': False}
    sink = QVideoSink()
    media.attach_sink(sink)
    returned = []
    requests = []

    def deliver(request):
        image = QImage(4, 4, QImage.Format.Format_RGB32)
        image.fill(QColor(request.frame, 0, 0))
        returned.append(request)
        media._decoded.emit(request, image, media.index.stamp(request.frame), {})

    def delayed_seek(request):
        requests.append(request)
        QTimer.singleShot(60, lambda request=request: deliver(request))

    # Only decoder completion is substituted. Real Bridge commands, Qt signal
    # dispatch, MediaController generation checks, and QVideoSink still execute.
    media.worker.seek = delayed_seek
    yield bridge, media, sink, returned, requests, deliver
    QTest.qWait(80)
    bridge.shutdown()
    deadline = time.monotonic() + 2
    while media.worker.thread.is_alive() and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(.005)


def test_continuous_scrub_presents_real_intermediate_frames_before_release(delayed_media):
    bridge, media, sink, returned, requests, deliver = delayed_media
    assert bridge.command('media.scrubBegin', {'frame': 2})
    for frame in range(3, 22):
        QTest.qWait(20)
        assert bridge.command('media.scrub', {'frame': frame})
    state = media.snapshot()
    # These must remain strict: the old transport++ on every drag yielded zero
    # presented frames here and only updated after the mouse stopped.
    assert state['scrubbing'] is True
    assert state['presentedFrames'] >= 3, 'continuous dragging starved actual intermediate presentation'
    assert 1 < state['displayedFrame'] < state['requestedFrame'] == 21
    assert state['displayedFrame'] in {request.frame for request in returned}
    assert sink.videoFrame().isValid()
    assert sink.videoFrame().toImage().pixelColor(0, 0).red() == state['displayedFrame']
    assert sink.videoFrame().startTime() == round(media.index.stamp(state['displayedFrame']) * 1_000_000)
    assert len({request.transport for request in requests}) == 1, 'scrub must retain one cancellable generation'
    assert bridge.command('media.scrubEnd', {'frame': 21})
    QTest.qWait(100)
    assert media.snapshot()['scrubbing'] is False
    assert media.snapshot()['displayedFrame'] == media.snapshot()['requestedFrame'] == 21
    assert sink.videoFrame().toImage().pixelColor(0, 0).red() == 21


def test_late_scrub_result_never_overwrites_new_explicit_seek(delayed_media):
    bridge, media, sink, returned, requests, deliver = delayed_media
    assert bridge.command('media.scrubBegin', {'frame': 80})
    old = requests[-1]
    assert bridge.command('media.scrub', {'frame': 85})
    assert bridge.command('media.seek', {'frame': 10})
    new = requests[-1]
    assert new.transport != old.transport
    deliver(new)
    assert media.snapshot()['displayedFrame'] == 10
    assert media.snapshot()['requestedFrame'] == 10
    deliver(old)
    assert media.snapshot()['displayedFrame'] == 10
    assert media.snapshot()['requestedFrame'] == 10
    assert sink.videoFrame().toImage().pixelColor(0, 0).red() == 10
    QTest.qWait(100)
    assert media.snapshot()['displayedFrame'] == 10


def test_intermediate_return_cannot_pull_requested_drag_target_back(delayed_media):
    bridge, media, sink, returned, requests, deliver = delayed_media
    assert bridge.command('media.scrubBegin', {'frame': 20})
    first = requests[-1]
    assert bridge.command('media.scrub', {'frame': 40})
    deliver(first)
    state = media.snapshot()
    assert state['displayedFrame'] == 20, 'actual returned image should be presented while scrubbing'
    assert state['requestedFrame'] == 40, 'user-owned drag target must not be overwritten by older decoded PTS'
    assert state['scrubbing'] is True
    assert sink.videoFrame().toImage().pixelColor(0, 0).red() == 20
    assert bridge.command('media.scrubEnd', {'frame': 45})
    final = requests[-1]
    deliver(final)
    deliver(first)
    assert media.snapshot()['displayedFrame'] == 45
    assert media.snapshot()['requestedFrame'] == 45
    assert sink.videoFrame().toImage().pixelColor(0, 0).red() == 45
