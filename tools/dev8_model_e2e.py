"""Explicit local real-worker probe; never edits user project or launches UI."""
import json
from pathlib import Path
from lyricsync.model import Project,Lyric
from lyricsync.project_document import ProjectDocument
from lyricsync.local_alignment import generate_vocal_candidate,request_rows
from lyricsync.local_models import NEXTFIRE
from lyricsync.candidate_tracks import read_candidates,candidate_project,install_candidate,prepare_apply
root=Path(__file__).resolve().parents[1]
base=root.parent
fixture=json.loads((base/'work-dev7-alignment-20261114/.research/fixture.json').read_text(encoding='utf-8'))['clips'][0]['rows']
audio=base/'work-dev7-alignment-20261114/.research/opening/mix16.wav'
document=ProjectDocument(Project(lyrics=[Lyric(row['id'],row['text'],None,None,reading=row['reading']) for row in fixture]))
before=document.snapshot()
options=dict(audio_path=str(audio),runtime_python=str(base/'phase5-runtime-validation-20261112/env-cuda-retry/Scripts/python.exe'),cache_root=str(root/'dev8-nextfire-evidence/cache'),model_id=NEXTFIRE.id,language='Japanese',device='cuda:0',offset_seconds=0.,duration_seconds=18.,text_source='reference',use_separation=False)
record=generate_vocal_candidate(document,options=options,progress=lambda p,m:print(p,m,flush=True))
safe,warning=read_candidates([record]);assert not warning and len(safe)==1
project=candidate_project(record);project.validate_timeline()
assert document.snapshot()==before and not document.project.mappings
assert len(record['rows'])==len(fixture)
assert [r['text'] for r in record['rows']]==[r['text'] for r in fixture]
assert all(r['text_role']=='source_import' and r['status']=='new' for r in record['rows'])
install_candidate(document,record)
plan=prepare_apply(document,record['id'],[r['id'] for r in record['rows']],add_unmapped=True,apply_text=True)
assert not document.project.mappings
result={'plan':plan,'ok':True,'rows':record['rows'],'nonmutation':True,'direct_audio':True,'limitations':'18 second real pipeline connectivity, not alignment accuracy or full-song acceptance'}
output=root/'evidence-local/dev8-model-workflow/real-reference-e2e.json';output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print('SUCCESS',output)
