"""Actual offscreen merged audio/timeline and centered-picture contracts."""
import os
import time
from pathlib import Path
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from test_qt_phase1_preview_workspace import ui
from test_qt_preview_drag_gestures import chinese_fonts
from test_qt_preview_visibility import application, find, install_cues, present
from tools.qt_event_loop_wait import delay, wait_until
from test_qt_dev3_audio_timeline import choose_audio_mode
from test_dev4_spectrogram import encoded_audio
from lyricsync.audio_analysis import analyze_audio


def test_audio_states_modes_reset_and_pixels(ui, tmp_path):
    ui.window.resize(1400, 1080)
    install_cues(ui, resolution=(640, 360), entries=[(0., 2., 'Phase two workspace')])
    present(ui, 1); delay(150)
    audio = find(ui, 'audioAnalysisPanel')
    assert audio and audio.isVisible()
    before=ui.bridge.document.snapshot()
    surface=find(ui,'timelineSpectrogram');ruler=find(ui,'timelineRulerMouse')
    for status in ['idle', 'loading', 'no_audio', 'error']:
        ui.bridge._audio_analysis = dict(status=status, error='fixture decode failure')
        ui.bridge.audioAnalysisChanged.emit(); ui.bridge.stateChanged.emit(); delay(30)
        assert find(ui, 'audioStatusMessage').isVisible()
        assert not audio.property('available')
        assert not surface.property('active')
        assert not surface.isVisible()
        # Audio analysis failure must not disable the shared video ruler.
        assert ruler.isEnabled() and ui.bridge.media['ready']
    # Actual encoded PCM supplies the new renderer; an invented tiny spectrum
    # array must not stand in for a high-resolution native source.
    path=encoded_audio(tmp_path/'workspace-modes.wav',2)
    ui.bridge._audio_analysis=analyze_audio(path,2)
    ui.bridge.audioAnalysisChanged.emit();ui.bridge.stateChanged.emit();delay(80)
    assert audio.property('available')
    assert surface.isVisible() and surface.property('displayMode') == 'waveform'
    choose_audio_mode(ui,'spectrum')
    assert surface.property('status')=='ready' and surface.property('active')
    assert surface.property('metrics')['sampleRate']==22050
    assert surface.property('displayMode') == 'spectrum'
    choose_audio_mode(ui,'waveform')
    wait_until(lambda: surface.property('status') == 'ready', timeout_ms=10000)
    assert surface.isVisible() and surface.property('active')
    assert surface.property('metrics')['displayMode'] == 'waveform'
    choose_audio_mode(ui,'spectrum')
    assert ui.bridge.command('view.layout',{'topFraction':.6})
    image=ui.window.grabWindow(); assert not image.isNull()
    evidence=Path(os.environ.get('LYRIA_WORKSPACE_EVIDENCE',tmp_path))
    evidence.mkdir(parents=True,exist_ok=True)
    assert image.save(str(evidence/'merged-workspace-spectrum.png'))
    ui.window.resize(1400,940); delay(100)
    assert ui.window.grabWindow().save(str(evidence/'merged-workspace-default-1400x940.png'))
    timeline=find(ui,'frameTimeline')
    assert timeline.height() >= timeline.property('minimumExpandedHeight')
    scroll=find(ui,'makeScroll')
    assert scroll.property('contentHeight') is None
    assert timeline.mapToItem(scroll,QPointF()).y()+timeline.height() <= scroll.height()+1

    ui.bridge.command('view.layout', {'reset':True}); delay(50)
    assert audio.property('mode') == 'waveform'
    assert ui.bridge.state['workspaceLayout']['topFraction']==.58
    assert ui.bridge.document.snapshot()==before


def test_toggle_outline_ignores_focus_and_player_gap(ui):
    ui.window.resize(1400, 1080)
    install_cues(ui, resolution=(640,360),entries=[(0.,2.,'One')]);present(ui,1);delay(150)
    button=find(ui,'followCurrentButton')
    button.setProperty('checked',True);find(ui,'timelineSelectTool').forceActiveFocus();delay(30)
    assert button.property('stateOutline')
    button.setProperty('checked',False);button.forceActiveFocus();delay(30)
    assert not button.property('stateOutline')
    stage=find(ui,'videoStage'); controls=find(ui,'playerControls')
    assert controls.mapToScene(controls.boundingRect().topLeft()).y() - stage.mapToScene(stage.boundingRect().bottomLeft()).y() <= 7
    for width,height in [(900,700),(760,600),(1400,940)]:
        ui.window.resize(width,height);delay(50)
        audio=find(ui,'audioAnalysisPanel')
        assert 24 <= audio.height() <= 44  # responsive truthful status, never a fake plot
        assert find(ui,'audioStatusMessage').isVisible()
        assert not find(ui,'audioSplitHandle').isVisible()
        assert find(ui,'frameTimeline').height()>0
        assert find(ui,'playerControls').width()>300


def test_picture_is_centered_without_cropping_embedded_bars_across_aspects(ui):
    from PySide6.QtGui import QImage,QPainter,QColor
    from desktop_qt.media import Request
    for resolution in [(640,360),(960,360),(480,360)]:
        for width,height in [(1400,940),(900,700),(1400,1080)]:
            ui.window.resize(width,height)
            install_cues(ui,resolution=resolution,entries=[(0.,2.,'Short'),(2.,4.,'A longer subtitle '*8)])
            assert ui.bridge.command('settings.set',{'key':'subtitlePreview','value':False})
            # These black bands are INSIDE the source pixels, not external Qt
            # letterboxing. Preserve them while centering the uncropped frame.
            image=QImage(*resolution,QImage.Format.Format_RGB888);image.fill(QColor('#204060'))
            painter=QPainter(image);bar=round(resolution[1]*.12)
            painter.fillRect(0,0,resolution[0],bar,QColor('black'))
            painter.fillRect(0,resolution[1]-bar,resolution[0],bar,QColor('black'));painter.end()
            media=ui.bridge.media_controller
            request=Request(media.epoch,media.transport,media.sequence,1,time.monotonic())
            media._present(request,image,media.index.stamp(1),{})
            delay(250)
            video=find(ui,'videoPreview');stage=find(ui,'videoStage');controls=find(ui,'playerControls')
            rect=video.property('contentRect')
            assert rect.width()>0 and rect.height()>0
            assert abs(rect.width()/rect.height()-resolution[0]/resolution[1])<.001
            assert abs(rect.x()-(video.width()-rect.width())/2)<1
            assert abs(rect.y()-(video.height()-rect.height())/2)<1
            assert rect.x()>=-.5 and rect.y()>=-.5
            assert rect.right()<=video.width()+.5 and rect.bottom()<=video.height()+.5
            # Controls remain close to the outer stage, not to a bottom-aligned
            # picture that would contradict the approved centered placement.
            gap=controls.mapToScene(QPointF()).y()-stage.mapToScene(QPointF(0,stage.height())).y()
            assert -.5<=gap<=7
            rendered=ui.window.grabWindow();ratio=rendered.width()/ui.window.width()
            for fraction,expected in ((.05,(0,0,0)),(.5,(32,64,96)),(.95,(0,0,0))):
                location=video.mapToScene(QPointF(rect.x()+rect.width()*.5,rect.y()+rect.height()*fraction))
                assert 0<=location.x()<ui.window.width() and 0<=location.y()<ui.window.height()
                actual=rendered.pixelColor(round(location.x()*ratio),round(location.y()*ratio))
                assert max(abs(a-b) for a,b in zip((actual.red(),actual.green(),actual.blue()),expected))<12


def test_real_decoded_audio_pixels_and_seek(ui,tmp_path):
    import wave
    import numpy as np
    from tools.qt_event_loop_wait import wait_until
    # Deterministic chirp with real silent intervals, decoded by the product backend.
    rate=16000; duration=4.; t=np.arange(int(rate*duration))/rate
    signal=.6*np.sin(2*np.pi*(220*t+350*t*t))*(np.sin(2*np.pi*.7*t)**2)
    signal[(t>1.7)&(t<2.2)]=0
    path=tmp_path/'workspace-chirp.wav'
    with wave.open(str(path),'wb') as out:
        out.setnchannels(1);out.setsampwidth(2);out.setframerate(rate);out.writeframes((signal*32767).astype('<i2').tobytes())
    assert ui.bridge.command('media.open',{'path':str(path)})
    wait_until(lambda:ui.bridge.state['audioAnalysis']['status'] in ('ready','error'),timeout_ms=10000)
    assert ui.bridge.state['audioAnalysis']['status']=='ready'
    ui.bridge.command('stage.set',{'stage':'review'});ui.window.resize(1400,940);delay(100)
    assert ui.panel.property('nonVideo')
    assert not ui.panel.property('hasPicture')
    assert not find(ui,'subtitleLayoutPendingBadge').isVisible()
    from PySide6.QtCore import QObject
    assert ui.window.findChild(QObject,'relocateSourcesAction').property('enabled')
    audio=find(ui,'audioAnalysisPanel');assert audio.property('available')
    evidence=Path(os.environ.get('LYRIA_WORKSPACE_EVIDENCE',tmp_path));evidence.mkdir(parents=True,exist_ok=True)
    assert ui.window.grabWindow().save(str(evidence/'real-chirp-waveform.png'))
    choose_audio_mode(ui,'spectrum')
    assert ui.window.grabWindow().save(str(evidence/'real-chirp-spectrum.png'))
    before=ui.bridge.document.snapshot();selected=ui.bridge.selected
    area=find(ui,'timelineRulerMouse')
    point=area.mapToScene(QPointF(area.width()/2,12)).toPoint()
    QTest.mouseClick(ui.window,Qt.LeftButton,Qt.NoModifier,point);delay(40)
    timeline=find(ui,'frameTimeline')
    expected=(timeline.property('viewFirst')-1+timeline.property('span')/2)/100
    assert abs(ui.bridge.media['positionSeconds']-expected)<.02
    assert not area.property('scrubbing') and not ui.bridge.media['scrubbing']
    assert ui.bridge.document.snapshot()==before and ui.bridge.selected==selected
