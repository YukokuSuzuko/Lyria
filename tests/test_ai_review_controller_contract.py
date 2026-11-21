from tests.ai_pairing_helpers import pairing_reply
"""Execute real controller method AST without Qt; this is NOT Qt UI acceptance.

The source methods are compiled unchanged except UI Slot decorators.
Retained AI internals are called through an explicit test-only helper; the
production command gate stays closed and is tested separately. Workers,
dialogs and scheduling are synchronous stubs, while review/cache/budget code is
real and every provider call is mocked. No dependencies or network are required.
"""
import ast
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from lyricsync import ai_config, api_client
from lyricsync.model import Project, Lyric, Mapping, Translation
from lyricsync.storage import Cache


ROOT=Path(__file__).resolve().parents[1]
tree=ast.parse((ROOT/'desktop_qt/controller.py').read_text(encoding='utf-8'))
bridge=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='Bridge')
names={'command','api_text_consent','_api_check','_api_review','_review_summary','_settings','configure_api','_job_progress'}
methods=[deepcopy(node) for node in bridge.body if isinstance(node,ast.FunctionDef) and node.name in names]
candidate_tree=ast.parse((ROOT/'desktop_qt/candidate_workspace.py').read_text(encoding='utf-8'))
candidate_class=next(node for node in candidate_tree.body if isinstance(node,ast.ClassDef) and node.name=='CandidateWorkspace')
# Compile the real guard too; never replace this boundary with a no-op stub.
methods.extend(deepcopy(node) for node in candidate_class.body if isinstance(node,ast.FunctionDef) and node.name=='_candidate_guard')
for node in methods:
    node.decorator_list=[d for d in node.decorator_list if isinstance(d,ast.Name) and d.id=='staticmethod']
module=ast.Module(body=[ast.ClassDef(name='Methods',bases=[],keywords=[],body=methods,decorator_list=[])],type_ignores=[])
namespace={'__name__':'desktop_qt.controller','__package__':'desktop_qt','deepcopy':deepcopy,'Project':Project}
exec(compile(ast.fix_missing_locations(module),str(ROOT/'desktop_qt/controller.py'),'exec'),namespace)


class Harness(namespace['Methods']):
    def __init__(self,root):
        self._candidate_source='main'
        self._candidate_job=None;self._candidate_task={'running':False,'cancelling':False}
        self._shutdown=False  # Match the real Bridge lifecycle before command/progress dispatch.
        self._alignment_preview=SimpleNamespace(active=False)
        self.storage_dir=root;self._api_key='fake-controller-contract-key'
        self.api_settings={**ai_config.DEFAULTS,'api_retries':0}
        self._api_scope=ai_config.credential_scope(self.api_settings);self._api_text_consents=set()
        self.document=SimpleNamespace(project=Project(),revision=0)
        self.history=SimpleNamespace(end=lambda:None,push=lambda *args:None)
        self.diagnostics=SimpleNamespace(record=lambda *args,**kwargs:None)
        self.media_controller=SimpleNamespace(index=None)
        self.selected='m30';self._task={'running':False};self.api_state={}
        self.progress=[];self.messages=[];self.errors=[];self.budgets=[];self.resume=None
        self.cancel=lambda:False
        p=self.document.project
        p.lyrics=[Lyric(f'l{i}',f'original {i}',i,i+1) for i in range(60)]
        p.translations=[Translation(f't{i}',f'可靠中文{i}') for i in range(60)]
        p.mappings=[Mapping(f'm{i}',[f't{i}'],[f'l{i}'],start=i,end=i+1) for i in range(60)]
        p.settings.update(api_enabled=False,api_auto_review=False)

    def retained_api_for_test(self,name,payload):
        """Exercise retained consent/budget code, never reopen production dispatch."""
        try:
            self._candidate_guard(name)
            if name=='row.align':self._api_review({**payload,'id':self._row(payload.get('id')).id})
            elif name=='api.review':self._api_review(payload)
            else:raise AssertionError('Unexpected retained API test command')
            return True
        except ValueError as exc:
            self._error(str(exc));return False

    def _row(self,identity=None):
        return next(m for m in self.document.project.mappings if m.id==(identity or self.selected))
    def _changed(self):self.document.revision+=1
    def _refresh(self,*args):pass
    def _message(self,text):self.messages.append(text)
    def _error(self,text):self.errors.append(text)
    def _set_api_resume(self,callback):self.resume=callback
    def _budget_dialog(self,state):self.budgets.append(state)
    def _api_job(self,label,operation,callback,resume=None):
        cache=Cache(self.storage_dir/'cache.sqlite')
        try:
            cache.restore_records(self.document.project.api_records)
            result=operation(cache,self._api_key,deepcopy(self.api_settings),self.cancel,
                             lambda fraction,text:self.progress.append((fraction,text)))
            self.document.project.api_records=cache.records()
            callback(result)
        finally:cache.close()


class ControllerContracts(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.b=Harness(Path(self.folder.name))
        self.calls=[];self.shown=[];self.asks=[];self.allow=True
        dialogs=ModuleType('desktop_qt.dialogs')
        dialogs.confirm_text_api_consent=lambda settings:(self.asks.append(deepcopy(settings)),self.allow)[1]
        dialogs.show_text=lambda title,text,**kwargs:self.shown.append((title,text))
        self.modules=patch.dict('sys.modules',{'desktop_qt.dialogs':dialogs});self.modules.start()
        self.provider=patch.object(api_client,'request',self.transport);self.provider.start()
        self.network=patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('network forbidden'));self.network.start()
    def tearDown(self):
        self.network.stop();self.provider.stop();self.modules.stop();self.folder.cleanup()
    def transport(self,body,key,settings=None,**callbacks):
        self.calls.append(deepcopy(body));return pairing_reply(body),{}

    def test_candidate_source_blocks_api_without_provider_request(self):
        before=self.b.document.project.to_dict()
        for source in ('ocr','vocal'):
            self.b._candidate_source=source
            for command in ('row.align','api.review','alignment.batch'):
                self.assertFalse(self.b.command(command,{}))
                self.assertIn('暂时停用',self.b.errors[-1])
                with self.assertRaisesRegex(ValueError,'只读'):
                    self.b._candidate_guard(command)
        self.assertEqual(self.calls,[])
        self.assertEqual(self.asks,[])
        self.assertEqual(self.b.document.project.to_dict(),before)

    def test_production_ai_commands_remain_disabled_without_consent_or_network(self):
        self.b.document.project.settings.update(api_enabled=True,api_auto_review=True)
        before=self.b.document.project.to_dict()
        for name in ('row.align','api.review','alignment.batch','row.referenceTranslation','alignment.forceRetry'):
            self.assertFalse(self.b.command(name,{}))
            self.assertIn('暂时停用',self.b.errors[-1])
        self.assertEqual(self.calls,[]);self.assertEqual(self.asks,[])
        self.assertFalse(self.b.document.project.api_records)
        self.assertEqual(self.b.document.project.to_dict(),before)

    def test_row_command_only_current_and_two_neighbors_manual_switch_off(self):
        p=self.b.document.project
        p.settings['api_suggestions']=[{'mapping_id':'m1','status':'processed','sentinel':'untouched'}]
        self.assertTrue(self.b.retained_api_for_test('row.align',{}))
        self.assertEqual(len(self.calls),1);self.assertEqual(len(self.asks),1)
        payload=json.loads(self.calls[0]['messages'][1]['content'])
        self.assertEqual([r['id'] for r in payload['current_pairings']],['m29','m30','m31'])
        self.assertEqual(p.settings['api_suggestions'][0]['sentinel'],'untouched')
        self.assertFalse(p.settings['api_auto_review']);self.assertFalse(p.settings['api_enabled'])
        self.assertIn('1/1',self.shown[-1][1])

    def test_full_command_ignores_current_selection_and_shows_all(self):
        self.assertTrue(self.b.retained_api_for_test('api.review',{}));self.assertEqual(len(self.calls),10)
        self.assertIn('第 60 句',self.shown[-1][1]);self.assertIn('60/60',self.shown[-1][1])
        self.assertIn('10/10',self.b.progress[-1][1]);self.assertEqual(self.b.progress[-1][0],1)

    def test_declined_consent_even_legacy_imported_enabled_cannot_send(self):
        self.b.document.project.settings.update(api_enabled=True,api_auto_review=True)
        self.allow=False
        self.assertFalse(self.b.retained_api_for_test('row.align',{}));self.assertEqual(self.calls,[])
        self.assertEqual(self.b._api_text_consents,set());self.assertIn('已取消授权，本次未发送 AI 请求',self.b.errors[-1])

    def test_endpoint_change_requires_new_consent_and_no_old_key(self):
        self.b._api_check();self.assertEqual(len(self.asks),1)
        self.b.api_settings.update(api_provider='custom',api_base_url='https://different.invalid/v1')
        with self.assertRaises(ValueError):self.b._api_check()
        self.assertEqual(len(self.asks),1)
        self.b._api_scope=ai_config.credential_scope(self.b.api_settings)
        self.allow=False
        with self.assertRaises(ValueError):self.b._api_check()
        self.assertEqual(len(self.asks),2)

    def test_configuring_key_alone_does_not_consent_or_enable_automatic(self):
        self.b.configure_api(self.b.api_settings,'fake-new-key',False)
        self.assertFalse(self.b.api_text_consent());self.assertFalse(self.b.document.project.settings['api_auto_review'])
        self.assertEqual(self.calls,[])
        self.b.configure_api(self.b.api_settings,'',True)
        self.assertTrue(self.b.api_text_consent());self.assertFalse(self.b.document.project.settings['api_auto_review'])

    def test_auto_toggle_off_keeps_manual_consent(self):
        self.b._api_text_consents.add(self.b._api_scope)
        self.assertTrue(self.b.command('settings.set',{'key':'apiEnabled','value':False}))
        self.assertTrue(self.b.retained_api_for_test('row.align',{}));self.assertEqual(self.asks,[])

    def test_partial_cancel_is_not_reported_as_success(self):
        self.b.cancel=lambda:any('已核对 6/60' in message for _,message in self.b.progress)
        self.assertTrue(self.b.retained_api_for_test('api.review',{}))
        coverage=self.b.document.project.settings['api_coverage']
        self.assertEqual(coverage['status'],'cancelled');self.assertEqual(len(self.calls),1)
        self.assertIn('已取消',self.shown[-1][1]);self.assertIn('6/60',self.shown[-1][1])
        self.assertNotIn('核对已完成',self.shown[-1][1]);self.assertEqual(self.b.budgets,[])
        self.assertLess(self.b._task['progress'],1)

    def test_auth_failure_stops_full_review_and_explains_fix(self):
        def denied(body,*args,**callbacks):
            self.calls.append(body);raise HTTPError('https://invalid',401,'secret response',{},None)
        with patch.object(api_client,'request',denied):self.b.retained_api_for_test('api.review',{})
        self.assertEqual(len(self.calls),1);self.assertIn('密钥无效',self.shown[-1][1])
        self.assertIn('待核对 54',self.shown[-1][1]);self.assertNotIn('secret response',self.shown[-1][1])

    def test_budget_continue_reuses_completed_cache_and_keeps_default(self):
        self.b.api_settings['api_max_calls']=1;self.b.retained_api_for_test('api.review',{})
        self.assertEqual(len(self.calls),1);self.assertEqual(len(self.b.budgets),1)
        self.b.resume(100)
        self.assertEqual(len(self.calls),10);self.assertEqual(self.b.api_settings['api_max_calls'],1)
        self.assertEqual(self.b.document.project.settings['api_coverage']['status'],'completed')

    def test_retry_progress_and_cancelled_error_prevent_further_attempts(self):
        self.b.api_settings['api_retries']=3
        def timeout(body,*args,**callbacks):
            self.calls.append(body);raise TimeoutError()
        self.b.cancel=lambda:bool(self.calls)
        with patch.object(api_client,'request',timeout):self.b.retained_api_for_test('api.review',{})
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.b.document.project.settings['api_coverage']['status'],'cancelled')
        self.assertEqual(self.b.document.project.api_records[0]['status'],'timeout')

    def test_cancelling_progress_label_cannot_be_overwritten(self):
        self.b._task={'id':'job','cancelling':True,'label':'stopping','progress':.1}
        self.b._job_progress('job',.5,'waiting')
        self.assertEqual(self.b._task['label'],'stopping')

    def test_dialog_never_auto_checks_consent_on_key_input(self):
        source=(ROOT/'desktop_qt/dialogs.py').read_text(encoding='utf-8')
        self.assertNotIn('self.key.textChanged.connect',source)
        self.assertNotIn('suggest_enabled',source)
        self.assertIn('setDefaultButton(QMessageBox.StandardButton.No)',source)
        self.assertNotIn('self.auto_review=QCheckBox',source)
        self.assertIn('AI 核对入口暂时隐藏；保存配置不会启用请求，OCR 完成后也不会自动发送。',source)


if __name__=='__main__':unittest.main()
