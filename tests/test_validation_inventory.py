"""Freeze the actual QML module declarations as well as Python/QML source."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools import validate_qt_cloud


class SourceInventoryTests(unittest.TestCase):
    def test_qmldir_change_and_addition_are_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);qml=root/'desktop_qt/qml';qml.mkdir(parents=True)
            (qml/'Main.qml').write_text('import QtQuick\nItem {}')
            target=qml/'qmldir';target.write_text('singleton Theme 1.0 Theme.qml\n')
            with patch.object(validate_qt_cloud,'ROOT',root):
                before=validate_qt_cloud.source_inventory()
                target.write_text('singleton Theme 1.0 Missing.qml\n')
                after=validate_qt_cloud.source_inventory()
                self.assertNotEqual(before['desktop_qt/qml/qmldir'],after['desktop_qt/qml/qmldir'])
                nested=qml/'Nested';nested.mkdir();(nested/'qmldir').write_text('module Nested\n')
                self.assertIn('desktop_qt/qml/Nested/qmldir',validate_qt_cloud.source_inventory())

    def test_machine_manifest_is_bound_but_cache_is_not(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'tools').mkdir()
            path=root/'tools/portable_runtime_manifest.json';path.write_text('{}')
            cache=root/'tools/__pycache__';cache.mkdir();(cache/'ignored.py').write_text('pass')
            with patch.object(validate_qt_cloud,'ROOT',root):
                snapshot=validate_qt_cloud.source_inventory()
                self.assertEqual(set(snapshot),{'tools/portable_runtime_manifest.json'})
                path.write_text('{"changed":true}')
                self.assertNotEqual(snapshot,validate_qt_cloud.source_inventory())


if __name__=='__main__':unittest.main()
