"""v0.21 display-only processing, LOD and physical parameter invariants."""
import numpy as np
import pytest
from lyricsync.spectrogram import (AudioSource,RATE,FFT,LodTileCache,colourize,palette,
    perceptual_lightness,visual_frequency_weights,frequency_rows,stft_columns,global_calibration)


def synthetic():
    t=np.arange(RATE*2)/RATE
    pcm=(.6*np.sin(2*np.pi*100*t)+.08*np.sin(2*np.pi*1600*t)*(t>=.5)*(t<1.)).astype(np.float32)
    return AudioSource(None,pcm,2.,float(np.max(np.abs(pcm))))


def test_purple_palette_is_perceptually_monotone_to_near_white():
    colours=palette(True)
    lightness=perceptual_lightness(colours)
    assert np.min(np.diff(lightness))>=0
    assert lightness[0]==0 and lightness[-1]>92
    assert np.all(colours[:,2]>=colours[:,1])


def test_visual_tilt_changes_frequency_balance_without_mutating_pcm_or_fft():
    source=synthetic();before=source.samples.copy()
    mag=stft_columns(source,[.75],window=768);original=mag.copy()
    weights=visual_frequency_weights(1025,3.)
    assert weights[round(100*FFT/RATE)]<.4
    assert weights[round(2000*FFT/RATE)]>1.3
    rgb=colourize(mag,source.reference,200,mapping='soft_log',tilt_db_oct=3,purple=True)
    assert rgb.any()
    np.testing.assert_array_equal(mag,original)
    np.testing.assert_array_equal(source.samples,before)


def test_soft_log_black_and_gain_are_independent_and_silence_stays_dark():
    mags=np.geomspace(1e-8,1,100)[:,None]*np.ones((1,1025))
    base=colourize(mags,1,80,mapping='soft_log',gamma=1.,purple=True)
    gain=colourize(mags,1,80,mapping='soft_log',gamma=1.,purple=True,gain_db=6)
    black=colourize(mags,1,80,mapping='soft_log',gamma=1.,purple=True,black_level=.08)
    assert np.all(gain>=base) and np.all(black<=base)
    assert not colourize(np.zeros((10,1025)),1,80,mapping='soft_log',gain_db=12).any()
    assert not base[:,:10].any()


def test_frequency_axes_limits_and_allocations_are_explicit():
    for maximum in (5500,8000,10000,11025):
        for axis in ('linear','legacy','speech'):
            rows=frequency_rows(200,maximum,axis)
            assert np.all(np.diff(rows)<0) and rows.max()<maximum and rows.min()>0
            if axis=='speech': assert np.count_nonzero(rows<4000)==150
            mag=np.zeros((10,1025));mag[:,1000]=1
            image=colourize(mag,1,200,max_frequency=maximum,axis=axis)
            assert bool(image.any()) == (maximum==11025)


def test_lod_reuses_fft_across_fractional_zoom_and_display_settings():
    source=synthetic();cache=LodTileCache()
    times=np.linspace(.6,.9,300)
    a=cache.get(source,None,times,window=768)
    count=cache.computations
    b=cache.get(source,None,np.linspace(.61,.89,280),window=768)
    assert cache.computations==count and cache.hits>0 and cache.last_hop==64
    for height in (120,200,320):
        colourize(a,1,height,mapping='soft_log',tilt_db_oct=3)
    assert cache.computations==count
    cache.get(source,None,times,window=512)
    assert cache.computations>count


def test_coarse_lod_pools_short_event_instead_of_missing_between_pixels():
    pcm=np.zeros(RATE,dtype=np.float32)
    pcm[round(.515*RATE):round(.518*RATE)]=.8
    source=AudioSource(None,pcm,1.,1.)
    cache=LodTileCache()
    times=np.array([.35,.65])  # one centre per 300ms would miss the event
    assert not stft_columns(source,times).any()
    result=cache.get(source,None,times,window=768)
    assert cache.last_hop>=512 and result.max()>.03
    assert cache.bytes<=cache.max_bytes


def test_window_768_has_intermediate_transient_spread():
    t=np.arange(RATE)/RATE
    source=AudioSource(None,(np.sin(2*np.pi*1600*t)*(t>=.5)).astype(np.float32),1.,1.)
    times=np.arange(.45,.55,.0005)
    widths=[]
    for window in (512,768,1024):
        values=stft_columns(source,times,window=window)[:,round(1600*FFT/RATE)]
        values/=values.max()
        widths.append(times[np.flatnonzero(values>=.9)[0]]-times[np.flatnonzero(values>=.1)[0]])
    assert widths[0]<widths[1]<widths[2]


def test_lod_cancellation_never_publishes_wrong_source():
    cache=LodTileCache()
    with pytest.raises(InterruptedError):
        cache.get(synthetic(),None,np.linspace(.1,.5,128),cancel=lambda:True)
    assert not cache.tiles


def test_new_layout_parameters_validate_and_round_trip():
    from desktop_qt.workspace_layout import DEFAULTS,update_layout
    changes=dict(spectrumPreset='vocal',spectrumWindow=768,spectrumBlack=.04,
                 spectrumTilt=3,spectrumFrequency=8000,spectrumAxis='speech')
    layout=update_layout(DEFAULTS,changes)
    assert layout['spectrumPreset']=='balanced'  # v0.23 migrates misleading duplicate
    assert all(layout[k]==v for k,v in changes.items() if k != 'spectrumPreset')
    assert update_layout(DEFAULTS,layout,loading=True)==layout
    with pytest.raises(ValueError): update_layout(layout,dict(spectrumFrequency=99999))

def test_short_window_coarse_grid_has_no_hann_endpoint_blind_spot():
    pcm=np.zeros(RATE,dtype=np.float32)
    pcm[512]=1.
    source=AudioSource(None,pcm,1.,1.)
    cache=LodTileCache()
    result=cache.get(source,None,np.array([.01,.11,.21]),window=512)
    ideal=stft_columns(source,[512/RATE],window=512).max()
    unsafe=stft_columns(source,np.array([256,768])/RATE,window=512).max()
    assert unsafe<1e-6  # Non-overlapping Hann grid really loses this event.
    assert cache.BASE_HOP==64 and result.max()>.49*ideal


def test_progressive_tile_overlays_keep_old_pixels_and_correct_time_coordinates():
    import os
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QImage,QPainter
    from desktop_qt.spectrogram_item import SpectrogramItem
    app=QApplication.instance() or QApplication([])
    item=SpectrogramItem();item.setWidth(512);item.setHeight(10)
    item._path='source';item._duration=1.;item._active=True;item._span=512.;item._first=1.
    old=QImage(512,10,QImage.Format.Format_RGB888);old.fill(0x0000ff)
    sig=item._signature();seq=item._sequence
    item._accept(seq,old,0.,dict(imageFirstFrame=1.,imageFrameStep=1.,imageSignature=sig),'')
    patch=QImage(256,10,QImage.Format.Format_RGB888);patch.fill(0xff0000)
    item._accept(seq,patch,0.,dict(partial=True,completedTiles=1,totalTiles=2,
          imageFirstFrame=257.,imageFrameStep=1.,imageSignature=sig),'')
    def painted():
        image=QImage(512,10,QImage.Format.Format_RGB888)
        painter=QPainter(image);item.paint(painter);painter.end();return image
    try:
        image=painted()
        assert image.pixelColor(20,5).blue()==255  # unfinished left kept, not cleared
        assert image.pixelColor(300,5).red()==255
        item._first=129.;item._span=256.
        image=painted()
        assert image.pixelColor(20,5).value()==0 and image.pixelColor(300,5).value()==0  # stale zoom never stretched
        assert item._status=='updating' and not item._metrics['renderComplete']
        item._path='new-source';item._schedule()
        assert not item._partial_tiles and item._image.isNull()
        item._accept(seq,patch,0.,dict(partial=True,imageSignature=sig),'')
        assert not item._partial_tiles  # late old-source patch rejected
    finally:
        item._timer.stop();item.deleteLater()
