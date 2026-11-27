"""Offscreen icon/resource checks; no foreground window or package build."""
import ast
import os
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QImageReader
from PySide6.QtWidgets import QApplication
from desktop_qt.app_icon import application_icon_path, setup_application_icon


class ApplicationIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_ico_all_sizes_are_valid_and_decodable(self):
        data = application_icon_path().read_bytes()
        self.assertEqual(struct.unpack_from("<HHH", data), (0, 1, 7))
        sizes = []
        for i in range(7):
            width, height, _, _, _, bits, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + i * 16)
            sizes.append(width or 256)
            self.assertEqual(width, height)
            self.assertEqual(bits, 32)
            self.assertLessEqual(offset + size, len(data))
        self.assertEqual(sizes, [16, 24, 32, 48, 64, 128, 256])
        icon = setup_application_icon(self.app)
        self.assertFalse(icon.isNull())
        self.assertFalse(self.app.windowIcon().isNull())
        for size in sizes:
            pixmap = icon.pixmap(size, size)
            self.assertFalse(pixmap.isNull())
            self.assertEqual((pixmap.width(), pixmap.height()), (size, size))

    def test_png_and_svg_are_valid(self):
        from PySide6.QtSvg import QSvgRenderer
        assets = application_icon_path().parent
        reader = QImageReader(str(assets / "lyria.png"))
        self.assertTrue(reader.canRead())
        self.assertEqual((reader.size().width(), reader.size().height()), (1024, 1024))
        self.assertFalse(reader.read().isNull())
        self.assertTrue(QSvgRenderer(str(assets / "lyria.svg")).isValid())

    def test_titlebar_startup_and_packaging_references(self):
        qml = (ROOT / "desktop_qt/qml/Main.qml").read_text(encoding="utf-8")
        self.assertIn('objectName: "lyriaTitlebarIcon"', qml)
        self.assertIn('source: "../assets/app/lyria.png"', qml)
        entry = (ROOT / "app_qt.py").read_text(encoding="utf-8")
        ast.parse(entry)
        self.assertIn("setup_application_icon(app)", entry)
        spec = (ROOT / "LyriaQt.spec").read_text(encoding="utf-8")
        tree = ast.parse(spec)
        exe = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "EXE")
        icon_arg = next(k.value for k in exe.keywords if k.arg == "icon")
        self.assertIn("lyria.ico", ast.unparse(icon_arg))
        self.assertIn("'desktop_qt/assets'", spec)


if __name__ == "__main__":
    unittest.main()
