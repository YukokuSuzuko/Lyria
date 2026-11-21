"""Bounded original/stem audition shared by ASR and separation-only dialogs.

No playback on result arrival. Each explicit source switch seeks before play,
and the original/stem use different offsets for the same captured audio range.
"""
from pathlib import Path
from PySide6.QtCore import QUrl, Qt


class AudioPreview:
    def _init_preview(self):
        self._preview_player = None
        self._preview_seek_ms = None
        self._preview_end_ms = 0

    def _listen_audio(self, record, vocals):
        path = record.get("stem_path") if vocals else record["source_path"]
        if not path or not Path(path).is_file():
            self.status.setText('试听音频文件不存在，请重新生成结果。')
            return
        from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink
        if self._preview_player is None:
            self._preview_player = QMediaPlayer(self)
            self._preview_output = QAudioOutput(self)
            self._preview_output.setVolume(.8)
            self._preview_player.setAudioOutput(self._preview_output)
            # Drain cover art and timed-video frames even though this is audio-only.
            # Qt FFmpeg can block stop() if its attached-picture queue is undrained.
            self._preview_video_sink = QVideoSink(self)
            self._preview_player.setVideoSink(self._preview_video_sink)
            self._preview_player.positionChanged.connect(self._preview_position, Qt.ConnectionType.QueuedConnection)
            self._preview_player.errorOccurred.connect(lambda error, message: self.status.setText("试听失败：" + message))
            self._preview_player.mediaStatusChanged.connect(self._preview_loaded, Qt.ConnectionType.QueuedConnection)
        self._stop_preview()
        start = 0.0 if vocals else record["offset_seconds"]
        self._preview_seek_ms = round(start * 1000)
        self._preview_end_ms = round((start + record["duration_seconds"]) * 1000)
        source = QUrl.fromLocalFile(str(Path(path).resolve()))
        if self._preview_player.source() == source:
            self._preview_player.setPosition(self._preview_seek_ms)
            self._preview_seek_ms = None
            self._preview_player.play()
        else:
            self._preview_player.setSource(source)

    def _preview_position(self, position):
        # A queued tick from the old A/B source must not stop the new source.
        if (self._preview_end_ms and position >= self._preview_end_ms
                and self._preview_player.position() >= self._preview_end_ms):
            self._stop_preview()

    def _preview_loaded(self, status):
        from PySide6.QtMultimedia import QMediaPlayer
        if (status == QMediaPlayer.MediaStatus.LoadedMedia and self._preview_seek_ms is not None
                and self._preview_player.mediaStatus() in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia)):
            self._preview_player.setPosition(self._preview_seek_ms)
            self._preview_seek_ms = None
            self._preview_player.play()

    def _stop_preview(self):
        self._preview_seek_ms = None
        self._preview_end_ms = 0
        if self._preview_player is not None:
            from .media import stop_media_player
            stop_media_player(self._preview_player)
