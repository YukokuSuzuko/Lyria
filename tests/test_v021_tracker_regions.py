"""Horizontal regions must retain independent OCR evidence and lifetimes."""
import numpy as np
from lyricsync.model import Detection
from lyricsync.vision import Tracker

IMAGE = np.zeros((80, 800, 3), dtype=np.uint8)
LEFT = [0, 10, 100, 30]
RIGHT = [650, 10, 750, 30]


def detection(text, box):
    return Detection(text, list(box), .99)


def test_same_text_jumping_region_is_a_new_occurrence():
    tracker = Tracker(.1)
    tracker.feed(0, IMAGE, [detection('same subtitle', LEFT)])
    tracker.feed(.1, IMAGE, [detection('same subtitle', RIGHT)])
    tracks = tracker.finish()
    assert len(tracks) == 2
    assert [t.box for t in tracks] == [LEFT, RIGHT]
    assert [[o['box'] for o in t.observations] for t in tracks] == [[LEFT], [RIGHT]]
    assert [(t.first, t.last) for t in tracks] == [(0, 0), (.1, .1)]


def test_simultaneous_identical_captions_do_not_exchange_regions():
    tracker = Tracker(.1)
    tracker.feed(0, IMAGE, [detection('same subtitle', LEFT), detection('same subtitle', RIGHT)])
    tracker.feed(.1, IMAGE, [detection('same subtitle', RIGHT), detection('same subtitle', LEFT)])
    tracks = tracker.finish()
    assert len(tracks) == 2
    for track in tracks:
        assert len(track.observations) == 2
        assert all(o['box'] == track.box for o in track.observations)


def test_small_motion_and_nearby_region_keep_occurrence():
    for box in ([8, 11, 108, 31], [140, 10, 240, 30]):
        tracker = Tracker(.1)
        tracker.feed(0, IMAGE, [detection('same subtitle', LEFT)])
        tracker.feed(.1, IMAGE, [detection('same subtitle', box)])
        tracks = tracker.finish()
        assert len(tracks) == 1
        assert [o['box'] for o in tracks[0].observations] == [LEFT, box]
        assert tracks[0].last == .1


def test_distant_different_caption_does_not_end_visible_left_track(monkeypatch):
    monkeypatch.setattr('lyricsync.vision.visibility_score', lambda *args: .95)
    tracker = Tracker(.1)
    tracker.feed(0, IMAGE, [detection('AAAAAAA', LEFT)])
    tracker.feed(.1, IMAGE, [detection('ZZZZZZZ', RIGHT)])
    assert not tracker.finished
    assert len(tracker.active) == 2
    assert tracker.active[0].shape_hits == 1
    tracker.feed(.2, IMAGE, [detection('AAAAAAA', LEFT), detection('ZZZZZZZ', RIGHT)])
    assert len(tracker.finish()) == 2
    assert all(t.last == .2 for t in tracker.finish())


def test_conflicting_text_in_same_region_still_ends_track(monkeypatch):
    monkeypatch.setattr('lyricsync.vision.visibility_score', lambda *args: .95)
    tracker = Tracker(.1)
    tracker.feed(0, IMAGE, [detection('AAAAAAA', LEFT)])
    tracker.feed(.1, IMAGE, [detection('ZZZZZZZ', LEFT)])
    assert len(tracker.finished) == 1
    assert tracker.finished[0].last == 0
    assert len(tracker.active) == 1
