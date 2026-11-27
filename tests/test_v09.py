"""Player responsiveness tests through actual Tk callbacks, with controllable decoding."""
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch
from lyricsync.gui import Application
from lyricsync.vision import Video
from lyricsync.frames import FrameIndex
from lyricsync.playback import PlaybackBuffer
from lyricsync.model import Translation,Mapping


class PlayerTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.root=tk.Tk();self.root.withdraw()
        self.app=Application(self.root,self.folder.name)
        self.app.preview_video=Video(Path(__file__).resolve().parents[1]/'examples/synthetic_mv.mp4')
        self.app.frame_index=FrameIndex([i*.05 for i in range(300)],15)
        self.app.timeline.configure(to=15)
        self.app.sound_enabled.set(False)
        self.app.seek(1)

    def tearDown(self):
        for hold in self.app.preview_holds:hold.stop()
        self.app.pause_playback()
        self.app.project_saver.close()
        from lyricsync.tk_lifecycle import destroy_root
        destroy_root(self.root);self.folder.cleanup()

    def test_drag_requests_frames_before_mouse_stops(self):
        requests=[]
        with patch.object(self.app,'request_seek',side_effect=lambda *a,**k:requests.append(a[0])):
            for n in range(12):self.root.after(n*20,lambda n=n:self.app.seek_changed(str(1+n*.1)))
            self.root.after(170,self.root.quit);self.root.mainloop()
        self.assertGreater(len(requests),0,'continuous dragging must preview before release')

    def test_drag_thumb_is_not_pulled_back_by_older_decoded_frame(self):
        # Exercise the actual scale callbacks while an earlier decode is in flight.
        started=threading.Event();release=threading.Event();actual=self.app.preview_video.at
        def slow(stamp, **kwargs):
            if stamp==2:started.set();release.wait(2)
            return actual(stamp, **kwargs)
        with patch.object(self.app.preview_video,'at',side_effect=slow):
            self.app.timeline.event_generate('<ButtonPress-1>',x=1,y=1)
            self.app.seek_changed('2');self.assertTrue(started.wait(1))
            self.app.updating_timeline=True;self.app.timeline.set(3);self.app.updating_timeline=False
            self.app.seek_changed('3');release.set()
            # Old displayed PTS must not overwrite the user's thumb position.
            self.app.stamp=2;self.app.sync_playhead()
            self.assertEqual(float(self.app.timeline.get()),3)
            self.app.timeline.event_generate('<ButtonRelease-1>',x=1,y=1)
            self.root.after(250,self.root.quit);self.root.mainloop()
        self.assertAlmostEqual(self.app.stamp,3)
        self.assertAlmostEqual(float(self.app.timeline.get()),3)

    def test_decoder_never_runs_on_tk_thread_during_play(self):
        main=threading.get_ident();threads=[]
        actual=self.app.preview_video.frames
        def frames(*args,**kwargs):
            threads.append(threading.get_ident())
            yield from actual(*args,**kwargs)
        with patch.object(self.app.preview_video,'frames',side_effect=frames):
            self.app.toggle_play()
            self.root.after(180,self.root.quit);self.root.mainloop()
            self.app.pause_playback()
        self.assertTrue(threads)
        self.assertNotIn(main,threads,'decoding must not block UI events')

    def test_slow_drag_shows_intermediate_frames_without_waiting_for_release(self):
        actual=self.app.preview_video.at
        shown=[]
        def slow(stamp, **kwargs):time.sleep(.045);return actual(stamp, **kwargs)
        with patch.object(self.app.preview_video,'at',side_effect=slow),patch.object(self.app,'draw',side_effect=lambda:shown.append(self.app.stamp)):
            for n in range(20):self.root.after(n*15,lambda n=n:self.app.seek_changed(str(2+n*.05)))
            self.root.after(220,self.root.quit);self.root.mainloop()
            self.assertTrue(any(x>=2 for x in shown),'dragging faster than decoder must not starve preview')
            self.root.after(240,self.root.quit);self.root.mainloop()
        self.assertAlmostEqual(self.app.stamp,2.95)

    def test_pause_during_slow_decode_returns_without_waiting(self):
        original=self.app.preview_video.frames
        started=threading.Event();release=threading.Event()
        def slow(*args,**kwargs):
            started.set();release.wait(2);yield from original(*args,**kwargs)
        with patch.object(self.app.preview_video,'frames',side_effect=slow):
            self.app.toggle_play();self.assertTrue(started.wait(.5))
            player=self.app.player
            tick=time.perf_counter();self.app.pause_playback()
            self.assertLess(time.perf_counter()-tick,.1)
            release.set();player.thread.join(2)
            self.assertFalse(player.thread.is_alive())

    def test_five_frame_step_uses_actual_index(self):
        self.app.step(5)
        self.root.after(250,self.root.quit);self.root.mainloop()
        self.assertAlmostEqual(self.app.stamp,1.25)
        self.app.step(-5)
        self.root.after(250,self.root.quit);self.root.mainloop()
        self.assertAlmostEqual(self.app.stamp,1)

    def test_fast_steps_accumulate_while_decoder_is_busy(self):
        actual=self.app.preview_video.at
        def slow(stamp, **kwargs):time.sleep(.04);return actual(stamp, **kwargs)
        with patch.object(self.app.preview_video,'at',side_effect=slow):
            for delta in (5,5,1,-1,5):self.assertTrue(self.app.request_step(delta))
            self.root.after(280,self.root.quit);self.root.mainloop()
        self.assertAlmostEqual(self.app.stamp,1.75)
        # A separate seek starts a new series; it must not reuse the old target.
        self.app.seek(3);self.app.request_step(-5)
        self.root.after(200,self.root.quit);self.root.mainloop()
        self.assertAlmostEqual(self.app.stamp,2.75)

    def test_hold_repeats_then_release_stops_without_edit_history(self):
        hold=self.app.preview_holds[-1];moves=[]
        with patch.object(self.app,'request_step',side_effect=lambda delta:moves.append(delta) or True):
            hold.start()
            self.root.after(460,hold.stop)
            self.root.after(500,self.root.quit);self.root.mainloop()
            count=len(moves)
            self.root.after(150,self.root.quit);self.root.mainloop()
        self.assertGreaterEqual(count,3)
        self.assertEqual(moves,[5]*count)
        self.assertEqual(len(self.app.history.undo_stack),0)
        self.assertIsNone(hold.timer)

    def test_hold_stops_on_video_change(self):
        hold=self.app.preview_holds[1];moves=[]
        with patch.object(self.app,'request_step',side_effect=lambda delta:moves.append(delta) or True):
            hold.start();self.app.preview_video=None
            self.root.after(390,self.root.quit);self.root.mainloop()
        self.assertEqual(moves,[-1]);self.assertFalse(hold.active)

    def test_hold_stops_when_window_loses_focus(self):
        hold=self.app.preview_holds[2];moves=[]
        with patch.object(self.app,'request_step',side_effect=lambda delta:moves.append(delta) or True):
            self.root.update_idletasks()
            hold.start();self.root.event_generate('<FocusOut>',when='now')
            self.root.after(400,self.root.quit);self.root.mainloop()
        self.assertEqual(moves,[1]);self.assertFalse(hold.active)

    def test_last_short_cue_does_not_restart_from_video_beginning(self):
        self.app.seek(14.95)
        self.app.toggle_play(stop_at=15)
        self.root.after(180,self.root.quit);self.root.mainloop()
        self.assertAlmostEqual(self.app.stamp,14.95)
        self.assertFalse(self.app.playing)

    def test_preview_layout_cache_tracks_text_time_and_style_edits(self):
        from lyricsync.export import layout_entries
        self.app.project.translations=[Translation('t','第一句')]
        self.app.project.mappings=[Mapping('m',['t'],start=1,end=2)]
        self.app.preview_chinese.set(True)
        with patch('lyricsync.gui.layout_entries',wraps=layout_entries) as layout:
            self.app.draw();self.app.draw();self.assertEqual(layout.call_count,1)
            self.app.project.translations[0].text='修改后'
            self.app.draw();self.assertEqual(layout.call_count,2)
            self.app.project.mappings[0].end=3
            self.app.draw();self.assertEqual(layout.call_count,3)
            self.app.project.settings['font_size']=32
            self.app.draw();self.assertEqual(layout.call_count,4)

    def test_cancelled_drag_cannot_overwrite_new_explicit_seek(self):
        started=threading.Event();release=threading.Event();actual=self.app.preview_video.at
        def slow(stamp, **kwargs):
            if stamp==2:started.set();release.wait(2)
            return actual(stamp, **kwargs)
        with patch.object(self.app.preview_video,'at',side_effect=slow):
            self.app.request_seek(2,intermediate=True);self.assertTrue(started.wait(1))
            self.app.seek(4);release.set()
            self.root.after(180,self.root.quit);self.root.mainloop()
        self.assertAlmostEqual(self.app.stamp,4)


class BufferTests(unittest.TestCase):
    def test_buffer_is_bounded_and_worker_exits_when_full_and_cancelled(self):
        class Video:
            def frames(self,*args,**kwargs):
                for i in range(10000):yield i*.04,object()
        player=PlaybackBuffer(Video(),0,capacity=3)
        deadline=time.monotonic()+1
        while player.max_buffered<3 and time.monotonic()<deadline:time.sleep(.002)
        self.assertEqual(player.max_buffered,3)
        player.stop();player.thread.join(1)
        self.assertFalse(player.thread.is_alive())

    def test_late_ui_shows_latest_due_frame_and_keeps_future_frame(self):
        class Video:
            def frames(self,*args,**kwargs):
                for stamp in (0,.033,.081,.105,.18):yield stamp,stamp
        player=PlaybackBuffer(Video(),0,capacity=6)
        player.thread.join(1)
        self.assertEqual(player.take_due(.11)[0],.105)
        self.assertEqual(player.peek()[0],.18)
        self.assertEqual(player.skipped,3)
        self.assertFalse(player.finished)
        self.assertEqual(player.take_due(.2)[0],.18)
        self.assertTrue(player.finished)


if __name__=='__main__':unittest.main()
