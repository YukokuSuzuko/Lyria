"""Bounded, whole-source display calibration, not voice-boundary accuracy."""
import math
import threading
from dataclasses import asdict

import numpy as np
import pytest

import lyricsync.spectrogram as spectrum
from lyricsync.spectrogram import (AudioSource, Calibration, DISPLAY_PRESETS, RATE,
    TileCache, colourize, global_calibration, stft_columns)
from test_dev4_spectrogram import encoded_audio


def synthetic(seconds=4, amplitude=.7):
    t=np.arange(round(seconds*RATE))/RATE
    values=amplitude*(np.sin(2*np.pi*1000*t)+.2*np.sin(2*np.pi*2200*t))
    return AudioSource(None,values.astype(np.float32),seconds,max(amplitude*1.2,1e-4))


def test_calibration_is_deterministic_global_and_version_cached(monkeypatch):
    source=synthetic(6)
    source.samples[:RATE*5] *= .01
    first=global_calibration(source)
    times=(np.arange(60)+.5)/60*source.duration
    mag=stft_columns(source,times)
    freq=np.fft.rfftfreq(2048,1/RATE)
    exact=float(np.percentile(mag[:,(freq>=200)&(freq<4000)],99.5))
    assert abs(20*np.log10(first.sampled_quantile/exact)) < .04
    assert first.centres==60 and first.fallback==''
    assert global_calibration(source) is first
    prefix=AudioSource(None,source.samples[:RATE],1,source.reference)
    assert global_calibration(prefix).reference < first.reference
    repeat=AudioSource(None,source.samples.copy(),6,source.reference)
    assert asdict(global_calibration(repeat))==asdict(first)
    old_calls=len(source.calibrations)
    monkeypatch.setattr(spectrum,'SPECTRUM_VERSION',spectrum.SPECTRUM_VERSION+1)
    assert global_calibration(source) is not first
    assert len(source.calibrations)==old_calls+1


@pytest.mark.parametrize('amplitude',[0.,1e-8,1e-6,6e-5])
def test_near_silence_decoding_and_quantile_never_fill_screen(tmp_path,amplitude):
    source=AudioSource.decode(encoded_audio(tmp_path/'quiet.wav',1,amplitude=amplitude,silence=False),1)
    try:
        assert source.reference==1.
        calibration=global_calibration(source)
        assert calibration.reference==1. and calibration.fallback=='near_silence_or_sparse'
        values=stft_columns(source,[.2,.5,.8])
        for preset in DISPLAY_PRESETS.values():
            reference=calibration.reference if preset['quantile'] else source.reference
            assert not colourize(values,reference,180,purple=True,gamma=preset['gamma'],floor_db=preset['floor_db'],ceiling_db=preset['ceiling_db']).any()
    finally: source.close()


def test_short_sparse_audio_and_noise_have_finite_bounded_reference():
    rng=np.random.default_rng(3401)
    for values,duration in [(np.zeros(1,dtype=np.float32),1/RATE),
                            (rng.normal(0,.00002,RATE).astype(np.float32),1),
                            (np.concatenate([np.zeros(RATE*3),np.ones(8),np.zeros(RATE)]).astype(np.float32),4)]:
        source=AudioSource(None,values,duration,1.)
        c=global_calibration(source)
        assert math.isfinite(c.reference) and c.reference >= 10**(-24/20)
        assert c.centres>=1


def test_long_duration_sampling_covers_end_and_is_bounded(monkeypatch):
    visits=[]
    def fake(source,seconds,window=512,fft=2048):
        visits.extend(seconds)
        return np.full((len(seconds),fft//2+1),.1,dtype=np.float32)
    monkeypatch.setattr(spectrum,'stft_columns',fake)
    source=AudioSource(None,np.zeros(1,dtype=np.float32),86400,1.)
    c=global_calibration(source)
    assert c.centres==16384 and len(visits)==16384
    assert 0<visits[0]<3 and 86397<visits[-1]<86400
    assert len(set(visits))==16384


def test_cancelled_calibration_never_publishes_partial_cache():
    source=synthetic(30)
    checks=[]
    def cancel():
        checks.append(1)
        return len(checks)>2
    with pytest.raises(InterruptedError): global_calibration(source,cancel=cancel)
    assert not source.calibrations
    assert global_calibration(source).centres==300


def test_window_cache_and_source_identity_are_separate():
    source=synthetic()
    cache=TileCache()
    times=np.linspace(.1,.4,32)
    a=cache.get(source,('tile',0),times,window=512)
    b=cache.get(source,('tile',0),times,window=1024)
    assert cache.computations==2 and not np.array_equal(a,b)
    assert cache.get(source,('tile',0),times,window=512) is a
    other=synthetic(amplitude=.2)
    assert not np.array_equal(cache.get(other,('tile',0),times),a)
    assert len(cache.tiles)==1
    assert global_calibration(source,512) is not global_calibration(source,1024)


def test_v022_detail_suppresses_background_and_lilac_order():
    values=np.geomspace(1e-5,1,512)[:,None]*np.ones((1,1025),dtype=np.float32)
    legacy=colourize(values,1.,160,purple=True)
    preset=DISPLAY_PRESETS['detail']
    detail=colourize(values,1.,160,purple=True,mapping=preset['mapping'],gamma=preset['gamma'],log_scale=preset['log_scale'])
    # New requirement deliberately darkens low-level background, not universally brighter.
    low=20*np.log10(values[:,0]) < -25
    assert detail[:,low].mean()<legacy[:,low].mean()
    palette=spectrum.palette(True).astype(int)
    assert np.all(palette[:,0]>=palette[:,1]) and np.all(palette[:,2]>=palette[:,1])
    assert tuple(palette[-1])==(245,232,255)


def test_long_window_onset_tradeoff_is_not_timing_precision():
    t=np.arange(RATE)/RATE
    source=AudioSource(None,(.7*np.sin(2*np.pi*1000*t)*(t>=.5)).astype(np.float32),1,.7)
    times=np.arange(.46,.54,.0005)
    spreads=[]
    for window in (512,1024):
        a=stft_columns(source,times,window=window)[:,round(1000*2048/RATE)]
        a/=a.max()
        onset=[times[np.flatnonzero(a>=threshold)[0]] for threshold in (.1,.9)]
        assert onset[0]<.5<onset[1]
        spreads.append(onset[1]-onset[0])
    assert .010<spreads[0]<.014 and .020<spreads[1]<.025


def test_worker_mid_decode_source_switch_and_inactive_calibration_cancel(monkeypatch):
    import time
    import desktop_qt.spectrogram_item as native
    entered=threading.Event();cancelled=threading.Event();delivered=threading.Event()
    results=[]
    def decode(path,duration,cancel):
        if path=='slow':
            entered.set()
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                if cancel():
                    cancelled.set()
                    raise InterruptedError('test cancellation')
                time.sleep(.002)
            raise AssertionError('Source switch did not cancel decode')
        return synthetic(1)
    monkeypatch.setattr(native.AudioSource,'decode',decode)
    def receive(seq,image,offset,metrics,error):
        results.append((seq,error,metrics))
        delivered.set()
    worker=native._Worker(receive)
    def request(sequence,path,preset='balanced'):
        return dict(sequence=sequence,source=(path,1,0),step=.1,width=256,height=80,
            offset=0,gain=0,gamma=1.32,preset=preset,window=512,secondsPerColumn=.001,
            tiles=[(i,np.linspace(.1,.3,256)) for i in range(3)])
    try:
        worker.request(request(1,'slow'))
        assert entered.wait(3)
        worker.request(request(2,'new'))
        assert cancelled.wait(3) and delivered.wait(3)
        assert results and all(row[0]==2 and not row[1] for row in results)
        # Hold actual calibration until hide arrives; no old image may publish.
        started=threading.Event();stopped=threading.Event();results.clear();delivered.clear()
        def calibrate(source,window,cancel,**kwargs):
            started.set()
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                if cancel():
                    stopped.set()
                    raise InterruptedError('test calibration cancelled')
                time.sleep(.002)
            raise AssertionError('Hide did not cancel calibration')
        monkeypatch.setattr(native,'global_calibration',calibrate)
        worker.request(request(3,'another'))
        assert started.wait(3)
        worker.request(dict(sequence=4,source=('another',1,0),inactive=True))
        assert stopped.wait(3)
        assert not delivered.wait(.05)
    finally:
        worker.stop();worker.thread.join(3)
    assert not worker.thread.is_alive()


def test_weak_onset_survives_physical_reference_without_local_normalization():
    t=np.arange(RATE*2)/RATE
    amplitude=np.where((t>=.5)&(t<.8),.002,np.where(t>=1.2,.8,0.))
    pcm=(amplitude*np.sin(2*np.pi*1000*t)).astype(np.float32)
    source=AudioSource(None,pcm,2.,float(np.abs(pcm).max()))
    times=np.arange(.46,.56,.0005)
    values=stft_columns(source,times,window=512)
    old=colourize(values,source.reference,160,purple=True)
    preset=DISPLAY_PRESETS['physical']
    new=colourize(values,source.reference,160,purple=True,gamma=preset['gamma'],floor_db=preset['floor_db'],ceiling_db=preset['ceiling_db'])
    assert np.all(new>=old)
    assert not new[:,times<.48].any()  # silence before even the centred window
    assert new[:,times>.515].max()>25  # -52 dB weak onset remains visible
    assert new.max()<170  # never normalize this quiet viewport to white
    assert source.reference>.79 and source.calibrations=={}
