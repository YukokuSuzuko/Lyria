"""Legacy bounded overview envelopes; not the timing-resolution spectrum source.

Uses existing PyAV/numpy only. 8 kHz mono analysis (0..4 kHz), 512-sample
Hann windows, 128 linear frequency bands. Bounded result: 16384 peaks + 2048x128.
Codec buffers excluded; one resampled frame and one FFT window are transient.
The timing surface separately decodes 22.05 kHz via lyricsync.spectrogram.
The retained legacy spectrum is not displayed by the dev4 timing surface.
"""
import math

def analyze_audio(path, duration, cancel=lambda: False):
    import av
    import numpy as np
    if not math.isfinite(duration) or not 0 < duration <= 86400:
        raise ValueError('音频分析仅支持 24 小时以内的媒体。')
    bins, bands, rate, window = 16384, 128, 8000, 512
    columns=min(2048,max(1,math.ceil(duration*rate/window)))
    low=np.zeros(bins); high=np.zeros(bins)
    spectrum=np.zeros((columns,bands)); counts=np.zeros(columns)
    pending=np.empty(0,dtype=np.float32); offset=0; samples=0
    hann=np.hanning(window)
    with av.open(path) as container:
        if not container.streams.audio:
            return dict(status='no_audio',error='',durationSeconds=duration,waveform=[],spectrum=[],maxFrequency=4000)
        stream=container.streams.audio[0]
        origin=float(container.start_time / av.time_base) if container.start_time is not None else float((stream.start_time or 0)*stream.time_base)
        resampler=av.AudioResampler(format='fltp',layout='mono',rate=rate)
        def consume(frame):
            nonlocal pending, offset, samples
            values=frame.to_ndarray().reshape(-1)
            start=max(0.,float(frame.pts*frame.time_base)-origin) if frame.pts is not None else samples/rate
            positions=np.minimum(bins-1, np.maximum(0,((start+np.arange(len(values))/rate)/duration*bins).astype(int)))
            np.minimum.at(low,positions,values); np.maximum.at(high,positions,values)
            if not len(pending): offset=round(start*rate)
            pending=np.concatenate((pending,values)); samples+=len(values)
            while len(pending)>=window:
                col=min(columns-1,max(0,int((offset+window/2)/rate/duration*columns)))
                power=np.abs(np.fft.rfft(pending[:window]*hann))[1:257] / (window/2)
                spectrum[col]+=power.reshape(bands,2).max(axis=1); counts[col]+=1
                pending=pending[window:]; offset+=window
        for frame in container.decode(stream):
            if cancel(): raise InterruptedError('音频分析已取消')
            for resampled in resampler.resample(frame): consume(resampled)
        for resampled in resampler.resample(None): consume(resampled)
        if len(pending):
            col=min(columns-1,max(0,int(offset/rate/duration*columns)))
            power=np.abs(np.fft.rfft(np.pad(pending,(0,window-len(pending)))*hann))[1:257]/(window/2)
            spectrum[col]+=power.reshape(bands,2).max(axis=1); counts[col]+=1
    spectrum /= np.maximum(1,counts[:,None])
    # Fixed dBFS scale, not per-file normalization: silence stays dark.
    spectrum=np.clip((20*np.log10(np.maximum(spectrum,1e-6))+80)/80,0,1)
    return dict(status='ready',error='',durationSeconds=duration,sourcePath=str(path),
                waveform=np.column_stack((low,high)).clip(-1,1).tolist(),
                spectrum=spectrum.tolist(),maxFrequency=4000,sampleRate=rate,
                spectrumColumns=columns, frequencyBands=bands, timeResolutionSeconds=duration/columns,
                analysisNote='8 kHz mono; 128 linear bands; 64 ms FFT; bounded 2048 columns; -80..0 dBFS; no inferred detail')
