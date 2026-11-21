"""10 ms editing coordinates for audio/fileless projects, not video frames.

Imported second boundaries are never quantized by this index. Only explicit
frame-coordinate gestures snap to the editing grid. Stamps are lazy (O(1) RAM).
"""
import math
import hashlib
import json
from collections.abc import Sequence
from .frames import FrameIndex

class _Grid(Sequence):
    def __init__(self, count): self.count = count
    def __len__(self): return self.count
    def __getitem__(self, index):
        if isinstance(index, slice): return [self[i] for i in range(*index.indices(self.count))]
        if index < 0: index += self.count
        if not 0 <= index < self.count: raise IndexError(index)
        return index / 100.0

class VirtualFrameIndex(FrameIndex):
    def __init__(self, duration):
        if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 86400:
            raise ValueError("Editing duration must be between 0 and 86400 seconds.")
        self.duration = float(duration)
        self.stamps = _Grid(max(1, math.ceil(duration * 100)))


    def candidate_signature(self, *, refresh=False):
        # A virtual grid is a formula, not millions of decoded media PTS.
        if (type(self.stamps) is not _Grid or not isinstance(self.stamps.count,int)
                or not math.isfinite(self.duration) or not 0 < self.duration <= 86400
                or self.stamps.count != max(1,math.ceil(self.duration*100))):
            raise ValueError('虚拟编辑时间轴无效。')
        return hashlib.sha256(json.dumps({'grid':'10ms','count':self.stamps.count,
                                         'duration':self.duration},
                                        sort_keys=True,separators=(',',':')).encode()).hexdigest()
