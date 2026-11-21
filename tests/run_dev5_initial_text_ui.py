"""Real QML rendering/edit/export of a real OCR initial-result fixture.

Offscreen Qt keyboard events are not foreground Windows/user acceptance.
"""
from pathlib import Path
import os,sys,time,json,hashlib
os.environ['QT_QPA_PLATFORM']='offscreen';os.environ['QT_QUICK_BACKEND']='software'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from PySide6.QtCore import QUrl,Qt
from PySide6.QtGui import QFontDatabase,QFont
from PySide6.QtWidgets import QApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from desktop_qt.controller import Bridge
from lyricsync.editing import mapping_text


def main(source,out):
    source=Path(source).resolve();out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
    original_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    QQuickStyle.setStyle('Basic');app=QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc');app.setFont(QFont('Microsoft YaHei',10))
    b=Bridge(storage_dir=out/'storage');engine=QQmlApplicationEngine();warnings=[]
    engine.warnings.connect(lambda ws:warnings.extend(w.toString() for w in ws))
    engine.rootContext().setContextProperty('bridge',b)
    engine.load(QUrl.fromLocalFile(str(ROOT/'desktop_qt/qml/Main.qml')))
    assert engine.rootObjects();window=engine.rootObjects()[0];window.setWidth(1600);window.setHeight(1020)
    def wait(predicate):
        until=time.monotonic()+30
        while not predicate() and time.monotonic()<until:app.processEvents();time.sleep(.005)
        assert predicate(),b.state.get('error')
    def find(item,name):
        if item.objectName()==name:return item
        for child in item.childItems():
            result=find(child,name)
            if result:return result
    def item(name):
        result=find(window.contentItem(),name);assert result is not None,name;return result
    def evidence():
        return {key:b.document.project.to_dict()[key] for key in ('translations','lyrics','visuals')}
    try:
        assert b.command('project.open',{'path':str(source)})
        wait(lambda:b.media['ready'] and b.media['displayedFrame']==1 and b.document.main_timeline)
        QTest.qWait(1000)
        row=b.document.main_timeline[0];identity=row.id
        editor=item('chineseEditor_'+identity)
        initial=b.document.project.to_dict();original_evidence=evidence()
        assert editor.property('text')=='HELLO WORLD'
        assert row.text_role=='source_ocr' and not row.confirmed
        assert item('timelineText_'+identity).property('text')=='原文 · HELLO WORLD'
        assert b.media['subtitleText']=='HELLO WORLD'
        assert 'OCR 原文' in b.media['subtitleTextRoleLabel']
        assert 'OCR 原文' in item('previewHeading').property('text')
        assert item('mainTextRole_'+identity) is None  # dev7 preserves state without the permanent caption
        assert item('reviewSourceEvidence').property('text')=='HELLO WORLD'
        assert item('reviewOcrEvidence').property('text')=='HELLO WORLD'
        assert window.grabWindow().save(str(out/'01-initial-original-main-timeline-preview.png'))
        QTest.keyClick(window,Qt.Key_F2);app.processEvents()
        assert not editor.property('readOnly') and editor.hasActiveFocus()
        QTest.keyClick(window,Qt.Key_A,Qt.ControlModifier)
        for char in 'hello edited':
            QTest.keyClick(window,Qt.Key_Space if char==' ' else getattr(Qt,'Key_'+char.upper()),
                           Qt.NoModifier)
        app.processEvents()
        print('keyboard edit state:', repr(editor.property('text')), repr(mapping_text(b.document.project,b.document.main_timeline[0])),flush=True)
        QTest.keyClick(window,Qt.Key_Escape);app.processEvents()
        wait(lambda:mapping_text(b.document.project,b.document.main_timeline[0])=='hello edited')
        edited=b.document.project.to_dict()
        assert b.document.main_timeline[0].text_role=='source_ocr' and evidence()==original_evidence
        assert b.command('history.undo',{}) and b.document.project.to_dict()==initial
        assert b.command('history.redo',{}) and b.document.project.to_dict()==edited
        assert b.media['subtitleText']=='hello edited'
        QTest.qWait(500)
        assert item('timelineText_'+identity).property('text')=='原文 · hello edited'
        assert window.grabWindow().save(str(out/'02-edited-main-original-evidence-unchanged.png'))
        saved=out/'edited-original.lyria'
        assert b.command('project.saveAs',{'path':str(saved)})
        wait(lambda:b._save_running is None and b.document.saved_revision==b.document.revision)
        exports={}
        for fmt in ('ass','srt','lrc'):
            output=out/('edited-original.'+fmt)
            assert b.command('export.ass',{'format':fmt,'path':str(output),'draft':True})
            wait(lambda:not b.jobs.active and not b._handlers)
            assert output.is_file(),b.state.get('error')
            text=output.read_text(encoding='utf-8-sig');assert 'hello edited' in text
            exports[fmt]=True
        # A fresh bridge reopens the real saved file; no in-memory substitution.
        reopened=Bridge(storage_dir=out/'reopen-storage')
        try:
            assert reopened.command('project.open',{'path':str(saved)})
            wait(lambda:reopened.media['ready'] and reopened.media['displayedFrame']==1)
            assert reopened.document.project.to_dict()==edited
            assert reopened.media['subtitleText']=='hello edited'
        finally:
            reopened.shutdown();reopened.wait_for_shutdown(app)
        assert not warnings,warnings
        assert hashlib.sha256(source.read_bytes()).hexdigest()==original_hash
        report={'scope':'real OCR fixture and actual Main.qml offscreen keyboard events; no foreground Windows/user acceptance',
                'initial_main':'HELLO WORLD','initial_role':'source_ocr','timeline':'原文 · HELLO WORLD',
                'preview':'HELLO WORLD','edited_main':'hello edited','edited_role':'source_ocr',
                'source_ocr_panels_unchanged':'HELLO WORLD','translations_not_changed':True,
                'actual_keyboard_edit':True,'undo_redo':True,'save_fresh_bridge_open':True,
                'exports':exports,'input_fixture_unchanged':True,'qml_warnings':warnings}
        (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:
        window.hide();engine.deleteLater();app.processEvents();b.shutdown();b.wait_for_shutdown(app)


if __name__=='__main__':main(sys.argv[1],sys.argv[2])
