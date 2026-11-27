"""Transport clock regression; no Qt window, media file, or audible output."""
from types import SimpleNamespace
from desktop_qt.media import MediaController
import desktop_qt.media as media

def test_silent_transport_does_not_quantize_to_coarse_monotonic(monkeypatch):
    requested=[]
    monkeypatch.setattr(media.time,"monotonic",lambda:100.)
    monkeypatch.setattr(media.time,"perf_counter",lambda:100.006)
    obj=SimpleNamespace(_state={"playing":True,"muted":True,"requestedFrame":0},
        index=SimpleNamespace(current=lambda stamp:round(stamp*1000),display_coordinate=lambda stamp:stamp*1000,stamps=range(100),duration=1.),
        cursor_frame=0.,cursorChanged=SimpleNamespace(emit=lambda:None),
        metadata={"has_audio":False},_clock_stamp=0.,_clock_time=100.,_range_end=None,
        _request=requested.append,changed=SimpleNamespace(emit=lambda:None))
    MediaController._tick(obj)
    assert requested==[6] and abs(obj.cursor_frame-6)<1e-6
