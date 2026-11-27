from copy import deepcopy
import pytest
from lyricsync.model import Project,Mapping,Lyric,Translation
from lyricsync.candidate_tracks import capture_source,candidate_from_project,install_candidate,prepare_apply,apply_plan,candidate_project
from lyricsync.editing import History
from lyricsync.local_alignment import source_row_bindings,rebind_source_candidate
from desktop_qt.vocal_application import prepare_rebuild,commit_rebuild
from desktop_qt.timeline_views import timeline_choices
from test_qt_entry_initialization_dev5 import bridge,ready,settled

def start(bridge):
 detached=deepcopy(bridge.document);index=deepcopy(bridge.media_controller.index)
 def work(cancel,progress):
  snapshot=capture_source(detached,'vocal',index)
  generated=Project.from_dict(detached.project.to_dict())
  if not generated.mappings:
   generated.lyrics=[Lyric('a','歌',None,None),Lyric('b','空',None,None)]
   generated.replace_timeline([Mapping('a',[],lyric_ids=['a'],text_override='歌',text_role='source_import'),Mapping('b',[],lyric_ids=['b'],text_override='空',text_role='source_import')])
  for i,row in enumerate(generated.mappings):row.start=.2+i;row.end=.8+i
  return candidate_from_project(snapshot,generated)
 bridge._start_candidate_job('test','vocal',work,lambda record,error:bridge._complete_vocal_candidate(record) if error is None else (_ for _ in ()).throw(error))

def test_first_result_editable_and_single_undo(bridge):
 ready(bridge,'audio');before=deepcopy(bridge.document.project.to_dict());start(bridge);settled(bridge)
 assert bridge._candidate_source=='main' and len(bridge.document.main_timeline)==2
 assert bridge._candidate_task['applied']==2
 assert all(not row.human_edited for row in bridge.document.main_timeline)
 assert not any(x['id']=='vocal' for x in timeline_choices(bridge.document))
 bridge.document.project,_=bridge.history.restore(bridge.document.project)
 assert bridge.document.project.to_dict()==before

def test_rerun_stays_main_and_prompts_without_overwriting(bridge,monkeypatch):
 ready(bridge,'audio')
 bridge.document.project.replace_timeline([Mapping('m',[],text_override='手改',start=.1,end=.3,human_edited=True,locked=True)])
 bridge._changed();before=deepcopy(bridge.document.project.to_dict());called=[]
 monkeypatch.setattr(bridge,'_offer_vocal_application',lambda record:called.append(record))
 start(bridge);settled(bridge)
 assert len(called)==1 and bridge._candidate_source=='main'
 assert bridge.document.project.to_dict()==before

def conflict(bridge):
 ready(bridge,'audio');p=bridge.document.project
 p.translations=[Translation('t','完整译文')];p.replace_timeline([Mapping('old',['t'],text_override='完整译文')])
 bridge._changed();snapshot=capture_source(bridge.document,'vocal',bridge.media_controller.index)
 generated=Project.from_dict(p.to_dict());generated.lyrics=[Lyric('a','歌',None,None),Lyric('b','空',None,None)]
 generated.replace_timeline([Mapping('a',[],lyric_ids=['a'],text_override='歌',text_role='source_import',start=.2,end=.5),Mapping('b',[],lyric_ids=['b'],text_override='空',text_role='source_import',start=None,end=None)])
 record=candidate_from_project(snapshot,generated);install_candidate(bridge.document,record);return record

def test_conflict_rebuild_preserves_all_text_and_undo(bridge):
 record=conflict(bridge);before=deepcopy(bridge.document.project.to_dict())
 result=prepare_rebuild(bridge.document,record['id'],bridge.media_controller.index)
 assert bridge.document.project.to_dict()==before
 commit_rebuild(bridge.document,bridge.history,result,bridge.media_controller.index)
 assert len(bridge.document.main_timeline)==2 and bridge.document.main_timeline[1].start is None
 assert bridge.document.project.translations[0].text=='完整译文'
 assert bridge.document.project.settings['pending_translation_ids']==['t']
 assert bridge.document.project.settings['timeline_archives'][0]['project']==before
 bridge.document.project,_=bridge.history.restore(bridge.document.project);assert bridge.document.project.to_dict()==before

def test_conflict_rebuild_rejects_change_after_preview(bridge):
 record=conflict(bridge);result=prepare_rebuild(bridge.document,record['id'],bridge.media_controller.index)
 bridge.document.project.mappings[0].text_override='不能覆盖'
 with pytest.raises(ValueError):commit_rebuild(bridge.document,bridge.history,result,bridge.media_controller.index)
 assert bridge.document.project.mappings[0].text_override=='不能覆盖'

def test_multi_source_binding_is_exact_partition_not_text_guess():
 p=Project(lyrics=[Lyric('a','一',None,None),Lyric('b','二',None,None)],mappings=[Mapping('m',[],lyric_ids=['a','b'])])
 rows=[dict(id='s1',text='一',lyric_ids=['a']),dict(id='s2',text='二',lyric_ids=['b'])]
 assert source_row_bindings(p,rows)=={'s1':'m','s2':'m'}
 assert source_row_bindings(p,rows[::-1])=={}
 assert source_row_bindings(p,rows+[rows[0]])=={}

def token(label='a',start=.2,end=.3,row='a'):
 return dict(label=label,token_id=1,start=start,end=end,start_frame=10,end_frame=15,score=.9,source_row_id=row,reading_index=0,source_start=0,source_end=1,ownership='kana')

def test_token_evidence_survives_candidate_apply_save_and_undo(bridge):
 ready(bridge,'audio');snapshot=capture_source(bridge.document,'vocal',bridge.media_controller.index)
 generated=Project.from_dict(bridge.document.project.to_dict());generated.lyrics=[Lyric('a','歌',None,None)]
 generated.replace_timeline([Mapping('a',[],lyric_ids=['a'],text_override='歌',text_role='source_import',start=.2,end=.8,alignment_tokens=[token()])])
 record=candidate_from_project(snapshot,generated);assert record['rows'][0]['alignment_tokens']==[token()]
 assert candidate_project(record).mappings[0].alignment_tokens==[token()]
 install_candidate(bridge.document,record);before=bridge.document.project.to_dict()
 plan=prepare_apply(bridge.document,record['id'],['a'],add_unmapped=True,frame_index=bridge.media_controller.index)
 apply_plan(bridge.document,bridge.history,plan,frame_index=bridge.media_controller.index)
 saved=Project.from_dict(bridge.document.project.to_dict());assert saved.mappings[0].alignment_tokens==[token()]
 bridge.document.project,_=bridge.history.restore(bridge.document.project);assert bridge.document.project.to_dict()==before

@pytest.mark.parametrize('missing',[False,True])
def test_multi_source_aggregate_keeps_chinese_and_exact_tokens(bridge,missing):
 ready(bridge,'audio');p=bridge.document.project
 p.lyrics=[Lyric('a','一',None,None),Lyric('b','二',None,None)];p.translations=[Translation('t','完整中文')]
 p.replace_timeline([Mapping('m',['t'],lyric_ids=['a','b'],text_override='完整中文')])
 snapshot=capture_source(bridge.document,'vocal',bridge.media_controller.index)
 generated=Project.from_dict(p.to_dict());generated.replace_timeline([
 Mapping('s1',[],lyric_ids=['a'],text_override='一',text_role='source_import',start=.2,end=.5,alignment_tokens=[token()]),
 Mapping('s2',[],lyric_ids=['b'],text_override='二',text_role='source_import',start=None if missing else 1.,end=None if missing else 1.5,alignment_tokens=[] if missing else [token('b',1.,1.2,'b')])])
 source=[dict(id='s1',text='一',lyric_ids=['a']),dict(id='s2',text='二',lyric_ids=['b'])]
 record=rebind_source_candidate(snapshot,candidate_from_project(snapshot,generated),source)
 assert len(record['rows'])==1
 row=record['rows'][0];assert row['sentence_id']=='m' and row['text']=='完整中文'
 assert row['start']==(None if missing else .2) and row['end']==(None if missing else 1.5)
 assert row['provenance']['translation_ids']==['t']
 assert len(row['alignment_tokens'])==(1 if missing else 2)

def test_rerun_defaults_skip_manual_confirmed_and_locked(bridge):
 from desktop_qt.candidate_dialog import CandidateDialog
 ready(bridge,'audio');p=bridge.document.project
 p.replace_timeline([Mapping('auto',[],text_override='a',start=.2,end=.5),Mapping('manual',[],text_override='b',start=1,end=1.2,human_edited=True),Mapping('locked',[],text_override='c',start=2,end=2.2,locked=True)])
 snapshot=capture_source(bridge.document,'vocal',bridge.media_controller.index)
 record=candidate_from_project(snapshot,p)
 d=CandidateDialog(bridge,record)
 assert d.chosen()==[0]
 assert '保护' in d.table.item(1,2).text() and '保护' in d.table.item(2,2).text()
 d.reject()

def test_rebuild_tampered_plan_cannot_drop_translation(bridge):
 record=conflict(bridge);before=bridge.document.project.to_dict()
 result=prepare_rebuild(bridge.document,record['id'],bridge.media_controller.index)
 result['project']['translations']=[]
 with pytest.raises(ValueError):commit_rebuild(bridge.document,bridge.history,result,bridge.media_controller.index)
 assert bridge.document.project.to_dict()==before


def test_mode_switch_isolates_vocal_without_deleting_history_and_undo(bridge,monkeypatch):
 from desktop_qt.timeline_views import current_candidate
 import desktop_qt.dialogs as dialogs
 ready(bridge,'audio');start(bridge);settled(bridge)
 bridge.document.main_timeline[0].human_edited=True;bridge._changed()
 old=deepcopy(bridge.document.source_candidates);before=bridge.document.project.to_dict()
 monkeypatch.setattr(dialogs,'choose_asset_clear',lambda:'detach')
 assert bridge._start_editor({'mode':'edit'})
 assert bridge.document.source_candidates==old
 assert not current_candidate(bridge.document,old[-1])
 assert not any(r['id']=='vocal' for r in timeline_choices(bridge.document))
 bridge.switchCandidateSource('vocal');assert bridge._candidate_record() is None and bridge._candidate_rows==[]
 answers=[];bridge.candidate_preview(old[-1]['id'],[],{},False,lambda *x:answers.append(x))
 assert answers and isinstance(answers[0][1],ValueError)
 with pytest.raises(ValueError,match='历史'):bridge.candidate_apply({'candidate_id':old[-1]['id']})
 bridge.switchCandidateSource('main')
 bridge.document.project,_=bridge.history.restore(bridge.document.project)
 assert bridge.document.project.to_dict()==before
 assert current_candidate(bridge.document,old[-1])
 assert bridge.document.source_candidates==old


def test_mode_cancel_preserves_and_detach_isolates_both_candidate_sources(bridge,monkeypatch):
 from desktop_qt.timeline_views import current_candidate
 import desktop_qt.dialogs as dialogs
 ready(bridge,'audio');start(bridge);settled(bridge)
 record=deepcopy(bridge.document.source_candidates[-1]);record['id']='ocr-test';record['source']='ocr'
 bridge.document.source_candidates.append(record)
 bridge.document.main_timeline[0].human_edited=True;bridge._changed()
 before=deepcopy(bridge.document.source_candidates);snapshot=bridge.document.snapshot()
 rows=deepcopy(bridge.document.project.to_dict()['mappings'])
 monkeypatch.setattr(dialogs,'choose_asset_clear',lambda:'cancel')
 assert not bridge._start_editor({'mode':'ocr'})
 assert bridge.document.snapshot()==snapshot and bridge._entry_mode=='audio'
 assert all(current_candidate(bridge.document,r) for r in before)
 monkeypatch.setattr(dialogs,'choose_asset_clear',lambda:'detach')
 assert bridge._start_editor({'mode':'ocr'})
 assert bridge._entry_mode=='edit' and bridge.document.source_candidates==before
 assert bridge.document.project.to_dict()['mappings']==rows
 assert not any(current_candidate(bridge.document,r) for r in before)
 assert bridge.command('history.undo',{})
 assert bridge.document.project.to_dict()['mappings']==rows
 assert all(current_candidate(bridge.document,r) for r in before)
