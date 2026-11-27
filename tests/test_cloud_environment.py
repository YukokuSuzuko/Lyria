"""Offline environment selection; no OCR executable is launched."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lyricsync.vision import TesseractOCR
from lyricsync.tesseract_process import installed_tessdata


class OCRExecutableSelectionTests(unittest.TestCase):
    def test_explicit_ocr_path_survives_removal_of_developer_machine_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root / "explicit-local-tesseract"
            executable.write_bytes(b"test placeholder, never executed")
            data = root / "tessdata"
            data.mkdir()
            model = b"test model fingerprint, never used for recognition"
            (data / "eng.traineddata").write_bytes(model)
            fake = SimpleNamespace(
                pytesseract=SimpleNamespace(tesseract_cmd=None),
                get_languages=lambda **kwargs: ["eng"],
                get_tesseract_version=lambda: "mocked-test-version",
            )
            with patch.dict(sys.modules, {"pytesseract": fake}), \
                    patch.dict(os.environ, {"LYRICSYNC_TESSERACT": str(executable),
                                            "TESSDATA_PREFIX": str(root / "other-data")}), \
                    patch("lyricsync.vision.resources", return_value=root / "resources"), \
                    patch("lyricsync.vision.shutil.which", return_value=None), \
                    patch("lyricsync.tesseract_process.installed_tessdata") as discover:
                provider = TesseractOCR(language="eng")
                self.assertEqual(os.environ["TESSDATA_PREFIX"], str(data))
                discover.assert_not_called()
            self.assertEqual(provider.executable, str(executable))
            self.assertEqual(fake.pytesseract.tesseract_cmd, str(executable))
            self.assertEqual(provider.signature["version"], "mocked-test-version")
            self.assertEqual(provider.signature["models"],
                             {"eng": hashlib.sha256(model).hexdigest()})

    def test_system_installation_hashes_discovered_models_and_preserves_prefix(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root / "bin" / "tesseract"
            executable.parent.mkdir()
            executable.write_bytes(b"test placeholder, never executed")
            data = root / "custom data 日本語"
            data.mkdir()
            model = b"different model fingerprint, never used for recognition"
            (data / "eng.traineddata").write_bytes(model)
            fake = SimpleNamespace(
                pytesseract=SimpleNamespace(tesseract_cmd=None),
                get_languages=lambda **kwargs: ["eng"],
                get_tesseract_version=lambda: "mocked-test-version",
            )
            with patch.dict(sys.modules, {"pytesseract": fake}), \
                    patch.dict(os.environ, {"LYRICSYNC_TESSERACT": str(executable),
                                            "TESSDATA_PREFIX": str(data)}), \
                    patch("lyricsync.vision.resources", return_value=root / "resources"), \
                    patch("lyricsync.tesseract_process._TesseractProcesses.check_output",
                          return_value=f'List of available languages in "{data}" (1):\neng\n') as probe:
                provider = TesseractOCR(language="eng")
                self.assertEqual(os.environ["TESSDATA_PREFIX"], str(data))
                self.assertEqual(probe.call_args.args[0], [str(executable), "--list-langs"])
            self.assertEqual(provider.signature["models"],
                             {"eng": hashlib.sha256(model).hexdigest()})


class InstalledTessdataTests(unittest.TestCase):
    def test_accepts_windows_paths_spaces_unicode_and_crlf(self):
        directory = r"C:\Lyria 日本語\resources\tesseract\tessdata/"
        output = f'List of available languages in "{directory}" (2):\r\neng\r\njpn\r\n'
        with patch("lyricsync.tesseract_process._TesseractProcesses.check_output", return_value=output):
            self.assertEqual(installed_tessdata("tesseract.exe"), Path(directory))

    def test_unknown_output_fails_clearly_instead_of_guessing_models(self):
        with patch("lyricsync.tesseract_process._TesseractProcesses.check_output", return_value="eng\n"), \
                self.assertRaisesRegex(RuntimeError, "未找到 Tesseract 语言包目录"):
            installed_tessdata("tesseract")


if __name__ == "__main__":
    unittest.main()
