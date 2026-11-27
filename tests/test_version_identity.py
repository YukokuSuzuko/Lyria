"""Application and Windows packaging have one explicit version identity."""
from pathlib import Path
import subprocess
import sys
import unittest
from lyricsync import __version__, __display_version__, __edition__
ROOT=Path(__file__).resolve().parents[1]

class VersionIdentityTests(unittest.TestCase):
    def test_main_version_matches_v032_test(self):
        self.assertEqual(__version__, 'v0.3.4')
        self.assertEqual(__edition__, '测试版')
        self.assertEqual(__display_version__, __version__ + (' ' + __edition__ if __edition__ else ''))

    def test_cli_version_does_not_start_ui(self):
        result=subprocess.run([sys.executable,str(ROOT/'app_qt.py'),'--version'],
                              capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'Lyria '+__display_version__)

    def test_package_default_and_windows_resource_use_release_version(self):
        script=(ROOT/'packaging/build_windows_qt.ps1').read_text(encoding='utf8')
        self.assertIn('Lyria_'+__version__+'_Windows_x64_test',script)
        self.assertIn('Refusing to overwrite an existing portable package',script)
        spec=(ROOT/'packaging/LyriaQt.spec').read_text(encoding='utf8')
        self.assertIn('from lyricsync import __version__',spec)
        self.assertIn("StringStruct('FileVersion', __display_version__)",spec)
        self.assertIn("StringStruct('ProductVersion', __display_version__)",spec)
        self.assertIn('version=windows_version',spec)
