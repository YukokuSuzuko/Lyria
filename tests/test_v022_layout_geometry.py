"""Actual offscreen root-coordinate mouse traces; not user foreground acceptance."""
import json
import pytest
from PySide6.QtCore import QPoint,QPointF,Qt
from PySide6.QtTest import QTest
from test_qt_phase1_preview_workspace import ui
from test_qt_preview_visibility import application,find,install_cues,present
from tools.qt_event_loop_wait import delay


def setup(ui,width=1400,height=1080):
    ui.window.resize(width,height)
    install_cues(ui,resolution=(640,360),entries=[(0.,2.,'Geometry')]);present(ui,1)
    ui.bridge._audio_analysis=dict(status='loading',durationSeconds=2.)
    ui.bridge.audioAnalysisChanged.emit();ui.bridge.stateChanged.emit();delay(350)


def geo(item):
    point=item.mapToScene(QPointF())
    return [point.x(),point.y(),item.width(),item.height()]


def trace_drag(ui,handle,deltas):
    start=handle.mapToScene(QPointF(handle.width()/2,handle.height()/2)).toPoint()
    QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,start);rows=[]
    for d in deltas:
        QTest.mouseMove(ui.window,start+QPoint(0,d));delay(35)
        scroll=find(ui,'makeScroll')
        rows.append(dict(delta=d,handle=geo(handle),preview=geo(find(ui,'mainPreviewPanel')),review=geo(find(ui,'sideWorkspace')),timeline=geo(find(ui,'frameTimeline')),audio=geo(find(ui,'audioAnalysisPanel')),lyric=geo(find(ui,'lyricTimelineRegion')),video=geo(find(ui,'videoViewport')),controls=geo(find(ui,'playerControls')),scroll=[scroll.height(),scroll.y()]))
    QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,start+QPoint(0,deltas[-1]));delay(40)
    return rows


@pytest.mark.parametrize('size',[(1400,1080),(900,700)])
def test_audio_divider_is_root_linear_and_only_redistributes_bottom(ui,tmp_path,size):
    setup(ui,*size);handle=find(ui,'audioSplitHandle')
    scroll=find(ui,'makeScroll')
    assert scroll.property('contentHeight') is None  # v0.23: only inner panes scroll
    assert 0 <= handle.mapToScene(QPointF()).y() <= ui.window.height()-handle.height()
    assert handle.isVisible() and handle.height()>=6
    # The fixed-height v0.23 workspace has finite space, unlike the old outer
    # scrolling page. Give the bottom pane room through its real divider first.
    if size[1] < 800:
        trace_drag(ui,find(ui,'timelineSplitHandle'),[0,-20,-40,-60,-80])
    timeline=find(ui,'frameTimeline')
    travel=min(50,int(timeline.property('maximumAudioHeight')-timeline.property('audioHeight')))
    assert travel>=10, (travel,geo(timeline))
    before={n:geo(find(ui,n)) for n in ['mainPreviewPanel','sideWorkspace','frameTimeline']}
    rows=trace_drag(ui,handle,[round(i*travel/10) for i in range(11)]);(tmp_path/'audio-geometry.json').write_text(json.dumps(rows,indent=2))
    for row in rows:
        assert row['preview']==before['mainPreviewPanel']
        assert row['review']==before['sideWorkspace']
        assert row['timeline']==before['frameTimeline']
        assert row['scroll']==rows[0]['scroll']
        assert abs(row['handle'][1]-rows[0]['handle'][1]-row['delta'])<=1,rows
        assert abs(row['audio'][3]-rows[0]['audio'][3]+row['lyric'][3]-rows[0]['lyric'][3])<=1


@pytest.mark.parametrize('size',[(1400,1080),(900,700)])
def test_player_divider_has_ten_continuous_root_positions(ui,tmp_path,size):
    setup(ui,*size);handle=find(ui,'playerControlsResizeHandle');assert handle.height()>=6
    rows=trace_drag(ui,handle,list(range(0,-51,-5)));(tmp_path/'player-geometry.json').write_text(json.dumps(rows,indent=2))
    assert len({round(row['controls'][3],2) for row in rows})==11,rows
    for row in rows:
        assert abs(row['handle'][1]-rows[0]['handle'][1]-row['delta'])<=1,rows
        assert row['review']==rows[0]['review'] and row['timeline']==rows[0]['timeline']


def test_upper_audio_divider_exists_and_moves(ui):
    setup(ui);handle=find(ui,'timelineSplitHandle');assert handle and handle.isVisible() and handle.height()>=6
    rows=trace_drag(ui,handle,[0,5,10,15,20])
    assert rows[-1]['handle'][1]-rows[0]['handle'][1]>15,rows


def test_unlocated_selected_range_does_not_draw_fake_left_tick(ui):
    setup(ui);p=ui.bridge.document.project;p.mappings[0].start=None;p.mappings[0].end=None;ui.bridge._changed();delay(80)
    assert not find(ui,'playerSelectedRange').isVisible()
    assert find(ui,'upperPlayheadThumb').width()==12


def test_full_width_review_switch_restores_four_panel_geometry(ui):
    setup(ui)
    names=['mainPreviewPanel','sideWorkspace','frameTimeline','audioAnalysisPanel']
    before={n:geo(find(ui,n)) for n in names}
    assert ui.window.setProperty('fullWidthReview',True);delay(200)
    wide=find(ui,'fullWidthReviewWorkspace');area=find(ui,'workspaceArea')
    assert wide.isVisible() and abs(wide.width()-area.width())<1
    assert not find(ui,'mainPreviewPanel').isVisible()
    assert ui.window.setProperty('fullWidthReview',False);delay(250)
    assert not wide.isVisible()
    assert before=={n:geo(find(ui,n)) for n in names}


def test_source_prefix_removed_without_changing_role_or_markers(ui,tmp_path):
    setup(ui);p=ui.bridge.document.project
    row=p.mappings[0];row.text_role='source_import';row.text_override='Actual source'
    ui.bridge._changed();delay(180)
    assert find(ui,'timelineText_m0').property('text')=='Actual source'
    assert row.text_role=='source_import'
    before=ui.bridge.document.snapshot()
    assert ui.window.grabWindow().save(str(tmp_path/'layout-and-rail.png'))
    assert ui.bridge.document.snapshot()==before

def test_spectrum_selectors_use_shared_value_options(ui):
    setup(ui)
    from test_qt_dev5_spectrum import click
    assert ui.bridge.command('view.layout', {'audioMode':'spectrum'})
    delay(100)
    click(ui,find(ui,'spectrumSettings'));delay(100)
    panel=find(ui,'audioAnalysisPanel')
    for selector,options_name,value_name in [('spectrumWindowSelector','windowOptions','windowSamples'),('spectrumPresetSelector','presetOptions','displayPreset')]:
        options=panel.property(options_name)
        if hasattr(options,'toVariant'):
            options=options.toVariant()
        box=find(ui,selector)
        assert box.property('count')==len(options)
        current=box.property('currentIndex')
        assert current>=0
        assert options[current]['value']==panel.property(value_name)
        assert box.property('currentText')==options[current]['label']


def test_wide_picture_continuous_controls_keep_picture_adjacent(ui,tmp_path):
    ui.window.resize(900,700)
    install_cues(ui,resolution=(1600,300),entries=[(0.,2.,'Wide picture')]);present(ui,1);delay(250)
    handle=find(ui,'playerControlsResizeHandle')
    rows=trace_drag(ui,handle,list(range(0,-51,-5)))
    assert len({round(row['controls'][3],2) for row in rows})==11,rows
    for row in rows:
        assert abs(row['handle'][1]-rows[0]['handle'][1]-row['delta'])<=1,rows
        assert row['review']==rows[0]['review'] and row['timeline']==rows[0]['timeline']
    video=find(ui,'videoPreview');controls=find(ui,'playerControls')
    rect=video.property('contentRect')
    bottom=video.mapToScene(QPointF(0,rect.bottom())).y()
    assert -.5<=controls.mapToScene(QPointF()).y()-bottom<=7
    (tmp_path/'wide-picture-geometry.json').write_text(json.dumps(rows,indent=2))
    assert ui.window.grabWindow().save(str(tmp_path/'wide-picture-after-drag.png'))
