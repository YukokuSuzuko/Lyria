"""User-facing review regressions with real Tk and decoded frame timestamps."""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch
from lyricsync.gui import Application
from lyricsync.model import Mapping, Translation
from lyricsync.frames import FrameIndex
from lyricsync.vision import Video
from lyricsync.workspace import overlap_ids,describe_suggestions
from lyricsync.export import export_project
from lyricsync.model import Lyric,Visual,Project
from lyricsync.outline_assist import outline_boundary,unreliable_ids
from lyricsync.storage import Cache
import json
import threading
import time


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.root=tk.Tk();self.root.withdraw()
        self.callback_errors=[]
        self.root.report_callback_exception=lambda kind,error,trace:self.callback_errors.append(str(error))
        self.app=Application(self.root,self.folder.name)
        self.app.preview_video=Video(Path(__file__).resolve().parents[1]/'examples/synthetic_mv.mp4')
        self.app.timeline.configure(to=15)
        self.app.frame_index=FrameIndex([i*.05 for i in range(300)],15)
        self.app.project.translations=[Translation('a','第一句'),Translation('b','第二句')]
        self.app.project.mappings=[Mapping('a',['a'],start=1,end=2),Mapping('b',['b'],start=3,end=4)]
        self.app.refresh()

    def tearDown(self):
        self.app.project_saver.close()
        self.app.pause_playback()
        from lyricsync.tk_lifecycle import destroy_root
        destroy_root(self.root);self.folder.cleanup()
        self.assertEqual(self.callback_errors,[])

    def test_seek_selects_current_lyric_and_updates_slider(self):
        self.app.seek(3.25)
        self.assertEqual(self.app.results.selection(),('b',))
        self.assertAlmostEqual(self.app.timeline.get(),self.app.stamp)
        self.root.update()
        self.assertAlmostEqual(self.app.stamp,3.25)  # no selection -> seek feedback loop

    def test_slow_seek_keeps_ui_responsive_and_only_latest_result_is_used(self):
        started=threading.Event();release=threading.Event();done=threading.Event()
        calls=[];at=self.app.preview_video.at;heartbeat=[]
        def slow_at(stamp, **kwargs):
            calls.append(stamp)
            if stamp==1:
                started.set();release.wait(2)
            result=at(stamp, **kwargs)
            if stamp==3.25:done.set()
            return result
        with patch.object(self.app.preview_video,'at',side_effect=slow_at):
            self.app.request_seek(1);self.assertTrue(started.wait(1))
            self.root.after_idle(lambda:heartbeat.append(True));self.root.update()
            self.assertEqual(heartbeat,[True])
            self.app.request_seek(2);self.app.request_seek(3.25)
            release.set();self.assertTrue(done.wait(2))
            deadline=time.monotonic()+2
            while self.app.seek_worker_active and time.monotonic()<deadline:time.sleep(.005)
            self.assertFalse(self.app.seek_worker_active)
            self.app.poll();self.root.update()
        self.assertEqual(calls,[1,3.25])
        self.assertAlmostEqual(self.app.stamp,3.25)
        self.assertEqual(self.app.selected_mapping().id,'b')

    def test_api_settings_is_single_instance(self):
        with patch('lyricsync.key_widget.KeyStore.get',return_value=''):
            self.app.ai_settings_button.invoke();self.app.api_settings()
        windows=[w for w in self.root.winfo_children() if isinstance(w,tk.Toplevel) and w.title()=='AI 辅助设置']
        self.assertEqual(len(windows),1)

    def select(self,identity):
        self.app.results.selection_set(identity);self.app.select_mapping()

    def test_nudge_only_moves_selected_boundary_and_preserves_selection(self):
        self.select('a');before=self.app.project.to_dict()
        self.app.nudge_boundary(False,1)
        self.assertEqual(self.app.first_frame.get(),'22');self.assertEqual(self.app.last_frame.get(),'40')
        self.assertAlmostEqual(self.app.project.mappings[0].start,1.05)
        self.assertFalse(self.app.project.mappings[0].confirmed)
        self.root.after(180,self.root.quit);self.root.mainloop()
        self.assertAlmostEqual(self.app.stamp,1.05);self.assertEqual(self.app.selected_mapping().id,'a')
        self.app.nudge_boundary(True,-1)
        self.assertEqual(self.app.first_frame.get(),'22');self.assertEqual(self.app.last_frame.get(),'39')
        self.assertTrue(self.app.confirm_mapping());self.assertAlmostEqual(self.app.project.mappings[0].end,1.95)

    def test_boundary_edit_commits_without_waiting_for_video_decode(self):
        self.select('a');started=threading.Event();release=threading.Event()
        actual=self.app.preview_video.at
        def slow(stamp, **kwargs):
            started.set();release.wait(2);return actual(stamp, **kwargs)
        with patch.object(self.app.preview_video,'at',side_effect=slow):
            begin=time.perf_counter();self.app.nudge_boundary(False,1)
            self.assertLess(time.perf_counter()-begin,.15)
            self.assertAlmostEqual(self.app.project.mappings[0].start,1.05)
            self.assertTrue(started.wait(1));release.set()
            self.root.after(180,self.root.quit);self.root.mainloop()
        self.assertAlmostEqual(self.app.stamp,1.05)

    def test_invalid_nudge_leaves_form_unchanged(self):
        self.select('a');self.app.first_frame.set('40');self.app.last_frame.set('40')
        with patch('lyricsync.workspace.messagebox.showwarning') as warning:self.app.nudge_boundary(False,1)
        self.assertEqual(self.app.first_frame.get(),'40');warning.assert_called_once()

    def test_playhead_changes_do_not_discard_unconfirmed_input(self):
        self.select('a');self.app.first_frame.set('23')
        self.app.seek(3.25);self.assertEqual(self.app.selected_mapping().id,'b')
        self.app.seek(1.3);self.assertEqual(self.app.first_frame.get(),'23')
        self.assertFalse(self.app.project.mappings[0].confirmed)

    def test_overlap_marks_both_rows_but_not_adjacent_frames(self):
        self.app.project.mappings[1].start=2
        self.assertEqual(overlap_ids(self.app.project.mappings,self.app.frame_index),set())
        self.app.project.mappings[1].start=1.95;self.app.refresh()
        self.assertEqual(overlap_ids(self.app.project.mappings,self.app.frame_index),{'a','b'})
        self.app.seek(1.95)
        for identity in ('a','b'):self.assertIn('overlap',self.app.results.item(identity,'tags'))
        self.assertIn('2 条',self.app.current_hint.get())

    def test_empty_interval_and_filtered_current_cue_have_explicit_status(self):
        self.app.seek(2.5);self.assertEqual(self.app.current_hint.get(),'当前没有字幕')
        m=self.app.project.mappings[1];m.locked=m.confirmed=True
        self.app.only_issues.set(True);self.app.refresh();self.app.seek(3.25)
        self.assertIn('筛选隐藏',self.app.current_hint.get())

    def test_partial_export_requires_opt_in_reports_omissions_and_preserves_project(self):
        self.app.project.mappings[1].start=None
        before=self.app.project.to_dict();target=Path(self.folder.name)/'partial.ass'
        with self.assertRaises(ValueError):export_project(self.app.project,target,draft=True)
        omitted=export_project(self.app.project,target,skip_missing_times=True)
        self.assertEqual(len(omitted),1)
        body=target.read_text(encoding='utf-8-sig')
        self.assertIn('第一句',body);self.assertNotIn('第二句',body)
        report=json.loads(target.with_name('partial_遗漏清单.json').read_text(encoding='utf-8'))
        self.assertEqual(report['omitted'][0]['text'],'第二句')
        self.assertEqual(before,self.app.project.to_dict())

    def test_partial_export_cannot_bypass_corrupt_references(self):
        self.app.project.mappings[1].start=None
        self.app.project.mappings[0].translation_ids=['missing']
        with self.assertRaises(ValueError):export_project(self.app.project,Path(self.folder.name)/'bad.ass',skip_missing_times=True)

    def test_partial_export_dialog_is_single_and_uses_actual_lyrics(self):
        self.app.project.mappings[1].start=None
        self.app.export(True);first=self.app.windows['partial_export'];self.app.export(True)
        self.assertIs(first,self.app.windows['partial_export'])
        text=next(w for w in first.winfo_children() if isinstance(w,tk.Text))
        self.assertIn('第二句',text.get('1.0','end'))

    def test_previous_and_redo_restore_confirmed_change(self):
        self.select('b');self.app.previous_mapping();self.assertEqual(self.app.selected_mapping().id,'a')
        self.app.first_frame.set('24');self.app.confirm_mapping()
        self.app.undo();self.assertAlmostEqual(self.app.project.mappings[0].start,1.15)
        self.assertFalse(self.app.project.mappings[0].confirmed)
        self.app.undo();self.assertEqual(self.app.project.mappings[0].start,1)
        self.app.undo(True);self.assertAlmostEqual(self.app.project.mappings[0].start,1.15)

    def test_confirm_next_visits_already_confirmed_cue(self):
        self.app.project.mappings[1].confirmed=True
        self.app.project.mappings[1].locked=True
        self.select('a');self.app.confirm_next()
        self.assertEqual(self.app.selected_mapping().id,'b')
        self.assertFalse(self.app.project.unresolved())

    def test_confirm_next_selects_pending_cue(self):
        self.select('a');self.app.confirm_next()
        self.assertEqual(self.app.selected_mapping().id,'b')
        self.assertTrue(self.app.project.mappings[0].confirmed)
        self.assertFalse(self.app.project.mappings[1].confirmed)

    def test_ai_suggestions_show_text_not_internal_ids(self):
        self.app.project.lyrics=[Lyric('lyric_internal','HELLO',1,2)]
        self.app.project.settings['api_suggestions']=[{'mapping_id':'a','suggestion':{'mappings':[{
            'lyric_ids':['lyric_internal'],'translation_ids':['a'],'confidence':.7,'reason':'文字对应'}],'corrections':[]}}]
        result=describe_suggestions(self.app.project,self.app.project.mappings[0])
        self.assertIn('HELLO',result);self.assertIn('第一句',result);self.assertNotIn('lyric_internal',result)

    def test_ai_switch_and_settings_share_state(self):
        self.app.ai_enabled.set(True);self.app.ai_toggle()
        self.assertTrue(self.app.project.settings['api_enabled'])
        self.app.ai_enabled.set(False);self.app.ai_toggle();self.assertFalse(self.app.project.settings['api_enabled'])

    def test_chinese_edit_is_live_and_original_is_unchanged(self):
        self.select('a');box=self.app.review_cards['chinese']
        box.delete('1.0','end');box.insert('1.0','人工修正中文');self.root.update()
        from lyricsync.editing import mapping_text
        self.assertEqual(mapping_text(self.app.project,self.app.project.mappings[0]),'人工修正中文')
        self.assertEqual(self.app.project.translations[0].text,'第一句')
        self.assertIn('人工修正中文',self.app.results.item('a','values'))
        self.app.autosave()
        self.app.project_saver.flush()
        from lyricsync.storage import load_project
        saved=load_project(self.app.project_path)
        self.assertEqual(mapping_text(saved,saved.mappings[0]),'人工修正中文')

    def test_hold_adjustment_is_one_undo_and_stops_on_release(self):
        self.select('a');hold=self.app.boundary_buttons[3].hold_action
        before=self.app.project.mappings[0].start
        hold.start();self.root.after(550,self.root.quit);self.root.mainloop();hold.stop()
        after=self.app.project.mappings[0].start
        self.assertGreater(after,before+.25)
        self.root.after(150,self.root.quit);self.root.mainloop()
        self.assertEqual(self.app.project.mappings[0].start,after)
        self.assertEqual(len(self.app.history.undo_stack),1)
        self.app.undo();self.assertEqual(self.app.project.mappings[0].start,before)

    def test_zero_filter_and_numbers_do_not_renumber_visible_subset(self):
        self.app.project.mappings[0].confidence=.8
        self.app.only_zero.set(True);self.app.refresh()
        self.assertEqual(self.app.results.get_children(),('b',))
        self.assertEqual(str(self.app.results.item('b','values')[0]),'2')

    def test_page_animation_and_history_window_do_not_raise_callbacks(self):
        self.app.steps.select(2);self.root.update()
        self.app.show_history();self.app.show_history()
        self.root.after(200,self.root.quit);self.root.mainloop()
        self.assertEqual(self.callback_errors,[])


class OutlineTests(unittest.TestCase):
    def test_outline_tracks_same_shape_without_recognizing_characters(self):
        import cv2,numpy as np
        blank=np.zeros((120,400,3),np.uint8);letters=blank.copy()
        cv2.putText(letters,'HELLO',(20,70),cv2.FONT_HERSHEY_SIMPLEX,1,(255,255,255),2)
        class FakeVideo:
            info={'duration':3}
            def at(self,stamp):return stamp,letters
            def frames(self,left,right,cancel):
                for n in range(30):
                    t=n/10
                    if left<=t<=right:yield t,letters if .6<=t<1.8 else blank
        visual=Visual('v','unclear',.8,1.6,[10,35,180,80],.4,1,evidence={'seed_time':1})
        result=outline_boundary(FakeVideo(),visual)
        self.assertAlmostEqual(result['start'],.6);self.assertAlmostEqual(result['end'],1.8)

    def test_missing_seed_is_not_invented_and_locked_rows_are_excluded(self):
        from lyricsync.outline_assist import propose
        p=Project(inputs={'video':str(Path(__file__).resolve().parents[1]/'examples/synthetic_mv.mp4')})
        p.mappings=[Mapping('a',['t']),Mapping('locked',['t'],locked=True)]
        self.assertEqual(unreliable_ids(p),['a'])
        with tempfile.TemporaryDirectory() as folder:
            cache=Cache(Path(folder)/'test.sqlite')
            try:rows=propose(p,['a','locked'],cache)
            finally:cache.close()
        self.assertEqual(len(rows),1);self.assertNotIn('start',rows[0])


if __name__=='__main__':unittest.main()
