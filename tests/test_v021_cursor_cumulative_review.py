from copy import deepcopy
import pytest,wave
from lyricsync import local_alignment as la
from lyricsync.local_alignment_runtime import PartialAlignmentError
from test_v021_cursor_context import setup,output

def test_retries_expand_actual_range_and_max_two(tmp_path,monkeypatch):
 req,base=setup(tmp_path,monkeypatch,120.,((30.,60.),))
 base['words'][0].update(start_time=None,end_time=None)
 before=deepcopy(base);calls=[];messages=[]
 def worker(r,*a,**kw):
  with wave.open(r['audio_wav'],'rb') as w:calls.append(w.getnframes()/16000)
  kw['progress'](100,'done')
  return output(r,base,{'one':[(.01,1.)]})
 monkeypatch.setattr(la,'run_worker',worker)
 result=la.refine_alignment_with_cursor(req,base,'mock',progress=lambda p,m:messages.append((p,m)))
 batches=result['raw_batches'][1:]
 assert [(b['start'],b['end']) for b in batches]==[(28.,62.),(23.,67.),(18.,72.)]
 assert calls==[34.,44.,54.]
 assert result['chunking']['cursor_refinement']['boundary_review_attempts']==2
 assert base==before and result['words']==before['words']
 assert all('/' not in m and p<=98 for p,m in messages)
 assert len(result['raw_batches'])==4

def test_media_limit_skips_duplicate_and_preserves_anchor(tmp_path,monkeypatch):
 req,base=setup(tmp_path,monkeypatch,12.,((0.,12.),));before=deepcopy(base);calls=[]
 def worker(r,*a,**k):
  calls.append(1);return output(r,base,{'one':[(3.,4.)],'two':[(10.,11.99)]})
 monkeypatch.setattr(la,'run_worker',worker)
 result=la.refine_alignment_with_cursor(req,base,'mock')
 assert len(calls)==1
 assert any(x.get('status')=='no_further_context' for x in result['chunking']['cursor_refinement']['reports'])
 assert result['words']==before['words'] and base==before
 assert len(result['raw_batches'])==2

def test_cap_keeps_later_natural_window_in_queue(tmp_path,monkeypatch):
 req,base=setup(tmp_path,monkeypatch,120.,((30.,100.),(105.,120.)))
 base['words'][0].update(start_time=None,end_time=None);calls=[]
 def worker(r,*a,**k):
  with wave.open(r['audio_wav'],'rb') as w:calls.append(w.getnframes()/16000)
  return output(r,base,{'one':[(.01,1.)]})
 monkeypatch.setattr(la,'run_worker',worker)
 result=la.refine_alignment_with_cursor(req,base,'mock')
 assert calls==[74.,84.]
 reports=result['chunking']['cursor_refinement']['reports']
 assert any(x.get('base_start')==105. for x in reports)
 assert any(x.get('status')=='model_limit_incomplete' for x in reports)
 assert all(c<=85 for c in calls)
 assert result['chunking']['cursor_refinement']['boundary_review_attempts']<=2

@pytest.mark.parametrize('failure',[RuntimeError,ValueError])
def test_failed_reviews_retain_raw_and_anchored_timing(tmp_path,monkeypatch,failure):
 req,base=setup(tmp_path,monkeypatch,90.,((30.,60.),));before=deepcopy(base);calls=[]
 def worker(r,*a,**k):
  calls.append(1)
  if len(calls)>1:raise failure('controlled failure')
  return output(r,base,{'one':[(3.,4.)],'two':[(60.,61.99)]})
 monkeypatch.setattr(la,'run_worker',worker)
 result=la.refine_alignment_with_cursor(req,base,'mock')
 assert len(calls)==3
 assert result['words']==before['words'] and base==before
 assert len(result['raw_batches'])==2
 assert sum(x.get('status')=='failed' for x in result['chunking']['cursor_refinement']['reports'])==2

def test_cancelled_review_keeps_interrupted_raw_not_accepting_it(tmp_path,monkeypatch):
 req,base=setup(tmp_path,monkeypatch,90.,((30.,60.),));before=deepcopy(base);calls=[]
 def worker(r,*a,**k):
  calls.append(1)
  if len(calls)>1:raise PartialAlignmentError(output(r,base,{'two':[(10.,11.)]}),'cancelled')
  return output(r,base,{'two':[(60.,61.99)]})
 monkeypatch.setattr(la,'run_worker',worker)
 with pytest.raises(PartialAlignmentError) as info:la.refine_alignment_with_cursor(req,base,'mock')
 result=info.value.result
 assert info.value.reason=='cancelled' and len(calls)==2
 assert result['words']==before['words'] and base==before
 assert len(result['raw_batches'])==3
 assert result['raw_batches'][-1]['status']=='interrupted_unaccepted'

def test_no_extra_pending_padding_pushes_legal_second_review_over_cap(tmp_path,monkeypatch):
 req,base=setup(tmp_path,monkeypatch,120.,((30.,90.),))
 base['words'][0].update(start_time=None,end_time=None);calls=[]
 def worker(r,*a,**kw):
  with wave.open(r['audio_wav'],'rb') as w:calls.append(w.getnframes()/16000)
  return output(r,base,{'one':[(.01,1.)]})
 monkeypatch.setattr(la,'run_worker',worker)
 result=la.refine_alignment_with_cursor(req,base,'mock')
 assert calls==[64.,74.,84.]
 assert not any(x.get('status')=='model_limit_incomplete' for x in result['chunking']['cursor_refinement']['reports'])
