"""Internal long-audio scheduling; no network, project mutation or guessed times."""
from difflib import SequenceMatcher
from collections import Counter
import math
import unicodedata


def audio_windows(duration, *, seconds=90., overlap=8.):
    if not all(math.isfinite(x) for x in (duration, seconds, overlap)) or not duration > 0 or not seconds > overlap >= 0:
        raise ValueError('Invalid internal audio window')
    start = 0.
    while start < duration:
        end = min(duration, start + seconds)
        yield start, end
        if end == duration:
            break
        start = end - overlap


def _normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())


def merge_transcript_chunks(chunks):
    """Retain every occurrence; plain ASR text cannot prove temporal duplication.

    Production ASR uses disjoint audio windows. Overlapping legacy/external input
    is explicitly unresolved instead of deleting an indistinguishable chorus.
    """
    text, locations, unresolved = '', [], []
    for index, chunk in enumerate(chunks):
        addition = str(chunk.get('text', '')).strip()
        if not addition:
            continue
        if text and index and chunk['start'] < chunks[index - 1]['end']:
            unresolved.append(index)
        if text:
            text += '\n'
        text += addition
        locations.extend([index] * len(_normalized(addition)))
    return text, locations, unresolved


def locate_lyric_rows(rows, chunks):
    """Global monotonic ASR anchors, never timestamp interpolation or row index split.

    Repeated words remain separate ordered occurrences. Weak/missing matches are
    explicitly unlocated; a coarse chunk range is only an input to the acoustic
    aligner, never an accepted lyric timestamp.
    """
    transcript, locations, unresolved = merge_transcript_chunks(chunks)
    originals = [_normalized(row['text']) for row in rows]
    target, observed = ''.join(originals), _normalized(transcript)
    repeated = Counter(originals)
    matched = {}
    for block in SequenceMatcher(None, target, observed, autojunk=False).get_matching_blocks():
        for offset in range(block.size):
            matched[block.a + offset] = locations[block.b + offset]
    result, cursor = [], 0
    for row, original in zip(rows, originals):
        hits = [matched[i] for i in range(cursor, cursor + len(original)) if i in matched]
        cursor += len(original)
        coverage = len(hits) / max(1, len(original))
        repeat_ambiguous = bool(original and repeated[original] > 1 and
                                observed.count(original) != repeated[original])
        overlapping_ambiguous = any(hit in unresolved or hit + 1 in unresolved for hit in hits)
        if coverage < .6 or len(hits) < min(4, len(original)) or repeat_ambiguous or overlapping_ambiguous:
            result.append(dict(id=row['id'], located=False, coverage=coverage,
                               reason='ambiguous_repeated_lyric' if repeat_ambiguous else
                               'overlapping_asr_evidence' if overlapping_ambiguous else 'insufficient_asr_anchor'))
            continue
        first, last = min(hits), max(hits)
        result.append(dict(id=row['id'], located=True, coverage=coverage,
                           start=chunks[first]['start'], end=chunks[last]['end'],
                           first_chunk=first, last_chunk=last))
    return result, unresolved


def silence_intervals(path, *, minimum_seconds=.6, threshold_db=-50.):
    """PCM energy silence, not a claim of semantic voice detection.

    Scan bounded 20 ms blocks; report original sample coordinates, trim nothing.
    A separated vocal track gives more relevant evidence than mixed music.
    """
    import wave
    import numpy as np
    threshold=10.**(threshold_db/20.)*32768.
    intervals=[];run=None;cursor=0
    with wave.open(str(path),'rb') as stream:
        rate=stream.getframerate()
        if (rate,stream.getnchannels(),stream.getsampwidth())!=(16000,1,2):raise ValueError('Expected mono PCM16 at 16 kHz')
        while data:=stream.readframes(320):
            samples=np.frombuffer(data,dtype='<i2').astype(np.float32)
            quiet=float(np.sqrt(np.mean(samples*samples)))<=threshold
            if quiet and run is None:run=cursor
            if not quiet and run is not None:
                if (cursor-run)/rate>=minimum_seconds:intervals.append((run/rate,cursor/rate))
                run=None
            cursor+=len(samples)
        if run is not None and (cursor-run)/rate>=minimum_seconds:intervals.append((run/rate,cursor/rate))
    return intervals


def bounded_context_indices(rows,group,anchors,*,margin_rows=2):
    """Widen by instance IDs only where time evidence overlaps this audio batch."""
    first,last=group[0]['index'],group[-1]['index']
    start,end=group[0]['start'],max(item['end'] for item in group)
    for index in range(max(0,first-margin_rows),min(len(rows),last+margin_rows+1)):
        anchor=anchors[index]
        if anchor.get('located') and anchor['start']>=start and anchor['end']<=end:
            first=min(first,index);last=max(last,index)
    return list(range(first,last+1))


def disjoint_speech_windows(path, *, seconds=90., search_seconds=8., quiet_regions=None):
    """Read bounded PCM blocks; choose a quiet boundary without dropping samples."""
    import wave
    import numpy as np
    quiet_regions=silence_intervals(path) if quiet_regions is None else quiet_regions
    with wave.open(str(path), 'rb') as source:
        rate = source.getframerate()
        if (rate, source.getnchannels(), source.getsampwidth()) != (16000, 1, 2):
            raise ValueError('Expected mono PCM16 at 16 kHz')
        count, start = source.getnframes(), 0
        while start < count:
            end = min(count, start + round(seconds * rate))
            if end < count:
                first = max(start + round(seconds * .5 * rate), end - round(search_seconds * rate))
                gaps=[(max(first,round(a*rate)),min(end,round(b*rate))) for a,b in quiet_regions if round(b*rate)>first and round(a*rate)<end]
                gaps=[(a,b) for a,b in gaps if b-a>=round(.6*rate)]
                if gaps:
                    a,b=max(gaps,key=lambda pair:(pair[1]-pair[0],pair[1]))
                    end=(a+b)//2
                    yield start/rate,end/rate
                    start=end
                    continue
                source.setpos(first)
                samples = np.frombuffer(source.readframes(end - first), dtype='<i2').astype(np.float32)
                frame = 320
                blocks = len(samples) // frame
                if blocks:
                    energy = (samples[:blocks * frame].reshape(blocks, frame) ** 2).mean(axis=1)
                    end = first + int(energy.argmin()) * frame + frame // 2
            yield start / rate, end / rate
            start = end


def alignment_groups(anchors, duration, *, limit=280., context=10.):
    """Group located row identities; intervening text is retained by the caller.

    A missing anchor must not fragment one audio window into many requests that
    each omit most of its sung transcript. It still does not gain a timestamp.
    """
    current = []
    for index, anchor in enumerate(anchors):
        if not anchor['located']:
            continue
        start = max(0., anchor['start'] - context)
        end = min(duration, anchor['end'] + context)
        if end - start > limit:
            continue
        item = dict(index=index, start=start, end=end)
        if current and end - current[0]['start'] > limit:
            yield current
            current = []
        current.append(item)
    if current:
        yield current


def context_row_indices(rows, group, *, neighbor_rows=2):
    """Acoustic transcript context, distinct from the anchored output owners."""
    first = max(0, group[0]['index'] - neighbor_rows)
    last = min(len(rows), group[-1]['index'] + neighbor_rows + 1)
    return list(range(first, last))
