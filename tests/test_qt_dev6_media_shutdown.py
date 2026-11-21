"""Real local cover/WAV/video transport and orderly shutdown, no UI mocks."""
import os
import subprocess
import time
import pytest
from PIL import Image
from PySide6.QtMultimedia import QVideoSink
from test_qt_media_callback_dispatch import app, media
from test_phase2_audio_editing import wav
from lyricsync.audit_service import ffmpeg_executable
from tools.qt_event_loop_wait import wait_until, delay


def encode(args):
    result = subprocess.run([ffmpeg_executable(), '-hide_banner', '-loglevel', 'error', '-y', *args],
                            capture_output=True, timeout=30,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    assert result.returncode == 0, result.stderr.decode(errors='replace')


def fixtures(tmp_path):
    audio = wav(tmp_path / 'plain.wav')
    cover = tmp_path / 'cover.png'; Image.new('RGB', (64, 64), (90, 30, 180)).save(cover)
    mp3 = tmp_path / 'cover.mp3'
    encode(['-i', str(audio), '-i', str(cover), '-map', '0:a', '-map', '1:v',
            '-c:a', 'libmp3lame', '-c:v', 'png', '-disposition:v', 'attached_pic', str(mp3)])
    video = tmp_path / 'timed.mp4'
    encode(['-f', 'lavfi', '-i', 'testsrc2=size=96x64:rate=25', '-i', str(audio),
            '-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(video)])
    return audio, mp3, video


def test_cover_wav_video_switch_clear_and_close_use_private_drain(app, media, tmp_path):
    audio, mp3, video = fixtures(tmp_path)
    visible_sink = QVideoSink(media)
    media.attach_sink(visible_sink)
    assert media.player.videoSink() is media._audio_frame_drain
    assert media.player.videoSink() is not visible_sink
    for path, kind in [(mp3, 'audio'), (audio, 'audio'), (video, 'video'), (mp3, 'audio')]:
        media.open(path)
        wait_until(lambda: media.snapshot()['ready'], timeout_ms=10000)
        assert media.snapshot()['mediaKind'] == kind
        media.seek_seconds(1.)
        wait_until(lambda: not media.snapshot()['seekPending'], timeout_ms=5000)
        media.play(); delay(80); media.pause()
        if kind == 'video':
            wait_until(lambda: media.player.activeVideoTrack() == -1, timeout_ms=5000)
            assert visible_sink.videoFrame().isValid()
            assert media.snapshot()['decodedFrames'] > 0
        started = time.monotonic(); media.clear()
        assert time.monotonic() - started < 2.
        assert not media.snapshot()['ready']
        assert not media._audio_frame_drain.videoFrame().isValid()
    media.open(mp3); wait_until(lambda: media.snapshot()['ready'], timeout_ms=10000)
    started = time.monotonic(); media.close()
    assert time.monotonic() - started < 2.
    wait_until(lambda: not media.worker.thread.is_alive(), timeout_ms=5000)
    assert not media._audio_frame_drain.videoFrame().isValid()
    # Idempotent close must not reenter Qt transport or recreate work.
    media.close()


# Keep each cycle a distinct pytest item: native teardown must finish before
# the next owner is created, including under pytest's default fd capture.
@pytest.mark.parametrize("suffix,codec", [(".mp3", "libmp3lame"), (".m4a", "aac")])
@pytest.mark.parametrize("cycle", range(6))
def test_immediate_cover_close_after_wav_reopen(app, tmp_path, suffix, codec, cycle):
    from contextlib import contextmanager
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtMultimedia import QMediaPlayer
    from shiboken6 import isValid
    from desktop_qt.controller import Bridge
    from test_phase2_audio_editing import wait

    @contextmanager
    def session(name):
        owner = Bridge(storage_dir=tmp_path / name)
        owner.media_controller.audio.setMuted(True)
        try:
            yield owner
        finally:
            started = time.monotonic()
            owner.wait_for_shutdown(app)
            assert time.monotonic() - started < 2.
            assert owner.shutdown_complete and not owner.jobs.live_threads()
            assert not owner.media_controller.worker.thread.is_alive()
            assert owner.media_controller.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
            assert not owner.media_controller._audio_frame_drain.videoFrame().isValid()
            owner.media_controller.close()  # Still idempotent.
            owner.deleteLater()
            QCoreApplication.sendPostedEvents(owner, QEvent.Type.DeferredDelete)
            assert not isValid(owner)

    audio = wav(tmp_path / 'plain.wav')
    cover = tmp_path / 'cover.png'
    Image.new('RGB', (64, 64), (90, 30, 180)).save(cover)
    target = tmp_path / ('album' + suffix)
    encode(['-i', str(audio), '-i', str(cover), '-map', '0:a', '-map', '1:v',
            '-c:a', codec, '-c:v', 'png', '-disposition:v', 'attached_pic', str(target)])

    # Native playback/reopen in the previous owner exposes the real shutdown
    # race more reliably than a fresh-process, cover-only smoke test.
    with session('warmup') as owner:
        for _ in range(2):
            assert owner.command('media.open', {'path': str(audio)})
            wait(app, lambda: owner.state['audioAnalysis']['status'] == 'ready')
            assert owner.command('media.seekSeconds', {'seconds': 1.25})
            owner._new()
        assert owner.command('media.open', {'path': str(audio)})
        wait(app, lambda: owner.state['audioAnalysis']['status'] == 'ready')
        assert owner.command('media.play', {})
        wait(app, lambda: owner.media['positionSeconds'] > .05)
        assert owner.command('media.pause', {})

    with session('cover') as owner:
        assert owner.command('media.open', {'path': str(target)})
        wait(app, lambda: owner.state['audioAnalysis']['status'] == 'ready')
        assert owner.media['mediaKind'] == 'audio'
        assert owner.media['decodedFrames'] == 0
        assert owner.command('media.seekSeconds', {'seconds': 1.25})
        # Deliberately no event pumping/sleep between seek and shutdown. The
        # original failure is inside synchronous stop(), not a missing wait.
        assert owner.media['positionSeconds'] == 1.25
