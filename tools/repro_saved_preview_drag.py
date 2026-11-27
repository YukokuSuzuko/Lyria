"""Local-only saved-project preview probe. Explicit paths; no credential/API access."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['QT_QPA_PLATFORM']='offscreen'
os.environ['QT_QUICK_BACKEND']='software'
from PySide6.QtCore import QPoint, QPointF, QUrl, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from tools.qt_event_loop_wait import delay, wait_until
from lyricsync import ai_config, credentials

blocked=[]
def deny(*args,**kwargs):
    blocked.append('unexpected external/credential access')
    raise RuntimeError('Offline preview probe forbids network and credentials')
socket.socket.connect=deny
socket.socket.connect_ex=deny
socket.create_connection=deny
credentials.KeyStore.__init__=deny
ai_config.load=lambda home:dict(ai_config.DEFAULTS)
from desktop_qt.controller import Bridge


def walk(item):
    yield item
    for child in item.childItems():yield from walk(child)


def run(source,qml,out):
    out.mkdir(parents=True,exist_ok=False)
    raw=source.read_bytes();source_hash=hashlib.sha256(raw).hexdigest()
    (out/'original-project.lyria').write_bytes(raw)
    data=json.loads(raw);settings=data['project']['settings']
    settings.update(api_enabled=False,api_auto_review=False,api_max_calls=0,api_retries=0)
    isolated=out/'offline-project.lyria';isolated.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    video=Path(data['project']['inputs']['video'])
    assert video.is_file(), 'Referenced original source video is missing'
    report=dict(sourceProjectSha256=source_hash,sourceVideo=str(video),qml=str(qml),cases=[],blockedAccess=blocked)
    QQuickStyle.setStyle('Basic');app=QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    bridge=Bridge(storage_dir=out/'storage');bridge.media_controller.mute(True)
    bridge.load_api_credential=deny
    engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('bridge',bridge)
    engine.loadData(b"""import QtQuick
import QtQuick.Controls
import "."
ApplicationWindow {
    visible: true; width: 1000; height: 760
    PreviewPanel { objectName: "probePanel"; anchors.fill: parent
        controller: bridge; media: bridge.media; state: bridge.uiState; review: true }
}""",QUrl.fromLocalFile(str(qml/'OfflineSavedProjectHarness.qml')))
    assert engine.rootObjects()
    window=engine.rootObjects()[0]
    def find(name):return next((i for i in walk(window.contentItem()) if i.objectName()==name),None)
    panel=find('probePanel')
    def rows():return [i for i in walk(window.contentItem()) if i.objectName().startswith('previewSubtitle_')]
    def geom():return [(r.x(),r.y()) for r in rows()]
    def point(item):return item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint()
    def move(start,end):
        count=max(abs(end.x()-start.x()),abs(end.y()-start.y()))
        for n in range(1,count+1):
            QTest.mouseMove(window,start+(end-start)*(n/count),1);delay(2)
    def settled():
        wait_until(lambda:bridge._layout_revision==bridge.document.revision,timeout_ms=30000);delay(50)
    def seek(stamp):
        frame=bridge.media_controller.index.first(stamp)
        assert bridge.command('media.seek',{'frame':frame})
        wait_until(lambda:bridge.media['displayedFrame']==frame,timeout_ms=30000)
        settled();return frame
    try:
        assert bridge.command('project.open',{'path':str(isolated)})
        wait_until(lambda:bridge.media['ready'] and bridge.media['displayedFrame']>0,timeout_ms=90000)
        settled()
        report['initialStyle']={k:v for k,v in bridge.document.project.settings.items() if k.startswith('subtitle_')}
        mappings=bridge.document.project.mappings
        spans=[m for m in mappings if m.start is not None and m.end is not None and not m.ignored]
        overlap=max(((sum(m.start<=a.start+.05<m.end for m in spans),a.start+.05) for a in spans),default=(0,0))
        report['maxOverlap']={'count':overlap[0],'stamp':overlap[1]}
        times=[(spans[0].start+min(1.,(spans[0].end-spans[0].start)/2),'first'),(spans[1].start+.5,'second')]
        if overlap[0]>1:times.append((overlap[1],'overlap'))
        for stamp,name in times:
            frame=seek(stamp);assert rows()
            case=dict(name=name,frame=frame,stamp=bridge.media['positionSeconds'],cueIds=bridge.media['activeCueIds'],rowCount=len(rows()))
            bridge.media_controller._last_image.save(str(out/(name+'-raw-source.png')))
            window.grabWindow().save(str(out/(name+'-before.png')))
            before=bridge.document.snapshot();start=point(rows()[0]);video_item=find('videoPreview');rect=video_item.property('contentRect')
            top=min(r.y() for r in rows());bottom=max(r.y()+r.height() for r in rows())
            target=start+QPoint(round(rect.center().x()-(rows()[0].x()+rows()[0].width()/2)),round(rect.center().y()-(top+bottom)/2))
            QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,start);move(start,target);delay(120)
            case['snapped']=[panel.property('subtitleSnapX'),panel.property('subtitleSnapY')]
            case['dragging']=panel.property('subtitleDragging');case['beforeRelease']=geom()
            case['documentUnchangedWhileDragging']=bridge.document.snapshot()==before
            QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,target)
            bridge.layout_timer.stop();delay(150)
            case['heldPending']=dict(pending=panel.property('subtitleCommitPending'),rows=geom())
            bridge.layout_timer.start();samples=[]
            for _ in range(45):samples.append(geom());delay(10)
            settled();case['releaseSamples']=samples
            start=point(rows()[0]);QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,start)
            escape=[]
            for n in range(1,41):
                QTest.mouseMove(window,start+QPoint(n,-n),2);delay(4)
                escape.append([panel.property('subtitleDragX'),panel.property('subtitleDragY')])
                if n in (8,20,40):delay(90)
            case['escapeOffsets']=escape;case['escapedPosition']=geom()
            QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,start+QPoint(40,-40));settled()
            case['afterEscapeRelease']=geom();window.grabWindow().save(str(out/(name+'-after-drag.png')))
            # Real playback refreshes real source frames while retaining committed placement.
            play_start=bridge.media['displayedFrame'];bridge.media_controller.play();delay(350);bridge.media_controller.pause();delay(40)
            case['playbackFrames']=[play_start,bridge.media['displayedFrame']];case['afterPlayback']=geom()
            case['afterPlaybackCueIds']=bridge.media['activeCueIds']
            report['cases'].append(case)
            (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        assert not blocked
    finally:
        bridge.shutdown();window.close();engine.deleteLater();delay(100)
        report['originalUnchanged']=hashlib.sha256(source.read_bytes()).hexdigest()==source_hash
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(out),cases=[dict(name=c['name'],rows=c['rowCount'],snapped=c['snapped'],escape=c['escapeOffsets'][-1],playback=c['playbackFrames']) for c in report['cases']],originalUnchanged=report['originalUnchanged'],blockedAccess=blocked),ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--project',type=Path,required=True);parser.add_argument('--qml',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();run(args.project,args.qml,args.out)
