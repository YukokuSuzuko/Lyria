"""Long audio regression contracts; synthetic coverage is not model accuracy."""
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from lyricsync import local_transcription as transcription
from lyricsync.experimental_nextfire_alignment import ctc_token_spans
from lyricsync.dev9_long_audio import audio_windows, merge_transcript_chunks, locate_lyric_rows, alignment_groups, disjoint_speech_windows, context_row_indices


def test_readings_worker_needs_neither_device_nor_full_model_environment(tmp_path):
    request = tmp_path / 'request.json'
    result = tmp_path / 'result.json'
    request.write_text(json.dumps({'operation': 'readings', 'rows': [
        {'id': 'repeat-1', 'text': '同じ', 'reading': 'onaji'}]}), encoding='utf-8')
    worker = Path(__file__).resolve().parents[1] / 'lyricsync/local_alignment_worker.py'
    done = subprocess.run([sys.executable, '-I', '-B', str(worker), '--request', str(request),
                           '--result', str(result)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert json.loads(result.read_text(encoding='utf-8'))['rows'][0]['reading'] == 'onaji'


def test_transcription_accepts_whole_song_without_user_slicing(tmp_path, monkeypatch):
    source = tmp_path / 'source.wav'
    source.write_bytes(b'not decoded by validation')
    monkeypatch.setattr(transcription, 'validate_runtime_path', lambda value: value)
    options = dict(audio_path=str(source), runtime_python='isolated-python', device='cpu',
                   language='Japanese', offset_seconds=0., duration_seconds=361.)
    assert transcription.validate_transcription_options(options)['duration_seconds'] == 361.


def test_ctc_above_previous_50m_limit_keeps_every_repeat_occurrence():
    # 10,000 * 5,201 = 52,010,000 previous traceback cells, but only 80 kB emissions.
    probs = np.log(np.full((10000, 2), .5, dtype=np.float32))
    result = ctc_token_spans(probs, [1] * 2600, blank_id=0)
    assert len(result) == 2600
    assert all(a['end_frame'] < b['start_frame'] for a, b in zip(result, result[1:]))


def test_internal_windows_cover_tail_and_preserve_bounded_overlap():
    windows = list(audio_windows(361.125, seconds=90., overlap=8.))
    assert windows[0][0] == 0 and windows[-1][1] == 361.125
    assert all(end - start <= 90 for start, end in windows)
    assert all(a[1] - b[0] == 8 for a, b in zip(windows, windows[1:]))


def test_production_speech_windows_cover_every_sample_including_odd_tail(tmp_path):
    import wave
    samples = np.zeros(480001, dtype='<i2')
    samples[-1] = 13000
    path = tmp_path/'odd-tail.wav'
    with wave.open(str(path),'wb') as stream:
        stream.setparams((1,2,16000,0,'NONE','not compressed'))
        stream.writeframes(samples.tobytes())
    windows = list(disjoint_speech_windows(path,seconds=10.,search_seconds=1.))
    read = []
    with wave.open(str(path),'rb') as stream:
        for first,last in windows:
            a,b=round(first*16000),round(last*16000)
            stream.setpos(a)
            read.append(np.frombuffer(stream.readframes(b-a),dtype='<i2'))
    np.testing.assert_array_equal(np.concatenate(read),samples)
    assert all(a[1] == b[0] for a,b in zip(windows,windows[1:]))


def test_transcript_merge_never_deletes_chorus_without_time_evidence():
    chunks = [dict(start=0, end=60, text='first verse the repeated chorus'),
              dict(start=52, end=112, text='the repeated chorus second verse continues with enough distinct lyrics throughout the remaining section'),
              dict(start=104, end=164, text='another part the repeated chorus')]
    text, locations, unresolved = merge_transcript_chunks(chunks)
    assert text.count('the repeated chorus') == 3
    assert unresolved == [1, 2]
    assert len(locations) == len(''.join(c for c in text if c.isalnum()))


def test_overlapping_identical_choruses_are_not_deleted_or_misassigned():
    chunks = [dict(start=0, end=60, text='abcdefghij'),
              dict(start=52, end=112, text='abcdefghij finalword')]
    rows = [dict(id='chorus-1', text='abcdefghij'), dict(id='chorus-2', text='abcdefghij'),
            dict(id='last', text='finalword')]
    text, _, unresolved = merge_transcript_chunks(chunks)
    assert text.count('abcdefghij') == 2 and unresolved == [1]
    anchors, _ = locate_lyric_rows(rows, chunks)
    assert not anchors[0]['located'] and not anchors[1]['located']


def test_missing_chorus_is_ambiguous_not_assigned_to_wrong_occurrence():
    rows = [dict(id='chorus-1', text='same chorus'), dict(id='chorus-2', text='same chorus')]
    anchors, _ = locate_lyric_rows(rows, [dict(start=0, end=60, text='same chorus')])
    assert all(not row['located'] and row['reason'] == 'ambiguous_repeated_lyric' for row in anchors)


def test_global_lyric_anchors_keep_two_chorus_instances_and_cross_boundary_line():
    rows = [dict(id='verse-a', text='unique opening'), dict(id='chorus-a', text='sing again'),
            dict(id='bridge', text='bridge starts bridge ends'), dict(id='chorus-b', text='sing again'),
            dict(id='end', text='unique ending')]
    chunks = [dict(start=0, end=60, text='unique opening sing again bridge starts'),
              dict(start=60, end=120, text='bridge ends sing again unique ending')]
    anchors, _ = locate_lyric_rows(rows, chunks)
    assert anchors[1]['first_chunk'] == 0 and anchors[3]['first_chunk'] == 1
    assert anchors[2]['start'] == 0 and anchors[2]['end'] == 120
    assert all(row['located'] for row in anchors)


def test_unknown_lyrics_not_given_proportional_timestamps():
    rows = [dict(id='missing', text='zzzzzzzzzz'), dict(id='found', text='known words')]
    anchors, _ = locate_lyric_rows(rows, [dict(start=280, end=350, text='known words')])
    assert not anchors[0]['located']
    groups = list(alignment_groups(anchors, 361.))
    assert [row['index'] for group in groups for row in group] == [1]


def test_missing_anchors_keep_full_intervening_acoustic_text_without_owning_times():
    rows = [dict(id=str(i),text='row '+str(i)) for i in range(7)]
    anchors = [dict(id=str(i),located=i in (0,3,6),coverage=1.,start=0.,end=60.) for i in range(7)]
    groups=list(alignment_groups(anchors,361.))
    assert len(groups)==1
    assert context_row_indices(rows,groups[0])==list(range(7))
    assert [item['index'] for item in groups[0]]==[0,3,6]


def test_checkpoint_ctc_equals_full_matrix_oracle_with_repetitions():
    # Independent small recurrence, exercising checkpoint boundaries/tie policy.
    rng = np.random.default_rng(1203)
    for frames in (31, 32, 33):
        probs = np.log(rng.dirichlet([1, 1, 1], size=frames))
        tokens = [1, 1, 2, 1, 2]
        states = [0]
        for token in tokens:
            states.extend([token, 0])
        scores = np.full((frames, len(states)), -np.inf)
        back = np.zeros_like(scores, dtype=int)
        scores[0, :2] = probs[0, states[:2]]
        for t in range(1, frames):
            for s, label in enumerate(states):
                options = [(scores[t-1, s], s)]
                if s:
                    options.append((scores[t-1, s-1], s-1))
                if s > 1 and label and label != states[s-2]:
                    options.append((scores[t-1, s-2], s-2))
                best = max(options, key=lambda item: item[0])
                scores[t, s] = best[0] + probs[t, label]
                back[t, s] = best[1]
        s = len(states) - 2 + int(scores[-1, -1] > scores[-1, -2])
        path = []
        for t in range(frames - 1, -1, -1):
            path.append(s)
            s = back[t, s]
        path.reverse()
        expected = []
        for i in range(len(tokens)):
            positions = [j for j, value in enumerate(path) if value == 2*i+1]
            expected.append((positions[0], positions[-1]+1))
        actual = ctc_token_spans(probs, tokens, blank_id=0, max_backtrack_cells=44)
        assert [(x['start_frame'], x['end_frame']) for x in actual] == expected


def test_nextfire_inference_failure_closes_emission_map_and_keeps_original_error(tmp_path, monkeypatch):
    import importlib
    import tempfile
    import wave
    from contextlib import nullcontext
    from types import SimpleNamespace
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'lyricsync'))
    monkeypatch.setattr(tempfile,'tempdir',str(tmp_path))
    audio=tmp_path/'long.wav'
    with wave.open(str(audio),'wb') as stream:
        stream.setparams((1,2,16000,0,'NONE','not compressed'))
        stream.writeframes(b'\0\0'*(61*16000))
    class Tensor:
        def __init__(self,values):self.values=values
        def __len__(self):return len(self.values)
        def __getitem__(self,key):return Tensor(self.values[key])
        def float(self):return self
        def log_softmax(self,*args):return self
        def cpu(self):return self
        def numpy(self):return self.values
    class Model:
        config=SimpleNamespace(conv_stride=[320],conv_kernel=[400],pad_token_id=0)
        calls=0
        def to(self,*args):return self
        def eval(self):return self
        def parameters(self):return iter([SimpleNamespace(device='cpu')])
        def __call__(self,**kwargs):
            self.calls+=1
            if self.calls==2:raise RuntimeError('injected inference failure')
            return SimpleNamespace(logits=[Tensor(np.zeros((2000,3),np.float32))])
    class Processor:
        def __call__(self,**kwargs):return SimpleNamespace(to=lambda *args:{})
    class Manager:
        def __init__(self,*args):pass
        def ensure_verified(self,*args):return tmp_path
        def path(self,*args):return tmp_path
    monkeypatch.setitem(sys.modules,'torch',SimpleNamespace(float32='float32',inference_mode=nullcontext))
    monkeypatch.setitem(sys.modules,'transformers',SimpleNamespace(
        AutoProcessor=SimpleNamespace(from_pretrained=lambda *a,**k:Processor()),
        AutoModelForCTC=SimpleNamespace(from_pretrained=lambda *a,**k:Model())))
    monkeypatch.setitem(sys.modules,'local_models',SimpleNamespace(ModelManager=Manager,
        NEXTFIRE=SimpleNamespace(id='nextfire',revision='test')))
    monkeypatch.setitem(sys.modules,'local_alignment_worker',SimpleNamespace(runtime_info=lambda *a:{}))
    worker=importlib.import_module('nextfire_worker')
    with pytest.raises(RuntimeError,match='^injected inference failure$'):
        worker.align_nextfire(dict(device='cpu',language='Japanese',cache_root=str(tmp_path),
            audio_wav=str(audio),rows=[dict(id='one',text='same',reading='same')]))
    assert not list(tmp_path.glob('lyria-nextfire-emissions-*'))

def test_continuous_silence_preferred_without_deleting_samples(tmp_path):
    import wave
    from lyricsync.dev9_long_audio import silence_intervals
    samples=np.full(40*16000,8000,dtype='<i2');samples[25*16000:27*16000]=0
    path=tmp_path/'gap.wav'
    with wave.open(str(path),'wb') as w:
        w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(samples.tobytes())
    assert silence_intervals(path)==[(25.,27.)]
    windows=list(disjoint_speech_windows(path,seconds=30))
    assert windows[0]==(0.,26.) and windows[-1][1]==40.
    assert sum(round((b-a)*16000) for a,b in windows)==len(samples)


def test_wide_context_never_includes_another_chorus_outside_audio():
    from lyricsync.dev9_long_audio import bounded_context_indices
    rows=[dict(id=str(i),text='same chorus') for i in range(5)]
    anchors=[dict(located=True,start=a,end=a+4) for a in [0,11,15,22,200]]
    group=[dict(index=2,start=10.,end=30.)]
    assert bounded_context_indices(rows,group,anchors)==[1,2,3]
