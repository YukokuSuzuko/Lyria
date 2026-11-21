"""Synthetic draft safety contracts, not singing accuracy."""
from copy import deepcopy
import pytest
from lyricsync import local_transcription as tr
from lyricsync import local_alignment as la
from lyricsync.local_models import ASR,QWEN
from lyricsync.local_separation import file_digest
from lyricsync.candidate_tracks import capture_source
from lyricsync.project_document import ProjectDocument
from lyricsync.model import Project,Mapping


def draft_for(document,path):
    return dict(kind='transcript_draft',version=1,id='draft-test',document_id=document.id,revision=document.revision,
        status='unreviewed',model_id=ASR.id,model_revision=ASR.revision,text='Hello\nHello',complete=True,
        source_path=str(path),source_sha256=file_digest(path),offset_seconds=2.,duration_seconds=5.,language='English')


def test_asr_is_distinct_pinned_model_not_aligner():
    assert ASR.id!=QWEN.id and ASR.revision!=QWEN.revision
    assert next(f for f in ASR.files if f.name=='model.safetensors').sha256=='d3f212dd20abecd315d830bc54ae3865e56ebfc3276484e57b771288ba27fd35'
    assert len(ASR.files)==8


def test_confirmation_required_and_repeated_lines_keep_distinct_ids(tmp_path):
    audio=tmp_path/'audio.wav';audio.write_bytes(b'synthetic source')
    doc=ProjectDocument(Project());before=deepcopy(doc.snapshot());draft=draft_for(doc,audio)
    with pytest.raises(ValueError,match='勾选确认'):tr.confirm_transcript_draft(draft,'Hello\nHello',doc)
    confirmed=tr.confirm_transcript_draft(draft,'Hello\nHello',doc,confirmed=True)
    assert [r['text'] for r in confirmed['rows']]==['Hello','Hello']
    assert len({r['id'] for r in confirmed['rows']})==2
    assert draft['status']=='unreviewed' and doc.snapshot()==before


@pytest.mark.parametrize('change',['foreign','stale','truncated','blank'])
def test_bad_draft_cannot_be_confirmed(tmp_path,change):
    audio=tmp_path/'audio.wav';audio.write_bytes(b'placeholder')
    doc=ProjectDocument(Project());draft=draft_for(doc,audio);text='Hello'
    if change=='foreign':draft['document_id']='other'
    if change=='stale':draft['revision']+=1
    if change=='truncated':draft['complete']=False
    if change=='blank':text='  \n '
    with pytest.raises(ValueError):tr.confirm_transcript_draft(draft,text,doc,confirmed=True)


def test_confirmed_new_rows_rebase_against_real_project_without_fake_translation(tmp_path,monkeypatch):
    audio=tmp_path/'audio.wav';audio.write_bytes(b'synthetic source')
    doc=ProjectDocument(Project(mappings=[Mapping('existing',[],start=10.,end=12.,text_override='Hello')]))
    before=deepcopy(doc.snapshot())
    draft=tr.confirm_transcript_draft(draft_for(doc,audio),'Hello\nHello',doc,confirmed=True)
    def generate(staged,index,*,options,cancel,progress):
        assert [row.id for row in staged.project.mappings]!=['existing']
        result={'model_id':QWEN.id,'revision':QWEN.revision,'duration':5.,'words':[
            {'text':'Hello','start_time':.3,'end_time':1.,'score':.8},
            {'text':'Hello','start_time':2.,'end_time':3.,'score':.8}]}
        assert options['main_source_confirmed'] is True
        assert all(row.text_role=='source_asr' for row in staged.project.mappings)
        return la.candidate_from_alignment(capture_source(staged,'vocal'),la.request_rows(staged),result,offset_seconds=2.)
    monkeypatch.setattr(tr,'generate_vocal_candidate',generate)
    result=tr.generate_confirmed_draft_candidate(doc,options={'transcript_draft':draft})
    assert all(row['sentence_id'] is None and row['status']=='new' for row in result['rows'])
    assert [(row['start'],row['end']) for row in result['rows']]==[(2.3,3.),(4.,5.)]
    assert all(row['text_role']=='source_asr' for row in result['rows'])
    assert not result['evidence']['translations'] and doc.snapshot()==before
    audio.write_bytes(b'changed source')
    with pytest.raises(ValueError,match='原音频文件已变动'):tr.generate_confirmed_draft_candidate(doc,options={'transcript_draft':draft})


def test_unconfirmed_draft_never_reaches_alignment(monkeypatch):
    monkeypatch.setattr(tr,'generate_vocal_candidate',lambda *a,**k:pytest.fail('unconfirmed inference'))
    with pytest.raises(ValueError,match='请先确认语音转写初稿'):
        tr.generate_confirmed_draft_candidate(ProjectDocument(Project()),options={'transcript_draft':{'status':'unreviewed'}})
