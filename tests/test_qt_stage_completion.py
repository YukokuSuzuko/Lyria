"""Stage badges are evidence summaries, never page-visit or quality claims."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication
from desktop_qt.controller import Bridge
from lyricsync.frames import FrameIndex
from lyricsync.model import Mapping, Translation, Visual


@pytest.fixture
def bridge(tmp_path):
    app = QApplication.instance() or QApplication([])
    obj = Bridge(storage_dir=tmp_path / 'state')
    yield obj
    obj.shutdown()
    app.processEvents()


def test_visiting_stages_does_not_complete_them(bridge):
    for stage in ('import', 'ocr', 'review', 'export'):
        assert bridge.command('stage.set', {'stage': stage})
        assert bridge.uiState['stageCompletion'] == {'import':False,'ocr':False,'review':False}


def test_manual_rows_are_not_ocr_evidence(bridge):
    p = bridge.document.project
    p.translations = [Translation('t', '手动歌词')]
    p.mappings = [Mapping('m', ['t'], start=0, end=1)]
    bridge.media_controller.index = FrameIndex([0., .5], 1.)
    bridge._refresh()
    assert bridge.uiState['stageCompletion'] == {'import':True,'ocr':False,'review':False}
    p.mappings[0].confirmed = True
    bridge._refresh()
    assert bridge.uiState['stageCompletion'] == {'import':True,'ocr':False,'review':True}


def test_only_completed_nonempty_scan_marks_ocr(bridge):
    p = bridge.document.project
    p.visuals = [Visual('v', '实际识别', 0, 1, [0, 0, 10, 10], .9, 3)]
    p.video['ocr_metrics'] = {'ocr_calls':3}
    for stage in ('new', 'vision', 'matching', 'cancelled', 'failed'):
        p.stage = stage
        bridge._refresh()
        assert not bridge.uiState['stageCompletion']['ocr']
    for stage in ('review', 'ready'):
        p.stage = stage
        bridge._refresh()
        assert bridge.uiState['stageCompletion']['ocr']
    p.visuals = []
    bridge._refresh()
    assert not bridge.uiState['stageCompletion']['ocr']
    assert not bridge.uiState['stageCompletion']['review']


def test_state_completion_is_detached_and_clears_with_project(bridge):
    state = bridge.uiState
    state['stageCompletion']['ocr'] = True
    assert not bridge.uiState['stageCompletion']['ocr']
    bridge.media_controller.index = FrameIndex([0., .5], 1.)
    bridge._refresh()
    assert bridge.uiState['stageCompletion']['import']
    assert bridge.command('project.new', {})
    assert bridge.uiState['stageCompletion'] == {'import':False,'ocr':False,'review':False}


def test_ass_literal_braces_show_compatibility_warning_only_when_needed(bridge):
    p=bridge.document.project
    p.translations=[Translation('t','普通文字')]
    p.mappings=[Mapping('m',['t'],start=0,end=1)]
    bridge._refresh()
    assert bridge.uiState['export']['compatibilityWarnings']==[]
    p.mappings[0].text_override=r'原样{\alpha&HFF&}'
    bridge._refresh()
    assert 'ASS 格式下可能被识别为特效代码' in bridge.uiState['export']['compatibilityWarnings'][0]
    p.mappings[0].text_override='普通文字'
    bridge._refresh()
    assert bridge.uiState['export']['compatibilityWarnings']==[]
