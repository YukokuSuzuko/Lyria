"""Run the portable backend and desktop-adapter suite with explicit, recorded desktop exclusions.

This is not a Windows, OCR-model, or full GUI acceptance command. Ordinary test
skips remain visible and are not counted as passes. The original full unittest
discovery command remains the authoritative full-suite entry point.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import sys
import unittest


DESKTOP_CLASSES = {
    "test_gui_playback.PlaybackTests",
    "test_v05.WorkspaceTests",
    "test_v08.DesktopRegressionTests",
    "test_v09.PlayerTests",
}


def leaves(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from leaves(item)
        else:
            yield item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json-report", type=Path)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve()
    sys.path.insert(0, str(source))
    tests = list(leaves(unittest.TestLoader().discover(str(source / "tests"), pattern="test_*.py")))
    excluded = [test.id() for test in tests if test.id().rsplit(".", 1)[0] in DESKTOP_CLASSES]
    selected = [test for test in tests if test.id() not in excluded]
    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(unittest.TestSuite(selected))
    versions = {}
    for package in ("av", "numpy", "opencv-python", "Pillow", "pytesseract", "rapidocr", "onnxruntime"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Portable backend and headless desktop-adapter regression; not full Windows/GUI/real-model acceptance",
        "source": str(source),
        "python": sys.version,
        "dependencies": versions,
        "discovered": len(tests),
        "desktop_excluded": len(excluded),
        "desktop_excluded_ids": excluded,
        "run": result.testsRun,
        "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
        "skipped": [{"id": test.id(), "reason": reason} for test, reason in result.skipped],
        "failures": [{"id": test.id(), "traceback": detail} for test, detail in result.failures],
        "errors": [{"id": test.id(), "traceback": detail} for test, detail in result.errors],
        "successful": result.wasSuccessful(),
    }
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Discovered {len(tests)}; excluded {len(excluded)} desktop tests; "
          f"ran {result.testsRun}; passed {report['passed']}; skipped {len(result.skipped)}; "
          f"failures {len(result.failures)}; errors {len(result.errors)}.")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
