#!/usr/bin/env python3
"""Focused real QML/IME/F6 regression. Reads an existing local test video; no OCR/API."""
import argparse,hashlib,json,os,sys,time
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT));os.chdir(ROOT)
p=argparse.ArgumentParser();p.add_argument('--video',required=True);p.add_argument('--output',required=True);args=p.parse_args()
out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');os.environ.setdefault('QT_QUICK_BACKEND','software')
if os.name!='nt':cache=out/'cache';cache.mkdir(exist_ok=True);os.environ['XDG_CACHE_HOME']=str(cache)
from PySide6.QtCore import QPoint,Qt,QUrl
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from desktop_qt.controller import Bridge
from lyricsync.model import Mapping,Translation
summary={'status':'running','created_utc':datetime.now(timezone.utc).isoformat(),'harness_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'review_qml_sha256':hashlib.sha256((ROOT/'desktop_qt/qml/ReviewPanel.qml').read_bytes()).hexdigest(),'controller_sha256':hashlib.sha256((ROOT/'desktop_qt/controller.py').read_bytes()).hexdigest()}
(out/'summary.json').write_text(json.dumps(summary,indent=2))
QQuickStyle.setStyle('Basic');app=QApplication([]);b=Bridge(storage_dir=out/'recovery');e=QQmlApplicationEngine();e.rootContext().setContextProperty('bridge',b);e.load(QUrl.fromLocalFile(str(ROOT/'desktop_qt/qml/Main.qml')));assert e.rootObjects();w=e.rootObjects()[0]
def wait(predicate,timeout=10):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  app.processEvents()
  if predicate():return
  time.sleep(.005)
 raise AssertionError('Timed out: '+b._error_text)
def find(name,item=None):
 item=item or w.contentItem()
 if item.objectName()==name:return item
 for child in item.childItems():
  found=find(name,child)
  if found:return found
 return None
try:
 assert b.command('media.open',{'path':str(Path(args.video).resolve())})
 wait(lambda:b.media['ready'] and b.media['displayedFrame']==1)
 project=b.document.project;idx=b.media_controller.index
 project.translations=[Translation('ime-t','完整字符与组合符 e\u0301 👨‍👩‍👧‍👦')]
 project.mappings=[Mapping('ime-cue',['ime-t'],start=idx.interval(10,100)[0],end=idx.interval(10,100)[1])]
 b.selected='ime-cue';b.stage='review';b._changed();b.command('marker.add',{'frame':55});b.command('media.seek',{'frame':55});wait(lambda:b.media['displayedFrame']==55)
 editor=find('chineseEditor_ime-cue');assert editor;QTest.keyClick(w,Qt.Key.Key_F2);assert editor.hasActiveFocus() and not editor.property('readOnly');editor.setProperty('cursorPosition',2);app.processEvents()
 app.sendEvent(editor,QInputMethodEvent('候选输入中',[]));app.processEvents()
 assert editor.property('inputMethodComposing') and b._editor_composing
 before=deepcopy(b.document.project.to_dict());revision=b.document.revision;undo=len(b.history.undo_stack)
 QTest.keyClick(w,Qt.Key.Key_F6);app.processEvents()
 assert b.document.project.to_dict()==before and b.document.revision==revision and len(b.history.undo_stack)==undo
 assert editor.property('inputMethodComposing') and '先完成输入法输入' in w.property('noticeText')
 # Defense in depth: even an external direct bridge call cannot split preedit.
 assert b.command('row.split',{'id':'ime-cue','frame':55,'offsetUtf16':2}) is False
 assert b.document.project.to_dict()==before and '输入法' in b._error_text
 w.grabWindow().save(str(out/'ime-f6-rejected.png'))
 commit=QInputMethodEvent();commit.setCommitString('已提交');app.sendEvent(editor,commit);app.processEvents()
 assert not editor.property('inputMethodComposing') and not b._editor_composing
 assert '已提交' in b.model.items[0]['chinese'] and '候选输入中' not in b.model.items[0]['chinese']
 text=b.model.items[0]['chinese'];editor.setProperty('cursorPosition',2);QTest.keyClick(w,Qt.Key.Key_F6)
 wait(lambda:len(b.document.project.mappings)==2)
 rows=b.state['rows'];assert ''.join(r['chinese'] for r in rows)==text
 assert rows[0]['lastFrame']==54 and rows[1]['firstFrame']==55 and b.document.project.settings['frame_markers']==[55]
 w.grabWindow().save(str(out/'ime-after-commit-f6-split.png'))
 summary.update(status='passed',finished_utc=datetime.now(timezone.utc).isoformat(),ime_preedit_f6_rejected=True,bridge_composition_split_rejected=True,no_preedit_saved_or_split=True,visible_rejection_feedback=True,post_commit_f6_split_works=True,fixed_marker_preserved=True,windows_real_ime_acceptance=False)
except BaseException as exc:summary.update(status='failed',error=type(exc).__name__+': '+str(exc),bridge_error=b._error_text);raise
finally:(out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));b.shutdown();app.processEvents()
