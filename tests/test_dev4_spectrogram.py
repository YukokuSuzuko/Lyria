"""dev4 signal/pixel invariants; generated encoded PCM is not human acceptance."""
import json
import os
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from lyricsync.spectrogram import (AudioSource, TileCache, RATE, TILE_WIDTH,
                                  stft_columns, frequency_rows, colourize)



def evidence_dir(tmp_path):
    """Isolated by default; explicit collection destinations are opt-in."""
    configured=os.environ.get('LYRIA_SPECTRUM_EVIDENCE')
    path=Path(configured).expanduser() if configured else tmp_path/'evidence'
    path=path.resolve()
    path.mkdir(parents=True,exist_ok=True)
    return path


def encoded_audio(path, seconds=4, frequency=1000, amplitude=.7, rate=48000, silence=True):
    t=np.arange(round(rate*seconds))/rate
    signal=amplitude*np.sin(2*np.pi*frequency*t)
    if silence:
        signal[(t>1.5)&(t<2)]=0
    with wave.open(str(path),'wb') as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(np.repeat((signal*32767).astype('<i2')[:,None],2,axis=1).tobytes())
    return path


@pytest.fixture
def source(tmp_path):
    source=AudioSource.decode(encoded_audio(tmp_path/'stereo-48k.wav'),4)
    yield source
    source.close()


def test_independent_decode_antialias_and_positive_high_frequencies(tmp_path):
    for frequency in (1000,7000,13000):
        source=AudioSource.decode(encoded_audio(tmp_path/f'{frequency}.wav',1,frequency,silence=False),1)
        try:
            assert source.samples.dtype==np.float32 and len(source.samples)==RATE
            bins=stft_columns(source,[.5])[0]
            if frequency<10000:
                assert abs(bins.argmax()*RATE/2048-frequency)<RATE/2048
                assert bins.max()>.35
            else:
                # A 13 kHz source must not alias into the 0..10 kHz display.
                assert bins[:round(10000*2048/RATE)].max()<.002
        finally:source.close()


def test_black_silence_global_reference_and_frequency_layout(source):
    times=np.linspace(1,2.4,512)
    magnitudes=stft_columns(source,times)
    rgb=colourize(magnitudes,source.reference,256)
    assert not rgb[:,(times>1.55)&(times<1.95)].any()
    assert rgb[:,times<1.4].max()==255
    quiet=colourize(magnitudes*1e-5,source.reference,256)
    assert not quiet.any(), 'silence/noise may not be amplified by local tile gain'
    rows=frequency_rows(1000)
    assert np.count_nonzero(rows<=4000)==680
    assert 0<rows.min()<10 and 9900<rows.max()<10000


def test_column_centres_are_independent_and_hop_overlap_is_coherent(source):
    seconds=.45+np.arange(256)*.002
    t=np.arange(4*RATE)/RATE
    chirp=AudioSource(None,(.7*np.sin(2*np.pi*(200*t+400*t*t))).astype(np.float32),4,.7)
    values=stft_columns(chirp,seconds)
    assert values.shape==(256,1025)
    # A chirp changes each independent window instead of enlarging fixed cells.
    assert np.unique(values,axis=0).shape[0]>200
    overlap=1-.002*RATE/512
    assert .91<overlap<.92
    assert 1-.005*RATE/512==pytest.approx(.78466796875)


def test_cache_is_byte_bounded_and_resize_reuses_fft(source):
    cache=TileCache(max_bytes=3*TILE_WIDTH*1025*4)
    times=.2+np.arange(TILE_WIDTH)*.002
    values=cache.get(source,('zoom',0),times)
    first=cache.computations
    colourize(values,source.reference,180)
    colourize(cache.get(source,('zoom',0),times),source.reference,360)
    assert cache.computations==first and cache.hits==1
    for n in range(10):cache.get(source,('zoom',n),times+n*.02)
    assert cache.bytes<=cache.max_bytes and len(cache.tiles)==3


def test_encoded_long_media_performance_and_preview(tmp_path):
    path=encoded_audio(tmp_path/'long-encoded-fixture.wav',140)
    start=time.perf_counter()
    source=AudioSource.decode(path,140)
    decode=time.perf_counter()-start
    try:
        cache=TileCache()
        durations=[]
        for n in range(8):
            start=time.perf_counter()
            values=cache.get(source,(.002,n),n*.512+np.arange(256)*.002)
            colourize(values,source.reference,320)
            durations.append(time.perf_counter()-start)
        assert max(durations)<1.
        report=dict(scope='140-second synthetic stereo 48k PCM WAV decoded through actual PyAV/libswresample; no user/native acceptance or Aegisub comparison',
                    decodeSeconds=decode,maxTileSeconds=max(durations),meanTileSeconds=float(np.mean(durations)),
                    cacheBytes=cache.bytes,windowSamples=512,fftSamples=2048,sampleRate=22050,
                    physicalColumnsPerTile=256,hopSeconds=.002,overlap=1-.002*22050/512)
        (evidence_dir(tmp_path)/'dev4-spectrum-performance.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        # Save actual native RGB spectrum pixels for inspection, not a mockup.
        from PySide6.QtGui import QImage
        values=stft_columns(source,np.linspace(1.25,2.25,1024))
        rgb=colourize(values,source.reference,320)
        image=QImage(rgb.data,1024,320,rgb.strides[0],QImage.Format.Format_RGB888).copy()
        assert image.save(str(evidence_dir(tmp_path)/'dev4-spectrum-synthetic.png'))
    finally:source.close()

def test_media_pts_audio_offset_is_not_slid_to_zero(tmp_path):
    import av
    from fractions import Fraction
    path=tmp_path/'audio-offset.mkv'
    with av.open(str(path),'w') as container:
        video=container.add_stream('mpeg4',rate=25)
        video.width=64;video.height=36;video.pix_fmt='yuv420p'
        audio=container.add_stream('pcm_s16le',rate=48000)
        audio.layout='mono'
        for i in range(100):
            frame=av.VideoFrame.from_ndarray(np.zeros((36,64,3),dtype=np.uint8),format='rgb24')
            frame.pts=i;frame.time_base=Fraction(1,25)
            for packet in video.encode(frame):container.mux(packet)
        for packet in video.encode():container.mux(packet)
        t=np.arange(96000)/48000
        samples=(.7*np.sin(2*np.pi*1000*t)*32767).astype('<i2')
        for first in range(0,len(samples),1024):
            frame=av.AudioFrame.from_ndarray(samples[first:first+1024][None,:],format='s16',layout='mono')
            frame.sample_rate=48000;frame.pts=48000+first;frame.time_base=Fraction(1,48000)
            for packet in audio.encode(frame):container.mux(packet)
        for packet in audio.encode():container.mux(packet)
    source=AudioSource.decode(path,4)
    try:
        values=stft_columns(source,[.5,1.5,2.5,3.5])
        assert not values[[0,3]].any()
        assert np.all(values[[1,2]].max(axis=1)>.5)
    finally:source.close()
