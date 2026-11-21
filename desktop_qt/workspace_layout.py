"""Validated application-only workspace preferences, separate from project undo."""
from __future__ import annotations
import math

DEFAULTS = {'previewFraction': .52, 'topFraction': .58, 'previewHeight': 420.,
            'audioHeight': 160., 'audioHeightExplicit': False, 'audioMode': 'waveform', 'controlsHeight': 0.,
            'previewVisible': True, 'reviewVisible': True, 'timelineVisible': True, 'audioVisible': True, 'spectrumPreset': 'detail', 'spectrumWindow': 384, 'spectrumGain': 0., 'spectrumBlack': 0., 'spectrumTilt': 3., 'spectrumFrequency': 10000, 'spectrumAxis': 'balanced'}
LIMITS = {'previewFraction': (.08, .92), 'topFraction': (.08, .92),
          'previewHeight': (240., 2400.), 'audioHeight': (36., 600.), 'controlsHeight': (-52., 120.), 'spectrumGain': (-12., 12.), 'spectrumBlack': (0., .3)}


def update_layout(current, payload, *, loading=False):
    """Validate all updates before publishing; tolerate obsolete disk entries."""
    if not isinstance(payload, dict):
        if loading:
            return dict(current)
        raise ValueError('无效的工作区布局。')
    if not loading and 'reset' in payload and type(payload['reset']) is not bool:
        raise ValueError('无效的布局重置参数。')
    updated = dict(DEFAULTS if payload.get('reset') is True and not loading else current)
    for key, value in payload.items():
        if key == 'reset' and not loading:
            continue
        if key == 'audioMode' and value in ('waveform', 'spectrum'):
            updated[key] = value
        elif key in ('previewVisible', 'reviewVisible', 'timelineVisible', 'audioVisible', 'audioHeightExplicit') and type(value) is bool:
            updated[key] = value
        elif key == 'spectrumPreset' and value in ('detail', 'balanced', 'contrast', 'vocal', 'onset', 'harmonic', 'physical'):
            updated[key] = {'vocal': 'balanced', 'onset': 'balanced', 'harmonic': 'detail'}.get(value, value)
        elif key == 'spectrumWindow' and type(value) is int and value in (256, 384, 512, 768, 1024):
            updated[key] = value
        elif key == 'spectrumTilt' and type(value) in (int, float) and value in (0,3):
            updated[key] = float(value)
        elif key == 'spectrumFrequency' and type(value) is int and value in (5500,8000,10000,11025):
            updated[key] = value
        elif key == 'spectrumAxis' and value in ('legacy','speech','linear','balanced'):
            updated[key] = value
        elif key in LIMITS and type(value) in (int, float) and math.isfinite(value):
            low, high = LIMITS[key]
            if not loading or low <= value <= high:
                updated[key] = max(low, min(high, float(value)))
        elif not loading:
            raise ValueError('无效的工作区布局。')
    if 'audioHeight' in payload and (not loading or 'audioHeightExplicit' not in payload):
        value=payload['audioHeight']
        if type(value) in (int,float) and math.isfinite(value) and (not loading or LIMITS['audioHeight'][0]<=value<=LIMITS['audioHeight'][1]):
            updated['audioHeightExplicit']=True
    return updated
