from copy import deepcopy
from pathlib import Path
import wave
import pytest
from lyricsync.model import Project
from lyricsync.project_document import ProjectDocument
from lyricsync.frames import FrameIndex
from lyricsync.local_alignment import media_boundary_policy,constrain_candidate_to_media,audio_activity
from lyricsync.japanese_readings import scan_reading_inputs,prepare_reading_rows,invalid_reading_rows


def boundary(tmp_path,delta=.047578,offset=0.,same=True):
    file=tmp_path/'media.mp4';file.write_bytes(b'test')
    doc=ProjectDocument(Project(inputs={'video':str(file)},video={'content_hash':'abc'}))
    index=FrameIndex([i/30 for i in range(3300)],110.)
    audio=dict(path=str(file if same else tmp_path/'other.wav'),sha256='abc')
    info=dict(audio_start=0.,video_start=0.,audio_frame_seconds=1024/44100,video_frame_seconds=1/30,audio_end=110+delta)
    return media_boundary_policy(doc,index,audio,110+delta-offset,offset,stream_info=info)


def test_same_source_small_codec_tail_keeps_full_audio(tmp_path):
    result=boundary(tmp_path)
    assert result['strategy']=='same_source_codec_tail'
    assert result['audio_end']>result['video_end'] and result['tolerance']<.1


@pytest.mark.parametrize('delta,offset,same',[(.047578,0,False),(.5,0,True),(5,10,True)])
def test_unproven_or_large_tail_reports_values(tmp_path,delta,offset,same):
    with pytest.raises(ValueError,match='差值'):boundary(tmp_path,delta,offset,same)


def test_short_audio_and_offset_tail(tmp_path):
    assert boundary(tmp_path,-1)['strategy']=='within_video'
    assert boundary(tmp_path,.047578,10)['strategy']=='same_source_codec_tail'


def test_boundary_mapping_never_invents_frame():
    record=dict(rows=[dict(start=109.9,end=110.04,status='ready',reasons=[]),dict(start=110.01,end=110.04,status='ready',reasons=[])])
    before=deepcopy(record)
    out=constrain_candidate_to_media(record,dict(video_end=110.,strategy='same_source_codec_tail',tolerance=.06))
    assert record==before and out['rows'][0]['end']==110.
    assert out['rows'][1]['start'] is None and out['rows'][1]['status']=='missing'
    assert '110.040000000' in out['rows'][0]['reasons'][0]


def test_silence_activity_does_not_trim(tmp_path):
    file=tmp_path/'silent.wav'
    with wave.open(str(file),'wb') as stream:
        stream.setparams((1,2,16000,0,'NONE','not compressed'));stream.writeframes(bytes(64000))
    result=audio_activity(file)
    assert result['samples']==32000 and result['digital_silence'] and result['quiet_frame_fraction']==1


def test_reading_scan_decorations_numbers_elongation_faces_preserves_text():
    rows=[dict(id='a',text='桜♪'),dict(id='b',text='1億年〜'),dict(id='c',text='好き (o_o)'),dict(id='d',text='桜♪',reading='my sakura')]
    before=deepcopy(rows);result=scan_reading_inputs(rows)
    assert rows==before and result['rows'][0]['reading_input']=='桜'
    assert result['rows'][1]['reading_input']=='1億年ー'  # v0.23 ordinary elongation is nonblocking
    assert result['rows'][2]['reading_input']=='好き '  # temporary view only; source asserted unchanged above
    assert result['rows'][3]['reading_input']=='my sakura'
    assert {x['kind'] for x in result['issues']} >= {'temporary_normalization','spoken_number'}
    assert result['counts']['review']==0


def test_all_reading_issues_collected_and_hand_edit_resolves_review():
    rows=prepare_reading_rows([dict(id='a',text='〜'),dict(id='b',text='(o_o)'),dict(id='c',text='2')],lambda s:[dict(hepburn=s)])
    issues=invalid_reading_rows(rows)
    assert {x['index'] for x in issues}=={3}  # v0.23 pure symbols remain as untimed source rows
    rows[1]['reading']='oh'
    rows[1]['reading_origin']='manual'  # v0.22 explicit editor state, not string inequality as implicit approval
    assert not any(x['index']==2 for x in invalid_reading_rows(rows))


def test_ruby_and_manual_readings_preserved():
    rows=[dict(id='a',text='｜明日《あす》♪'),dict(id='b',text='何か♪',reading='manual')]
    seen=[]
    def convert(text):seen.append(text);return [dict(hepburn='asu')]
    result=prepare_reading_rows(rows,convert)
    assert seen==['あす'] and result[0]['text']==rows[0]['text'] and result[1]['reading']=='manual'

@pytest.mark.parametrize("delta", [.02,.047578,1/30+1024/44100+2/16000-1e-8])
def test_proven_tail_budget_accepts_derived_boundary(tmp_path,delta):
    assert boundary(tmp_path,delta)['strategy']=='same_source_codec_tail'

@pytest.mark.parametrize("delta", [.1, .100001, 2/30,1/30+1024/44100+2/16000+1e-8])
def test_tail_budget_is_not_blanket_100ms(tmp_path,delta):
    from lyricsync.local_alignment import MediaBoundaryError
    with pytest.raises(MediaBoundaryError) as error:boundary(tmp_path,delta)
    assert error.value.details['delta_seconds']>error.value.details['tolerance']
    assert error.value.details['choices']==['select_matching_media','keep_settings_cancel']

def test_tail_candidate_flows_through_real_initial_apply_without_fake_frame():
    from lyricsync.model import Mapping
    from lyricsync.local_alignment import candidate_from_alignment
    from lyricsync.local_models import QWEN
    from lyricsync.candidate_tracks import capture_source,install_candidate,prepare_apply,apply_plan
    from lyricsync.editing import History
    doc=ProjectDocument(Project(mappings=[Mapping('a',[],text_override='A'),Mapping('b',[],text_override='B')]))
    index=FrameIndex([0.,1.,2.],3.)
    snap=capture_source(doc,'vocal',index)
    raw=dict(model_id=QWEN.id,revision=QWEN.revision,duration=3.05,
        words=[dict(text='A',start_time=2.8,end_time=3.01),dict(text='B',start_time=3.02,end_time=3.04)])
    record=candidate_from_alignment(snap,[dict(id='a',text='A'),dict(id='b',text='B')],raw)
    record=constrain_candidate_to_media(record,dict(strategy='same_source_codec_tail',video_end=3.,tolerance=.06))
    install_candidate(doc,record)
    plan=prepare_apply(doc,record['id'],['a','b'],frame_index=index,initial_missing_time=True)
    assert not any(e['blocked'] for e in plan['entries'])
    apply_plan(doc,History(),plan,frame_index=index)
    assert doc.project.mappings[0].end==3.
    assert doc.project.mappings[1].start is None and doc.project.mappings[1].end is None

def test_qwen_local_failure_keeps_completed_and_later_instances(tmp_path,monkeypatch):
    import importlib,sys,json
    from types import SimpleNamespace
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'lyricsync'))
    worker=importlib.import_module('local_alignment_worker')
    long=importlib.import_module('dev9_long_audio')
    audio=tmp_path/'full.wav'
    with wave.open(str(audio),'wb') as w:
        w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(bytes(301*32000))
    rows=[dict(id=str(i),text='same') for i in range(3)]
    anchors=[dict(id=str(i),located=True,coverage=1.,start=i*100.,end=i*100+20) for i in range(3)]
    monkeypatch.setattr(long,'locate_lyric_rows',lambda *a:(anchors,[]))
    monkeypatch.setitem(sys.modules,'local_transcription_worker',SimpleNamespace(transcribe=lambda *a:dict(chunks=[],runtime={},elapsed_seconds=0,chunking={},duration=301.)))
    monkeypatch.setattr(worker,'ModelManager',lambda *a:SimpleNamespace(ensure_verified=lambda *a:tmp_path))
    seen=[]
    def align(r):
        row=r['rows'][0];seen.append(row['id'])
        if row['id']=='1':raise RuntimeError('synthetic isolated batch failure')
        return dict(runtime={},tokenization=dict(row_word_counts=[1]),words=[dict(text='same',start_time=1.,end_time=2.)],timings_seconds={})
    monkeypatch.setattr(worker,'align',align)
    checkpoint=tmp_path/'partial.json'
    result=worker.align_long(dict(rows=rows,audio_wav=str(audio),cache_root=str(tmp_path),model_id=worker.QWEN.id,_partial_result_path=str(checkpoint)),301.)
    assert seen==['0','1','2']
    assert len(result['raw_batches'])==2
    assert result['raw_batches'][1]['response']['words'][0]['start_time']==1.  # unshifted raw output
    assert [w['source_row_id'] for w in result['words']]==['0','1','2']
    assert result['words'][0]['start_time']==1.
    assert result['words'][1]['start_time'] is None
    assert result['words'][2]['start_time']==199.
    assert result['chunking']['batches'][1]['status']=='failed'
    assert json.loads(checkpoint.read_text(encoding='utf-8'))['words']==result['words']

def test_annotation_priority_and_parenthetical_lyrics_remain_distinct():
    from lyricsync.japanese_readings import reading_input_from_annotations
    source='夜（よる）に歌う (もう一度)'
    value,annotations,review=reading_input_from_annotations(dict(text=source))
    assert value=='よるに歌う (もう一度)' and review
    assert annotations[0]['base']=='夜'
    assert reading_input_from_annotations(dict(text=source,reading='user exact'))==('user exact',[],[])
    assert reading_input_from_annotations(dict(text='君《きみ》と｜明日《あす》'))[0]=='きみとあす'
    assert reading_input_from_annotations(dict(text='<ruby>明日<rt>あす</rt></ruby>'))[0]=='あす'
    assert reading_input_from_annotations(dict(text='<ruby><rb>明日</rb><rp>(</rp><rt>あす</rt><rp>)</rp></ruby>'))[0]=='あす'
    assert reading_input_from_annotations(dict(text='歌う（もう一度）'))[0]=='歌う（もう一度）'
    assert reading_input_from_annotations(dict(text='夜(yoru)'))[2]
    assert reading_input_from_annotations(dict(text='歌う(うたう)'))[0]=='うたう'
    assert reading_input_from_annotations(dict(text='行く（もういくよ）'))[2]  # ambiguous lyrics need review


def test_partial_reading_spans_preserve_unannotated_text_and_romaji():
    from lyricsync.japanese_readings import reading_input_from_annotations
    row=dict(id='one',text='明日Love歌う',reading_segments=[dict(start=0,end=2,text='明日',reading='あす')])
    assert reading_input_from_annotations(row)[0]=='あすLove歌う'
    called=[]
    def converter(s):called.append(s);return [dict(hepburn='converted')]
    result=prepare_reading_rows([row],converter)[0]
    assert 'Love' in result['reading'] and not any('Love' in x for x in called)
    assert result['text']==row['text']
    bad=dict(row,reading_segments=[dict(start=1,end=3,text='明日',reading='あす')])
    assert reading_input_from_annotations(bad)[0]==row['text']
    assert reading_input_from_annotations(bad)[2]

def test_explicit_full_export_contains_raw_and_never_overwrites_input(tmp_path):
    import json
    from lyricsync.local_alignment import export_alignment_diagnostics
    source=tmp_path/'original.json';source.write_text('keep')
    kwargs=dict(request=dict(rows=[dict(id='one',text='private lyrics')],runtime_python='not exported'),response=dict(words=[dict(text='private lyrics',raw_start_time=1.)]),candidate=dict(rows=[]),manifest=dict(status='partial'),protected_inputs=[source])
    with pytest.raises(ValueError):export_alignment_diagnostics(source,**kwargs)
    assert source.read_text()=='keep'
    target=tmp_path/'chosen.json';export_alignment_diagnostics(target,**kwargs)
    data=json.loads(target.read_text(encoding='utf-8'))
    assert data['contains_private_lyrics'] and data['raw_response']['words'][0]['raw_start_time']==1.
    assert 'runtime_python' not in data['request']

@pytest.mark.parametrize('retry_fails',[False,True])
def test_boundary_retry_is_bounded_and_cursor_waits(tmp_path,monkeypatch,retry_fails):
    import importlib,sys
    from types import SimpleNamespace
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'lyricsync'))
    worker=importlib.import_module('local_alignment_worker');long=importlib.import_module('dev9_long_audio')
    audio=tmp_path/'full.wav'
    with wave.open(str(audio),'wb') as w:
        w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(bytes(301*32000))
    anchors=[dict(id='one',located=True,coverage=1.,start=10.,end=20.)]
    monkeypatch.setattr(long,'locate_lyric_rows',lambda *a:(anchors,[]))
    monkeypatch.setitem(sys.modules,'local_transcription_worker',SimpleNamespace(transcribe=lambda *a:dict(chunks=[],runtime={},elapsed_seconds=0,chunking={},duration=301.)))
    monkeypatch.setattr(worker,'ModelManager',lambda *a:SimpleNamespace(ensure_verified=lambda *a:tmp_path))
    calls=[]
    def align(r):
        calls.append(r['rows'][0]['id'])
        if len(calls)==2 and retry_fails:raise RuntimeError('retry failed')
        return dict(runtime={},tokenization=dict(row_word_counts=[1]),words=[dict(text='word',start_time=3.,end_time=13.99 if len(calls)==1 else 5.)],timings_seconds={})
    monkeypatch.setattr(worker,'align',align)
    result=worker.align_long(dict(rows=[dict(id='one',text='word')],audio_wav=str(audio),cache_root=str(tmp_path),model_id=worker.QWEN.id),301.)
    assert calls==['one','one']
    assert result['chunking']['batches'][0]['cursor_after']==0
    if retry_fails:assert result['words'][0]['boundary_pending']
    else:
        assert result['chunking']['batches'][-1]['is_boundary_retry']
        assert result['chunking']['batches'][-1]['cursor_after']==1
        assert not result['words'][0].get('boundary_pending')

def cursor_fixture(tmp_path):
    from lyricsync.local_models import QWEN
    audio=tmp_path/'audio.wav'
    with wave.open(str(audio),'wb') as w:
        w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(b'\x00\x10'*(12*16000))
    rows=[dict(id=x,text=x) for x in ('one','two','three')]
    base=dict(version=1,model_id=QWEN.id,revision=QWEN.revision,duration=12.,words=[dict(text='one',start_time=1.,end_time=2.),dict(text='two',start_time=None,end_time=None),dict(text='three',start_time=10.,end_time=11.)],tokenization=dict(row_word_counts=[1,1,1]))
    return dict(rows=rows,audio_wav=str(audio),model_id=QWEN.id),base


def test_estimated_cursor_keeps_anchors_and_only_consumes_valid_instances(tmp_path,monkeypatch):
    from lyricsync import local_alignment as la
    request,base=cursor_fixture(tmp_path);before=deepcopy(base)
    def worker(r,*a,**kw):
        assert [x['id'] for x in r['rows']]==['one','two','three']
        return dict(base,words=[dict(text='one',start_time=1.,end_time=2.),dict(text='two',start_time=5.,end_time=6.),dict(text='three',start_time=10.,end_time=11.)],tokenization=dict(row_word_counts=[1,1,1]))
    monkeypatch.setattr(la,'run_worker',worker)
    result=la.refine_alignment_with_cursor(request,base,'mock')
    assert base==before
    assert [w['start_time'] for w in result['words']]==[1.,5.,10.]
    assert result['words'][1]['source_row_id']=='two' and result['words'][1]['estimated_window']
    assert result['chunking']['cursor_refinement']['cursor']==3
    assert result['chunking']['cursor_refinement']['accepted_new']==1
    assert len(result['raw_batches'])==2


@pytest.mark.parametrize('mode',['failure','next_anchor_conflict','cancel'])
def test_estimated_cursor_failure_conflict_cancel_never_overwrites_anchor(tmp_path,monkeypatch,mode):
    from lyricsync import local_alignment as la
    from lyricsync.local_alignment_runtime import PartialAlignmentError
    request,base=cursor_fixture(tmp_path)
    def worker(*a,**kw):
        if mode=='failure':raise RuntimeError('injected')
        return dict(base,words=[dict(text='one',start_time=1.,end_time=2.),dict(text='two',start_time=9.,end_time=11.5),dict(text='three',start_time=11.6,end_time=11.8)],tokenization=dict(row_word_counts=[1,1,1]))
    monkeypatch.setattr(la,'run_worker',worker)
    if mode=='cancel':
        with pytest.raises(PartialAlignmentError) as error:la.refine_alignment_with_cursor(request,base,'mock',cancel=lambda:True)
        result=error.value.result
    else:result=la.refine_alignment_with_cursor(request,base,'mock')
    assert result['words']==base['words']
    assert result['chunking']['cursor_refinement']['cursor']==1
    assert result['chunking']['cursor_refinement']['accepted_new']==0

def test_manifest_reason_categories_do_not_include_paths_or_lyrics():
    from lyricsync.local_alignment import alignment_reason_counts
    result=alignment_reason_counts(dict(rows=[dict(reasons=['audio-sha256:abc;源文件：C:/private/song.wav','private lyrics','部分字词未检出有效时间，请手动补轴','部分字词含时间插值估算，请试听核对'])]))
    assert result==dict(missing_word_time=1,interpolated_time=1)
