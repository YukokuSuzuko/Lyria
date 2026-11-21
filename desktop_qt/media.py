"""Qt Quick video presentation with worker-owned PyAV and a real PTS index.

This adapter deliberately does not claim zero-copy or hardware decode. Decoding,
indexing and RGB conversion happen on one worker; only QVideoSink presentation
happens on the GUI thread. One replaceable mailbox bounds seek work. Playback
uses Qt's audio transport clock; silent media uses a monotonic clock.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import OrderedDict
from dataclasses import dataclass
import math
from pathlib import Path
import threading
import time

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot, Qt, QMetaObject, QThread
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame, QVideoSink

from lyricsync.frames import FrameIndex
from .playback_clock import AudioSeekTimeout


def stop_media_player(player):
    """Synchronously stop a QMediaPlayer on its owner thread, releasing the GIL.

    PySide 6.8's direct stop() binding can keep the GIL while Qt waits for
    native media threads that need Python callbacks to finish. invokeMethod
    explicitly releases it. DirectConnection keeps the stop synchronous and
    on this thread: this is not a deferred stop or abandoned backend cleanup.
    """
    if QThread.currentThread() != player.thread():
        raise RuntimeError('媒体播放器必须在所属线程停止。')
    if not QMetaObject.invokeMethod(player, 'stop', Qt.ConnectionType.DirectConnection):
        raise RuntimeError('媒体播放器停止失败。')


@dataclass(frozen=True)
class Request:
    epoch: int
    transport: int
    sequence: int
    frame: int
    requested_at: float


class _DecodeWorker:
    """One worker, one decoder, one pending request. Never touches GUI objects."""
    def __init__(self, signals):
        self.signals = signals
        self.condition = threading.Condition()
        self.source = None
        self.request = None
        self.epoch = 0
        self.stopped = False
        self.exit_error = None
        self.thread = threading.Thread(target=self._run_guarded, name="lyria-video-decode", daemon=True)
        self.thread.start()

    def _run_guarded(self):
        try:self.run()
        except BaseException as exc:
            # Keep cleanup failures inspectable by the owning GUI lifecycle.
            self.exit_error=exc
            self.emit(self.signals._failed,self.epoch,'解码器已停止：'+type(exc).__name__)

    def open(self, path, epoch):
        with self.condition:
            self.epoch = epoch
            self.source = (path, epoch)
            self.request = None
            self.condition.notify()

    def seek(self, request):
        with self.condition:
            self.request = request
            self.condition.notify()

    def stop(self):
        with self.condition:
            self.stopped = True
            self.epoch += 1
            self.request = None
            self.condition.notify()

    def stale(self, epoch):
        return self.stopped or self.epoch != epoch

    def emit(self, signal, *args):
        if not self.stopped:
            try:
                signal.emit(*args)
            except RuntimeError:  # Qt object may already be destroyed at process shutdown.
                pass

    def index_source(self, path, epoch):
        import av
        stamps, pts, end = [], [], None
        with av.open(path) as container:
            video_streams=[s for s in container.streams.video if not getattr(s,'disposition',av.stream.Disposition(0)) & av.stream.Disposition.attached_pic]
            if not video_streams:
                if not container.streams.audio:
                    raise ValueError("所选文件没有视频或音频轨道。")
                audio = container.streams.audio[0]
                duration = float(audio.duration * audio.time_base) if audio.duration else float((container.duration or 0) / av.time_base)
                if duration <= 0:
                    for frame in container.decode(audio):
                        if self.stale(epoch): return None
                        duration = max(duration, float((frame.pts or 0) * frame.time_base) + frame.samples / frame.sample_rate)
                from lyricsync.virtual_timeline import VirtualFrameIndex
                from lyricsync.storage import file_hash
                index = VirtualFrameIndex(duration)
                return index, dict(width=0,height=0,duration=duration,has_audio=True,
                                   has_video=False,media_kind='audio',fps=100,origin=0,
                                   timestamp_source='10 ms editing grid; not video PTS',
                                   frame_count=len(index.stamps),content_hash=file_hash(path,lambda:self.stale(epoch)))
            stream = video_streams[0]
            stream.thread_type = "AUTO"
            origin = float(container.start_time / av.time_base) if container.start_time is not None else float((stream.start_time or 0) * stream.time_base)
            stream_base = stream.time_base
            metadata = {"width": stream.width, "height": stream.height,
                        "origin": origin, "time_base": str(stream_base),
                        "fps": float(stream.average_rate or 0),
                        "has_audio": bool(container.streams.audio),
                        "timestamp_source": "decoded integer PTS × time_base − container origin"}
            last_progress = 0
            for frame in container.decode(stream):
                if self.stale(epoch):
                    return None
                if frame.pts is None:
                    raise ValueError('视频缺少帧时间戳信息，无法逐帧精确定位，建议重新封装后再试。')
                stamp = float(frame.pts * frame.time_base) - origin
                if stamp < -1e-6:
                    continue
                if stamps and stamp <= stamps[-1]:
                    raise ValueError('视频帧时间戳异常（存在乱序或重复），无法建立视频帧轴，建议重新转码或封装。')
                stamps.append(max(0.0, stamp)); pts.append(frame.pts)
                if frame.duration:
                    end = stamp + float(frame.duration * frame.time_base)
                now = time.monotonic()
                if now-last_progress > .25:
                    self.emit(self.signals._progress, epoch, len(stamps))
                    last_progress = now
            if not stamps:
                raise ValueError('无法从该文件中解码出视频画面。')
            if end is None or end <= stamps[-1]:
                # An average rate or copied previous interval is not authoritative
                # for a VFR tail. Use the stream's declared end, otherwise refuse
                # to invent an export boundary while claiming frame precision.
                stream_end = float((stream.start_time or 0) * stream_base + (stream.duration or 0) * stream_base) - origin
                if stream_end <= stamps[-1]:
                    raise ValueError('视频最后一帧缺少时长信息，请检查文件或重新封装后再试。')
                end=stream_end
                metadata["tail_boundary_source"] = "stream declared duration"
            else:
                metadata["tail_boundary_source"] = "decoded final frame duration"
            deltas=[b-a for a,b in zip(stamps,stamps[1:])]
            metadata['vfr']=bool(deltas and max(deltas)-min(deltas)>max(1e-6,float(stream_base)*1.5))
            metadata.update(duration=end, frame_count=len(stamps), frame_pts=pts,
                            pts_time_base=str(stream_base), frame_stamps=stamps,
                            index_version=1)
        from lyricsync.storage import file_hash
        metadata['content_hash']=file_hash(path,lambda:self.stale(epoch))
        return FrameIndex(stamps, end), metadata

    def run(self):
        import av
        container = None
        current_epoch, index, metadata, iterator = -1, None, None, None
        cursor=None; seek_count=decoded_count=0; previous_wanted=None
        cache, cache_bytes = OrderedDict(), 0
        # Explicit application-held budgets, excluding opaque codec buffers and
        # one active RGB conversion / submitted frame. Native frames avoid paying
        # RGB conversion for every predecessor that the user may skip.
        native_cache, native_bytes = OrderedDict(), 0
        rgb_peak_bytes=native_peak_bytes=0
        rgb_limit=96*1024*1024;native_limit=32*1024*1024
        def as_image(frame):
            pixels=frame.to_ndarray(format='rgb24')
            return QImage(pixels.data,pixels.shape[1],pixels.shape[0],pixels.strides[0],QImage.Format.Format_RGB888).copy()
        def remember_image(frame_number,picture):
            nonlocal cache_bytes,rgb_peak_bytes
            if frame_number in cache:cache_bytes-=cache.pop(frame_number).sizeInBytes()
            cache[frame_number]=picture;cache_bytes+=picture.sizeInBytes()
            while cache and (len(cache)>12 or cache_bytes>rgb_limit):
                _,old=cache.popitem(last=False);cache_bytes-=old.sizeInBytes()
            rgb_peak_bytes=max(rgb_peak_bytes,cache_bytes)
        def remember_native(frame_number,frame):
            nonlocal native_bytes,native_peak_bytes
            if frame_number in native_cache:native_bytes-=native_cache.pop(frame_number)[1]
            size=sum(plane.buffer_size for plane in frame.planes)
            if size>native_limit:return
            native_cache[frame_number]=(frame,size);native_bytes+=size
            while native_cache and (len(native_cache)>11 or native_bytes>native_limit):
                _,(_,size)=native_cache.popitem(last=False);native_bytes-=size
            native_peak_bytes=max(native_peak_bytes,native_bytes)
        def metrics():
            return {'decoderSeeks':seek_count,'decodedFrames':decoded_count,'cacheBytes':cache_bytes,
                    'nativeCacheBytes':native_bytes,'nativeCacheFrames':len(native_cache),
                    'rgbCachePeakBytes':rgb_peak_bytes,'nativeCachePeakBytes':native_peak_bytes}
        try:
            while True:
                with self.condition:
                    while not self.stopped and self.source is None and self.request is None:
                        self.condition.wait()
                    if self.stopped:
                        return
                    source, request = self.source, self.request
                    self.source = self.request = None
                if source:
                    path, current_epoch = source
                    if container is not None:
                        container.close(); container = None
                    cache.clear();cache_bytes=0;native_cache.clear();native_bytes=0;rgb_peak_bytes=native_peak_bytes=0
                    iterator=None;cursor=None;decoded=None;picture=None;seek_count=decoded_count=0;previous_wanted=None
                    if not path:
                        index=None; metadata=None
                        continue
                    try:
                        result = self.index_source(path, current_epoch)
                        if result is None or self.stale(current_epoch):
                            continue
                        index, metadata = result
                        if metadata.get('has_video',True):
                            container = av.open(path)
                            stream = next(s for s in container.streams.video if not getattr(s,'disposition',av.stream.Disposition(0)) & av.stream.Disposition.attached_pic)
                            stream.thread_type = "AUTO"
                        self.emit(self.signals._indexed, current_epoch, index, metadata)
                    except Exception as exc:
                        self.emit(self.signals._failed, current_epoch, f'读取媒体文件失败（{type(exc).__name__}）：{exc}')
                    continue
                if not request or request.epoch != current_epoch or container is None or index is None:
                    continue
                try:
                    wanted=request.frame;delivered=False
                    backwards=previous_wanted is not None and wanted<previous_wanted
                    previous_wanted=wanted
                    if wanted in cache:
                        picture = cache[wanted]
                        cache.move_to_end(wanted)
                    elif wanted in native_cache:
                        decoded,size=native_cache.pop(wanted);native_bytes-=size
                        picture=as_image(decoded);remember_image(wanted,picture)
                    else:
                        target = index.stamp(wanted)
                        # Continue the existing decoder for nearby monotonic playback.
                        # Only backwards/unbounded jumps seek to a keyframe; otherwise
                        # each compressed frame is decoded at most once in this run.
                        if iterator is None or cursor is None or wanted<=cursor or wanted-cursor>120:
                            container.seek(int((target + metadata['origin']) / stream.time_base),
                                           stream=stream, backward=True, any_frame=False)
                            iterator=container.decode(stream);cursor=None;seek_count+=1
                        picture = None
                        for decoded in iterator:
                            if self.stale(current_epoch):
                                iterator=None;cursor=None
                                break
                            # A newer *paused* seek is allowed to cancel this decode early.
                            with self.condition:
                                newer = self.request
                            if newer and (newer.transport != request.transport):
                                # for/next already consumed this frame. Reusing the
                                # prior cursor would skip a new target equal to it.
                                iterator=None;cursor=None
                                break
                            if decoded.pts is None:
                                raise ValueError('视频帧缺少时间戳信息')
                            stamp = float(decoded.pts * decoded.time_base)-metadata["origin"]
                            cursor=index.current(stamp);decoded_count+=1
                            if stamp < target-1e-6:
                                if backwards and wanted-11<=cursor<wanted and abs(stamp-index.stamp(cursor))<=1e-6:
                                    remember_native(cursor,decoded)
                                continue
                            if abs(stamp-target)>1e-6:
                                raise ValueError('视频帧定位存在偏差，请重试或重新封装视频')
                            picture=as_image(decoded)
                            break
                        if picture is None:
                            continue
                        remember_image(wanted,picture)
                        self.emit(self.signals._decoded,request,picture,index.stamp(wanted),metrics())
                        delivered=True
                        # Cache a small number of next frames from the same decode session.
                        for future in (() if backwards else range(wanted+1,min(wanted+5,len(index.stamps)+1))):
                            if self.stale(current_epoch):
                                break
                            with self.condition:
                                # Any pending target takes priority over speculative work.
                                if self.request is not None:
                                    break
                            decoded = next(iterator, None)
                            if decoded is None or decoded.pts is None:
                                break
                            stamp = float(decoded.pts*decoded.time_base)-metadata["origin"]
                            cursor=index.current(stamp);decoded_count+=1
                            if abs(stamp-index.stamp(future)) > 1e-6:
                                break
                            remember_image(future,as_image(decoded))
                    if not delivered and not self.stale(current_epoch):
                        self.emit(self.signals._decoded,request,picture,index.stamp(wanted),
                                  metrics())
                except Exception as exc:
                    iterator=None;cursor=None
                    self.emit(self.signals._failed,current_epoch,f"视频解码失败：{type(exc).__name__}: {exc}")
        finally:
            if container is not None:
                container.close()


class MediaController(QObject):
    changed = Signal()
    cursorChanged = Signal()
    frameReady = Signal(QImage)
    presented = Signal(int,float)
    loaded = Signal(object, object)
    error = Signal(str)
    _indexed = Signal(int, object, object)
    _decoded = Signal(object, QImage, float, object)
    _failed = Signal(int, str)
    _progress = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.index = None
        self.metadata = {}
        self.path = ""
        self.epoch = self.transport = self.sequence = 0
        self._requested_generation = None
        self.sink = None
        self._last_image = None
        self._state = dict(ready=False, loaded=False, displayedFrame=0, requestedFrame=0,
                           totalFrames=0, positionSeconds=0.0, durationSeconds=0.0,
                           playing=False, status="未导入视频", width=0, height=0,
                           fpsLabel="—", audioStatus="未载入", clock="none",
                           seekLatencyMs=0.0, presentedFrames=0,decoderSeeks=0,decodedFrames=0,cacheBytes=0,nativeCacheBytes=0,nativeCacheFrames=0,rgbCachePeakBytes=0,nativeCachePeakBytes=0,muted=False,scrubbing=False)
        from .playback_clock import AudioPositionClock
        self._audio_clock=AudioPositionClock(max_prediction=.12)
        self.cursor_frame=0.
        self._audio_seek=None
        self._closing=False
        self.player=QMediaPlayer(self)
        self.audio=QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        # QMediaPlayer is our audio/clock backend; visible frames come from
        # the independent indexed decoder. Give its native video pipeline a
        # private drain nonetheless: attached PNG cover art can otherwise
        # leave Qt FFmpeg stop() blocked even without a visible video output.
        # This sink is never attached to the preview; queued track disabling
        # below still prevents sustained duplicate timed-video decoding.
        self._audio_frame_drain = QVideoSink(self)
        self.player.setVideoSink(self._audio_frame_drain)
        # QMediaPlayer emits status/error synchronously inside native transport
        # calls too. Do not reenter that backend via pause/track selection.
        self.player.errorOccurred.connect(self._audio_error, Qt.ConnectionType.QueuedConnection)
        self.player.mediaStatusChanged.connect(self._audio_status, Qt.ConnectionType.QueuedConnection)
        self.timer=QTimer(self); self.timer.setTimerType(Qt.TimerType.PreciseTimer); self.timer.setInterval(16); self.timer.timeout.connect(self._tick)
        # Transport interpolation needs a high-resolution monotonic clock on Windows.
        # Keep request-latency diagnostics on their existing matching time base.
        self._clock_time=0.; self._clock_stamp=0.; self._range_end=None; self._loop_range=None; self._play_after_seek=False; self._audio_failed=False
        self._indexed.connect(self._accept_index)
        self._decoded.connect(self._present)
        self._failed.connect(self._error)
        self._progress.connect(self._index_progress)
        self.worker=_DecodeWorker(self)

    def snapshot(self):
        # The playhead is the user's transport intent while seeking, not a claim
        # that a delayed decoder has already presented that frame.
        playhead=(self._state['displayedFrame'] if self._state['playing'] else
                  self._state['requestedFrame'] or self._state['displayedFrame'])
        return dict(self._state,mediaKind=self.metadata.get("media_kind", "video" if self.index else "none"),playheadFrame=playhead,
                    seekPending=bool(self._state['requestedFrame'] != self._state['displayedFrame']),
                    playPending=bool(self._play_after_seek),loopRange=bool(self._loop_range))

    def attach_sink(self, sink):
        if sink is not None and not isinstance(sink, QVideoSink):
            raise ValueError('视频输出组件初始化异常（VideoSink 无效）。')
        self.sink=sink
        if sink and self._last_image is not None:
            sink.setVideoFrame(QVideoFrame(self._last_image))

    def clear(self):
        self.pause(); self.epoch+=1; self.transport+=1
        self.path=''; self.index=None; self.metadata={}; self._last_image=None
        self.player.setSource(QUrl()); self.worker.open('',self.epoch);self.frameReady.emit(QImage())
        self._audio_frame_drain.setVideoFrame(QVideoFrame())
        if self.sink:self.sink.setVideoFrame(QVideoFrame())
        self._state.update(ready=False,loaded=False,displayedFrame=0,requestedFrame=0,totalFrames=0,positionSeconds=0.,durationSeconds=0.,playing=False,status='未导入视频',width=0,height=0,fpsLabel='—',audioStatus='未载入',clock='none')
        self.changed.emit()

    def open(self,path):
        path=str(Path(path).expanduser().resolve())
        if not Path(path).is_file():
            raise ValueError('找不到该媒体文件，请检查路径是否存在。')
        self.pause(); self.epoch+=1; self.transport+=1
        self.path=path; self.index=None; self.metadata={}; self._last_image=None; self._audio_failed=False
        self.frameReady.emit(QImage())
        if self.sink:
            self.sink.setVideoFrame(QVideoFrame())
        self._state.update(ready=False,loaded=False,displayedFrame=0,requestedFrame=0,
                           totalFrames=0,positionSeconds=0.,durationSeconds=0.,status='正在建立视频帧索引…')
        self.player.setSource(QUrl.fromLocalFile(path))
        self.worker.open(path,self.epoch); self.changed.emit()

    def open_virtual(self, duration=300.):
        from lyricsync.virtual_timeline import VirtualFrameIndex
        index=VirtualFrameIndex(duration)  # Validate before retiring the old source.
        self.clear()
        self._accept_index(self.epoch,index,dict(width=0,height=0,duration=duration,
            has_audio=False,has_video=False,media_kind='virtual',fps=100,
            timestamp_source='10 ms editing grid; not video PTS'))

    def seek_seconds(self, seconds):
        if self.index is None or type(seconds) not in (int,float) or not math.isfinite(seconds):
            raise ValueError('跳转时间无效。')
        self.seek(self.index.current(max(0.,min(self.index.duration,float(seconds)))))

    @Slot(int,object,object)
    def _accept_index(self,epoch,index,metadata):
        if epoch!=self.epoch:return
        self.index,self.metadata=index,metadata
        fps=metadata.get('fps',0)
        self._state.update(ready=True,loaded=True,totalFrames=len(index.stamps),
                           durationSeconds=index.duration,width=metadata['width'],height=metadata['height'],
                           fpsLabel=(f"{fps:.3f} fps · "+('可变帧率' if metadata.get('vfr') else 'PTS')) if fps else "VFR · PTS",status="视频就绪",
                           audioStatus='音轨正常' if metadata['has_audio'] else "无音轨",clock="audio" if metadata['has_audio'] else "monotonic")
        if not metadata.get('has_video',True):
            self._state.update(fpsLabel='10 毫秒精度时间轴',status='音频就绪' if metadata['has_audio'] else '纯时间轴模式已就绪')
        self.loaded.emit(metadata,index)
        # A project identity validator may reject and clear this media in loaded.
        if epoch==self.epoch and self.index is index:self.seek(1)

    @Slot(int,int)
    def _index_progress(self,epoch,count):
        if epoch==self.epoch:
            self._state['status']=f'正在建立视频帧索引… 已扫描 {count} 帧'
            self.changed.emit()

    def seek(self,frame, *, keep_playing=False):
        if self.index is None:raise ValueError('视频帧索引尚未就绪，请稍候。')
        if type(frame) is not int or not 1<=frame<=len(self.index.stamps):
            raise ValueError('目标帧号超出视频范围。')
        if not keep_playing:self.pause()
        self._state['scrubbing']=False;self.transport+=1;self._audio_clock.reset()
        if self.metadata.get('has_audio'):
            target_ms=round(self.index.stamp(frame)*1000)
            self.player.setPosition(target_ms)
            self._audio_seek=(self.epoch,self.transport,target_ms)
        self._clock_stamp=self.index.stamp(frame); self._clock_time=time.perf_counter()
        self._request(frame); self.changed.emit()

    def scrub(self,frame,begin=False):
        if self.index is None or type(frame) is not int or not 1<=frame<=len(self.index.stamps):raise ValueError('定位帧号超出视频范围。')
        if begin or not self._state['scrubbing']:
            self.pause();self.transport+=1;self._state['scrubbing']=True
        elif frame == self._state['requestedFrame']:
            return  # Pointer motion within one frame must not flood Qt/decoder work.
        self._request(frame);self.changed.emit()

    def _request(self,frame):
        self._requested_generation=(self.epoch,self.transport)
        self.sequence+=1
        self._state['requestedFrame']=frame
        if not self.metadata.get('has_video',True):
            stamp=self.index.stamp(frame)
            self._state.update(displayedFrame=frame,positionSeconds=stamp,status='已定位')
            self.presented.emit(frame,stamp)
        else:
            self.worker.seek(Request(self.epoch,self.transport,self.sequence,frame,time.monotonic()))

    def step(self,delta):
        if type(delta) is not int or delta not in (-5,-1,1,5):
            raise ValueError("仅支持 ±1 或 ±5 帧。")
        if self.index is None:raise ValueError("请先载入视频。")
        current=self._state['requestedFrame'] or self._state['displayedFrame'] or 1
        self.seek(max(1,min(len(self.index.stamps),current+delta)))

    def play(self):
        if self.index is None:raise ValueError("请先载入视频。")
        if self._state['playing']:return
        if self.metadata.get('has_audio') and not self._state['muted'] and self._audio_failed:raise ValueError('音频设备或音轨不可用，请开启静音后再播放（逐帧定位不受影响）。')
        current=self._state['displayedFrame'];target=self._state['requestedFrame'] or current or 1
        if self._state['scrubbing']:
            self.seek(target)  # Finish the gesture with a strict exact generation.
        if target!=current or not current:
            # Pause can invalidate a scrub request without changing its target.
            # Never wait on a generation that can no longer be presented.
            if self._requested_generation != (self.epoch,self.transport):self.seek(target)
            self._play_after_seek=True;self._state['status']='正在定位画面…';self.changed.emit()
            return
        self._clock_stamp=self.index.stamp(current); self._clock_time=time.perf_counter()
        self._audio_clock.reset(anchor=self._clock_stamp, now=self._clock_time)
        self.cursor_frame=float(current)
        self.cursorChanged.emit()
        if self.metadata.get('has_audio') and not self._state['muted']:
            target_ms=round(self._clock_stamp*1000)
            issued=self._audio_seek==(self.epoch,self.transport,target_ms)
            if not issued and abs(self.player.position()-target_ms)>25:
                self.player.setPosition(target_ms)
            self._audio_seek=None
            self.player.play()
        self._state['playing']=True; self.timer.start(); self.changed.emit()

    def mute(self,value):
        if type(value) is not bool:raise ValueError('静音参数无效。')
        was_playing=self._state['playing'];self.pause();self._state['muted']=value
        self.audio.setMuted(value)
        self._state['clock']='monotonic-muted' if value else ('audio' if self.metadata.get('has_audio') else 'monotonic')
        self._state['audioStatus']='已静音' if value else ('音轨正常' if self.metadata.get('has_audio') else '无音轨')
        self.changed.emit()
        if was_playing:self.play()

    def pause(self):
        self.timer.stop()
        self.player.pause()
        self._audio_clock.reset()
        was_playing=self._state['playing'];was_pending=self._play_after_seek;self._state['playing']=False
        self._range_end=None;self._loop_range=None;self._play_after_seek=False
        if self._state['scrubbing']:
            self._state['scrubbing']=False;self.transport+=1;self._audio_clock.reset()
        if was_playing:
            self.transport+=1
            # Preserve the actual displayed frame; an outstanding playback request
            # cannot move the paused playhead after release.
            self._state['requestedFrame']=self._state['displayedFrame']
            self.changed.emit()
        elif was_pending:self._state['status']='已暂停';self.changed.emit()

    def play_range(self,first,last,loop=False):
        if self.index is None:raise ValueError("请先载入视频。")
        first=max(1,first);last=min(len(self.index.stamps),last)
        self.seek(first); self._range_end=last
        self._loop_range=(first,last) if loop else None
        # Wait for exact presentation before starting transport.
        if not self.metadata.get('has_video',True): self.play()
        else: self._play_after_seek=True
        self.changed.emit()

    @Slot(object,QImage,float,object)
    def _present(self,request,image,stamp,metrics):
        if request.epoch!=self.epoch or request.transport!=self.transport:return
        if not self._state['playing'] and not self._state['scrubbing'] and request.sequence!=self.sequence:return
        if self._state['playing'] and request.frame<self._state['displayedFrame']:return
        self._last_image=image
        self.frameReady.emit(image)
        if self.sink:
            video_frame=QVideoFrame(image)
            video_frame.setStartTime(round(stamp*1_000_000))
            video_frame.setEndTime(round(self.index.interval(request.frame,request.frame)[1]*1_000_000))
            self.sink.setVideoFrame(video_frame)
        self._state.update(metrics)
        self._state.update(displayedFrame=request.frame,positionSeconds=stamp,
                           status="播放中" if self._state['playing'] else '已定位',
                           seekLatencyMs=(time.monotonic()-request.requested_at)*1000,
                           presentedFrames=self._state['presentedFrames']+1)
        self.changed.emit();self.presented.emit(request.frame,stamp)
        if getattr(self,'_play_after_seek',False):
            self._play_after_seek=False; self.play()

    def _tick(self):
        if not self._state['playing'] or self.index is None:return
        if self.metadata.get('has_audio') and not self._state['muted']:
            running=self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState and self.player.mediaStatus()!=QMediaPlayer.MediaStatus.StalledMedia
            try:
                stamp=self._audio_clock.sample(self.player.position()/1000,time.perf_counter(),running)
            except AudioSeekTimeout:
                self._state['audioStatus']='音频缓冲超时'
                self._error(self.epoch,'音频定位超时，已自动暂停；请重新播放或切换为静音预览。')
                return
        else:
            stamp=self._clock_stamp+time.perf_counter()-self._clock_time
        frame=self.index.current(stamp)
        end=self._range_end or len(self.index.stamps)
        if frame>end or stamp>=self.index.duration:
            if self._loop_range:
                first,last=self._loop_range
                self.play_range(first,last,loop=True)
            else:self.pause();self.seek(end)
            return
        cursor=self.index.display_coordinate(stamp)
        if self.cursor_frame!=cursor:
            self.cursor_frame=cursor
            self.cursorChanged.emit()
        if frame!=self._state['requestedFrame']:
            self._request(frame); self.changed.emit()

    @Slot(int,str)
    def _error(self,epoch,message):
        if epoch==self.epoch:
            self.pause(); self._state['status']=message; self.changed.emit(); self.error.emit(message)

    @Slot(QMediaPlayer.Error, str)
    def _audio_error(self,error,error_string):
        if self._closing:return
        # A queued notification may have been superseded by a new source/state.
        # These checks reject known-stale events, not a full source-generation ID.
        if (error == QMediaPlayer.Error.NoError or self.player.error() != error
                or self.player.errorString() != error_string):return
        self._audio_failed=True
        if self.metadata.get('has_audio'):
            self.pause(); self._state['audioStatus']='音频播放异常'
            self.changed.emit()
            self.error.emit('音频播放失败，请检查系统音频输出设备或媒体音轨（逐帧定位仍可正常使用）。')

    @Slot(QMediaPlayer.MediaStatus)
    def _audio_status(self,status):
        if self._closing or status != self.player.mediaStatus():return
        if (status in (QMediaPlayer.MediaStatus.LoadedMedia,QMediaPlayer.MediaStatus.BufferedMedia)
                and self.player.activeVideoTrack() != -1):
            self.player.setActiveVideoTrack(-1)

    def close(self):
        if self._closing:return
        self._closing=True
        self.pause()
        self.worker.stop()
        stop_media_player(self.player)
        self._audio_frame_drain.setVideoFrame(QVideoFrame())
        self.epoch+=1
