"""Retained audio/timeline regressions, migrated to the approved dev5 independent view.

The historical filename is retained for existing test commands. The old 8 kHz
analysis invariant remains as a legacy-overview check; actual UI tests target
the shared timing, paired playheads, left mode selector and independent resize.
"""
import math, os, time, wave
from pathlib import Path
import numpy as np
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtQml import QQmlExpression, qmlContext
from test_qt_phase1_preview_workspace import ui
from test_qt_preview_visibility import application, find
from test_qt_preview_drag_gestures import chinese_fonts
from tools.qt_event_loop_wait import delay, wait_until
from lyricsync.audio_analysis import analyze_audio

def wav(path, seconds=4):
    rate=16000;t=np.arange(rate*seconds)/rate
    samples=.7*np.sin(2*np.pi*1000*t);samples[(t>1.5)&(t<2)]=0
    with wave.open(str(path),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(rate);f.writeframes((samples*32767).astype('<i2').tobytes())
    return path

def evaluate(item, expr):
    e=QQmlExpression(qmlContext(item),item,expr);r=e.evaluate();assert not e.hasError(),e.error().toString();return r

def setup(ui,tmp_path):
    ui.window.resize(1400,1080)
    assert ui.bridge.command('media.open',{'path':str(wav(tmp_path/'tone.wav'))})
    wait_until(lambda:ui.bridge.state['audioAnalysis']['status']=='ready',timeout_ms=10000)
    assert ui.bridge.command('stage.set',{'stage':'review'})
    delay(200)


def evidence_dir(tmp_path):
    path=Path(os.environ.get('LYRIA_MERGED_AUDIO_EVIDENCE',os.environ.get('LYRIA_DEV3_EVIDENCE',tmp_path)))
    path.mkdir(parents=True,exist_ok=True)
    return path


def choose_audio_mode(ui,mode):
    """Use the actual always-visible left selector, not a hidden command."""
    timeline=find(ui,'frameTimeline')
    wait_until(lambda:timeline.isVisible() and not timeline.property('folding'),timeout_ms=2000)
    delay(20)
    selector=find(ui,'timelineAudioModeSwitch')
    assert selector and selector.isVisible() and selector.isEnabled()
    QTest.mouseClick(ui.window,Qt.LeftButton,Qt.NoModifier,selector.mapToScene(selector.boundingRect().center()).toPoint())
    QTest.keyClick(ui.window,Qt.Key_Home if mode=='waveform' else Qt.Key_End)
    QTest.keyClick(ui.window,Qt.Key_Return)
    wait_until(lambda:find(ui,'audioAnalysisPanel').property('mode')==mode,timeout_ms=2000)
    assert selector.property('currentIndex')==(0 if mode=='waveform' else 1)
    if mode=='spectrum':
        surface=find(ui,'timelineSpectrogram')
        try:
            wait_until(lambda:surface.property('status')=='ready',timeout_ms=10000)
        except TimeoutError:
            worker=surface._worker
            raise AssertionError(dict(status=surface.property('status'),error=surface.property('error'),active=surface.property('active'),size=(surface.width(),surface.height()),source=surface.property('sourcePath'),sequence=surface._sequence,workerClosed=worker.closed if worker else None,workerAlive=worker.thread.is_alive() if worker else None,latestSequence=worker.latest.get('sequence') if worker and worker.latest else None,latestInactive=worker.latest.get('inactive') if worker and worker.latest else None))
    delay(25)


def assert_shared_audio_geometry(ui):
    audio=find(ui,'audioAnalysisPanel');ruler=find(ui,'timelineRulerMouse')
    surface=find(ui,'timelineSpectrogram');timeline=find(ui,'frameTimeline')
    assert audio and ruler and surface and timeline
    assert abs(audio.mapToScene(QPointF()).x()-ruler.mapToScene(QPointF()).x())<.01
    assert abs(audio.width()-ruler.width())<.01
    assert abs(surface.width()-ruler.width())<.01
    origin=audio.mapToItem(timeline,QPointF())
    assert origin.y()>=0 and origin.y()+audio.height()<=timeline.height()
    assert audio.height()>=timeline.property('minimumAudioHeight')
    tracks=find(ui,'timelineTracks')
    assert tracks.height()>=30 and tracks.mapToScene(QPointF(0,tracks.height())).y()<=ui.window.height()
    assert abs(audio.height()-timeline.property('audioHeight'))<.01
    assert find(ui,'audioSplitHandle').isVisible()
    assert audio.mapToScene(QPointF(0,audio.height())).y() < ruler.mapToScene(QPointF()).y()
    return audio,ruler,surface


def test_real_fft_resolution_silence_and_bound(tmp_path):
    result=analyze_audio(wav(tmp_path/'tone.wav'),4)
    assert len(result['waveform'])==16384
    assert len(result['spectrum'])==63
    spectrum=np.asarray(result['spectrum']);assert spectrum.shape==(63,128)
    assert 30<=spectrum.mean(axis=0).argmax()<=32
    assert not spectrum[26:30].any()
    assert result['timeResolutionSeconds']<.065

def test_audio_repeated_scrub_shared_viewport(ui,tmp_path):
    setup(ui,tmp_path)
    timeline=find(ui,'frameTimeline')
    evaluate(timeline,'view(41, 241)');delay(60)
    audio,area,surface=assert_shared_audio_geometry(ui)
    assert audio.property('firstFrame')==41 and audio.property('frameSpan')==200
    before=ui.bridge.document.snapshot()
    for mode in ('waveform','spectrum'):
        choose_audio_mode(ui,mode)
        assert (timeline.property('viewFirst'),timeline.property('span'))==(41,200)
        for a,b in [(0.2,0.8),(0.8,0.3),(0.3,0.65)]:
            # Existing lyric ruler seeking stays synchronized with the separate
            # audio surface; its own gestures have independent dev5 coverage.
            start=area.mapToScene(QPointF(area.width()*a,12)).toPoint()
            end=area.mapToScene(QPointF(area.width()*b,12)).toPoint()
            QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,start)
            for n in range(1,7):QTest.mouseMove(ui.window,start+(end-start)*(n/6),15)
            assert area.property('scrubbing')
            assert abs(ui.bridge.media['playheadFrame']-(41+round(200*b)))<=1
            QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,end);delay(40)
            assert not area.property('scrubbing') and not ui.bridge.media['scrubbing']
        assert ui.bridge.document.snapshot()==before
        # One playhead must align with both waveform and STFT physical columns.
        head=find(ui,'timelinePlayhead')
        expected=audio.mapToScene(QPointF((timeline.property('playheadFrame')-41)/200*audio.width(),0)).x()
        assert abs(head.mapToScene(QPointF(head.width()/2,0)).x()-expected)<1
        assert surface.property('firstFrame')==audio.property('firstFrame')
        assert surface.property('frameSpan')==audio.property('frameSpan')
        assert ui.window.grabWindow().save(str(evidence_dir(tmp_path)/f'dev4-merged-audio-{mode}.png'))
    evaluate(timeline,'zoom(0.5)');delay(30)
    assert audio.property('frameSpan')==timeline.property('span')==100
    evaluate(timeline,'panTo(120,true)');delay(30)
    assert audio.property('firstFrame')==timeline.property('viewFirst')==120
    wait_until(lambda:surface.property('status')=='ready',timeout_ms=5000)
    assert surface.property('metrics')['physicalColumns']==math.ceil(surface.width()*surface.property('pixelRatio'))
    assert ui.bridge.document.snapshot()==before


def test_follow_toggle_and_play_center(ui,tmp_path):
    setup(ui,tmp_path)
    t=find(ui,'frameTimeline');button=find(ui,'followCurrentButton')
    for _ in range(3):
        previous=t.property('followEnabled');span=t.property('span')
        point=button.mapToScene(button.boundingRect().center()).toPoint()
        QTest.mouseClick(ui.window,Qt.LeftButton,Qt.NoModifier,point);delay(40)
        assert t.property('followEnabled') != previous
        assert button.property('checked')==t.property('followEnabled')
        assert t.property('span')==span
    for width,height in [(1400,940),(1100,760),(900,700)]:
        ui.window.resize(width,height)
        # Resize starts the header/height motion on the next Qt polish, not
        # necessarily before resize() returns. Wait for painted geometry to
        # remain stable instead of accepting an initially-false animation flag.
        last=[None,0.]
        def settled():
            audio=find(ui,'audioAnalysisPanel')
            geometry=(t.width(),t.height(),audio.height(),audio.mapToItem(t,QPointF()).y())
            if t.property('folding') or geometry!=last[0]:
                last[:]=[geometry,time.monotonic()]
                return False
            return time.monotonic()-last[1]>=.12
        wait_until(settled,timeout_ms=2500)
        play=find(ui,'transportPlayPause')
        assert play.isVisible() and find(ui,'timelinePlayPause') is None
        assert_shared_audio_geometry(ui)
        scroll=find(ui,'timelineHorizontalScroll')
        assert scroll.height()>=16
        # v0.23 has no outer scrolling. The real horizontal bar must already
        # be wholly inside the timeline and fixed workspace clips.
        workspace=find(ui,'makeScroll')
        assert workspace.property('contentHeight') is None
        delay(30)
        viewport=find(ui,'timelineFoldContent')
        assert scroll.mapToItem(viewport,QPointF()).y()>=0
        assert scroll.mapToItem(viewport,QPointF(0,scroll.height())).y()<=viewport.height()
        assert scroll.mapToItem(workspace,QPointF()).y()>=0
        assert scroll.mapToItem(workspace,QPointF(0,scroll.height())).y()<=workspace.height()
        assert scroll.mapToScene(QPointF(0,scroll.height())).y()<ui.window.height()
        assert ui.window.grabWindow().save(str(evidence_dir(tmp_path)/f'dev4-merged-layout-{width}x{height}.png'))


def test_normal_window_workspace_handle_resizes_lyrics_not_independent_audio(ui,tmp_path):
    # v0.22 restores the workspace divider with continuous root-coordinate motion.
    setup(ui,tmp_path);ui.window.resize(1600,1200);delay(100)
    restored_handle = find(ui,'timelineSplitHandle')
    assert restored_handle is not None and restored_handle.isVisible() and restored_handle.height() >= 6
    panel=find(ui,'frameTimeline');top=find(ui,'mainSplit')
    before=ui.bridge.document.snapshot();span=panel.property('span')
    for mode in ('waveform','spectrum'):
        choose_audio_mode(ui,mode)
        for delta in [-24,48,-24]:
            old=(panel.height(),find(ui,'audioAnalysisPanel').height(),top.height())
            start=restored_handle.mapToScene(QPointF(restored_handle.width()/2,restored_handle.height()/2)).toPoint();end=start+QPoint(0,delta)
            QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,start)
            for n in range(1,7):QTest.mouseMove(ui.window,start+(end-start)*(n/6),15)
            QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,end);delay(80)
            assert_shared_audio_geometry(ui)
            assert panel.height()>=panel.property('minimumExpandedHeight')
            assert panel.property('span')==span
            assert abs(panel.height()-old[0]+delta)<2
            assert find(ui,'audioAnalysisPanel').height()==old[1]
            assert abs(top.height()-old[2]-delta)<2
    assert ui.bridge.document.snapshot()==before


def test_long_real_audio_zoom_playback_perf(ui,tmp_path):
    # Actual encoded synthetic WAV: legacy overview plus viewport-driven STFT.
    path=wav(tmp_path/'bounded.wav',140)
    ui.window.resize(1400,1080)
    start=time.perf_counter();assert ui.bridge.command('media.open',{'path':str(path)})
    wait_until(lambda:ui.bridge.state['audioAnalysis']['status']=='ready',timeout_ms=30000)
    analysis_seconds=time.perf_counter()-start
    ui.bridge.command('stage.set',{'stage':'review'});choose_audio_mode(ui,'spectrum')
    timeline=find(ui,'frameTimeline');surface=find(ui,'timelineSpectrogram')
    result=ui.bridge.state['audioAnalysis'];assert len(result['spectrum'])==2048 # retained overview, NOT the display image
    assert result['sourcePath']==str(path)
    durations=[]
    for n in range(20):
        start=time.perf_counter();evaluate(timeline,f'view({1+n*100}, {301+n*100})');delay(5);durations.append(time.perf_counter()-start)
    wait_until(lambda:surface.property('status')=='ready',timeout_ms=10000)
    metrics=dict(surface.property('metrics'))
    assert surface.property('firstFrame')==1901 and surface.property('frameSpan')==300
    assert metrics['sampleRate']==22050 and metrics['fftSamples']==1024
    assert metrics['windowSamples']==384
    assert metrics['physicalColumns']==math.ceil(surface.width()*surface.property('pixelRatio'))
    assert metrics['secondsPerColumn']<=.005 and metrics['cacheBytes']<=32*1024*1024
    ui.bridge.command('media.mute',{'value':True});ui.bridge.command('media.play',{})
    wait_until(lambda:ui.bridge.media['positionSeconds']>.2,timeout_ms=10000)
    ui.bridge.command('media.pause',{})
    start=time.perf_counter();assert ui.bridge.command('timeline.createAtPlayhead',{})
    assert ui.bridge.command('row.edit',{'id':ui.bridge.selected,'text':'Performance probe'});delay(30)
    edit_seconds=time.perf_counter()-start
    assert ui.bridge.state['selection']['current']['chinese']=='Performance probe'
    (evidence_dir(tmp_path)/'merged-audio-performance.txt').write_text(f'140 sec actual generated WAV; overview analysis {analysis_seconds:.3f}s; 20 zoom/pan event samples max {max(durations):.4f}s mean {np.mean(durations):.4f}s; create+edit+30ms settle {edit_seconds:.4f}s; new viewport {metrics["physicalColumns"]} independent columns; maximum hop {metrics["secondsPerColumn"]:.6f}s; FFT cache {metrics["cacheBytes"]} bytes. Legacy 2048x128 overview is retained but not displayed. Offscreen/software Qt; not an audible playback or GPU acceptance test.',encoding='utf-8')
    assert max(durations)<1.0


def test_narrow_blank_clip_text_never_paints_outside(ui,tmp_path):
    setup(ui,tmp_path)
    assert ui.bridge.command('timeline.createRange',{'first':101,'last':101})
    identity=ui.bridge.selected;t=find(ui,'frameTimeline')
    evaluate(t,'view(1,401)');delay(80)
    text=find(ui,'timelineText_'+identity);clip=find(ui,'timelineClip_'+identity)
    assert text is not None and clip is not None
    assert text.property('clip') and text.width()==0 and not text.isVisible()
    evaluate(t,'view(95,115)');delay(80)
    text=find(ui,'timelineText_'+identity);assert text.isVisible() and text.width()>0
    assert text.x()+text.width()<=find(ui,'timelineClip_'+identity).width()


def test_fast_split_release_persists_target_not_previous_layout(ui,tmp_path):
    import json
    # Keep quick-release persistence coverage on the retained audio-height grip.
    setup(ui,tmp_path);ui.window.resize(1600,1100);delay(100)
    before=ui.bridge.document.snapshot()
    restored_handle = find(ui,'timelineSplitHandle')
    assert restored_handle is not None and restored_handle.isVisible() and restored_handle.height() >= 6
    for mode,delta in [('waveform',45),('spectrum',-35)]:
        choose_audio_mode(ui,mode)
        handle=find(ui,'audioSplitHandle');timeline=find(ui,'frameTimeline');audio=find(ui,'audioAnalysisPanel')
        old_height=audio.height();span=timeline.property('span')
        start=handle.mapToScene(handle.boundingRect().center()).toPoint();end=start+QPoint(0,delta)
        QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,start)
        QTest.mouseMove(ui.window,end,10)
        target=timeline.property('audioHeight')
        QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,end);delay(60)
        saved=json.loads((ui.bridge.storage_dir/'workspace-layout.json').read_text())
        assert abs(audio.height()-old_height)>10
        assert abs(audio.height()-target)<1
        assert abs(saved['audioHeight']-audio.height())<1
        assert saved['audioMode']==mode
        assert timeline.property('span')==span
        assert_shared_audio_geometry(ui)
    assert ui.bridge.document.snapshot()==before
