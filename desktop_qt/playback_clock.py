"""Bounded audio interpolation and finite asynchronous-seek confirmation."""


class AudioSeekTimeout(TimeoutError):
    """The audio backend did not report a reachable post-seek position."""


class AudioPositionClock:
    def __init__(self, max_prediction=.075, seek_timeout=2.):
        self.max_prediction=max_prediction
        self.seek_timeout=seek_timeout
        self.reset()

    def reset(self, anchor=None, now=None):
        self.anchor=anchor
        # MediaController supplies the monotonic time when issuing setPosition.
        # Standalone callers without it start the wait at their first sample.
        self.seek_started_at=now
        self.position=None
        self.observed_at=0.
        self.last=0.
        self.advancing=False

    def sample(self, position, now, running=True):
        if self.anchor is not None:
            if self.seek_started_at is None:
                self.seek_started_at=now
            waited=max(0.,now-self.seek_started_at)
            # A delayed GUI sample may already be well past the seek target.
            # Confirm positions reachable by 1x playback since setPosition, not
            # a fixed +/-250ms gate that a successful seek can jump over forever.
            # The 2ms lower allowance covers QMediaPlayer's integer-ms rounding.
            reachable=self.anchor-.002 <= position <= self.anchor+waited+.25
            if not reachable:
                if waited >= self.seek_timeout:
                    raise AudioSeekTimeout('音频跳转超时，未能定位到目标位置')
                return self.anchor
            position=max(position,self.anchor)
            self.anchor=None
        if self.position is None or position < self.position-.25:
            self.position=position;self.observed_at=now;self.last=position;self.advancing=False
        elif position!=self.position:
            self.advancing=position>self.position
            self.position=position;self.observed_at=now
        # Never invent unbounded audio progress during a stall. Explicit seeks,
        # pause/resume and mute transitions reset this independent estimator.
        elapsed=min(self.max_prediction,max(0.,now-self.observed_at)) if running and self.advancing else 0.
        self.last=max(self.last,position+elapsed)
        return self.last
