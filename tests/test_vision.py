from pathlib import Path
from dataclasses import asdict
import json
import tempfile
import unittest
from unittest.mock import patch as mocked

import cv2
import numpy as np
from lyricsync.model import Detection,Project
from lyricsync.vision import Tracker,edge_mask,visibility_score,Video
from lyricsync.storage import Cache,load_project
from lyricsync.pipeline import run
from lyricsync.subtitles import import_original,import_translation


class VisionTests(unittest.TestCase):
    def image(self,lines=(),color=(255,255,255),dx=0):
        frame=np.zeros((360,640,3),np.uint8)
        for text,y in lines:cv2.putText(frame,text,(30+dx,y),cv2.FONT_HERSHEY_SIMPLEX,1,color,2,cv2.LINE_AA)
        return frame
    def det(self,text,y):return Detection(text,[25,y-26,300,y+8],.95)
    def test_sparse_text_is_not_blank_shape(self):
        self.assertGreater(np.count_nonzero(edge_mask(self.image([("HELLO",150)]))),0)
    def test_shape_present_is_separate_from_ocr_success(self):
        image=self.image([("HELLO",150)])
        track=Tracker(.3,max_gap=.45)
        track.feed(.3,image,[self.det("HELLO",150)])
        track.feed(.6,image,[])
        self.assertEqual(track.active[0].last,.6)
        self.assertEqual(track.active[0].shape_hits,1)
    def test_color_and_small_movement_do_not_end_line(self):
        image=self.image([("HELLO",150)])
        track=Tracker(.3)
        track.feed(.3,image,[self.det("HELLO",150)])
        track.feed(.6,self.image([("HELLO",150)],(30,220,250),dx=3),[])
        self.assertEqual(track.active[0].last,.6)
    def test_new_line_does_not_end_old_three_overlap(self):
        t=Tracker(.3)
        t.feed(.3,self.image([("HELLO",100)]),[self.det("HELLO",100)])
        t.feed(.6,self.image([("HELLO",100),("WORLD",180),("NIGHT",260)]),
               [self.det("HELLO",100),self.det("WORLD",180),self.det("NIGHT",260)])
        self.assertEqual(len(t.active),3)
        self.assertEqual(t.active[0].last,.6)
    def test_disappearance_and_repeated_chorus_are_distinct(self):
        t=Tracker(.3)
        t.feed(.3,self.image([("HELLO",100)]),[self.det("HELLO",100)])
        t.feed(.6,self.image(),[]);t.feed(.9,self.image(),[])
        t.feed(1.2,self.image([("HELLO",100)]),[self.det("HELLO",100)])
        self.assertEqual(len(t.finish()),2)
    def test_short_observed_blank_separates_identical_lyrics(self):
        t=Tracker(.1,max_gap=.45)
        text=self.image([('HELLO',100)])
        t.feed(.1,text,[self.det('HELLO',100)])
        t.feed(.2,self.image(),[])
        t.feed(.3,text,[self.det('HELLO',100)])
        tracks=t.finish()
        self.assertEqual(len(tracks),2)
        self.assertEqual((tracks[0].last,tracks[0].next_absent),(.1,.2))
        self.assertEqual(tracks[1].first,.3)
    def test_unobserved_long_gap_cannot_reconnect_same_text(self):
        t=Tracker(.1,max_gap=.45)
        text=self.image([('HELLO',100)])
        t.feed(.1,text,[self.det('HELLO',100)])
        t.feed(1.,text,[self.det('HELLO',100)])
        self.assertEqual(len(t.finish()),2)
    def test_pts_decoding_and_seek(self):
        v=Video(Path(__file__).resolve().parents[1]/"examples"/"synthetic_mv.mp4")
        frames=list(v.frames(1,1.2))
        self.assertAlmostEqual(frames[0][0],1)
        self.assertTrue(all(b[0]>a[0] for a,b in zip(frames,frames[1:])))
    def test_plain_original_has_no_fabricated_timing(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"source.txt";path.write_text("HELLO\nHELLO\n",encoding="utf-8")
            units,warnings=import_original(path)
            self.assertIsNone(units[0].start);self.assertNotEqual(units[0].id,units[1].id)
    def test_cancel_saves_checkpoint_and_does_not_write_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);examples=Path(__file__).resolve().parents[1]/"examples"
            inputs={"video":str(examples/"synthetic_mv.mp4"),"ktv":str(examples/"synthetic_ktv.ass"),"translation":str(examples/"synthetic_zh.txt")}
            project=Project(inputs=inputs)
            before={key:Path(path).read_bytes() for key,path in inputs.items() if key!="video"}
            with self.assertRaises(InterruptedError):run(project,folder/"p.json",cancel=lambda:True)
            self.assertEqual(load_project(folder/"p.json").stage,"cancelled")
            self.assertEqual(before,{key:Path(inputs[key]).read_bytes() for key in before})


if __name__=="__main__":unittest.main()
