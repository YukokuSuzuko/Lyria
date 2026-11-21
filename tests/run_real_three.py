"""Run the actual three-input pipeline, without confirming uncertain mappings."""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lyricsync.model import Project
from lyricsync.pipeline import run
from lyricsync.storage import atomic_json
from lyricsync.export import validate_project


def main(video, original, translation, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    project = Project(inputs=dict(video=video, ktv=original, translation=translation))
    project.settings.update(engine="tesseract", language="eng", sample_fps=3,
                            roi=[0, 950, 1920, 1080], api_enabled=False)
    started = time.monotonic()
    run(project, output / "real_three.lyricsync.json",
        progress=lambda p, m: print(f"{p:.0%} {m}", flush=True))
    lyrics = {l.id: l.text for l in project.lyrics}
    translations = {t.id: t.text for t in project.translations}
    result = dict(elapsed_seconds=time.monotonic() - started,
                  original_units=len(lyrics), translation_units=len(translations),
                  visual_intervals=len(project.visuals), mapping_groups=len(project.mappings),
                  unresolved=len(project.unresolved()), stage=project.stage,
                  export_problems=validate_project(project), warnings=project.warnings,
                  api_calls=len(project.api_records),
                  mapping_error_rate=None,
                  note="No bilingual gold labels supplied. Structural candidates are not semantic ground truth.",
                  rows=[dict(id=m.id, original=[lyrics[i] for i in m.lyric_ids],
                             chinese=[translations[i] for i in m.translation_ids],
                             start=m.start, end=m.end, confidence=m.confidence,
                             reasons=m.reasons) for m in project.mappings])
    atomic_json(output / "three_input_results.json", result)
    print({k: v for k, v in result.items() if k not in ("rows", "export_problems")}, flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])
