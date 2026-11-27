import wave
from copy import deepcopy
import pytest
from lyricsync import local_alignment as la,dev9_long_audio as windows
from test_v021_alignment_core import cursor_fixture

def setup(tmp_path,monkeypatch,duration=90.,ranges=((0.,30.),(30.,60.),(60.,90.))):
    req,base=cursor_fixture(tmp_path)
    with wave.open(req['audio_wav'],'wb') as f:
        f.setparams((1,2,16000,0,'NONE','not compressed'));f.writeframes(b'\x00\x10'*round(duration*16000))
    base['duration']=duration
    base['words'][2].update(start_time=None,end_time=None)
    monkeypatch.setattr(windows,'silence_intervals',lambda p:[])
    monkeypatch.setattr(windows,'disjoint_speech_windows',lambda *a,**k:iter(ranges))
    return req,base

def output(req,base,values):
    with wave.open(req['audio_wav'],'rb') as w:d=w.getnframes()/w.getframerate()
    words=[];counts=[]
    for row in req['rows']:
        spans=values.get(row['id'],[(None,None)])
        for a,b in spans:words.append(dict(text=row['text'],start_time=a,end_time=b))
        counts.append(len(spans))
    return dict(base,duration=d,words=words,tokenization=dict(row_word_counts=counts))

def test_both_lyric_margins_and_immutable_context(tmp_path,monkeypatch):
    req,base=setup(tmp_path,monkeypatch,12.,((0.,12.),));before=deepcopy(base)
    def worker(r,*a,**k):
        assert [x['id'] for x in r['rows']]==['one','two','three']
        return output(r,base,{'one':[(1.5,2.5)],'two':[(5.,6.)],'three':[(10.,11.)]})
    monkeypatch.setattr(la,'run_worker',worker)
    out=la.refine_alignment_with_cursor(req,base,'mock')
    assert out['words'][0]['start_time']==1.
    assert out['words'][1]['start_time']==5.
    assert base==before
    assert out['chunking']['cursor_refinement']['reports'][0]['context_only_row_ids']==['one']

def test_base_boundary_pending_expands_next_audio_to_sentence_start(tmp_path,monkeypatch):
    req,base=setup(tmp_path,monkeypatch);calls=[]
    def worker(r,*a,**k):
        with wave.open(r['audio_wav'],'rb') as w:calls.append(w.getnframes()/16000)
        return output(r,base,{'two':[(20.,31.)]} if len(calls)==1 else {'two':[(20.,35.)],'three':[(40.,41.)]})
    monkeypatch.setattr(la,'run_worker',worker)
    out=la.refine_alignment_with_cursor(req,base,'mock')
    reports=out['chunking']['cursor_refinement']['reports']
    assert reports[0]['pending_boundary_ids']==['two']
    assert reports[1]['start']==0. # necessary preceding base window retained, not 28 s
    assert calls[:2]==[32.,62.]
    assert out['words'][1]['start_time']==20.
    assert reports[0]['cursor_after']==1 and reports[1]['cursor_after']==3

def test_final_window_gets_bounded_review_no_forced_acceptance(tmp_path,monkeypatch):
    req,base=setup(tmp_path,monkeypatch,12.,((0.,12.),));calls=[]
    def worker(r,*a,**k):
        calls.append(1);return output(r,base,{'two':[(10.,11.99)]})
    monkeypatch.setattr(la,'run_worker',worker)
    out=la.refine_alignment_with_cursor(req,base,'mock')
    assert len(calls)==1
    assert out['chunking']['cursor_refinement']['reports'][-1]['status']=='no_further_context'
    assert out['words'][1]['start_time'] is None
    assert out['chunking']['cursor_refinement']['pending_boundary_ids']==['two']

def test_start_edge_gets_local_backward_review(tmp_path,monkeypatch):
    req,base=setup(tmp_path,monkeypatch,90.,((30.,60.),));calls=[]
    # No preceding anchor: ensures crop start is not pulled to time zero by context.
    base['words'][0].update(start_time=None,end_time=None)
    def worker(r,*a,**k):
        calls.append(1)
        return output(r,base,{'one':[(.01,1.)]} if len(calls)==1 else {'one':[(6.,7.)],'two':[(8.,9.)],'three':[(10.,11.)]})
    monkeypatch.setattr(la,'run_worker',worker)
    out=la.refine_alignment_with_cursor(req,base,'mock')
    reports=out['chunking']['cursor_refinement']['reports']
    assert reports[0]['start']==28.
    assert reports[1]['is_boundary_retry'] and reports[1]['start']<28.
    assert out['words'][0]['start_time'] is not None

def test_85_second_limit_never_silently_crops_required_context(tmp_path,monkeypatch):
    req,base=setup(tmp_path,monkeypatch,120.,((90.,120.),))
    monkeypatch.setattr(la,'run_worker',lambda *a,**k:pytest.fail('required context exceeds cap'))
    out=la.refine_alignment_with_cursor(req,base,'mock')
    report=out['chunking']['cursor_refinement']['reports'][0]
    assert report['status']=='model_limit_incomplete' and report['required_seconds']==120.
    assert out['words']==base['words']

@pytest.mark.parametrize('near_tail',[False,True])
def test_fast_lyrics_only_tail_cluster_is_suspect(tmp_path,monkeypatch,near_tail):
    req,base=setup(tmp_path,monkeypatch,12.,((0.,12.),))
    # Two lexical tokens in one row, both 50 ms: fast singing alone is not suspicious.
    req['rows'][1]['text']='fast words'
    base['words'][1]['text']='fast words'
    def worker(r,*a,**k):
        t=11.6 if near_tail else 5.
        result=output(r,base,{'two':[(t,t+.05),(t+.06,t+.11)],'three':[(8.,9.)]})
        idx=next(i for i,w in enumerate(result['words']) if w['text']=='fast words')
        result['words'][idx]['text']='fast';result['words'][idx+1]['text']='words'
        return result
    monkeypatch.setattr(la,'run_worker',worker)
    out=la.refine_alignment_with_cursor(req,base,'mock')
    reports=out['chunking']['cursor_refinement']['reports']
    if near_tail:
        assert any('tail_word_cluster_compressed' in x.get('diagnostics',[]) for x in reports[0]['rejected'])
        assert out['words'][1]['start_time'] is None
    else:
        assert not reports[0]['pending_boundary_ids']
        assert out['words'][1]['start_time']==5.
