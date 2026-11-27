"""Sentence QC contracts: real local video, stubbed OCR/transport, never paid API."""
import json
from pathlib import Path
import urllib.error
import pytest
from lyricsync.audit_sentences import assess_samples, text_differences, run_sentence_audit
from lyricsync.model import Detection


def sample(text='Hello',confidence=.99,path='frame.png',frame=1):
    return dict(text=text,confidence=confidence,path=path,frame=frame,seconds=frame/30)


def test_sentence_evidence_never_promotes_missing_images_or_low_confidence():
    for samples in ([sample()], [sample(path=None),sample(path=None)], [sample(confidence=.5),sample(confidence=.5)]):
        cue=assess_samples({'expected_text':'Hello','samples':samples})
        assert cue['content_status']=='unable_to_judge' and not cue['counts_as_pass']
    cue=assess_samples({'expected_text':'Hello','samples':[sample(),sample(frame=2)]})
    assert cue['content_status']=='sample_text_matches' and not cue['counts_as_pass']
    cue=assess_samples({'expected_text':'Hello','samples':[sample(),sample('Goodbye',frame=2)]})
    assert cue['content_status']=='unable_to_judge'


def test_concrete_differences_preserve_punctuation_and_spacing():
    assert any('缺字' in s for s in text_differences('Hello!', 'Hello'))
    assert any('多字' in s for s in text_differences('Hello', 'Hello world'))
    assert any('替换' in s for s in text_differences('Hello', 'Hallo'))


@pytest.fixture
def sentence_fixture(tmp_path,monkeypatch):
    import av,numpy as np
    video=tmp_path/'source.mp4'
    with av.open(str(video),'w') as container:
        stream=container.add_stream('mpeg4',rate=10);stream.width=320;stream.height=180;stream.pix_fmt='yuv420p'
        for i in range(20):
            image=np.full((180,320,3),30+i,dtype=np.uint8)
            frame=av.VideoFrame.from_ndarray(image,format='bgr24')
            for packet in stream.encode(frame):container.mux(packet)
        for packet in stream.encode():container.mux(packet)
    ref=tmp_path/'expected.srt'
    ref.write_text('\n\n'.join(f'{i}\n00:00:00,200 --> 00:00:01,200\nHello' for i in range(1,66)),encoding='utf-8')
    class OCR:
        def recognize(self,image): return [Detection('Hello',[1,1,100,30],.99)]
    monkeypatch.setattr('lyricsync.audit_sentences.make_ocr',lambda *a,**k:OCR())
    roi={'box':[0,0,320,80],'origin':'user','confirmed':True}
    return video,ref,roi


def test_65_repeated_lines_stay_65_ordered_sentences_and_unmatched_separate(sentence_fixture,tmp_path):
    video,ref,roi=sentence_fixture
    report=run_sentence_audit(video,ref,tmp_path/'result',chinese_roi=roi)
    assert len(report['cues'])==65
    assert [c['number'] for c in report['cues']]==list(range(1,66))
    assert len({c['id'] for c in report['cues']})==65
    assert all(c['content_status']=='sample_text_matches' for c in report['cues'])
    assert len(report['unmatched_cues'])==1
    assert report['coverage']['sentences_checked']==65
    assert not report['coverage']['frame_boundaries_verified']
    assert all(c['first_frame'] is None for c in report['cues'])
    assert all(len(c['samples'])==3 and Path(c['screenshot']).is_file() for c in report['cues'])
    assert report['performance']['total_seconds']>0
    assert not any(c['counts_as_pass'] for c in report['cues'])


def test_cancel_retains_actual_samples_and_unchecked_sentence_list(sentence_fixture,tmp_path):
    video,ref,roi=sentence_fixture;stopped=[False]
    def progress(p,m):
        if '抽样识别 1/' in m:stopped[0]=True
    output=tmp_path/'cancel'
    with pytest.raises(InterruptedError):
        run_sentence_audit(video,ref,output,chinese_roi=roi,cancel=lambda:stopped[0],progress=progress)
    report=json.loads((output/'report.json').read_text(encoding='utf8'))
    assert report['local_status']=='cancelled'
    assert len(report['cues'])==65 and len(report['cues'][0]['samples'])==1
    assert report['coverage']['sentences_partial']==65
    assert report['coverage']['sentences_unchecked']==65
    assert report['scope_detail']['unchecked_sentence_numbers']==list(range(1,66))


def test_api_failure_keeps_local_results_and_precise_unchecked_scope(sentence_fixture,tmp_path):
    video,ref,roi=sentence_fixture
    source={'box':[0,90,320,170],'origin':'user','confirmed':True}
    def semantic(report,*a,**k):
        report['api']['text'].update(status='failed',checked_ids=[report['cues'][0]['id']])
        raise urllib.error.HTTPError('https://private.invalid/secret',401,'SECRET',{},None)
    output=tmp_path/'api'
    with pytest.raises(urllib.error.HTTPError):
        run_sentence_audit(video,ref,output,chinese_roi=roi,source_roi=source,mode='text',semantic_runner=semantic)
    report=json.loads((output/'report.json').read_text(encoding='utf8'))
    assert report['coverage']['sentences_checked']==65 and report['local_status']=='completed'
    assert len(report['api']['text']['unchecked_ids'])==64
    assert '密钥无效' in report['api']['text']['reason']
    assert 'SECRET' not in json.dumps(report) and 'private.invalid' not in json.dumps(report)


def test_single_character_noise_not_added_as_unmatched(sentence_fixture,tmp_path,monkeypatch):
    video,ref,roi=sentence_fixture
    class OCR:
        def recognize(self,image): return [Detection('1',[1,1,12,12],.99)]
    monkeypatch.setattr('lyricsync.audit_sentences.make_ocr',lambda *a,**k:OCR())
    report=run_sentence_audit(video,ref,tmp_path/'noise',chinese_roi=roi)
    assert report['unmatched_cues']==[] and report['suppressed_noise']>0
    assert all(c['content_status']=='unable_to_judge' for c in report['cues'])


def test_sentence_local_recheck_does_not_launch_frame_scan(sentence_fixture,tmp_path):
    from lyricsync.audit_service import run_audit,rescan_audit
    video,ref,roi=sentence_fixture
    output=tmp_path/'parent'
    parent=run_audit(video,ref,output,chinese_roi=roi,settings={'audit_strategy':'sentence'})
    before=parent['cues'][0]['text']
    result=rescan_audit(parent,output,first_frame=3,last_frame=11,cue_ids=[parent['cues'][0]['id']])
    assert result['report']['strategy']=='sentence'
    assert result['report']['scope_detail']['frame_range']==[3,11]
    assert parent['cues'][0]['text']==before
    assert result['rescan']['counts_as_pass'] is False

def test_association_keeps_whole_detected_lines_and_handles_simultaneous_subtitles():
    from lyricsync.audit_sentences import sentence_observation
    observation={**sample(), 'detections':[{'text':'Hello','confidence':.99}, {'text':'Good night','confidence':.97}]}
    chosen=sentence_observation(observation,'Good night')
    assert chosen['text']=='Good night' and len(chosen['detections'])==2
    extra={**sample(),'detections':[{'text':'Hello world','confidence':.99}]}
    assert sentence_observation(extra,'Hello')['text']=='Hello world'


def test_outside_video_and_untimed_rows_are_not_clamped_into_last_frame(sentence_fixture,tmp_path):
    video,ref,roi=sentence_fixture
    ref.write_text('1\n00:01:00,000 --> 00:01:02,000\nHello',encoding='utf8')
    report=run_sentence_audit(video,ref,tmp_path/'outside',chinese_roi=roi)
    cue=report['cues'][0]
    assert cue['samples']==[] and cue['content_status']=='unable_to_judge' and not cue['checked']
    assert report['coverage']['sentences_unchecked']==1


def test_local_recheck_keeps_original_and_manual_mark_updates_only_selected(sentence_fixture,tmp_path):
    from lyricsync.audit_service import run_audit,rescan_audit,record_audit_review
    video,ref,roi=sentence_fixture;output=tmp_path/'recheck-mark'
    parent=run_audit(video,ref,output,chinese_roi=roi,settings={'audit_strategy':'sentence'})
    selected=parent['cues'][0]['id']
    record_audit_review(parent,output,[selected],'reviewed','looked at original')
    rescan_audit(parent,output,first_frame=3,last_frame=11,cue_ids=[selected])
    assert parent['cues'][0]['review_status']=='reviewed'
    assert parent['cues'][0]['review_note']=='looked at original'
    assert parent['cues'][0]['latest_recheck']['observation']['samples']
    assert 'latest_recheck' not in parent['cues'][1]
    assert not parent['cues'][0]['latest_recheck']['counts_as_pass']


def test_changed_project_media_is_disclosed_and_missing_source_is_not_green(sentence_fixture,tmp_path):
    from lyricsync.model import Project,Mapping,Translation
    from lyricsync.project_document import ProjectDocument,write_document
    video,ref,roi=sentence_fixture
    project=Project(inputs={'video':str(tmp_path/'different-original.mp4')},translations=[Translation('t','Hello')],
                    mappings=[Mapping('m',['t'],start=.2,end=1.2)])
    reference=tmp_path/'reference.lyria';write_document(ProjectDocument(project).snapshot(),reference)
    report=run_sentence_audit(video,ref,tmp_path/'binding',chinese_roi=roi,project_path=reference,settings={'audit_use_current_project':True})
    assert not report['reference_media']['same_file_path'] and not report['reference_media']['edit_timeline_verified']
    assert any('并非同一文件' in w for w in report['warnings'])
    assert report['cues'][0]['content_status']=='sample_text_matches'
    assert report['cues'][0]['overall_status']=='unable_to_judge'
    assert report['performance']['total_seconds']>=report['performance']['report_write_seconds']


from test_qt_controller import application,bridge,cue,wait_for


@pytest.mark.parametrize('strategy',['sentence','frame'])
def test_controller_defaults_quick_but_preserves_explicit_advanced(application,bridge,tmp_path,monkeypatch,strategy):
    import lyricsync.audit_service as service
    cue(bridge,'Hello');seen=[]
    bridge._qc.update(inputPath=str(tmp_path/'video.mp4'),chineseRoi={'box':[0,0,100,100],'origin':'user','confirmed':True})
    def fake(video,reference,output,**kwargs):
        seen.append(kwargs['settings']['audit_strategy'])
        return {'inputs':{'video':video},'cues':[],'issues':[],'api':{'text':{'status':'disabled'}},'coverage':{},'stage':{}}
    monkeypatch.setattr(service,'run_audit',fake)
    payload={} if strategy=='sentence' else {'strategy':'frame'}
    assert bridge.command('qc.start',payload)
    wait_for(application,lambda:not bridge.jobs.active)
    assert seen==[strategy]
    assert bridge.state['qc']['performance']['total_seconds']>=0


def test_nonfirst_local_recheck_keeps_original_number_and_controller_identity(sentence_fixture,tmp_path,bridge):
    from lyricsync.audit_service import run_audit,rescan_audit,record_audit_review
    video,ref,roi=sentence_fixture
    ref.write_text('1\n00:00:00,200 --> 00:00:00,600\nHello\n\n2\n00:00:01,200 --> 00:00:01,800\nHello',encoding='utf8')
    output=tmp_path/'nonfirst';parent=run_audit(video,ref,output,chinese_roi=roi,settings={'audit_strategy':'sentence'})
    second=parent['cues'][1];identity=second['id'];expected_ids=list(second['expected_ids'])
    record_audit_review(parent,output,[identity],'reviewed','second row')
    result=rescan_audit(parent,output,first_frame=13,last_frame=18,cue_ids=[identity])
    assert len(result['report']['cues'])==1 and result['report']['cues'][0]['number']==2
    # UI keeps original identity even if a historical child numbered locally.
    parent['cues'][1]['latest_recheck']['observation']['number']=1
    bridge._display_qc_report(parent,str(video),output)
    shown=bridge.state['qc']['cues'][1]
    assert shown['number']==2 and shown['cueId']==identity and shown['expected_ids']==expected_ids
    assert shown['reviewStatus']=='reviewed'
    assert bridge._qc_cue(identity)['number']==2
    assert parent['cues'][0]['id']!=identity and 'latest_recheck' not in parent['cues'][0]


def test_sentence_role_engine_selection_with_same_language(sentence_fixture, tmp_path, monkeypatch):
    video, ref, roi = sentence_fixture
    called = []
    class OCR:
        def recognize(self, image): return [Detection('Hello', [1, 1, 100, 30], .99)]
    def factory(options):
        called.append((options['language'], options['engine']))
        return OCR()
    monkeypatch.setattr('lyricsync.audit_sentences.make_ocr', factory)
    report = run_sentence_audit(video, ref, tmp_path/'mixed', chinese_roi=roi,
        source_roi={'box':[0,90,320,170], 'origin':'user', 'confirmed':True},
        settings={'engine':'tesseract', 'chinese_engine':'rapidocr', 'source_engine':'tesseract',
                  'chinese_language':'eng', 'source_language':'eng'})
    assert called == [('eng', 'rapidocr'), ('eng', 'tesseract')]
    assert report['ocr_settings']['chinese_engine'] == 'rapidocr'
    assert report['ocr_settings']['source_engine'] == 'tesseract'
