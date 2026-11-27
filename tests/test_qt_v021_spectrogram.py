"""Offscreen v0.21 panel integration; does not establish native acceptance."""
import json
from PySide6.QtCore import QPointF
from test_qt_phase1_preview_workspace import ui
from test_qt_preview_visibility import application,find
from test_qt_preview_drag_gestures import chinese_fonts
from test_qt_dev4_spectrum_timeline import setup,evaluate
from tools.qt_event_loop_wait import wait_until,delay


def test_vocal_preset_controls_reach_worker_without_changing_project(ui,tmp_path):
    timeline,surface=setup(ui,tmp_path)
    panel=find(ui,'audioAnalysisPanel')
    before=ui.bridge.document.snapshot()
    first=timeline.property('viewFirst');span=timeline.property('span')
    evaluate(panel,'applyPreset("vocal")')
    wait_until(lambda:surface.property('status')=='ready' and surface.property('metrics').get('displayPreset')=='balanced',timeout_ms=10000)
    metrics=surface.property('metrics')
    assert metrics['brightnessMapping']=='soft_knee'
    assert metrics['windowSamples']==384 and metrics['tiltDbPerOctave']==3
    assert metrics['blackLevel']==0 and metrics['frequencyMaximum']==10000
    assert metrics['frequencyAxis']=='balanced' and metrics['fixedHopSamples'] in (64,128,256,512)
    assert panel.property('frequencyMaximum')==10000
    saved=json.loads((ui.bridge.storage_dir/'workspace-layout.json').read_text())
    assert saved['spectrumPreset']=='balanced' and saved['spectrumBlack']==0
    assert timeline.property('viewFirst')==first and timeline.property('span')==span
    assert ui.bridge.document.snapshot()==before
    # A display-only setting affects no source identity or timeline range.
    ui.bridge.command('view.layout',{'spectrumFrequency':5500,'spectrumAxis':'linear','spectrumBlack':.08})
    wait_until(lambda:surface.property('status')=='ready' and surface.property('metrics').get('frequencyMaximum')==5500,timeout_ms=10000)
    assert surface.property('metrics')['blackLevel']==.08
    assert ui.bridge.document.snapshot()==before
    evidence=tmp_path/'v021-spectrum-controls-offscreen.png'
    assert ui.window.grabWindow().save(str(evidence))


def test_extra_settings_control_fits_minimum_width(ui,tmp_path):
    timeline,surface=setup(ui,tmp_path)
    for width in (760,900,1400):
        ui.window.resize(width,800);delay(150)
        strip=find(ui,'timelineHeader')
        for name in ('timelineAudioModeSwitch','audioSourceSelector','spectrumSettings','audioZoomDivider'):
            item=find(ui,name);point=item.mapToItem(strip,QPointF())
            assert point.x()>=0 and point.x()+item.width()<=strip.width()+.01,(name,width,point.x(),item.width(),strip.width())

def test_clarity_menu_persists_768_then_resets_only_spectrum_display(application,tmp_path):
    from types import SimpleNamespace
    from PySide6.QtCore import QObject
    from test_qt_timeline_input import timeline_session
    from test_qt_dev5_spectrum import click
    from test_qt_audio_source_switch import add_stem, encoded_audio
    profile = tmp_path/'display-profile'
    def open_ui(bridge,window):
        bridge.command('stage.set',{'stage':'review'})
        window.resize(1000,800);window.show();delay(100)
        return SimpleNamespace(bridge=bridge,window=window)
    with timeline_session(application,profile) as (bridge,window):
        view=open_ui(bridge,window);panel=find(view,'audioAnalysisPanel')
        assert panel.property('windowSamples')==384
        assert bridge.command('view.layout',dict(audioMode='spectrum',audioHeight=112,previewFraction=.42))
        delay(60)
        menu=window.findChild(QObject,'audioModeMenu')
        evaluate(menu,'itemAt(5).triggered()')
        advanced=window.findChild(QObject,'advancedSpectrumPopup')
        wait_until(lambda:advanced.property('opened'))
        evaluate(find(view,'advanced_spectrumWindow_768'),'clicked()')
        wait_until(lambda:panel.property('windowSamples')==768,timeout_ms=3000)
        assert json.loads((profile/'workspace-layout.json').read_text())['spectrumWindow']==768
    with timeline_session(application,profile) as (bridge,window):
        view=open_ui(bridge,window);panel=find(view,'audioAnalysisPanel')
        menu=window.findChild(QObject,'audioModeMenu')
        advanced=window.findChild(QObject,'advancedSpectrumPopup')
        def advanced_control(name):
            evaluate(menu,'itemAt(5).triggered()')
            wait_until(lambda:advanced.property('opened'))
            control=find(view,name)
            assert control is not None
            return control
        def checked_windows():
            initial=advanced.property('opened')
            if not initial:advanced_control('advanced_spectrumWindow_384')
            checked=[find(view,f'advanced_spectrumWindow_{size}').property('emphasized') for size in (256,384,512,768,1024)]
            if not initial:evaluate(advanced,'close()');delay(20)
            return checked
        assert panel.property('windowSamples')==768 and checked_windows()==[False,False,False,True,False]
        click(view,find(view,'spectrumSettings'));delay(50)
        selector=find(view,'spectrumWindowSelector')
        assert '768' in selector.property('displayText')
        evaluate(selector,'activated(1)');delay(30)
        assert checked_windows()==[False,False,False,False,True]
        evaluate(advanced_control('advanced_spectrumWindow_768'),'clicked()');delay(30)
        evaluate(advanced,'close()');delay(20)
        assert '768' in selector.property('displayText')
        evaluate(window.findChild(QObject,'audioModeMenu'),'close()')
        path=encoded_audio(tmp_path/'mix.wav',2)
        assert bridge.command('media.open',{'path':str(path)})
        wait_until(lambda:bridge.audioAnalysis['status']=='ready',timeout_ms=8000)
        add_stem(bridge,tmp_path,path)
        bridge.selectAudioAnalysisSource('vocal')
        wait_until(lambda:bridge.audioAnalysis['source']=='vocal' and bridge.audioAnalysis['status']=='ready',timeout_ms=8000)
        for tilt in (0,3):
            assert bridge.command('view.layout',{'spectrumTilt':tilt})
            for i in (2,3,4):
                evaluate(menu,f'itemAt({i}).triggered()');delay(20)
                assert panel.property('tiltDb')==tilt
        evaluate(advanced_control('advancedPhysicalSpectrum'),'clicked()');delay(20)
        evaluate(advanced,'close()');delay(20)
        assert panel.property('displayPreset')=='physical' and panel.property('tiltDb')==0
        layout_before=json.loads((profile/'workspace-layout.json').read_text())
        source=bridge.audioAnalysis['sourcePath']
        evaluate(menu,'itemAt(6).triggered()');delay(40)
        layout_after=json.loads((profile/'workspace-layout.json').read_text())
        assert panel.property('windowSamples')==384 and checked_windows()==[False,True,False,False,False]
        assert panel.property('mode')=='spectrum' and source==bridge.audioAnalysis['sourcePath'] and bridge.audioAnalysis['source']=='vocal'
        assert {k:v for k,v in layout_before.items() if not k.startswith('spectrum')}=={k:v for k,v in layout_after.items() if not k.startswith('spectrum')}

def test_worker_publishes_progressive_tiles_before_full_view(ui,tmp_path,monkeypatch):
    import threading
    import desktop_qt.spectrogram_item as native
    timeline,surface=setup(ui,tmp_path)
    original=native.colourize
    entered=threading.Event();release=threading.Event();calls=[]
    def slowed(*args,**kwargs):
        calls.append(1)
        if len(calls)==2:
            entered.set()
            assert release.wait(5)
        return original(*args,**kwargs)
    monkeypatch.setattr(native,'colourize',slowed)
    try:
        ui.bridge.command('view.layout',{'spectrumGain':3})
        wait_until(lambda:entered.is_set() and bool(surface._partial_tiles),timeout_ms=5000)
        assert surface.property('status')=='updating'
        assert not surface._image.isNull()  # old completed view stays below patches
        assert surface.property('metrics')['renderComplete'] is False
        release.set()
        wait_until(lambda:surface.property('status')=='ready',timeout_ms=5000)
        assert not surface._partial_tiles and surface.property('metrics')['renderComplete']
    finally:
        release.set()