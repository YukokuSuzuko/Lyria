"""Actual offscreen QTest/pixel evidence. Never controls the user's desktop."""
import json
import os
from pathlib import Path
import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt, QEvent
from PySide6.QtGui import QMouseEvent
from tools.qt_event_loop_wait import delay, wait_until
from test_qt_preview_visibility import install_cues, present, find as pfind
from PySide6.QtTest import QTest
from test_qt_timeline_input import ui, find, point, drag
from test_qt_timeline_snap import application, base_application


@pytest.fixture
def preview_ui(application, tmp_path):
    from types import SimpleNamespace
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from desktop_qt.controller import Bridge
    from test_qt_preview_visibility import QML_ROOT
    bridge=engine=window=None
    try:
        bridge=Bridge(storage_dir=tmp_path/'preview-recovery')
        engine=QQmlApplicationEngine()
        engine.rootContext().setContextProperty('bridge',bridge)
        engine.loadData(b'''import QtQuick
import QtQuick.Controls
import "."
ApplicationWindow {
    visible: true; width: 900; height: 700; color: "#F7F5FB"
    PreviewPanel {
        objectName: "previewUnderTest"; anchors.fill: parent
        controller: bridge; media: bridge.media; state: bridge.uiState; review: true
    }
}''',QUrl.fromLocalFile(str(QML_ROOT/'PreviewVisibilityHarness.qml')))
        assert engine.rootObjects()
        window=engine.rootObjects()[0]
        result=SimpleNamespace(app=application,bridge=bridge,window=window)
        result.panel=pfind(result,'previewUnderTest')
        yield result
    finally:
        if bridge is not None:
            bridge.shutdown();bridge.wait_for_shutdown(application)
            bridge.media_controller.worker.thread.join(10)
            assert not bridge.media_controller.worker.thread.is_alive()
        if window is not None:window.close()
        if engine is not None:
            destroyed=[];engine.destroyed.connect(lambda:destroyed.append(True))
            engine.deleteLater();application.sendPostedEvents(None,QEvent.Type.DeferredDelete)
            wait_until(lambda:bool(destroyed))
        application.processEvents()


@pytest.fixture(scope='module')
def timeline_video(tmp_path_factory):
    import av
    path=tmp_path_factory.mktemp('visual-media')/'visual-synthetic.mp4'
    with av.open(str(path),'w') as container:
        stream=container.add_stream('libx264',rate=30)
        stream.width=640;stream.height=360;stream.pix_fmt='yuv420p'
        stream.options={'preset':'ultrafast','threads':'1'}
        for _ in range(1200):
            frame=av.VideoFrame(640,360,'yuv420p')
            for plane in frame.planes:plane.update(bytes([100])*plane.buffer_size)
            for packet in stream.encode(frame):container.mux(packet)
        for packet in stream.encode():container.mux(packet)
    return path


def test_measure_player_and_hover(ui, tmp_path):
    app, bridge, window, timeline = ui
    out = Path(os.environ.get('LYRIA_VISUAL_EVIDENCE', str(tmp_path / 'visual')))
    out.mkdir(parents=True, exist_ok=True)
    # Synthetic decoder source uses a readable subtitle style.
    bridge.document.project.settings.update(font_size=20, font='Microsoft YaHei UI')
    bridge._changed(); delay(150)
    before = bridge.document.snapshot()
    records = []
    for width, height in [(1400, 940), (1920, 1080), (2560, 1440)]:
        window.resize(width, height); QTest.qWait(180)
        for mode in ('preferred', 'maximum', 'minimum'):
            if mode != 'preferred':
                handle = find(window, 'mainSplitHandle')
                start = point(handle)
                drag(window, start, QPoint(width-390 if mode == 'maximum' else 340, start.y()))
                restored_handle = find(window, 'timelineSplitHandle')
                assert restored_handle is not None and restored_handle.isVisible() and restored_handle.height() >= 6
                assert timeline.height() >= timeline.property('minimumExpandedHeight')
            panel = find(window, 'mainPreviewPanel')
            controls = find(window, 'playerControls')
            video = find(window, 'videoPreview'); rect = video.property('contentRect')
            review = find(window, 'reviewList')
            records.append(dict(window=[width,height],mode=mode,picture=[rect.width(),rect.height()],
                panel=[panel.width(),panel.height()],controlsHeight=controls.height(),
                controlBottom=controls.mapToScene(QPointF(0,controls.height())).y(),
                panelBottom=panel.mapToScene(QPointF(0,panel.height())).y(),
                reviewListHeight=review.height(),timelineHeight=timeline.height(),
                preferences=bridge.state['workspaceLayout']))
            assert abs(rect.width()/rect.height()-16/9)<.001
            assert window.grabWindow().save(str(out/f'{width}-{mode}.png'))
    window.resize(1400,940); QTest.qWait(180)
    hover=[]
    for name in ('fileMenuButton','stageButton_import','transportCueFirst'):
        button=find(window,name); QTest.mouseMove(window,QPoint(1,1)); QTest.qWait(160)
        QTest.mouseMove(window,point(button))
        for n in range(10):
            QTest.qWait(12)
            image=window.grabWindow(); pos=button.mapToScene(QPointF(button.width()/2,3))
            c=image.pixelColor(round(pos.x()*image.width()/window.width()),round(pos.y()*image.height()/window.height()))
            hover.append(dict(name=name,n=n,rgb=[c.red(),c.green(),c.blue()]))
            if n in (1,4,9): image.save(str(out/f'hover-{name}-{n}.png'))
    assert bridge.document.snapshot()==before
    (out/'measurements.json').write_text(json.dumps(dict(dpi=window.devicePixelRatio(),layout=records,hover=hover),indent=2),encoding='utf8')


def evidence(tmp_path):
    out=Path(os.environ.get('LYRIA_VISUAL_EVIDENCE',str(tmp_path/'visual')))
    out.mkdir(parents=True,exist_ok=True)
    return out


def test_button_centers_hover_pixels_and_tooltip_lifecycle(ui,tmp_path):
    app,bridge,window,timeline=ui
    window.resize(1400,940);window.requestActivate();delay(220)
    out=evidence(tmp_path);records=[]
    for name in ('fileMenuButton','stageButton_import','transportCueFirst','addMarkerButton'):
        button=find(window,name);content=find(window,name+'Content')
        assert content is not None
        center=content.mapToItem(button,QPointF(content.width()/2,content.height()/2))
        assert abs(center.x()-button.width()/2)<=.1
        assert abs(center.y()-button.height()/2)<=.1, (name, center, [button.property(k) for k in ("topPadding","bottomPadding","padding","availableHeight")], content.parentItem().y(), content.parentItem().height())
        for child in content.childItems():
            if child.isVisible():
                child_center=child.mapToItem(button,QPointF(child.width()/2,child.height()/2))
                assert abs(child_center.y()-button.height()/2)<=.1,(name,child.metaObject().className(),child_center)
        QTest.mouseMove(window,QPoint(1,1));delay(160)
        minimum=255
        for iteration in range(3):
            QTest.mouseMove(window,point(button))
            for _ in range(8):
                delay(15);im=window.grabWindow();pos=button.mapToScene(QPointF(button.width()/2,3))
                c=im.pixelColor(round(pos.x()*im.width()/window.width()),round(pos.y()*im.height()/window.height()))
                minimum=min(minimum,c.red(),c.green(),c.blue())
            QTest.mouseMove(window,QPoint(1,1));delay(30)
        assert minimum>=210,(name,minimum)
        QTest.mouseMove(window,point(button));wait_until(lambda: button.property('toolTipVisible'),timeout_ms=1800)
        QTest.mouseMove(window,QPoint(1,1));wait_until(lambda: not button.property('toolTipVisible'))
        button.forceActiveFocus();delay(20)
        assert button.hasActiveFocus() and not button.property('toolTipVisible')
        window.grabWindow().save(str(out/(name+'-focus.png')))
        records.append(dict(name=name,center=[center.x(),center.y()],size=[button.width(),button.height()],minimumHoverRGB=minimum))
    file_button=find(window,'fileMenuButton');file_button.forceActiveFocus()
    QTest.keyClick(window,Qt.Key.Key_Tab);delay(30)
    assert not file_button.hasActiveFocus() and not file_button.property('toolTipVisible')
    button=find(window,'addMarkerButton');QTest.mouseMove(window,point(button));wait_until(lambda: button.property('toolTipVisible'),timeout_ms=1800)
    window.setProperty('workspace','qc');delay(80)
    assert not button.property('toolTipVisible')
    window.setProperty('workspace','make');delay(220)
    assert not button.property('toolTipVisible')
    (out/'button-centers.json').write_text(json.dumps(records,indent=2),encoding='utf8')


def test_player_bottom_tools_and_fixed_marker_centers(ui,tmp_path):
    app,bridge,window,timeline=ui
    assert bridge.command('marker.add',{'frame':1})
    for width,height in ((1400,940),(1920,1080),(760,600)):
        window.resize(width,height);delay(200)
        panel=find(window,'mainPreviewPanel');controls=find(window,'playerControls')
        bottom=controls.mapToItem(panel,QPointF(0,controls.height())).y()
        # Controls hug the picture, not the bottom of an oversized shared panel.
        # Spare height remains above the image; compare actual scene coordinates.
        assert bottom<=panel.height()+.1
        stage=find(window,'videoStage');video=find(window,'videoPreview');rect=video.property('contentRect')
        assert 0 <= controls.mapToScene(QPointF()).y()-video.mapToScene(QPointF(rect.x(),rect.bottom())).y()<12
        if width>=1040:
            review=find(window,'reviewPanel')
            assert abs(review.mapToScene(QPointF(0,review.height())).y()-panel.mapToScene(QPointF(0,panel.height())).y())<.1
        row=find(window,'playerToolRow');children=[c for c in row.childItems() if c.isVisible()]
        for c in children:
            assert c.x()>=0 and c.x()+c.width()<=row.width()+.1
            assert c.y()>=0 and c.y()+c.height()<=row.height()+.1
        if row.width()>600: assert len({c.y() for c in children})==1
        marker=find(window,'fixedMarker_1')
        for suffix in ('GuideTop','GuideBottom'):
            guide=find(window,'fixedMarker_1'+suffix)
            assert guide.x()+guide.width()/2==marker.width()/2
        window.grabWindow().save(str(evidence(tmp_path)/f'controls-{width}.png'))


def prepared_subtitle(ui):
    project=install_cues(ui,resolution=(640,360),entries=[(0.,2.,'字幕拖动测试')])
    project.settings['font']='Microsoft YaHei UI'
    ui.bridge._changed();wait_until(lambda:ui.bridge._layout_revision==ui.bridge.document.revision)
    present(ui,1);delay(80)
    return project,pfind(ui,'previewSubtitle_0')


def modified_move(ui,end,shift=True):
    import time
    event=QMouseEvent(QEvent.Type.MouseMove,QPointF(end),QPointF(ui.window.mapToGlobal(end)),Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier)
    event.setTimestamp(int(time.monotonic()*1000));ui.app.sendEvent(ui.window,event);delay(20)


@pytest.mark.parametrize('offset,axis',[(QPoint(80,20),'horizontal'),(QPoint(15,-70),'vertical')])
def test_subtitle_shift_locks_axis_without_size_change(preview_ui,offset,axis):
    ui=preview_ui;project,label=prepared_subtitle(ui);start=point(label)
    font=project.settings['font_size'];before=ui.bridge.document.snapshot()
    QTest.mousePress(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ShiftModifier,start)
    modified_move(ui,start+offset)
    if axis=='horizontal': assert ui.panel.property('subtitleDragY')==0 and ui.panel.property('subtitleDragX')>60
    else: assert ui.panel.property('subtitleDragX')==0 and ui.panel.property('subtitleDragY')<-50
    assert ui.bridge.document.snapshot()==before
    y=label.y();x=label.x()
    QTest.mouseRelease(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ShiftModifier,start+offset)
    ui.bridge.layout_timer.stop()
    # The accepted position remains painted while the async layout is pending.
    assert ui.panel.property('subtitleCommitPending') and pfind(ui,'previewSubtitle_0') is not None
    assert abs(pfind(ui,'previewSubtitle_0').y()-y)<.01 and abs(pfind(ui,'previewSubtitle_0').x()-x)<.01
    ui.bridge.layout_timer.start();wait_until(lambda:ui.bridge._layout_revision==ui.bridge.document.revision)
    delay(30);final=pfind(ui,'previewSubtitle_0')
    assert abs(final.y()-y)<1.5 and abs(final.x()-x)<1.5
    assert project.settings['font_size']==font
    assert not ui.panel.property('subtitleCommitPending')


def test_subtitle_visual_center_snaps_without_rebound(preview_ui,tmp_path):
    ui=preview_ui;project,label=prepared_subtitle(ui)
    video=pfind(ui,'videoPreview');rect=video.property('contentRect')
    start=point(label);target=video.mapToScene(QPointF(rect.x()+rect.width()/2+5,rect.y()+rect.height()/2+4)).toPoint()
    QTest.mousePress(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start)
    QTest.mouseMove(ui.window,target,30);delay(20)
    assert ui.panel.property('subtitleSnapX') and ui.panel.property('subtitleSnapY')
    assert abs(label.y()+label.height()/2-(rect.y()+rect.height()/2))<.05
    ui.window.grabWindow().save(str(evidence(tmp_path)/'subtitle-center-snap.png'))
    y=label.y();font=project.settings['font_size']
    QTest.mouseRelease(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,target)
    samples=[]
    for _ in range(30):
        item=pfind(ui,'previewSubtitle_0');samples.append(None if item is None else item.y());delay(10)
    assert all(v is not None and abs(v-y)<1.5 for v in samples),samples
    assert project.settings['font_size']==font
    assert not pfind(ui,'subtitleCenterGuideY').isVisible()
    (evidence(tmp_path)/'subtitle-no-rebound.json').write_text(json.dumps(samples),encoding='utf8')


def test_extended_workspace_preferences_persist(ui):
    app,bridge,window,timeline=ui
    desired={'previewVisible':True,'reviewVisible':True,'timelineVisible':True,'previewFraction':.1,'topFraction':.9,'previewHeight':320.,'audioHeight':180.,'audioHeightExplicit':True,'audioMode':'spectrum','controlsHeight':60.,'audioVisible':False,'spectrumPreset':'contrast','spectrumWindow':1024,'spectrumGain':4.5,'spectrumBlack':0.,'spectrumTilt':0.,'spectrumFrequency':10000,'spectrumAxis':'legacy'}
    before=bridge.document.snapshot()
    assert bridge.command('view.layout',desired)
    assert bridge.state['workspaceLayout']==desired and bridge.document.snapshot()==before
    assert json.loads((bridge.storage_dir/'workspace-layout.json').read_text(encoding='utf-8'))==desired
    from desktop_qt.controller import Bridge
    other=None
    try:
        other=Bridge(storage_dir=bridge.storage_dir)
        assert other.state['workspaceLayout']==desired
    finally:
        if other is not None:other.shutdown();other.wait_for_shutdown(app)


def test_legacy_workspace_preferences_add_audio_defaults_without_editing_project(application,tmp_path):
    from desktop_qt.controller import Bridge
    storage=tmp_path/'legacy-layout';storage.mkdir()
    legacy={'previewFraction':.1,'topFraction':.9,'previewHeight':320.,'audioHeight':180.,'audioMode':'spectrum','controlsHeight':60.}
    path=storage/'workspace-layout.json';path.write_text(json.dumps(legacy),encoding='utf-8')
    expected={**legacy,'previewVisible':True,'reviewVisible':True,'timelineVisible':True,'audioVisible':True,'spectrumPreset':'detail','spectrumWindow':384,'spectrumGain':0.}
    expected.update(spectrumBlack=0.,spectrumTilt=3.,spectrumFrequency=10000,spectrumAxis='balanced',audioHeightExplicit=True)
    bridge=None
    try:
        bridge=Bridge(storage_dir=storage)
        assert bridge.state['workspaceLayout']==expected
        before=bridge.document.snapshot()
        # A subsequent preference save persists the complete migrated schema.
        assert bridge.command('view.layout',{'spectrumGain':-3.5,'audioVisible':False})
        expected.update(spectrumGain=-3.5,audioVisible=False)
        assert bridge.state['workspaceLayout']==expected
        assert json.loads(path.read_text(encoding='utf-8'))==expected
        assert bridge.document.snapshot()==before
    finally:
        if bridge is not None:bridge.shutdown();bridge.wait_for_shutdown(application)
    restored=None
    try:
        restored=Bridge(storage_dir=storage)
        assert restored.state['workspaceLayout']==expected
    finally:
        if restored is not None:restored.shutdown();restored.wait_for_shutdown(application)


def test_identical_next_cue_cancels_drag_by_identity(preview_ui):
    ui=preview_ui
    install_cues(ui,resolution=(640,360),entries=[(0.,.5,'重复歌词'),(.5,1.,'重复歌词')])
    present(ui,1);delay(50)
    label=pfind(ui,'previewSubtitle_0');start=point(label)
    before=ui.bridge.document.snapshot()
    QTest.mousePress(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start)
    QTest.mouseMove(ui.window,start+QPoint(45,-20),20)
    assert ui.panel.property('subtitleDragging')
    present(ui,8);delay(20)
    assert not ui.panel.property('subtitleDragging')
    QTest.mouseRelease(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start+QPoint(45,-20))
    assert ui.bridge.document.snapshot()==before


def test_candidate_controls_stay_in_review_without_reducing_player(ui,tmp_path,monkeypatch):
    from test_qt_timeline_input import click
    app,bridge,window,timeline=ui
    before=bridge.document.snapshot()
    records=[]
    for width,height,compact in ((1400,940,False),(1400,940,True),(1920,1080,False),(1920,1080,True),(760,600,False)):
        window.resize(width,height);delay(180)
        if width>760:
            handle=find(window,'mainSplitHandle');start=point(handle)
            drag(window,start,QPoint(width//2,start.y()))
        player=find(window,'mainPreviewPanel')
        player_before_drag=player.width()
        if compact:
            handle=find(window,'mainSplitHandle');start=point(handle)
            drag(window,start,QPoint(width-390,start.y()))
        if compact:assert player.width()>=player_before_drag, 'Wrapping review controls must not shrink the player'
        player_width=player.width()
        panel=find(window,'reviewPanel');banner=find(window,'candidatePreviewBanner')
        assert banner.parentItem().parentItem()==panel
        compact = find(window,'candidateActionRow').width() < 560
        assert banner.property('compactActions') is compact
        assert not find(window,'alignmentOriginalButton').isVisible()
        assert not find(window,'alignmentCandidateButton').isVisible()
        assert not banner.isVisible()
        row=find(window,'candidateActionRow')
        children=[c for c in row.childItems() if c.isVisible()]
        header=find(window,'reviewHeaderRow');heading=find(window,'reviewHeading')
        assert row.parentItem()==header
        assert not row.isVisible()  # F47 must also remove its layout allocation.
        # Flow may wrap at the minimum review width. Validate every 2D box,
        # including all visible header siblings, rather than requiring one row.
        def box(item,parent):
            pos=item.mapToItem(parent,QPointF(0,0))
            return (pos.x(),pos.y(),pos.x()+item.width(),pos.y()+item.height())
        def contained_nonoverlapping(items,parent):
            bounds=[box(item,parent) for item in items]
            for item,(left,top,right,bottom) in zip(items,bounds):
                assert item.width()>0 and item.height()>0
                assert left>=-.1 and top>=-.1 and right<=parent.width()+.1 and bottom<=parent.height()+.1, (item.objectName(),bounds,parent.width(),parent.height())
            for i,a in enumerate(bounds):
                for b in bounds[i+1:]:
                    assert min(a[2],b[2])-max(a[0],b[0])<=.1 or min(a[3],b[3])-max(a[1],b[1])<=.1, bounds
        contained_nonoverlapping(children,row)
        # The deliberate fill-width spacer is a zero-height, nonpainted Item.
        contained_nonoverlapping([c for c in header.childItems() if c.isVisible() and (c.objectName() or c.height()>0)],header)
        assert header.mapToScene(QPointF(0,header.height())).y()<=find(window,'reviewList').mapToScene(QPointF()).y()+.1
        multi=find(window,'multiSelectMode')
        more=find(window,'reviewOperationsButton')
        assert multi is None  # v0.22: Ctrl/Shift selection, no mode button
        assert more.parentItem()==header and abs(more.x()+more.width()-header.width())<1
        assert all(c.isEnabled() for c in children if c.objectName() in ('alignmentOriginalButton','alignmentMoreButton'))
        # Menu/open-close and repeated layout settlement cannot steal player width.
        delay(80)
        assert abs(player.width()-player_width)<.1
        status=find(window,'candidateSourceStatus')
        assert not bridge.state['timelineSwitchAvailable']
        assert status is None
        assert find(window,'candidateSourceBar') is None
        review_list=find(window,'reviewList')
        if width==760:
            selected_row=find(window,'cueRow_'+bridge.selected)
            assert review_list.height()>=2*selected_row.height()+review_list.property('spacing')
        else:assert review_list.height()>=108
        # v0.23 fits the four panels directly in the window. Only each panel
        # scrolls internally; headers and timeline controls need no outer reveal.
        workspace=find(window,'makeScroll')
        assert workspace.property('contentItem') is None
        assert header.mapToScene(QPointF()).y()>=0
        assert header.mapToScene(QPointF(0,header.height())).y()<window.height()
        assert timeline.mapToScene(QPointF(0,timeline.height())).y()<=window.height()
        if width==760:
            assert window.grabWindow().save(str(evidence(tmp_path)/'candidate-controls-760-top.png'))
        if compact:
            click(window,find(window,'reviewOperationsButton'))
            menu=window.findChild(QObject,'reviewOperationsMenu')
            assert menu is not None and menu.property('visible'), (width,height,compact,point(find(window,'reviewOperationsButton')))
            QTest.keyClick(window,Qt.Key.Key_Escape);wait_until(lambda:not menu.property('visible'))
        assert abs(player.width()-player_width)<.1
        records.append(dict(window=[width,height],compact=compact,actionRows=len({c.y() for c in children}),playerBeforeDrag=player_before_drag,playerWidth=player.width(),actionBounds=[box(c,row) for c in children],headerBounds=[box(c,header) for c in header.childItems() if c.isVisible()],bannerHeight=banner.height(),reviewMinimum=panel.property('minimumPanelHeight'),reviewList=find(window,'reviewList').height(),pictureHeight=find(window,'videoPreview').property('contentRect').height()))
        window.grabWindow().save(str(evidence(tmp_path)/f'candidate-controls-{width}-{compact}.png'))
    assert bridge.document.snapshot()==before
    (evidence(tmp_path)/'candidate-controls.json').write_text(json.dumps(records,indent=2),encoding='utf8')


@pytest.mark.parametrize('size',[(1400,940),(760,600)])
def test_pending_split_borrows_review_space_and_keeps_usable_tracks(ui,tmp_path,size):
    from test_qt_razor_input import cut_at
    from test_qt_timeline_input import click
    from qt_visual_assertions import assert_painted_editor_text
    app,bridge,window,timeline=ui
    window.resize(*size);delay(250)
    workspace=find(window,'makeScroll')
    assert workspace.property('contentItem') is None
    review=find(window,'reviewPanel');tracks=find(window,'timelineTracks')
    def geometry():
        return dict(timelineHeight=timeline.height(),timelineY=timeline.y(),
            tracksHeight=tracks.height(),reviewMinimum=review.property('minimumPanelHeight'),
            reviewListHeight=find(window,'reviewList').height(),
            headerHeight=find(window,'timelineHeader').height(),
            pictureHeight=find(window,'videoPreview').property('contentRect').height())
    before=geometry();samples=[];header_pixels=[];returned_pixels=[]
    click(window,find(window,'timelineRazorTool'))
    cut_at(window,timeline,'r40',424,wait_ms=0)
    for index in range(12):
        samples.append(geometry());delay(15)
        if index in (0,11):
            shot=window.grabWindow()
            # The blank white title strip is outside the workspace. Nested
            # off-viewport review delegates must never paint over it.
            color=shot.pixelColor(round(shot.width()/2),round(10*shot.height()/window.height()))
            header_pixels.append([color.red(),color.green(),color.blue()])
            assert min(header_pixels[-1])>=250,header_pixels
    left,right=bridge.document.project.mappings[40:42]
    cut_at(window,timeline,right.id,427,wait_ms=0)
    for index in range(12):
        samples.append(geometry());delay(15)
        if index in (0,11):
            shot=window.grabWindow()
            color=shot.pixelColor(round(shot.width()/2),round(10*shot.height()/window.height()))
            header_pixels.append([color.red(),color.green(),color.blue()])
            assert min(header_pixels[-1])>=250,header_pixels
    assert find(window,'reviewPendingText').isVisible()
    assert find(window,'reviewAllocateSplitText').isEnabled()
    assert find(window,'reviewKeepSplitText').isEnabled()
    review_list=find(window,'reviewList')
    selected_row=find(window,'cueRow_'+bridge.selected)
    assert review_list.height()>=(selected_row.height() if size[0]==760 else 63)
    assert tracks.height()>=(1 if size[0]==760 else 3)*timeline.property('rowHeight')
    assert all(g['reviewMinimum']==before['reviewMinimum'] for g in samples),(before,samples)
    assert all(g['timelineHeight']==before['timelineHeight'] and g['timelineY']==before['timelineY'] for g in samples),(before,samples)
    assert all(g['pictureHeight']==before['pictureHeight'] for g in samples)
    out=evidence(tmp_path)
    window.grabWindow().save(str(out/f'pending-tracks-{size[0]}.png'))
    if size[0]==760:
        pending=find(window,'reviewPendingText')
        assert 0<=pending.mapToScene(QPointF()).y()<window.height()
        document=bridge.document.snapshot()
        assert bridge.command('row.select',{'id':left.id})
        delay(20);wait_until(lambda:not review.property('autoScrollRunning'))
        for _ in range(2):
            delay(80)  # fixed workspace, no outer scroll manipulation
            returned=find(window,'cueRow_'+left.id);review_list=find(window,'reviewList')
            assert returned.isVisible()
            returned_y=returned.mapToItem(review_list,QPointF(0,0)).y()
            assert returned_y>=-.1 and returned_y+returned.height()<=review_list.height()+.1,(returned_y,returned.height(),review_list.height())
            assert find(window,'chineseEditor_'+left.id).property('text')=='真实中文第41句'
            returned_pixels.append(assert_painted_editor_text(window,find(window,'chineseEditor_'+left.id)))
            window.grabWindow().save(str(out/'pending-review-760.png'))
            assert timeline.mapToScene(QPointF(0,timeline.height())).y()<=window.height();delay(80)
            shot=window.grabWindow()
            color=shot.pixelColor(round(shot.width()/2),round(10*shot.height()/window.height()))
            assert min(color.red(),color.green(),color.blue())>=250
        assert bridge.document.snapshot()==document
        window.grabWindow().save(str(out/'pending-tracks-return-760.png'))
    (out/f'pending-layout-{size[0]}.json').write_text(json.dumps(dict(before=before,samples=samples,headerPixels=header_pixels,returnedPixels=returned_pixels),indent=2),encoding='utf8')


def test_review_internal_scroll_clips_partial_rows_without_ending_edit_or_preedit(ui,tmp_path):
    from PySide6.QtGui import QInputMethodEvent
    from qt_visual_assertions import assert_painted_editor_text
    app,bridge,window,timeline=ui
    window.resize(760,600);delay(250)
    assert find(window,'makeScroll').property('contentItem') is None
    outer=find(window,'reviewList')
    review=find(window,'reviewPanel');row=find(window,'cueRow_r40')
    wait_until(lambda:not review.property('autoScrollRunning'))
    from test_qt_timeline_input import wheel
    wheel(app,window,outer,-120)
    wait_until(lambda:not review.property('followSelection'))
    content=outer.property('contentItem')
    def move_to(relative_y):
        target=row.mapToItem(content,QPointF(0,0)).y()-relative_y
        assert 0<=target<=outer.property('contentHeight')-outer.height(),target
        outer.setProperty('contentY',target);delay(25)
        actual=row.mapToItem(outer,QPointF(0,0)).y()
        assert abs(actual-relative_y)<.1,(actual,relative_y)
    move_to(30)
    QTest.keyClick(window,Qt.Key.Key_F2);delay(30)
    editor=find(window,'chineseEditor_r40')
    assert editor.hasActiveFocus() and row.property('editing')
    QTest.keyClick(window,Qt.Key.Key_End)
    QTest.keyClick(window,Qt.Key.Key_X);delay(30)
    assert editor.property('text').endswith('x')
    app.sendEvent(editor,QInputMethodEvent('组合中',[]));delay(30)
    assert editor.property('inputMethodComposing')
    before=bridge.document.snapshot();history=len(bridge.history.undo_stack)
    text=editor.property('text');cursor=editor.property('cursorPosition');height=row.height()
    origin=outer.mapToScene(QPointF(0,0))
    top=origin.y();bottom=top+outer.height()
    left=origin.x();right=left+outer.width()
    # These strips are outside the internal review viewport, but within an unclipped
    # partially crossing delegate. Compare exact pixels, not object existence.
    def guard_pixels():
        image=window.grabWindow();scale=image.width()/window.width()
        return [image.pixelColor(round(x*scale),round(y*scale)).rgba()
                for y in (top-5,top-2,bottom+2,bottom+5)
                for x in range(round(left+8),round(right-8),15)]
    baseline=guard_pixels();records=[];out=evidence(tmp_path)
    for cycle in range(2):
        for label,y,opacity in [('top-half',-height/2,1),('above',-height-12,0),
                               ('bottom-half',outer.height()-height/2,1),
                               ('below',outer.height()+12,0),('returned',30,1)]:
            move_to(y)
            assert row.opacity()==opacity,(label,row.opacity())
            assert row.isVisible() and row.height()==height
            assert editor.hasActiveFocus() and row.property('editing')
            assert editor.property('inputMethodComposing')
            assert editor.property('preeditText')=='组合中'
            assert editor.property('text')==text and editor.property('cursorPosition')==cursor
            assert bridge.document.snapshot()==before and len(bridge.history.undo_stack)==history
            assert bridge.selected=='r40'
            assert guard_pixels()==baseline,label
            pixels=assert_painted_editor_text(window,editor) if label=='returned' else None
            records.append(dict(cycle=cycle,position=label,rowY=y,opacity=opacity,returnedPixels=pixels))
            window.grabWindow().save(str(out/f'partial-row-{cycle}-{label}.png'))
    app.sendEvent(editor,QInputMethodEvent());delay(25)
    assert not editor.property('inputMethodComposing')
    assert editor.hasActiveFocus() and row.property('editing')
    assert bridge.document.snapshot()==before and len(bridge.history.undo_stack)==history
    assert_painted_editor_text(window,editor)
    (out/'partial-row-editing.json').write_text(json.dumps(records,indent=2),encoding='utf8')
