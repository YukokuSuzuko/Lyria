"""0.13 round-two interaction contracts, synthetic Windows/offscreen."""
import os
import pytest
from pathlib import Path
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontDatabase
from PySide6.QtQml import QQmlExpression, qmlContext
from lyricsync.editing import pending_text_links
from test_qt_timeline_input import application, timeline_video, ui, find, point, click, drag, wait


@pytest.fixture(scope='module',autouse=True)
def fonts(application):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')

def evaluate(item, expression):
    e=QQmlExpression(qmlContext(item),item,expression)
    result=e.evaluate()
    assert not e.hasError(),e.error().toString()
    return result



def assert_rows_fit_independent_audio(window,timeline):
    tracks=find(window,'timelineTracks');ruler=find(window,'timelineRulerMouse')
    audio=find(window,'audioAnalysisPanel');row_height=timeline.property('rowHeight')
    assert tracks.height()>=3*row_height
    assert tracks.height()==int((ruler.height()-24)//row_height)*row_height
    assert audio.mapToScene(QPointF(0,audio.height())).y() < ruler.mapToScene(QPointF()).y()
    assert audio.width()==ruler.width()
    assert abs(audio.mapToScene(QPointF()).x()-ruler.mapToScene(QPointF()).x())<.01
    # A no-audio fixture keeps only its truthful compact status, not an empty plot.
    if audio.property('status') not in ('ready','loading'):
        assert audio.height()==44
    else:
        assert audio.height()>=120
    assert audio.mapToItem(timeline,QPointF(0,audio.height())).y()<=timeline.height()
    return tracks


def test_default_rows_resize_and_transport(ui,tmp_path):
    app,b,w,t=ui
    tracks=find(w,'timelineTracks')
    assert find(w,'timelineRowMode') is None
    assert t.property('shownRows')==5
    # Lyric row capacity remains independent of the separate audio plot.
    # A compact no-audio status must not push selected lyrics out of view.
    assert_rows_fit_independent_audio(w,t)
    prev=find(w,'transportPreviousCue');nxt=find(w,'transportNextCue');play=find(w,'transportPlayPause')
    assert prev.property('glyph')=='previous-cue' and nxt.property('glyph')=='next-cue'
    click(w,nxt);assert b.selected=='r41'
    click(w,prev);assert b.selected=='r40'
    click(w,play);wait(app,lambda:b.media['playing'])
    assert play.property('glyph')=='pause'
    click(w,play);assert not b.media['playing'] and not b.media['playPending']
    before=b.document.snapshot()
    play_cue=find(w,'playCurrentCueButton')
    assert play_cue.property('glyph')=='play-cue'
    assert find(w,'timelinePlayPause') is None and play.isVisible()
    click(w,play_cue)
    seen=[]
    for _ in range(100):
        QTest.qWait(10);seen.append(b.media['displayedFrame'])
    assert not b.media['loopRange']
    assert not b.media['playing'] and not b.media['playPending']
    assert all(a<=z for a,z in zip(seen,seen[1:])),seen
    assert all(415<=f<=432 for f in seen if f>400),seen
    output=Path(os.environ.get('LYRIA_013_TIMELINE_EVIDENCE',str(tmp_path)));output.mkdir(parents=True,exist_ok=True)
    assert w.grabWindow().save(str(output/'round3-play-cue.png'))
    assert b.document.snapshot()==before
    assert b.command('marker.add',{'frame':422});QTest.qWait(80)
    assert b.command('media.seek',{'frame':422});wait(app,lambda:b.media['displayedFrame']==422)
    upper=find(w,'fixedMarker_422');guide=find(w,'fixedMarker_422GuideBottom');thumb=find(w,'upperPlayheadThumb')
    assert abs(upper.mapToScene(QPointF(upper.width()/2,0)).x()-thumb.mapToScene(QPointF(thumb.width()/2,0)).x())<.01
    assert guide.mapToScene(QPointF(0,guide.height())).y()<thumb.mapToScene(QPointF()).y()
    marker=find(w,'timelineMarker_422');head=find(w,'timelineMarkerHead_422');tick=find(w,'timelineTickLabel_5');playhead=find(w,'timelinePlayheadDiamond')
    guide=next(child for child in marker.childItems() if child.objectName()=='timelineMarkerGuide_0')
    assert 0 <= head.mapToScene(QPointF(head.width()/2,head.property('tipY'))).y()-guide.mapToScene(QPointF()).y() <= 1.01
    assert tick.isVisible()
    assert abs(head.mapToScene(QPointF(head.width()/2,0)).x()-marker.mapToScene(QPointF(marker.width()/2,0)).x())<.01
    path=Path(os.environ.get('LYRIA_013_TIMELINE_EVIDENCE',tmp_path));path.mkdir(parents=True,exist_ok=True)
    b.command('settings.set',{'key':'subtitlePreview','value':False});QTest.qWait(60)
    assert w.grabWindow().save(str(path/'round2-timeline.png'))


def test_lyric_context_edit_copy_paste_split_and_multi(ui,tmp_path):
    app,b,w,t=ui
    # QML TextEdit.copy/paste run in C++ and cannot be intercepted by monkeypatch.
    # The offscreen QPA has a process-local clipboard. Never run this test against
    # the native Windows backend on the user's shared window station.
    if app.platformName()!='offscreen':
        pytest.skip('Native clipboard requires a disposable isolated environment; no clipboard accessed')
    context=find(w,'lyricContext_r40');editor=find(w,'chineseEditor_r40')
    assert context and editor
    before=b.document.snapshot()
    QTest.qWait(300)
    QTest.mouseClick(w,Qt.RightButton,Qt.NoModifier,point(context));QTest.qWait(50)
    output=Path(os.environ.get('LYRIA_013_TIMELINE_EVIDENCE',str(tmp_path)));output.mkdir(parents=True,exist_ok=True)
    assert w.grabWindow().save(str(output/'round2-lyric-menu.png'))
    action=find(w,'lyricCopyAction_r40')
    assert point(action).x()>point(context).x()-220 and point(action).y()>point(context).y()-50
    click(w,action)
    assert app.clipboard().text()==b.state['selection']['current']['chinese']
    assert b.document.snapshot()==before
    QTest.mouseClick(w,Qt.RightButton,Qt.NoModifier,point(context));QTest.qWait(50)
    click(w,find(w,'lyricEditAction_r40'));assert not editor.property('readOnly')
    QTest.keyClick(w,Qt.Key_Escape)
    # Paste uses the existing editor, undo history, and lock checks.
    app.clipboard().setText('测试')
    QTest.mouseClick(w,Qt.RightButton,Qt.NoModifier,point(context));QTest.qWait(50)
    click(w,find(w,'lyricPasteAction_r40'));assert '测试' in b.state['selection']['current']['chinese']
    assert b.command('history.undo',{})


def test_pending_context_reuses_allocation_actions(ui):
    app,b,w,t=ui
    assert b.command('timeline.razor',{'id':'r40','frame':424})
    QTest.qWait(250)
    identity=b.selected
    clip=find(w,'timelineClip_'+identity);assert clip
    QTest.mouseClick(w,Qt.RightButton,Qt.NoModifier,point(clip));QTest.qWait(80)
    action=find(w,'timelineKeepAction_'+identity);assert action and action.isEnabled()
    before=[(m.id,m.start,m.end) for m in b.document.project.mappings]
    # A captured stale context is rejected by the existing selection/revision guard.
    assert b.command('timeline.select',{'id':'r41'});QTest.qWait(30)
    click(w,action)
    assert pending_text_links(b.document.project)
    assert '状态已更新' in b.state['error']
    assert b.command('timeline.select',{'id':identity});QTest.qWait(120)
    clip=find(w,'timelineClip_'+identity)
    QTest.mouseClick(w,Qt.RightButton,Qt.NoModifier,point(clip));QTest.qWait(70)
    click(w,find(w,'timelineKeepAction_'+identity))
    assert [(m.id,m.start,m.end) for m in b.document.project.mappings]==before
    assert not pending_text_links(b.document.project)


def test_splitter_allows_three_to_window_capacity(ui,tmp_path):
    app,b,w,t=ui
    w.resize(1400,1200);QTest.qWait(300)
    tracks=find(w,'timelineTracks');grip=find(w,'timelineSplitHandle')
    before=b.document.snapshot()
    assert_rows_fit_independent_audio(w,t)
    assert find(w,'audioSplitHandle') is not None
    assert not find(w,'audioSplitHandle').isVisible()  # no audio, compact status only
    original_height=t.height();original_audio=find(w,'audioAnalysisPanel').height()
    span=t.property('span')
    # v0.22 restores this divider; actual mouse drags redistribute workspace height;
    # row capacity still adapts to the window and independent audio status.
    assert grip is not None and grip.isVisible() and grip.height()>=6
    for delta in (-120,120):
        old_height=t.height()
        start=grip.mapToScene(QPointF(grip.width()/2,grip.height()/2)).toPoint()
        drag(w,start,start+QPoint(0,delta));QTest.qWait(150)
        assert abs(t.height()-old_height+delta)<2
        assert find(w,'audioAnalysisPanel').height()==original_audio
        assert tracks.height()>=90
        assert_rows_fit_independent_audio(w,t)
        assert t.property('span')==span
    for height in (940,1200):
        w.resize(1400,height);QTest.qWait(150)
        assert t.height()>=t.property('minimumExpandedHeight')
        assert_rows_fit_independent_audio(w,t)
        assert t.property('span')==span
    assert b.document.snapshot()==before
    w.resize(760,600);QTest.qWait(300)
    outer=find(w,'makeScroll')
    assert outer.property('contentItem') is None
    QTest.qWait(100)
    assert tracks.height()>=t.property('rowHeight')
    from test_qt_timeline_input import wheel
    before_y=tracks.property('contentY')
    wheel(app,w,tracks,-120,Qt.KeyboardModifier.ControlModifier)
    assert tracks.property('contentY')>before_y
    assert b.document.snapshot()==before
    for name in ['timelineSelectTool','timelineHandTool','timelineZoomOut','audioVisibilityToggle','timelineCreateMenuButton']:
        item=find(w,name);p=item.mapToItem(t,QPointF())
        assert 0<=p.x() and p.x()+item.width()<=t.width()
    out=Path(os.environ.get('LYRIA_013_TIMELINE_EVIDENCE',tmp_path));out.mkdir(parents=True,exist_ok=True)
    assert w.grabWindow().save(str(out/'round2-narrow-complete-scrollable-row.png'))


def test_context_split_and_multiselect_merge_share_existing_commands(ui):
    app,b,w,t=ui
    assert b.command('marker.add',{'frame':424})
    QTest.qWait(300)
    context=find(w,'lyricContext_r40');editor=find(w,'chineseEditor_r40')
    x=evaluate(context,'editor.positionToRectangle(3).x')[0]+1
    y=evaluate(context,'editor.positionToRectangle(3).y')[0]+8
    original=b.state['selection']['current']['chinese'];count=len(b.document.project.mappings)
    QTest.mouseClick(w,Qt.RightButton,Qt.NoModifier,point(context,x,y));QTest.qWait(70)
    assert find(w,'lyricMergeAction_r40') is None or not find(w,'lyricMergeAction_r40').isVisible()
    click(w,find(w,'lyricSplitAction_r40'));QTest.qWait(300)
    assert len(b.document.project.mappings)==count+1
    current=b.selected
    rows=b.state['rows'];parts=[r for r in rows if r['firstFrame'] in (420,424)]
    assert [(r['firstFrame'],r['lastFrame']) for r in parts]==[(420,423),(424,427)]
    assert ''.join(r['chinese'] for r in parts)==original
    for row in parts:assert b.command('row.multiToggle',{'id':row['id'],'selected':True})
    QTest.qWait(300)
    context=find(w,'lyricContext_'+current)
    QTest.mouseClick(w,Qt.RightButton,Qt.NoModifier,point(context));QTest.qWait(70)
    click(w,find(w,'lyricMergeAction_'+current));QTest.qWait(120)
    assert len(b.document.project.mappings)==count
    assert b.state['selection']['current']['chinese']==original


def test_hidden_height_is_not_cached_and_manual_zoom_keeps_vertical_follow(ui):
    app,b,w,t=ui
    t.setProperty('manualView',True);t.setProperty('followEnabled',True)
    t.setProperty('viewFirst',390);t.setProperty('viewLast',470)
    assert b.command('stage.set',{'stage':'import'});QTest.qWait(30)
    cached=t.property('expandedLayoutHeight')
    t.setHeight(1000)
    assert t.property('expandedLayoutHeight')==cached
    assert b.command('stage.set',{'stage':'review'});QTest.qWait(300)
    tracks=find(w,'timelineTracks');row=find(w,'timelineRow_r40')
    assert row is not None
    y=row.mapToItem(tracks,QPointF()).y()
    assert 0<=y and y+row.height()<=tracks.height()
    assert [t.property('viewFirst'),t.property('viewLast')]==[390,470]


def test_native_clipboard_guard_exits_before_any_ui_or_clipboard_access(tmp_path):
    class NativeBackend:
        def platformName(self):return 'windows'
        def clipboard(self):raise AssertionError('Must not obtain a native clipboard')
    with pytest.raises(pytest.skip.Exception,match='Native clipboard'):
        test_lyric_context_edit_copy_paste_split_and_multi((NativeBackend(),None,None,None),tmp_path)
