"""Actual UI queue dispatch with a headless host, not a Windows FPS benchmark."""
from types import SimpleNamespace
import queue
import unittest
from unittest.mock import Mock, patch

from lyricsync.gui import Application


class Value:
    def __init__(self):
        self.values = []
    def set(self, value):
        self.values.append(value)


class PollHost:
    poll = Application.poll
    def __init__(self):
        self.jobs = queue.Queue()
        self.status = Value()
        self.bar = {}
        self.audit_panel = SimpleNamespace(bar={})
        self.root = SimpleNamespace(after=Mock(), destroy=Mock())
        self.error = Mock()
        self.busy = True
        self.close_requested = False
        self.save_revision = 2
        self.save_status = Value()
        self.write_log = Mock()
        self.project_saver = SimpleNamespace(close=Mock())


class MainThreadBudgetTests(unittest.TestCase):
    def test_backlog_yields_after_64_messages_and_coalesces_progress(self):
        host = PollHost()
        for i in range(10_000):host.jobs.put(('progress', i / 10_000, str(i)))
        with patch('lyricsync.gui.time.perf_counter', return_value=0):host.poll()
        self.assertEqual(host.jobs.qsize(), 10_000 - 64)
        self.assertEqual(host.status.values, ['63'])
        self.assertEqual(host.bar, host.audit_panel.bar)
        host.root.after.assert_called_once_with(8, host.poll)
        host.error.assert_not_called()

    def test_elapsed_budget_yields_even_when_count_limit_not_reached(self):
        host = PollHost()
        for i in range(5):host.jobs.put(('saved', 2, 'ignored', None))
        with patch('lyricsync.gui.time.perf_counter', side_effect=[0, .003, .005]):host.poll()
        self.assertEqual(host.jobs.qsize(), 3)
        self.assertEqual(len(host.save_status.values), 2)
        host.root.after.assert_called_once_with(8, host.poll)

    def test_progress_order_is_preserved_before_completion(self):
        host = PollHost()
        def finish(value):
            self.assertEqual(host.status.values, ['latest'])
            self.assertEqual(value, 42)
            host.status.set('finished')
        host.jobs.put(('progress', .2, 'old'))
        host.jobs.put(('progress', .9, 'latest'))
        host.jobs.put(('done', finish, 42))
        with patch('lyricsync.gui.time.perf_counter', return_value=0):host.poll()
        self.assertEqual(host.status.values, ['latest', 'finished'])
        self.assertFalse(host.busy)
        host.root.after.assert_called_once_with(16, host.poll)

    def test_progress_on_each_side_of_other_message_is_not_reordered(self):
        host = PollHost()
        host.jobs.put(('progress', .1, 'first'))
        host.jobs.put(('error', 'worker error'))
        host.jobs.put(('progress', .2, 'second'))
        host.error.side_effect = lambda _: host.status.set('error')
        with patch('lyricsync.gui.time.perf_counter', return_value=0):host.poll()
        self.assertEqual(host.status.values, ['first', 'error', 'second'])
        host.error.assert_called_once_with('worker error')

    def test_completion_is_eventually_delivered_once_after_backlog(self):
        host = PollHost()
        done = Mock()
        for i in range(130):host.jobs.put(('progress', .1, str(i)))
        host.jobs.put(('done', done, 'result'))
        with patch('lyricsync.gui.time.perf_counter', return_value=0):
            host.poll();host.poll();host.poll()
        done.assert_called_once_with('result')
        self.assertEqual(host.status.values, ['63', '127', '129'])
        self.assertTrue(host.jobs.empty())

    def test_empty_queue_schedules_one_poll_without_busy_spin(self):
        host = PollHost()
        host.poll()
        host.root.after.assert_called_once_with(16, host.poll)
        self.assertEqual(host.status.values, [])

    def test_requested_close_after_completion_does_not_reschedule(self):
        host = PollHost()
        host.close_requested = True
        done = Mock()
        host.jobs.put(('progress', 1, 'complete'))
        host.jobs.put(('done', done, 'result'))
        host.poll()
        done.assert_called_once_with('result')
        host.project_saver.close.assert_called_once()
        host.root.destroy.assert_called_once()
        host.root.after.assert_not_called()


if __name__ == '__main__':unittest.main()


class PreviewQueueTests(unittest.TestCase):
    def host(self, scrubbing=True):
        host = PollHost()
        host.seek_epoch = 2
        host.preview_video = object()
        host.seek_generation = 40
        host.seek_presented = 0
        host.scrubbing = scrubbing
        host.sync_playhead = Mock()
        host.draw = Mock()
        return host

    def enqueue(self, host, generation, video=None, epoch=2):
        host.jobs.put(('preview_frame', generation, video or host.preview_video,
                       (generation / 10, f'frame {generation}'), True, epoch))

    def test_40_pending_scrub_frames_paint_only_latest(self):
        host = self.host()
        for i in range(1, 41):self.enqueue(host, i)
        with patch('lyricsync.gui.time.perf_counter', return_value=0):host.poll()
        self.assertEqual((host.seek_presented, host.stamp, host.frame), (40, 4, 'frame 40'))
        host.draw.assert_called_once()
        host.sync_playhead.assert_called_once_with(True)

    def test_older_out_of_order_or_invalid_video_epoch_cannot_replace_latest(self):
        host = self.host()
        self.enqueue(host, 35);self.enqueue(host, 30)
        self.enqueue(host, 40, epoch=1)
        self.enqueue(host, 40, video=object())
        with patch('lyricsync.gui.time.perf_counter', return_value=0):host.poll()
        self.assertEqual(host.seek_presented, 35)
        host.draw.assert_called_once()

    def test_released_seek_only_presents_exact_final_generation(self):
        host = self.host(scrubbing=False)
        for i in range(1, 40):self.enqueue(host, i)
        with patch('lyricsync.gui.time.perf_counter', return_value=0):host.poll()
        host.draw.assert_not_called()
        self.enqueue(host, 40)
        host.poll()
        self.assertEqual(host.stamp, 4)
        host.draw.assert_called_once()

    def test_frame_is_presented_before_terminal_callback_replaces_video(self):
        host = self.host()
        self.enqueue(host, 39)
        def done(_):
            self.assertEqual(host.seek_presented, 39)
            host.preview_video = object()
        host.jobs.put(('done', done, None))
        self.enqueue(host, 40, video=object())
        with patch('lyricsync.gui.time.perf_counter', return_value=0):host.poll()
        host.draw.assert_called_once()
        self.assertEqual(host.seek_presented, 39)
