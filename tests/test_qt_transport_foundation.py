"""0.2 transport-intent regressions. Real QML input plus delayed decode.
Not native desktop/GPU/audio or user-project acceptance.
"""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from test_qt_timeline_input import application, timeline_video, ui, find, point
from test_qt_scrub_regression import delayed_media
from desktop_qt.playback_clock import AudioPositionClock


@pytest.mark.parametrize("finish", ["release", "focus_loss", "collapse"])
def test_qml_release_retains_target_while_exact_decode_is_pending(ui, monkeypatch, finish):
    app, bridge, window, timeline = ui
    requests = []
    monkeypatch.setattr(bridge.media_controller.worker, 'seek', requests.append)
    ruler = find(window, 'timelineRulerMouse')
    head = find(window, 'timelinePlayhead')
    start = point(ruler, ruler.width() * .25, 12)
    end = point(ruler, ruler.width() * .75, 12)
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(window, end, 5)
    QTest.qWait(20)
    target = bridge.media['requestedFrame']
    before = head.x()
    assert target > 1 and before > 0
    if finish == "focus_loss":
        timeline.setProperty("windowActive", False)
    elif finish == "collapse":
        timeline.setProperty("collapsed", True)
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end)
    QTest.qWait(40)
    assert bridge.media['displayedFrame'] == 1  # no fake rendered-frame progress
    assert bridge.media['requestedFrame'] == target
    assert abs(head.x() - before) < .01, 'release snapped to stale/offscreen decoded frame'
    assert ruler.property('preventStealing')


def test_repeated_identical_scrub_targets_do_not_enqueue_more_decode(delayed_media):
    bridge, media, sink, returned, requests, deliver = delayed_media
    media.scrub(50, begin=True)
    for _ in range(50):
        media.scrub(50)
    assert len(requests) == 1
    media.seek(50)
    assert len(requests) == 2  # release still requests an exact new generation
    assert media.snapshot()['playheadFrame'] == 50
    assert media.snapshot()['displayedFrame'] == 1


def test_audio_clock_waits_for_seek_ack_in_either_direction():
    clock = AudioPositionClock()
    clock.reset(anchor=10.)
    assert clock.sample(0., 1.) == 10.
    assert clock.sample(.05, 1.05) == 10.
    assert clock.sample(10., 1.1) == 10.
    assert clock.sample(10.05, 1.15) == 10.05
    clock.reset(anchor=2.)
    assert clock.sample(10.1, 2.) == 2.
    assert clock.sample(2., 2.1) == 2.
    assert clock.sample(2.04, 2.14) == 2.04


def test_pause_during_scrub_then_play_can_complete(delayed_media):
    bridge, media, sink, returned, requests, deliver = delayed_media
    media.scrub(50, begin=True)
    old = requests[-1]
    media.pause()
    media.play()
    latest = requests[-1]
    assert latest.transport == media.transport, 'play waits forever for invalidated scrub generation'
    deliver(latest)
    assert media.snapshot()['playing']
    assert media.snapshot()['displayedFrame'] == 50
    deliver(old)
    assert media.snapshot()['displayedFrame'] == 50
    media.pause()

@pytest.mark.parametrize('samples', [
    [(0., 0.), (10.4, .4), (10.5, .5), (11., 1.), (12., 2.)],
    [(10.4, .4), (10.5, .5), (11., 1.), (12., 2.)],
    [(13., 3.), (13.1, 3.1)],
])
def test_audio_seek_ack_accepts_delayed_observation(samples):
    clock = AudioPositionClock()
    clock.reset(anchor=10., now=0.)
    for position, now in samples:
        observed = clock.sample(position, now)
        assert observed == pytest.approx(10. if position == 0. else position)


def test_audio_seek_missing_ack_times_out_not_stays_playing_forever():
    from desktop_qt.playback_clock import AudioSeekTimeout
    clock = AudioPositionClock()
    clock.reset(anchor=2., now=0.)
    assert clock.sample(10., 0.) == 2.
    assert clock.sample(10.5, .5) == 2.
    with pytest.raises(AudioSeekTimeout):
        clock.sample(12., 2.)


def test_media_delayed_audio_sample_progresses_and_bad_seek_pauses(delayed_media, monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtMultimedia import QMediaPlayer
    import desktop_qt.media as media_module
    bridge, media, sink, returned, requests, deliver = delayed_media
    now = [0.]
    position = [0]
    monkeypatch.setattr(media_module, 'time', SimpleNamespace(monotonic=lambda: now[0],perf_counter=lambda: now[0]))
    monkeypatch.setattr(media.player, 'setPosition', lambda _: None)
    monkeypatch.setattr(media.player, 'play', lambda: None)
    monkeypatch.setattr(media.player, 'pause', lambda: None)
    monkeypatch.setattr(media.player, 'position', lambda: position[0])
    monkeypatch.setattr(media.player, 'playbackState', lambda: QMediaPlayer.PlaybackState.PlayingState)
    monkeypatch.setattr(media.player, 'mediaStatus', lambda: QMediaPlayer.MediaStatus.BufferedMedia)
    media.metadata['has_audio'] = True
    media._state.update(displayedFrame=51, requestedFrame=51, positionSeconds=2.)
    media.play()
    media.timer.stop()  # deterministic ticks only, real transport and generation logic
    now[0] = .4; position[0] = 2400
    media._tick()
    assert requests[-1].frame == media.index.current(2.4)
    deliver(requests[-1])
    assert media.snapshot()['displayedFrame'] == 61 and media.snapshot()['playing']
    media.pause()
    now[0] = 1.; position[0] = 0
    errors = []
    media.error.connect(errors.append)
    media.play(); media.timer.stop()
    now[0] = 3.1
    media._tick()
    assert not media.snapshot()['playing']
    assert not media.snapshot()['playPending']
    assert media.snapshot()['displayedFrame'] == media.snapshot()['requestedFrame'] == 61
    assert errors and '已自动暂停' in errors[-1]
    # A transient failed seek does not permanently disable audio or retry.
    now[0] = 4.; position[0] = 2400
    media.play(); media.timer.stop(); media._tick()
    assert media.snapshot()['playing']
    media.pause()
