"""No Qt, no user profiles, no subprocess launches."""
import importlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from lyricsync import portable_profile as profile
from desktop_qt import local_alignment_settings as settings
from tools.create_portable_launchers import create, launcher_text, EDITIONS

class PortableProfileTests(unittest.TestCase):
    def test_source_mode_defers_to_edition_qt_appdata(self):
        with patch.object(sys, "frozen", False, create=True):
            self.assertIsNone(profile.default_portable_storage())
        with patch("lyricsync.__edition__", ""):
            self.assertEqual(profile.application_name(), "Lyria-v0.28-main")
        with patch("lyricsync.__edition__", "OCR修改"):
            self.assertEqual(profile.application_name(), "Lyria-v0.28-ocr-modified")

    def test_direct_exe_profiles_move_with_delivery_and_remain_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            old = Path(directory) / "original package"
            for label, edition in (("v0.24", ""), ("v0.24 OCR修改", "OCR修改")):
                exe = old / label / "Lyria.exe"
                exe.parent.mkdir(parents=True)
                exe.touch()
                with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(exe)), patch("lyricsync.__edition__", edition):
                    location = Path(profile.default_portable_storage())
                    location.mkdir(parents=True)
                    (location / "marker").write_text(label)
            moved = Path(directory) / "moved package 中文"
            shutil.move(str(old), str(moved))
            locations = []
            for label, edition in (("v0.24", ""), ("v0.24 OCR修改", "OCR修改")):
                with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(moved / label / "Lyria.exe")), patch("lyricsync.__edition__", edition):
                    location = Path(profile.default_portable_storage())
                    locations.append(location)
                    self.assertEqual((location / "marker").read_text(), label)
                    self.assertTrue(location.is_relative_to(moved))
            self.assertNotEqual(*locations)

    def test_shared_runtime_not_persisted_and_external_choice_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "shared-runtime" / "python.exe"
            external = str(Path(directory) / "user runtime" / "python.exe")
            with patch("lyricsync.shared_runtime.discover_shared_runtime", return_value=runtime):
                result = settings.save(directory, {"runtime_python":str(runtime), "reading_runtime_python":external, "cache_root": "my-models"})
                self.assertEqual(result["runtime_python"], "")
                self.assertEqual(settings.load(directory),result)
                self.assertEqual(result["reading_runtime_python"], external)
                self.assertEqual(result["cache_root"], "my-models")

    def test_invalid_bundle_preserves_external_preferences(self):
        with patch("lyricsync.shared_runtime.discover_shared_runtime", side_effect=ValueError("invalid")):
            self.assertEqual(profile.portable_preferences({"runtime_python":"external.exe"}), {"runtime_python":"external.exe"})

    def test_launchers_relative_unicode_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for edition in EDITIONS:
                (root/edition).mkdir()
                (root/edition/"Lyria.exe").touch()
            paths=create(root)
            for path, edition in zip(paths,EDITIONS):
                text = path.read_text("utf-16")
                self.assertIn("WScript.ScriptFullName", text)
                self.assertIn(edition, text)
                self.assertNotIn(str(root), text)
                self.assertNotIn("--storage-dir", text)
                self.assertNotIn("CodexWorkspace", text)
            with self.assertRaises(FileExistsError): create(root)
        with self.assertRaises(ValueError): launcher_text("../escape")

    def test_entry_point_preserves_explicit_storage(self):
        text=(Path(__file__).parents[1]/"app_qt.py").read_text("utf-8")
        self.assertIn("if not args.storage_dir:\n        args.storage_dir = default_portable_storage()",text)
        self.assertIn("QCoreApplication.setApplicationName(application_name())",text)

if __name__ == "__main__":
    unittest.main()
