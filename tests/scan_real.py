"""Local user-supplied MV test. No translation is invented or exported."""
from pathlib import Path
from dataclasses import asdict
import sys,time,json

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lyricsync.storage import Cache,atomic_json,file_hash
from lyricsync.subtitles import import_original
from lyricsync.vision import scan
from lyricsync.matching import associate_visuals
from lyricsync.export import convert_ktv


def run(video,source,folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    lyrics,warnings=import_original(source)
    started=time.monotonic()
    cache=Cache(folder/"real.cache.sqlite")
    try:
        def progress(p,m):
            if not hasattr(progress,"last") or p-progress.last>.025 or p==1:
                print(f"{p:.0%} {m}",flush=True);progress.last=p
        visuals,info=scan(video,{"engine":"tesseract","language":"eng","sample_fps":3,"roi":[0,950,1920,1080],"visibility":.62},
            cache,[l.text for l in lyrics],progress)
        associations,unknown=associate_visuals(lyrics,visuals)
        atomic_json(folder/"real_visual_results.json",{"video":info,"elapsed":time.monotonic()-started,
            "input_hashes":{"video":file_hash(video),"source":file_hash(source)},
            "lyrics":[asdict(l) for l in lyrics],"visuals":[asdict(v) for v in visuals],"warnings":warnings,
            "associations":{key:[{"visual_id":v.id,"confidence":score,"reasons":notes} for v,score,notes in values] for key,values in associations.items()},
            "unknown_visuals":unknown,"translation_matching":"not tested: user translation not supplied"})
        units,notes=convert_ktv(source,folder/"real_KTV_普通原文.ass",overwrite=True)
        atomic_json(folder/"real_conversion.json",{"units":len(units),"warnings":notes,"notes":[{"id":u.id,"warnings":u.warnings} for u in units if u.warnings]})
        print(json.dumps({"visuals":len(visuals),"lyrics":len(lyrics),"unknown":len(unknown),"elapsed":time.monotonic()-started}),flush=True)
    finally:cache.close()


if __name__=="__main__":run(*sys.argv[1:])
