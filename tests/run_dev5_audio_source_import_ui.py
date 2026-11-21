"""Actual QML original-import button and real audio decode; no model inference."""
from pathlib import Path
import os,sys,time,json,wave,hashlib
from copy import deepcopy
from unittest.mock import patch
os.environ['QT_QPA_PLATFORM']='offscreen';os.environ['QT_QUICK_BACKEND']='software'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from PySide6.QtCore import QUrl,QPointF,Qt
from PySide6.QtGui import QFontDatabase,QFont
from PySide6.QtWidgets import QApplication,QFileDialog
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from desktop_qt.controller import Bridge
from lyricsync.local_alignment import request_rows
from lyricsync.editing import mapping_text


def main(folder):
    out=Path(folder).resolve();out.mkdir(parents=True,exist_ok=False)
    source=out/'original.txt';source.write_text('same chorus\nsame chorus\nlast original',encoding='utf-8')
    audio=out/'controlled-silence.wav'
    with wave.open(str(audio),'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000);stream.writeframes(b'\0\0'*64000)
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (source,audio)}
    QQuickStyle.setStyle('Basic');app=QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc');app.setFont(QFont('Microsoft YaHei',10))
    b=Bridge(storage_dir=out/'storage');b.media_controller.audio.setMuted(True)
    engine=QQmlApplicationEngine();warnings=[]
    engine.warnings.connect(lambda ws:warnings.extend(w.toString() for w in ws))
    engine.rootContext().setContextProperty('bridge',b);engine.load(QUrl.fromLocalFile(str(ROOT/'desktop_qt/qml/Main.qml')))
    assert engine.rootObjects();w=engine.rootObjects()[0];w.setWidth(1600);w.setHeight(1020)
    def wait(predicate):
        until=time.monotonic()+30
        while not predicate() and time.monotonic()<until:app.processEvents();time.sleep(.005)
        assert predicate(),b.state.get('error')
    def walk(item):
        yield item
        for child in item.childItems():yield from walk(child)
    def find(name):return next(x for x in walk(w.contentItem()) if x.objectName()==name)
    def button(text):
        return next(x for x in walk(w.contentItem()) if x.isVisible() and x.metaObject().indexOfProperty('down')>=0 and x.property('text')==text)
    def click(item):
        assert item.isVisible() and item.isEnabled()
        point=item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint()
        QTest.mouseClick(w,Qt.LeftButton,Qt.NoModifier,point);app.processEvents()
    try:
        with patch('lyricsync.local_alignment.generate_vocal_candidate',side_effect=AssertionError('import cannot infer')) as model:
            assert b.command('project.newEntry',{'mode':'audio'})
            QTest.qWait(500)
            with patch.object(QFileDialog,'getOpenFileName',return_value=(str(audio),'WAV')):
                click(button('选择媒体'))
            print('media selection:',b.document.project.inputs,flush=True)
            wait(lambda:b.media['ready'] and b.media['mediaKind']=='audio')
            # Existing media-loaded behavior opens review. Use the real stage
            # navigation to return to the original-import card, not a bridge stub.
            click(find('stageButton_import'));QTest.qWait(300)
            before=deepcopy(b.document.project.to_dict());depth=len(b.history.undo_stack)
            with patch.object(QFileDialog,'getOpenFileName',return_value=(str(source),'TXT')):
                click(button('导入演唱原文'))
            wait(lambda:not b.jobs.active and not b._handlers)
            assert len(b.document.main_timeline)==3 and not b.document.project.translations
            assert all(m.text_role=='source_import' and m.start is None and m.end is None and not m.translation_ids for m in b.document.main_timeline)
            assert len(b.history.undo_stack)==depth+1
            requested=request_rows(b.document);assert requested==request_rows(b.document,text_source='lyrics')
            assert requested[0]['id']!=requested[1]['id'] and requested[0]['text']==requested[1]['text']
            imported=deepcopy(b.document.project.to_dict())
            QTest.qWait(500);w.grabWindow().save(str(out/'01-actual-audio-original-import.png'))
            assert b.command('stage.set',{'stage':'review'});QTest.qWait(500)
            first=b.document.main_timeline[0]
            assert find('chineseEditor_'+first.id).property('text')=='same chorus'
            assert find('mainTextRole_'+first.id) is None  # dev7 removes the permanent role caption
            w.grabWindow().save(str(out/'02-untimed-original-main-seed.png'))
            assert b.command('history.undo',{}) and b.document.project.to_dict()==before
            assert b.command('history.redo',{}) and b.document.project.to_dict()==imported
            saved=out/'source-seed.lyria';assert b.command('project.saveAs',{'path':str(saved)})
            wait(lambda:b._save_running is None and b.document.saved_revision==b.document.revision)
            reopened=Bridge(storage_dir=out/'reopened-storage')
            try:
                assert reopened.command('project.open',{'path':str(saved)})
                wait(lambda:reopened.media['ready'])
                assert reopened.document.project.to_dict()==imported
                assert request_rows(reopened.document)==requested
            finally:reopened.shutdown();reopened.wait_for_shutdown(app)
            model.assert_not_called()
        assert hashes=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (source,audio)}
        assert not warnings,warnings
        report={'scope':'actual Main.qml import button with only QFileDialog path stub, real WAV decode, no model or API; offscreen not native Windows acceptance',
                'requested_import_role':'source','media_kind':b.media['mediaKind'],'main_seed':imported['mappings'],
                'original_lyrics':imported['lyrics'],'translations':imported['translations'],
                'local_4a_request_rows_main_and_lyrics':requested,'input_unchanged':True,
                'untimed_preserved':True,'duplicate_text_distinct_ids':True,'single_import_undo_redo':True,
                'save_fresh_bridge_reload':True,'model_not_called':True,'qml_warnings':warnings}
        (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:
        w.hide();engine.deleteLater();app.processEvents();b.shutdown();b.wait_for_shutdown(app)


if __name__=='__main__':main(sys.argv[1])
