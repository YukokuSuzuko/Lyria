"""Frozen EXE acceptance for the bundled dictionary, its GUI and its owned worker."""
import ast
import json
from pathlib import Path

from build_v024_dual import sha, write


QA_TEXT = ("朝の空を見上げる", "小さな歌を重ねよう", "明日へ一緒に歩こう", "・・・", "(^o^)")
EXPECTED = [{"id": f"qa-reading-{i}", "text": text} for i, text in enumerate(QA_TEXT, 1)]


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def read(path):
    return path.read_text(encoding="utf-8-sig")


def checked_rows(rows, *, dialog=False, projection=False):
    prefix = "reference-" if dialog else ""
    identity = [(r["id"], r["text"]) for r in rows]
    require(identity == [(prefix + r["id"], r["text"]) for r in EXPECTED], "Frozen reading rows changed")
    if dialog:
        require([r["lyric_ids"] for r in rows] == [[r["id"]] for r in EXPECTED], "Wrong source ownership")
    for r in rows[:3]:
        reading = r["reading"]
        require(reading.strip() and not any("\u3040" <= c <= "\u9fff" for c in reading), "Invalid romanized reading")
    for r in rows:
        require(not any(i.get("hard_error") for i in r.get("reading_issues", [])), "Hard reading error")
    for r in rows[3:]:
        skipped = (any(i.get("kind") == "nonvocal_symbols" for i in r["reading_issues"])
                   if projection else r.get("alignment_skip") == "nonvocal_symbols")
        require(r["reading"] == "" and skipped, "Symbol row changed")
    return [r["reading"] for r in rows]


def verify_packaged_readings(source, edition, target, evidence, isolated, env):
    application = target / "Lyria.exe"
    identity = sha(application)
    tree = ast.parse(read(source / "desktop_qt/reading_dialog_probe.py"))
    contract = next(n for n in tree.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "ROWS" for t in n.targets))
    require(ast.literal_eval(contract.value) == EXPECTED, "Unexpected packaged QA contract")
    offline = dict(env, LYRIA_QA_READING_OFFLINE="1", HF_HUB_OFFLINE="1",
                   TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    report = evidence / "reading-dialog-artifacts/result.json"
    isolated("reading-dialog", [str(application), "--reading-dialog-self-check", str(report)], 180, offline)
    value = json.loads(read(report))
    state = json.loads(read(evidence / "reading-dialog/result.json"))
    fields = dict(kind="packaged-real-reading-dialog", ok=True, frozen=True, actual_modeltasks=True,
                  worker_mocked=False, network_guard=True, app_version=edition["label"],
                  external_reading_path="", exe_sha256=identity, screenshot_saved=True)
    require(all(value.get(k) == v for k, v in fields.items()), "Unverified packaged reading dialog")
    require(Path(value["executable"]).resolve() == application.resolve(), "Wrong packaged executable")
    require(value["private_desktop"] == state["desktop"] and state["desktop"].startswith("LyriaTest_")
            and state["returncode"] == 0 and state["desktop_handle_closed"] is True, "Desktop ownership mismatch")
    require((report.parent / "reading-dialog.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), "Missing screenshot")
    events = value["events"]
    require([e["action"] for e in events] == ["reading_probe", "readings"], "Missing real task events")
    for event in events:
        ctx = event["context"]
        require(not event.get("error") and ctx.get("effective_reading_python") == "@bundled-reading"
                and ctx.get("reading_runtime_python") == "" and ctx.get("reading_tool") == "sudachi",
                "Unverified reading runtime")
    probe = events[0]["value"]
    require(probe["reading_tool"]["ready"] and probe["reading_tool"]["bundled"]
            and probe["reading_runtime_python"] == "@bundled-reading", "Dictionary probe failed")
    worker_readings = checked_rows(events[1]["value"]["rows"], dialog=True)
    require(checked_rows(value["dialog_rows"], dialog=True, projection=True) == worker_readings, "Refill mismatch")
    request = evidence / "five-row-worker-request.json"
    result = evidence / "five-row-worker-result.json"
    write(request, dict(operation="readings", reading_tool="sudachi", rows=EXPECTED))
    isolated("five-row-worker", [str(application), "--reading-worker", "--request", str(request), "--result", str(result)], 180, offline)
    worker = json.loads(read(result))
    require(not worker.get("error") and checked_rows(worker["rows"]) == worker_readings, "Independent worker disagrees")
    require(sha(application) == identity, "Packaged executable changed")
    write(evidence / "packaged-reading-gate.json",
          dict(ok=True, source_commit=edition["commit"], source_fingerprint=edition["tracked_sha256"],
               exe_sha256=identity, dialog_report_sha256=sha(report), worker_result_sha256=sha(result),
               offline_environment=True, actual_modeltasks=True, worker_mocked=False))


def verify_relocated_files(target, evidence, runtime_hash):
    known = {i["path"]: i for i in json.loads(read(evidence / "postbuild.json"))["files"]}
    reference = "_internal/shared-runtime-reference.json"
    actual = {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()}
    require(actual == set(known) | {"BUILD_INCOMPLETE", reference}, "Unexpected relocated package inventory")
    require(json.loads(read(target / reference)) == dict(schema=1, runtime_relative="../shared-runtime",
            manifest_sha256=runtime_hash), "Wrong portable runtime reference")
    for name, row in known.items():
        p = target / name
        require(not p.is_symlink() and p.stat().st_size == row["size"] and sha(p) == row["sha256"],
                "Packaged file changed after relocation: " + name)
