from pathlib import Path
import json
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lyricsync.model import Project
from lyricsync.pipeline import run
from lyricsync.export import export_project,convert_ktv
from lyricsync.storage import save_project,atomic_json
from lyricsync.model import normalized


def synthetic(folder):
    root=Path(__file__).resolve().parents[1]
    examples=root/"examples"
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    project=Project(inputs={"video":str(examples/"synthetic_mv.mp4"),"ktv":str(examples/"synthetic_ktv.ass"),
                            "translation":str(examples/"synthetic_bilingual.txt")})
    project.settings.update(engine="rapidocr",language="jpn+eng",sample_fps=3)
    started=time.monotonic()
    path=folder/"synthetic.lyricsync.json"
    run(project,path,progress=lambda p,m:print(f"{p:.0%} {m}",flush=True))
    truth=json.loads((examples/"truth.json").read_text(encoding="utf-8"))["cues"]
    rows=[]
    matched=set()
    for cue in truth:
        candidates=[v for v in project.visuals if normalized(v.text)==normalized(cue["text"]) and v.id not in matched]
        if not candidates:
            rows.append({"text":cue["text"],"detected":False});continue
        visual=min(candidates,key=lambda v:abs(v.start-cue["start"]))
        matched.add(visual.id)
        rows.append({"text":cue["text"],"detected":True,"start_error":visual.start-cue["start"],"end_error":visual.end-cue["end"],
            "warnings":visual.warnings,"fade":cue.get("fade",False)})
    errors=[r for r in rows if r["detected"]]
    # Semantic mapping truth exists for these original, synthetic bilingual pairs.
    trans={t.id:t for t in project.translations};lyrics={l.id:l for l in project.lyrics}
    mapping_errors=[]
    for m in project.mappings:
        if len(m.translation_ids)!=1 or len(m.lyric_ids)!=1 or normalized(trans[m.translation_ids[0]].reference)!=normalized(lyrics[m.lyric_ids[0]].text):mapping_errors.append(m.id)
    metrics={"fixture":"original synthetic video; not a real MV", "elapsed_seconds":time.monotonic()-started,
        "detected":len(errors),"expected":len(truth),"start_mae":sum(abs(r["start_error"]) for r in errors)/max(1,len(errors)),
        "end_mae":sum(abs(r["end_error"]) for r in errors)/max(1,len(errors)),"mapping_errors":mapping_errors,
        "unresolved":len(project.unresolved()),"rows":rows,"visuals":len(project.visuals)}
    atomic_json(folder/"synthetic_metrics.json",metrics)
    print(json.dumps(metrics,ensure_ascii=False,indent=2),flush=True)
    if not project.unresolved() and not project.warnings:export_project(project,folder/"synthetic_auto_中文.ass",overwrite=True)
    # Keep a clearly labelled manually confirmed fixture output to test review/export.
    for m in project.mappings:
        if m.start is not None and m.end is not None:m.confirmed=m.locked=True
    project.settings["parser_acknowledged"]=True
    save_project(project,folder/"synthetic_reviewed.lyricsync.json")
    export_project(project,folder/"synthetic_reviewed_中文.ass",overwrite=True)
    convert_ktv(examples/"synthetic_ktv.ass",folder/"synthetic_普通.ass",overwrite=True)


if __name__=="__main__":synthetic(sys.argv[1])
