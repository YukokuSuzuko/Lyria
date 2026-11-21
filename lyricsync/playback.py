"""Bounded preview decoder. Only its worker owns/closes the video iterator."""
from collections import deque
import threading


class PlaybackBuffer:
    def __init__(self, video, start, end=None, capacity=6):
        self.video, self.start, self.end = video, start, end
        self.capacity = capacity
        self.frames = deque()
        self.condition = threading.Condition()
        self.cancel = threading.Event()
        self.done = False
        self.error = None
        self.decoded = 0
        self.max_buffered = 0
        self.skipped = 0
        self.thread = threading.Thread(target=self._decode, daemon=True)
        self.thread.start()

    def _decode(self):
        iterator = None
        try:
            iterator = self.video.frames(self.start, self.end, cancel=self.cancel.is_set)
            for frame in iterator:
                if self.cancel.is_set() or (self.end is not None and frame[0] >= self.end-1e-6):
                    break
                with self.condition:
                    while len(self.frames) >= self.capacity and not self.cancel.is_set():
                        self.condition.wait()
                    if self.cancel.is_set():break
                    self.frames.append(frame)
                    self.decoded += 1
                    self.max_buffered = max(self.max_buffered, len(self.frames))
        except InterruptedError:
            pass
        except Exception as exc:
            if not self.cancel.is_set():self.error = str(exc)
        finally:
            if iterator is not None:iterator.close()
            with self.condition:
                self.done = True
                self.condition.notify_all()

    def peek(self):
        with self.condition:
            return self.frames[0] if self.frames else None

    def take_due(self, target):
        result = None
        count = 0
        with self.condition:
            while self.frames and self.frames[0][0] <= target+1e-6:
                result = self.frames.popleft()
                count += 1
            self.skipped += max(0, count-1)
            self.condition.notify_all()
        return result

    @property
    def finished(self):
        with self.condition:
            return self.done and not self.frames

    def stop(self):
        self.cancel.set()
        with self.condition:
            self.frames.clear()
            self.condition.notify_all()
