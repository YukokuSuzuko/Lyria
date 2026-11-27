"""Real Tk/PyAV integration checks; windows remain withdrawn."""
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from lyricsync.gui import Application
from lyricsync.vision import Video
from lyricsync.model import Mapping, Translation
from lyricsync.tk_lifecycle import destroy_root


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.root=tk.Tk();self.root.withdraw()
        self.root.update_idletasks()
        self.app=Application(self.root,self.folder.name)
        self.app.preview_video=Video(Path(__file__).resolve().parents[1]/'examples/synthetic_mv.mp4')
        self.app.timeline.configure(to=self.app.preview_video.info['duration'])
        self.app.seek(1)

    def tearDown(self):
        self.app.project_saver.close()
        self.app.pause_playback()
        destroy_root(self.root);self.folder.cleanup()

    def test_previous_and_next_are_distinct_real_frames(self):
        self.app.step(-1);self.assertAlmostEqual(self.app.stamp,.95)
        self.app.step(1);self.assertAlmostEqual(self.app.stamp,1)

    def test_play_advances_pause_stops_and_seek_stays_paused(self):
        self.app.toggle_play()
        self.root.after(450,self.root.quit);self.root.mainloop()
        self.assertGreater(self.app.stamp,1.15)
        self.assertTrue(self.app.playing)
        self.app.pause_playback();stamp=self.app.stamp
        self.root.after(120,self.root.quit);self.root.mainloop()
        self.assertEqual(stamp,self.app.stamp)
        self.app.seek(2);self.assertAlmostEqual(self.app.stamp,2)
        self.assertFalse(self.app.playing)

    def test_play_current_cue_stops_before_following_cue_and_keeps_selection(self):
        self.app.project.translations=[Translation('a','第一句'),Translation('b','第二句')]
        self.app.project.mappings=[Mapping('a',['a'],start=1,end=1.2),Mapping('b',['b'],start=1.2,end=2)]
        self.app.refresh();self.app.results.selection_set('a');self.app.select_mapping(auto=True)
        self.app.sound_enabled.set(False)
        self.app.play_current_cue()
        self.root.after(450,self.root.quit);self.root.mainloop()
        self.assertFalse(self.app.playing)
        self.assertGreaterEqual(self.app.stamp,1.1)
        self.assertLess(self.app.stamp,1.2)
        self.assertEqual(self.app.results.selection(),('a',))

    def test_friendly_language_choice_keeps_saved_code(self):
        self.app.language.set('eng');self.app.sync()
        self.assertEqual(self.app.project.settings['language'],'eng')
        self.assertFalse(self.app.mapping_editor.opened)
        self.app.mapping_editor.flip();self.assertTrue(self.app.mapping_editor.opened)

    def prepare_unknown_mapping(self):
        self.app.project.translations=[Translation('t','到那时我一定会生气地哭出来吧')]
        self.app.project.mappings=[Mapping('m',['t'])]
        self.app.refresh();self.app.results.selection_set('m');self.app.select_mapping()

    def test_preview_does_not_show_previous_cue_at_rounded_boundary(self):
        edge=67.00026666666666
        self.app.project.video=dict(self.app.preview_video.info)
        self.app.project.translations=[Translation('a','被飘渺云烟缠绕着的你'),Translation('b','带着如此寂寞的眼神')]
        self.app.project.mappings=[Mapping('a',['a'],start=61.8618,end=67.000267),Mapping('b',['b'],start=edge,end=71.10436666666666)]
        self.app.project.settings['subtitle_position']='top'
        self.app.preview_chinese.set(True);self.app.stamp=edge;self.app.draw()
        texts=[self.app.canvas.itemcget(item,'text') for item in self.app.canvas.find_all() if self.app.canvas.type(item)=='text']
        self.assertNotIn('被飘渺云烟缠绕着的你',texts)
        self.assertIn('带着如此寂寞的眼神',texts)

    def test_confirm_does_not_round_existing_pts(self):
        self.prepare_unknown_mapping()
        m=self.app.project.mappings[0]
        m.start=1.001001001001;m.end=2.000000333333
        original=(m.start,m.end)
        self.app.select_mapping();self.assertTrue(self.app.confirm_mapping())
        self.assertEqual((m.start,m.end),original)

    def test_preview_before_project_metadata_does_not_fail_for_no_roi(self):
        self.prepare_unknown_mapping()
        self.app.preview_chinese.set(True)
        self.app.draw()

    def test_confirm_unknown_times_explains_missing_fields_without_crash(self):
        self.prepare_unknown_mapping()
        before=self.app.project.to_dict()
        with patch('lyricsync.gui.messagebox.showwarning') as warning,patch.object(self.app,'error') as error:
            self.app.confirm_mapping()
        error.assert_not_called()
        warning.assert_called_once()
        self.assertIn('出现时间',warning.call_args.args[1])
        self.assertIn('消失时间',warning.call_args.args[1])
        self.assertEqual(before,self.app.project.to_dict())

    def test_invalid_time_input_keeps_mapping_unconfirmed(self):
        self.prepare_unknown_mapping()
        for start,end in [('abc','2'),('nan','2'),('2','1'),('1','999')]:
            self.app.start.set(start);self.app.end.set(end)
            with patch('lyricsync.gui.messagebox.showwarning') as warning,patch.object(self.app,'error') as error:
                self.assertFalse(self.app.confirm_mapping())
            error.assert_not_called();warning.assert_called_once()
            self.assertFalse(self.app.project.mappings[0].confirmed)

    def test_user_supplied_valid_times_can_confirm_previously_unknown_mapping(self):
        self.prepare_unknown_mapping()
        self.app.start.set('2');self.app.end.set('3.5')
        with patch.object(self.app,'error') as error:
            self.assertTrue(self.app.confirm_mapping())
        error.assert_not_called()
        mapping=self.app.project.mappings[0]
        self.assertEqual((mapping.start,mapping.end),(2,3.5))
        self.assertTrue(mapping.confirmed and mapping.locked)

    def test_repeat_confirm_does_not_delete_original_and_is_undoable(self):
        self.prepare_unknown_mapping()
        self.app.start.set('2');self.app.end.set('3.5');self.app.confirm_mapping()
        with patch('lyricsync.gui.messagebox.askyesno',return_value=True):self.app.repeat_mapping()
        repeated=self.app.selected_mapping()
        self.app.start.set('5');self.app.end.set('6');self.app.confirm_mapping()
        self.assertEqual(len(self.app.project.mappings),2)
        self.assertEqual(self.app.project.mappings[0].start,2)
        self.assertEqual(self.app.project.mappings[1].start,5)
        # Confirmation, end, start and repeat are independently reversible.
        for _ in range(4):self.app.undo()
        self.assertEqual(len(self.app.project.mappings),1)

    def test_confirm_next_moves_only_after_success(self):
        self.prepare_unknown_mapping()
        self.app.project.translations.append(Translation('t2','下一句'))
        self.app.project.mappings.append(Mapping('m2',['t2'],start=4,end=5))
        self.app.refresh();self.app.results.selection_set('m');self.app.select_mapping()
        with patch('lyricsync.gui.messagebox.showwarning'):self.app.confirm_next()
        self.assertEqual(self.app.selected_mapping().id,'m')
        self.app.start.set('2');self.app.end.set('3');self.app.confirm_next()
        self.assertEqual(self.app.selected_mapping().id,'m2')

    def test_key_field_clear_blank_preserve_and_delete_requires_confirmation(self):
        from lyricsync.key_widget import KeyWidget
        class Store:
            value=''
            def get(self,scope):return self.value
            def save(self,scope,key):self.value=key
            def delete(self,scope):self.value=''
        store=Store();widget=KeyWidget(self.root,'test','test',store)
        widget.entry.set('fake');widget.save()
        self.assertEqual(widget.entry.get(),'');self.assertEqual(store.value,'fake')
        widget.save();self.assertEqual(store.value,'fake')
        with patch('lyricsync.key_widget.messagebox.askyesno',return_value=False) as ask:
            widget.delete();self.assertEqual(store.value,'fake')
            self.assertEqual(ask.call_args.kwargs['default'],'no')
        with patch('lyricsync.key_widget.messagebox.askyesno',return_value=True):widget.delete()
        self.assertEqual(store.value,'')


if __name__=='__main__':unittest.main()
