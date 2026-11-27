"""Synthetic worker results exercise source identities, not model accuracy."""
from copy import deepcopy
from pathlib import Path
import sys
import pytest

from lyricsync import local_alignment as la
from lyricsync.model import Lyric, Mapping, Project, Translation
from lyricsync.project_document import ProjectDocument
from lyricsync.local_models import QWEN
from lyricsync.candidate_tracks import install_candidate, prepare_apply


def seeded():
    return ProjectDocument(Project(
        lyrics=[Lyric('l1', 'Hello', None, None), Lyric('l2', 'Hello', None, None)],
        translations=[Translation('t1', '保留中文一', None, None), Translation('t2', '保留中文二', None, None)],
        mappings=[Mapping('seed1', ['t1'], lyric_ids=['l1']), Mapping('seed2', ['t2'], lyric_ids=['l2'])]))


def generate(doc, tmp_path, monkeypatch, *, source='reference', rows=None, words=None):
    audio = tmp_path/'synthetic.wav'
    audio.write_bytes(b'synthetic audio, decoder explicitly replaced')
    monkeypatch.setattr(la, 'validate_runtime_path', lambda p: Path(sys.executable))
    monkeypatch.setattr(la.ModelManager, 'ensure_verified', lambda *a, **k: None)
    monkeypatch.setattr(la, '_extract_audio', lambda *a, **k: 10.)
    # Decoder and worker are synthetic; no PCM file is emitted by this fixture.
    monkeypatch.setattr(la, 'audio_activity', lambda *a: dict(digital_silence=False))
    monkeypatch.setattr(la, 'run_worker', lambda *a, **k: dict(
        model_id=QWEN.id, revision=QWEN.revision, duration=10.,
        words=words if words is not None else [dict(text='Hello', start_time=1., end_time=2.), dict(text='Hello', start_time=4., end_time=5.)]))
    return la.generate_vocal_candidate(doc, options=dict(
        audio_path=str(audio), runtime_python='explicit mock', use_separation=False,
        duration_seconds=10, text_source=source, source_rows=rows))


def test_reference_repeated_occurrences_keep_main_identity_and_chinese(tmp_path, monkeypatch):
    doc = seeded()
    before = doc.snapshot()
    result = generate(doc, tmp_path, monkeypatch)
    assert [r['sentence_id'] for r in result['rows']] == ['seed1', 'seed2']
    assert [r['text'] for r in result['rows']] == ['保留中文一', '保留中文二']
    assert doc.snapshot() == before
    install_candidate(doc, result)
    plan = prepare_apply(doc, result['id'], [r['id'] for r in result['rows']])
    assert not any(entry['blocked'] for entry in plan['entries'])


def test_external_identical_words_do_not_guess_occurrences(tmp_path, monkeypatch):
    result = generate(seeded(), tmp_path, monkeypatch, source='external',
        rows=[{'id':'ext1','text':'Hello'}, {'id':'ext2','text':'Hello'}])
    assert all(r['sentence_id'] is None for r in result['rows'])


def test_reference_split_mapping_is_not_guessed(tmp_path, monkeypatch):
    doc = seeded()
    doc.project.mappings[1].lyric_ids = ['l1']
    result = generate(doc, tmp_path, monkeypatch)
    assert all(r['sentence_id'] is None for r in result['rows'])


def test_external_source_id_and_exact_text_allow_explicit_binding(tmp_path, monkeypatch):
    result = generate(seeded(), tmp_path, monkeypatch, source='external',
        rows=[{'id':'ext1','text':'Hello','lyric_ids':['l1']}, {'id':'ext2','text':'Hello','lyric_ids':['l2']}])
    assert [r['sentence_id'] for r in result['rows']] == ['seed1', 'seed2']


def test_changed_external_original_never_rebinds_by_id_alone(tmp_path, monkeypatch):
    result = generate(seeded(), tmp_path, monkeypatch, source='external',
        rows=[{'id':'ext1','text':'Changed','lyric_ids':['l1']}, {'id':'ext2','text':'Hello','lyric_ids':['l2']}],
        words=[dict(text='Changed', start_time=1., end_time=2.), dict(text='Hello', start_time=4., end_time=5.)])
    assert [r['sentence_id'] for r in result['rows']] == [None, 'seed2']


def test_valid_candidate_cannot_apply_after_main_edit(tmp_path, monkeypatch):
    doc = seeded()
    result = generate(doc, tmp_path, monkeypatch)
    install_candidate(doc, result)
    doc.project.mappings[0].text_override = '用户后改的中文'
    doc.touch()
    plan = prepare_apply(doc, result['id'], [result['rows'][0]['id']])
    assert plan['entries'][0]['blocked']


def test_partial_and_empty_are_distinct_and_keep_reasons(tmp_path, monkeypatch):
    doc = seeded()
    result = generate(doc, tmp_path, monkeypatch, words=[dict(text='Hello', start_time=1., end_time=2.), dict(text='Hello', start_time=None, end_time=None)])
    summary = la.alignment_result_summary(result)
    assert (summary['valid'], summary['missing'], summary['status']) == (1, 1, 'partial')
    assert summary['missing_ranges'] == ['2']
    assert result['rows'][1]['reasons']
    empty = deepcopy(result)
    for row in empty['rows']: row['start'] = row['end'] = None
    assert la.alignment_result_summary(empty)['status'] == 'empty'


def test_unbound_external_id_collision_does_not_grant_main_identity(tmp_path, monkeypatch):
    result = generate(seeded(), tmp_path, monkeypatch, source='external',
        rows=[{'id':'seed1','text':'Hello'}, {'id':'seed2','text':'Hello'}])
    assert all(row['sentence_id'] is None for row in result['rows'])
    assert not {'seed1','seed2'} & {row['id'] for row in result['rows']}


def test_external_file_ids_absent_from_project_remain_valid_independent_candidates(tmp_path, monkeypatch):
    result=generate(seeded(),tmp_path,monkeypatch,source='external',
        rows=[{'id':'ext1','text':'Hello','lyric_ids':['external-file-original-1']},
              {'id':'ext2','text':'Hello','lyric_ids':['external-file-original-2']}])
    assert len(result['rows'])==2 and all(row['sentence_id'] is None for row in result['rows'])
    assert all(not row['provenance']['lyric_ids'] for row in result['rows'])


def test_source_reordered_by_explicit_ids_never_uses_position(tmp_path, monkeypatch):
    result = generate(seeded(), tmp_path, monkeypatch, source='external',
        rows=[{'id':'ext2','text':'Hello','lyric_ids':['l2']}, {'id':'ext1','text':'Hello','lyric_ids':['l1']}])
    assert [r['sentence_id'] for r in result['rows']] == ['seed2','seed1']
    assert [r['text'] for r in result['rows']] == ['保留中文二','保留中文一']


def test_full_song_duration_read_from_actual_selected_media(tmp_path, monkeypatch):
    import wave
    audio=tmp_path/'long.wav'
    with wave.open(str(audio),'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(8000)
        stream.writeframes(b'\0' * 2 * 8000 * 430)
    monkeypatch.setattr(la, 'validate_runtime_path', lambda p: Path(sys.executable))
    settings=la.validate_options(seeded(), dict(audio_path=str(audio),runtime_python='mock',
        text_source='reference',use_separation=False,duration_seconds=None))
    assert settings['duration_seconds'] == pytest.approx(430.)
    settings=la.validate_options(seeded(), dict(audio_path=str(audio),runtime_python='mock',
        text_source='reference',use_separation=False,duration_seconds=None,offset_seconds=20))
    assert settings['duration_seconds'] == pytest.approx(410.)
