import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lyricsync.model import Project, Mapping, Translation, Visual, Lyric, stable_id
from lyricsync.editing import Editor, History, mapping_text
from lyricsync.project_document import ProjectDocument, load_document, write_document
from lyricsync.video_only import build_empty_translations


class DocumentTests(unittest.TestCase):
    def test_roundtrip_revisions_and_secret_metadata(self):
        p = Project(inputs={"video": "source.mp4"}, settings={"api_key": "private", "provider": {"authorization": "secret", "model": "x"}})
        p.translations = [Translation("t", "api_key 是这句歌词的原文，不能删")]
        d = ProjectDocument(p); d.touch()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "work.lyria"
            write_document(d.snapshot(explicit_save=True), path)
            self.assertEqual(d.saved_revision, -1, "worker must not mutate live state")
            d.mark_saved(1)
            result = load_document(path)
            self.assertEqual((result.revision, result.saved_revision), (1, 1))
            self.assertEqual(result.project.translations[0].text, p.translations[0].text)
            self.assertNotIn("api_key", result.project.settings)
            self.assertEqual(result.project.settings["provider"], {"model": "x"})
            self.assertEqual(result.project.inputs["video"], str(Path(folder) / "source.mp4"))

    def test_snapshot_is_detached_and_old_ack_does_not_clear_dirty(self):
        d = ProjectDocument(); d.touch(); snap = d.snapshot(explicit_save=True)
        d.project.settings["value"] = 2; d.touch(); d.mark_saved(1)
        self.assertNotIn("value", snap["project"]["settings"])
        self.assertIn("unsaved_changes", d.close_reasons)
        self.assertEqual(d.revision, 2)

    def test_saved_but_unexported_has_distinct_reason(self):
        d = ProjectDocument(Project(mappings=[Mapping("m", [])]))
        d.mark_saved(0)
        self.assertEqual(d.close_reasons, ["current_revision_not_exported"])
        d.mark_exported(0); self.assertEqual(d.close_reasons, [])
        d.touch(); self.assertEqual(len(d.close_reasons), 2)

    def test_recovery_does_not_turn_autosave_into_explicit_save(self):
        d = ProjectDocument(); d.touch()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recovery.lyria"
            write_document(d.snapshot(), path)
            self.assertEqual(load_document(path).saved_revision, -1)

    def test_invalid_revision_ack_rejected(self):
        d = ProjectDocument()
        for value in (-1, True, 2, "0"):
            with self.subTest(value=value), self.assertRaises(ValueError): d.mark_saved(value)

    def test_corrupt_file_recovers_previous_valid_snapshot(self):
        d = ProjectDocument()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "work.lyria"
            write_document(d.snapshot(explicit_save=True), path)
            d.touch(); write_document(d.snapshot(explicit_save=True), path)
            path.write_text("{broken", encoding="utf8")
            with self.assertRaises(ValueError): load_document(path)
            self.assertEqual(load_document(path, recover=True).revision, 0)

    def test_rejects_legacy_unknown_and_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "x.lyria"
            path.write_text(json.dumps(Project().to_dict()), encoding="utf8")
            with self.assertRaises(ValueError): load_document(path)
            # The graph now rejects duplicate IDs before a writable document exists.
            with self.assertRaises(ValueError):
                d = ProjectDocument(Project(mappings=[Mapping("m", []), Mapping("m", [])]))
                write_document(d.snapshot(), path)
            self.assertEqual(json.loads(path.read_text())["version"], 2)

    def test_input_hardlink_and_backup_path_protection(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "video.mp4"; source.write_bytes(b"not replaceable")
            output = Path(folder) / "project.lyria"; os.link(source, output)
            d = ProjectDocument(Project(inputs={"video": str(source)}))
            with self.assertRaises(ValueError): write_document(d.snapshot(), output)
            self.assertEqual(source.read_bytes(), b"not replaceable")

    def test_invalid_reference_text_time_and_provenance_rejected(self):
        projects = [Project(mappings=[Mapping('m',['missing'])]),
                    Project(translations=[Translation('t',[])]) ,
                    Project(mappings=[Mapping('m',[],start=float('nan'))]),
                    Project(translations=[Translation('t','你好')],mappings=[Mapping('m',['t'],text_segments=[{'id':'t','start':0,'end':5}])])]
        with tempfile.TemporaryDirectory() as folder:
            for p in projects:
                with self.subTest(project=p), self.assertRaises(ValueError):
                    write_document(ProjectDocument(p).snapshot(), Path(folder)/'bad.lyria')


class BlankAndMarkersTests(unittest.TestCase):
    def project(self):
        return Project(translations=[Translation("a", "前句"), Translation("b", "后句")],
                       mappings=[Mapping("a", ["a"], start=1, end=2), Mapping("b", ["b"], start=3, end=4)])

    def test_insert_fills_only_unoccupied_gap_one_undo(self):
        p = self.project(); h = History(); e = Editor(p, h)
        row = e.insert_blank("a")
        self.assertEqual((row.start, row.end), (2, 3)); self.assertEqual(mapping_text(p, row), "")
        self.assertFalse(row.confirmed); self.assertEqual(len(h.undo_stack), 1)
        restored, _ = h.restore(p); self.assertEqual(len(restored.mappings), 2)

    def test_insert_in_overlap_contiguous_or_occupied_gap_is_untimed(self):
        for second_start in (1.5, 2):
            p = self.project(); p.mappings[1].start = second_start
            row = Editor(p, History()).insert_blank("a")
            self.assertEqual((row.start, row.end), (None, None))
        p = self.project(); p.mappings.append(Mapping("c", [], start=2.2, end=2.3))
        row = Editor(p, History()).insert_blank("a")
        self.assertEqual((row.start, row.end), (None, None))

    def test_delete_all_markers_is_reversible_and_empty_noop(self):
        p = self.project(); p.settings["frame_markers"] = [12, 20]; h = History(); e = Editor(p, h)
        self.assertTrue(e.delete_all_markers()); self.assertEqual(p.settings["frame_markers"], [])
        self.assertFalse(e.delete_all_markers()); self.assertEqual(len(h.undo_stack), 1)
        restored, _ = h.restore(p); self.assertEqual(restored.settings["frame_markers"], [12, 20])


class VideoOnlyTests(unittest.TestCase):
    def project(self):
        visuals = [Visual("v1", "原文甲", 1, 2, [0, 0, 20, 20], .9, 3),
                   Visual("v2", "原文乙", 3, 4, [0, 0, 20, 20], .7, 2, warnings=["边界需复核"])]
        return Project(visuals=visuals, lyrics=[Lyric(stable_id("ocr-source", v.id), v.text, v.start, v.end) for v in visuals])

    def test_builds_blank_not_auto_translation_and_rescan_is_idempotent(self):
        p = self.project(); build_empty_translations(p)
        first = p.to_dict(); build_empty_translations(p)
        self.assertEqual(first, p.to_dict()); self.assertEqual(len(p.mappings), 2)
        self.assertTrue(all(mapping_text(p, m) == "" and not m.confirmed for m in p.mappings))
        self.assertEqual((p.mappings[1].start, p.mappings[1].end), (3, 4))
        self.assertIn("边界需复核", p.mappings[1].reasons)

    def test_rescan_preserves_manually_entered_chinese(self):
        p = self.project(); build_empty_translations(p)
        identity = p.mappings[0].id; e = Editor(p, History()); e.set_text(identity, "亲手填写")
        e.set_time(identity, start=1.1, end=2.1)
        build_empty_translations(p)
        self.assertEqual(len(p.mappings), 2)
        self.assertEqual(mapping_text(p, p.mappings[0]), "亲手填写")
        self.assertEqual((p.mappings[0].start, p.mappings[0].end), (1.1, 2.1))

    def test_optional_original_lyrics_remain_linked_without_translation_input(self):
        p = self.project();p.inputs['ktv']='provided-source.ass'
        p.lyrics=[Lyric('original-a','原文甲',1,2),Lyric('original-b','原文乙',3,4)]
        build_empty_translations(p)
        self.assertEqual([m.lyric_ids for m in p.mappings],[['original-a'],['original-b']])
        self.assertTrue(all(mapping_text(p,m)=='' for m in p.mappings))

    def test_pipeline_accepts_only_video_and_never_calls_provider(self):
        from lyricsync.pipeline import run
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "video.mp4"; path.write_bytes(b"video placeholder")
            p = Project(inputs={"video": str(path)}); p.settings["api_enabled"] = True
            with patch("lyricsync.pipeline.scan", return_value=(self.project().visuals, {"duration": 5})), \
                 patch("lyricsync.pipeline.suggest") as suggest:
                run(p, Path(folder) / "pipeline.json")
            self.assertEqual(len(p.mappings), 2); self.assertEqual(p.stage, "review")
            self.assertTrue(all(mapping_text(p, m) == "" for m in p.mappings)); suggest.assert_not_called()


if __name__ == "__main__": unittest.main()
