"""Independent cursor failure isolation; no model/GPU or user media."""
from copy import deepcopy
import wave
import pytest
from test_v021_alignment_core import cursor_fixture
from lyricsync import local_alignment as la, dev9_long_audio as windows
from lyricsync.local_alignment_runtime import PartialAlignmentError


def setup_two_windows(tmp_path,monkeypatch):
    request,base=cursor_fixture(tmp_path)
    base['words'][2].update(start_time=None,end_time=None)
    monkeypatch.setattr(windows,'silence_intervals',lambda path:[])
    monkeypatch.setattr(windows,'disjoint_speech_windows',lambda *a,**kw:iter([(0.,6.),(6.,12.)]))
    calls=[]
    def first(request,*args,**kwargs):
        calls.append(request)
        return dict(base,duration=8.,words=[dict(text='one',start_time=1.,end_time=2.),dict(text='two',start_time=3.,end_time=4.),dict(text='three',start_time=None,end_time=None)],tokenization={'row_word_counts':[1,1,1]})
    return request,base,calls,first


@pytest.mark.parametrize('mode',['failure','cancel'])
def test_later_window_failure_keeps_newly_completed_prefix(tmp_path,monkeypatch,mode):
    request,base,calls,first=setup_two_windows(tmp_path,monkeypatch)
    def worker(*a,**kw):
        if calls:
            if mode=='cancel':raise InterruptedError('cancelled')
            raise RuntimeError('window failed')
        return first(*a,**kw)
    monkeypatch.setattr(la,'run_worker',worker)
    if mode=='cancel':
        with pytest.raises(PartialAlignmentError) as error:la.refine_alignment_with_cursor(request,base,'mock')
        result=error.value.result
    else:result=la.refine_alignment_with_cursor(request,base,'mock')
    assert result['words'][0]['start_time']==1.
    assert result['words'][1]['start_time']==3.
    assert result['words'][2]['start_time'] is None
    assert result['chunking']['cursor_refinement']['cursor']==2


def test_later_window_pcm_io_failure_keeps_newly_completed_prefix(tmp_path,monkeypatch):
    request,base,calls,first=setup_two_windows(tmp_path,monkeypatch)
    real_open=wave.open
    def failing_open(path,mode=None):
        if calls and str(path)==request['audio_wav'] and mode=='rb':raise OSError('injected crop read failure')
        return real_open(path,mode)
    monkeypatch.setattr(la,'run_worker',first)
    monkeypatch.setattr(wave,'open',failing_open)
    try:result=la.refine_alignment_with_cursor(request,base,'mock')
    except PartialAlignmentError as error:result=error.result
    assert result['words'][1]['start_time']==3.
    assert result['chunking']['cursor_refinement']['cursor']==2


def test_quiet_window_consumes_no_rows_and_calls_no_model(tmp_path,monkeypatch):
    request,base=cursor_fixture(tmp_path)
    monkeypatch.setattr(windows,'silence_intervals',lambda path:[(0.,12.)])
    monkeypatch.setattr(windows,'disjoint_speech_windows',lambda *a,**kw:iter([(0.,12.)]))
    monkeypatch.setattr(la,'run_worker',lambda *a,**kw:pytest.fail('silence must not call model'))
    result=la.refine_alignment_with_cursor(request,base,'mock')
    assert result['words']==base['words']
    report=result['chunking']['cursor_refinement']['reports'][0]
    assert report['cursor_before']==report['cursor_after']==1
    assert report['status']=='quiet_no_consumption'


def test_boundary_pending_survives_next_window_smaller_k(tmp_path,monkeypatch):
    request,base=cursor_fixture(tmp_path)
    with wave.open(request['audio_wav'],'wb') as f:
        f.setparams((1,2,16000,0,'NONE','not compressed'));f.writeframes(b'\x00\x10'*(100*16000))
    rows=[dict(id='row'+str(i),text='word'+str(i)) for i in range(20)]
    request['rows']=rows
    base.update(duration=100.,words=[dict(text=r['text'],start_time=None,end_time=None) for r in rows],tokenization={'row_word_counts':[1]*20})
    monkeypatch.setattr(windows,'silence_intervals',lambda path:[])
    monkeypatch.setattr(windows,'disjoint_speech_windows',lambda *a,**kw:iter([(0.,50.),(50.,51.)]))
    calls=[]
    def worker(r,*args,**kw):
        calls.append([x['id'] for x in r['rows']])
        values=[dict(text=x['text'],start_time=None,end_time=None) for x in r['rows']]
        if len(calls)==1:
            assert len(values)==13
            values[-1].update(start_time=51.,end_time=51.99)
        else:
            assert 'row12' in calls[-1]
            values[12].update(start_time=50.,end_time=50.5)
        with wave.open(r['audio_wav'],'rb') as w: actual=w.getnframes()/16000
        return dict(base,duration=actual,words=values,tokenization={'row_word_counts':[1]*len(values)})
    monkeypatch.setattr(la,'run_worker',worker)
    result=la.refine_alignment_with_cursor(request,base,'mock')
    reports=result['chunking']['cursor_refinement']['reports']
    assert reports[0]['estimated_k']==10 and reports[1]['estimated_k']==1
    assert reports[0]['pending_boundary_ids']==['row12']
    assert result['words'][12]['start_time']==50.
    assert result['chunking']['cursor_refinement']['cursor']==0
    assert not result['chunking']['cursor_refinement']['pending_boundary_ids']


@pytest.mark.parametrize('stage',['silence_scan','window_scan','temporary_directory'])
def test_preparation_io_failure_preserves_initial_anchor(tmp_path,monkeypatch,stage):
    request,base=cursor_fixture(tmp_path)
    def fail(*a,**kw):raise OSError('injected preparation error')
    if stage=='silence_scan':monkeypatch.setattr(windows,'silence_intervals',fail)
    elif stage=='window_scan':monkeypatch.setattr(windows,'disjoint_speech_windows',fail)
    else:monkeypatch.setattr(la.tempfile,'TemporaryDirectory',fail)
    with pytest.raises(PartialAlignmentError) as error:la.refine_alignment_with_cursor(request,base,'mock')
    assert error.value.result['words']==base['words']
    assert error.value.reason=='failed'
