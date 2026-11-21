"""Threaded actual seek dispatch without Tk, using controllable decoder barriers."""
import queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from lyricsync.gui import Application


class SeekDispatchTests(unittest.TestCase):
    def setUp(self):
        self.started=threading.Event();self.release=threading.Event()
        self.calls=[];self.cancellations=[];self.threads=[];self.last_limits=[]
        self.fail_stale=False
        def at(position,cancel=lambda:False,last_pts=None):
            self.calls.append(position);self.last_limits.append(last_pts)
            if position==1:
                self.started.set()
                if not self.release.wait(2):raise AssertionError('test barrier timed out')
                self.cancellations.append(cancel())
                if self.fail_stale:raise ValueError('stale decoder failure')
            if cancel():raise InterruptedError('cancelled')
            return (position, f'frame {position}')
        self.host=SimpleNamespace(pause_playback=Mock(),seek_pending=None,
            preview_video=SimpleNamespace(at=at),scrubbing=False,seek_lock=threading.Lock(),
            seek_generation=0,seek_epoch=0,seek_request=None,seek_worker_active=False,
            close_requested=False,jobs=queue.Queue())
        self.real_thread=threading.Thread
        def start_thread(*args,**kwargs):
            thread=self.real_thread(*args,**kwargs);self.threads.append(thread);return thread
        self.patcher=patch('lyricsync.gui.threading.Thread',side_effect=start_thread)
        self.patcher.start();self.addCleanup(self.patcher.stop)
        self.addCleanup(self.finish)

    def request(self,position,intermediate=True):
        Application.request_seek(self.host,position,intermediate=intermediate)

    def finish(self):
        self.release.set()
        for thread in self.threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())

    def messages(self):
        rows=[]
        while not self.host.jobs.empty():rows.append(self.host.jobs.get_nowait())
        return rows

    def test_fast_intermediate_requests_do_not_starve_inflight_preview(self):
        self.request(1);self.assertTrue(self.started.wait(1))
        self.request(2);self.request(3)
        self.finish()
        self.assertEqual(self.calls,[1,3])
        self.assertEqual(self.cancellations,[False])
        self.assertEqual([row[3][0] for row in self.messages()],[1,3])

    def test_final_release_cancels_old_decode_and_publishes_exact_target_only(self):
        self.request(1);self.assertTrue(self.started.wait(1))
        self.request(2);self.request(3.125,intermediate=False)
        self.finish()
        self.assertEqual(self.calls,[1,3.125])
        self.assertEqual(self.cancellations,[True])
        rows=self.messages()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0][3][0],3.125)
        self.assertEqual(rows[0][1],self.host.seek_generation)

    def test_replaced_context_cancels_without_stale_error_or_frame(self):
        for attribute,value in (('seek_epoch',1),('close_requested',True)):
            with self.subTest(attribute=attribute):
                self.calls.clear();self.cancellations.clear();self.started.clear();self.release.clear()
                self.host.seek_epoch=0;self.host.close_requested=False
                self.request(1);self.assertTrue(self.started.wait(1))
                setattr(self.host,attribute,value)
                self.finish()
                self.assertEqual(self.cancellations,[True])
                self.assertEqual(self.messages(),[])

    def test_stale_error_is_suppressed_but_current_error_remains_observable(self):
        self.fail_stale=True
        self.request(1);self.assertTrue(self.started.wait(1))
        self.request(2,intermediate=False);self.finish()
        self.assertEqual([row[0] for row in self.messages()],['preview_frame'])
        self.host.preview_video=SimpleNamespace(at=Mock(side_effect=ValueError('current failure')))
        self.request(4,intermediate=False);self.finish()
        rows=self.messages()
        self.assertEqual(rows[0][0],'preview_error')
        self.assertEqual(rows[0][2],'current failure')

    def test_worker_captures_real_last_pts_with_its_request(self):
        self.host.frame_index=SimpleNamespace(stamps=[0,.9833333333333333])
        self.request(1);self.assertTrue(self.started.wait(1))
        self.host.frame_index=SimpleNamespace(stamps=[0,2.98])
        self.request(2,intermediate=False);self.finish()
        self.assertEqual(self.last_limits,[.9833333333333333,2.98])
