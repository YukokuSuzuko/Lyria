from copy import deepcopy
from lyricsync.model import Mapping,Project
from lyricsync.editing import split_mapping,merge_mappings,Editor,History

def tok(a,b,i=0):
 return dict(label='a',token_id=1,start=a,end=b,start_frame=i*10,end_frame=i*10+5,score=.9,source_row_id='original',reading_index=i,source_start=i,source_end=i+1,ownership='kana')

def test_split_keeps_complete_token_times_crossing_unknown_once():
 original=[tok(.2,.4),tok(.8,1.2,1),tok(1.4,1.6,2)]
 p=Project(mappings=[Mapping('m',[],text_override='歌声',text_role='source_import',start=0,end=2,alignment_tokens=deepcopy(original))])
 left,right=split_mapping(p,'m',1,1.)
 assert len(left.alignment_tokens)==2 and len(right.alignment_tokens)==1
 assert left.alignment_tokens[0]==original[0] and right.alignment_tokens[0]==original[2]
 cross=left.alignment_tokens[1];assert cross['start']==.8 and cross['end']==1.2 and cross['start_frame']==10
 assert cross['ownership']=='unknown' and cross['source_start'] is None
 assert len(merge_mappings(p,[left.id,right.id]).alignment_tokens)==3
 p.validate_timeline()

def test_untimed_text_split_does_not_guess_character_token_relation():
 p=Project(mappings=[Mapping('m',[],text_override='歌声',text_role='source_import',alignment_tokens=[tok(.2,.4)])])
 left,right=split_mapping(p,'m',1)
 assert left.alignment_tokens[0]['ownership']=='unknown' and not right.alignment_tokens

def test_source_text_edit_invalidates_ownership_without_deleting_acoustic_raw():
 p=Project(mappings=[Mapping('m',[],text_override='歌',text_role='source_import',start=0,end=2,alignment_tokens=[tok(.2,.4)])]);history=History();before=p.to_dict()
 Editor(p,history).set_text('m','別')
 assert p.mappings[0].alignment_tokens[0]['ownership']=='unknown'
 assert p.mappings[0].alignment_tokens[0]['start']==.2
 restored,_=history.restore(p);assert restored.to_dict()==before

def test_translation_edit_does_not_invalidate_original_token_ownership():
 p=Project(mappings=[Mapping('m',[],text_override='译文',start=0,end=2,alignment_tokens=[tok(.2,.4)])]);history=History()
 Editor(p,history).set_text('m','新译文');assert p.mappings[0].alignment_tokens[0]['ownership']=='kana'
