"""Preview-only overlap layout; actual QML geometry, pixels and mouse gestures."""
import json
import os
from pathlib import Path
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from tools.qt_event_loop_wait import delay, wait_until
from test_qt_preview_visibility import application, ui, install_cues, present, find
from test_qt_preview_drag_gestures import chinese_fonts, center, move_steps, settled
from lyricsync.export import render_ass, placement_options


def rows(ui):
    result=[]
    for n in range(10):
        row=find(ui,'previewSubtitle_'+str(n))
        if row is None:break
        result.append(row)
    return result


def geometry(ui):return [(r.x(),r.y(),r.height()) for r in rows(ui)]


def assert_separated(ui):
    rect=find(ui,'videoPreview').property('contentRect')
    ordered=sorted(rows(ui),key=lambda r:r.y())
    assert len(ordered)>1
    assert ordered[0].y()>=rect.y()-.1
    assert ordered[-1].y()+ordered[-1].height()<=rect.bottom()+.1
    for a,b in zip(ordered,ordered[1:]):assert a.y()+a.height()+3.9<=b.y()


def setup_overlap(ui,position='bottom',height=80):
    p=install_cues(ui,resolution=(640,360),entries=[(0.,2.,'第一句中文\n日本語の歌詞'),(0.,1.,'第二句中文'),(.1,1.,'第三句较长中文\n第二行内容')])
    p.settings.update(font='Microsoft YaHei UI',subtitle_position=position,subtitle_height_percent=height)
    ui.bridge._changed();settled(ui);present(ui,3);delay(30)
    return p


def export(p):
    texts={t.id:t.text for t in p.translations}
    entries=[(m.start,m.end,'\n'.join(texts[t] for t in m.translation_ids)) for m in p.mappings]
    return render_ass(entries,(640,360),p.settings['font'],p.settings['font_size'],**placement_options(p))


def evidence(ui,tmp_path,name):
    out=Path(os.environ.get('LYRIA_013_PREVIEW_EVIDENCE') or tmp_path);out.mkdir(parents=True,exist_ok=True)
    assert ui.window.grabWindow().save(str(out/(name+'.png')))
    (out/(name+'.json')).write_text(json.dumps(geometry(ui)),encoding='utf-8')


@pytest.mark.parametrize('position,height',[('bottom',80),('top',80),('custom',5),('custom',95)])
def test_overlap_offsets_are_readable_temporary_and_not_exported(ui,tmp_path,position,height):
    p=setup_overlap(ui,position,height);before=ui.bridge.document.snapshot();ass=export(p)
    assert_separated(ui);initial=geometry(ui)
    for _ in range(8):ui.bridge.mediaChanged.emit();delay(15);assert geometry(ui)==initial
    assert ui.bridge.document.snapshot()==before and export(p)==ass
    evidence(ui,tmp_path,'overlap-'+position+'-'+str(height))
    # Leaving the overlap restores the remaining cue's original user anchor.
    present(ui,16);delay(30);remaining=rows(ui)
    assert len(remaining)==1
    assert abs(remaining[0].y()-remaining[0].property('sourceTop'))<.01
    assert ui.bridge.document.snapshot()==before and export(p)==ass
    # Returning to the same frame gives the same deterministic layout.
    present(ui,3);delay(30);assert geometry(ui)==initial
    ui.window.resize(620,560);delay(80);assert_separated(ui)
    assert ui.bridge.document.snapshot()==before and export(p)==ass


def test_overlap_drag_snap_escape_and_release_preserve_preview_offsets(ui,tmp_path):
    p=setup_overlap(ui);before=ui.bridge.document.snapshot();times=[(m.start,m.end) for m in p.mappings]
    rect=find(ui,'videoPreview').property('contentRect');items=rows(ui)
    start=center(items[0]);top=min(r.y() for r in items);bottom=max(r.y()+r.height() for r in items)
    target=start+QPoint(0,round(rect.center().y()-(top+bottom)/2))
    QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,start);move_steps(ui,start,target);delay(50)
    assert ui.panel.property('subtitleSnapY');assert_separated(ui)
    assert ui.bridge.document.snapshot()==before
    final=geometry(ui);dy=ui.panel.property('subtitleDragY')/rect.height()
    initial_anchor=ui.panel.property('fallbackStyle').toVariant()['y']
    QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,target)
    ui.bridge.layout_timer.stop();delay(80);assert geometry(ui)==final
    ui.bridge.layout_timer.start();settled(ui);after=geometry(ui)
    assert all(abs(a[1]-b[1])<1.5 for a,b in zip(after,final))
    # Only the intentional drag delta reaches actual subtitle settings.
    assert abs(p.settings['subtitle_height_percent']/100-initial_anchor-dy)<1e-6
    assert [(m.start,m.end) for m in p.mappings]==times
    start=center(rows(ui)[0]);QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,start)
    move_steps(ui,start,start+QPoint(32,-32));delay(100)
    assert ui.panel.property('subtitleDragY')<-30
    final=geometry(ui);QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,start+QPoint(32,-32));settled(ui)
    assert all(abs(a[1]-b[1])<1.5 for a,b in zip(geometry(ui),final));assert_separated(ui)
    evidence(ui,tmp_path,'overlap-dragged')


@pytest.mark.parametrize('direction',[-1,1])
def test_overlap_group_clamps_at_picture_edge_without_release_jump(ui,direction):
    p=setup_overlap(ui,'custom',5 if direction<0 else 95)
    before=geometry(ui);rect=find(ui,'videoPreview').property('contentRect')
    start=center(rows(ui)[0]);end=start+QPoint(0,direction*160)
    QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,start);move_steps(ui,start,end)
    assert_separated(ui);final=geometry(ui)
    QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,end);settled(ui)
    assert_separated(ui)
    assert all(abs(a[1]-b[1])<1.5 for a,b in zip(geometry(ui),final))
    # Move away from an already clamped edge: keep preview correction local,
    # never bake it into the intentional anchor delta or rebound on release.
    start=center(rows(ui)[0]);end=start+QPoint(0,-direction*40)
    QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,start);move_steps(ui,start,end)
    final=geometry(ui);assert_separated(ui)
    QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,end);settled(ui)
    assert all(abs(a[1]-b[1])<1.5 for a,b in zip(geometry(ui),final));assert_separated(ui)


def test_overlap_hidden_preview_and_roi_keep_existing_priority(ui):
    from test_qt_preview_visibility import enter_roi_import
    p=setup_overlap(ui);enter_roi_import(ui);before=ui.bridge.document.snapshot();start=center(rows(ui)[0])
    ui.panel.setProperty('roiSelecting',True);delay(20)
    QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,start);move_steps(ui,start,start+QPoint(70,-45))
    assert not ui.panel.property('subtitleDragging')
    QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,start+QPoint(70,-45));delay(30)
    assert ui.bridge.state['roi'];settled(ui);assert_separated(ui)
    assert ui.bridge.command('settings.set',{'key':'subtitlePreview','value':False});delay(20);assert not rows(ui)
    assert ui.bridge.command('settings.set',{'key':'subtitlePreview','value':True});settled(ui);assert_separated(ui)


def test_horizontal_commit_does_not_leave_layout_preservation_latched(ui):
    setup_overlap(ui);start=center(rows(ui)[0])
    QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,start)
    move_steps(ui,start,start+QPoint(35,0))
    QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,start+QPoint(35,0));settled(ui)
    assert not ui.panel.property('subtitlePreservePreviewLayout')
    assert_separated(ui)


@pytest.mark.parametrize('count',[2,3])
def test_center_anchor_repacking_uses_available_picture_capacity(ui,tmp_path,count):
    entries=[(0.,.9,'短句'),(0.,2.,'\n'.join('长字幕第'+str(n)+'行' for n in range(1,10)))]
    if count==3:entries.append((.1,1.,'第三句'))
    p=install_cues(ui,resolution=(640,360),entries=entries)
    p.settings.update(font='Microsoft YaHei UI',subtitle_position='custom',subtitle_height_percent=50)
    ui.bridge._changed();settled(ui);present(ui,3);delay(30)
    rect=find(ui,'videoPreview').property('contentRect');items=rows(ui)
    assert len(items)==count
    total=sum(r.height() for r in items)+4*(count-1)
    assert total<rect.height()  # This is not an unavoidable overflow case.
    before=ui.bridge.document.snapshot();ass=export(p)
    evidence(ui,tmp_path,'overlap-center-repack-'+str(count))
    assert_separated(ui)
    original=geometry(ui)
    for _ in range(5):ui.bridge.mediaChanged.emit();delay(10);assert geometry(ui)==original
    assert ui.bridge.document.snapshot()==before and export(p)==ass
    # Resize reflows actual text heights; a complete group still fits.
    ui.window.resize(780,620);delay(60);assert_separated(ui)
    ui.window.resize(900,700);delay(60);assert geometry(ui)==original
    # A user drag preserves packed order and commits only its real displacement.
    anchor=ui.panel.property('fallbackStyle').toVariant()['y']
    start=center(rows(ui)[0]);end=start+QPoint(20,15)
    QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,start);move_steps(ui,start,end)
    final=geometry(ui);assert_separated(ui)
    delta=ui.panel.property('subtitleDragY')/rect.height()
    QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,end);settled(ui)
    assert all(abs(a[1]-b[1])<1.5 for a,b in zip(geometry(ui),final));assert_separated(ui)
    assert abs(p.settings['subtitle_height_percent']/100-anchor-delta)<1e-6
    assert [(m.start,m.end) for m in p.mappings]==[(a,b) for a,b,_ in entries]
