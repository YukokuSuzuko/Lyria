"""Actual decoded-frame index; never frame_number / average_fps."""
from bisect import bisect_left,bisect_right
import math
import hashlib
import json
from .timebase import TIME_EPSILON, known_time


class _TrackedStamps(list):
    """List-compatible PTS storage with conservative mutation invalidation."""
    def __init__(self, values=()):
        super().__init__(values)
        self._version = 0

    def _mutate(self, method, *args, **kwargs):
        try:
            return getattr(super(), method)(*args, **kwargs)
        finally:
            # sort/extend can partially mutate before raising.
            self._version = getattr(self, '_version', 0) + 1

    def __setitem__(self, key, value): return self._mutate('__setitem__', key, value)
    def __delitem__(self, key): return self._mutate('__delitem__', key)
    def __iadd__(self, value): return self._mutate('__iadd__', value)
    def __imul__(self, value): return self._mutate('__imul__', value)
    def append(self, value): return self._mutate('append', value)
    def extend(self, values): return self._mutate('extend', values)
    def insert(self, index, value): return self._mutate('insert', index, value)
    def pop(self, index=-1): return self._mutate('pop', index)
    def clear(self): return self._mutate('clear')
    def remove(self, value): return self._mutate('remove', value)
    def reverse(self): return self._mutate('reverse')
    def sort(self, *args, **kwargs): return self._mutate('sort', *args, **kwargs)

    def __deepcopy__(self, memo):
        result = type(self)(self)
        result._version = self._version
        memo[id(self)] = result
        return result


class FrameIndex:
    def __setattr__(self, name, value):
        if name == 'stamps' and isinstance(value, (list, tuple)):
            value = _TrackedStamps(value)
        if name in ('stamps', 'duration'):
            object.__setattr__(self, '_candidate_cache', None)
        object.__setattr__(self, name, value)

    def candidate_signature(self, *, refresh=False):
        """O(1) on unchanged PTS; recompute only on an explicit worker refresh."""
        if not isinstance(self.stamps, _TrackedStamps):
            raise ValueError('视频帧索引已失效，请重新载入素材。')
        key = (self.stamps._version, self.duration)
        cached = getattr(self, '_candidate_cache', None)
        if cached is not None and cached[:2] == key:
            return cached[2]
        if not refresh:
            raise ValueError('视频帧索引已变动，请重新校验后再应用。')
        stamps = self.stamps
        if (not stamps or not all(known_time(x) for x in stamps)
                or any(b <= a for a,b in zip(stamps,stamps[1:]))
                or not known_time(self.duration) or self.duration <= stamps[-1]):
            raise ValueError('视频帧索引数据无效。')
        value = hashlib.sha256(json.dumps({'stamps':list(stamps),'duration':self.duration},
                                         sort_keys=True,separators=(',',':'),
                                         allow_nan=False).encode('utf-8')).hexdigest()
        self._candidate_cache = (*key,value)
        return value


    def __init__(self,stamps,duration):
        if not stamps or not all(known_time(x) for x in stamps):
            raise ValueError('视频帧时间戳无效')
        if any(b<=a for a,b in zip(stamps,stamps[1:])):raise ValueError('视频帧时间戳顺序异常，建议先转码为标准 MP4')
        if not known_time(duration) or duration<=stamps[-1]:raise ValueError('视频结束时间无效')
        # Own the index; mutating a caller's list must not change frame identity.
        self.stamps=list(stamps);self.duration=duration
        self.candidate_signature(refresh=True)
    @classmethod
    def build(cls,video,cancel=lambda:False):
        import av
        stamps=[];end=None
        with av.open(video.path) as container:
            stream=container.streams.video[0];stream.thread_type='AUTO'
            for frame in container.decode(stream):
                if cancel():raise InterruptedError('已取消视频帧索引加载')
                if frame.pts is None:raise ValueError('视频帧缺少时间戳')
                stamp=float(frame.pts*frame.time_base)-video.origin
                if stamp<0:continue
                stamps.append(stamp)
                if frame.duration:end=stamp+float(frame.duration*frame.time_base)
        return cls(stamps,max(video.info['duration'],end or 0,stamps[-1]+1e-6))
    def first(self,start):return min(len(self.stamps),bisect_left(self.stamps,start-TIME_EPSILON)+1)
    def last(self,end):return max(1,min(len(self.stamps),bisect_left(self.stamps,end-TIME_EPSILON)))
    def current(self,stamp):return max(1,min(len(self.stamps),bisect_right(self.stamps,stamp+TIME_EPSILON)))
    def display_coordinate(self, stamp):
        """Continuous presentation coordinate, independent from integer edit boundaries."""
        frame=self.current(stamp)
        if frame>=len(self.stamps):
            return float(frame)
        start,end=self.stamps[frame-1],self.stamps[frame]
        return frame+max(0.,min(1.,(stamp-start)/(end-start)))

    def frame_span(self, start, end):
        """Project a nonempty cue onto display frames, including a sub-frame cue."""
        first, last = self.first(start), self.last(end)
        if first > last:
            first = last = self.current(start)
        return first, last

    def interval(self,first,last):
        if type(first) is not int or type(last) is not int or not 1<=first<=last<=len(self.stamps):
            raise ValueError(f'帧号需满足 1 ≤ 开始帧 ≤ 结束帧 ≤ {len(self.stamps)}。')
        return self.stamps[first-1],self.stamps[last] if last<len(self.stamps) else self.duration
    def stamp(self,frame):return self.interval(frame,frame)[0]

    def ass_boundary(self,stamp):
        """Centisecond boundary with the same first active frame as the PTS.

        A boundary belongs to (previous PTS, next PTS]. Blind nearest rounding
        can move it above next PTS and delay activation by a whole frame.
        """
        pos=bisect_left(self.stamps,stamp-TIME_EPSILON)
        upper=self.stamps[pos] if pos<len(self.stamps) else self.duration
        lower=self.stamps[pos-1] if pos else -1
        # Aegisub indexes frames using millisecond timecodes. Stay inside the
        # intersection of its interval and the original high-resolution PTS interval.
        upper=min(upper,math.floor(upper*1000+.5)/1000)
        lower=max(lower,math.floor(lower*1000+.5)/1000)
        high=math.floor((upper+TIME_EPSILON)*100)
        low=max(0,math.floor((lower+TIME_EPSILON)*100)+1)
        if low>high:raise ValueError('该帧间隔小于 0.01 秒，超出 ASS 时间精度，请微调相邻帧边界')
        cs=max(low,min(high,round(stamp*100)))
        return cs/100
