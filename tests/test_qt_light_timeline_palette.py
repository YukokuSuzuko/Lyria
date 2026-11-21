"""Actual offscreen light lyric lanes and separately dark audio, not native QA."""
from copy import deepcopy
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor
from lyricsync.candidate_tracks import capture_source, candidate_from_project
from lyricsync.model import Project
from test_qt_phase1_preview_workspace import ui
from test_qt_preview_visibility import application, find
from test_qt_preview_drag_gestures import chinese_fonts
from test_qt_dev4_spectrum_timeline import setup, evaluate
from tools.qt_event_loop_wait import delay, wait_until


def luminance(color):
    parts=[color.redF(),color.greenF(),color.blueF()]
    return sum((v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4)*w
               for v,w in zip(parts,(.2126,.7152,.0722)))


def contrast(a,b):
    lo,hi=sorted((luminance(a),luminance(b)))
    return (hi+.05)/(lo+.05)


def qml(item,expression):
    value=evaluate(item,expression)
    return value[0] if isinstance(value,tuple) else value


def pixel(ui,item,local_point):
    # Resolve geometry only after rendering has settled the current layout.
    shot=ui.window.grabWindow()
    point=item.mapToScene(local_point(item))
    ancestor=item
    while ancestor is not None:
        if ancestor is item or ancestor.clip():
            local=ancestor.mapFromScene(point)
            assert ancestor.isVisible()
            assert 0<=local.x()<ancestor.width() and 0<=local.y()<ancestor.height(), (
                ancestor.objectName(),local,ancestor.width(),ancestor.height())
        ancestor=ancestor.parentItem()
    ratio=shot.width()/ui.window.width()
    x,y=round(point.x()*ratio),round(point.y()*ratio)
    assert 0<=x<shot.width() and 0<=y<shot.height(),(x,y,shot.size(),item.objectName())
    color=shot.pixelColor(x,y);assert color.isValid()
    return color


def assert_light_lanes(ui):
    lyric_background=find(ui,'lyricTimelineBackground')
    # Sample the actual background in the 4px gap below the first 26px lane
    # (ruler 24px + lane 26px + 2px). A fraction of the full content height
    # can be outside the window after audio is hidden: this is a scrollable UI.
    # The x position avoids grid lines, clips, the marker and the playhead.
    background=pixel(ui,lyric_background,lambda item:QPointF(item.width()*.935,52))
    assert luminance(background)>.85,background.name()
    return background


def seed(ui,tmp_path):
    timeline,surface=setup(ui,tmp_path);ui.window.resize(1600,1200)
    ids=[]
    for first,last,text in [(110,140,'未选中歌词'),(160,190,'当前选中歌词')]:
        assert ui.bridge.command('timeline.createRange',{'first':first,'last':last})
        ids.append(ui.bridge.selected)
        assert ui.bridge.command('row.edit',{'id':ids[-1],'text':text})
    assert ui.bridge.command('marker.add',{'frame':151})
    assert ui.bridge.command('media.seek',{'frame':151})
    evaluate(timeline,'view(101,201)');delay(180)
    wait_until(lambda:surface.property('status')=='ready',timeout_ms=5000)
    return timeline,surface,ids


def test_no_media_lyrics_keep_the_same_light_background(ui):
    assert ui.bridge.command('stage.set',{'stage':'review'})
    ui.window.resize(1600,1200);delay(120)
    before=ui.bridge.document.snapshot()
    assert not ui.bridge.media['ready']
    assert not find(ui,'audioAnalysisPanel').property('available')
    assert_light_lanes(ui)
    assert ui.bridge.document.snapshot()==before


def test_light_lyrics_tick_clip_handle_and_marker_contrast(ui,tmp_path):
    timeline,surface,ids=seed(ui,tmp_path)
    before=ui.bridge.document.snapshot();background=assert_light_lanes(ui)
    tick=find(ui,'timelineTickLabel_3')
    assert contrast(tick.property('color'),background)>=4.5
    fills=[]
    for identity in ids:
        text=find(ui,'timelineText_'+identity);clip=find(ui,'timelineClip_'+identity)
        fills.append(clip.property('color').name())
        selected=identity==ui.bridge.selected
        assert clip.property('color').name()==('#8562c5' if selected else '#f1ecfb')
        assert text.property('color').name()==('#ffffff' if selected else '#29213a')
        assert text.opacity()==1
        assert contrast(text.property('color'),clip.property('color'))>=4.5
        assert qml(text,'style === Text.Normal')
        # Selected purple is itself the visible boundary against the light lane;
        # do not require its darker decorative border to contrast with its fill.
        edge=clip.property('color') if selected else qml(clip,'border.color')
        assert contrast(edge,background)>=3
    assert fills[0]!=fills[1]
    handle=find(ui,'cueEdge_first');handle.forceActiveFocus();delay(20)
    glyph=next(c for c in handle.childItems() if c.width()==2 and c.height()==12)
    assert contrast(glyph.property('color'),handle.property('color'))>=3
    assert contrast(qml(handle,'border.color'),handle.property('color'))>=3
    guide=find(ui,'timelineMarkerGuide_0');assert contrast(guide.property('color'),background)>=3
    playhead=find(ui,'timelinePlayhead');assert contrast(playhead.property('color'),background)>=3
    head=find(ui,'timelineMarkerHead_151');tip=head.mapToScene(QPointF(head.width()/2,head.property('tipY')))
    assert 0<=tip.y()-guide.mapToScene(QPointF()).y()<=1.01
    assert abs(tip.x()-guide.mapToScene(QPointF(guide.width()/2,0)).x())<.01
    assert ui.bridge.document.snapshot()==before
    assert ui.window.grabWindow().save(str(tmp_path/'light-lyrics-dark-spectrum-offscreen.png'))


@pytest.mark.parametrize('mode',['spectrum','waveform'])
def test_dark_audio_hide_restore_never_darkens_lyrics_or_changes_view(ui,tmp_path,mode):
    timeline,surface,ids=seed(ui,tmp_path)
    assert ui.bridge.command('view.layout',{'audioMode':mode})
    delay(100);before=ui.bridge.document.snapshot();audio=find(ui,'audioAnalysisPanel')
    original=(timeline.property('viewFirst'),timeline.property('span'))
    assert_light_lanes(ui)
    # The decoded fixture is silent from 1.5 to 2 seconds. Sample that actual
    # silence away from the waveform centreline, not a full-height peak.
    assert luminance(pixel(ui,audio,lambda item:QPointF(item.width()*.75,item.height()*.1)))<.05
    assert ui.bridge.command('view.layout',{'audioVisible':False});delay(220)
    assert not find(ui,'independentAudioRegion').isVisible()
    assert not find(ui,'audioSplitHandle').isVisible()
    assert_light_lanes(ui)
    assert (timeline.property('viewFirst'),timeline.property('span'))==original
    assert ui.bridge.command('media.seek',{'frame':175})
    evaluate(timeline,'view(141,181)');delay(40)
    assert ui.bridge.command('view.layout',{'audioVisible':True});delay(220)
    if mode=='spectrum':wait_until(lambda:surface.property('status')=='ready',timeout_ms=5000)
    assert_light_lanes(ui)
    assert audio.property('firstFrame')==timeline.property('viewFirst')==141
    assert audio.property('frameSpan')==timeline.property('span')==40
    a=find(ui,'audioPlayhead');t=find(ui,'timelinePlayhead')
    assert abs(a.mapToScene(QPointF(a.width()/2,0)).x()-t.mapToScene(QPointF(t.width()/2,0)).x())<.01
    assert ui.bridge.document.snapshot()==before
    assert ui.window.grabWindow().save(str(tmp_path/('light-restored-'+mode+'-offscreen.png')))


@pytest.mark.parametrize('source',['ocr','vocal'])
def test_readonly_candidate_lanes_remain_light_and_cannot_edit(ui,tmp_path,source):
    timeline,surface,ids=seed(ui,tmp_path)
    captured=capture_source(deepcopy(ui.bridge.document),source,ui.bridge.media_controller.index)
    generated=Project.from_dict(ui.bridge.document.project.to_dict())
    record=candidate_from_project(captured,generated);ui.bridge._candidate_install(record)
    before=ui.bridge.document.snapshot();transport=ui.bridge.media_controller.snapshot()
    ui.bridge.switchCandidateSource(source);delay(100)
    assert timeline.property('readOnly')
    background=assert_light_lanes(ui)
    assert contrast(find(ui,'timelineTickLabel_3').property('color'),background)>=4.5
    for command,payload in [('row.edit',{'id':ids[-1],'text':'must not overwrite'}),
                            ('timeline.move',{'id':ids[-1],'delta':1}),('history.undo',{})]:
        assert not ui.bridge.command(command,payload)
    assert ui.bridge.document.snapshot()==before
    assert ui.bridge.media_controller.snapshot()==transport
    ui.bridge.switchCandidateSource('main');delay(50)
    assert not timeline.property('readOnly') and ui.bridge.document.snapshot()==before


def test_pending_and_untimed_text_remain_readable_on_light_lanes(ui,tmp_path):
    timeline,surface,ids=seed(ui,tmp_path)
    assert ui.bridge.command('timeline.razor',{'id':ids[-1],'frame':175})
    delay(260);p=ui.bridge.document.project
    links=p.settings['pending_text_splits'];assert len(links)==1
    left_id,right_id=links[0]['left'],links[0]['right']
    text=find(ui,'timelineText_'+right_id);fill=find(ui,'pendingClipFill_'+right_id)
    assert text.property('text')=='待分配文字' and fill.opacity()==1
    assert contrast(text.property('color'),fill.property('color'))>=4.5
    # These exact amber values are the user's AE pending-state specification.
    assert fill.property('color').name()=='#f5deb7'
    assert qml(fill,'border.color').name()=='#d99a42'
    selected=find(ui,'timelineClip_'+left_id);mark=find(ui,'pendingClipMark_'+left_id)
    assert selected.property('color').name()=='#8562c5'
    assert mark.property('color').name()=='#d99a42' and mark.opacity()==1
    assert 2<=mark.height()<=3 and 0<selected.height()-mark.y()-mark.height()<=2
    # Exercise the actual untimed placeholder without moving any time boundary.
    p.mappings[0].start=p.mappings[0].end=None;ui.bridge._changed();delay(80)
    label=find(ui,'timelineOffscreen_'+ids[0]);assert label.isVisible()
    assert '未打轴' in label.property('text')
    assert contrast(label.property('color'),QColor('#FFFFFF'))>=4.5
    assert_light_lanes(ui)
    assert ui.window.grabWindow().save(str(tmp_path/'light-pending-and-untimed-offscreen.png'))


def assert_clip_state(ui,identity,*,selected,pending):
    clip=find(ui,'timelineClip_'+identity);text=find(ui,'timelineText_'+identity)
    fill=find(ui,'pendingClipFill_'+identity);mark=find(ui,'pendingClipMark_'+identity)
    assert clip.property('pendingState') is pending
    assert clip.property('color').name()==('#8562c5' if selected else '#f1ecfb')
    assert text.property('color').name()==('#ffffff' if selected else '#29213a')
    assert fill.property('color').name()=='#f5deb7'
    assert qml(fill,'border.color').name()=='#d99a42'
    assert mark.property('color').name()=='#d99a42'
    assert fill.opacity()==(1 if pending and not selected else 0)
    assert mark.opacity()==(1 if pending and selected else 0)
    effective_fill=fill.property('color') if pending and not selected else clip.property('color')
    # A real on-screen pixel inside the clip but above glyphs verifies composition,
    # not only the base Rectangle hidden under the pending fill.
    assert pixel(ui,clip,lambda item:QPointF(item.width()*.65,4)).name()==effective_fill.name()
    if selected:
        assert text.opacity()==1  # White pending-placeholder text must not be dimmed.
    alpha=text.opacity();fg=text.property('color');bg=effective_fill
    composite=QColor.fromRgbF(*(alpha*a+(1-alpha)*b for a,b in zip(
        (fg.redF(),fg.greenF(),fg.blueF()),(bg.redF(),bg.greenF(),bg.blueF()))))
    assert contrast(composite,bg)>=4.5
    if selected and pending:
        assert 2<=mark.height()<=3
        assert 0<clip.height()-mark.y()-mark.height()<=2
        assert 0<mark.x() and mark.x()+mark.width()<clip.width()
        # Wait for animation completion and two equal rendered samples, not a
        # longer fixed sleep. Offscreen Qt can retain #d89943 for >1s even with
        # opacity=1, pendingAnimating=False, and exact property #d99a42.
        # Only this composited 8-bit pixel allows one RGB quantization step;
        # the source color, opacity, geometry and contrast above remain exact.
        samples=[]
        def stable_mark_pixel():
            if clip.property('pendingAnimating'):
                samples.clear()
                return False
            samples.append(pixel(ui,mark,lambda item:QPointF(item.width()/2,item.height()/2)))
            return len(samples)>=2 and samples[-1]==samples[-2]
        wait_until(stable_mark_pixel,timeout_ms=1000,poll_ms=32)
        actual=samples[-1];expected=QColor('#d99a42')
        assert actual.alpha()==255
        assert all(abs(a-b)<=1 for a,b in zip(actual.getRgb()[:3],expected.getRgb()[:3])), actual.name()



def test_normal_pending_selection_four_states_and_split_undo_are_independent(ui,tmp_path):
    timeline,surface,ids=seed(ui,tmp_path)
    assert_clip_state(ui,ids[0],selected=False,pending=False)
    assert_clip_state(ui,ids[1],selected=True,pending=False)
    before=ui.bridge.document.project.to_dict();history=len(ui.bridge.history.undo_stack)
    transport=ui.bridge.media_controller.snapshot()
    viewport=(timeline.property('viewFirst'),timeline.property('viewLast'))
    assert ui.bridge.command('timeline.razor',{'id':ids[1],'frame':175});delay(260)
    links=ui.bridge.document.project.settings['pending_text_splits'];assert len(links)==1
    left,right=links[0]['left'],links[0]['right']
    after=ui.bridge.document.project.to_dict()
    assert len(ui.bridge.history.undo_stack)==history+1
    assert_clip_state(ui,left,selected=True,pending=True)
    assert_clip_state(ui,right,selected=False,pending=True)
    assert find(ui,'timelineText_'+right).property('text')=='待分配文字'
    # Select the blank pending half: white text and the amber bottom mark replace
    # its amber fill, while both halves retain exactly the same pending business state.
    assert ui.bridge.command('timeline.select',{'id':right});delay(80)
    assert_clip_state(ui,right,selected=True,pending=True)
    assert_clip_state(ui,left,selected=False,pending=True)
    assert ui.bridge.document.project.to_dict()==after
    assert len(ui.bridge.history.undo_stack)==history+1
    assert ui.bridge.media_controller.snapshot()==transport
    assert (timeline.property('viewFirst'),timeline.property('viewLast'))==viewport
    assert_light_lanes(ui)
    assert ui.window.grabWindow().save(str(tmp_path/'selected-blank-pending-offscreen.png'))
    assert ui.bridge.command('timeline.select',{'id':ids[0]});delay(80)
    assert_clip_state(ui,ids[0],selected=True,pending=False)
    for identity in (left,right):assert_clip_state(ui,identity,selected=False,pending=True)
    assert ui.bridge.document.project.to_dict()==after
    assert ui.bridge.command('history.undo',{});delay(260)
    assert ui.bridge.document.project.to_dict()==before
    assert len(ui.bridge.history.undo_stack)==history
    assert ui.bridge.media_controller.snapshot()==transport
    assert (timeline.property('viewFirst'),timeline.property('viewLast'))==viewport
    for identity in ids:
        assert_clip_state(ui,identity,selected=identity==ui.bridge.selected,pending=False)
    assert_light_lanes(ui)
