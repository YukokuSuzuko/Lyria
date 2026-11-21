"""A playback tick between press/release cannot retarget a confirmation."""
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from test_qt_review_selection import application, ui, find, settle


def test_confirmation_keeps_pressed_identity_across_playback_tick(ui):
    button=find(ui.window,'confirmCurrent')
    pos=button.mapToScene(QPointF(button.width()/2,button.height()/2)).toPoint()
    ui.bridge.media_controller._state['playing']=True
    QTest.mousePress(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,pos)
    ui.bridge.selected='r41';ui.bridge._refresh(False)
    QTest.qWait(20)
    QTest.mouseRelease(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,pos)
    assert ui.bridge._row('r40').confirmed
    assert not ui.bridge._row('r41').confirmed
    assert ui.bridge.selected=='r41'
    assert not ui.transport


def test_player_splitter_remembers_dragged_width_on_window_resize(ui):
    handle=find(ui.window,'mainSplitHandle');preview=find(ui.window,'mainPreviewPanel')
    start=handle.mapToScene(QPointF(handle.width()/2,handle.height()/2)).toPoint()
    before=preview.width()
    QTest.mousePress(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start)
    QTest.mouseMove(ui.window,start-QPoint(90,0),50)
    QTest.mouseRelease(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start-QPoint(90,0))
    QTest.qWait(100)
    assert preview.width()<before-50
    ratio=preview.width()/handle.parentItem().width()
    ui.window.resize(1600,940);QTest.qWait(120)
    assert abs(preview.width()/handle.parentItem().width()-ratio)<.01


def test_timing_strip_compacts_at_wide_size_and_wraps_without_overlap(ui):
    ui.window.resize(1800,1000);QTest.qWait(160)
    strip=find(ui.window,'reviewTimingStrip')
    assert strip is not None and strip.height()<=35
    for width in (760,1100,1800):
        ui.window.resize(width,1000);QTest.qWait(120)
        children=[c for c in strip.childItems() if c.isVisible() and c.width()>0 and c.height()>0]
        for i,item in enumerate(children):
            assert item.x()>=0 and item.x()+item.width()<=strip.width()+1
            for other in children[i+1:]:
                overlapX=min(item.x()+item.width(),other.x()+other.width())-max(item.x(),other.x())
                overlapY=min(item.y()+item.height(),other.y()+other.height())-max(item.y(),other.y())
                assert overlapX<=0 or overlapY<=0


def test_split_handles_show_on_hover_and_vertical_drag_changes_preview(ui,tmp_path):
    import json
    from lyricsync.audio_analysis import analyze_audio
    from test_dev4_spectrogram import encoded_audio
    from test_qt_dev4_spectrum_timeline import evaluate
    # A genuinely decoded source makes the independent plot/grip available;
    # no-audio state correctly has only its compact status and no visible grip.
    source=encoded_audio(tmp_path/'layout-reference.wav',150,rate=22050,silence=False)
    ui.bridge._audio_analysis=analyze_audio(source,150)
    ui.bridge.media_controller._state.update(ready=True,displayedFrame=405,
        requestedFrame=405,positionSeconds=40.4,playing=False,totalFrames=1500)
    ui.bridge.audioAnalysisChanged.emit();ui.bridge._refresh(False)
    ui.window.resize(1600,1200);QTest.qWait(180)
    preview=find(ui.window,'mainPreviewPanel');timeline=find(ui.window,'frameTimeline')
    audio=find(ui.window,'audioAnalysisPanel');split=find(ui.window,'verticalSplit')
    original=ui.bridge.document.snapshot();selected=ui.bridge.selected;span=timeline.property('span')
    # Media notifications are queued independently of the directly seeded state.
    # Wait for the real timeline binding and its initial fit before testing a drag.
    settle(ui.app, lambda: timeline.property('total') == 1500)
    evaluate(timeline,'view(390,430)');QTest.qWait(50)
    span=timeline.property('span');first=timeline.property('viewFirst')
    assert first == 390 and span == 40
    audio_grip=find(ui.window,'audioSplitHandle')
    assert audio_grip is not None and audio_grip.isVisible()
    assert audio.property('available') and audio.height()==160
    def assert_audio_sync():
        ruler=find(ui.window,'timelineRulerMouse')
        assert audio.width()==ruler.width()
        assert abs(audio.mapToScene(QPointF()).x()-ruler.mapToScene(QPointF()).x())<.01
        assert audio.mapToScene(QPointF(0,audio.height())).y()<ruler.mapToScene(QPointF()).y()
        assert audio.property('firstFrame')==timeline.property('viewFirst')==first, {key:timeline.property(key) for key in ('projectIdentity','sourceIdentity','manualView','followedId','total','current')}
        assert audio.property('frameSpan')==timeline.property('span')==span
        ah=find(ui.window,'audioPlayhead');th=find(ui.window,'timelinePlayhead')
        assert ah.isVisible() and th.isVisible()
        assert abs(ah.mapToScene(QPointF(ah.width()/2,0)).x()-th.mapToScene(QPointF(th.width()/2,0)).x())<.01
    assert_audio_sync()
    for handle_name in ('mainSplitHandle','audioSplitHandle'):
        handle=find(ui.window,handle_name)
        if handle_name=='mainSplitHandle':
            indicator=find(ui.window,'mainSplitIndicator')
        else:
            width=38 if handle_name=='audioSplitHandle' else 36
            candidates=[child for child in handle.childItems() if child.width()==width and child.height()==3]
            assert len(candidates)==1
            indicator=candidates[0]
        QTest.mouseMove(ui.window,QPoint(4,4));QTest.qWait(180)
        resting=indicator.opacity()
        assert resting<=(.5 if handle_name=='audioSplitHandle' else .25)
        point=handle.mapToScene(QPointF(handle.width()/2,handle.height()/2)).toPoint()
        QTest.mouseMove(ui.window,point);QTest.qWait(180)
        assert indicator.opacity()>.95 and indicator.opacity()>resting+.5
    # v0.22 restores the workspace-height grip alongside the independent audio grip.
    restored_handle = find(ui.window,'timelineSplitHandle')
    assert restored_handle is not None and restored_handle.isVisible() and restored_handle.height() >= 6
    for delta in (45,-45):
        before=preview.height();old_audio=audio.height();old_timeline=timeline.height()
        point=restored_handle.mapToScene(QPointF(restored_handle.width()/2,restored_handle.height()/2)).toPoint()
        QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,point)
        QTest.mouseMove(ui.window,point+QPoint(0,delta),50)
        QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,point+QPoint(0,delta));QTest.qWait(100)
        assert abs(timeline.height()-old_timeline+delta)<2
        assert abs(preview.height()-before-delta)<2 and audio.height()==old_audio
        assert timeline.height()>=timeline.property('minimumExpandedHeight')
        assert_audio_sync()
    # The separate audio grip resizes only the requested audio plot height;
    # all timing/view mappings and the main/candidate documents remain intact.
    for delta in (35,-20):
        old_audio=audio.height()
        point=audio_grip.mapToScene(QPointF(audio_grip.width()/2,audio_grip.height()/2)).toPoint()
        QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,point)
        QTest.mouseMove(ui.window,point+QPoint(0,delta),50)
        QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,point+QPoint(0,delta));QTest.qWait(180)
        assert abs(audio.height()-old_audio-delta)<2
        saved=json.loads((ui.bridge.storage_dir/'workspace-layout.json').read_text())
        assert abs(saved['audioHeight']-audio.height())<.01
        assert find(ui.window,'timelineTracks').height()>=3*timeline.property('rowHeight')
        assert_audio_sync()
    assert ui.window.grabWindow().save(str(tmp_path/'independent-audio-and-main-splits-offscreen.png'))
    assert ui.bridge.document.snapshot()==original and ui.bridge.selected==selected
    assert not ui.transport


def test_delete_selected_from_both_panels_and_multi_selection_is_reversible(ui):
    ui.window.requestActivate();QTest.qWait(40)
    original=ui.bridge.document.project.to_dict()
    for owner in (ui.view,find(ui.window,'frameTimeline')):
        ui.bridge.command('timeline.select',{'id':'r40'});owner.forceActiveFocus();QTest.qWait(30)
        QTest.keyClick(ui.window,Qt.Key.Key_Delete);QTest.qWait(60)
        assert 'r40' not in {m.id for m in ui.bridge.document.project.mappings}
        assert ui.bridge.command('history.undo',{}) and ui.bridge.document.project.to_dict()==original
    for identity in ('r40','r41'):ui.bridge.command('row.multiToggle',{'id':identity,'selected':True})
    ui.view.forceActiveFocus();QTest.keyClick(ui.window,Qt.Key.Key_Delete);QTest.qWait(60)
    assert not {'r40','r41'} & {m.id for m in ui.bridge.document.project.mappings}
    assert ui.bridge.command('history.undo',{}) and ui.bridge.document.project.to_dict()==original
    assert ui.bridge.command('history.redo',{})
    assert not {'r40','r41'} & {m.id for m in ui.bridge.document.project.mappings}


def test_delete_in_text_editor_only_deletes_character(ui):
    from test_qt_review_selection import enter_lyric_editing
    ui.window.requestActivate();QTest.qWait(30)
    editor=enter_lyric_editing(ui);editor.setProperty('cursorPosition',0)
    count=len(ui.bridge.document.project.mappings);before=editor.property('text')
    QTest.keyClick(ui.window,Qt.Key.Key_Delete);QTest.qWait(60)
    assert len(ui.bridge.document.project.mappings)==count
    assert editor.property('text')==before[1:]
