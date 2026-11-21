import pytest
from copy import deepcopy
from lyricsync.alignment_romaji import romanize_kana,expand_numbers
from lyricsync.japanese_readings import invalid_reading_rows,prepare_reading_rows,reading_is_confirmed

@pytest.mark.parametrize('kana,expected',[('っち','tchi'),('っちゃ','tcha'),('あっ','ah'),('あっあ','aha'),('んあ',"n'a"),('んや',"n'ya"),('ねー','nee'),('ステージ','suteeji'),('ナイト','naito'),('チェック','chekku')])
def test_independent_spelling(kana,expected):assert romanize_kana(kana)==expected

def test_confirmation_without_changing_text_or_reading():
 row=dict(id='a',text='ね～',reading='ne',generated_reading='ne',reading_origin='automatic_suggestion',reading_issues=[dict(message='review',requires_review=True)])
 assert invalid_reading_rows([row])
 row['reading_confirmed_for']=[row['text'],row['reading']]
 assert not invalid_reading_rows([row])
 before=deepcopy(row);out=prepare_reading_rows([row],lambda s:pytest.fail('confirmed must not regenerate'))
 assert row==before and out[0]['reading']=='ne' and reading_is_confirmed(out[0])
 row['text']='別～'
 assert invalid_reading_rows([row])

def test_confirmed_hard_invalid_still_blocked():
 row=dict(id='a',text='顔',reading='ᐢ',reading_confirmed_for=['顔','ᐢ'])
 assert invalid_reading_rows([row])[0]['kind']=='invalid_labels'

def test_manual_reading_is_preserved():
 row=dict(id='a',text='ね～',reading='nee',reading_origin='manual')
 assert prepare_reading_rows([row],lambda s:pytest.fail('manual'))[0]['reading']=='nee'

def test_number_expansion_keeps_source_span():
 text,owners=expand_numbers('20日と1人')
 assert text=='はつかとひとり' and owners[:3]==[(0,3)]*3

def test_graph_old_project_without_optional_token_field_roundtrips():
 from lyricsync.model import Project,Mapping
 p=Project(mappings=[Mapping('a',[])])
 data=p.to_dict()
 for row in data['mappings']:row.pop('alignment_tokens')
 for row in data['alignment_graph']['cue_specs']:row.pop('alignment_tokens')
 restored=Project.from_dict(data)
 assert restored.mappings[0].alignment_tokens==[]
 assert Project.from_dict(restored.to_dict()).to_dict()==restored.to_dict()

def test_token_evidence_roundtrip_and_unknown_fields_not_silently_lost():
 from lyricsync.model import Project,Mapping
 token=dict(label='a',token_id=1,start=.1,end=.2,start_frame=5,end_frame=10,score=.8,source_row_id='row',reading_index=0,source_start=None,source_end=None,ownership='unknown')
 p=Project(mappings=[Mapping('a',[],alignment_tokens=[token])]);raw=p.to_dict()
 assert Project.from_dict(raw).mappings[0].alignment_tokens==[token]
 bad=deepcopy(token);bad['mystery']=1
 with pytest.raises(ValueError):Mapping('b',[],alignment_tokens=[bad])


def test_tail_guard_long_voiced_vowel_not_trimmed(tmp_path):
 import wave,math,struct
 from lyricsync.nextfire_tail_guard import inspect_tail_spans
 path=tmp_path/'voiced.wav'
 with wave.open(str(path),'wb') as w:
  w.setparams((1,2,16000,0,'NONE','not compressed'))
  w.writeframes(b''.join(struct.pack('<h',int(6000*math.sin(2*math.pi*220*i/16000))) for i in range(32000)))
 row=dict(start_time=0,end_time=2,frame_seconds=.02,token_spans=[dict(start_frame=0,end_frame=20),dict(start_frame=20,end_frame=100)])
 assert inspect_tail_spans([row],path,enabled=True)[0]['end_time']==2
 assert not row['token_boundary_diagnostics']['suspected_tail_gaps']


def test_tail_guard_optional_keeps_raw_spans(tmp_path):
 import wave,math,struct
 from lyricsync.nextfire_tail_guard import inspect_tail_spans
 path=tmp_path/'quiet.wav'
 with wave.open(str(path),'wb') as w:
  w.setparams((1,2,16000,0,'NONE','not compressed'))
  w.writeframes(b''.join(struct.pack('<h',int(6000*math.sin(2*math.pi*220*i/16000)) if i<16000 or i>=28800 else 0) for i in range(32000)))
 row=dict(start_time=0,end_time=2,frame_seconds=.02,token_spans=[dict(start_frame=0,end_frame=20),dict(start_frame=20,end_frame=100)])
 original=deepcopy(row)
 assert inspect_tail_spans([row],path)[0]['end_time']==2
 assert row['token_boundary_diagnostics']['applied'] is False
 trimmed=inspect_tail_spans([deepcopy(original)],path,enabled=True)[0]
 assert trimmed['end_time']==1 and trimmed['raw_end_time']==2
 assert trimmed['token_spans']==original['token_spans']


def test_persisted_tokens_keep_overlapping_source_evidence():
 from lyricsync.alignment_tokens import validate_alignment_tokens
 token=dict(label='a',token_id=1,start=.1,end=.5,start_frame=5,end_frame=25,score=.8,source_row_id='one',reading_index=0,source_start=None,source_end=None,ownership='unknown')
 second=dict(token,start=.2,end=.6,source_row_id='two')
 assert validate_alignment_tokens([token,second])==[token,second]
 with pytest.raises(ValueError):validate_alignment_tokens([second,token])


@pytest.mark.parametrize('text,expected,source',[
 ('君(kimi)はLove','kimiはLove',(0,1)),
 ('君《きみ》はLove','きみはLove',(0,1)),
 ('{君||きみ}を','きみを',(1,2)),
 ('<ruby><rb>君</rb><rt>kimi</rt></ruby>は','kimiは',(10,11)),
 ('あ↑君','あ君',(0,1)),
])
def test_annotation_source_map_preserves_actual_original_offsets(text,expected,source):
 from lyricsync.japanese_readings import scan_reading_inputs,alignment_input_mapping
 row=scan_reading_inputs([dict(id='one',text=text)])['rows'][0]
 assert row['reading_input']==expected
 mapped=alignment_input_mapping(row)
 assert mapped[0][:2]==source
 assert row['text']==text


def test_sudachi_explicit_latin_is_not_reinterpreted_as_english(monkeypatch):
 from lyricsync import sudachi_readings as s
 class Morph:
  def surface(self):return 'は'
  def reading_form(self):return 'ハ'
  def begin(self):return 0
  def end(self):return 1
  def part_of_speech(self):return ['助詞']
 class Tokenizer:
  def tokenize(self,text,mode):assert text=='は';return [Morph()]
 monkeypatch.setattr(s,'create_tokenizer',lambda:(Tokenizer(),None))
 row=prepare_reading_rows([dict(id='one',text='君(kimi)はLove')],tool='sudachi')[0]
 assert row['reading']=='kimiwarabu'
 assert row['alignment_units'][0]['source_start']==0 and row['alignment_units'][0]['source_end']==1
 assert row['alignment_units'][0]['ownership']=='provided_segment'
 assert invalid_reading_rows([row])[0]['kind']=='review_input'
 row['reading_confirmed_for']=[row['text'],row['reading']]
 assert not invalid_reading_rows([row])


def test_refill_cannot_erase_partial_generation_hard_error():
 row=dict(id='one',text='君 UNKNOWN',reading='kimi',generated_reading='kimi',reading_origin='automatic_suggestion',reading_issues=[dict(kind='unknown_english',message='unknown',hard_error=True)],reading_confirmed_for=['君 UNKNOWN','kimi'])
 result=prepare_reading_rows([row],lambda s:pytest.fail('confirmed skips converter'))
 assert invalid_reading_rows(result)[0]['kind']=='invalid_labels'
 row['reading_origin']='manual';row['reading']='kimiannoun';row['reading_confirmed_for']=[row['text'],row['reading']]
 assert not invalid_reading_rows(prepare_reading_rows([row]))


def test_nextfire_compact_token_identity_and_raw_order():
 from lyricsync.nextfire_candidate import candidate_from_nextfire
 from lyricsync.model import Project,Mapping
 from lyricsync.project_document import ProjectDocument
 from lyricsync.candidate_tracks import capture_source,candidate_project
 from lyricsync.local_models import NEXTFIRE
 doc=ProjectDocument(Project(mappings=[Mapping('a',[],text_override='あい')]))
 snapshot=capture_source(doc,'vocal');rows=[dict(id='a',text='あい',reading='ai',alignment_units=[dict(reading='a',source_start=0,source_end=1,ownership='kana'),dict(reading='i',source_start=1,source_end=2,ownership='kana')])]
 target=dict(rows[0],start_time=.2,end_time=.8,path_score=.8,model_reading='ai',frame_seconds=.02,token_spans=[[1,10,20,.8],[9,20,40,.7]])
 response=dict(model_id=NEXTFIRE.id,revision=NEXTFIRE.revision,duration=2.,rows=[target])
 record=candidate_from_nextfire(snapshot,rows,response)
 assert len(record['rows'][0]['alignment_tokens'])==2
 assert record['rows'][0]['alignment_tokens'][1]['source_start']==1
 for change in [dict(model_reading='aa'),dict(token_spans=[[1,10,30,.8],[9,20,40,.7]])]:
  bad=deepcopy(response);bad['rows'][0].update(change)
  with pytest.raises(ValueError):candidate_from_nextfire(snapshot,rows,bad)


def test_reading_runtime_does_not_overwrite_existing(tmp_path):
 from lyricsync.local_runtime_setup import create_reading_runtime
 base=tmp_path/'python.exe';base.touch();target=tmp_path/'existing';target.mkdir();sentinel=target/'keep';sentinel.write_text('kept')
 with pytest.raises(ValueError):create_reading_runtime(base,target)
 assert sentinel.read_text()=='kept'


def test_full_song_collects_conversion_errors_without_first_row_abort(monkeypatch):
 from lyricsync import sudachi_readings
 def convert(text,**kwargs):
  if text=='巨大数':raise ValueError('unsupported number')
  return dict(reading='a',units=[],spelling='test',issues=[])
 monkeypatch.setattr(sudachi_readings,'convert_sudachi',convert)
 result=prepare_reading_rows([dict(id='a',text='巨大数'),dict(id='b',text='あ')],tool='sudachi')
 assert len(result)==2 and result[1]['reading']=='a'
 assert invalid_reading_rows(result)[0]['id']=='a'


def test_tail_guard_quiet_interval_may_extend_past_ctc_endpoint(tmp_path):
 import wave,math,struct
 from lyricsync.nextfire_tail_guard import inspect_tail_spans
 path=tmp_path/'longquiet.wav'
 with wave.open(str(path),'wb') as w:
  w.setparams((1,2,16000,0,'NONE','not compressed'))
  w.writeframes(b''.join(struct.pack('<h',int(6000*math.sin(2*math.pi*220*i/16000)) if i<16000 else 0) for i in range(48000)))
 row=dict(start_time=0,end_time=2,frame_seconds=.02,token_spans=[dict(start_frame=0,end_frame=20),dict(start_frame=20,end_frame=100)])
 assert inspect_tail_spans([row],path,enabled=True)[0]['end_time']==1
