"""Synthetic regression for bounded OCR reuse; not recognition quality evidence."""
from unittest.mock import patch
import numpy as np
import pytest
from lyricsync import vision
from lyricsync.model import Detection
from lyricsync.storage import Cache


class MemoryCache:
    key = staticmethod(Cache.key)
    def __init__(self):
        self.values = {}
    def get(self, key):
        return self.values.get(key)
    def put(self, key, value):
        self.values[key] = value


def run_frames(frames, confidence=.99):
    class FakeVideo:
        info = {'width': 80, 'height': 30, 'duration': 3.0, 'fps': 10}
        def __init__(self, path):
            self.info = dict(type(self).info)
        def frames(self, *args, **kwargs):
            yield from frames
    class Provider:
        signature = {'name': 'synthetic-fixture'}
        def __init__(self):
            self.seen = []
        def recognize(self, image):
            self.seen.append(image.copy())
            return [Detection('fixture text', [0, 0, 70, 25], confidence)]
    provider = Provider()
    with patch.object(vision, 'Video', FakeVideo), patch.object(vision, 'file_hash', return_value='fixture'), patch.object(vision, 'edge_mask', return_value=np.zeros((2, 2), np.uint8)), patch.object(vision, 'refine_track', side_effect=lambda video, track, *args: (track.first, track.last+.1, [[0, .1], [track.last, track.last+.1]], [])):
        visuals, info = vision.scan('fixture', {'sample_fps': 10}, MemoryCache(), provider=provider)
    return provider, visuals, info


def test_edge_collision_does_not_reuse_changed_pixels():
    frames = [(i/10, np.full((30, 80, 3), i, np.uint8)) for i in range(10)]
    provider, _, info = run_frames(frames)
    assert len(provider.seen) == 10
    assert info['ocr_metrics']['ocr_calls'] == 10
    assert info['ocr_metrics']['reused_frames'] == 0


def test_identical_pixels_reuse_is_bounded_by_actual_inference():
    frame = np.zeros((30, 80, 3), np.uint8)
    provider, _, info = run_frames([(i/10, frame) for i in range(30)])
    assert len(provider.seen) == 3
    assert info['ocr_metrics']['reused_frames'] == 27


def test_low_confidence_is_not_presented_as_verified_accuracy():
    _, visuals, _ = run_frames([(0, np.zeros((30, 80, 3), np.uint8)), (.1, np.ones((30, 80, 3), np.uint8))], .7)
    assert visuals
    assert any('置信度不是准确率' in note for item in visuals for note in item.warnings)
    assert all(item.confidence <= .7 for item in visuals)
