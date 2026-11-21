import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app


class SelfCheckReportingTests(unittest.TestCase):
    def test_failed_selfcheck_writes_failure_report_and_exits(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'selfcheck.json'
            with patch.object(app,'main',side_effect=RuntimeError('synthetic native failure')), \
                    patch.object(app.sys,'argv',['Lyria.exe','--self-check',str(target)]):
                with self.assertRaises(SystemExit) as caught:app.run_main()
            self.assertEqual(caught.exception.code,1)
            report=json.loads(target.read_text(encoding='utf8'))
            self.assertFalse(report['ok'])
            self.assertEqual(report['error'],'synthetic native failure')

    def test_regular_gui_errors_keep_their_existing_exception_path(self):
        error=RuntimeError('synthetic GUI failure')
        with patch.object(app,'main',side_effect=error), patch.object(app.sys,'argv',['Lyria.exe']):
            with self.assertRaises(RuntimeError) as caught:app.run_main()
        self.assertIs(caught.exception,error)
