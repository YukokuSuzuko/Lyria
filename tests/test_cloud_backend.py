from dataclasses import asdict
"""Headless regressions found during the cloud backend audit."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lyricsync.export import export_project, missing_time_rows, validate_project
from lyricsync.model import Lyric, Mapping, Project, TIME_EPSILON, Translation, Visual
from lyricsync.storage import load_project, save_project
from lyricsync.subtitles import parse_ass


class PartialExportDurationTests(unittest.TestCase):
    def project(self, end):
        return Project(
            video={"duration": 1, "width": 960, "height": 540},
            translations=[Translation("t1", "保留正文"), Translation("t2", "越界正文")],
            mappings=[
                Mapping("m1", ["t1"], start=.1, end=.5, confirmed=True),
                Mapping("m2", ["t2"], start=.6, end=end, confirmed=True),
            ],
        )

    def test_explicit_omission_uses_validation_tolerance_and_keeps_project(self):
        for overrun in (TIME_EPSILON * 1.1, .01, .04, .05):
            with self.subTest(overrun=overrun), tempfile.TemporaryDirectory() as folder:
                project = self.project(1 + overrun)
                before = deepcopy(project.to_dict())
                output = Path(folder) / "partial.ass"
                omitted = export_project(project, output, skip_missing_times=True)
                self.assertEqual(omitted, [{
                    "id": "m2", "text": "越界正文", "reason": "时间超出视频范围",
                }])
                self.assertEqual([event.text for event in parse_ass(output).events], ["保留正文"])
                report = json.loads(output.with_name("partial_遗漏清单.json").read_text(encoding="utf-8"))
                self.assertEqual(report["exported_count"], 1)
                self.assertEqual(report["skipped_count"], 1)
                self.assertEqual(report["omitted"], omitted)
                self.assertEqual(project.to_dict(), before)

    def test_ordinary_export_still_rejects_small_duration_overrun(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "ordinary.ass"
            with self.assertRaisesRegex(ValueError, "时间超出视频"):
                export_project(self.project(1.01), output)
            self.assertFalse(output.exists())
            self.assertFalse(output.with_name("ordinary_遗漏清单.json").exists())

    def test_duration_rounding_tolerance_does_not_omit_legal_row(self):
        for overrun in (0, TIME_EPSILON / 2, TIME_EPSILON):
            with self.subTest(overrun=overrun), tempfile.TemporaryDirectory() as folder:
                project = self.project(1 + overrun)
                self.assertEqual(validate_project(project), [])
                self.assertEqual(missing_time_rows(project), [])
                output = Path(folder) / "complete.ass"
                self.assertEqual(export_project(project, output, skip_missing_times=True), [])
                self.assertEqual(len(parse_ass(output).events), 2)
                self.assertFalse(output.with_name("complete_遗漏清单.json").exists())

    def test_time_omission_does_not_bypass_structural_errors(self):
        project = self.project(1.01)
        project.mappings[1].lyric_ids = ["missing-original"]
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "invalid.ass"
            with self.assertRaisesRegex(ValueError, "关联的原文或识别记录无效"):
                export_project(project, output, skip_missing_times=True)
            self.assertFalse(output.exists())


class ProjectRecoveryShapeTests(unittest.TestCase):
    def test_non_object_roots_have_recoverable_validation_errors(self):
        for root in (None, [], [{}], "damaged", 2, 1.5, True):
            with self.subTest(root=root):
                with self.assertRaisesRegex(ValueError, "工程文件格式错误"):
                    Project.from_dict(root)
        # Minimal version-2 objects remain compatible with dataclass defaults.
        self.assertEqual(Project.from_dict({"version": 2}).to_dict(), Project().to_dict())

    def test_recovery_and_next_save_preserve_valid_backup_after_bad_root(self):
        for root in (None, [], [{}], "damaged", 2, 1.5, True):
            with self.subTest(root=root), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "project.lyricsync.json"
                backup = path.with_name(path.name + ".bak")
                save_project(Project(stage="backup"), path)
                save_project(Project(stage="latest"), path)
                good_backup = backup.read_bytes()
                path.write_text(json.dumps(root), encoding="utf-8")
                damaged_primary = path.read_bytes()
                with self.assertRaisesRegex(ValueError, "工程文件格式错误"):
                    load_project(path)
                recovered = load_project(path, recover=True)
                self.assertEqual(recovered.stage, "backup")
                self.assertEqual(path.read_bytes(), damaged_primary)
                self.assertEqual(backup.read_bytes(), good_backup)
                save_project(Project(stage="new"), path)
                self.assertEqual(load_project(path).stage, "new")
                self.assertEqual(backup.read_bytes(), good_backup)


class StableIdentityValidationTests(unittest.TestCase):
    def project(self):
        # ID uniqueness is namespace-local; these IDs may legitimately match.
        return Project(
            translations=[Translation("same", "保留中文")],
            lyrics=[Lyric("same", "SOURCE", 0, 1)],
            visuals=[Visual("same", "SOURCE", 0, 1, [0, 0, 10, 10], .9, 3)],
            mappings=[Mapping("same", ["same"], ["same"], ["same"],
                              start=0, end=1, confirmed=True)],
        )

    def test_ids_can_match_across_different_record_kinds(self):
        self.assertEqual(validate_project(self.project()), [])

    def test_duplicate_ids_in_each_namespace_block_all_export_modes(self):
        for field, label in (("translations", "译文"), ("lyrics", "原文"),
                             ("visuals", "识别片段"), ("mappings", "字幕条目")):
            for mode in ({}, {"draft": True}, {"skip_missing_times": True}):
                with self.subTest(field=field, mode=mode), tempfile.TemporaryDirectory() as folder:
                    project = self.project()
                    duplicate = deepcopy(getattr(project, field)[0])
                    if field == "translations":
                        duplicate.text = "不可静默替换原中文"
                    if field == 'mappings':
                        before=deepcopy(project.to_dict())
                        with self.assertRaises(ValueError):getattr(project,field).append(duplicate)
                        self.assertEqual(project.to_dict(),before)
                        self.assertFalse((Path(folder)/'invalid.ass').exists())
                        continue
                    getattr(project, field).append(duplicate)
                    before = deepcopy(project.to_dict())
                    self.assertIn(label + "存在重复 ID", validate_project(project, draft=True))
                    output = Path(folder) / "invalid.ass"
                    with self.assertRaisesRegex(ValueError, label + "存在重复 ID"):
                        export_project(project, output, **mode)
                    self.assertFalse(output.exists())
                    self.assertFalse(output.with_name("invalid_遗漏清单.json").exists())
                    self.assertEqual(project.to_dict(), before)


class RepeatedOccurrenceTests(unittest.TestCase):
    def fixture(self, folder, confirmed=True):
        from lyricsync.editing import Editor, History
        from lyricsync.matching import match_project
        from lyricsync.subtitles import import_original, import_translation

        original = Path(folder) / "original.txt"
        translation = Path(folder) / "translation.txt"
        original.write_text("HELLO WORLD\n", encoding="utf-8")
        translation.write_text("你好，世界\n", encoding="utf-8")
        lyrics, warnings = import_original(original)
        project = Project(
            lyrics=lyrics, translations=import_translation(translation), warnings=warnings,
            visuals=[Visual("visual", "HELLO WORLD", .1, .5, [0, 0, 10, 10], .9, 3)],
        )
        match_project(project)
        source = project.mappings[0]
        if confirmed:
            Editor(project, History()).confirm(source.id)
        self.assertFalse(source.human_edited)
        app = SimpleNamespace(
            project=project, history=History(), root=None,
            guard=lambda: True, selected_mapping=lambda: source,
            autosave=Mock(), refresh=Mock(), select_mapping=Mock(),
            results=SimpleNamespace(selection_set=Mock()),
        )
        return app, source

    def test_actual_repeat_action_survives_rematch_without_becoming_confirmed(self):
        from lyricsync.editing import Editor, History, mapping_text
        from lyricsync.gui import Application
        from lyricsync.matching import match_project

        with tempfile.TemporaryDirectory() as folder:
            app, source = self.fixture(folder)
            original_state = deepcopy(asdict(source))
            original_translations = deepcopy(app.project.translations)
            with patch("lyricsync.gui.messagebox.askyesno", return_value=True):
                Application.repeat_mapping(app)
            self.assertEqual(len(app.project.mappings), 2)
            repeated = app.project.mappings[1]
            self.assertNotEqual(repeated.id, source.id)
            self.assertTrue(repeated.id.startswith("repeat-"))
            self.assertEqual(mapping_text(app.project, repeated), "你好，世界")
            self.assertEqual((repeated.start, repeated.end), (None, None))
            self.assertEqual((repeated.lyric_ids, repeated.visual_ids), ([], []))
            self.assertFalse(repeated.confirmed)
            self.assertIsNot(repeated.translation_ids, source.translation_ids)
            repeated_state = deepcopy(asdict(repeated))
            for _ in range(2):
                match_project(app.project)
                self.assertEqual([m.id for m in app.project.mappings], [source.id, repeated.id])
                self.assertEqual(asdict(repeated), repeated_state)
            self.assertTrue(repeated.human_edited)
            editor = Editor(app.project, History())
            editor.set_time(repeated.id, start=2, end=3)
            editor.set_text(repeated.id, "再次唱起：你好，世界")
            match_project(app.project)
            self.assertEqual((repeated.start, repeated.end), (2, 3))
            self.assertEqual(mapping_text(app.project, repeated), "再次唱起：你好，世界")
            self.assertFalse(repeated.confirmed)
            self.assertEqual(asdict(source), dict(original_state, human_edited=True))
            self.assertEqual(app.project.translations, original_translations)
            app.autosave.assert_called_once_with()
            app.results.selection_set.assert_called_once_with(repeated.id)

    def test_unconfirmed_source_and_repeat_both_survive_and_undo_restores_flags(self):
        from lyricsync.editing import mapping_text
        from lyricsync.gui import Application
        from lyricsync.matching import match_project

        with tempfile.TemporaryDirectory() as folder:
            app, source = self.fixture(folder, confirmed=False)
            before = deepcopy(app.project.to_dict())
            source_before = deepcopy(asdict(source))
            with patch("lyricsync.gui.messagebox.askyesno", return_value=True):
                Application.repeat_mapping(app)
            repeated = app.project.mappings[1]
            for _ in range(2):
                match_project(app.project)
                self.assertEqual([m.id for m in app.project.mappings], [source.id, repeated.id])
                self.assertEqual((source.start, source.end), (.1, .5))
                self.assertEqual((repeated.start, repeated.end), (None, None))
                self.assertEqual(mapping_text(app.project, source), "你好，世界")
                self.assertEqual(mapping_text(app.project, repeated), "你好，世界")
                self.assertFalse(source.confirmed or repeated.confirmed)
            expected_source = dict(source_before, human_edited=True)
            self.assertEqual(asdict(source), expected_source)
            self.assertTrue(repeated.human_edited)
            restored, label = app.history.restore(app.project)
            self.assertEqual(label, "复用中文")
            self.assertEqual(restored.to_dict(), before)
            self.assertFalse(restored.mappings[0].human_edited)

    def test_repeat_protection_survives_unconfirm_and_both_actions_undo_exactly(self):
        from lyricsync.editing import Editor, mapping_text
        from lyricsync.gui import Application
        from lyricsync.matching import match_project

        for action in ("gui_unlock", "editor_unconfirm"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as folder:
                app, source = self.fixture(folder, confirmed=True)
                before_repeat = deepcopy(app.project.to_dict())
                with patch("lyricsync.gui.messagebox.askyesno", return_value=True):
                    Application.repeat_mapping(app)
                after_repeat = deepcopy(app.project.to_dict())
                repeated = app.project.mappings[1]
                if action == "gui_unlock":
                    Application.unlock(app)
                else:
                    Editor(app.project, app.history).confirm(source.id, False)
                for _ in range(2):
                    match_project(app.project)
                    self.assertEqual([m.id for m in app.project.mappings], [source.id, repeated.id])
                    self.assertEqual((source.start, source.end), (.1, .5))
                    self.assertEqual((repeated.start, repeated.end), (None, None))
                    self.assertEqual(mapping_text(app.project, source), "你好，世界")
                    self.assertEqual(mapping_text(app.project, repeated), "你好，世界")
                    self.assertFalse(source.locked or source.confirmed or repeated.confirmed)
                    self.assertTrue(source.human_edited and repeated.human_edited)
                restored, label = app.history.restore(app.project)
                self.assertEqual(label, "取消确认")
                self.assertEqual(restored.to_dict(), after_repeat)
                restored, label = app.history.restore(restored)
                self.assertEqual(label, "复用中文")
                self.assertEqual(restored.to_dict(), before_repeat)

    def test_cancelled_repeat_action_does_not_change_project_or_history(self):
        from lyricsync.gui import Application

        for confirmed in (False, True):
            with self.subTest(confirmed=confirmed), tempfile.TemporaryDirectory() as folder:
                app, _ = self.fixture(folder, confirmed=confirmed)
                before = deepcopy(app.project.to_dict())
                with patch("lyricsync.gui.messagebox.askyesno", return_value=False):
                    Application.repeat_mapping(app)
                self.assertEqual(app.project.to_dict(), before)
                self.assertEqual(app.history.undo_stack, [])
                app.autosave.assert_not_called()


if __name__ == "__main__":
    unittest.main()
