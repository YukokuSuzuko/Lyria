"""Replay saved evidence only; this is not cross-language semantic validation."""
from pathlib import Path
import argparse, importlib.util, json, sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lyricsync.model import Project,valid_time
from lyricsync.matching import match_project
parser=argparse.ArgumentParser();parser.add_argument('--audit',required=True);parser.add_argument('--baseline',required=True);parser.add_argument('--out',required=True);a=parser.parse_args()
raw=json.loads(Path(a.audit).read_text(encoding='utf-8'))
spec=importlib.util.spec_from_file_location('lyricsync.dev7_matching',a.baseline);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
before=Project.from_dict(raw);after=Project.from_dict(raw)
old.match_project(before);match_project(after)
changes=[]
for x,y in zip(before.mappings,after.mappings):
    if (x.start,x.end,x.visual_ids)!=(y.start,y.end,y.visual_ids):
        changes.append({'id':y.id,'before':{'start':x.start,'end':x.end,'visual_ids':x.visual_ids},'after':{'start':y.start,'end':y.end,'visual_ids':y.visual_ids},'reasons':y.reasons})
report={'before_timed':sum(valid_time(r.start,r.end) for r in before.mappings),'after_timed':sum(valid_time(r.start,r.end) for r in after.mappings),'mapping_count':len(after.mappings),'changes':changes,'warnings':sum(any('没有跨语言句意依据' in n for n in r.reasons) for r in after.mappings),'limitations':'Candidate source grouping has no cross-language meaning evidence. More timed rows is NOT an accuracy measurement. No media or user project edits.'}
p=Path(a.out);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2))
