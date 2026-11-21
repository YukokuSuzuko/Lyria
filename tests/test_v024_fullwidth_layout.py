from pathlib import Path
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from test_qt_phase1_preview_workspace import ui, application
from test_qt_preview_visibility import find, install_cues
from tools.qt_event_loop_wait import delay

def test_outer_split_does_not_persist_audio_height():
    qml=(Path(__file__).parents[1]/'desktop_qt/qml/Main.qml').read_text(encoding='utf-8')
    outer=qml.split('id: verticalSplit',1)[1].split('handle: Rectangle',1)[0]
    assert 'audioHeight:' not in outer
    assert 'SplitView.fillHeight: !window.narrow' not in qml

@pytest.mark.parametrize('role,name',[('translation','fullTranslation_m0'),('source_import','fullSource_m0')])
def test_full_width_edit_then_project_undo_updates_text(ui,role,name):
    ui.window.resize(1400,800)
    p=install_cues(ui,entries=[(0.,2.,'Original')])
    p.mappings[0].text_role=role
    p.mappings[0].text_override='Original'
    ui.bridge._changed()
    ui.window.setProperty('fullWidthReview',True);delay(200)
    editor=find(ui,name)
    assert editor is not None and editor.property('text')=='Original'
    editor.setProperty('editing',True);editor.forceActiveFocus()
    editor.setProperty('text','Edited')
    delay(100)
    editor.setProperty('editing',False)
    find(ui,'fullWidthReviewWorkspace').forceActiveFocus()
    delay(50)
    ui.bridge.command('history.undo',{})
    delay(100)
    assert find(ui,name).property('text')=='Original'
    ui.bridge.command('history.redo',{})
    delay(100)
    assert find(ui,name).property('text')=='Edited'

def test_workspace_hold_never_persists_or_freezes_minimum_audio(ui):
    from test_qt_dev4_spectrum_timeline import evaluate
    ui.window.resize(1600,1200)
    install_cues(ui,entries=[(0.,2.,'Original')])
    ui.bridge._audio_analysis=dict(status='loading',durationSeconds=2.)
    ui.bridge.audioAnalysisChanged.emit();ui.bridge.stateChanged.emit();delay(150)
    timeline=find(ui,'frameTimeline')
    assert not ui.bridge.state['workspaceLayout']['audioHeightExplicit']
    # The minimum-sized state must remain responsive when space returns.
    timeline.setProperty('resizingAudioHeight',36.)
    assert timeline.property('audioHeight')==36.
    evaluate(timeline,'holdAudioHeightForWorkspaceResize()')
    assert timeline.property('workspaceAudioHeight')==-1.
    timeline.setProperty('resizingAudioHeight',-1.)
    assert timeline.property('audioHeight')>36.
    evaluate(timeline,'holdAudioHeightForWorkspaceResize()')
    assert timeline.property('workspaceAudioHeight')==timeline.property('audioHeight')
    assert not ui.bridge.state['workspaceLayout']['audioHeightExplicit']
    # A genuine window resize returns to automatic budget fitting.
    ui.window.resize(1600,1100);delay(80)
    assert timeline.property('workspaceAudioHeight')==-1.
    assert not ui.bridge.state['workspaceLayout']['audioHeightExplicit']
