import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
import pytest
from pathlib import Path
from copy import deepcopy
from PySide6.QtCore import QUrl, QObject, Qt
from PySide6.QtQml import QQmlApplicationEngine
from test_qt_startup_phase5 import application, find, click
from test_qt_timeline_commands import bridge
from desktop_qt.controller import Bridge
from desktop_qt.workspace_layout import DEFAULTS, update_layout
from lyricsync import styles
from tools.qt_event_loop_wait import delay


def test_panel_guard_and_independence():
    layout = update_layout(DEFAULTS, {'timelineVisible':False})
    assert layout['audioVisible']
    layout = update_layout(layout, {'previewVisible':False,'reviewVisible':False})
    assert not update_layout(layout, {'audioVisible':False})['previewVisible']
    assert all(update_layout(layout, {'reset':True})[k] for k in ('audioVisible','timelineVisible','previewVisible','reviewVisible'))
    loaded = update_layout(DEFAULTS, {k:False for k in ('audioVisible','timelineVisible','previewVisible','reviewVisible')},loading=True)
    assert not loaded['previewVisible'] and not loaded['reviewVisible']


def test_style_snapshot_preserves_other_rows_and_speaker(bridge):
    p=bridge.document.project
    p.mappings[0].speaker_style={'font':'Arial','size':36,'color':'#FFFFFF','bold':False,'italic':False}
    before=deepcopy(p.mappings[0].speaker_style)
    profiles=styles.project_profiles(p)
    profiles['pink']={'name':'粉色','style':dict(font='Arial',size=42,color='#FF00AA',bold=True,italic=False)}
    styles.save_project_profiles(p,bridge.history,profiles,'pink')
    styles.apply_style(p,bridge.history,['target'])
    assert p.mappings[0].speaker_style==before
    assert next(m for m in p.mappings if m.id=='target').speaker_style['color']=='#FF00AA'
    assert not next(m for m in p.mappings if m.id=='target').speaker_id
    profiles.pop('pink')
    styles.save_project_profiles(p,bridge.history,profiles,'default')
    assert next(m for m in p.mappings if m.id=='target').speaker_style['color']=='#FF00AA'


def test_library_roundtrip_and_ass_subset(tmp_path):
    profiles={'a':{'name':'测试','style':dict(font='Arial',size=32,color='#AA22FF',bold=True,italic=False)}}
    path=tmp_path/'library.json';styles.save_library(path,profiles)
    assert styles.load_library(path)==profiles
    ass=tmp_path/'styles.ass';ass.write_text('[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, Bold, Italic\nStyle: Main,Arial,32,&H00FF22AA,-1,0\n',encoding='utf-8')
    value=next(iter(styles.import_styles(ass).values()))
    assert value['style']==profiles['a']['style']
    with pytest.raises(ValueError):styles.save_library(ass,profiles,[str(ass)])


def test_seven_menus_and_independent_audio(application,tmp_path):
    b=Bridge(storage_dir=tmp_path/'state',startup=True)
    e=QQmlApplicationEngine();warnings=[]
    e.warnings.connect(lambda ms:warnings.extend(m.toString() for m in ms))
    e.rootContext().setContextProperty('bridge',b)
    e.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1]/'desktop_qt/qml/Main.qml')))
    assert e.rootObjects(),warnings
    w=e.rootObjects()[0]
    try:
        delay(350)
        buttons=[find(w,key+'MenuButton') for key in ('file','edit','subtitle','view','tools','settings','help')]
        assert [v.property('text') for v in buttons]==['文件','编辑','字幕','视图','工具','设置','帮助']
        assert all(v.isVisible() for v in buttons)
        assert not find(w,'workspaceSwitch').isVisible()
        for key in ('file','edit','subtitle','view','tools','settings','help'):
            click(w,find(w,key+'MenuButton'));delay(250)
            menu=find(w,key+'Menu');assert menu.property('opened')
            from PySide6.QtCore import QMetaObject
            QMetaObject.invokeMethod(menu,'close');delay(180)
        assert not find(w,'toggleTimelinePanel').property('enabled')
        b.command('editor.start',{'mode':'edit'})
        b.command('editor.continue', {});delay(350)
        assert b.state['stage']=='review'
        assert find(w,'reviewHeading').property('text')=='歌词编辑'
        assert not find(w,'reviewOcrEvidence').isVisible()
        assert b.command('view.layout',{'timelineVisible':False});delay(120)
        timeline=find(w,'frameTimeline')
        assert timeline.isVisible() and not timeline.property('lyricVisible')
        assert find(w,'audioAnalysisPanel').isVisible()
        assert not find(w,'timelineHeader').isVisible()
        assert b.command('view.layout',{'previewVisible':False,'reviewVisible':False});delay(120)
        assert timeline.isVisible() and not find(w,'mainSplit').isVisible()
        assert not find(w,'mainPreviewPanel').isVisible() and find(w,'audioOnlyPlayback').isVisible()
        assert b.command('view.layout',{'audioVisible':False})
        assert b.command('view.layout',{'reset':True});delay(120)
        assert find(w,'mainPreviewPanel').isVisible() and find(w,'reviewPanel').isVisible()
        w.setWidth(800);delay(120)
        assert all(v.x()>=0 and v.width()>0 for v in buttons)
        w.grabWindow().save(str(tmp_path/'dev8-menu-review-800.png'))
        assert not warnings, warnings
    finally:
        w.setProperty('allowClose',True);w.close();b.shutdown();e.deleteLater();application.processEvents()


def test_style_manager_cancel_and_explicit_library_scope(bridge, tmp_path):
    from desktop_qt.style_dialog import StyleManagerDialog
    before = deepcopy(bridge.document.project.to_dict())
    d = StyleManagerDialog(bridge)
    d.show(); delay(120)
    assert d.project_list.count() == 1 and d.library_list.count() == 0
    d.duplicate(); d.to_library()
    assert len(d.profiles) == 2 and len(d.library) == 1
    assert bridge.document.project.to_dict() == before
    assert not d.library_path.exists()
    assert d.grab().save(str(tmp_path/'dev8-style-manager.png'))
    d.reject()
    assert bridge.document.project.to_dict() == before and not d.library_path.exists()


def test_style_rejects_locked_scope_without_partial_write(bridge):
    p=bridge.document.project
    p.settings['manual_locked_ids']=['target']
    before=deepcopy(p.to_dict())
    with pytest.raises(ValueError):styles.apply_style(p,bridge.history,['overlap','target'])
    assert p.to_dict()==before


def test_spectrum_only_real_media_resize_keeps_plot_mapping(application,tmp_path):
    from tools.qt_event_loop_wait import wait_until
    root=Path(__file__).resolve().parents[1]
    b=Bridge(storage_dir=tmp_path/'spectrum-state')
    e=QQmlApplicationEngine();warnings=[]
    e.warnings.connect(lambda ms:warnings.extend(m.toString() for m in ms))
    e.rootContext().setContextProperty('bridge',b);e.load(QUrl.fromLocalFile(str(root/'desktop_qt/qml/Main.qml')))
    assert e.rootObjects(),warnings
    w=e.rootObjects()[0]
    try:
        import wave, math, struct
        audio=tmp_path/'synthetic-tone.wav'
        with wave.open(str(audio),'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000)
            wav.writeframes(b''.join(struct.pack('<h',int(12000*math.sin(2*math.pi*440*i/16000))) for i in range(32000)))
        assert b.command('media.open',{'path':str(audio)})
        wait_until(lambda:b.media['ready'],timeout_ms=20000)
        wait_until(lambda:b.audioAnalysis.get('status') in ('ready','error'),timeout_ms=20000)
        assert b.audioAnalysis['status']=='ready', b.audioAnalysis
        assert b.command('stage.set',{'stage':'review'})
        assert b.command('view.layout',{'timelineVisible':False,'audioVisible':True,'audioMode':'spectrum'})
        delay(500)
        for width in (1400,800,1200):
            w.setWidth(width);delay(150)
            timeline=find(w,'frameTimeline');plot=find(w,'audioAnalysisPanel')
            assert timeline.isVisible() and plot.isVisible()
            assert not timeline.property('lyricVisible')
            assert abs(timeline.property('plotWidth')-plot.width())<1
            assert plot.width()>0
        assert w.grabWindow().save(str(tmp_path/'dev8-independent-spectrum.png'))
        assert not warnings,warnings
    finally:
        w.setProperty('allowClose',True);w.close();b.shutdown();e.deleteLater();application.processEvents()
