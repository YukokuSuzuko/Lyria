from dataclasses import asdict
from copy import deepcopy
from lyricsync.model import Project, Mapping, Lyric, Translation
from lyricsync.candidate_tracks import _digest, _fingerprint


def test_empty_dev9_fields_preserve_dev8_fingerprint():
    p=Project(lyrics=[Lyric('l','original',None,None)],translations=[Translation('t','translation')],
              mappings=[Mapping('m',['t'],lyric_ids=['l'])])
    row=asdict(p.mappings[0])
    for key in ('text_role','style_id','style_overrides','alignment_tokens','speaker_name'):row.pop(key)
    lyric=deepcopy(p.lyrics[0].__dict__);lyric.pop('subtitle_metadata')
    translation=deepcopy(p.translations[0].__dict__);translation.pop('subtitle_metadata')
    old=_digest({'row':row,'text':'translation','translations':[translation],'lyrics':[lyric],'visuals':[]})
    assert _fingerprint(p,p.mappings[0])==old
    p.mappings[0].style_id='named'
    assert _fingerprint(p,p.mappings[0])!=old
    p.mappings[0].style_id=''
    p.translations[0].subtitle_metadata={'actor':'Actor'}
    assert _fingerprint(p,p.mappings[0])!=old
