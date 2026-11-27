"""Conservative optional end-only silence guard; raw CTC evidence is immutable.

A quiet gap is a diagnostic observation, not proof a lyric was sung. Never trim
sustained voiced vowels solely because their duration is long.
"""
import wave
import math


def inspect_tail_spans(rows,audio_path,*,enabled=False):
    import numpy as np
    power=[]
    with wave.open(str(audio_path),'rb') as w:
        if (w.getframerate(),w.getnchannels(),w.getsampwidth())!=(16000,1,2):raise ValueError('Expected alignment PCM16 mono')
        while True:
            data=w.readframes(320)
            if not data:break
            a=np.frombuffer(data,dtype='<i2').astype(np.float64)/32768.
            power.append(float(np.mean(a*a)))
    values=np.asarray(power);threshold=min(1e-5,max(1e-10,float(np.percentile(values,75))*.01)) if len(values) else 1e-10
    quiet=[];start=None
    for i,energy in enumerate(power+[math.inf]):
        if energy<=threshold and start is None:start=i
        if energy>threshold and start is not None:
            if (i-start)*.02>=.6:quiet.append((start*.02,i*.02))
            start=None
    for row in rows:
        spans=row['token_spans'];step=row['frame_seconds']
        if not spans:continue
        first_start=spans[0]['start_frame']*step;last_end=spans[-1]['end_frame']*step
        row['token_boundary_diagnostics']=dict(first_to_second_start=(spans[1]['start_frame']-spans[0]['start_frame'])*step if len(spans)>1 else None,
            penultimate_to_last_end=(spans[-1]['end_frame']-spans[-2]['end_frame'])*step if len(spans)>1 else None,
            energy_threshold_power=threshold,quiet_min_seconds=.6,suspected_tail_gaps=[])
        diag=row['token_boundary_diagnostics']
        # Only gaps within the last two target letters, not ordinary inter-word
        # pauses elsewhere. Require an actual nonquiet prefix and nonempty cue.
        window_start=spans[max(0,len(spans)-2)]['start_frame']*step
        candidates=[(a,min(b,last_end)) for a,b in quiet if window_start<=a<last_end and min(b,last_end)-a>=.6 and a-first_start>=.08]
        diag['suspected_tail_gaps']=[list(x) for x in candidates]
        if candidates:
            endpoint=candidates[0][0]
            diag.update(proposed_end=endpoint,applied=bool(enabled),reason='last_two_labels_cross_sustained_quiet')
            if enabled:
                row['raw_end_time']=row['end_time'];row['end_time']=endpoint
                row['tail_guard']=dict(applied=True,original_end=row['raw_end_time'],end=endpoint,kind='optional_end_only_silence_guard')
    return rows
