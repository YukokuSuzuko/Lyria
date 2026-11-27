"""Full workspace regressions, not isolated PreviewPanel gestures."""
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from desktop_qt.controller import Bridge
from lyricsync.model import Lyric, Visual
from test_qt_preview_visibility import application, install_cues, present, find, ROOT
from test_qt_preview_drag_gestures import center, move_steps, settled, chinese_fonts
from tools.qt_event_loop_wait import delay, wait_until

@pytest.fixture
def ui(application, tmp_path):
    from test_qt_timeline_input import timeline_session
    with timeline_session(application, tmp_path / 'recovery') as (bridge, window):
        result = SimpleNamespace(app=application, bridge=bridge, window=window)
        result.panel = find(result, 'mainPreviewPanel')
        yield result

@pytest.mark.parametrize('width,height', [(1400, 700), (900, 700)])
def test_full_workspace_upward_drag_keeps_pointer_and_committed_pixels(ui, width, height):
    ui.window.resize(width, height)
    install_cues(ui, resolution=(640,360), entries=[(0.,2.,'Subtitle drag')])
    present(ui, 1); settled(ui); delay(100)
    workspace = find(ui, 'makeScroll')
    assert workspace.property('contentItem') is None  # no outer scroll surface
    before_workspace = (workspace.x(), workspace.y(), workspace.width(), workspace.height())
    for _ in range(2):
        row = find(ui, 'previewSubtitle_0'); start = center(row); end = start + QPoint(0,-45)
        QTest.mousePress(ui.window, Qt.LeftButton, Qt.NoModifier, start)
        move_steps(ui,start,end)
        assert ui.panel.property('subtitleDragging'), 'outer scroll stole the subtitle pointer'
        assert ui.panel.property('subtitleDragY') < -30
        before_release = (row.x(),row.y())
        QTest.mouseRelease(ui.window, Qt.LeftButton, Qt.NoModifier, end)
        settled(ui); delay(50)
        row = find(ui,'previewSubtitle_0')
        assert abs(row.y()-before_release[1]) < 1.5
        assert (workspace.x(), workspace.y(), workspace.width(), workspace.height()) == before_workspace


def test_selected_evidence_and_hidden_cue_hint_do_not_resize_workspace(ui):
    ui.window.resize(1400, 700)
    p=install_cues(ui,resolution=(640,360),entries=[(0.,.5,'Short'),(.5,2.,'Long')])
    p.lyrics=[Lyric('l0','Short',0.,.5),Lyric('l1','Long source text '*50,.5,2.)]
    p.visuals=[Visual('v0','Short',0.,.5,[],1.,1),Visual('v1','Long OCR text '*50,.5,2.,[],1.,1)]
    for i,m in enumerate(p.mappings):m.lyric_ids=['l'+str(i)];m.visual_ids=['v'+str(i)]
    ui.bridge._changed();settled(ui);present(ui,1);delay(100)
    names=['mainPreviewPanel','mainSplit','verticalSplit','frameTimeline','videoStage']
    # mainSplit has no objectName in earlier builds; inspect the visible panels.
    names=[n for n in names if find(ui,n) is not None]
    def geometry():return {n:(find(ui,n).y(),find(ui,n).width(),find(ui,n).height()) for n in names}
    before=geometry()
    for frame,identity in [(8,'m1'),(1,'m0'),(8,'m1')]:
        ui.bridge.selected=identity;ui.bridge._refresh(False);present(ui,frame);delay(80)
        assert geometry()==before, (before,geometry())

@pytest.mark.parametrize('cancel', ['resize','cue'])
def test_full_workspace_interrupted_drag_never_commits(ui, cancel):
    ui.window.resize(1400,700)
    install_cues(ui,resolution=(640,360),entries=[(0.,.5,'First'),(.5,2.,'Second')])
    present(ui,1);settled(ui);delay(70)
    before=ui.bridge.document.snapshot()
    start=center(find(ui,'previewSubtitle_0'));end=start+QPoint(20,-35)
    QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,start);move_steps(ui,start,end)
    assert ui.panel.property('subtitleDragging')
    if cancel=='resize':ui.window.resize(1250,650)
    else:present(ui,8)
    delay(60)
    assert not ui.panel.property('subtitleDragging')
    QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,end);delay(60)
    assert ui.bridge.document.snapshot()==before


def test_full_workspace_shift_axis_and_async_refresh_keep_gesture(ui):
    ui.window.resize(1400,700)
    install_cues(ui,resolution=(640,360),entries=[(0.,2.,'One'),(0.,2.,'Two')])
    present(ui,1);settled(ui);delay(60)
    start=center(find(ui,'previewSubtitle_0'));end=start+QPoint(12,-40)
    QTest.keyPress(ui.window,Qt.Key_Shift)
    QTest.mousePress(ui.window,Qt.LeftButton,Qt.ShiftModifier,start)
    move_steps(ui,start,end)
    assert ui.panel.property('subtitleDragging')
    assert ui.panel.property('subtitleDragX')==0
    assert ui.panel.property('subtitleDragY') < -30
    for _ in range(5):ui.bridge.mediaChanged.emit();delay(10)
    assert ui.panel.property('subtitleDragging')
    before=[find(ui,'previewSubtitle_'+str(n)).y() for n in range(2)]
    QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.ShiftModifier,end)
    QTest.keyRelease(ui.window,Qt.Key_Shift)
    settled(ui);delay(50)
    after=[find(ui,'previewSubtitle_'+str(n)).y() for n in range(2)]
    assert all(abs(a-b)<1.5 for a,b in zip(before,after))


def test_long_evidence_is_scrollable_without_resizing(ui):
    ui.window.resize(1400,700)
    p=install_cues(ui,resolution=(640,360),entries=[(0.,2.,'One')])
    p.lyrics=[Lyric('long','Source evidence '*200,0.,2.)]
    p.mappings[0].lyric_ids=['long']
    ui.bridge._changed();settled(ui);present(ui,1);delay(60)
    evidence=find(ui,'reviewEvidencePanel');scroll=find(ui,'reviewEvidenceScroll')
    content=scroll.property('contentItem')
    assert scroll.property('contentHeight') > scroll.property('availableHeight') * 2
    before=(ui.panel.height(),evidence.height())
    content.setProperty('contentY',scroll.property('contentHeight')-scroll.property('availableHeight'))
    delay(50)
    assert content.property('contentY')>0
    assert (ui.panel.height(),evidence.height())==before


def test_hidden_cue_hint_keeps_player_geometry_and_remains_visible(ui, tmp_path):
    ui.window.resize(1400,700)
    install_cues(ui,resolution=(640,360),entries=[(0.,2.,'One')])
    present(ui,1);settled(ui);delay(60)
    stage=find(ui,'videoStage');before=(stage.width(),stage.height(),ui.panel.height())
    for hidden in (True,False,True):
        ui.bridge._visible_ids=set() if hidden else {'m0'}
        ui.bridge.mediaChanged.emit();delay(40)
        assert (stage.width(),stage.height(),ui.panel.height())==before
        hint=find(ui,'hiddenCueHint')
        assert bool(hint.property('text').strip())==hidden
    assert ui.window.grabWindow().save(str(tmp_path/'phase1-workspace-stable.png'))

@pytest.mark.parametrize('width,height', [(1400,1000),(900,900)])
def test_final_workspace_visual_evidence(ui,width,height):
    from pathlib import Path
    ui.window.resize(width,height)
    p=install_cues(ui,resolution=(640,360),entries=[(0.,2.,'字幕拖动稳定性检查')])
    p.lyrics=[Lyric('long','原文证据可以完整滚动查看。'*40,0.,2.)]
    p.visuals=[Visual('v0','画面 OCR 证据。'*30,0.,2.,[],1.,1)]
    p.mappings[0].lyric_ids=['long'];p.mappings[0].visual_ids=['v0']
    p.settings['font']='Microsoft YaHei UI'
    ui.bridge._changed();settled(ui);present(ui,1);delay(120)
    out=ROOT.parent/'reports'/'0.2-preview-final-20261110'
    out.mkdir(parents=True,exist_ok=True)
    assert ui.window.grabWindow().save(str(out/f'workspace-{width}-top.png'))
    scroll=find(ui,'reviewEvidenceScroll');content=scroll.property('contentItem')
    content.setProperty('contentY',scroll.property('contentHeight')-scroll.property('availableHeight'))
    delay(60)
    make=find(ui,'makeScroll')
    assert make.property('contentItem') is None
    timeline=find(ui,'frameTimeline')
    assert timeline.mapToScene(QPoint(0,0)).y() >= 0
    assert timeline.mapToScene(QPoint(0,round(timeline.height()))).y() <= ui.window.height()
    delay(100)
    assert ui.window.grabWindow().save(str(out/f'workspace-{width}-scrolled.png'))
