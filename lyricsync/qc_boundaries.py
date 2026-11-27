"""Read the exact union of boundary windows once, retaining every decoded PTS."""
from bisect import bisect_left,bisect_right
import time


class BoundaryObservations:
    def __init__(self,video,clock,visuals,radius,recognize,cancel,progress,track,metrics):
        self.video=video;self.clock=clock;self.radius=radius;self.recognize=recognize
        self.cancel=cancel;self.progress=progress;self.track=track;self.metrics=metrics
        self.ranges=[];self.stamps=[];self.rows=[];self.pending=None;self.finished=False
        self.last_progress=0.;self.last_update=0.;self.started=time.perf_counter()
        windows=sorted(self.window(t) for v in visuals for t in (v.start,v.end))
        for left,right in windows:
            if self.ranges and left<=self.ranges[-1][1]+2e-6:self.ranges[-1][1]=max(self.ranges[-1][1],right)
            else:self.ranges.append([left,right])
        self.total=sum(bisect_right(clock.stamps,right+1e-6)-bisect_left(clock.stamps,left-1e-6) for left,right in self.ranges)
        metrics.update(requested_windows=len(windows),merged_windows=len(self.ranges),decode_windows=0,decoded_frames=0,ocr_requests=0,decode_seconds=0.,ocr_seconds=0.)
        self.stream=self.decoded()

    def check(self):
        if self.cancel():raise InterruptedError('质检已取消。')

    def window(self,center):return max(0,center-self.radius),min(self.video.info['duration'],center+self.radius)

    def decoded(self):
        for left,right in self.ranges:
            self.check();self.metrics['decode_windows']+=1
            iterator=self.video.frames(left,right,cancel=self.cancel)
            try:
                while True:
                    self.check();tick=time.perf_counter()
                    try:item=next(iterator)
                    except StopIteration:break
                    finally:self.metrics['decode_seconds']+=time.perf_counter()-tick
                    self.metrics['decoded_frames']+=1
                    yield item
            finally:
                if hasattr(iterator,'close'):iterator.close()

    def neighborhood(self,center):
        left,right=self.window(center)
        while not self.finished:
            self.check()
            if self.pending is None:
                try:self.pending=next(self.stream)
                except StopIteration:self.finished=True;break
            stamp,frame=self.pending
            if stamp>right+1e-6:break
            self.pending=None
            number=self.clock.frame(stamp);tick=time.perf_counter()
            result=self.recognize(frame);self.check()
            self.metrics['ocr_seconds']+=time.perf_counter()-tick;self.metrics['ocr_requests']+=1
            self.stamps.append(stamp);self.rows.append({**self.clock.evidence(number),'ocr':result})
            now=time.perf_counter()
            if now-self.last_update>=.1:
                self.last_update=now;fraction=min(.99,len(self.rows)/max(1,self.total))
                self.last_progress=max(self.last_progress,fraction)
                self.progress(self.last_progress,f'{self.track}：逐帧扫描进出点 {len(self.rows)}/{self.total}')
        return self.rows[bisect_left(self.stamps,left-1e-6):bisect_right(self.stamps,right+1e-6)]

    def close(self):
        self.stream.close();self.pending=None
        self.metrics['total_seconds']=time.perf_counter()-self.started
