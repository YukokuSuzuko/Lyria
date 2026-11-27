"""Windows PCM preview using the OS audio clock; cached extraction stays local."""
from pathlib import Path
import ctypes
import hashlib
import os
import subprocess
import uuid
from .vision import resources


def prepare_audio(video,folder,cancel=lambda:False):
    import av
    with av.open(video.path) as container:
        if not container.streams.audio:return None
    stat=Path(video.path).stat()
    signature=f'audio-v2:{Path(video.path).resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{video.origin}'
    target=Path(folder)/(hashlib.sha256(signature.encode()).hexdigest()+'.wav')
    if target.is_file():return target
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_name(target.stem+'-'+uuid.uuid4().hex+'.wav')
    # copyts/start_at_zero preserves stream lead relative to the container origin;
    # first_pts=0 pads initial audio delay rather than shifting the lyrics/video.
    command=[str(resources()/'ffmpeg.exe'),'-nostdin','-hide_banner','-loglevel','error','-y',
        '-copyts','-start_at_zero','-i',video.path,'-map','0:a:0','-vn','-af',
        'aresample=48000:async=1:first_pts=0','-ac','2','-c:a','pcm_s16le',str(temporary)]
    process=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        while True:
            try:code=process.wait(timeout=.15);break
            except subprocess.TimeoutExpired:
                if cancel():raise InterruptedError('音频准备已取消')
        if code!=0:raise RuntimeError('无法准备预览音频，可暂用静音播放。')
        os.replace(temporary,target)
        return target
    finally:
        if process.poll() is None:process.terminate();process.wait(timeout=5)
        temporary.unlink(missing_ok=True)


class AudioPlayer:
    """PCM waveOut playback, with sample position as the video master clock.

    Python opens Unicode/long paths; no legacy MCI filename parser is involved.
    One bounded song buffer avoids streaming-thread races during seek/pause.
    """
    def __init__(self):
        self.opened=False;self.handle=ctypes.c_void_p();self.header=None;self.buffer=None
        self.started=False;self.offset=0;self.stopped_position=0;self.pcm=b''
    def check(self,code):
        if code:raise RuntimeError(f'音频设备操作失败（waveOut {code}），可切换为静音播放。')
    def open(self,path):
        import wave
        from ctypes import wintypes
        self.close()
        class Format(ctypes.Structure):
            _fields_=[('tag',wintypes.WORD),('channels',wintypes.WORD),('rate',wintypes.DWORD),
                ('bytes_per_sec',wintypes.DWORD),('align',wintypes.WORD),('bits',wintypes.WORD),('extra',wintypes.WORD)]
        class Header(ctypes.Structure):
            _fields_=[('data',ctypes.c_void_p),('length',wintypes.DWORD),('recorded',wintypes.DWORD),
                ('user',ctypes.c_size_t),('flags',wintypes.DWORD),('loops',wintypes.DWORD),
                ('next',ctypes.c_void_p),('reserved',ctypes.c_size_t)]
        with wave.open(str(path),'rb') as stream:
            self.rate=stream.getframerate();self.align=stream.getnchannels()*stream.getsampwidth()
            # The preview extractor writes PCM stereo 48 kHz, limited to a local MV.
            if stream.getnframes()*self.align>512*1024*1024:
                raise RuntimeError('音轨超过预览缓冲上限（约 46 分钟）；可使用静音预览。')
            self.pcm=stream.readframes(stream.getnframes())
            fmt=Format(1,stream.getnchannels(),self.rate,self.rate*self.align,self.align,stream.getsampwidth()*8,0)
        self.winmm=ctypes.WinDLL('winmm')
        self.winmm.waveOutOpen.argtypes=[ctypes.POINTER(ctypes.c_void_p),ctypes.c_uint,ctypes.POINTER(Format),ctypes.c_size_t,ctypes.c_size_t,ctypes.c_uint]
        for name in ('waveOutPrepareHeader','waveOutUnprepareHeader','waveOutWrite'):
            getattr(self.winmm,name).argtypes=[ctypes.c_void_p,ctypes.POINTER(Header),ctypes.c_uint]
        for name in ('waveOutReset','waveOutClose'):getattr(self.winmm,name).argtypes=[ctypes.c_void_p]
        self.check(self.winmm.waveOutOpen(ctypes.byref(self.handle),0xffffffff,ctypes.byref(fmt),0,0,0))
        self.Header=Header;self.opened=True
    def play(self,stamp,end=None):
        self.pause()
        first=min(len(self.pcm)//self.align,max(0,round(stamp*self.rate)))
        self.offset=first/self.rate;self.stopped_position=self.offset
        last=len(self.pcm)//self.align if end is None else min(len(self.pcm)//self.align,max(first,round(end*self.rate)))
        data=self.pcm[first*self.align:last*self.align]
        if not data:return
        self.buffer=ctypes.create_string_buffer(data)
        self.header=self.Header(ctypes.cast(self.buffer,ctypes.c_void_p),len(data),0,0,0,0,None,0)
        self.check(self.winmm.waveOutPrepareHeader(self.handle,ctypes.byref(self.header),ctypes.sizeof(self.header)))
        self.check(self.winmm.waveOutWrite(self.handle,ctypes.byref(self.header),ctypes.sizeof(self.header)))
        self.started=True
    def position(self):
        if not self.started:return self.stopped_position
        # MMTIME: UINT wType + 8-byte union. Request TIME_SAMPLES.
        data=(ctypes.c_uint32*3)(2,0,0)
        self.winmm.waveOutGetPosition.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint]
        self.check(self.winmm.waveOutGetPosition(self.handle,ctypes.byref(data),ctypes.sizeof(data)))
        elapsed=data[1]/self.rate if data[0]==2 else data[1]/(self.rate*self.align) if data[0]==4 else data[1]/1000
        return self.offset+elapsed
    def is_playing(self):return self.started and self.header is not None and not (self.header.flags & 1)
    def pause(self):
        if not self.opened:return
        if self.started:self.stopped_position=self.position()
        self.check(self.winmm.waveOutReset(self.handle));self.started=False
        if self.header is not None:
            self.check(self.winmm.waveOutUnprepareHeader(self.handle,ctypes.byref(self.header),ctypes.sizeof(self.header)))
            self.header=None;self.buffer=None
    def close(self):
        if self.opened:
            self.pause();self.check(self.winmm.waveOutClose(self.handle));self.opened=False
        self.pcm=b''
