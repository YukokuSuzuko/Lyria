import numpy as np
from lyricsync.spectrogram import *

def test_short_windows_and_fft_contract():
    source=AudioSource(None,np.ones(22050,dtype=np.float32),1.,1.)
    for window in (256,384,512,768,1024):
        assert stft_columns(source,[.5],window).shape==(1,fft_samples(window)//2+1)

def test_piecewise_eq_low_rolloff_and_high_guard():
    w=20*np.log10(visual_frequency_weights(11026,3))
    assert w[50]<w[110]<w[1000]<w[3000]
    assert abs(w[4000]-w[6500])<.02 and w[10000]<w[7000]
    assert np.array_equal(visual_frequency_weights(50,0),np.ones(50))

def test_background_dark_and_highlights_not_clipped():
    x=10**(np.array([-45.,-35.,-25.,-12.,0.,5.,10.])/20)
    result=brightness_transfer(x,gamma=1.8)
    assert np.all(result[:3]<.08)
    assert np.all(np.diff(result)>0) and result[-1]<1.
    colors=palette(True);assert colors[-1].tolist()==[245,232,255]
    assert np.all(np.diff(perceptual_lightness(colors))>=-1e-8)

def test_axis54_and_peak_pooling():
    assert np.isclose(frequency_position(.46,10000,'balanced'),4000.)
    a=np.zeros((1,1025),np.float32);a[0,750]=1
    assert frequency_row_values(a,30,11025,'balanced').max()==1.

def test_active_calibration_ignores_long_silent_lead():
    t=np.arange(22050)/RATE;signal=(.2*np.sin(2*np.pi*1000*t)).astype(np.float32)
    a=AudioSource(None,signal,1.,.2)
    b=AudioSource(None,np.r_[np.zeros(22050*9,dtype=np.float32),signal],10.,.2)
    ca=global_calibration(a,384,active_only=True);cb=global_calibration(b,384,active_only=True)
    assert ca.active_centres==cb.active_centres==10
    assert abs(ca.reference-cb.reference)<1e-5

def test_pyramid_levels_reduce_same_base_fft_no_sparse_resampling():
    pcm=np.zeros(RATE*2,np.float32);pcm[10001]=1
    source=AudioSource(None,pcm,2.,1.);cache=LodTileCache()
    base=cache._block(source,256,0,0,lambda:False).copy()
    right=cache._block(source,256,0,1,lambda:False).copy();before=cache.computations
    coarse=cache._block(source,256,1,0,lambda:False)
    assert cache.computations==before
    assert np.array_equal(coarse,np.r_[base,right].reshape(256,2,-1).max(axis=1))
    assert coarse.max()>0
