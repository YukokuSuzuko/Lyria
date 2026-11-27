from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from lyricsync.model import Project
from lyricsync.storage import Cache
from lyricsync.subtitles import import_original
from lyricsync.matching import match_project
from lyricsync.export import export_project
from lyricsync.pipeline import run


class IntegrationTests(unittest.TestCase):
    def test_plain_original_srt_and_txt_group_import(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            p=folder/"original.srt";p.write_text("1\n00:00:01,000 --> 00:00:02,000\nHELLO\nWORLD\n",encoding="utf8")
            units,_=import_original(p)
            self.assertEqual(units[0].text,"HELLO\nWORLD")
            self.assertEqual(units[0].start,1)
            p=folder/"original.txt";p.write_text("夜の空\nSEE YOU\n\nHELLO\n",encoding="utf8")
            units,_=import_original(p,True)
            self.assertEqual(len(units),2);self.assertIsNone(units[0].start)
    def test_cached_end_to_end_no_api_and_locks_survive_rescan(self):
        root=Path(__file__).resolve().parents[1]
        evidence=root.parent/"reports"/"synthetic"/"synthetic.lyricsync.json"
        if not evidence.exists():self.skipTest("run run_pipeline.py first")
        from lyricsync.storage import load_project
        fixture=load_project(evidence)
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            p=Project(inputs=dict(fixture.inputs),settings=dict(fixture.settings))
            # Cached OCR evidence is injected; no provider call or API allowed.
            with patch("lyricsync.pipeline.scan",return_value=(fixture.visuals,fixture.video)),patch("lyricsync.pipeline.suggest",side_effect=AssertionError("API must be off")):
                run(p,folder/"p.json")
                self.assertEqual(len(p.unresolved()),len(p.mappings))
                export_project(p,folder/'draft.ass',draft=True)
                self.assertTrue(list(folder.glob("*.ass")))
                p.mappings[0].start+=.07;p.mappings[0].locked=p.mappings[0].confirmed=True
                chosen=p.mappings[0].start
                run(p,folder/"p.json")
                self.assertEqual(p.mappings[0].start,chosen)
                self.assertTrue(p.mappings[0].locked)
    def test_untimed_plain_original_matching_uses_visual_evidence(self):
        root=Path(__file__).resolve().parents[1]
        evidence=root.parent/"reports"/"synthetic"/"synthetic.lyricsync.json"
        if not evidence.exists():self.skipTest("run run_pipeline.py first")
        from lyricsync.storage import load_project
        p=load_project(evidence)
        for l in p.lyrics:l.start=l.end=None
        p.mappings=[]
        match_project(p)
        self.assertTrue(all(m.start is not None for m in p.mappings))

    def test_changed_translation_content_flags_retained_locks(self):
        root=Path(__file__).resolve().parents[1]
        evidence=root.parent/"reports"/"synthetic"/"synthetic.lyricsync.json"
        if not evidence.exists():self.skipTest("run run_pipeline.py first")
        from lyricsync.storage import load_project
        p=load_project(evidence)
        p.mappings[0].locked=p.mappings[0].confirmed=True
        locked_id=p.mappings[0].id
        p.settings["input_hashes"]={"ktv":"changed","translation":"changed"}
        with tempfile.TemporaryDirectory() as folder,patch("lyricsync.pipeline.scan",return_value=(p.visuals,p.video)):
            run(p,Path(folder)/"p.json")
            m=next(m for m in p.mappings if m.id==locked_id)
            self.assertTrue(m.locked);self.assertFalse(m.confirmed)
            self.assertTrue(any("导入文件已变更" in reason for reason in m.reasons))


if __name__=="__main__":unittest.main()
