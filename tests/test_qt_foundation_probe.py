"""Run the shipping CLI test hook, including its no-overwrite safety guard."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
pytest.importorskip('PySide6')

ROOT = Path(__file__).resolve().parents[1]


def invoke(report, *extra):
    env = os.environ.copy()
    env.update(QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', PYTHONUTF8='1')
    return subprocess.run([sys.executable, str(ROOT/'app_qt.py'), '--foundation-self-check', str(report), *extra],
                          cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8', timeout=90)


def test_live_qml_foundation_probe(tmp_path):
    report = tmp_path/'fresh'/'result.json'
    result = invoke(report)
    assert result.returncode == 0, result.stdout + result.stderr + (report.read_text(encoding='utf-8') if report.exists() else '')
    data = json.loads(report.read_text(encoding='utf-8'))
    assert data['ok'] and data['qml_window_loaded'] and data['screenshot_saved']
    assert len(data['checks']) == 11
    assert all(check['status'] == 'passed' for check in data['checks'])
    assert 'user project acceptance' in data['not_verified']


def test_probe_refuses_existing_directory(tmp_path):
    sentinel = tmp_path/'existing.lyria'
    sentinel.write_text('must not change', encoding='utf-8')
    result = invoke(tmp_path/'result.json')
    assert result.returncode != 0
    assert sentinel.read_text(encoding='utf-8') == 'must not change'
    assert not (tmp_path/'storage').exists()


def test_probe_rejects_user_project_input(tmp_path):
    report = tmp_path/'fresh'/'result.json'
    result = invoke(report, '--project', str(tmp_path/'user.lyria'))
    assert result.returncode != 0
    assert not report.parent.exists()