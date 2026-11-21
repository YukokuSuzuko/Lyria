"""Offline only: selected separator boundary, no ASR/alignment/model download."""
from pathlib import Path
import pytest
from lyricsync import separation_preview as preview
from lyricsync.local_models import UVR,UVR_HQ1,ModelError


@pytest.fixture
def options(tmp_path,monkeypatch):
    source=tmp_path/'source.wav';source.write_bytes(b'test original')
    monkeypatch.setattr(preview,'validate_runtime_path',lambda _:tmp_path/'runtime/python.exe')
    return dict(audio_path=str(source),cache_root=str(tmp_path/'cache'),runtime_python='mock',
        device='cpu',offset_seconds=12.,duration_seconds=5.)


@pytest.mark.parametrize('model',[UVR.id,UVR_HQ1.id])
def test_only_selected_separator_without_any_lyrics(options,tmp_path,monkeypatch,model):
    options['separation_model_id']=model;calls=[]
    def prohibited(*args,**kwargs):pytest.fail('ASR/alignment/download must not run')
    monkeypatch.setattr('lyricsync.local_alignment.generate_vocal_candidate',prohibited)
    monkeypatch.setattr('lyricsync.local_transcription.generate_transcript_draft',prohibited)
    monkeypatch.setattr('lyricsync.local_models.ModelManager.download',prohibited)
    def separate(settings,**kwargs):
        calls.append(settings)
        stem=tmp_path/'vocals.wav';stem.write_bytes(b'test stem')
        return dict(id='result',path=str(stem),source_path=settings['audio_path'],model_id=settings['separation_model_id'],
            offset_seconds=settings['offset_seconds'],duration_seconds=settings['duration_seconds'])
    monkeypatch.setattr(preview,'prepare_vocals',separate)
    result=preview.generate_separation_preview(options=options)
    assert len(calls)==1 and calls[0]['separation_model_id']==model
    assert result['kind']=='separation_preview' and result['duration_seconds']==5.
    assert 'rows' not in result and 'text' not in result
    assert result['source_stamp']==preview.file_stamp(options['audio_path'])


@pytest.mark.parametrize('key,value',[('offset_seconds',-1),('duration_seconds',0),('duration_seconds',float('inf')),
    ('duration_seconds',True),('offset_seconds',float('nan')),('device','directml'),('separation_model_id','not-a-separator')])
def test_invalid_inputs_never_run_separator(options,monkeypatch,key,value):
    options[key]=value
    monkeypatch.setattr(preview,'prepare_vocals',lambda *a,**k:pytest.fail('invalid input reached separator'))
    with pytest.raises((ValueError,ModelError)):preview.generate_separation_preview(options=options)


def test_entire_long_song_reaches_separator_without_manual_slicing(options,tmp_path,monkeypatch):
    options['duration_seconds']=361.25
    def separate(settings,**kwargs):
        assert settings['duration_seconds']==361.25
        stem=tmp_path/'vocals-long.wav';stem.write_bytes(b'test stem')
        return dict(path=str(stem),source_path=settings['audio_path'],model_id=UVR.id,
                    duration_seconds=settings['duration_seconds'],offset_seconds=settings['offset_seconds'])
    monkeypatch.setattr(preview,'prepare_vocals',separate)
    assert preview.generate_separation_preview(options=options)['duration_seconds']==361.25


def test_cancellation_before_and_after_worker_never_completes(options,tmp_path,monkeypatch):
    with pytest.raises(InterruptedError):preview.generate_separation_preview(options=options,cancel=lambda:True)
    cancelled=[]
    def late(*args,**kwargs):cancelled.append(True);return {}
    monkeypatch.setattr(preview,'prepare_vocals',late)
    with pytest.raises(InterruptedError):preview.generate_separation_preview(options=options,cancel=lambda:bool(cancelled))


def test_worker_failure_is_not_alignment_or_success(options,monkeypatch):
    def fails(*a,**kw):raise RuntimeError('separator runtime failed')
    monkeypatch.setattr(preview,'prepare_vocals',fails)
    with pytest.raises(RuntimeError,match='separator runtime failed'):preview.generate_separation_preview(options=options)


def test_source_change_after_core_final_hash_is_rejected(options,tmp_path,monkeypatch):
    def tail_window_change(settings,**kwargs):
        # Core has finished hashing/extracting the original, then writes its result.
        stem=tmp_path/'tail-vocals.wav';stem.write_bytes(b'old extracted vocals')
        Path(settings['audio_path']).write_bytes(b'new source after core final hash')
        return dict(path=str(stem),source_path=settings['audio_path'],model_id=UVR.id)
    monkeypatch.setattr(preview,'prepare_vocals',tail_window_change)
    with pytest.raises(ValueError,match='原素材发生变化'):
        preview.generate_separation_preview(options=options)
