"""The shared project clock: media-relative PTS seconds, half-open intervals.

Frame numbers are presentation coordinates only (one based). Never derive time
from average FPS: variable-frame-rate media must use its decoded PTS index.
Audio, subtitle-only editing and future candidates use this same seconds clock.
"""
from dataclasses import dataclass, asdict
import math

TIME_EPSILON = 1e-6


@dataclass(frozen=True)
class Timebase:
    unit: str = "seconds"
    origin: str = "media-relative"
    interval: str = "half-open"
    frame_coordinates: str = "one-based-decoded-pts"

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        virtual=cls(frame_coordinates='one-based-10ms-editing-grid').to_dict()
        if not isinstance(value, dict) or value not in (cls().to_dict(),virtual):
            raise ValueError('不支持的工程时间基准格式。')
        return cls(**value)


def known_time(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def valid_time(start, end):
    return known_time(start) and known_time(end) and start < end


def validate_boundaries(start, end, *, duration=None):
    for value in (start, end):
        if value is not None and not known_time(value):
            raise ValueError('时间必须为大于等于 0 的有效秒数。')
    if start is not None and end is not None and not valid_time(start, end):
        raise ValueError('结束时间必须晚于开始时间。')
    if duration is not None:
        if not known_time(duration):
            raise ValueError('媒体时长无效。')
        if any(v is not None and v > duration + TIME_EPSILON for v in (start, end)):
            raise ValueError('字幕时间超出媒体总时长。')
    return start, end
