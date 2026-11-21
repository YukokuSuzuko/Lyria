"""Independent draft review. These failures block promotion, not frozen source tests."""
import wave
from test_v021_cursor_context import setup,output
from lyricsync import local_alignment as la,dev9_long_audio as windows


def test_pending_then_all_silent_tail_keeps_result_without_division_zero(tmp_path,monkeypatch):
    req,base=setup(tmp_path,monkeypatch,12.,((0.,6.),(6.,12.)))
    monkeypatch.setattr(windows,'silence_intervals',lambda p:[(6.,12.)])
    monkeypatch.setattr(la,'run_worker',lambda r,*a,**k:output(r,base,{'two':[(5.,6.1)]}))
    result=la.refine_alignment_with_cursor(req,base,'mock')
    assert result['words'][0]['start_time']==1.


def test_known_trailing_context_has_matching_pcm(tmp_path,monkeypatch):
    req,base=setup(tmp_path,monkeypatch,12.,((0.,6.),))
    base['words'][2].update(start_time=10.,end_time=11.)
    def worker(r,*a,**k):
        assert 'three' in [x['id'] for x in r['rows']]
        with wave.open(r['audio_wav'],'rb') as w:seconds=w.getnframes()/16000
        assert seconds>=11.,'Known trailing context at 10-11 s absent from supplied PCM'
        return output(r,base,{'one':[(1.,2.)],'two':[(3.,4.)],'three':[(10.,11.)]})
    monkeypatch.setattr(la,'run_worker',worker)
    la.refine_alignment_with_cursor(req,base,'mock')


def test_invalid_returned_raw_is_preserved_for_diagnostics(tmp_path,monkeypatch):
    req,base=setup(tmp_path,monkeypatch,12.,((0.,12.),))
    def worker(r,*a,**k):
        raw=output(r,base,{'two':[(5.,6.)]});raw['duration']=999.;raw['review_marker']='raw-returned'
        return raw
    monkeypatch.setattr(la,'run_worker',worker)
    result=la.refine_alignment_with_cursor(req,base,'mock')
    assert any(b['response'].get('review_marker')=='raw-returned' for b in result['raw_batches'])


def test_start_edge_total_retries_are_bounded_to_two(tmp_path,monkeypatch):
    req,base=setup(tmp_path,monkeypatch,12.,((0.,12.),))
    for word in base['words']:word.update(start_time=None,end_time=None)
    calls=[]
    def worker(r,*a,**k):
        calls.append(1);return output(r,base,{'one':[(.01,.5)]})
    monkeypatch.setattr(la,'run_worker',worker)
    result=la.refine_alignment_with_cursor(req,base,'mock')
    assert len(calls)<=3,'one base inference plus at most two total boundary reviews'
    assert result['words'][0]['start_time'] is None


def test_interrupted_worker_partial_raw_is_retained(tmp_path,monkeypatch):
    from lyricsync.local_alignment_runtime import PartialAlignmentError
    import pytest
    req,base=setup(tmp_path,monkeypatch,12.,((0.,12.),))
    def worker(r,*a,**k):
        raw=output(r,base,{'two':[(5.,6.)]});raw['review_marker']='partial-worker-raw'
        raise PartialAlignmentError(raw,'cancelled')
    monkeypatch.setattr(la,'run_worker',worker)
    with pytest.raises(PartialAlignmentError) as error:la.refine_alignment_with_cursor(req,base,'mock')
    result=error.value.result
    assert result['words'][0]['start_time']==1.
    assert any(b['response'].get('review_marker')=='partial-worker-raw' for b in result['raw_batches'])
