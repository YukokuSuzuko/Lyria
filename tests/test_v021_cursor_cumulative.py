import wave
import pytest
from lyricsync import local_alignment as la
from test_v021_cursor_context import setup,output

def fixture(tmp_path,monkeypatch,duration=100.,ranges=((20.,40.),)):
    req,base=setup(tmp_path,monkeypatch,duration,ranges)
    base['words'][0].update(start_time=None,end_time=None)
    return req,base

def test_second_review_expands_actual_first_review_by_five(tmp_path,monkeypatch):
    req,base=fixture(tmp_path,monkeypatch);calls=[];messages=[]
    def worker(r,*a,**k):
        k['progress'](50,'mock')
        with wave.open(r['audio_wav'],'rb') as w:duration=w.getnframes()/16000
        calls.append(duration)
        return output(r,base,{'one':[(22.,23.99)]} if len(calls)==1 else {})
    monkeypatch.setattr(la,'run_worker',worker)
    out=la.refine_alignment_with_cursor(req,base,'mock',progress=lambda p,m:messages.append(m))
    reports=out['chunking']['cursor_refinement']['reports'];a,b=reports[-2:]
    assert len(calls)==3 and a['is_boundary_retry'] and b['is_boundary_retry']
    assert b['start']==pytest.approx(a['start']-5.)
    assert b['end']==pytest.approx(a['end']+5.)
    assert messages==[f'候选窗 {n}（含边界复核）：mock' for n in (1,2,3)]
    assert out['words'][0]['start_time'] is None

def test_physical_edges_skip_identical_review_and_keep_raw(tmp_path,monkeypatch):
    req,base=fixture(tmp_path,monkeypatch,12.,((0.,12.),));calls=[]
    def worker(r,*a,**k):
        calls.append(1);return output(r,base,{'one':[(10.,11.99)]})
    monkeypatch.setattr(la,'run_worker',worker)
    out=la.refine_alignment_with_cursor(req,base,'mock')
    assert len(calls)==1
    assert out['chunking']['cursor_refinement']['reports'][-1]['status']=='no_further_context'
    assert len(out['raw_batches'])==2
    assert out['words'][0]['start_time'] is None

def test_cumulative_expansion_never_exceeds_85(tmp_path,monkeypatch):
    req,base=fixture(tmp_path,monkeypatch,120.,((0.,75.),));calls=[]
    def worker(r,*a,**k):
        with wave.open(r['audio_wav'],'rb') as w:d=w.getnframes()/16000
        calls.append(d)
        return output(r,base,{'one':[(75.,76.99)]} if len(calls)==1 else {})
    monkeypatch.setattr(la,'run_worker',worker)
    out=la.refine_alignment_with_cursor(req,base,'mock')
    assert len(calls)==2 and max(calls)<=85.
    report=out['chunking']['cursor_refinement']['reports'][-1]
    assert report['status']=='model_limit_incomplete' and report['required_seconds']>85.
    assert out['words'][0]['start_time'] is None

def test_failed_first_review_keeps_anchor_and_second_uses_expanded_range(tmp_path,monkeypatch):
    req,base=setup(tmp_path,monkeypatch,100.,((20.,40.),));calls=[]
    def worker(r,*a,**k):
        with wave.open(r['audio_wav'],'rb') as w:d=w.getnframes()/16000
        calls.append(d)
        if len(calls)==2:raise RuntimeError('injected')
        return output(r,base,{'two':[(40.,41.99)]} if len(calls)==1 else {})
    monkeypatch.setattr(la,'run_worker',worker)
    out=la.refine_alignment_with_cursor(req,base,'mock')
    assert len(calls)==3 and calls[2]==pytest.approx(calls[1]+5.)
    assert out['words'][0]['start_time']==1.
    assert out['words'][1]['start_time'] is None
