"""Qt playback parity using real no-audio frames and controlled delayed replies.

These are Linux/offscreen contracts, not Windows GPU/audio/performance or real
mouse/IME acceptance. No external providers, real credentials, or user media.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
import subprocess
import time
import pytest
pytest.importorskip('PySide6')
from PySide6.QtGui import QColor, QImage
from PySide6.QtMultimedia import QVideoSink
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from desktop_qt.controller import Bridge
from lyricsync.model import Mapping, Translation
from lyricsync.audit_service import ffmpeg_executable


@pytest.fixture(scope='module')
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope='module')
def no_audio_video(tmp_path_factory):
    path = tmp_path_factory.mktemp('playback-parity') / 'real-80-frames.mp4'
    subprocess.run([ffmpeg_executable(), '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=160x90:rate=40', '-frames:v', '80', '-an',
                    '-c:v', 'libx264', '-g', '80', '-pix_fmt', 'yuv420p', str(path)], check=True)
    return path


def wait_for(application, predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(.003)
    assert predicate(), 'timed out waiting for actual Qt presentation/state'


@pytest.fixture
def loaded(application, no_audio_video, tmp_path):
    bridge = Bridge(storage_dir=tmp_path / 'recovery')
    sink = QVideoSink()
    bridge.attachVideoSink(sink)
    assert bridge.command('media.open', {'path': str(no_audio_video)})
    wait_for(application, lambda: bridge.media['displayedFrame'] == 1)
    assert bridge.media['totalFrames'] == 80 and sink.videoFrame().isValid()
    assert not bridge.media_controller.metadata['has_audio']
    yield bridge, sink
    bridge.shutdown()
    deadline = time.monotonic() + 2
    while bridge.media_controller.worker.thread.is_alive() and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(.003)


def seek(application, bridge, frame):
    assert bridge.command('media.seek', {'frame': frame})
    wait_for(application, lambda: bridge.media['displayedFrame'] == frame)


def set_cues(bridge, spans):
    p = bridge.document.project
    index = bridge.media_controller.index
    p.translations = [Translation(identity, 'QA字幕 ' + identity) for identity, *_ in spans]
    p.mappings = [Mapping(identity, [identity], start=index.interval(first, last)[0],
                          end=index.interval(first, last)[1], confirmed=confirmed)
                  for identity, first, last, confirmed in spans]
    bridge.selected = p.mappings[0].id
    bridge._changed()


def test_play_advances_pause_freezes_and_seek_stays_paused(application, loaded):
    bridge, sink = loaded
    seek(application, bridge, 10)
    assert bridge.command('media.play', {})
    wait_for(application, lambda: bridge.media['displayedFrame'] >= 14)
    assert bridge.media['playing']
    assert bridge.command('media.pause', {})
    paused = bridge.media['displayedFrame']
    QTest.qWait(160)
    assert not bridge.media['playing']
    assert bridge.media['displayedFrame'] == bridge.media['requestedFrame'] == paused
    seek(application, bridge, 35)
    QTest.qWait(120)
    assert bridge.media['displayedFrame'] == bridge.media['requestedFrame'] == 35
    assert not bridge.media['playing']
    assert sink.videoFrame().startTime() == round(bridge.media_controller.index.stamp(35) * 1e6)


def test_cue_play_uses_five_frame_handles_and_keeps_selected_cue(application, loaded):
    bridge, _ = loaded
    set_cues(bridge, [('before', 1, 9, False), ('current', 10, 20, False), ('after', 21, 35, False)])
    bridge.selected = 'current'
    bridge._refresh(False)
    presented = []
    bridge.media_controller.presented.connect(lambda frame, stamp: presented.append(frame))
    assert bridge.command('media.playCue', {})
    wait_for(application, lambda: bool(presented))
    assert presented[0] == 5, 'cue playback must start at first minus five, not at the cue start'
    wait_for(application, lambda: not bridge.media['playing'] and bridge.media['displayedFrame'] == 25)
    assert max(presented) == 25 and min(presented) == 5
    assert bridge.selected == 'current', 'five-frame neighboring handles must not steal cue selection'
    QTest.qWait(100)
    assert bridge.media['displayedFrame'] == 25


def test_tail_cue_clamps_to_last_frame_and_never_restarts(application, loaded):
    bridge, _ = loaded
    set_cues(bridge, [('tail', 79, 80, False)])
    presented = []
    bridge.media_controller.presented.connect(lambda frame, stamp: presented.append(frame))
    assert bridge.command('media.playCue', {})
    wait_for(application, lambda: bool(presented))
    assert presented[0] == 74
    wait_for(application, lambda: not bridge.media['playing'] and bridge.media['displayedFrame'] == 80)
    assert all(74 <= frame <= 80 for frame in presented)
    assert bridge.command('media.play', {})
    wait_for(application, lambda: not bridge.media['playing'])
    QTest.qWait(80)
    assert bridge.media['displayedFrame'] == bridge.media['requestedFrame'] == 80
    assert 1 not in presented


def test_fast_steps_accumulate_pending_targets_and_explicit_seek_resets(application, loaded, monkeypatch):
    bridge, sink = loaded
    seek(application, bridge, 40)
    media = bridge.media_controller
    requests = []
    monkeypatch.setattr(media.worker, 'seek', requests.append)
    for delta in (5, 5, 1, -1, 5):
        assert bridge.command('media.step', {'delta': delta})
    assert [request.frame for request in requests] == [45, 50, 51, 50, 55]
    assert media.snapshot()['displayedFrame'] == 40
    assert media.snapshot()['requestedFrame'] == 55
    old = requests[-1]
    assert bridge.command('media.seek', {'frame': 10})
    assert bridge.command('media.step', {'delta': 5})
    assert requests[-1].frame == media.snapshot()['requestedFrame'] == 15
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(QColor(15, 0, 0))
    media._decoded.emit(requests[-1], image, media.index.stamp(15), {})
    assert media.snapshot()['displayedFrame'] == 15
    image_old = QImage(4, 4, QImage.Format.Format_RGB32)
    image_old.fill(QColor(55, 0, 0))
    media._decoded.emit(old, image_old, media.index.stamp(55), {})
    assert media.snapshot()['displayedFrame'] == media.snapshot()['requestedFrame'] == 15
    assert sink.videoFrame().toImage().pixelColor(0, 0).red() == 15


def test_actual_seek_follows_cue_and_reports_gap_without_fake_selection(application, loaded):
    bridge, _ = loaded
    set_cues(bridge, [('first', 6, 15, False), ('second', 21, 30, False)])
    seek(application, bridge, 25)
    assert bridge.selected == 'second'
    assert bridge.media['activeCueIds'] == ['second']
    assert bridge.media['frameStatus'] == 'single'
    seek(application, bridge, 18)
    assert bridge.media['activeCueIds'] == []
    assert bridge.media['activeCueCount'] == 0
    assert bridge.media['frameStatus'] == 'gap'
    assert bridge.media['subtitleText'] == ''


def test_filtered_current_cue_has_explicit_hint_and_remains_real_preview(application, loaded):
    bridge, _ = loaded
    set_cues(bridge, [('first', 6, 15, False), ('second', 21, 30, True)])
    assert bridge.command('view.filter', {'unconfirmedOnly': True})
    assert [row['number'] for row in bridge.model.items] == [1]
    seek(application, bridge, 25)
    assert bridge.media['activeCueIds'] == ['second']
    assert bridge.media['frameStatus'] == 'single', 'filtering must not erase physical subtitle presence'
    assert bridge.media['hiddenCueCount'] == 1
    assert '筛选隐藏' in bridge.media['frameMessage']
    assert 'second' in bridge.media['subtitleText'], 'filtering review rows must not suppress actual subtitle preview'
    assert bridge.selected == 'first', 'hidden row must not become an unrepresentable list selection'
    assert bridge.command('view.filter', {'unconfirmedOnly': False})
    assert bridge.media['hiddenCueCount'] == 0
    assert '筛选隐藏' not in bridge.media['frameMessage']


def test_overlap_marks_both_rows_but_never_adjacent_frames(application, loaded):
    bridge, _ = loaded
    set_cues(bridge, [('first', 6, 20, False), ('second', 21, 30, False)])
    assert [row['overlap'] for row in bridge.state['rows']] == [False, False]
    seek(application, bridge, 20)
    assert bridge.media['activeCueCount'] == 1
    assert bridge.media['frameStatus'] == 'single'
    row = bridge.document.project.mappings[1]
    row.start = bridge.media_controller.index.stamp(20)
    bridge._changed()
    assert [row['overlap'] for row in bridge.state['rows']] == [True, True]
    assert [row['overlap'] for row in bridge.model.items] == [True, True]
    assert bridge.media['frameStatus'] == 'overlap'
    assert bridge.media['activeCueCount'] == 2
    assert set(bridge.media['activeCueIds']) == {'first', 'second'}
    assert '重叠' in bridge.media['frameMessage']


def test_overlap_marks_all_conflicting_rows_not_just_list_neighbors(application, loaded):
    bridge, _ = loaded
    set_cues(bridge, [('first', 6, 20, False), ('middle', 30, 40, False), ('last', 18, 25, False)])
    flags = {row['id']: row['overlap'] for row in bridge.state['rows']}
    assert flags == {'first': True, 'middle': False, 'last': True}
    seek(application, bridge, 19)
    assert bridge.media['activeCueCount'] == 2
    assert set(bridge.media['activeCueIds']) == {'first', 'last'}
    assert bridge.media['frameStatus'] == 'overlap'
