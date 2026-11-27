"""Separation provenance/mapping contracts with synthetic bytes, not quality."""
import json
import struct
import pytest
from lyricsync import local_models as lm
from lyricsync import local_separation as sep
from lyricsync import local_alignment as align
from lyricsync.project_document import ProjectDocument
from lyricsync.model import Project,Mapping


def test_alignment_never_bypasses_required_separation(tmp_path,monkeypatch):
    audio=tmp_path/'source.wav';audio.write_bytes(b'synthetic')
    doc=ProjectDocument(Project(mappings=[Mapping('one',[],text_override='original')]))
    before=doc.snapshot()
    settings=dict(model_id=lm.QWEN.id,cache_root=str(tmp_path/'cache'),audio_path=str(audio),runtime_python='unused',
        device='cpu',language='English',rows=[{'id':'one','text':'original'}],offset_seconds=0.,duration_seconds=1.,
        min_timestamp_score=.01,use_separation=True,skip_separation=True,trusted_vocal_path=str(audio))
    monkeypatch.setattr(align,'validate_options',lambda *a:settings)
    monkeypatch.setattr(lm.ModelManager,'verify',lambda *a,**k:tmp_path)
    calls=[]
    def required(*a,**k):calls.append('separation');raise ValueError('required separation failed')
    # This test isolates mandatory separation; decoder/signal are explicit synthetic doubles.
    monkeypatch.setattr(align,'_extract_audio',lambda *a,**k:1.)
    monkeypatch.setattr(align,'audio_activity',lambda *a:dict(digital_silence=False))
    monkeypatch.setattr(sep,'prepare_vocals',required)
    monkeypatch.setattr(align,'run_worker',lambda *a,**k:pytest.fail('mix reached aligner'))
    with pytest.raises(ValueError,match='required separation failed'):align.generate_vocal_candidate(doc,options=settings)
    assert calls==['separation'] and doc.snapshot()==before


def test_stem_lookup_rejects_tampering(tmp_path):
    with pytest.raises(ValueError,match='缺少对应的人声分轨记录'):sep.stem_for_candidate({})
    source,vocal,manifest=[tmp_path/name for name in ('source','vocal','mapping.json')]
    source.write_bytes(b'source');vocal.write_bytes(b'vocal')
    mapping=dict(model_id=lm.UVR.id,model_revision=lm.UVR.revision,path=str(vocal),sha256=sep.file_digest(vocal),
        source_sha256=sep.file_digest(source),offset_seconds=12.5,duration_seconds=8.,sample_rate=44100)
    manifest.write_text(json.dumps(mapping),encoding='utf-8')
    record={'provenance':{'auxiliary_files':{role:{'path':str(p),'sha256':sep.file_digest(p)} for role,p in
        [('alignment_audio',source),('separated_vocals',vocal),('separation_manifest',manifest)]}}}
    assert sep.stem_for_candidate(record)['offset_seconds']==12.5
    vocal.write_bytes(b'changed')
    with pytest.raises(ValueError,match='原音频或人声分轨文件已变动'):sep.stem_for_candidate(record)


def float_wav(path,samples):
    body=struct.pack('<'+'f'*len(samples),*samples)
    fmt=struct.pack('<HHIIHH',3,2,44100,352800,8,32)
    chunks=b'fmt '+struct.pack('<I',len(fmt))+fmt+b'data'+struct.pack('<I',len(body))+body
    path.write_bytes(b'RIFF'+struct.pack('<I',len(chunks)+4)+b'WAVE'+chunks)


def test_float_output_independently_rejects_nonfinite_mismatch_and_truncation(tmp_path):
    output=tmp_path/'vocal.wav';float_wav(output,[.5,-.25])
    assert sep._validate_float_wav(output,1,lambda:False)==.5
    with pytest.raises(ValueError):sep._validate_float_wav(output,2,lambda:False)
    float_wav(output,[float('nan'),0.])
    with pytest.raises(ValueError):sep._validate_float_wav(output,1,lambda:False)
    output.write_bytes(output.read_bytes()[:-1])
    with pytest.raises(ValueError):sep._validate_float_wav(output,1,lambda:False)
