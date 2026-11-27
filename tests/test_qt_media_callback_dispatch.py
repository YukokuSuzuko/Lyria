"""Qt signal contract: native media notifications must not reenter transport.

These deterministic tests exercise real QMediaPlayer signal connections, not a
native-deadlock reproduction. The real audio stress harness remains separate.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import time
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QThread
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaPlayer
from desktop_qt.media import MediaController

@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])

@pytest.fixture
def media(app):
    value = MediaController()
    yield value
    value.close()
    deadline = time.monotonic() + 5
    while value.worker.thread.is_alive() and time.monotonic() < deadline:
        app.processEvents()
        value.worker.thread.join(.01)
    assert not value.worker.thread.is_alive()
    value.deleteLater()
    QCoreApplication.sendPostedEvents(value, QEvent.Type.DeferredDelete)

@pytest.mark.parametrize('status', [QMediaPlayer.MediaStatus.LoadedMedia,
                                   QMediaPlayer.MediaStatus.BufferedMedia])
def test_status_waits_for_native_transaction_to_return(app, media, status):
    calls = []
    media.player.mediaStatus = lambda: status
    media.player.activeVideoTrack = lambda: 0
    media.player.setActiveVideoTrack = lambda track: calls.append(
        (track, QThread.currentThread() == media.thread()))
    # Emitting synchronously models the actual setPosition/setSource callback
    # stack captured in native-callback-trace01. A direct connection fails here.
    media.player.mediaStatusChanged.emit(status)
    assert calls == [], 'status callback reentered the emitting native transaction'
    app.processEvents()
    assert calls == [(-1, True)]


def test_error_waits_then_preserves_failure_reporting(app, media):
    media.metadata['has_audio'] = True
    media.player.error = lambda: QMediaPlayer.Error.ResourceError
    media.player.errorString = lambda: 'synthetic'
    failures = []
    media.error.connect(failures.append)
    media.player.errorOccurred.emit(QMediaPlayer.Error.ResourceError, 'synthetic')
    assert not media._audio_failed, 'error callback reentered native transport'
    assert failures == []
    app.processEvents()
    assert media._audio_failed
    assert len(failures) == 1
    assert not media.snapshot()['playing']


def test_close_discards_already_queued_native_notifications(app, media):
    media.metadata['has_audio'] = True
    media.player.mediaStatus = lambda: QMediaPlayer.MediaStatus.LoadedMedia
    media.player.error = lambda: QMediaPlayer.Error.ResourceError
    media.player.errorString = lambda: 'synthetic'
    calls = []
    failures = []
    media.player.activeVideoTrack = lambda: 0
    media.player.setActiveVideoTrack = calls.append
    media.error.connect(failures.append)
    media.player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.LoadedMedia)
    media.player.errorOccurred.emit(QMediaPlayer.Error.ResourceError, 'synthetic')
    media.close()
    app.processEvents()
    assert calls == []
    assert failures == []
    assert not media._audio_failed


def test_superseded_status_does_not_reconfigure_current_source(app, media):
    calls = []
    media.player.activeVideoTrack = lambda: 0
    media.player.setActiveVideoTrack = calls.append
    media.player.mediaStatus = lambda: QMediaPlayer.MediaStatus.LoadedMedia
    media.player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.LoadedMedia)
    # Source replacement can move status to LoadingMedia before queued delivery.
    media.player.mediaStatus = lambda: QMediaPlayer.MediaStatus.LoadingMedia
    app.processEvents()
    assert calls == []


@pytest.mark.parametrize('current_error,current_message', [
    (QMediaPlayer.Error.NoError, ''),
    (QMediaPlayer.Error.FormatError, 'synthetic'),
    (QMediaPlayer.Error.ResourceError, 'different-source-error'),
])
def test_superseded_error_cannot_pause_current_source(app, media, current_error, current_message):
    media.metadata['has_audio'] = True
    failures = []
    pauses = []
    media.error.connect(failures.append)
    media.player.error = lambda: QMediaPlayer.Error.ResourceError
    media.player.errorString = lambda: 'synthetic'
    media.player.errorOccurred.emit(QMediaPlayer.Error.ResourceError, 'synthetic')
    media.player.error = lambda: current_error
    media.player.errorString = lambda: current_message
    original_pause = media.pause
    media.pause = lambda: pauses.append(True)
    try:
        app.processEvents()
        assert not media._audio_failed
        assert pauses == []
        assert failures == []
    finally:
        media.pause = original_pause


def test_already_disabled_video_track_needs_no_native_reconfiguration(app, media):
    # Actual empty-player state is disabled, without overriding the state getter.
    assert media.player.activeVideoTrack() == -1
    calls = []
    media.player.mediaStatus = lambda: QMediaPlayer.MediaStatus.LoadedMedia
    media.player.setActiveVideoTrack = calls.append
    media.player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.LoadedMedia)
    app.processEvents()
    assert calls == []


def test_close_stops_synchronously_on_owner_thread(app, media, monkeypatch):
    import desktop_qt.media as module
    real_invoke = module.QMetaObject.invokeMethod
    calls = []

    def invoke(player, name, connection):
        calls.append((player, name, connection, QThread.currentThread() == player.thread()))
        return real_invoke(player, name, connection)

    monkeypatch.setattr(module.QMetaObject, 'invokeMethod', invoke)
    media.close()
    assert calls == [(media.player, 'stop', module.Qt.ConnectionType.DirectConnection, True)]
    assert media.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
    media.close()
    assert len(calls) == 1


def test_native_stop_refuses_wrong_owner_thread(app, media):
    import threading
    from desktop_qt.media import stop_media_player
    failures = []

    def stop_from_worker():
        try:
            stop_media_player(media.player)
        except RuntimeError as error:
            failures.append(str(error))

    thread = threading.Thread(target=stop_from_worker)
    thread.start()
    thread.join(2)
    assert not thread.is_alive()
    assert len(failures) == 1 and '所属线程' in failures[0]


def test_native_stop_reports_failed_invocation(app, media, monkeypatch):
    import desktop_qt.media as module
    with monkeypatch.context() as patch:
        patch.setattr(module.QMetaObject, 'invokeMethod', lambda *args: False)
        with pytest.raises(RuntimeError, match='停止失败'):
            module.stop_media_player(media.player)
