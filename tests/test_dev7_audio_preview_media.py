"""Bounded real Qt audition stop/reopen: synthetic fixtures, muted, offscreen."""
import os
from pathlib import Path
import subprocess
import sys
import pytest
from test_qt_dev6_media_shutdown import fixtures, encode


@pytest.mark.parametrize('repeat',range(5))
def test_real_wav_cover_mp3_m4a_video_audition_closes_without_hang(tmp_path,repeat):
    audio,mp3,video=fixtures(tmp_path)
    m4a=tmp_path/'cover.m4a'
    encode(['-i',str(audio),'-i',str(tmp_path/'cover.png'),'-map','0:a','-map','1:v',
        '-c:a','aac','-c:v','png','-disposition:v','attached_pic',str(m4a)])
    script=r"""
import json,sys,time
from pathlib import Path
from PySide6.QtWidgets import QApplication,QDialog,QLabel
from PySide6.QtMultimedia import QAudioOutput,QMediaPlayer
import PySide6.QtMultimedia as multimedia
from PySide6.QtCore import QCoreApplication,QEvent
from desktop_qt.audio_preview import AudioPreview
from tools.qt_event_loop_wait import wait_until,delay
class SilentOutput(QAudioOutput):
    def setVolume(self,value):super().setVolume(0.0)
multimedia.QAudioOutput=SilentOutput
class Preview(QDialog,AudioPreview):
    def __init__(self):
        super().__init__();self._init_preview();self.status=QLabel(self)
    def reject(self):self._stop_preview();super().reject()
app=QApplication([])
rows=[]
for path in sys.argv[1:]:
    dialog=Preview()
    record=dict(source_path=path,stem_path=sys.argv[1],offset_seconds=.2,duration_seconds=.7)
    for vocal in (False,True,False):
        dialog._listen_audio(record,vocal)
        wait_until(lambda:dialog._preview_player.playbackState()==QMediaPlayer.PlaybackState.PlayingState,timeout_ms=8000)
        assert dialog._preview_player.videoSink() is dialog._preview_video_sink
        delay(60)
        started=time.monotonic();dialog._stop_preview();assert time.monotonic()-started<2
    dialog._listen_audio(record,False)
    wait_until(lambda:dialog._preview_player.playbackState()==QMediaPlayer.PlaybackState.PlayingState,timeout_ms=8000)
    started=time.monotonic();dialog.reject();assert time.monotonic()-started<2
    dialog.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()
    rows.append(Path(path).suffix)
print(json.dumps(dict(passed=rows,muted=True,offscreen=True)))
"""
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',PYTHONDONTWRITEBYTECODE='1')
    result=subprocess.run([sys.executable,'-B','-c',script,*map(str,(audio,mp3,m4a,video))],
        cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',
        timeout=50,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    assert '"muted": true' in result.stdout and '".m4a"' in result.stdout
