"""Actual Qt offscreen render, interaction and worker-lifecycle regressions."""
import json
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtQml import QQmlExpression, qmlContext
from PySide6.QtTest import QTest
from test_qt_phase1_preview_workspace import ui
from test_qt_preview_visibility import application, find
from test_qt_preview_drag_gestures import chinese_fonts
from test_dev4_spectrogram import encoded_audio, evidence_dir
from tools.qt_event_loop_wait import delay, wait_until


def evaluate(item, expression):
    value=QQmlExpression(qmlContext(item),item,expression)
    result=value.evaluate()
    assert not value.hasError(), value.error().toString()
    return result


def setup(ui,tmp_path):
    ui.window.resize(1400,1100)
    assert ui.bridge.command('media.open',{'path':str(encoded_audio(tmp_path/'stereo.wav',10))})
    wait_until(lambda:ui.bridge.audioAnalysis.get('status')=='ready',timeout_ms=10000)
    ui.bridge.command('stage.set',{'stage':'review'})
    ui.bridge.command('view.layout',{'audioMode':'spectrum'})
    timeline=find(ui,'frameTimeline')
    evaluate(timeline,'view(101,201)')
    surface=find(ui,'timelineSpectrogram')
    wait_until(lambda:surface.property('status')=='ready',timeout_ms=10000)
    delay(50)
    return timeline,surface


def test_independent_purple_spectrum_pixels_geometry_marker_and_scroll(ui,tmp_path):
    timeline,surface=setup(ui,tmp_path)
    assert ui.bridge.command('marker.add',{'frame':151})
    assert ui.bridge.command('timeline.createRange',{'first':110,'last':140})
    ui.bridge.command('row.edit',{'id':ui.bridge.selected,'text':'Lyrics below spectrum'})
    evaluate(timeline,'view(101,201)')
    wait_until(lambda:surface.property('status')=='ready',timeout_ms=5000)
    delay(100)
    audio=find(ui,'audioAnalysisPanel')
    assert audio.height()>=timeline.property('minimumAudioHeight')
    assert audio.mapToItem(timeline,QPointF()).y()+audio.height()<=timeline.height()
    assert audio.parentItem().parentItem().parentItem() is not None
    assert find(ui,'timelineAudioModeSwitch').isVisible()
    image=ui.window.grabWindow()
    assert not image.isNull()
    assert image.save(str(evidence_dir(tmp_path)/'dev4-timeline-spectrum-offscreen.png'))
    # The expected 1 kHz band is genuinely coloured; mid-silence is black at
    # an unoccluded row gap. Avoid guides/ruler/clip outlines in the samples.
    origin=surface.mapToScene(QPointF())
    ratio=image.width()/ui.window.width()
    spectrum_pixels=[]
    for y in np.linspace(.78,.86,12):
        for x in np.linspace(.05,.10,12):
            c=image.pixelColor(round((origin.x()+surface.width()*x)*ratio),round((origin.y()+surface.height()*y)*ratio))
            spectrum_pixels.append((c.red(),c.green(),c.blue()))
    assert any(r>g+30 and b>g+30 for r,g,b in spectrum_pixels)
    ruler=find(ui,'timelineRulerMouse')
    assert audio.mapToScene(QPointF(0,audio.height())).y() < ruler.mapToScene(QPointF()).y()
    assert abs(audio.mapToScene(QPointF()).x()-ruler.mapToScene(QPointF()).x())<.01
    assert audio.width()==ruler.width()
    head=find(ui,'timelineMarkerHead_151');line=find(ui,'timelineMarkerGuide_0')
    tip=head.mapToScene(QPointF(head.width()/2,head.property('tipY')))
    assert 0 <= tip.y()-line.mapToScene(QPointF()).y() <= 1.01
    assert abs(tip.x()-line.mapToScene(QPointF(line.width()/2,0)).x())<.01
    # Verify no background-colour gap at the geometric joint, not only QML y.
    tip=head.mapToScene(QPointF(head.width()/2,head.property('tipY')))
    for dy in (-1,0,1):
        colors=[image.pixelColor(round((tip.x()+dx)*ratio),round((tip.y()+dy)*ratio)) for dx in (-.5,0,.5)]
        assert any(c.red()>c.green()+20 and c.blue()>c.green()+40 for c in colors)
    scroll=find(ui,'timelineHorizontalScroll')
    bottom=scroll.mapToScene(QPointF(0,scroll.height())).y()
    assert scroll.height()>=16 and bottom<ui.window.height()-2
    viewport=find(ui,'timelineFoldContent')
    assert bottom<=viewport.mapToScene(QPointF(0,viewport.height())).y()


def test_follow_retains_zoom_and_resize_does_no_gui_fft(ui,tmp_path,monkeypatch):
    timeline,surface=setup(ui,tmp_path)
    assert ui.bridge.command('timeline.createRange',{'first':600,'last':650})
    evaluate(timeline,'view(101,201); manualView=false; followEnabled=false')
    span=timeline.property('span')
    follow=find(ui,'followCurrentButton')
    QTest.mouseClick(ui.window,Qt.LeftButton,Qt.NoModifier,follow.mapToScene(follow.boundingRect().center()).toPoint())
    delay(40)
    assert timeline.property('followEnabled')
    assert timeline.property('span')==span
    assert timeline.property('viewFirst')<=600<timeline.property('viewLast')
    import lyricsync.spectrogram as engine
    import threading
    original=engine.stft_columns
    threads=[]
    def checked(*args,**kwargs):
        threads.append(threading.current_thread().name)
        assert threading.current_thread() is not threading.main_thread()
        return original(*args,**kwargs)
    monkeypatch.setattr(engine,'stft_columns',checked)
    durations=[]
    for width,height in ((1100,760),(1600,1080),(900,700),(1400,940)):
        start=time.perf_counter()
        ui.window.resize(width,height)
        delay(25)
        durations.append(time.perf_counter()-start)
    wait_until(lambda:surface.property('status')=='ready',timeout_ms=10000)
    assert max(durations)<.5  # Zero FFT calls is valid with reusable resident pyramid.
    metrics=dict(surface.property('metrics'))
    metrics.update(resizeEventMaxSeconds=max(durations),scope='Windows Qt offscreen/software. Not native GPU, same-media Aegisub or user acceptance.')
    (evidence_dir(tmp_path)/'dev4-spectrum-ui-performance.json').write_text(json.dumps(metrics,indent=2),encoding='utf-8')


def test_height_resize_reuses_fft_and_source_switch_cancels(ui,tmp_path):
    timeline,surface=setup(ui,tmp_path)
    wait_until(lambda:surface._worker is not None,timeout_ms=3000)
    delay(100)  # allow the two bounded adjacent tiles to finish prefetching
    before=surface.property('metrics')['fftTiles']
    surface.setHeight(surface.height()+20)
    wait_until(lambda:surface.property('status')=='ready',timeout_ms=5000)
    assert surface.property('metrics')['fftTiles']<=before+2
    assert ui.bridge.command('media.open',{'path':str(encoded_audio(tmp_path/'seven-k.wav',2,7000))})
    wait_until(lambda:ui.bridge.audioAnalysis.get('status')=='ready',timeout_ms=10000)
    evaluate(timeline,'view(1,101)')
    wait_until(lambda:surface.property('status')=='ready',timeout_ms=10000)
    assert surface.property('sourcePath').endswith('seven-k.wav')
    assert surface.property('metrics')['sampleRate']==22050

def test_same_media_legacy_comparison_pixels(ui,tmp_path,monkeypatch):
    """Prior committed QML renders the identical encoded WAV and viewport.

    This is the dev3 audio-panel algorithm in a controlled harness, NOT a claim
    to have run the full old executable or a same-media Aegisub comparison.
    """
    import hashlib
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlComponent, qmlEngine
    from PySide6.QtQuick import QQuickWindow
    timeline,surface=setup(ui,tmp_path)
    evidence=evidence_dir(tmp_path)
    root=Path(__file__).resolve().parents[1]
    # Deliberately leave the checkout working directory: a source ZIP needs
    # neither a repository cwd nor Git for this reference comparison.
    monkeypatch.chdir(tmp_path)
    width=round(surface.width());height=round(surface.height())
    # Actual native pixels, no guides/clip occlusion, same physical rectangle.
    new=surface._image.copy(round(surface._offset),0,round(width*surface.property('pixelRatio')),round(height*surface.property('pixelRatio')))
    assert new.save(str(evidence/'dev4-comparison-new-native.png'))
    # Exact immutable source: dev3 commit 07241ae148ee9b34c79368835e16b5d9a3dd99f6.
    # SHA-256: ad17da95ef970dc877702ed9073f9079a8ace594d631ce4ba9d35bd87e6c9776
    legacy=(root/'tests/fixtures/spectrum/AudioAnalysisPanel.dev3.qml').read_bytes()
    # Git may check out CRLF on Windows; verify unchanged canonical LF fixture content.
    assert hashlib.sha256(legacy.replace(b"\r\n", b"\n")).hexdigest()=='ad17da95ef970dc877702ed9073f9079a8ace594d631ce4ba9d35bd87e6c9776'
    component=QQmlComponent(qmlEngine(timeline))
    component.setData(legacy,QUrl.fromLocalFile(str(root/'desktop_qt/qml/LegacySpectrumComparison.qml')))
    assert component.status()==QQmlComponent.Ready, [e.toString() for e in component.errors()]
    window=QQuickWindow()
    window.resize(width+114,height)
    panel=component.createWithInitialProperties(dict(controller=ui.bridge,state=ui.bridge.uiState,media=ui.bridge.media,timelineView=timeline))
    assert panel is not None,[e.toString() for e in component.errors()]
    panel.setParentItem(window.contentItem())
    panel.setWidth(width+114);panel.setHeight(height)
    window.show();delay(150)
    screen=window.grabWindow()
    ratio=screen.width()/window.width()
    old=screen.copy(round(89*ratio),0,round(width*ratio),round(height*ratio))
    assert old.save(str(evidence/'dev4-comparison-legacy-qml.png'))
    assert new.width()==old.width() and new.height()==old.height()
    # The legacy cell corresponding to the silence onset is ~64ms wide;
    # native timing surface columns are <1ms, with a 23.22ms effective window.
    report=dict(scope='Same 10-second synthetic stereo WAV; prior committed dev3 AudioAnalysisPanel.qml rendered offscreen beside dev4 native surface. Not full dev3 executable, Aegisub or user acceptance.',
                legacyReferenceCommit='07241ae148ee9b34c79368835e16b5d9a3dd99f6',legacyReferenceSha256='ad17da95ef970dc877702ed9073f9079a8ace594d631ce4ba9d35bd87e6c9776',
                mediaSha256=hashlib.sha256((tmp_path/'stereo.wav').read_bytes()).hexdigest(),
                viewportFrames=[101,201],viewportSeconds=[1,2],logicalWidth=width,logicalHeight=height,dpr=ratio,
                legacyColumns=ui.bridge.audioAnalysis['spectrumColumns'],legacyCellSeconds=ui.bridge.audioAnalysis['timeResolutionSeconds'],
                newPhysicalColumns=surface.property('metrics')['physicalColumns'],newHopSeconds=surface.property('metrics')['secondsPerColumn'],
                newEffectiveWindowSeconds=512/22050,fftSamples=2048)
    (evidence/'dev4-spectrum-comparison.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    window.close();panel.deleteLater();window.deleteLater();delay(20)
