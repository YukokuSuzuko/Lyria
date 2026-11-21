"""Deterministic worker cursor safety; no GUI throughput or Windows claims."""
import threading
import time
from fractions import Fraction
import numpy as np
import pytest
pytest.importorskip('PySide6')
from desktop_qt.media import _DecodeWorker,Request
from lyricsync.frames import FrameIndex


def test_cancel_after_iterator_consumed_frame_cannot_skip_new_exact_target(monkeypatch):
    class Signal:
        def __init__(self,callback):self.callback=callback
        def emit(self,*args):self.callback(*args)
    class Frame:
        def __init__(self,number):
            self.number=number;self.pts=number-1;self.time_base=Fraction(1,25)
        def to_ndarray(self,format):return np.full((2,2,3),self.number,dtype=np.uint8)
        @property
        def planes(self):return []
    class Stream:
        time_base=Fraction(1,25)
    class Container:
        def __init__(self):
            self.streams=type('Streams',(),{'video':[Stream()]})()
            self.seeks=0;self.triggered=False;self.closed=False
        def seek(self,*args,**kwargs):self.seeks+=1
        def decode(self,stream):
            for number in range(1,11):
                if number==4 and not self.triggered:
                    self.triggered=True;worker.seek(Request(1,2,2,4,time.monotonic()))
                yield Frame(number)
        def close(self):self.closed=True
    worker=_DecodeWorker.__new__(_DecodeWorker)
    worker.condition=threading.Condition();worker.source=('fake',1)
    worker.request=None;worker.epoch=1;worker.stopped=False
    index=FrameIndex([n/25 for n in range(10)],.4)
    worker.index_source=lambda path,epoch:(index,{'origin':0})
    errors=[];accepted=[]
    def on_index(*args):worker.seek(Request(1,1,1,8,time.monotonic()))
    def on_error(*args):errors.append(args);worker.stop()
    def on_frame(request,image,stamp,metrics):
        accepted.append((request,image.pixelColor(0,0).red(),stamp,metrics));worker.stop()
    worker.signals=type('Signals',(),{'_indexed':Signal(on_index),'_failed':Signal(on_error),'_decoded':Signal(on_frame)})()
    container=Container();monkeypatch.setattr('av.open',lambda *args,**kwargs:container)
    worker.run()
    assert not errors,'old cursor skipped the frame consumed just before transport cancellation'
    assert len(accepted)==1 and accepted[0][0].frame==4
    assert accepted[0][0].transport==2 and accepted[0][0].sequence==2
    assert accepted[0][1:3]==(4,.12)  # Actual pixel and PTS, not requested-frame label alone.
    assert container.seeks==2 and container.closed
