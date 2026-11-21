from lyricsync.nextfire_worker import chunk_frame_ranges
from lyricsync.japanese_readings import prepare_reading_rows
from lyricsync.local_models import NEXTFIRE, CATALOG, download_url
import pytest


def test_pinned_model_and_hf_only():
    assert CATALOG[NEXTFIRE.id] is NEXTFIRE
    assert len(NEXTFIRE.files)==7
    assert NEXTFIRE.license=='CC-BY-NC-SA-4.0'
    assert all(NEXTFIRE.revision in download_url(NEXTFIRE,f) for f in NEXTFIRE.files)
    with pytest.raises(Exception): download_url(NEXTFIRE,NEXTFIRE.files[0],'modelscope')


def test_chunk_ranges_cover_every_global_frame_once():
    strides=[5,2,2,2,2,2,2];kernels=[10,3,3,3,3,2,2]
    for count in [400,16000,480000,481100,4800000]:
        ranges=list(chunk_frame_ranges(count,strides,kernels))
        indices=[]
        for start,end,left,right,first,last in ranges:
            assert start%320==0 and end<=count
            assert right-left==last-first
            assert (end-start-400)//320+1>=right
            assert start//320+left==first
            indices.extend(range(first,last))
        assert indices==list(range((count-400)//320+1))


def test_existing_reading_and_duplicate_text_preserve_occurrences():
    rows=[dict(id='a',text='同じ',reading='onaji'),dict(id='b',text='同じ',reading='onaji')]
    result=prepare_reading_rows(rows,lambda s: (_ for _ in ()).throw(Exception('not called')))
    assert [r['id'] for r in result]==['a','b']
    assert [r['text'] for r in result]==['同じ','同じ']
    assert rows==[dict(id='a',text='同じ',reading='onaji'),dict(id='b',text='同じ',reading='onaji')]


def test_auto_reading_suggestion_and_ruby_source_preserved():
    seen=[]
    def converter(text):
        seen.append(text);return [dict(orig=text,hepburn='hi')]
    result=prepare_reading_rows([dict(id='a',text='｜日《ひ》')],converter)
    assert seen==['ひ']
    assert result[0]['text']=='｜日《ひ》'
    assert result[0]['reading']=='hi'
    assert result[0]['reading_origin']=='automatic_suggestion'
    with pytest.raises(ValueError): prepare_reading_rows([dict(id='a',text='x'),dict(id='a',text='x')],converter)


def test_candidate_rejects_identity_timing_and_preserves_original():
    from lyricsync.nextfire_candidate import candidate_from_nextfire
    from lyricsync.model import Project,Mapping
    from lyricsync.project_document import ProjectDocument
    from lyricsync.candidate_tracks import capture_source
    from copy import deepcopy
    document=ProjectDocument(Project(mappings=[Mapping('a',[],text_override='漢字')]))
    snapshot=capture_source(document,'vocal')
    rows=[dict(id='a',text='漢字',reading='kanji')]
    response=dict(model_id=NEXTFIRE.id,revision=NEXTFIRE.revision,duration=2.,rows=[dict(rows[0],start_time=.2,end_time=1.,path_score=.8)])
    result=candidate_from_nextfire(snapshot,rows,response)
    assert result['rows'][0]['confidence']==0
    assert document.project.mappings[0].start is None
    for change in [dict(id='bad'),dict(reading='wrong'),dict(start_time=float('nan')),dict(path_score=2.)]:
        bad=deepcopy(response);bad['rows'][0].update(change)
        with pytest.raises(ValueError):candidate_from_nextfire(snapshot,rows,bad)
