"""Offline regressions for preserving source files during atomic output."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lyricsync.export import write_ass
from lyricsync.model import Project
from lyricsync.storage import atomic_json, atomic_text, load_project, protect_output, save_project


class AtomicOutputTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.source = self.root / "原始 译文.txt"
        self.original = "不可改写的原始中文\n".encode("utf-8")
        self.source.write_bytes(self.original)

    def hardlink(self, name):
        target = self.root / name
        try:
            os.link(self.source, target)
        except (NotImplementedError, OSError) as exc:
            self.skipTest("Filesystem cannot create hard links: " + type(exc).__name__)
        return target

    def test_existing_hardlink_output_is_rejected_without_changing_source(self):
        output = self.hardlink("alias.json")
        with self.assertRaisesRegex(ValueError, "不能直接覆盖原导入素材"):
            atomic_json(output, {"new": True}, [self.source])
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertTrue(output.samefile(self.source))

    def test_stale_fixed_json_temp_cannot_overwrite_source(self):
        output = self.root / "project.lyricsync.json"
        alias = self.hardlink(output.name + ".tmp")
        atomic_json(output, {"中文": "新快照"}, [self.source])
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"中文": "新快照"})
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(alias.read_bytes(), self.original)
        self.assertEqual(list(self.root.glob(".lyria-*.tmp")), [])

    def test_stale_fixed_ass_temp_cannot_overwrite_source(self):
        output = self.root / "字幕.ass"
        alias = self.hardlink(output.name + ".tmp")
        body = "[Script Info]\nTitle: 合成测试\n"
        write_ass(output, body, [self.source])
        self.assertEqual(output.read_text(encoding="utf-8-sig"), body)
        self.assertTrue(output.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertEqual(alias.read_bytes(), self.original)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_serialization_failure_preserves_previous_file_and_cleans_temp(self):
        output = self.root / "previous.json"
        atomic_json(output, {"saved": True})
        before = output.read_bytes()
        with self.assertRaises(ValueError):
            atomic_json(output, {"bad": float("nan")})
        self.assertEqual(output.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".lyria-*.tmp")), [])

    def test_replace_failure_preserves_previous_file_and_cleans_temp(self):
        output = self.root / "previous.txt"
        output.write_text("old", encoding="utf-8")
        with patch("lyricsync.storage.os.replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                atomic_text(output, "new")
        self.assertEqual(output.read_text(encoding="utf-8"), "old")
        self.assertEqual(list(self.root.glob(".lyria-*.tmp")), [])

    def test_concurrent_atomic_writers_do_not_share_temporary_path(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        import lyricsync.storage as storage
        output = self.root / "same.json"
        barrier = Barrier(2)
        real_replace = os.replace
        seen = []

        def replace(source, target):
            seen.append(source)
            if seen.count(source) == 1:
                barrier.wait(timeout=5)
            real_replace(source, target)

        with patch.object(storage.os, "replace", side_effect=replace):
            with ThreadPoolExecutor(max_workers=2) as pool:
                tasks = [pool.submit(atomic_json, output, {"writer": index}) for index in (1, 2)]
                for task in tasks:
                    task.result(timeout=10)
        self.assertEqual(len(set(seen)), 2)
        self.assertIn(json.loads(output.read_text(encoding="utf-8"))["writer"], (1, 2))
        self.assertEqual(list(self.root.glob(".lyria-*.tmp")), [])

    @unittest.skipUnless(os.name == "nt", "Windows sharing semantics")
    def test_transient_replace_lock_retries_the_same_complete_snapshot(self):
        import lyricsync.storage as storage
        output = self.root / "retry.json"
        atomic_json(output, {"old": True})
        before = output.read_bytes()
        real_replace = os.replace
        attempts = []

        def replace(source, target):
            attempts.append(source)
            if len(attempts) == 1:
                self.assertEqual(output.read_bytes(), before)
                error = PermissionError("transient Windows replace lock")
                error.winerror = 32
                raise error
            return real_replace(source, target)

        with patch.object(storage.os, "replace", side_effect=replace), \
                patch.object(storage.time, "sleep") as sleep:
            atomic_json(output, {"new": True}, [self.source])
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], attempts[1])
        sleep.assert_called_once()
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"new": True})
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse(list(self.root.glob(".lyria-*.tmp")))

    @unittest.skipUnless(os.name == "nt", "Windows sharing semantics")
    def test_persistent_replace_lock_is_bounded_and_preserves_previous_file(self):
        import lyricsync.storage as storage
        output = self.root / "locked.json"
        atomic_json(output, {"old": True})
        before = output.read_bytes()
        error = PermissionError("persistent Windows replace lock")
        error.winerror = 5
        with patch.object(storage.os, "replace", side_effect=error) as replace, \
                patch.object(storage.time, "sleep") as sleep:
            with self.assertRaises(PermissionError):
                atomic_json(output, {"new": True})
        self.assertLessEqual(replace.call_count, 5)
        self.assertEqual(sleep.call_count, replace.call_count - 1)
        self.assertEqual(output.read_bytes(), before)
        self.assertFalse(list(self.root.glob(".lyria-*.tmp")))

    def test_temporary_name_does_not_exceed_filesystem_limit_for_long_target(self):
        output = self.root / ("a" * 237 + ".json")
        try:
            output.write_text("{}", encoding="utf-8")
        except OSError as exc:
            self.skipTest("Filesystem does not support this target name: " + type(exc).__name__)
        atomic_json(output, {"saved": True})
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"saved": True})
        self.assertEqual(list(self.root.glob(".lyria-*.tmp")), [])

    def test_save_backup_alias_is_rejected_before_source_can_be_changed(self):
        output = self.root / "project.lyricsync.json"
        project = Project(inputs={"translation": str(self.source)})
        save_project(project, output)
        before = output.read_bytes()
        self.hardlink(output.name + ".bak")
        project.stage = "review"
        with self.assertRaisesRegex(ValueError, "不能直接覆盖原导入素材"):
            save_project(project, output)
        self.assertEqual(output.read_bytes(), before)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_symlink_output_still_rejected_and_missing_input_allowed(self):
        alias = self.root / "alias.json"
        try:
            alias.symlink_to(self.source)
        except (NotImplementedError, OSError) as exc:
            self.skipTest("Filesystem cannot create symlinks: " + type(exc).__name__)
        with self.assertRaises(ValueError):
            protect_output(alias, [])
        self.assertEqual(protect_output(self.root / "new.json", [self.root / "missing.txt"]), self.root / "new.json")
        self.assertEqual(self.source.read_bytes(), self.original)


if __name__ == "__main__":
    unittest.main()
