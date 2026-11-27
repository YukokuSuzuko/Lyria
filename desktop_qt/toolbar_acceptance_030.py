"""Private Windows regression against the complete packaged QML scene."""
import json,hashlib,wave,time
from copy import deepcopy
import numpy as np
from PySide6.QtCore import QObject,QPointF,Qt
from PySide6.QtGui import QFontMetricsF
from PySide6.QtTest import QTest
from lyricsync.model import Project,Mapping
from lyricsync.local_models import UVR
from lyricsync.candidate_tracks import capture_source,candidate_from_project

from tools.qt_event_loop_wait import delay,wait_until

def walk(item):
    yield item
    for child in item.childItems():
        yield from walk(child)

def find(window, name):
    return next((item for item in walk(window.contentItem()) if item.objectName() == name), None)

def point(item, x=None, y=None):
    return item.mapToScene(QPointF(item.width() / 2 if x is None else x,
                                  item.height() / 2 if y is None else y)).toPoint()

def click(window, item, x=None, y=None):
    assert item and item.isVisible() and item.isEnabled()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(item, x, y))
    QTest.qWait(40)
def add_stem(bridge, root, path, offset=0., duration=2., mapping_updates=None):
    stem=encoded_audio(root/'vocals.wav', duration)
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    manifest=root/'mapping.json'
    manifest.write_text(json.dumps(dict(path=str(stem.resolve()),source_path=str(path.resolve()),
        sha256=sha(stem),source_sha256=sha(path),model_id=UVR.id,model_revision=UVR.revision,
        offset_seconds=offset,duration_seconds=duration,latency_samples=0,padding_removed=True,
        time_mapping='project_seconds = offset_seconds + stem_sample / sample_rate')),encoding='utf8')
    if mapping_updates:
        data=json.loads(manifest.read_text(encoding='utf8'));data.update(mapping_updates)
        manifest.write_text(json.dumps(data),encoding='utf8')
    bridge.document.project.mappings=[Mapping('a',[],text_override='synthetic',start=.1,end=.3)]
    bridge._changed()
    snapshot=capture_source(deepcopy(bridge.document),'vocal',bridge.media_controller.index,
        auxiliary_paths={'alignment_audio':str(path),'separated_vocals':str(stem),'separation_manifest':str(manifest)})
    record=candidate_from_project(snapshot,Project.from_dict(bridge.document.project.to_dict()))
    bridge._candidate_install(record)
    return record,stem
def encoded_audio(path, seconds=4, frequency=1000, amplitude=.7, rate=48000, silence=True):
    t=np.arange(round(rate*seconds))/rate
    signal=amplitude*np.sin(2*np.pi*frequency*t)
    if silence:
        signal[(t>1.5)&(t<2)]=0
    with wave.open(str(path),'wb') as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(np.repeat((signal*32767).astype('<i2')[:,None],2,axis=1).tobytes())
    return path

def check(bridge,w,out):
    result={'layouts':[],'actions':[]}
    def save():(out/'toolbar.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
    playback_calls=[]
    media=bridge.media_controller
    media.mute(True)
    media.player.setAudioOutput(None)
    media.play=lambda:playback_calls.append('play')
    assert media.player.audioOutput() is None
    w.show();w.requestActivate()
    path=encoded_audio(out/'source.wav',4)
    assert bridge.command('media.open',{'path':str(path)})
    wait_until(lambda:bridge.audioAnalysis.get('status')=='ready',timeout_ms=15000)
    wait_until(lambda:media.snapshot().get('ready'),timeout_ms=12000)
    add_stem(bridge,out,path)
    assert bridge._audio_source_state()['vocalAvailable'],bridge._audio_source_state()
    bridge.command('stage.set',{'stage':'review'})
    bridge.command('view.layout',dict(audioVisible=True,timelineVisible=True,audioMode='spectrum'))
    delay(300)
    it=lambda n:find(w,n)
    header=it('timelineHeader');group=it('timelineRightTools');left=it('timelineToolGroup');timeline=it('frameTimeline');panel=it('audioAnalysisPanel')
    def rect(item):
        p=item.mapToItem(header,QPointF())
        return [round(p.x(),2),round(p.y(),2),round(item.width(),2),round(item.height(),2)]
    names=['timelineAudioModeSwitch','audioSourceSelector','spectrumSettings','audioZoomDivider','timelineZoomOut','timelineZoomIn','audioVisibilityToggle','followCurrentButton','timelineFoldButton']
    def layout(width):
        w.resize(width,800);delay(180)
        b=[rect(it(n)) for n in names];L=rect(left);h=header.height();wrapped=bool(header.property('stacked'))
        result['layouts'].append(dict(window=w.width(),header_width=header.width(),header_height=h,wrapped=wrapped,right=b,left=L,dpr=w.devicePixelRatio()))
        save()
        assert abs(b[-1][0]+b[-1][2]-header.width())<2
        assert all(b[i][0]+b[i][2]<=b[i+1][0]+.1 for i in range(len(b)-1))
        assert all(v[0]>=0 and v[1]>=0 and v[1]+v[3]<=h+.01 for v in b)
        assert all(v[3]==27 for i,v in enumerate(b) if i!=3)
        assert b[0][1]>=L[1]+L[3] if wrapped else b[0][0]>=L[0]+L[2]+10
        return (wrapped,b)
    def shot(name):
        image=w.grabWindow()
        assert not image.isNull() and image.save(str(out/(name+'.png')))
    def choose(box,index):
        box.forceActiveFocus(Qt.TabFocusReason)
        QTest.keyClick(w,Qt.Key_Space);delay(80)
        QTest.keyClick(w,Qt.Key_Home if index==0 else Qt.Key_End);QTest.keyClick(w,Qt.Key_Return);delay(120)
    for width in (1400,900,760):
        wrap,baseline=layout(width)
        for indicator,index in [('timelineAudioModeSwitch',0),('timelineAudioModeSwitch',1),('audioSourceSelector',1),('audioSourceSelector',0)]:
            box=it(indicator);choose(box,index)
            assert box.property('currentIndex')==index,(indicator,index,box.property('currentIndex'))
            if indicator=='audioSourceSelector':
                try:
                    wait_until(lambda:bridge.audioAnalysis.get('source')==('vocal' if index else 'original') and bridge.audioAnalysis.get('status')=='ready',timeout_ms=20000)
                except TimeoutError:
                    raise AssertionError((width,index,{k:bridge.audioAnalysis.get(k) for k in ('source','status','error')},bridge._audio_source_state(),bridge._error_text))
            width_needed=QFontMetricsF(box.property('font')).horizontalAdvance(box.property('displayText'))
            assert width_needed<=box.width()-box.property('leftPadding')-box.property('rightPadding')+.1
            assert bool(header.property('stacked'))==wrap and [rect(it(n)) for n in names]==baseline
        shot('window-'+str(width))
    if not any(v['wrapped'] for v in result['layouts']):
        # Exercise the component below the application's normal minimum as an additional boundary.
        w.setMinimumWidth(480);layout(650);shot('component-narrow')
    assert any(v['wrapped'] for v in result['layouts'])
    assert not result['layouts'][0]['wrapped']
    layout(1100)
    before=bridge.document.snapshot()
    gear=it('spectrumSettings');menu=w.findChild(QObject,'audioModeMenu')
    advanced=w.findChild(QObject,'advancedSpectrumPopup')
    def open_menu():
        click(w,gear);wait_until(lambda:menu.property('opened'))
    for preset in ('detail','balanced','contrast','balanced'):
        open_menu()
        click(w,it('spectrumPreset_'+preset))
        wait_until(lambda:panel.property('displayPreset')==preset and not menu.property('visible'))
        open_menu()
        checked=[p for p in ('detail','balanced','contrast') if it('spectrumPreset_'+p).property('checked')]
        assert checked==[preset],checked
        if preset=='balanced':shot('preset-checked')
        QTest.keyClick(w,Qt.Key_Escape);delay(100)
    result['actions'].append('all three presets, exclusive checks, Escape')
    open_menu()
    slider=it('spectrumGainSlider');slider.forceActiveFocus(Qt.TabFocusReason)
    QTest.keyClick(w,Qt.Key_Right);delay(80)
    assert panel.property('gainDb')>0,panel.property('gainDb')
    choose(it('spectrumWindowSelector'),1)
    assert panel.property('windowSamples')==1024
    if not menu.property('opened'):open_menu()
    click(w,it('advancedSpectrumMenuItem'))
    wait_until(lambda:advanced.property('opened'))
    click(w,it('advanced_spectrumWindow_768'))
    wait_until(lambda:panel.property('windowSamples')==768)
    click(w,it('advancedPhysicalSpectrum'));wait_until(lambda:panel.property('displayPreset')=='physical')
    QTest.keyClick(w,Qt.Key_Escape);delay(100)
    open_menu();click(w,it('spectrumReset'))
    wait_until(lambda:panel.property('displayPreset')=='detail' and panel.property('gainDb')==0 and panel.property('windowSamples')==384)
    assert bridge.document.snapshot()==before
    result['actions'].append('brightness, window, advanced, physical spectrum, restore; document unchanged')
    wait_until(lambda:not menu.property('visible'));delay(100)
    it('audioSourceSelector').forceActiveFocus(Qt.TabFocusReason)
    QTest.keyClick(w,Qt.Key_Tab);delay(80)
    result['keyboard_focus']={'windowActive':w.isActive(),'control':w.activeFocusItem().objectName() if w.activeFocusItem() else '', 'gearFocus':gear.hasActiveFocus(),'visualFocus':gear.property('visualFocus')}
    save()
    assert gear.hasActiveFocus() and gear.property('visualFocus'),result['keyboard_focus']
    QTest.keyClick(w,Qt.Key_Space);wait_until(lambda:menu.property('opened'));QTest.keyClick(w,Qt.Key_Escape);delay(100)
    surface=it('audioNavigationMouse')
    QTest.mouseClick(w,Qt.RightButton,Qt.NoModifier,surface.mapToScene(QPointF(3,3)).toPoint());wait_until(lambda:menu.property('opened') and menu.property('fromPlot'))
    QTest.keyClick(w,Qt.Key_Escape);delay(100)
    click(w,it('timelineHandTool'));assert timeline.property('tool')=='hand'
    click(w,it('timelineSelectTool'));assert timeline.property('tool')=='select'
    click(w,it('manualTimingMode'));assert bridge.uiState['manualTiming']['enabled']
    click(w,it('manualTimingMode'));assert not bridge.uiState['manualTiming']['enabled']
    span=timeline.property('span');click(w,it('timelineZoomIn'));assert timeline.property('span')<span
    click(w,it('timelineZoomOut'))
    click(w,it('timelineFoldButton'));wait_until(lambda:timeline.property('collapsed'))
    click(w,it('timelineFoldButton'));wait_until(lambda:not timeline.property('collapsed'));delay(240)
    bridge.command('view.layout',{'timelineVisible':False});delay(150)
    assert not header.isVisible() and it('audioSourceSelector').isVisible() and it('audioFollowCurrentButton').isVisible()
    bridge.command('view.layout',{'timelineVisible':True,'audioVisible':False});delay(120)
    assert header.isVisible() and not gear.isVisible()
    result['actions'].append('keyboard focus, shared context menu, tools, manual timing, zoom, fold, audio-only, timeline-only')
    result['test_audio']={'output_detached':media.player.audioOutput() is None,'playback_calls':playback_calls}
    assert not playback_calls,playback_calls
    result['ok']=True

    save()
