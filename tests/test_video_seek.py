"""Cooperative preview cancellation without changing decoded-frame precision."""
from fractions import Fraction
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from lyricsync.frames import FrameIndex
from lyricsync.vision import Video


class FakeFrame:
    def __init__(self, pts, on_convert=lambda: None):
        self.pts = pts
        self.time_base = Fraction(1, 1000)
        self.image = object()
        self.conversions = 0
        self.on_convert = on_convert

    def to_ndarray(self, format):
        assert format == "bgr24"
        self.conversions += 1
        self.on_convert()
        return self.image


class FakeContainer:
    def __init__(self, frames=(), on_decode=lambda: None):
        self.stream = SimpleNamespace(time_base=Fraction(1, 1000), thread_type=None)
        self.streams = SimpleNamespace(video=[self.stream])
        self.frames = list(frames)
        self.on_decode = on_decode
        self.entered = self.closed = self.seeked = False
        self.decoded = self.decode_calls = 0

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc):
        self.closed = True

    def seek(self, *args, **kwargs):
        self.seeked = True

    def decode(self, stream):
        self.decode_calls += 1
        for frame in self.frames:
            self.decoded += 1
            self.on_decode()
            yield frame


class CancelledVideoSeekTests(unittest.TestCase):
    def setUp(self):
        self.video = Video.__new__(Video)
        self.video.path = "synthetic-only.mp4"
        self.video.origin = 0
        self.video.info = {"duration": 3}

    def test_already_cancelled_at_never_opens_file(self):
        with patch("av.open") as opened:
            with self.assertRaises(InterruptedError):
                self.video.at(2, cancel=lambda: True)
        opened.assert_not_called()

    def test_already_cancelled_frames_never_opens_file(self):
        with patch("av.open") as opened:
            with self.assertRaises(InterruptedError):
                next(self.video.frames(2, cancel=lambda: True))
        opened.assert_not_called()

    def test_cancel_after_open_avoids_seek_and_decode_and_closes(self):
        container = FakeContainer([FakeFrame(2000)])
        with patch("av.open", return_value=container):
            with self.assertRaises(InterruptedError):
                self.video.at(2, cancel=lambda: container.entered)
        self.assertFalse(container.seeked)
        self.assertEqual(container.decode_calls, 0)
        self.assertTrue(container.closed)

    def test_cancel_after_seek_avoids_decode_and_closes(self):
        container = FakeContainer([FakeFrame(2000)])
        with patch("av.open", return_value=container):
            with self.assertRaises(InterruptedError):
                self.video.at(2, cancel=lambda: container.seeked)
        self.assertTrue(container.seeked)
        self.assertEqual(container.decode_calls, 0)
        self.assertTrue(container.closed)

    def test_cancel_during_keyframe_preroll_skips_remaining_frames(self):
        frames = [FakeFrame(i * 100) for i in range(30)]
        container = FakeContainer(frames)
        with patch("av.open", return_value=container):
            with self.assertRaises(InterruptedError):
                self.video.at(2, cancel=lambda: container.decoded >= 2)
        self.assertEqual(container.decoded, 2)
        self.assertEqual(sum(frame.conversions for frame in frames), 0)
        self.assertTrue(container.closed)

    def test_cancel_during_conversion_does_not_publish_frame(self):
        cancelled = []
        frame = FakeFrame(2000, on_convert=lambda: cancelled.append(True))
        container = FakeContainer([frame])
        with patch("av.open", return_value=container):
            with self.assertRaises(InterruptedError):
                self.video.at(2, cancel=lambda: bool(cancelled))
        self.assertEqual(frame.conversions, 1)
        self.assertTrue(container.closed)

    def test_success_closes_decoder_before_return_without_changing_pts(self):
        # Intentionally irregular PTS: precision must not use average frame rate.
        frames = [FakeFrame(0), FakeFrame(33), FakeFrame(81), FakeFrame(105)]
        container = FakeContainer(frames)
        with patch("av.open", return_value=container):
            stamp, image = self.video.at(.04)
        self.assertEqual(stamp, .081)
        self.assertIs(image, frames[2].image)
        self.assertEqual(container.decoded, 3)
        self.assertTrue(container.closed)

    def test_missing_pts_still_raises_and_closes_decoder(self):
        container = FakeContainer([FakeFrame(None)])
        with patch("av.open", return_value=container):
            with self.assertRaisesRegex(ValueError, "视频帧缺少时间戳"):
                self.video.at(0)
        self.assertTrue(container.closed)

    def test_empty_decode_keeps_existing_fallback_and_closes(self):
        container = FakeContainer()
        with patch("av.open", return_value=container):
            self.assertEqual(self.video.at(0), (0, None))
        self.assertTrue(container.closed)

    def test_old_tail_clamp_is_unchanged(self):
        frames = [FakeFrame(2900), FakeFrame(2950), FakeFrame(2990)]
        container = FakeContainer(frames)
        with patch("av.open", return_value=container):
            stamp, image = self.video.at(3)
        self.assertEqual(stamp, 2.95)
        self.assertIs(image, frames[1].image)
        self.assertTrue(container.closed)

    def test_explicit_last_pts_restores_tail_frame_and_closes(self):
        frames = [FakeFrame(2900), FakeFrame(2950), FakeFrame(2990)]
        container = FakeContainer(frames)
        with patch("av.open", return_value=container):
            stamp, image = self.video.at(3, last_pts=2.99)
        self.assertEqual(stamp, 2.99)
        self.assertIs(image, frames[-1].image)
        self.assertTrue(container.closed)

    def test_zero_last_pts_is_not_treated_as_missing(self):
        frame = FakeFrame(0)
        container = FakeContainer([frame])
        with patch("av.open", return_value=container):
            self.assertEqual(self.video.at(3, last_pts=0), (0, frame.image))
        self.assertFalse(container.seeked)
        self.assertTrue(container.closed)

    def test_indexed_cancel_never_opens_file(self):
        with patch("av.open") as opened:
            with self.assertRaises(InterruptedError):
                self.video.at(3, cancel=lambda: True, last_pts=2.99)
        opened.assert_not_called()


class SyntheticVideoSeekTests(unittest.TestCase):
    def test_in_flight_cancellation_leaves_next_precise_seek_unchanged(self):
        examples = Path(__file__).resolve().parents[1] / "examples"
        for filename in ("synthetic_mv.mp4", "synthetic_burned_zh.mp4"):
            with self.subTest(filename=filename):
                video = Video(examples / filename)
                expected_stamp, expected_image = video.at(11.21)
                checks = []

                def cancelled():
                    checks.append(True)
                    # The first three checks precede decode; cancel only
                    # after the decoder has begun keyframe preroll.
                    return len(checks) >= 5

                with self.assertRaises(InterruptedError):
                    video.at(11.21, cancel=cancelled)
                self.assertEqual(len(checks), 5)
                actual_stamp, actual_image = video.at(11.21)
                self.assertEqual(actual_stamp, expected_stamp)
                self.assertTrue(np.array_equal(actual_image, expected_image))

    def test_forward_backward_and_cancelled_seeks_preserve_pts_and_pixels(self):
        examples = Path(__file__).resolve().parents[1] / "examples"
        for filename in ("synthetic_mv.mp4", "synthetic_burned_zh.mp4"):
            with self.subTest(filename=filename):
                video = Video(examples / filename)
                index = FrameIndex.build(video)
                # Sequential decoding is independent of each preview seek.
                targets = (0, .01, 1, 2.13, 11.21, 14.9, 3.25, -.5, 15.5)
                expected_stamps = {
                    target: next(stamp for stamp in index.stamps
                                 if stamp >= max(0, min(target, video.info["duration"] - .05)) - 1e-6)
                    for target in targets
                }
                expected_images = {stamp: image for stamp, image in video.frames()
                                   if stamp in expected_stamps.values()}
                for target in targets:
                    with self.assertRaises(InterruptedError):
                        video.at(target, cancel=lambda: True)
                    stamp, image = video.at(target)
                    self.assertEqual(stamp, expected_stamps[target])
                    self.assertTrue(np.array_equal(image, expected_images[stamp]))
                    stamp_with_callback, image_with_callback = video.at(target, cancel=lambda: False)
                    self.assertEqual(stamp_with_callback, stamp)
                    self.assertTrue(np.array_equal(image_with_callback, image))


class IndexedTailSeekTests(unittest.TestCase):
    """Small generated media reproduces the old 50ms guard's tail-frame loss."""

    @classmethod
    def setUpClass(cls):
        import av
        folder = tempfile.TemporaryDirectory(prefix="lyria-test-indexed-tail-")
        cls.addClassCleanup(folder.cleanup)
        cls.fixtures = {}
        definitions = (
            ("60fps", range(60), 60, Fraction(1, 60)),
            ("vfr", (0, 200, 400, 600, 800, 940, 960, 980), 50, Fraction(1, 1000)),
        )
        for name, pts, rate, time_base in definitions:
            path = Path(folder.name) / (name + ".mp4")
            with av.open(str(path), mode="w") as output:
                stream = output.add_stream("mpeg4", rate=rate)
                stream.width = stream.height = 32
                stream.pix_fmt = "yuv420p"
                stream.time_base = stream.codec_context.time_base = time_base
                stream.codec_context.max_b_frames = 0
                for number, stamp in enumerate(pts):
                    image = np.full((32, 32, 3), number * 3, np.uint8)
                    frame = av.VideoFrame.from_ndarray(image, format="rgb24")
                    frame.pts, frame.time_base = stamp, time_base
                    for packet in stream.encode(frame):
                        output.mux(packet)
                for packet in stream.encode():
                    output.mux(packet)
            video = Video(path)
            index = FrameIndex.build(video)
            images = dict(video.frames())
            cls.fixtures[name] = video, index, images

    def check_tail(self, name):
        video, index, images = self.fixtures[name]
        last_pts = index.stamps[-1]
        # This is an existing defect only in the no-index fallback. Keep that
        # default stable while proving the indexed API no longer loses frames.
        self.assertLess(video.at(last_pts)[0], last_pts)
        for target in index.stamps[-3:]:
            with self.subTest(target=target):
                actual, image = video.at(target, last_pts=last_pts)
                self.assertEqual(actual, target)
                self.assertTrue(np.array_equal(image, images[target]))
        for target in (video.info["duration"], video.info["duration"] + 10):
            with self.subTest(target=target):
                actual, image = video.at(target, last_pts=last_pts)
                self.assertEqual(actual, last_pts)
                self.assertTrue(np.array_equal(image, images[last_pts]))

    def test_60fps_index_preserves_each_tail_frame_and_last_target(self):
        self.assertEqual(len(self.fixtures["60fps"][1].stamps), 60)
        self.check_tail("60fps")

    def test_vfr_index_preserves_tail_despite_low_average_fps(self):
        video, index, _ = self.fixtures["vfr"]
        self.assertEqual(video.info["fps"], 8)
        self.assertEqual(index.stamps[-3:], [.94, .96, .98])
        self.check_tail("vfr")

    def test_indexed_inflight_cancel_then_final_target_preserves_pts_and_pixels(self):
        for name, (video, index, images) in self.fixtures.items():
            with self.subTest(name=name):
                checks = []

                def cancelled():
                    checks.append(True)
                    return len(checks) >= 5

                last_pts = index.stamps[-1]
                with self.assertRaises(InterruptedError):
                    video.at(last_pts, cancel=cancelled, last_pts=last_pts)
                actual, image = video.at(last_pts, cancel=lambda: False, last_pts=last_pts)
                self.assertEqual(actual, last_pts)
                self.assertTrue(np.array_equal(image, images[last_pts]))


if __name__ == "__main__":
    unittest.main()
