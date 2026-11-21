import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import wave
import threading
import numpy as np
import pytest
from lyricsync.spectrogram import AudioSource, ProgressiveAudioSource, RATE, LodTileCache

def make_audio(tmp_path):
    p = tmp_path / "sample.wav"
    pcm = np.random.default_rng(3).integers(-25000,25000,RATE*25,dtype=np.int16)
    with wave.open(str(p),"wb") as w:
        w.setparams((1,2,RATE,0,"NONE","not compressed")); w.writeframes(pcm.tobytes())
    return p, pcm

def test_seeked_blocks_preserve_absolute_pcm_and_do_not_decode_prefix(tmp_path):
    p, pcm = make_audio(tmp_path)
    s = ProgressiveAudioSource(p,25)
    try:
        indices = np.arange(RATE*19, RATE*20)
        np.testing.assert_allclose(s.samples[indices], pcm[indices]/32768., atol=1e-7)
        assert set(s.locations) == {2}
        assert not s.complete and s.reference == 1.
        assert s.file.seek(0,2) <= RATE*8*4
        s.prepare_global()
        assert s.complete and len(s.locations) == 4
        for begin in (0,RATE*8-128,RATE*16-128,RATE*24-128):
            np.testing.assert_allclose(s.samples[begin:begin+256],pcm[begin:begin+256]/32768.,atol=1e-7)
    finally:s.close()

def test_cancelled_block_not_published(tmp_path):
    p,_ = make_audio(tmp_path)
    s=ProgressiveAudioSource(p,25,lambda:True)
    try:
        with pytest.raises(InterruptedError):s.samples[:100]
        assert not s.locations
    finally:s.close()

def test_first_frame_delivered_before_whole_source_scan(tmp_path,monkeypatch):
    from desktop_qt.spectrogram_item import _Worker
    p,_=make_audio(tmp_path); event=threading.Event(); got=[];entered=threading.Event(); release=threading.Event()
    original=ProgressiveAudioSource.prepare_global
    def slow(self):
        entered.set(); release.wait(5)
        if not self.cancel(): original(self)
    monkeypatch.setattr(ProgressiveAudioSource,"prepare_global",slow)
    def deliver(seq,image,offset,metrics,error):
        got.append((metrics,error));event.set()
    worker=_Worker(deliver)
    request=dict(sequence=1,source=(str(p),25.,0,0),step=1.,width=256,height=80,offset=0,gain=0,preset="detail",window=384,secondsPerColumn=.01,progressiveDecode=True,tiles=[(i,np.linspace(18,20,256)) for i in range(3)])
    try:
        worker.request(request);assert event.wait(5);assert entered.wait(5)
        metrics,error=got[0];assert not error
        assert metrics["calibrationPending"] and metrics["referenceKind"]=="provisional_fixed_0dbfs"
        assert metrics["decodedBlocks"] < 4
    finally:
        worker.stop();release.set();worker.thread.join(5)

def test_preset_migration_preserves_custom_dimensions():
    from desktop_qt.workspace_layout import update_layout
    for old,new in [("vocal","balanced"),("onset","balanced"),("harmonic","detail")]:
        result=update_layout({},dict(spectrumPreset=old,spectrumWindow=768,spectrumGain=6.,spectrumTilt=0),loading=True)
        assert result["spectrumPreset"]==new
        assert result["spectrumWindow"]==768 and result["spectrumGain"]==6. and result["spectrumTilt"]==0

@pytest.mark.parametrize("rate", [44100,48000])
def test_resampler_phase_matches_full_decode_across_seek_boundary(tmp_path,rate):
    p=tmp_path/"resampled.wav"
    pcm=np.random.default_rng(42).integers(-25000,25000,rate*18,dtype=np.int16)
    with wave.open(str(p),"wb") as w:
        w.setparams((1,2,rate,0,"NONE","not compressed"));w.writeframes(pcm.tobytes())
    full=AudioSource.decode(p,18);lazy=ProgressiveAudioSource(p,18)
    try:
        for start in (RATE*8-100,RATE*16-100):
            np.testing.assert_allclose(lazy.samples[start:start+500],full.samples[start:start+500],atol=1e-7)
    finally:full.close();lazy.close()

def test_pooled_batch_matches_per_column_reference():
    import math
    source=AudioSource(None,np.random.default_rng(4).normal(0,.1,RATE*3).astype("f4"),3,1)
    times=np.linspace(.1,2.7,256)
    cache=LodTileCache();actual=cache.get(source,0,times,window=384)
    hop=cache.choose_hop(times);level=int(math.log2(hop/64))
    def row(i):return cache._block(source,384,level,int(i//256),lambda:False)[i%256]
    pos=times*RATE/hop-.5;lo=np.floor(pos).astype(int);fraction=pos-lo
    expected=np.stack([row(i)*(1-f)+row(i+1)*f for i,f in zip(lo,fraction)])
    edges=np.r_[times[0]-(times[1]-times[0])/2,(times[:-1]+times[1:])/2,times[-1]+(times[-1]-times[-2])/2]
    for col in range(len(times)):
        if edges[col+1]-edges[col]>=64/RATE:
            ids=range(math.floor(edges[col]*RATE/hop),math.ceil(edges[col+1]*RATE/hop))
            expected[col]=np.maximum(expected[col],np.stack([row(i) for i in ids]).max(axis=0))
    np.testing.assert_allclose(actual,expected,rtol=1e-6,atol=1e-7)
