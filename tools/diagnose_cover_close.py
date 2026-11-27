"""Opt-in real attached-picture audio lifecycle stress; generated media only.

Run explicitly, serially, under an external process deadline. This diagnostic
must not be included in normal collection by renaming it to test_*.py.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
import pytest
from test_phase2_audio_editing import app, bridge, test_real_audio_with_embedded_cover_is_not_video as exercise_cover

@pytest.mark.parametrize('iteration', range(50))
@pytest.mark.parametrize('suffix,codec', [('.mp3', 'libmp3lame'), ('.m4a', 'aac')])
def test_cover_close(app, bridge, tmp_path, iteration, suffix, codec):
    exercise_cover(app, bridge, tmp_path, suffix, codec)

from test_phase2_audio_editing import test_audio_load_seek_step_analysis_save_reopen as exercise_wav

@pytest.mark.parametrize('iteration', range(100))
def test_wav_transport_close(app, bridge, tmp_path, iteration):
    exercise_wav(app, bridge, tmp_path)
