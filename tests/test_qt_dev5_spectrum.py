"""Actual Windows Qt offscreen pixels/interaction, NOT native foreground acceptance."""
import json
import threading

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest

from test_qt_phase1_preview_workspace import ui
from test_qt_preview_visibility import application, find
from test_qt_preview_drag_gestures import chinese_fonts
from test_qt_dev4_spectrum_timeline import setup, evaluate
from test_dev4_spectrogram import encoded_audio, evidence_dir
from tools.qt_event_loop_wait import delay, wait_until


def click(ui,item):
    QTest.mouseClick(ui.window,Qt.LeftButton,Qt.NoModifier,item.mapToScene(item.boundingRect().center()).toPoint())
    delay(50)


def test_hide_restore_latest_view_playhead_and_release_space(ui,tmp_path):
    timeline,surface=setup(ui,tmp_path)
    ui.window.resize(1600,1200);delay(300)
    region=find(ui,'independentAudioRegion');audio=find(ui,'audioAnalysisPanel')
    before=ui.bridge.document.snapshot();height=timeline.height()
    for _ in range(3):
        old_lyrics=find(ui,'lyricTimelineRegion').height();released=audio.height()
        click(ui,find(ui,'audioVisibilityToggle'));delay(300)
        assert not region.isVisible() and not surface.property('active')
        assert not find(ui,'audioSplitHandle').isVisible()
        assert abs(timeline.height()-height)<1
        assert find(ui,'lyricTimelineRegion').height() >= old_lyrics+released
        evaluate(timeline,'view(401,421)')
        assert ui.bridge.command('media.seek',{'frame':410})
        delay(50)
        click(ui,find(ui,'audioVisibilityToggle'));delay(300)
        wait_until(lambda:surface.property('status')=='ready',timeout_ms=5000)
        assert region.isVisible() and surface.property('active')
        assert surface.property('firstFrame')==timeline.property('viewFirst')==401
        assert surface.property('frameSpan')==timeline.property('span')==20
        ah=find(ui,'audioPlayhead');th=find(ui,'timelinePlayhead')
        assert abs(ah.mapToScene(QPointF(ah.width()/2,0)).x()-th.mapToScene(QPointF(th.width()/2,0)).x())<.01
    assert ui.bridge.document.snapshot()==before
    assert ui.window.grabWindow().save(str(evidence_dir(tmp_path)/'dev5-independent-restored-offscreen.png'))


def test_independent_height_drag_persist_without_edit(ui,tmp_path):
    timeline,surface=setup(ui,tmp_path)
    ui.window.resize(1600,1200);delay(300)
    before=ui.bridge.document.snapshot();span=timeline.property('span')
    audio=find(ui,'audioAnalysisPanel');grip=find(ui,'audioSplitHandle')
    for delta in (45,-24,30):
        initial=audio.height()
        start=grip.mapToScene(grip.boundingRect().center()).toPoint();end=start+QPoint(0,delta)
        QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,start)
        QTest.mouseMove(ui.window,end,20)
        QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,end);delay(300)
        assert abs(audio.height()-(initial+delta))<2
        saved=json.loads((ui.bridge.storage_dir/'workspace-layout.json').read_text())
        assert abs(saved['audioHeight']-audio.height())<.01
        assert timeline.property('span')==span
        ruler=find(ui,'timelineRulerMouse')
        assert audio.mapToScene(QPointF(0,audio.height())).y()<ruler.mapToScene(QPointF()).y()
    assert ui.bridge.document.snapshot()==before
    assert ui.window.grabWindow().save(str(evidence_dir(tmp_path)/'dev5-independent-resized-offscreen.png'))


def test_presets_window_reset_and_pan_keep_global_reference(ui,tmp_path,monkeypatch):
    timeline,surface=setup(ui,tmp_path)
    import lyricsync.spectrogram as algorithm
    original=algorithm.global_calibration
    calls=[]
    # Worker imports a direct reference, so patch the binding it actually calls.
    import desktop_qt.spectrogram_item as native
    def checked(*args,**kwargs):
        calls.append(threading.current_thread().name)
        assert threading.current_thread() is not threading.main_thread()
        return original(*args,**kwargs)
    monkeypatch.setattr(native,'global_calibration',checked)
    for preset,window in [('balanced',512),('contrast',512),('balanced',1024)]:
        ui.bridge.command('view.layout',{'spectrumPreset':preset,'spectrumWindow':window})
        wait_until(lambda:surface.property('status')=='ready' and surface.property('metrics').get('displayPreset')==preset and surface.property('metrics').get('windowSamples')==window,timeout_ms=5000)
        reference=surface.property('metrics')['referenceAmplitude']
        for first,last in [(110,210),(122,125),(301,401)]:
            evaluate(timeline,f'view({first},{last})')
            wait_until(lambda:surface.property('status')=='ready',timeout_ms=5000)
            assert surface.property('metrics')['referenceAmplitude']==reference
            assert surface.property('metrics')['windowSamples']==window
        assert surface.property('metrics')['calibrationCentres']==100
    assert calls
    ui.bridge.command('view.layout',{'spectrumGain':9})
    click(ui,find(ui,'spectrumSettings'))
    delay(80)
    click(ui,find(ui,'spectrumReset'))
    wait_until(lambda:surface.property('status')=='ready' and surface.property('metrics').get('displayPreset')=='detail',timeout_ms=5000)
    assert surface.property('metrics')['gainDb']==0
    assert surface.property('metrics')['windowSamples']==384
    assert surface.property('metrics')['referenceKind']=='whole_source_sampled_q995'


def test_hide_switch_source_and_show_does_not_accept_old_pixels(ui,tmp_path):
    timeline,surface=setup(ui,tmp_path)
    click(ui,find(ui,'audioVisibilityToggle'))
    new=encoded_audio(tmp_path/'new-high-frequency.wav',3,7000)
    assert ui.bridge.command('media.open',{'path':str(new)})
    wait_until(lambda:ui.bridge.audioAnalysis.get('status')=='ready',timeout_ms=5000)
    evaluate(timeline,'view(41,61)')
    assert surface._image.isNull()
    click(ui,find(ui,'audioVisibilityToggle'))
    wait_until(lambda:surface.property('status')=='ready',timeout_ms=5000)
    assert __import__('os').path.normcase(surface.property('sourcePath'))==__import__('os').path.normcase(str(new))
    assert surface.property('firstFrame')==41 and surface.property('frameSpan')==20
    assert __import__('os').path.normcase(surface._worker.latest['source'][0])==__import__('os').path.normcase(str(new))


def test_hidden_source_tabs_keep_internal_readonly_guards(ui,tmp_path):
    timeline,surface=setup(ui,tmp_path)
    ui.window.resize(1600,1200);delay(300)
    before=ui.bridge.document.snapshot()
    # dev6 intentionally removes the three source tabs from normal review.
    # Keep the internal safety contract covered without reviving that UI.
    for key in ['main','ocr','vocal']:
        assert find(ui,'candidateSourceTab_'+key) is None
        assert find(ui,'candidateSourceUnderline_'+key) is None
    for name in ['main','main','ocr','ocr','vocal','vocal','main','vocal','main']:
        ui.bridge.switchCandidateSource(name);delay(30)
        assert ui.bridge.uiState['candidateSource']==name
        assert bool(timeline.property('readOnly'))==(name!='main')
    # An active edit still rejects an internal source switch.
    ui.bridge._editor_active=True
    try:
        ui.bridge.switchCandidateSource('ocr');delay(30)
        assert ui.bridge.uiState['candidateSource']=='main'
        assert not timeline.property('readOnly')
    finally: ui.bridge._editor_active=False
    assert ui.bridge.document.snapshot()==before


def test_no_ghost_viewport_when_resize_request_debounces(ui,tmp_path):
    timeline,surface=setup(ui,tmp_path)
    for width,height in [(900,700),(1500,1100),(1100,780),(1600,1200)]:
        ui.window.resize(width,height);delay(10)
    wait_until(lambda:surface.property('status')=='ready',timeout_ms=5000)
    assert not surface._image.isNull()
    assert not surface._worker.latest.get('pending',False)
    assert surface._worker.latest['sequence']==surface._sequence


def test_audio_scrub_cancel_window_deactivate_and_hide(ui,tmp_path):
    timeline,surface=setup(ui,tmp_path)
    area=find(ui,'audioNavigationMouse');before=ui.bridge.document.snapshot()
    for cancel in ['deactivate','hide']:
        point=area.mapToScene(QPointF(area.width()*.3,area.height()*.5)).toPoint()
        QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,point);delay(40)
        assert area.property('scrubbing') and timeline.property('interacting')
        if cancel=='deactivate': timeline.setProperty('windowActive',False)
        else: ui.bridge.command('view.layout',{'audioVisible':False})
        delay(40)
        assert not area.property('scrubbing') and not ui.bridge.media['scrubbing']
        QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,point)
        timeline.setProperty('windowActive',True)
        ui.bridge.command('view.layout',{'audioVisible':True});delay(100)
    assert ui.bridge.document.snapshot()==before


def test_small_workspace_scrollbar_complete_and_draggable(ui,tmp_path):
    timeline,surface=setup(ui,tmp_path)
    ui.window.resize(900,700);delay(300)
    workspace=find(ui,'makeScroll')
    assert workspace.property('contentHeight') is None
    delay(40)
    scroll=find(ui,'timelineHorizontalScroll')
    before=timeline.property('viewFirst');span=timeline.property('span')
    thumb_fraction=float(scroll.property('position'))+float(scroll.property('size'))/2
    start=scroll.mapToScene(QPointF(scroll.width()*thumb_fraction,scroll.height()/2)).toPoint()
    assert scroll.mapToItem(workspace,QPointF()).y()>=0
    assert scroll.mapToItem(workspace,QPointF(0,scroll.height())).y()<=workspace.height()
    QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,start)
    QTest.mouseMove(ui.window,start+QPoint(75,0),30)
    QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,start+QPoint(75,0));delay(60)
    assert timeline.property('viewFirst')>before
    assert timeline.property('span')==span
    assert surface.property('firstFrame')==timeline.property('viewFirst')
    assert ui.window.grabWindow().save(str(evidence_dir(tmp_path)/'dev5-small-scrolled-offscreen.png'))


def test_independent_audio_actual_scrub_pan_wheel_and_cancel_height(ui,tmp_path):
    from test_qt_dev3_audio_timeline import choose_audio_mode
    from test_qt_timeline_input import wheel
    from PySide6.QtWidgets import QApplication
    timeline,surface=setup(ui,tmp_path)
    ui.window.resize(1600,1200);delay(300)
    before=ui.bridge.document.snapshot()
    area=find(ui,'audioNavigationMouse')
    for mode in ('waveform','spectrum'):
        choose_audio_mode(ui,mode)
        evaluate(timeline,'view(201,401)');delay(40)
        start=area.mapToScene(QPointF(area.width()*.2,area.height()/2)).toPoint()
        end=area.mapToScene(QPointF(area.width()*.7,area.height()/2)).toPoint()
        QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,start)
        QTest.mouseMove(ui.window,end,30)
        assert area.property('scrubbing')
        assert abs(ui.bridge.media['playheadFrame']-341)<=1
        QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,end);delay(50)
        assert not area.property('scrubbing') and not ui.bridge.media['scrubbing']
        assert abs(find(ui,'audioPlayhead').mapToScene(QPointF(.75,0)).x()-find(ui,'timelinePlayhead').mapToScene(QPointF(.75,0)).x())<1
        span=timeline.property('span');first=timeline.property('viewFirst')
        QTest.mousePress(ui.window,Qt.MiddleButton,Qt.NoModifier,end)
        QTest.mouseMove(ui.window,end-QPoint(60,0),30)
        QTest.mouseRelease(ui.window,Qt.MiddleButton,Qt.NoModifier,end-QPoint(60,0));delay(40)
        assert timeline.property('viewFirst')>first and timeline.property('span')==span
        wheel(QApplication.instance(),ui.window,area,120,Qt.AltModifier)
        assert timeline.property('span')<span
        assert surface.property('frameSpan')==timeline.property('span')
        assert surface.property('firstFrame')==timeline.property('viewFirst')
    grip=find(ui,'audioSplitHandle');saved=ui.bridge.uiState['workspaceLayout']['audioHeight']
    disk=(ui.bridge.storage_dir/'workspace-layout.json').read_bytes()
    point=grip.mapToScene(grip.boundingRect().center()).toPoint()
    QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,point)
    QTest.mouseMove(ui.window,point+QPoint(0,45),30)
    assert timeline.property('resizingAudioHeight')>saved
    grip.ungrabMouse();delay(50)
    QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,point+QPoint(0,45))
    assert timeline.property('resizingAudioHeight')==-1
    assert find(ui,'audioAnalysisPanel').height()==saved
    assert (ui.bridge.storage_dir/'workspace-layout.json').read_bytes()==disk
    assert ui.bridge.document.snapshot()==before


def test_display_controls_stay_inside_minimum_window(ui,tmp_path):
    timeline,surface=setup(ui,tmp_path)
    for width in (760,900):
        ui.window.resize(width,700);delay(250)
        strip=find(ui,'independentAudioRegion')
        for name in ('timelineAudioModeSwitch','spectrumPresetSelector','spectrumSettings','audioSourceSelector'):
            item=find(ui,name);point=item.mapToItem(strip,QPointF())
            assert 0<=point.x() and point.x()+item.width()<=strip.width()+.01,(name,width,point.x(),item.width(),strip.width())
