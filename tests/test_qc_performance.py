from copy import deepcopy
import numpy as np
import pytest
from lyricsync.model import Visual
from lyricsync.storage import Cache
from lyricsync.vision import Detection
from lyricsync.qc_ocr_cache import CachedOCR
from lyricsync.audit_service import refine_cues
from test_qc_migration import FakeOCR,FakeVideo,clock
from qc_refine_reference import refine_cues as reference


@pytest.mark.parametrize('stamps,ends',[(tuple(n*.04 for n in range(100)),4.),(tuple(n*.03+(n//3)*.01 for n in range(100)),3.34)])
@pytest.mark.parametrize('confidence',[.99,.65])
def test_boundary_union_matches_all_reference_fields(stamps,ends,confidence):
    c=clock(stamps,ends);visuals=[Visual(str(n),'中文',stamps[n],stamps[n+5],[0,0,10,10],.95,2) for n in range(4,85,7)]
    active=tuple(n for n in range(1,101) if n%9 in (2,3,4,5));metrics={}
    old=reference(FakeVideo(c.stamps),visuals,c,{'box':[0,0,20,20]},{},provider=FakeOCR(active,confidence))
    progress=[]
    new=refine_cues(FakeVideo(c.stamps),visuals,c,{'box':[0,0,20,20]},{},provider=FakeOCR(active,confidence),metrics=metrics,progress=lambda p,m:progress.append(p))
    assert new==old
    assert metrics['decode_windows']<len(visuals)*2
    assert metrics['decoded_frames']<=len(c.stamps)
    assert progress==sorted(progress) and progress[-1]==1


class CountedOCR:
    def __init__(self,version=1):self.signature={'model':'hash-'+str(version),'language':'chi_sim'};self.calls=0
    def recognize(self,image):
        self.calls+=1;return [Detection('合成中文',[0,0,2,2],.9)] if image[0,0,0] else []


def make(provider,cache,video='video-A',roi=(0,0,3,3),settings=None,cancel=lambda:False):
    return CachedOCR(provider,cache,video,roi,settings or {'language':'chi_sim'},cancel)


def test_exact_pixels_persist_across_instances_without_aliasing_boxes(tmp_path):
    image=np.ones((3,3,3),np.uint8);cache=Cache(tmp_path/'cache.sqlite');p=CountedOCR();a=make(p,cache)
    try:
        first=a.recognize(image);first[0].box[0]=999;first[0].text='mutated'
        assert a.recognize(image)[0].box==[0,0,2,2] and p.calls==1
        second=CountedOCR();b=make(second,cache)
        assert b.recognize(image)[0].text=='合成中文' and second.calls==0
        assert b.metrics['persistent_hits']==1
        blank=np.zeros_like(image);assert b.recognize(blank)==[];assert b.recognize(blank)==[]
        assert second.calls==1
    finally:cache.close()


@pytest.mark.parametrize('changed',['video','roi','model','language','threads','pixels','shape','dtype'])
def test_any_material_input_change_invalidates_cache(tmp_path,changed):
    cache=Cache(tmp_path/'cache.sqlite');image=np.ones((3,3,3),np.uint8);original=CountedOCR();a=make(original,cache)
    try:
        a.recognize(image);p=CountedOCR(2 if changed=='model' else 1)
        kwargs={'video':'video-B'} if changed=='video' else {'roi':(0,0,2,3)} if changed=='roi' else {'settings':{'language':'jpn'}} if changed=='language' else {'settings':{'language':'chi_sim','ocr_threads':4}} if changed=='threads' else {}
        if changed=='pixels':image[1,1,0]=2
        if changed=='shape':image=image.reshape(1,9,3)
        if changed=='dtype':image=image.astype(np.uint16)
        b=make(p,cache,**kwargs);b.recognize(image)
        assert p.calls==1 and not b.metrics['persistent_hits']
    finally:cache.close()


def test_cancelled_model_result_is_not_cached_and_cached_lookup_cancels(tmp_path):
    cache=Cache(tmp_path/'cache.sqlite');stopped=[False];p=CountedOCR();original=p.recognize
    def model(image):result=original(image);stopped[0]=True;return result
    p.recognize=model;image=np.ones((3,3,3),np.uint8)
    try:
        a=make(p,cache,cancel=lambda:stopped[0])
        with pytest.raises(InterruptedError):a.recognize(image)
        assert cache.db.execute('SELECT COUNT(*) FROM cache').fetchone()[0]==0
        stopped[0]=False;fresh=CountedOCR();b=make(fresh,cache);b.recognize(image);assert fresh.calls==1
        stopped[0]=True;c=make(CountedOCR(),cache,cancel=lambda:stopped[0])
        with pytest.raises(InterruptedError):c.recognize(image)
    finally:cache.close()


def test_cancellation_closes_boundary_decoder():
    c=clock(tuple(n*.04 for n in range(80)),3.2);closed=[];stopped=[False]
    class Video(FakeVideo):
        def frames(self,*args,**kwargs):
            try:yield from super().frames(*args,**kwargs)
            finally:closed.append(True)
    provider=FakeOCR();old=provider.recognize
    def recognize(image):
        result=old(image)
        if len(provider.seen)==4:stopped[0]=True
        return result
    provider.recognize=recognize
    with pytest.raises(InterruptedError):refine_cues(Video(c.stamps),[Visual('a','中文',.8,2,[0,0,10,10],.9,2)],c,{'box':[0,0,20,20]},{},provider=provider,cancel=lambda:stopped[0])
    assert closed and len(provider.seen)==4


def test_separate_audit_output_directories_reuse_local_cache_and_one_provider_per_role(tmp_path,monkeypatch):
    import lyricsync.audit_service as service
    import lyricsync.audit as audit
    video_path=tmp_path/'synthetic.mp4';video_path.write_bytes(b'synthetic version A')
    text=tmp_path/'reference.txt';text.write_text('中文',encoding='utf8')
    c=clock()
    class IndexedVideo(FakeVideo):
        def __init__(self,path):super().__init__(c.stamps);self.path=str(path);self.origin=0
        def at(self,stamp,**kwargs):return stamp,np.full((20,20,3),c.frame(stamp),np.uint8)
    providers=[];scan_providers=[]
    def create(options,*args):
        p=FakeOCR();providers.append(p);return p
    def scan(path,options,*args,provider=None,**kwargs):
        assert provider is not None;scan_providers.append(provider)
        return [Visual('x','中文',.04,.3,[0,0,10,10],.96,2)],{}
    monkeypatch.setattr(service,'Video',IndexedVideo);monkeypatch.setattr(audit,'Video',IndexedVideo)
    monkeypatch.setattr(service.AuditClock,'build',lambda *args:c)
    monkeypatch.setattr(service,'make_ocr',create);monkeypatch.setattr(service,'scan',scan)
    reports=[service.run_audit(video_path,text,tmp_path/f'qc-{n}',cache_dir=tmp_path/'shared-cache') for n in range(2)]
    assert len(providers)==2 and len(scan_providers)==2
    assert providers[0].seen and not providers[1].seen
    assert reports[1]['local_performance']['chinese']['ocr_reuse']['persistent_hits']>0
    assert (tmp_path/'qc-0/report.json').is_file() and (tmp_path/'qc-1/report.json').is_file()
    assert reports[0]['cues'][0]['raw_observations']==reports[1]['cues'][0]['raw_observations']
