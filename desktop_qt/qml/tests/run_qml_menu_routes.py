#!/usr/bin/env python3
"""Real QML file-menu route to the native relocation dialog; cancellation is read-only."""
import argparse,hashlib,json,os,sys,time
from pathlib import Path
from copy import deepcopy
from contextlib import ExitStack
from unittest.mock import patch
import urllib.request
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT));os.chdir(ROOT)
p=argparse.ArgumentParser();p.add_argument('--video',required=True);p.add_argument('--output',required=True);args=p.parse_args();out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');os.environ.setdefault('QT_QUICK_BACKEND','software')
if os.name!='nt':cache=out/'cache';cache.mkdir(exist_ok=True);os.environ['XDG_CACHE_HOME']=str(cache)
from PySide6.QtCore import QPoint,Qt,QTimer,QUrl
from PySide6.QtQuick import QQuickItem
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication,QFileDialog,QDialog,QTextEdit
from PySide6.QtTest import QTest
from desktop_qt.controller import Bridge
from lyricsync.model import Mapping,Translation
summary={'status':'running','created_utc':datetime.now(timezone.utc).isoformat(),'harness_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'main_qml_sha256':hashlib.sha256((ROOT/'desktop_qt/qml/Main.qml').read_bytes()).hexdigest()};(out/'summary.json').write_text(json.dumps(summary,indent=2))
# Fail closed if a menu regression tries to use a provider or stored credentials.
# Record operation names only: no key, URL, payload or worker result is logged.
from lyricsync import api_client,credentials
forbidden_calls=[];guards=ExitStack()
def forbidden(name):
 def reject(*args,**kwargs):
  forbidden_calls.append(name)
  raise AssertionError('Read-only menu attempted '+name)
 return reject
for owner,name in [(urllib.request.OpenerDirector,'open'),(api_client,'request'),(api_client,'request_models')]+[(credentials.KeyStore,name) for name in ('api','get','save','delete')]:
 guards.enter_context(patch.object(owner,name,forbidden(owner.__name__+'.'+name)))
QQuickStyle.setStyle('Basic');app=QApplication([]);b=Bridge(storage_dir=out/'recovery')
for name in ('_api_check','_api_job'):
 guards.enter_context(patch.object(b,name,forbidden(name)))
job_records=[];media_records=[];phase=['setup'];began=time.monotonic();original_run=b.jobs.run
# Observe submissions, not just active jobs: a short unwanted job must fail too.
def tracked_run(kind,function):
 record={'kind':kind,'phase':phase[0],'submitted_seconds':round(time.monotonic()-began,6)};job_records.append(record)
 record['id']=original_run(kind,function)
 return record['id']
b.jobs.run=tracked_run
def finished(identity,result,error):
 for record in job_records:
  if record.get('id')==identity:
   record.update(completed_seconds=round(time.monotonic()-began,6),error_type=type(error).__name__ if error else None);break
b.jobs.finished.connect(finished)
def tracked_media(name,original):
 def call(*args,**kwargs):
  media_records.append({'kind':name,'phase':phase[0]})
  return original(*args,**kwargs)
 return call
for name in ('open','seek'):
 setattr(b.media_controller.worker,name,tracked_media(name,getattr(b.media_controller.worker,name)))
e=QQmlApplicationEngine();e.rootContext().setContextProperty('bridge',b);e.load(QUrl.fromLocalFile(str(ROOT/'desktop_qt/qml/Main.qml')));assert e.rootObjects();w=e.rootObjects()[0]
def wait(predicate):
 end=time.monotonic()+10
 while time.monotonic()<end:
  app.processEvents()
  if predicate():return
  time.sleep(.005)
 raise AssertionError('Timed out: '+b._error_text+' '+json.dumps(pending(),ensure_ascii=False))
def pending():
 return {'active_jobs':list(b.jobs.active),'handlers':list(b._handlers),
         'layout_timer':b.layout_timer.isActive(),'layout_running':b._layout_running,'layout_pending':b._layout_pending,
         'layout_revision':b._layout_revision,'document_revision':b.document.revision,
         'autosave_timer':b.autosave_timer.isActive(),'save_running':b._save_running is not None,'save_pending':b._save_pending is not None}
def settled():
 # Project/media readiness precedes deferred layout (120 ms) and recovery saves
 # (450 ms). Wait for their actual completion, not one instant with no workers.
 return (not b.jobs.active and not b._handlers and not b.layout_timer.isActive()
         and not b._layout_running and not b._layout_pending and b._layout_revision==b.document.revision
         and not b.autosave_timer.isActive() and b._save_running is None and b._save_pending is None
         and b.media['displayedFrame']==b.media['requestedFrame']>0)
readonly_checks=[]
def readonly_baseline(name):
 assert settled(),pending()
 phase[0]=name
 return {'project':deepcopy(b.document.project.to_dict()),'revision':b.document.revision,
         'jobs':len(job_records),'media':len(media_records),'api':deepcopy(b.api_state)}
def assert_readonly(before):
 assert b.document.project.to_dict()==before['project'] and b.document.revision==before['revision']
 new_jobs=job_records[before['jobs']:];new_media=media_records[before['media']:]
 assert not new_jobs,{'action':phase[0],'unexpected_job_submissions':new_jobs}
 assert not new_media,{'action':phase[0],'unexpected_media_requests':new_media}
 assert settled(),{'action':phase[0],'unexpected_pending_work':pending()}
 assert not b._api_key and not b._api_scope and not forbidden_calls,forbidden_calls
 assert b.api_state==before['api'] and not b._error_text,b._error_text
 readonly_checks.append({'action':phase[0],'new_jobs':0,'new_media_requests':0,'pending_work':pending(),'document_revision':b.document.revision})
 phase[0]='setup'
def click(item):
 assert item and item.property('enabled');point=item.mapToScene(QPoint(int(item.width()/2),int(item.height()/2)))
 QTest.mouseClick(w,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point.toPoint());app.processEvents()
try:
 action=w.findChild(QQuickItem,'relocateSourcesAction');assert action is not None and not action.property('enabled')
 assert b.command('media.open',{'path':str(Path(args.video).resolve())});wait(lambda:b.media['ready'] and b.media['displayedFrame']==1)
 assert action.property('enabled');wait(settled);before=readonly_baseline('relocateSourcesAction')
 click(w.findChild(QQuickItem,'fileMenuButton'));wait(lambda:action.property('visible') and action.property('enabled') and action.width()>0)
 captured=[];timer=QTimer();timer.setInterval(30)
 def dismiss():
  dialog=app.activeModalWidget()
  if isinstance(dialog,QFileDialog):
   captured.append(dialog.windowTitle());dialog.grab().save(str(out/'relocate-native-cancel.png'));timer.stop();dialog.reject()
 timer.timeout.connect(dismiss);timer.start();click(action);wait(lambda:bool(captured));timer.stop()
 assert '重新定位同一素材' in captured[0]
 assert_readonly(before)
 # A clearly labeled QA history fixture, persisted/reopened through real storage.
 # This is not a provider result and is never default application data.
 project=b.document.project;idx=b.media_controller.index
 project.translations=[Translation('menu-t','菜单验收中文')]
 project.mappings=[Mapping('menu-cue',['menu-t'],start=idx.interval(10,100)[0],end=idx.interval(10,100)[1])]
 project.settings['api_suggestions']=[{'mapping_id':'menu-cue','status':'processed','context_signature':'historical-signature','suggestion':{'mappings':[{'mapping_ids':['menu-cue'],'lyric_ids':[],'translation_ids':['menu-t'],'current_chinese':['菜单验收中文'],'reason':'QA已保存历史建议（测试夹具，非真实API输出）','reference_meaning':'QA参考译意'}],'corrections':[]}}]
 b.selected='menu-cue';b.stage='review';b._changed();saved=out/'qa-saved-ai-history.lyria';save_revision=b.document.revision
 assert b.command('project.save',{'path':str(saved)})
 wait(lambda:b.document.saved_revision==save_revision and b._save_running is None)
 b._new() # Reset this test's generated fixture, not a user's document.
 assert b.command('project.open',{'path':str(saved)})
 wait(lambda:b.path==str(saved) and b.media['ready'] and b.selected=='menu-cue')
 assert b.document.project.settings['api_suggestions'][0]['suggestion']['mappings'][0]['reason'].startswith('QA已保存历史建议')
 wait(settled)
 dialogs=[]
 def native_menu(menu_button,action_name,dialog_name,filename):
  before=readonly_baseline(action_name);capture=[];timer=QTimer();timer.setInterval(30)
  def dismiss():
   # Saved review is deliberately nonmodal; activeModalWidget() cannot see it.
   # Match the visible named top-level window opened by the actual menu route.
   matches=[widget for widget in app.topLevelWidgets() if isinstance(widget,QDialog)
            and widget.isVisible() and widget.objectName()==dialog_name]
   assert len(matches)<=1, 'Duplicate visible native dialog: '+dialog_name
   dialog=matches[0] if matches else None
   if dialog is not None:
    view=dialog.findChild(QTextEdit,'savedAiReviewText')
    capture.append({'name':dialog.objectName(),'title':dialog.windowTitle(),'text':view.toPlainText() if view else '', 'readOnly':view.isReadOnly() if view else None,'modal':dialog.isModal()})
    dialog.grab().save(str(out/filename));timer.stop();dialog.reject()
  click(w.findChild(QQuickItem,menu_button));action=w.findChild(QQuickItem,action_name);wait(lambda:action is not None and action.property('visible') and action.property('enabled'))
  timer.timeout.connect(dismiss);timer.start();click(action);wait(lambda:bool(capture));timer.stop()
  assert_readonly(before)
  dialogs.extend(capture);return capture[0]
 for menu_button,action_name,filename in [('toolsMenuButton','savedAiAllAction','saved-ai-all.png'),('reviewOperationsButton','savedAiCurrentAction','saved-ai-current.png')]:
  result=native_menu(menu_button,action_name,'savedAiReviewDialog',filename)
  assert result['readOnly'] and not result['modal'] and '不确定' in result['text'] and '已过期' in result['text'] and 'QA参考译意' not in result['text'],result
  assert b.document.project.settings['api_suggestions'][0]['suggestion']['mappings'][0]['reference_meaning']=='QA参考译意'
 native_menu('toolsMenuButton','alignmentBatchAction','alignmentRangeDialog','alignment-range-cancel.png')
 summary.update(status='passed',finished_utc=datetime.now(timezone.utc).isoformat(),empty_input_disabled=True,actual_qml_menu_click=True,real_native_relocation_dialog=captured[0],cancel_no_document_mutation=True,no_verification_job_on_cancel=True,hash_semantics_changed=False,saved_ai_fixture_explicitly_not_provider_output=True,saved_history_reopened=True,actual_saved_ai_all_and_current_menu_dialogs=True,readonly_no_key_no_job_no_document_change=True,alignment_range_cancel_zero_request=True,readonly_checks=readonly_checks,native_dialogs=dialogs)
except BaseException as exc:summary.update(status='failed',error=type(exc).__name__+': '+str(exc),bridge_error=b._error_text);raise
finally:
 summary.update(job_submissions=job_records,media_requests=media_records,forbidden_calls=forbidden_calls,pending_work=pending())
 (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));b.shutdown();app.processEvents();guards.close()
