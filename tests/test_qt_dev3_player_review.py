"""Dev3 player/review feedback, real controls in Windows offscreen software QML.
Generated fixtures only: no user media, credentials, network or paid APIs.
"""
import os
from pathlib import Path
from PySide6.QtCore import QPoint, QPointF, Qt, QMetaObject, QObject
from PySide6.QtTest import QTest
from test_qt_preview_visibility import application, ui, install_cues, present, find
from test_qt_review_selection import ui as review_ui, find as review_find


def click(window, item):
    assert item is not None and item.isVisible() and item.isEnabled()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier,
                    item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint())
    QTest.qWait(100)


def snapshot(window, name):
    QTest.mouseMove(window, QPoint(2, 2)); QTest.qWait(100)
    folder = os.environ.get('LYRIA_DEV3_PLAYER_EVIDENCE')
    if folder:
        p = Path(folder); p.mkdir(parents=True, exist_ok=True)
        assert window.grabWindow().save(str(p / (name + '.png')))


def test_toolbar_keeps_transport_center_and_all_controls_in_bounds(ui):
    install_cues(ui, layout=False); present(ui, 2)
    for width in (330, 500, 900):
        ui.window.resize(width, 700); QTest.qWait(160)
        center = find(ui, 'transportButtons'); summary = find(ui, 'transportSummary')
        assert abs(center.x()+center.width()/2-summary.width()/2) < .1
        controls = find(ui, 'playerControls')
        for name in ('addMarkerButton', 'morePlaybackTools', 'playCurrentCueButton',
                     'muteButton', 'subtitlePreviewToggle', 'subtitleStyleButton', 'selectRoiButton', 'showRoiButton'):
            b = find(ui, name)
            if not b.isVisible():continue
            pos = b.mapToItem(controls, QPointF())
            assert pos.x() >= 0 and pos.x()+b.width() <= controls.width()+.1, (width,name,pos.x(),b.width(),controls.width())
            assert pos.y() >= 0 and pos.y()+b.height() <= controls.height()+.1
        left, right = find(ui, 'playerToolRow'), find(ui, 'playerRightTools')
        assert left.x()+left.width() <= right.x()
        snapshot(ui.window, 'player-' + str(width))
    assert find(ui, 'showRoiButton').property('glyph') == 'ocr-region'
    assert find(ui, 'subtitleStyleButton').property('glyph') == 'gear'


def test_relocated_roi_and_marker_delete_confirmation(ui):
    install_cues(ui, layout=False); present(ui, 2)
    b = find(ui, 'showRoiButton')
    assert not b.isVisible() and not find(ui,'selectRoiButton').isVisible()
    click(ui.window,find(ui,'addMarkerButton'))
    markers = list(ui.bridge.document.project.settings['frame_markers'])
    assert markers
    click(ui.window,find(ui,'morePlaybackTools'))
    QMetaObject.invokeMethod(ui.window.findChild(QObject,'clearMarkersButton'),'triggered')
    QTest.qWait(100)
    dialog=ui.window.findChild(QObject,'clearMarkersDialog')
    assert dialog.property('visible')
    assert ui.bridge.document.project.settings['frame_markers'] == markers
    QMetaObject.invokeMethod(dialog,'reject'); QTest.qWait(100)
    assert ui.bridge.document.project.settings['frame_markers'] == markers
    click(ui.window,find(ui,'morePlaybackTools'))
    QMetaObject.invokeMethod(ui.window.findChild(QObject,'clearMarkersButton'),'triggered')
    QTest.qWait(100)
    QMetaObject.invokeMethod(dialog,'accept'); QTest.qWait(100)
    assert not ui.bridge.document.project.settings['frame_markers']
    ui.bridge.command('history.undo', {})
    assert ui.bridge.document.project.settings['frame_markers'] == markers


def test_review_top_controls_typography_and_action_hierarchy(review_ui):
    window=review_ui.window
    QTest.qWait(200)
    # v0.22 deliberately removes the multiselect button and persistent split hint.
    assert review_find(window,'multiSelectMode') is None
    assert review_find(window,'splitTargetHint') is None
    header=review_find(window,'reviewHeaderRow')
    more=review_find(window,'reviewOperationsButton')
    assert more.parentItem()==header
    assert abs(more.x()+more.width()-header.width())<1
    assert 'F' in more.property('tip')
    for name in ('unconfirmedFilter','reviewAddBlankButton','reviewTimeFormat'):
        item=review_find(window,name)
        assert item.isVisible() and item.isEnabled()
        assert item.x() >= 0 and item.x()+item.width() <= item.parentItem().width()+.1
    editor=review_find(window,'chineseEditor_r40')
    assert editor.property('font').pixelSize() == 15
    accent=review_find(window,'selectedCueAccent_r40')
    assert accent.isVisible() and accent.width()==3 and accent.height()>=26
    from test_qt_review_selection import contained, walk
    unselected=next(r for r in walk(window.contentItem()) if r.objectName().startswith('cueRow_') and r.property('cueId')!='r40' and contained(review_ui,r.property('cueId')))
    assert not review_find(window,'selectedCueAccent_'+unselected.property('cueId')).isVisible()
    assert review_find(window,'confirmCurrent').property('emphasized')
    assert review_find(window,'confirmAndNext').property('primary')
    assert review_find(window,'deleteCurrentCueButton').property('destructive')
    # Ctrl multi-select is permanently available without a mode toggle.
    from test_qt_review_selection import contained, walk
    other=next(r for r in walk(window.contentItem()) if r.objectName().startswith('cueRow_') and r.property('cueId')!='r40' and contained(review_ui,r.property('cueId')))
    QTest.mouseClick(window,Qt.LeftButton,Qt.ControlModifier,other.mapToScene(QPointF(5,other.height()/2)).toPoint())
    assert set(review_ui.bridge.state['multiSelectedIds'])=={'r40',other.property('cueId')}
    snapshot(window,'review-default')


def test_playback_control_height_drags_and_returns_to_compact_minimum(ui):
    install_cues(ui, layout=False); present(ui, 2)
    ui.panel.controlsHeightEdited.connect(lambda value: ui.panel.setProperty('controlsExtraHeight', value))
    controls=find(ui,'playerControls'); handle=find(ui,'playerControlsResizeHandle')
    minimum=controls.height()
    def drag_by(delta):
        pos=handle.mapToScene(QPointF(handle.width()/2,handle.height()/2)).toPoint()
        QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,pos)
        QTest.mouseMove(ui.window,pos+QPoint(0,delta),30)
        QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,pos+QPoint(0,delta))
        QTest.qWait(100)
    drag_by(-50)
    assert ui.panel.property('controlsExtraHeight') >= 45
    # Phase 5 expands actual rows/spacing, never 50 px of arbitrary blank area.
    assert minimum < controls.height() <= minimum+50
    content = find(ui, 'transportSummary')
    assert content.isVisible()
    drag_by(100)
    assert ui.panel.property('controlsExtraHeight') < 0
    assert controls.height() < minimum
    pos=handle.mapToScene(QPointF(handle.width()/2,handle.height()/2)).toPoint()
    QTest.mouseDClick(ui.window,Qt.LeftButton,Qt.NoModifier,pos)
    QTest.qWait(100)
    assert ui.panel.property('controlsExtraHeight') == 0
    assert abs(controls.height()-minimum)<.1
    snapshot(ui.window,'player-resize-reset')


def test_bookmark_icons_have_outward_tip_and_solid_delete_slash(application):
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    import xml.etree.ElementTree as ET
    root=Path(__file__).resolve().parents[1]/'desktop_qt/assets/icons'
    images=[]
    for name in ('bookmark','bookmark-remove'):
        source=root/(name+'.svg')
        paths=ET.fromstring(source.read_text(encoding='utf-8')).findall('{http://www.w3.org/2000/svg}path')
        assert paths[0].attrib['d']=='M5 3H19V15L12 21L5 15Z'
        assert all('stroke-dasharray' not in path.attrib for path in paths)
        if name=='bookmark-remove':
            assert paths[1].attrib['d']=='M2 2l20 20'
            assert paths[1].attrib['stroke-width']=='1.2'
        renderer=QSvgRenderer(str(source));assert renderer.isValid()
        im=QImage(96,96,QImage.Format_ARGB32_Premultiplied);im.fill(Qt.transparent)
        painter=QPainter(im);renderer.render(painter);painter.end()
        assert any(im.pixelColor(x,y).alpha()>100 for x in range(44,53) for y in range(80,87))
        assert all(im.pixelColor(x,y).alpha()==0 for x in range(18,23) for y in range(76,86))
        images.append(im)
    assert images[0] != images[1]
    if os.environ.get('LYRIA_DEV3_PLAYER_EVIDENCE'):
        canvas=QImage(224,112,QImage.Format_RGB32);canvas.fill(Qt.white)
        painter=QPainter(canvas)
        for i,im in enumerate(images):painter.drawImage(QPoint(8+i*112,8),im)
        painter.end()
        assert canvas.save(str(Path(os.environ['LYRIA_DEV3_PLAYER_EVIDENCE'])/'bookmark-final.png'))


def test_context_add_before_after_and_undo_preserves_existing_cues(review_ui):
    from test_qt_review_selection import assert_selected_visible
    ui=review_ui
    before=ui.bridge.document.snapshot()
    ids=[m.id for m in ui.bridge.document.project.mappings]
    for action,delta in [('lyricAddBefore_r40',0),('lyricAddAfter_r40',1)]:
        assert_selected_visible(ui,'r40')
        editor=review_find(ui.window,'chineseEditor_r40')
        QTest.mouseClick(ui.window,Qt.RightButton,Qt.NoModifier,editor.mapToScene(QPointF(8,editor.height()/2)).toPoint())
        QTest.qWait(80)
        item=review_find(ui.window,action)
        assert item is not None and item.isVisible()
        click(ui.window,item)
        rows=list(ui.bridge.document.project.mappings)
        assert len(rows)==len(ids)+1
        assert rows[ids.index('r40')+delta].id not in ids
        assert [m.id for m in rows if m.id in ids]==ids
        assert ui.bridge.command('history.undo',{})
        restored=ui.bridge.document.snapshot()
        assert restored['revision']>before['revision']  # Undo is itself a revision.
        assert {k:v for k,v in restored.items() if k!='revision'}=={k:v for k,v in before.items() if k!='revision'}
        assert ui.bridge.command('timeline.select',{'id':'r40'})
