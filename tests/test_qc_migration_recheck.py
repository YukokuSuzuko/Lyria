"""Unreliable OCR: scoped append-only evidence and explicit human dispositions."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from lyricsync.audit import local_audit,text_audit
from lyricsync.audit_service import (rescan_audit,record_audit_review,fingerprint_inputs,
    verify_input_versions,refine_cues,run_audit)
from lyricsync.model import Visual
from lyricsync.subtitles import import_translation
from test_qc_migration import clock,strong,report_fixture,FakeVideo,FakeOCR,ASS


class RecheckTests(unittest.TestCase):
    def parent(self,folder):
        folder=Path(folder);video=folder/'video.mp4';video.write_bytes(b'original video')
        text=folder/'reference.txt';text.write_text('中文',encoding='utf8')
        report=report_fixture();report['inputs']={'video':str(video),'translation':str(text),'ass':None,'project':None,'ktv':None}
        report['input_versions']=fingerprint_inputs(report['inputs'])
        report['stage']={'kind':'finished_video','label':'test'}
        report['regions']={'chinese':{'box':[0,10,20,20],'confirmed':True,'origin':'user'},
                           'source':{'box':[0,0,20,10],'confirmed':True,'origin':'user'}}
        report['cues'][0]['expected_ids']=[import_translation(text)[0].id]
        report['ocr_settings']={'engine':'rapidocr','chinese_language':'chi_sim','source_language':'jpn+eng','sample_fps':3}
        return report,folder/'report'

    def child(self,parent):
        report=copy.deepcopy(parent);report['cues'][0]['text']='new OCR candidate, not adopted'
        report['scope_detail']={'kind':'local_rescan','first_frame':2,'last_frame':4,'adjacent_evidence_may_extend_range':True}
        return report

    def test_scoped_rescan_appends_without_changing_parent_observations(self):
        with tempfile.TemporaryDirectory() as d:
            parent,out=self.parent(d);before=copy.deepcopy(parent['cues']);child=self.child(parent)
            with patch('lyricsync.audit_service.Video',return_value=FakeVideo(clock().stamps)),patch('lyricsync.audit_service.AuditClock.build',return_value=clock()),patch('lyricsync.audit_service.run_audit',return_value=child) as run:
                result=rescan_audit(parent,out,first_frame=2,last_frame=4,cue_ids=['c'],
                                    chinese_language='chi_tra',source_language='eng')
            args=run.call_args.kwargs
            self.assertEqual(args['mode'],'local');self.assertIsNone(args['semantic_runner'])
            self.assertEqual(args['frame_range'],[2,4]);self.assertEqual(args['expected_ids'],before[0]['expected_ids'])
            self.assertEqual(args['settings']['chinese_language'],'chi_tra');self.assertEqual(args['settings']['source_language'],'eng')
            self.assertEqual(parent['cues'],before)
            self.assertEqual(result['rescan']['status'],'needs_review')
            self.assertFalse(result['rescan']['counts_as_pass'])
            self.assertTrue(result['report']['parent_audit']['parent_observations_unchanged'])
            persisted=json.loads((out/'report.json').read_text(encoding='utf-8'))
            self.assertEqual(persisted['cues'],before)
            self.assertEqual(persisted['rescans'][0]['range']['start']['pts'],40)
            self.assertFalse(persisted['coverage']['complete_video_verified'])
            self.assertTrue(Path(result['rescan']['report_path']).is_file())

    def test_cancellation_and_failure_are_persisted_not_silent(self):
        for exception,status in ((InterruptedError('cancel'),'cancelled'),(RuntimeError('fail'),'failed')):
            with self.subTest(status=status),tempfile.TemporaryDirectory() as d:
                parent,out=self.parent(d);before=copy.deepcopy(parent['cues'])
                with patch('lyricsync.audit_service.Video',return_value=FakeVideo(clock().stamps)),patch('lyricsync.audit_service.AuditClock.build',return_value=clock()),patch('lyricsync.audit_service.run_audit',side_effect=exception):
                    with self.assertRaises(type(exception)):rescan_audit(parent,out,first_frame=2,last_frame=4,cue_ids=['c'])
                persisted=json.loads((out/'report.json').read_text(encoding='utf-8'))
                self.assertEqual(persisted['rescans'][0]['status'],status)
                self.assertEqual(persisted['cues'],before)
                self.assertIn('finished_at',persisted['rescans'][0])

    def test_changed_video_and_changed_ass_refuse_before_decode_or_ocr(self):
        for role in ('video','ass','translation','project'):
            with self.subTest(role=role),tempfile.TemporaryDirectory() as d:
                parent,out=self.parent(d)
                if not parent['inputs'].get(role):
                    path=Path(d)/(role+'.txt');path.write_bytes(b'original')
                    parent['inputs'][role]=str(path)
                parent['input_versions']=fingerprint_inputs(parent['inputs'])
                Path(parent['inputs'][role]).write_bytes(b'replaced content at same path')
                with patch('lyricsync.audit_service.Video') as decoder,patch('lyricsync.audit_service.run_audit') as run:
                    with self.assertRaisesRegex(ValueError,'文件内容已变化'):rescan_audit(parent,out,first_frame=1,last_frame=3)
                    decoder.assert_not_called();run.assert_not_called()
                self.assertNotIn('rescans',parent)

    def test_unversioned_old_report_refuses_to_assume_same_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            parent,out=self.parent(d);parent.pop('input_versions')
            with patch('lyricsync.audit_service.Video') as decoder:
                with self.assertRaisesRegex(ValueError,'缺少完整的素材校验信息'):rescan_audit(parent,out,first_frame=1,last_frame=2)
                decoder.assert_not_called()

    def test_invalid_frame_range_or_unconfirmed_region_does_not_start_scan(self):
        with tempfile.TemporaryDirectory() as d:
            parent,out=self.parent(d)
            with patch('lyricsync.audit_service.run_audit') as run:
                for first,last in ((True,3),(0,3),(3,2)):
                    with self.assertRaises(ValueError):rescan_audit(parent,out,first_frame=first,last_frame=last)
                with patch('lyricsync.audit_service.Video',return_value=FakeVideo(clock().stamps)),patch('lyricsync.audit_service.AuditClock.build',return_value=clock()):
                    with self.assertRaises(ValueError):rescan_audit(parent,out,first_frame=1,last_frame=3,chinese_roi={'box':[0,10,20,20],'origin':'candidate','confirmed':False})
                run.assert_not_called()

    def test_manual_skip_review_and_reset_never_pass_or_mutate_text_timing(self):
        with tempfile.TemporaryDirectory() as d:
            parent,out=self.parent(d);before={k:parent['cues'][0][k] for k in ('text','start','end','first_frame','last_frame')}
            for status in ('skipped','reviewed','needs_review'):
                record_audit_review(parent,out,['c'],status,note='User inspected local frame')
                cue=parent['cues'][0]
                self.assertEqual({k:cue[k] for k in before},before)
                self.assertEqual(cue['review_status'],status);self.assertFalse(cue['counts_as_pass'])
                self.assertFalse(parent['coverage']['complete_video_verified'])
            self.assertEqual(len(parent['review_history']),3)
            self.assertTrue(all(not event['counts_as_pass'] for event in parent['review_history']))
            self.assertEqual(parent['coverage']['manual_pending'],2)
            with self.assertRaises(ValueError):record_audit_review(parent,out,['c'],'passed')
            with self.assertRaises(ValueError):record_audit_review(parent,out,['unknown'],'reviewed')

    def test_review_save_source_alias_rejected_without_mutating_memory(self):
        with tempfile.TemporaryDirectory() as d:
            parent,out=self.parent(d);out.mkdir();os.link(parent['inputs']['video'],out/'report.json')
            before=copy.deepcopy(parent)
            with self.assertRaises(ValueError):record_audit_review(parent,out,['c'],'skipped')
            self.assertEqual(parent,before)
            self.assertEqual(Path(parent['inputs']['video']).read_bytes(),b'original video')

    def test_partial_expected_filter_does_not_call_outside_translation_missing(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);video=d/'v';video.touch();text=d/'t.txt';text.write_text('中文\n范围外',encoding='utf8')
            ids=[t.id for t in import_translation(text)]
            with patch('lyricsync.audit.Video',return_value=FakeVideo(clock().stamps)):
                result=local_audit(video,text,d/'report',_observed=[strong()],_clock=clock(),_expected_ids=[ids[0]])
            self.assertEqual(result['coverage']['expected_total'],1)
            self.assertEqual(result['coverage']['expected_matched'],1)
            self.assertEqual(result['unobserved_cues'],[])
            self.assertFalse(any('范围外' in i['reason'] for i in result['issues']))

    def test_scan_window_edges_not_promoted_to_true_boundaries(self):
        c=clock(tuple(i/10 for i in range(31)),end=3.1);v=FakeVideo(c.stamps)
        cue=refine_cues(v,[Visual('x','中文',1,1.4,[0,0,10,10],.99,2)],c,{'box':[0,0,20,20]}, {'sample_fps':3},provider=FakeOCR(active=tuple(range(1,32)),confidence=.99))[0]
        self.assertEqual(cue['boundary_status'],'needs_review')
        self.assertTrue(any('字幕超出局部扫描范围' in note for note in cue['warnings']))

    def test_unresolved_boundary_keeps_real_sample_without_fabricated_start(self):
        cue=refine_cues(FakeVideo(clock().stamps),[Visual('x','中文',.04,.3,[0,0,10,10],.99,2)],clock(),{'box':[0,0,20,20]},{},provider=FakeOCR(active=()))[0]
        self.assertIsNone(cue['first_frame']);self.assertIsNone(cue['last_frame'])
        self.assertEqual(cue['representative_frame'],2)
        self.assertEqual(cue['jump']['kind'],'sample_only')
        self.assertEqual(cue['raw_observations'][1]['pts'],40)

    def test_unobserved_expected_ass_gets_actual_reference_samples_not_ocr_text(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);video=d/'v';video.touch();ass=d/'expected.ass';ass.write_text(ASS)
            class IndexedVideo(FakeVideo):
                def __init__(self,path):super().__init__(clock().stamps);self.path=str(path);self.origin=0
                def at(self,stamp,**kwargs):return stamp,np.full((20,20,3),clock().frame(stamp),np.uint8)
            with patch('lyricsync.audit_service.Video',IndexedVideo),patch('lyricsync.audit.Video',IndexedVideo),patch('lyricsync.audit_service.AuditClock.build',return_value=clock()),patch('lyricsync.audit_service.scan',return_value=([],{})),patch('lyricsync.audit_service.make_ocr',return_value=FakeOCR(active=())):
                result=run_audit(video,None,d/'out',ass_path=ass,input_kind='hardsubbed',settings={'chinese_language':'chi_tra'})
            self.assertEqual(result['cues'],[])
            missing=result['unobserved_cues'][0]
            self.assertEqual(missing['text'],'');self.assertEqual(missing['expected_text'],'HELLO')
            self.assertIsNone(missing['first_frame']);self.assertIsNone(missing['last_frame'])
            self.assertTrue(missing['evidence_frames'])
            self.assertTrue(all('sample' in f['label'] and Path(f['path']).is_file() for f in missing['evidence_frames']))
            self.assertEqual(result['coverage']['chinese_candidates'],0)
            self.assertFalse(result['coverage']['complete_video_verified'])
            self.assertEqual(result['ocr_settings']['chinese_language'],'chi_tra')
            self.assertIn('参考译文（未识别）',(d/'out/report.html').read_text(encoding='utf-8'))

    def test_uncertain_window_png_is_sample_only_not_start_boundary(self):
        c=clock(tuple(i/10 for i in range(31)),end=3.1)
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);video=d/'v';video.touch();text=d/'t.txt';text.write_text('中文',encoding='utf8')
            class IndexedVideo(FakeVideo):
                def __init__(self,path):super().__init__(c.stamps);self.path=str(path);self.origin=0
                def at(self,stamp,**kwargs):return stamp,np.full((20,20,3),c.frame(stamp),np.uint8)
            with patch('lyricsync.audit_service.Video',IndexedVideo),patch('lyricsync.audit.Video',IndexedVideo),patch('lyricsync.audit_service.AuditClock.build',return_value=c),patch('lyricsync.audit_service.scan',return_value=([Visual('x','中文',1,1.4,[0,0,10,10],.99,2)],{})),patch('lyricsync.audit_service.make_ocr',return_value=FakeOCR(active=tuple(range(1,32)))):
                result=run_audit(video,text,d/'out',frame_range=[11,15])
            cue=result['cues'][0]
            self.assertIsNone(cue['first_frame']);self.assertIsNone(cue['last_frame'])
            self.assertEqual(cue['screenshot_kind'],'sample_only')
            self.assertEqual(cue['evidence_frames'][0]['label'],'sample_only')
            self.assertEqual(cue['evidence_frames'][0]['frame'],cue['representative_frame'])
            self.assertFalse(any(f['label'] in ('start_visible','end_visible') for f in cue['evidence_frames']))
            self.assertEqual(result['scope_detail']['first_frame'],11)
            self.assertEqual(result['scope_detail']['last_frame'],15)
            self.assertIn('仅扫描所选帧范围（外挂字幕预检仍需生成临时视频）',result['scope_detail']['processing_scope'])

    def test_current_baseline_policy_is_inherited_by_local_rescan(self):
        with tempfile.TemporaryDirectory() as d:
            parent,out=self.parent(d);parent['baseline_policy']={'use_current_project':True};child=self.child(parent)
            with patch('lyricsync.audit_service.Video',return_value=FakeVideo(clock().stamps)),patch('lyricsync.audit_service.AuditClock.build',return_value=clock()),patch('lyricsync.audit_service.run_audit',return_value=child) as run:
                rescan_audit(parent,out,first_frame=2,last_frame=4,cue_ids=['c'])
            self.assertTrue(run.call_args.kwargs['settings']['audit_use_current_project'])

    def test_high_confidence_does_not_override_missing_content_evidence(self):
        report=report_fixture();report['cues'][0].update(confidence=.999,content_status='ocr_completeness_unverified')
        response={'checked_ids':['c'],'issues':[{'cue_ids':['c'],'kind':'content','confidence':.999,'reason':'a suggestion only'}],
                  'pairings':[{'chinese_ids':['c'],'source_ids':['s'],'confidence':.999,'reason':'candidate correspondence'}]}
        with tempfile.TemporaryDirectory() as d:text_audit(report,d,'fake',transport=lambda *a:(response,{}))
        self.assertEqual(report['issues'][0]['kind'],'uncertain')
        self.assertEqual(report['issues'][0]['status'],'needs_review')
        self.assertEqual(report['semantic_pairs'][0]['time_delta']['status'],'needs_review')

    def test_new_lyria_current_split_merge_text_wins_with_unknown_times(self):
        from lyricsync.model import Project,Translation,Mapping
        from lyricsync.project_document import ProjectDocument,write_document
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);video=d/'v';video.touch();text=d/'original.txt';text.write_text('AABB\nCC\nDD',encoding='utf8')
            project=Project(translations=[Translation('t1','AABB'),Translation('t2','CC'),Translation('t3','DD')],
                mappings=[Mapping('split-a',['t1'],text_segments=[{'id':'t1','start':0,'end':2}],text_override='人工第一半'),
                          Mapping('split-b',['t1'],text_segments=[{'id':'t1','start':2,'end':4}],text_override='人工第二半'),
                          Mapping('merged',['t2','t3'],text_override='人工合并结果')])
            path=d/'current-project.lyria';write_document(ProjectDocument(project,revision=7).snapshot(),path)
            cues=[dict(strong('c'+str(i)),text=t,start=.04+i*.1,end=.15+i*.1) for i,t in enumerate(('人工第一半','人工第二半','人工合并结果'))]
            with patch('lyricsync.audit.Video',return_value=FakeVideo(clock().stamps)):
                report=local_audit(video,text,d/'out',project_path=path,settings={'audit_use_current_project':True},_observed=cues,_clock=clock())
            self.assertEqual(report['expected_basis'],'project_current_chinese')
            self.assertEqual([c['text'] for c in report['expected_cues']],['人工第一半','人工第二半','人工合并结果'])
            self.assertEqual([c['text'] for c in report['translation_baseline']],['AABB','CC','DD'])
            self.assertEqual([c['text'] for c in report['project_translation_baseline']],['AABB','CC','DD'])
            self.assertEqual(report['project_reference']['revision'],7)
            self.assertEqual(report['coverage']['expected_matched'],3)
            self.assertTrue(any('未完成打轴' in w for w in report['warnings']))
            self.assertTrue(all(c['start'] is None for c in report['expected_cues']))

    def test_video_only_manual_empty_rows_never_revive_old_reference_file(self):
        from lyricsync.model import Project,Mapping
        from lyricsync.project_document import ProjectDocument,write_document
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);video=d/'v';video.touch();text=d/'different-old.txt';text.write_text('不能回填的旧译文',encoding='utf8')
            project=Project(translations=[],mappings=[Mapping('manual',[],text_override='现在手填'),Mapping('blank',[],text_override='')])
            path=d/'current.lyria';write_document(ProjectDocument(project).snapshot(),path)
            with patch('lyricsync.audit.Video',return_value=FakeVideo(clock().stamps)):
                report=local_audit(video,text,d/'out',project_path=path,settings={'audit_use_current_project':True},_observed=[dict(strong(),text='现在手填')],_clock=clock())
            self.assertEqual([c['text'] for c in report['expected_cues']],['现在手填',''])
            self.assertEqual(report['translation_baseline'][0]['text'],'不能回填的旧译文')
            self.assertEqual(report['cues'][0]['expected_texts'],['现在手填'])
            self.assertTrue(any('空白译文行' in w for w in report['warnings']))
            self.assertFalse(any('不能回填' in c['text'] for c in report['expected_cues']))

    def test_canceled_new_api_run_keeps_old_evidence_as_history_not_current(self):
        report=report_fixture();report['api']['text'].update(status='completed',checked_ids=['c']);report['coverage']={'semantic_checked':1}
        report['issues']=[{'origin':'text_api','cue_ids':['c'],'kind':'uncertain','confidence':.5,'reason':'old evidence','status':'needs_review'}]
        old=copy.deepcopy(report['issues'])
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(InterruptedError):text_audit(report,d,'fake',cancel=lambda:True)
        self.assertEqual(report['previous_text_audits'][0]['issues'],old)
        self.assertFalse(report['previous_text_audits'][0]['is_current'])
        self.assertEqual(report['api']['text']['checked_ids'],[])
        self.assertEqual(report['api']['text']['status'],'cancelled')
        self.assertEqual(report['coverage']['semantic_checked'],0)


if __name__=='__main__':unittest.main()
