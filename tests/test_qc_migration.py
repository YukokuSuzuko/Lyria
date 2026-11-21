"""Migration QC contracts. Fixtures/mocked OCR are not production accuracy tests."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np

from lyricsync.audit import local_audit, text_audit, validate_audit_response, save_report, chat_request
from lyricsync.audit_service import (AuditClock, validate_region, validate_regions, refine_cues,
    timing_delta, neighbor_relation, rendered_input, run_audit, _run_render)
from lyricsync.model import Detection, Visual
from lyricsync.storage import Cache


class FakeVideo:
    def __init__(self, stamps, active=(2,3), confidence=.96):
        self.stamps=stamps;self.active=active;self.confidence=confidence
        self.info={'duration':stamps[-1]+.1,'width':20,'height':20,'fps':9999}
    def frames(self,start=0,end=None,cancel=lambda:False):
        for i,stamp in enumerate(self.stamps,1):
            if cancel():raise InterruptedError('cancel')
            if stamp>=start-1e-6 and (end is None or stamp<=end+1e-6):
                yield stamp,np.full((20,20,3),i,np.uint8)


class FakeOCR:
    def __init__(self,active=(2,3),confidence=.96):
        self.active=active;self.confidence=confidence;self.seen=[]
    @property
    def signature(self):return {'name':getattr(self,'name','fake'),'active':list(self.active),'confidence':self.confidence}
    def recognize(self,frame):
        number=int(frame[0,0,0]);self.seen.append(number)
        return [Detection('中文',[0,0,10,10],self.confidence)] if number in self.active else []


def clock(stamps=(0,.04,.11,.3,.7),end=.8):
    return AuditClock(tuple(stamps),tuple(round(x*1000) for x in stamps),tuple('1/1000' for _ in stamps),end)


def strong(identity='c',first=2,last=3,start=.04,end=.3):
    return dict(id=identity,text='中文',first_frame=first,last_frame=last,start=start,end=end,
                confidence=.96,boundary_status='adjacent_decoded_frames')


def report_fixture():
    return {'version':2,'inputs':{},'cues':[strong()],'source_cues':[dict(strong('s'),text='Hello')],
            'issues':[],'warnings':[],'scope':'test','api':{'text':{'status':'not_run','checked_ids':[]}}}


class QCFrameTests(unittest.TestCase):
    def test_one_actual_vfr_frame_not_point15_seconds(self):
        a=strong(first=4,last=4,start=.3,end=.7)
        b=strong(first=3,last=3,start=.11,end=.3)
        result=timing_delta(a,b)
        self.assertAlmostEqual(result['start_seconds'],.19)
        self.assertEqual(result['start_frames'],1)
        self.assertTrue(result['within_tolerance'])
        self.assertEqual(result['status'],'within_tolerance')
        # Two 20ms frames still exceed the tolerance even though < 0.15s.
        c=strong(first=5,last=5,start=.15,end=.17)
        d=strong(first=3,last=3,start=.11,end=.13)
        self.assertFalse(timing_delta(c,d)['within_tolerance'])
        self.assertEqual(timing_delta(c,d)['status'],'timing_suspect')
        self.assertEqual((a['first_frame'],b['first_frame']),(4,3))

    def test_reference_and_weak_ocr_never_proven_wrong(self):
        a=strong(first=4,last=4,start=.3,end=.7)
        b=strong(first=1,last=1,start=0,end=.04)
        a['confidence']=.5
        self.assertEqual(timing_delta(a,b)['status'],'needs_review')
        a['confidence']=1;b['boundary_status']='reference_only'
        self.assertEqual(timing_delta(a,b)['status'],'needs_review')
        self.assertEqual(timing_delta({'start':0,'end':1},{'start':0,'end':1})['status'],'needs_review')

    def test_local_reference_mapping_uses_pts_activation(self):
        a=strong(first=3,last=4,start=.11,end=.7)
        ref={'start':.041,'end':.301,'confidence':1,'boundary_status':'reference_only'}
        result=timing_delta(a,ref,clock())
        self.assertEqual((result['start_frames'],result['end_frames']),(0,0))
        self.assertEqual(result['status'],'needs_review')

    def test_true_gap_and_overlap_inclusive_ends(self):
        a=strong(first=2,last=3);b=strong('b',4,5,.3,.8)
        self.assertEqual(neighbor_relation(a,b)['gap_frames'],0)
        b['first_frame']=3
        self.assertEqual(neighbor_relation(a,b)['overlap_frames'],1)
        b['first_frame']=5
        self.assertEqual(neighbor_relation(a,b)['gap_frames'],1)
        b['last_frame']=None
        self.assertEqual(neighbor_relation(a,b)['status'],'needs_review')

    def test_neighbor_ocr_exact_frames_raw_pts_and_confidence(self):
        v=FakeVideo(clock().stamps)
        visual=Visual('x','中文',.06,.29,[0,0,10,10],.96,2)
        provider=FakeOCR()
        rows=refine_cues(v,[visual],clock(),{'box':[0,0,20,20]}, {'sample_fps':3},provider=provider)
        cue=rows[0]
        self.assertEqual((cue['first_frame'],cue['last_frame']),(2,3))
        self.assertEqual((cue['start'],cue['end']),(.04,.3))
        self.assertEqual(cue['boundary_status'],'adjacent_decoded_frames')
        self.assertEqual(cue['boundary_evidence']['start']['adjacent_nonmatching']['frame'],1)
        self.assertEqual(cue['boundary_evidence']['end']['adjacent_nonmatching']['pts'],300)
        self.assertEqual(cue['boundary_evidence']['end']['visible']['time_base'],'1/1000')
        self.assertEqual(cue['raw_confidences'],[.96,.96])
        self.assertIn(4,provider.seen)
        self.assertTrue(all(n in provider.seen for n in (1,2,3,4)))

    def test_low_confidence_or_no_match_retains_uncertainty(self):
        v=FakeVideo(clock().stamps)
        visual=Visual('x','中文',.04,.3,[0,0,10,10],.6,2)
        cue=refine_cues(v,[visual],clock(),{'box':[0,0,20,20]},{},provider=FakeOCR(confidence=.6))[0]
        self.assertEqual(cue['boundary_status'],'needs_review')
        self.assertEqual(cue['confidence'],.6)
        blank=refine_cues(v,[visual],clock(),{'box':[0,0,20,20]},{},provider=FakeOCR(active=()))[0]
        self.assertIsNone(blank['first_frame'])
        self.assertEqual(blank['boundary_status'],'needs_review')
        self.assertEqual(blank['boundary_evidence']['start']['status'],'unresolved')

    def test_missing_last_duration_not_average_fps_fabrication(self):
        c=clock(end=None);v=FakeVideo(c.stamps)
        visual=Visual('x','中文',.3,.8,[0,0,10,10],.96,2)
        cue=refine_cues(v,[visual],c,{'box':[0,0,20,20]},{},provider=FakeOCR(active=(4,5)))[0]
        self.assertEqual(cue['last_frame'],5)
        self.assertIsNone(cue['end'])
        self.assertEqual(cue['boundary_status'],'needs_review')

    def test_cancel_before_ocr_and_invalid_clock(self):
        with self.assertRaises(InterruptedError):
            refine_cues(FakeVideo(clock().stamps),[Visual('x','中文',.04,.3,[0,0,10,10],.96,2)],clock(),{'box':[0,0,20,20]},{},provider=FakeOCR(),cancel=lambda:True)
        with self.assertRaises(ValueError):clock(stamps=(0,.1,.1))
        with self.assertRaises(ValueError):clock().frame(.05)


class QCRegionAndPathTests(unittest.TestCase):
    def test_separate_confirmed_regions(self):
        zh={'box':[0,10,20,20],'confirmed':True,'origin':'user'}
        src={'box':[0,0,20,10],'confirmed':True,'origin':'candidate'}
        self.assertEqual(validate_regions(zh,src,20,20,mode='text'),(zh,src))
        for bad in (None, {'box':[0,0,20,10],'confirmed':False}, {'box':[0,0,21,10],'confirmed':True}, {'box':[False,0,20,10],'confirmed':True}):
            with self.assertRaises(ValueError):validate_region(bad,20,20)
        with self.assertRaises(ValueError):validate_regions(zh,zh,20,20,mode='text')
        self.assertEqual(validate_regions(None,None,20,20,mode='local')[0]['origin'],'full_frame')

    def test_missing_paths_reject_before_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'out'
            with self.assertRaises(ValueError):run_audit(Path(d)/'missing.mp4',Path(d)/'none.txt',out)
            self.assertFalse(out.exists())

    def test_text_mode_requires_service_and_never_silently_local(self):
        with tempfile.TemporaryDirectory() as d:
            v=Path(d)/'v';t=Path(d)/'t';v.touch();t.touch()
            with self.assertRaisesRegex(ValueError,'语义服务'):run_audit(v,t,Path(d)/'out',mode='text')

    def test_hardlink_report_outputs_protect_source(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);source=d/'original';source.write_bytes(b'original')
            for name in ('report.json','report.html','audit.cache.sqlite'):
                out=d/name.replace('.','_');out.mkdir();os.link(source,out/name)
                with self.assertRaises(ValueError):
                    local_audit(source,source,out)
                self.assertEqual(source.read_bytes(),b'original')
    def test_symlink_report_outputs_protect_source(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);source=d/'original';source.write_bytes(b'original')
            out=d/'links';out.mkdir()
            try:
                (out/'report.json').symlink_to(source)
            except OSError as exc:
                if getattr(exc, 'winerror', None) == 1314:
                    self.skipTest('Windows symbolic-link privilege is unavailable')
                raise
            with self.assertRaises(ValueError):local_audit(source,source,out)
            self.assertEqual(source.read_bytes(),b'original')

    def test_parse_confidence_one_does_not_mean_visual_verified(self):
        root=Path(__file__).resolve().parents[1]
        video=root/'examples/synthetic_mv.mp4'
        if not video.is_file():self.skipTest('synthetic media unavailable')
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);translation=d/'translation.txt';translation.write_text('你好',encoding='utf8')
            ass=d/'input.ass';ass.write_text(ASS,encoding='utf8')
            report=local_audit(video,translation,d/'out',ass_path=ass)
            self.assertEqual(report['stage']['kind'],'ass_parse_only')
            self.assertEqual(report['coverage']['chinese_adjacent_boundaries'],0)
            self.assertEqual(report['cues'][0]['parse_confidence'],1)
            self.assertIsNone(report['cues'][0]['visual_confidence'])
            self.assertFalse(report['coverage']['complete_video_verified'])
            self.assertNotIn('image',report['api'])


class QCSemanticTests(unittest.TestCase):
    def test_only_text_timeline_neighbors_no_media(self):
        report=report_fixture();c=report['cues'][0]
        c.update(screenshot='PRIVATE_IMAGE',frames=['PRIVATE_BYTES'],image_url='PRIVATE_URL',
                 raw_confidences=[.96,.91],evidence_frames=[{'path':'PRIVATE_FRAME'}],
                 boundary_evidence={'start':{'visible':{'frame':2,'seconds':.04,'pts':40,'time_base':'1/1000','image':'PRIVATE_IMAGE'}}})
        report['inputs']={'video':'PRIVATE_VIDEO'}
        report['source_cues'][0].update(screenshot='PRIVATE_SOURCE')
        calls=[]
        def transport(endpoint,key,body):
            calls.append(body)
            self.assertNotIn('PRIVATE_',json.dumps(body))
            self.assertNotIn('SECRET',json.dumps(body))
            payload=json.loads(body['messages'][1]['content'])
            self.assertEqual(payload['source_cues'][0]['id'],'s')
            self.assertEqual(payload['cues'][0]['first_frame'],2)
            self.assertEqual(payload['cues'][0]['boundary_pts']['start']['visible']['pts'],40)
            self.assertTrue(all(type(m['content']) is str for m in body['messages']))
            return {'checked_ids':['c'],'issues':[], 'pairings':[dict(chinese_ids=['c'],source_ids=['s'],confidence=.94,reason='Meaning corresponds')]},{}
        with tempfile.TemporaryDirectory() as d:
            text_audit(report,d,'SECRET',transport=transport)
            self.assertEqual(report['semantic_pairs'][0]['time_delta']['computed_by'],'local')
            self.assertEqual(report['semantic_pairs'][0]['time_delta']['start_frames'],0)
            text_audit(report,d,'SECRET',transport=transport)
            self.assertEqual(len(calls),1)

    def test_semantic_rejects_unknown_reference_and_invented_timing(self):
        value={'checked_ids':['c'],'issues':[], 'pairings':[dict(chinese_ids=['c'],source_ids=['missing'],confidence=.9,reason='test')]}
        with self.assertRaises(ValueError):validate_audit_response(value,['c'],['s'])
        value['pairings'][0]['source_ids']=['s'];value['pairings'][0]['start_frame']=40
        with self.assertRaises(ValueError):validate_audit_response(value,['c'],['s'])
        with self.assertRaises(ValueError):validate_audit_response('{"checked_ids":[],"checked_ids":["c"],"issues":[]}', ['c'])

    def test_lowconfidence_content_issue_becomes_uncertainty(self):
        report=report_fixture();report['cues'][0]['confidence']=.4
        with tempfile.TemporaryDirectory() as d:
            text_audit(report,d,'fake',transport=lambda *a:({'checked_ids':['c'],'issues':[{'cue_ids':['c'],'kind':'content','confidence':1,'reason':'different'}]},{}))
        self.assertEqual(report['issues'][0]['kind'],'uncertain')
        self.assertEqual(report['issues'][0]['status'],'needs_review')
        self.assertTrue(report['issues'][0]['weak_ocr_evidence'])

    def test_api_cancellation_and_budget_states(self):
        from lyricsync.api_client import BudgetLimit
        with tempfile.TemporaryDirectory() as d:
            report=report_fixture()
            with self.assertRaises(InterruptedError):text_audit(report,d,'fake',cancel=lambda:True)
            self.assertEqual(report['api']['text']['status'],'cancelled')
            with self.assertRaises(BudgetLimit):text_audit(report,d,'fake',maximum=0)
            self.assertEqual(report['api']['text']['status'],'budget_exhausted')
            self.assertEqual(report['api']['text']['budget']['choices'][0]['maximum'],100)

    def test_custom_endpoint_timeout_stops_once_and_resume_does_not_resend(self):
        from lyricsync.api_client import ApiRequestError
        report=report_fixture();calls=[]
        settings={'api_provider':'custom','api_base_url':'https://example.invalid/v1'}
        def transport(endpoint,key,body):
            calls.append(endpoint);raise TimeoutError('private token SECRET')
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ApiRequestError) as failed:
                text_audit(report,d,'SECRET',settings=settings,transport=transport)
            self.assertEqual(failed.exception.code,'timeout')
            self.assertEqual(calls,['https://example.invalid/v1/chat/completions'])
            self.assertEqual([r['status'] for r in report['api_records']],['timeout'])
            # Re-open the persisted report/cache, not merely an in-memory flag.
            restored=json.loads((Path(d)/'report.json').read_text('utf-8'))
            with self.assertRaises(ApiRequestError) as resumed:
                text_audit(restored,d,'SECRET',settings=settings,transport=transport)
            self.assertEqual(resumed.exception.code,'unknown_outcome')
            self.assertEqual(len(calls),1)
            self.assertEqual([r['status'] for r in restored['api_records']],['timeout'])
            for result in (report,restored):
                self.assertNotIn('SECRET',json.dumps(result))
                self.assertNotIn('private token',json.dumps(result))

    def test_custom_endpoint_explicit_http_failures_keep_four_attempt_budget(self):
        from urllib.error import HTTPError
        from lyricsync.api_client import ApiRequestError,BudgetLimit
        for status,expected in [(429,'rate_or_quota_limit'),(500,'server_error'),(503,'server_error')]:
            with self.subTest(status=status),tempfile.TemporaryDirectory() as d:
                report=report_fixture();calls=[]
                def transport(endpoint,key,body):
                    calls.append(endpoint);raise HTTPError(endpoint,status,'private token SECRET',{},None)
                with self.assertRaises(ApiRequestError) as failed:
                    text_audit(report,d,'SECRET',settings={'api_provider':'custom','api_base_url':'https://example.invalid/v1'},transport=transport)
                self.assertEqual(failed.exception.code,expected)
                self.assertEqual(calls,['https://example.invalid/v1/chat/completions']*4)
                self.assertEqual([r['status'] for r in report['api_records']],[expected]*4)
                self.assertNotIn('SECRET',json.dumps(report))
                self.assertNotIn('private token',json.dumps(report))
        # A smaller global allowance wins over the default four-attempt retry cap.
        with tempfile.TemporaryDirectory() as d:
            report=report_fixture();calls=[]
            def limited(endpoint,key,body):
                calls.append(endpoint);raise HTTPError(endpoint,429,'rate limited',{},None)
            with self.assertRaises(BudgetLimit):
                text_audit(report,d,'SECRET',maximum=2,transport=limited)
            self.assertEqual(len(calls),2)
            self.assertEqual(len(report['api_records']),2)
            self.assertEqual(report['api']['text']['budget']['used'],2)

    def test_legacy_transport_refuses_image_message(self):
        with self.assertRaises(ValueError):chat_request('https://example.invalid/v1/chat/completions','fake',{'model':'x','messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'abc'}}]}]})


ASS='''[Script Info]
ScriptType: v4.00+
PlayResX: 160
PlayResY: 90
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,22,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,1,1,1,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:00.80,Default,,0,0,0,,HELLO
'''


class QCRenderTests(unittest.TestCase):
    def test_hardsubbed_never_renders_even_when_ass_supplied(self):
        with tempfile.TemporaryDirectory() as d:
            video=Path(d)/'video.mp4';video.touch()
            with patch('lyricsync.audit_service._run_render') as render:
                with rendered_input(video,Path(d)/'unused.ass',input_kind='hardsubbed') as (path,stage):
                    self.assertEqual(path,video);self.assertFalse(stage['rendered'])
                render.assert_not_called()

    def test_render_cancellation_and_failure_clean_temp_files(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);video=d/'v.mp4';video.touch();ass=d/'a.ass';ass.write_text(ASS)
            for error in (InterruptedError('cancel'),RuntimeError('failure')):
                def fail(command,folder,target,*args):
                    target.write_bytes(b'partial');raise error
                with patch('lyricsync.audit_service.ffmpeg_executable',return_value='/mock-ffmpeg'),patch('lyricsync.audit_service._run_render',side_effect=fail):
                    with self.assertRaises(type(error)):
                        with rendered_input(video,ass,input_kind='raw',temp_parent=d):pass
                self.assertEqual(sorted(p.name for p in d.iterdir()),['a.ass','v.mp4'])

    def test_cancellation_during_render_consumer_cleans_successful_temp(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);video=d/'v.mp4';video.touch();ass=d/'a.ass';ass.write_text(ASS)
            def complete(command,folder,target,*args):target.write_bytes(b'finished temp')
            with patch('lyricsync.audit_service.ffmpeg_executable',return_value='/mock-ffmpeg'),patch('lyricsync.audit_service._run_render',side_effect=complete):
                with self.assertRaises(InterruptedError):
                    with rendered_input(video,ass,input_kind='raw',temp_parent=d) as (path,stage):
                        self.assertTrue(path.is_file())
                        raise InterruptedError('cancel during OCR')
            self.assertEqual(sorted(p.name for p in d.iterdir()),['a.ass','v.mp4'])

    def test_running_process_cancel_and_timeout_reaped(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);target=d/'out';command=[sys.executable,'-c','import time; time.sleep(20)']
            started=time.monotonic()
            with self.assertRaises(InterruptedError):_run_render(command,d,target,lambda:time.monotonic()-started>.1,5,100)
            self.assertLess(time.monotonic()-started,3)
            with self.assertRaises(TimeoutError):_run_render(command,d,target,lambda:False,.1,100)

    @unittest.skipUnless(shutil.which('ffmpeg'),'system FFmpeg unavailable')
    def test_real_ffmpeg_libass_render_and_cleanup_input_immutable(self):
        from lyricsync.vision import Video
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);video=d/'raw.mkv';ass=d/'外部字幕.ass';ass.write_text(ASS,encoding='utf8')
            result=subprocess.run([shutil.which('ffmpeg'),'-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=black:s=160x90:r=10:d=1','-c:v','ffv1',str(video)],capture_output=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr.decode())
            before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (video,ass)}
            with rendered_input(video,ass,input_kind='raw',temp_parent=d,timeout=20) as (rendered,stage):
                self.assertTrue(rendered.is_file());self.assertEqual(stage['kind'],'temporary_render_precheck')
                original=Video(video);burned=Video(rendered)
                a=AuditClock.build(original);b=AuditClock.build(burned)
                self.assertEqual(a.stamps,b.stamps)
                _,raw=original.at(.2);_,frame=burned.at(.2)
                self.assertGreater(int(np.max(frame)),int(np.max(raw))+100)
                kept=rendered
            self.assertFalse(kept.exists())
            self.assertEqual(before,{p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (video,ass)})


class QCOrchestrationTests(unittest.TestCase):
    def setup_files(self,d):
        d=Path(d);video=d/'video.mp4';video.touch();translation=d/'translation.txt';translation.write_text('中文',encoding='utf8')
        return video,translation,d/'out'

    def test_bilingual_two_independent_ocr_regions_and_saved_boundaries(self):
        with tempfile.TemporaryDirectory() as d:
            video,translation,out=self.setup_files(d)
            class IndexedVideo(FakeVideo):
                def __init__(self,path):super().__init__(clock().stamps);self.path=str(path);self.origin=0
                def at(self,stamp,**kwargs):
                    number=clock().frame(stamp)
                    return stamp,np.full((20,20,3),number,np.uint8)
            languages=[];seen_regions=[]
            def scan_stub(path,options,*args,**kwargs):
                seen_regions.append(options['roi'])
                top=options['roi'][1]
                return [Visual('x','中文' if top==10 else 'Hello',.04,.3,[0,top,10,top+10],.96,2)],{}
            class RegionalOCR(FakeOCR):
                def __init__(self,name):super().__init__();self.name=name
                def recognize(self,frame):
                    self.assert_crop=(frame.shape[0],frame.shape[1])
                    out=super().recognize(frame)
                    for item in out:item.text=self.name
                    return out
            def provider(settings,refs=()):
                languages.append(settings['language'])
                return RegionalOCR('中文' if settings['language']=='chi_sim' else 'Hello')
            calls=[]
            def semantics(report,out,**kwargs):
                calls.append(report)
                self.assertEqual(report['source_cues'][0]['text'],'Hello')
                self.assertEqual(report['cues'][0]['text'],'中文')
                self.assertNotIn('无法核对中外语义',str(report['warnings']))
                self.assertTrue(all(Path(f['path']).is_file() for cue in report['cues']+report['source_cues'] for f in cue['evidence_frames']))
                report['api']['text']['status']='mock_semantics'
            with patch('lyricsync.audit_service.Video',IndexedVideo),patch('lyricsync.audit.Video',IndexedVideo), \
                    patch('lyricsync.audit_service.AuditClock.build',return_value=clock()), \
                    patch('lyricsync.audit_service.scan',side_effect=scan_stub), \
                    patch('lyricsync.audit_service.make_ocr',side_effect=provider):
                report=run_audit(video,translation,out,mode='text',
                    chinese_roi={'box':[0,10,20,20],'confirmed':True},
                    source_roi={'box':[0,0,20,10],'confirmed':True},semantic_runner=semantics)
            self.assertEqual(languages,['chi_sim','jpn+eng'])
            self.assertEqual(seen_regions,[[0,10,20,20],[0,0,20,10]])
            self.assertEqual(len(calls),1)
            self.assertEqual(report['coverage']['chinese_adjacent_boundaries'],1)
            self.assertEqual(report['coverage']['source_adjacent_boundaries'],1)
            self.assertEqual(report['coverage']['saved_evidence_frames'],4)
            self.assertEqual(report['stage']['kind'],'finished_video')
            self.assertNotIn('image',report['api'])
            self.assertEqual(json.loads((out/'report.json').read_text(encoding='utf-8'))['api']['text']['status'],'mock_semantics')
            self.assertIn('frame-000000001.png',(out/'report.html').read_text(encoding='utf-8'))

    def test_shared_project_budget_counts_qc_calls(self):
        from lyricsync.api_client import BudgetLimit
        with tempfile.TemporaryDirectory() as d:
            cache=Cache(Path(d)/'project.sqlite')
            try:
                call=cache.reserve_call('previous-review-request',1);cache.finish_call(call,'success')
                report=report_fixture()
                with self.assertRaises(BudgetLimit):
                    text_audit(report,d,'fake',maximum=1,cache=cache)
                self.assertEqual(report['api']['text']['budget']['used'],1)
                # Caller-owned connection remains open and usable.
                self.assertEqual(len(cache.records()),1)
            finally:cache.close()

    @unittest.skipUnless(shutil.which('ffmpeg'),'system FFmpeg unavailable')
    def test_actual_vfr_render_retains_decoded_stamps(self):
        from lyricsync.vision import Video
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);video=d/'vfr.mkv';ass=d/'overlay.ass';ass.write_text(ASS)
            command=[shutil.which('ffmpeg'),'-hide_banner','-loglevel','error','-f','lavfi','-i',
                     'testsrc2=s=160x90:r=30:d=1','-vf',
                     "select='eq(n,0)+eq(n,1)+eq(n,3)+eq(n,8)+eq(n,9)+eq(n,14)'",
                     '-fps_mode','passthrough','-c:v','ffv1',str(video)]
            done=subprocess.run(command,capture_output=True,timeout=20)
            self.assertEqual(done.returncode,0,done.stderr.decode())
            original=AuditClock.build(Video(video))
            differences=[round(b-a,3) for a,b in zip(original.stamps,original.stamps[1:])]
            self.assertGreater(len(set(differences)),2)
            with rendered_input(video,ass,input_kind='raw',temp_parent=d,timeout=20) as (rendered,stage):
                actual=AuditClock.build(Video(rendered))
                self.assertEqual(actual.stamps,original.stamps)
                self.assertEqual(len(actual.stamps),6)


if __name__=='__main__':unittest.main()
