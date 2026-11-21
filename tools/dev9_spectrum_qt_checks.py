"""Isolate spectrum Qt checks after a Windows multi-fixture process failure."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    checks = [
        'test_hide_restore_latest_view_playhead_and_release_space',
        'test_independent_height_drag_persist_without_edit',
        'test_presets_window_reset_and_pan_keep_global_reference',
        'test_hide_switch_source_and_show_does_not_accept_old_pixels',
        'test_no_ghost_viewport_when_resize_request_debounces',
        'test_independent_audio_actual_scrub_pan_wheel_and_cancel_height',
    ]
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QT_QPA_FONTDIR=r'C:\Windows\Fonts', PYTHONDONTWRITEBYTECODE='1',
               PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    results = []
    for check in checks:
        folder = out / check
        folder.mkdir()
        started = time.monotonic()
        with (folder / 'output.log').open('w', encoding='utf-8') as log:
            try:
                run = subprocess.run([sys.executable, '-B', str(root / 'tools/pytest_process.py'),
                    '-q', str(root / 'tests/test_qt_dev5_spectrum.py') + '::' + check,
                    '-p', 'no:cacheprovider', '--basetemp=' + str(folder / 'tmp'),
                    '--junitxml=' + str(folder / 'report.xml')],
                    cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=90)
                code = run.returncode
            except subprocess.TimeoutExpired:
                code = 'timeout'
        result = dict(check=check, exit=code, seconds=round(time.monotonic()-started, 3))
        if (folder / 'report.xml').exists():
            suite = next(ET.parse(folder / 'report.xml').getroot().iter('testsuite'))
            result.update({k: int(suite.attrib[k]) for k in ('tests', 'failures', 'errors', 'skipped')})
        results.append(result)
        (out / 'summary.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
        print(json.dumps(result), flush=True)
    return int(any(row['exit'] != 0 for row in results))


if __name__ == '__main__':
    raise SystemExit(main())
