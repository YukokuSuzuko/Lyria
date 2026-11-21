"""Real offline OCR and real Qt widget clicks; generated media only.

Run with an unused evidence directory. This is offscreen functional evidence,
not foreground Windows/DPI or user-material acceptance. No provider calls.
"""
from pathlib import Path
import hashlib
import json
import os
import sys
import time
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM']='offscreen'
os.environ['QT_QUICK_BACKEND']='software'
os.environ['OMP_THREAD_LIMIT']='2'
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]

from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QFontDatabase,QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QPushButton,QMessageBox
from test_qt_import_panel import silent_text_video
from desktop_qt.controller import Bridge
from desktop_qt.candidate_dialog import CandidateDialog
from lyricsync.editing import mapping_text


def main(folder):
    out=Path(folder).resolve();out.mkdir(parents=True,exist_ok=False)
    app=QApplication.instance() or QApplication([])
    font=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/msyh.ttc'
    if font.is_file():QFontDatabase.addApplicationFont(str(font));app.setFont(QFont('Microsoft YaHei',10))
    video=silent_text_video.__wrapped__(out)
    original_hash=hashlib.sha256(video.read_bytes()).hexdigest()
    bridges=[]
    result={'scope':'real RapidOCR, actual offscreen Qt mouse events and confirmation dialog; no foreground Windows acceptance'}
    def create(name):
        b=Bridge(storage_dir=out/name);bridges.append(b);return b
    def wait(test,b,limit=180):
        until=time.monotonic()+limit
        while not test() and time.monotonic()<until:app.processEvents();time.sleep(.005)
        assert test(),b.state.get('error')
    def click(widget):
        assert widget.isEnabled() and widget.isVisible()
        QTest.mouseClick(widget,Qt.LeftButton);app.processEvents()
    def button(dialog,text):return next(x for x in dialog.findChildren(QPushButton) if x.text()==text)
    def media(b):
        assert b.command('media.open',{'path':str(video)})
        wait(lambda:b.media['ready'] and b.media['displayedFrame']==1,b)
        assert b.command('roi.set',{'x':0.,'y':.3,'width':1.,'height':.4})
        b.document.project.settings.update(engine='rapidocr',language='eng',sample_fps=3,api_enabled=False)
    def recognize(b):
        with patch('lyricsync.pipeline.suggest',side_effect=AssertionError('OCR cannot send AI')) as provider:
            assert b.command('ocr.start',{})
            wait(lambda:not b._candidate_job,b)
            provider.assert_not_called()
        assert b.document.source_candidates,b.state
    def confirm(choice):
        modal=app.activeModalWidget()
        if isinstance(modal,QMessageBox):click(modal.button(choice))
        else:QTimer.singleShot(10,lambda:confirm(choice))
    try:
        b=create('manual-storage');media(b);recognize(b)
        record=b.document.source_candidates[-1]
        assert record['rows'] and not b.document.main_timeline
        assert any(v['text']=='HELLO WORLD' for v in record['evidence']['visuals'])
        b.switchCandidateSource('ocr')
        d=CandidateDialog(b,record);d.show();app.processEvents()
        click(button(d,'全选'));click(button(d,'预览所选范围'))
        wait(lambda:d.plan is not None,b,15)
        assert not d.apply.isEnabled() and all(e['blocked'] for e in d.plan['entries'])
        assert d.table.isColumnHidden(8) and 'HELLO WORLD' in d.table.item(0,0).text()
        assert 'None' not in ' '.join(d.table.item(0,c).text() for c in (0,1,2,3,4,5,7))
        d.grab().save(str(out/'01-unassociated-is-explicit.png'))
        click(d.add_preview);wait(lambda:d.plan is not None,b,15)
        assert d.apply.isEnabled() and all(not e['blocked'] for e in d.plan['entries'])
        d.grab().save(str(out/'02-verified-explicit-new-preview.png'))
        before=b.document.project.to_dict();depth=len(b.history.undo_stack)
        QTimer.singleShot(20,lambda:confirm(QMessageBox.No));click(d.apply)
        assert b.document.project.to_dict()==before
        QTimer.singleShot(20,lambda:confirm(QMessageBox.Yes));click(d.apply)
        assert len(b.document.main_timeline)==len(record['rows'])
        assert len(b.history.undo_stack)==depth+1 and b._candidate_source=='main'
        applied=b.document.project.to_dict();d.grab().save(str(out/'03-applied-returned-to-main.png'));d.reject()
        assert b.command('history.undo',{}) and b.document.project.to_dict()==before
        assert b.command('history.redo',{}) and b.document.project.to_dict()==applied
        path=out/'applied.lyria';assert b.command('project.saveAs',{'path':str(path)})
        wait(lambda:b._save_running is None and b.document.saved_revision==b.document.revision,b)
        reopened=create('reopened-storage');assert reopened.command('project.open',{'path':str(path)})
        wait(lambda:reopened.media['ready'] and reopened.media['displayedFrame']==1,reopened)
        assert reopened.document.project.to_dict()==applied
        assert reopened.document.source_candidates==b.document.source_candidates
        assert reopened._candidate_source=='main'
        reopened.switchCandidateSource('ocr');assert reopened.state['candidateReadOnly']
        assert not reopened.command('row.edit',{'id':record['rows'][0]['id'],'text':'must not write'})
        reopened.switchCandidateSource('main')
        target=reopened.document.main_timeline[0].id
        assert reopened.command('row.edit',{'id':target,'text':'人工测试歌词，不是自动翻译'})
        assert reopened.document.main_timeline[0].text_override=='人工测试歌词，不是自动翻译'
        result['manual']={'ocr_rows':len(record['rows']),'recognized_text':[v['text'] for v in record['evidence']['visuals']],
                          'unassociated_default_blocked':True,'explicit_new_preview_and_real_confirmation':True,
                          'cancel_confirmation_no_change':True,'single_undo_redo':True,
                          'save_open_with_real_media_decode':True,'main_candidate_switch_readonly':True,'main_edit_after_apply':True}
        new=create('initial-storage');assert new.command('project.newEntry',{'mode':'ocr'})
        media(new);seed=new.document.project.to_dict();depth=len(new.history.undo_stack)
        recognize(new)
        assert new.document.main_timeline and len(new.history.undo_stack)==depth+1
        assert new._candidate_source=='main'
        assert all(mapping_text(new.document.project,m)=='HELLO WORLD' and m.text_role=='source_ocr'
                   and not m.confirmed for m in new.document.main_timeline)
        assert all(t.text=='' for t in new.document.project.translations)
        initialized=new.document.project.to_dict()
        init_path=out/'initialized.lyria';assert new.command('project.saveAs',{'path':str(init_path)})
        wait(lambda:new._save_running is None,new)
        assert new.command('history.undo',{}) and new.document.project.to_dict()==seed
        assert new.command('history.redo',{}) and new.document.project.to_dict()==initialized
        recognize(new)
        assert new.document.project.to_dict()==initialized,'later OCR overwrote main'
        result['initialization']={'new_entry_initial_main':True,'main_text':[mapping_text(new.document.project,m) for m in new.document.main_timeline],
                                  'text_roles':[m.text_role for m in new.document.main_timeline],
                                  'translation_not_fabricated':True,'one_undo':True,'later_result_only_candidate':True,'saved_path':init_path.name}
        assert hashlib.sha256(video.read_bytes()).hexdigest()==original_hash
        result['source_unchanged']=True;result['api_not_called']=True
        (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:
        for b in bridges:b.shutdown()
        for b in bridges:b.wait_for_shutdown(app)


if __name__=='__main__':main(sys.argv[1])
