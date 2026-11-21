"""Replay stored OCR candidates without scanning or modifying source documents.

This measures structural anomalies, not CER/semantic accuracy/manual timing IoU.
Usage: python tools/v021_pairing_replay.py INPUT.lyria OUTPUT.json
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
from copy import deepcopy
from hashlib import sha256
from lyricsync.project_document import load_document
from lyricsync.model import Project,Lyric,stable_id,valid_time
from lyricsync.matching import match_project
from lyricsync.sequence_alignment import eligible


def metrics(rows,old=False):
    get=lambda r,k:r.get(k) if old else getattr(r,k)
    vids=lambda r:r['provenance'].get('visual_ids',[]) if old else r.visual_ids
    shared=sum(bool(set(vids(a))&set(vids(b))) and valid_time(get(a,'start'),get(a,'end')) and valid_time(get(b,'start'),get(b,'end')) and min(get(a,'end'),get(b,'end'))>max(get(a,'start'),get(b,'start')) for i,a in enumerate(rows) for b in rows[i+1:])
    return dict(rows=len(rows),unlocated=sum(not valid_time(get(r,'start'),get(r,'end')) for r in rows),zero_or_negative=sum(get(r,'start') is not None and get(r,'end') is not None and get(r,'end')<=get(r,'start') for r in rows),under_100ms=sum(valid_time(get(r,'start'),get(r,'end')) and get(r,'end')-get(r,'start')<.1 for r in rows),shared_visual_overlapping_cue_pairs=shared)


def replay(source):
    before_hash=sha256(source.read_bytes()).hexdigest();document=load_document(source);results=[]
    for candidate in document.source_candidates:
        if candidate['source']!='ocr':continue
        payload=Project().to_dict();payload.pop('alignment_graph');payload.update(deepcopy(candidate['evidence']))
        project=Project.from_dict(payload);raw=deepcopy(project.to_dict()['visuals'])
        independent=bool(candidate['provenance']['files'].get('ktv'))
        if not independent:project.lyrics=[Lyric(stable_id('ocr-source',v.id),v.text,v.start,v.end) for v in project.visuals if eligible(v)]
        match_project(project)
        if not independent:
            project.lyrics=[]
            for row in project.mappings:row.lyric_ids=[]
        assert project.to_dict()['visuals']==raw
        results.append(dict(candidate=candidate['id'],provenance=candidate['provenance'],independent_original=independent,
            before=metrics(candidate['rows'],True),after=metrics(project.mappings),raw_evidence_unchanged=True))
    assert sha256(source.read_bytes()).hexdigest()==before_hash
    return dict(input_sha256=before_hash,candidates=results,limitations=[
        'Stored OCR observations replayed; no fresh scan or actual recognition accuracy evaluation.',
        'No manual truth: CER, IoU and actual maximum slip length unavailable.',
        'Offline cross-language structural associations remain uncertain.'])


if __name__=='__main__':
    source,output=map(Path,sys.argv[1:3]);result=replay(source)
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps([dict(candidate=r['candidate'],before=r['before'],after=r['after']) for r in result['candidates']],ensure_ascii=False))
