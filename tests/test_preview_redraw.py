"""Headless real preview raster/dispatch methods; not rendered Windows QA."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import numpy as np
from lyricsync.gui import Application
from lyricsync.model import Project, Mapping, Translation
from lyricsync.editing import mapping_text, segments


class Canvas:
    def __init__(self):self.width=640;self.height=360;self.calls=[]
    def winfo_width(self):return self.width
    def winfo_height(self):return self.height
    def delete(self,*args):self.calls.append(('delete',args))
    def create_text(self,*args,**kwargs):self.calls.append(('text',kwargs))
    def create_image(self,*args,**kwargs):self.calls.append(('image',kwargs))
    def create_rectangle(self,*args,**kwargs):pass


class PreviewHost:
    schedule_preview_draw=Application.schedule_preview_draw
    cancel_preview_draw=Application.cancel_preview_draw
    draw=Application.draw
    def __init__(self):
        self.canvas=Canvas();self.jobs={};self.next_id=0
        self.root=SimpleNamespace(after=self.after,after_cancel=self.cancel)
        self.frame=np.zeros((1080,1920,3),dtype=np.uint8)
        self.project=Project()
        self.preview_chinese=SimpleNamespace(get=lambda:False)
        self.sound_enabled=SimpleNamespace(get=lambda:False)
        self.preview_label=SimpleNamespace(set=Mock())
        self.frame_counter=lambda:'F1';self.stamp=0;self.audio_state='test'
    def after(self,delay,callback):
        self.next_id+=1;self.jobs[self.next_id]=(delay,callback);return self.next_id
    def cancel(self,identity):self.jobs.pop(identity,None)
    def tick(self):
        identity=min(self.jobs);_,callback=self.jobs.pop(identity);callback()
    def configure(self,width,height):
        self.canvas.width,self.canvas.height=width,height
        self.schedule_preview_draw(SimpleNamespace(width=width,height=height))


class PreviewRedrawTests(unittest.TestCase):
    def test_resize_burst_is_one_job_paints_latest_size_and_keeps_up_with_drag(self):
        host=PreviewHost()
        with patch('PIL.ImageTk.PhotoImage',return_value=object()) as photo:
            for w in range(600,700):host.configure(w,500)
            self.assertEqual(len(host.jobs),1)
            self.assertEqual(next(iter(host.jobs.values()))[0],16)
            host.tick()
            self.assertEqual(photo.call_count,1)
            self.assertEqual(photo.call_args.args[0].width,699)
            for w in range(700,800):host.configure(w,500)
            self.assertEqual(len(host.jobs),1)
            host.tick()
            self.assertEqual(photo.call_count,2)
            self.assertEqual(photo.call_args.args[0].width,799)

    def test_position_only_configure_does_not_schedule_more_renders(self):
        host=PreviewHost()
        host.configure(640,360)
        with patch('PIL.ImageTk.PhotoImage',return_value=object()):host.tick()
        for _ in range(100):host.configure(640,360)
        self.assertFalse(host.jobs)

    def test_explicit_seek_or_play_draw_absorbs_pending_resize(self):
        host=PreviewHost();host.configure(700,500)
        with patch('PIL.ImageTk.PhotoImage',return_value=object()) as photo:host.draw()
        self.assertFalse(host.jobs)
        self.assertEqual(photo.call_count,1)
        self.assertEqual(photo.call_args.args[0].width,700)

    def test_cancel_on_destroy_discards_pending_job(self):
        host=PreviewHost();host.configure(700,500)
        host.cancel_preview_draw();host.cancel_preview_draw()
        self.assertFalse(host.jobs)

    def test_same_immutable_frame_and_raster_size_reuse_photo_not_stale_overlays(self):
        host=PreviewHost()
        with patch('PIL.ImageTk.PhotoImage',side_effect=lambda *a,**k:object()) as photo:
            host.draw()
            for _ in range(50):host.draw()
            self.assertEqual(photo.call_count,1)
            host.frame=host.frame.copy();host.draw()
            self.assertEqual(photo.call_count,2)
            host.canvas.width=500;host.draw()
            self.assertEqual(photo.call_count,3)

    def test_overlay_reads_current_text_but_indexes_translations_only_once(self):
        class CountedList(list):
            visits=0
            def __iter__(self):
                self.visits+=1
                return super().__iter__()
        host=PreviewHost();host.preview_chinese=SimpleNamespace(get=lambda:True)
        host.project.translations=CountedList(Translation(str(i),'内容') for i in range(1000))
        host.project.mappings=[Mapping(str(i),[str(i)],start=i,end=i+.5) for i in range(1000)]
        with patch('PIL.ImageTk.PhotoImage',return_value=object()) as photo, \
                patch('lyricsync.gui.layout_entries',return_value=[]) as layout:
            host.draw()
            self.assertEqual(host.project.translations.visits,1)
            host.project.mappings[0].text_override='新内容';host.draw()
            self.assertEqual(host.project.translations.visits,2)
            self.assertEqual(layout.call_count,2)
            self.assertEqual(layout.call_args.args[0][0][2],'新内容')
            self.assertEqual(photo.call_count,1)


class SharedTextIndexTests(unittest.TestCase):
    def test_shared_index_preserves_all_text_semantics_and_segment_isolation(self):
        project=Project(translations=[Translation('a','你好😀\n世界'),Translation('b','第二句')])
        lookup={t.id:t.text for t in project.translations}
        rows=[Mapping('1',['a']),Mapping('2',['a','b']),Mapping('3',['missing']),
              Mapping('4',['a'],text_segments=[dict(id='a',start=1,end=5)]),
              Mapping('5',['a'],text_override=''),Mapping('6',['a'],text_override='替换')]
        for row in rows:
            with self.subTest(row=row.id):
                self.assertEqual(mapping_text(project,row),mapping_text(project,row,lookup))
                self.assertEqual(segments(project,row),segments(project,row,lookup))
        result=segments(project,rows[3],lookup);result[0]['start']=99
        self.assertEqual(rows[3].text_segments[0]['start'],1)
        self.assertEqual(mapping_text(project,rows[0],{}),'')
