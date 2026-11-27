"""Independent, synthetic FFmpeg/libass visible-frame regression (no real MV).

Uses a caller-selected native FFmpeg executable; never executes bundled .exe
resources. The historical legacy quantizer is only a control, not production.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lyricsync.export import render_ass
from lyricsync.frames import FrameIndex


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.ffmpeg:
        parser.error("A native FFmpeg with libass is required")
    if sys.platform != "win32" and Path(args.ffmpeg).suffix.lower() == ".exe":
        parser.error("Do not run bundled Windows executables on a non-Windows host")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    version = subprocess.check_output([args.ffmpeg, "-version"], text=True).splitlines()[0]
    rows = []
    for label, rate in (("30", 30), ("30000/1001", 30000 / 1001), ("60", 60)):
        count = 240
        index = FrameIndex([i / rate for i in range(count)], count / rate)
        entries = [(*index.interval(i, i + 2), "TEST") for i in range(2, count - 4, 8)]
        expected = np.zeros(count, dtype=bool)
        for first in range(2, count - 4, 8):
            expected[first - 1:first + 2] = True
        row = {"frame_rate": label, "frame_count": count, "event_count": len(entries)}
        for mode in ("legacy", "corrected"):
            path = out / f"visibility_{label.replace('/', '_')}_{mode}.ass"
            path.write_text(render_ass(entries, (320, 180), font="Arial", font_size=24,
                frame_index=index if mode == "corrected" else None), encoding="utf-8")
            run = subprocess.run([args.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                "-filter_threads", "1", "-f", "lavfi", "-i", f"color=c=black:s=320x180:r={label}",
                "-vf", f"ass={path.name}", "-frames:v", str(count), "-threads", "1",
                "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"], cwd=out,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=45)
            pixels = np.frombuffer(run.stdout, np.uint8).reshape(count, 180, 320)
            observed = pixels.max(axis=(1, 2)) > 50
            row[mode + "_different_frames"] = int((expected != observed).sum())
        rows.append(row)
    report = {"scope": "Synthetic CFR video only; does not resolve the user's Aegisub case",
              "ffmpeg": version, "results": rows,
              "successful": all(row["corrected_different_frames"] == 0 for row in rows)}
    (out / "validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["successful"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
