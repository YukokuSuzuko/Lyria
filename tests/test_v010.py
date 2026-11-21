from tests.ai_pairing_helpers import pairing_reply
"""Backend boundaries independent of the deferred HTML frontend."""
import json
import tempfile
import unittest
from pathlib import Path
from lyricsync.model import Project,Lyric,Translation,Mapping
from lyricsync.editing import Editor,History
from lyricsync.storage import Cache,save_project,load_project
from lyricsync.api import suggest
from lyricsync.audit import text_audit,local_audit


class BackendCompletionTests(unittest.TestCase):
    def project(self,count=8):
        p=Project();p.settings.update(api_enabled=True,api_retries=0,api_max_calls=10)
        p.lyrics=[Lyric(f'l{i}',f'line {i}',i,i+1) for i in range(count)]
        p.translations=[Translation(f't{i}',f'中文{i}') for i in range(count)]
        p.mappings=[Mapping(f'm{i}',[f't{i}'],[f'l{i}'],start=i,end=i+1) for i in range(count)]
        return p

    def test_split_review_uses_distinct_current_text_units(self):
        p=self.project(2)
        p.mappings[1].translation_ids=['t0']
        p.mappings[0].text_override='前半句';p.mappings[1].text_override='后半句'
        with tempfile.TemporaryDirectory() as d:
            cache=Cache(Path(d)/'c.sqlite');payloads=[]
            def transport(body,key):
                data=json.loads(body['messages'][1]['content']);payloads.append(data)
                return pairing_reply(body),{}
            try:result=suggest(p,cache,'fake',transport=transport)
            finally:cache.close()
        units=payloads[0]['current_pairings']
        self.assertEqual([u['chinese'] for u in units],['前半句','后半句'])
        self.assertEqual(len({u['id'] for u in units}),2)
        self.assertEqual(result[1]['suggestion']['verdict'],'uncertain')

    def test_adjacent_batch_context_and_checkpoint_survive_cancel(self):
        p=self.project();saved=[];calls=[]
        with tempfile.TemporaryDirectory() as d:
            cache=Cache(Path(d)/'c.sqlite');path=Path(d)/'p.json'
            def transport(body,key):
                calls.append(json.loads(body['messages'][1]['content']))
                return pairing_reply(body),{}
            def checkpoint():save_project(p,path);saved.append(load_project(path))
            try:
                with self.assertRaises(InterruptedError):
                    suggest(p,cache,'fake',cancel=lambda:bool(saved),transport=transport,checkpoint=checkpoint)
                self.assertEqual(saved[-1].settings['api_coverage']['processed'],6)
                self.assertEqual(saved[-1].settings['api_coverage']['pending'],2)
                self.assertIn('中文6',[r['chinese'] for r in calls[0]['current_pairings']])
                suggest(p,cache,'fake',transport=transport)
                self.assertEqual(len(calls),2,'first completed batch should be cached')
            finally:cache.close()

    def report(self):
        return dict(inputs={},cues=[dict(id='c',text='中文',start=1,end=2)],issues=[],warnings=[],scope='test',
                    api={'text':{'status':'not_run','checked_ids':[]},'image':{'status':'disabled','checked_ids':[]}})

    def test_audit_rerun_replaces_findings_and_coverage(self):
        report=self.report()
        raw=json.dumps(dict(checked_ids=['c'],issues=[dict(cue_ids=['c'],kind='uncertain',confidence=.5,reason='review')]))
        with tempfile.TemporaryDirectory() as d:
            text_audit(report,d,'fake',transport=lambda *a:(raw,{}))
            text_audit(report,d,'fake',transport=lambda *a:(raw,{}))
            self.assertEqual(len(report['issues']),1)
            text_audit(report,d,'fake',model='other',transport=lambda *a:('{"checked_ids":[],"issues":[]}',{}))
            self.assertEqual(report['api']['text']['status'],'partial')
            self.assertEqual(report['api']['text']['checked_ids'],[])
            self.assertEqual(report['issues'],[])

    def test_manual_text_and_ocr_source_are_used_as_labeled_project_reference(self):
        from lyricsync.subtitles import import_translation
        from lyricsync.export import export_project
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);zh=d/'zh.txt';zh.write_text('最初译文',encoding='utf8')
            p=self.project(1);p.inputs={'video':str(root/'examples/synthetic_mv.mp4'),'translation':str(zh)}
            p.translations=import_translation(zh);p.mappings[0].translation_ids=[p.translations[0].id]
            p.mappings[0].text_override='人工改好的中文';p.mappings[0].confirmed=True
            p.video={'width':960,'height':540,'duration':15}
            path=d/'p.json';save_project(p,path);ass=d/'out.ass';export_project(p,ass)
            report=local_audit(p.inputs['video'],zh,d/'report',ass_path=ass,project_path=path)
            self.assertEqual(report['cues'][0]['expected_texts'],['人工改好的中文'])
            self.assertEqual(report['cues'][0]['reference_lyrics'],['line 0'])
            self.assertFalse(any('无法核对中外语义' in w for w in report['warnings']))
            self.assertIn('最初译文',str(report['translation_baseline']))

    def test_marker_rejects_out_of_video_range_without_history(self):
        from lyricsync.frames import FrameIndex
        p=self.project(1);editor=Editor(p,History());index=FrameIndex([0,.1,.2],.3)
        with self.assertRaises(ValueError):editor.add_marker(4,index=index)
        self.assertEqual(editor.history.undo_stack,[])
        editor.add_marker(3,index=index)
        self.assertEqual(p.settings['frame_markers'],[3])

    def test_drop_plan_requires_text_roles_and_never_mutates_project(self):
        from lyricsync.importing import plan_files,resolve_plan
        with tempfile.TemporaryDirectory() as d:
            paths=[Path(d)/name for name in ('夜の歌.mp4','原文.ass','中文.txt')]
            for path in paths:path.touch()
            plan=plan_files(paths+[paths[0]])
            self.assertEqual(len(plan['ambiguous']),2)
            with self.assertRaises(ValueError):resolve_plan(plan,{})
            resolved=resolve_plan(plan,{str(paths[1].resolve()):'ktv',str(paths[2].resolve()):'translation'})
            self.assertEqual(set(resolved),{'video','ktv','translation'})
            with self.assertRaises(ValueError):plan_files([paths[0]],role='translation')
            with self.assertRaises(ValueError):resolve_plan(plan,{str(paths[1].resolve()):'ktv',str(paths[2].resolve()):'ktv'})
            paths[2].unlink()
            with self.assertRaises(ValueError):resolve_plan(plan,{str(paths[1].resolve()):'ktv',str(paths[2].resolve()):'translation'})

    def test_drop_project_cannot_be_mixed_with_new_material(self):
        from lyricsync.importing import plan_files
        with tempfile.TemporaryDirectory() as d:
            project=Path(d)/'旧任务.lyricsync.json';project.touch()
            text=Path(d)/'中文.txt';text.touch()
            self.assertEqual(plan_files([project])['assignments'],{'project':str(project.resolve())})
            with self.assertRaises(ValueError):plan_files([project,text])

    def test_merge_preserves_known_outer_bounds_of_partial_rows(self):
        from lyricsync.editing import merge_mappings
        p=self.project(2);p.mappings[0].start=1;p.mappings[0].end=None
        p.mappings[1].start=None;p.mappings[1].end=3
        merged=merge_mappings(p,{'m0','m1'})
        self.assertEqual((merged.start,merged.end),(1,3))
        self.assertFalse(merged.confirmed)
        self.assertTrue(any('包含未打轴的句子' in r for r in merged.reasons))

    def test_image_review_request_path_is_removed(self):
        from lyricsync import audit
        self.assertFalse(hasattr(audit,'image_audit'))

    def test_global_missing_items_are_in_html_report(self):
        from lyricsync.audit import save_report
        report=self.report();report['issues']=[dict(cue_ids=[],kind='missing',reason='缺失 <中文>')]
        with tempfile.TemporaryDirectory() as d:
            save_report(report,d)
            self.assertIn('缺失 &lt;中文&gt;',(Path(d)/'report.html').read_text(encoding='utf8'))

    def test_brand_change_keeps_credential_identity(self):
        import hashlib
        from lyricsync.credentials import KeyStore,TEXT_SCOPE
        self.assertEqual(KeyStore().target(TEXT_SCOPE),'MV_LyricSync/credentials/v1/'+hashlib.sha256(TEXT_SCOPE.encode()).hexdigest())

    def test_timeout_and_truncated_responses_have_specific_errors(self):
        from urllib.error import URLError
        from lyricsync.api import error_status,IncompleteResponse
        self.assertEqual(error_status(URLError(TimeoutError('private detail'))),'timeout')
        self.assertEqual(error_status(IncompleteResponse('private detail')),'response_truncated')

    def test_old_ai_suggestion_is_not_presented_as_current_after_edit(self):
        from lyricsync.api import review_signature
        from lyricsync.workspace import describe_suggestions
        p=self.project(1);m=p.mappings[0]
        p.settings['api_suggestions']=[dict(mapping_id=m.id,context_signature=review_signature(p,m),suggestion={})]
        Editor(p,History()).set_text(m.id,'已经改过的中文')
        self.assertIn('已过期',describe_suggestions(p,m))

    def test_frame_indices_reject_float_and_boolean(self):
        from lyricsync.frames import FrameIndex
        index=FrameIndex([0,.1,.2],.3)
        for first,last in ((1.0,2),(1,2.0),(True,2),(1,False)):
            with self.assertRaises(ValueError):index.interval(first,last)

    def test_first_legacy_project_keeps_model_but_existing_shared_settings_win(self):
        from lyricsync import ai_config
        with tempfile.TemporaryDirectory() as d:
            old=dict(api_model='user-chosen-model',api_max_calls=7,api_retries=0,api_key='DO_NOT_COPY')
            result=ai_config.adopt_project(d,old)
            self.assertEqual(result['api_model'],'user-chosen-model')
            self.assertNotIn('DO_NOT_COPY',(Path(d)/'ai_settings.json').read_text(encoding='utf-8'))
            result=ai_config.adopt_project(d,{**old,'api_model':'other-project'})
            self.assertEqual(result['api_model'],'user-chosen-model')


if __name__=='__main__':unittest.main()
