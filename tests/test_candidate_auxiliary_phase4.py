"""Structured auxiliary audio safety. Small local files, no ML/network.

Legacy candidate hashes and missing-field behavior are checked independently.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import wave
import pytest
from lyricsync import candidate_tracks as ct
from lyricsync import local_alignment as la
from lyricsync import local_separation as ls
from lyricsync.editing import History
from lyricsync.local_models import QWEN, UVR
from lyricsync.model import Mapping, Project
from lyricsync.project_document import ProjectDocument, load_document, write_document

def document():
    return ProjectDocument(Project(mappings=[Mapping("one", [], text_override="Hello", start=1., end=2.)]))

def candidate(doc, stem):
    before = deepcopy(doc.project.inputs)
    snapshot = ct.capture_source(doc, "vocal", auxiliary_paths={"alignment_audio": str(stem)})
    generated = Project.from_dict(snapshot["project"])
    generated.mappings[0].start, generated.mappings[0].end = 3., 4.
    record = ct.candidate_from_project(snapshot, generated)
    ct.install_candidate(doc, record)
    assert doc.project.inputs == before
    return record

@pytest.fixture
def seeded(tmp_path):
    stem = tmp_path / "vocals.wav"
    stem.write_bytes(b"small synthetic audio bytes")
    doc = document()
    record = candidate(doc, stem)
    return doc, record, stem

def test_auxiliary_record_is_structured_signed_and_not_project_input(seeded):
    doc, record, stem = seeded
    item = record["provenance"]["auxiliary_files"]["alignment_audio"]
    assert item["sha256"] == hashlib.sha256(stem.read_bytes()).hexdigest()
    assert item["path"] == str(stem.resolve()).casefold()
    assert len(item["stat"]) == 5
    assert doc.project.inputs == {}
    before = deepcopy(record["provenance"])
    without = deepcopy(before)
    without.pop("auxiliary_files")
    assert ct._source_signature(without) != record["source_signature"]
    before["auxiliary_files"]["alignment_audio"]["sha256"] = "0" * 64
    assert ct._source_signature(before) != record["source_signature"]

@pytest.mark.parametrize("change", ["replace", "delete"])
def test_changed_or_deleted_stem_after_generation_blocks_preview_and_apply(seeded, change):
    doc, record, stem = seeded
    if change == "replace":
        stem.write_bytes(b"changed vocal content")
    else:
        stem.unlink()
    before = deepcopy(doc.project.to_dict())
    assert ct.candidate_source_status(doc, record)
    plan = ct.prepare_apply(doc, record["id"], ["one"])
    assert plan["entries"][0]["blocked"]
    history = History()
    with pytest.raises(ValueError):
        ct.apply_plan(doc, history, plan)
    assert doc.project.to_dict() == before and not history.undo_stack

@pytest.mark.parametrize("change", ["replace", "delete"])
def test_stem_changed_after_preview_blocks_atomic_apply(seeded, change):
    doc, record, stem = seeded
    plan = ct.prepare_apply(doc, record["id"], ["one"])
    assert not plan["entries"][0]["blocked"]
    if change == "replace":
        replacement = stem.with_suffix(".replacement")
        replacement.write_bytes(b"replacement bytes")
        replacement.replace(stem)
    else:
        stem.unlink()
    before = deepcopy(doc.project.to_dict())
    history = History()
    with pytest.raises(ValueError, match="辅助素材"):
        ct.apply_plan(doc, history, plan)
    assert not history.undo_stack and doc.project.to_dict() == before

def test_preview_rehashes_even_when_stat_and_supplied_primary_token_look_same(seeded, monkeypatch):
    doc, record, stem = seeded
    stale_stat = record["provenance"]["auxiliary_files"]["alignment_audio"]["stat"]
    token = ct.verify_provenance(doc)  # older caller supplies only the primary files.
    stem.write_bytes(b"changed bytes, hidden from a synthetic stat")
    monkeypatch.setattr(ct, "_stat", lambda path: list(stale_stat))
    plan = ct.prepare_apply(doc, record["id"], ["one"], current_provenance=token)
    assert plan["entries"][0]["blocked"]
    assert plan["provenance"]["auxiliary_files"]["alignment_audio"]["sha256"] != record["provenance"]["auxiliary_files"]["alignment_audio"]["sha256"]

def test_primary_token_is_augmented_only_with_fresh_auxiliary_hash(seeded):
    doc, record, stem = seeded
    primary = ct.verify_provenance(doc)
    plan = ct.prepare_apply(doc, record["id"], ["one"], current_provenance=primary)
    assert "auxiliary_files" not in primary
    assert not plan["entries"][0]["blocked"]
    assert plan["provenance"]["auxiliary_files"] == record["provenance"]["auxiliary_files"]
    verified = ct.verify_candidate_source(doc, record, current_provenance=primary)
    assert verified["auxiliary_files"] == record["provenance"]["auxiliary_files"]

def test_empty_or_mismatched_token_cannot_bypass_auxiliary_verification(seeded, tmp_path):
    doc, record, stem = seeded
    plan = ct.prepare_apply(doc, record["id"], ["one"], current_provenance={})
    assert plan["entries"][0]["blocked"]
    other = tmp_path / "other.wav"
    other.write_bytes(stem.read_bytes())
    wrong = ct.verify_provenance(doc, auxiliary_paths={"alignment_audio": str(other)})
    plan = ct.prepare_apply(doc, record["id"], ["one"], current_provenance=wrong)
    assert plan["entries"][0]["blocked"]
    good = ct.prepare_apply(doc, record["id"], ["one"])
    with pytest.raises(ValueError):
        ct.apply_plan(doc, History(), good, current_provenance=ct.verify_provenance(doc))

def test_cancel_during_auxiliary_rehash_remains_cancellation(seeded):
    doc, record, stem = seeded
    before = deepcopy(doc.snapshot())
    # Larger than one block so cancellation is exercised inside the read loop.
    stem.write_bytes(b"x" * (2 * 1024 * 1024))
    count = [0]
    def cancel():
        count[0] += 1
        return count[0] >= 4
    with pytest.raises(InterruptedError):
        ct.prepare_apply(doc, record["id"], ["one"], cancel=cancel)
    assert doc.snapshot() == before

def test_cancel_capture_never_installs_partial_token(tmp_path):
    stem = tmp_path / "stem.wav"
    stem.write_bytes(b"audio")
    doc = document()
    before = doc.snapshot()
    with pytest.raises(InterruptedError):
        ct.capture_source(doc, "vocal", auxiliary_paths={"alignment_audio": str(stem)}, cancel=lambda: True)
    assert doc.snapshot() == before

def test_save_reload_preserves_auxiliary_and_revalidates_after_replacement(seeded, tmp_path):
    doc, record, stem = seeded
    target = tmp_path / "saved.lyria"
    write_document(doc.snapshot(explicit_save=True), target)
    loaded = load_document(target)
    assert loaded.source_candidates == doc.source_candidates
    assert not ct.prepare_apply(loaded, record["id"], ["one"])["entries"][0]["blocked"]
    stem.unlink()
    assert ct.prepare_apply(loaded, record["id"], ["one"])["entries"][0]["blocked"]
    assert loaded.project.inputs == {}

def test_status_and_apply_do_stat_only_no_file_opens_or_hashing(seeded, monkeypatch):
    doc, record, stem = seeded
    plan = ct.prepare_apply(doc, record["id"], ["one"])
    def forbidden(*args, **kwargs):
        pytest.fail("UI/apply must not read or hash media")
    monkeypatch.setattr(ct, "open", forbidden, raising=False)
    monkeypatch.setattr(ct, "_hash_paths", forbidden)
    assert ct.candidate_source_status(doc, record) == ""
    history = History()
    assert ct.apply_plan(doc, history, plan) == ["one"]
    restored, _ = history.restore(doc.project, validator=doc.validate_project)
    assert restored.mappings[0].start == 1.

def test_legacy_missing_auxiliary_field_and_signature_remain_byte_identical(tmp_path):
    doc = document()
    media = tmp_path / "original.mp4"
    media.write_bytes(b"old input")
    doc.project.inputs["video"] = str(media)
    snap = ct.capture_source(doc, "ocr")
    assert set(snap["provenance"]) == {"files", "clock"}
    old_shape = {"files": {role: {"path": item["path"], "sha256": item["sha256"]}
                           for role,item in snap["provenance"]["files"].items()},
                 "clock":snap["provenance"]["clock"]}
    old_digest = hashlib.sha256(json.dumps(old_shape, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    record = ct.candidate_from_project(snap, Project.from_dict(snap["project"]))
    assert record["source_signature"] == old_digest
    safe, warning = ct.read_candidates([record])
    assert not warning and safe == [record]
    ct.install_candidate(doc, record)
    assert not ct.prepare_apply(doc, record["id"], ["one"])["entries"][0]["blocked"]

@pytest.mark.parametrize("mutate", [
    lambda p: p.update(auxiliary_files={}),
    lambda p: p.update(auxiliary_files=[]),
    lambda p: p["auxiliary_files"].update(extra={"path":"relative.wav","sha256":"0"*64,"stat":[0]*5}),
    lambda p: p["auxiliary_files"]["alignment_audio"].update(path="https://invalid.example/stem.wav"),
    lambda p: p["auxiliary_files"]["alignment_audio"].update(stat=[0,0,True,0,0]),
    lambda p: p["auxiliary_files"]["alignment_audio"].update(sha256="not-a-hash"),
    lambda p: p["auxiliary_files"]["alignment_audio"].update(extra="unrecognized"),
    lambda p: p.update(unknown_source_field={}),
])
def test_malformed_auxiliary_credentials_rejected_on_read(seeded, mutate):
    _, record, _ = seeded
    altered = deepcopy(record)
    mutate(altered["provenance"])
    records, warning = ct.read_candidates([altered])
    assert not records and warning

def test_removing_auxiliary_credentials_without_signature_update_rejected(seeded):
    _, record, _ = seeded
    altered = deepcopy(record)
    altered["provenance"].pop("auxiliary_files")
    assert ct.read_candidates([altered])[1]

def test_capture_explicit_auxiliary_with_existing_primary_token(tmp_path):
    doc = document()
    path = tmp_path / "stem.wav"
    path.write_bytes(b"audio")
    token = ct.verify_provenance(doc)
    captured = ct.capture_source(doc, "vocal", current_provenance=token,
                                 auxiliary_paths={"alignment_audio":str(path)})
    assert "auxiliary_files" in captured["provenance"]
    assert "auxiliary_files" not in token

def test_missing_initial_audio_produces_no_candidate(tmp_path):
    doc = document()
    with pytest.raises(ValueError, match="辅助素材"):
        ct.capture_source(doc, "vocal", auxiliary_paths={"alignment_audio":str(tmp_path / "missing.wav")})
    assert not doc.source_candidates

def _stub_generation(monkeypatch, doc, stem, *, mutate_inference_role=None,
                     mutate_separation_source=False):
    """Stub external decoding/inference, not mandatory separation orchestration.

    Actual prepare_vocals validates the float WAV, hashes/copies it and publishes
    its mapping manifest. Production capture/verification remains untouched.
    These small local contract workers are synthetic, not real ML evidence.
    """
    settings = dict(audio_path=str(stem), cache_root=str(stem.parent / "unused-cache"), model_id=QWEN.id,
                    device="cpu", language="English", rows=la.request_rows(doc,main_source_confirmed=True), runtime_python="not executed",
                    offset_seconds=0., duration_seconds=10., min_timestamp_score=.01, use_separation=True)
    calls = dict(verified_models=[], extractions=[], workers=[], generated_paths={})
    monkeypatch.setattr(la, "validate_options", lambda *args: settings)

    def verify(manager, model_id, **kwargs):
        calls["verified_models"].append(model_id)
    monkeypatch.setattr(la.ModelManager, "verify", verify)

    def extract(source, destination, offset, duration, cancel, *, ffmpeg=None,
                sample_rate=16000, channels=1, gain=1.):
        assert Path(source).is_file() and not cancel()
        calls["extractions"].append((Path(source), sample_rate, channels, gain))
        with wave.open(str(destination), "wb") as output:
            output.setnchannels(channels)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(b"\x00\x10" * (round(duration * sample_rate) * channels))
        return duration
    monkeypatch.setattr(la, "_extract_audio", extract)

    def separate(request, runtime, **kwargs):
        assert request["operation"] == "separate"
        assert runtime == "not executed" and Path(request["audio_wav"]).is_file()
        calls["workers"].append("separate")
        sample_rate, samples = 44100, 441000
        payload = struct.pack("<f", .125) * (samples * 2)
        # IEEE float stereo RIFF, genuinely checked by _validate_float_wav.
        fmt = struct.pack("<HHIIHH", 3, 2, sample_rate, sample_rate * 8, 8, 32)
        Path(request["output_wav"]).write_bytes(
            b"RIFF" + struct.pack("<I", 36 + len(payload)) + b"WAVEfmt " +
            struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(payload)) + payload)
        if mutate_separation_source:
            stem.write_bytes(b"changed during synthetic separation call")
        return dict(version=1, model_id=UVR.id, revision=UVR.revision,
                    adapter="lyria-mdx-hq3-ola-v1", sample_rate=sample_rate,
                    samples=samples, duration=10., latency_samples=0, padding_removed=True,
                    runtime={"synthetic_test_only": True}, warning="Synthetic contract worker, not ML")
    monkeypatch.setattr(ls, "run_worker", separate)

    def result(request, runtime, **kwargs):
        assert runtime == "not executed" and Path(request["audio_wav"]).is_file()
        assert calls["workers"] == ["separate"], "4A cannot bypass mandatory separation"
        calls["workers"].append("align")
        manifests = list((Path(settings["cache_root"]) / "stems-v1").glob("*/mapping.json"))
        assert len(manifests) == 1, "Production separation must publish a real manifest"
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        calls["generated_paths"].update(alignment_audio=stem,
            separated_vocals=Path(manifest["path"]), separation_manifest=manifests[0])
        assert calls["extractions"][0] == (stem, 16000, 1, 1.)  # input signal diagnostics
        assert calls["extractions"][1] == (stem, 44100, 2, 1.)
        assert calls["extractions"][2] == (Path(manifest["path"]), 16000, 1, 1.)
        if mutate_inference_role:
            calls["generated_paths"][mutate_inference_role].write_bytes(
                b"changed during synthetic alignment call")
        return {"version":1,"model_id":QWEN.id,"revision":QWEN.revision,"duration":10.,
                "words":[{"text":"Hello","start_time":3.,"end_time":4.,"score":.9}]}
    monkeypatch.setattr(la, "run_worker", result)
    return calls

def test_generation_wires_actual_audio_into_structured_token(tmp_path, monkeypatch):
    stem = tmp_path / "stem.wav"
    stem.write_bytes(b"explicit synthetic audio")
    doc = document()
    before = doc.snapshot()
    calls = _stub_generation(monkeypatch, doc, stem)
    record = la.generate_vocal_candidate(doc, options={})
    assert calls["verified_models"] == [QWEN.id, UVR.id]
    assert calls["workers"] == ["separate", "align"]
    credentials = record["provenance"]["auxiliary_files"]
    assert set(credentials) == {"alignment_audio", "separated_vocals", "separation_manifest"}
    for role, path in calls["generated_paths"].items():
        assert credentials[role]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert credentials[role]["path"] == str(path.resolve()).casefold()
        assert len(credentials[role]["stat"]) == 5
        altered = deepcopy(record["provenance"])
        altered["auxiliary_files"][role]["sha256"] = "0" * 64
        assert ct._source_signature(altered) != record["source_signature"]
    manifest = json.loads(calls["generated_paths"]["separation_manifest"].read_text(encoding="utf-8"))
    assert manifest["source_sha256"] == credentials["alignment_audio"]["sha256"]
    assert manifest["sha256"] == credentials["separated_vocals"]["sha256"]
    assert manifest["model_id"] == UVR.id and manifest["model_revision"] == UVR.revision
    assert manifest["samples"] == 441000 and manifest["latency_samples"] == 0
    assert record["rows"][0]["start"] == 3. and record["rows"][0]["end"] == 4.
    assert doc.snapshot() == before

@pytest.mark.parametrize("role", ["alignment_audio", "separated_vocals", "separation_manifest"])
def test_generation_rejects_audio_changed_during_worker(tmp_path, monkeypatch, role):
    stem = tmp_path / "stem.wav"
    stem.write_bytes(b"before")
    doc = document()
    before = doc.snapshot()
    _stub_generation(monkeypatch, doc, stem, mutate_inference_role=role)
    with pytest.raises(ValueError, match="自动打轴期间素材文件发生了变化"):
        la.generate_vocal_candidate(doc, options={})
    assert not doc.source_candidates and doc.snapshot() == before


def test_generation_rejects_source_changed_during_mandatory_separation(tmp_path, monkeypatch):
    source = tmp_path / "mix.wav"
    source.write_bytes(b"source before separation")
    doc = document()
    before = doc.snapshot()
    calls = _stub_generation(monkeypatch, doc, source, mutate_separation_source=True)
    with pytest.raises(ValueError, match="人声分离期间原音频文件发生了变化"):
        la.generate_vocal_candidate(doc, options={})
    assert calls["workers"] == ["separate"]  # 4A must not start on a changed mix.
    assert doc.snapshot() == before and not doc.source_candidates


@pytest.fixture
def generated_with_separation(tmp_path, monkeypatch):
    source = tmp_path / "source.wav"
    source.write_bytes(b"synthetic source for mandatory separation contract")
    doc = document()
    calls = _stub_generation(monkeypatch, doc, source)
    record = la.generate_vocal_candidate(doc, options={})
    ct.install_candidate(doc, record)
    return doc, record, calls["generated_paths"]


@pytest.mark.parametrize("role", ["alignment_audio", "separated_vocals", "separation_manifest"])
@pytest.mark.parametrize("change", ["replace", "delete"])
def test_generated_separation_credentials_block_changed_source_before_preview(
        generated_with_separation, role, change, tmp_path):
    doc, record, paths = generated_with_separation
    saved = tmp_path / "generated.lyria"
    write_document(doc.snapshot(explicit_save=True), saved)
    loaded = load_document(saved)
    assert loaded.source_candidates == doc.source_candidates
    path = paths[role]
    if change == "replace":
        path.write_bytes(b"changed generated auxiliary content")
    else:
        path.unlink()
    before = loaded.snapshot()
    assert ct.candidate_source_status(loaded, record)
    plan = ct.prepare_apply(loaded, record["id"], ["one"])
    assert plan["entries"][0]["blocked"]
    history = History()
    with pytest.raises(ValueError):
        ct.apply_plan(loaded, history, plan)
    assert loaded.snapshot() == before and not history.undo_stack


@pytest.mark.parametrize("role", ["alignment_audio", "separated_vocals", "separation_manifest"])
@pytest.mark.parametrize("change", ["replace", "delete"])
def test_generated_separation_credentials_block_changes_after_preview(
        generated_with_separation, role, change):
    doc, record, paths = generated_with_separation
    plan = ct.prepare_apply(doc, record["id"], ["one"])
    assert not plan["entries"][0]["blocked"]
    path = paths[role]
    if change == "replace":
        replacement = path.with_suffix(".replacement")
        replacement.write_bytes(b"replaced generated auxiliary")
        replacement.replace(path)
    else:
        path.unlink()
    before = doc.snapshot()
    history = History()
    with pytest.raises(ValueError, match="辅助素材"):
        ct.apply_plan(doc, history, plan)
    assert doc.snapshot() == before and not history.undo_stack


@pytest.mark.parametrize("role", ["separated_vocals", "separation_manifest"])
def test_generated_separation_preview_rehashes_stem_and_manifest_even_with_old_stat(
        generated_with_separation, role, monkeypatch):
    doc, record, paths = generated_with_separation
    old = record["provenance"]["auxiliary_files"][role]
    primary_only = ct.verify_provenance(doc)
    paths[role].write_bytes(b"different actual bytes despite synthetic unchanged stat")
    original_stat = ct._stat
    monkeypatch.setattr(ct, "_stat", lambda path: list(old["stat"])
                        if str(Path(path).resolve()).casefold() == old["path"] else original_stat(path))
    plan = ct.prepare_apply(doc, record["id"], ["one"], current_provenance=primary_only)
    assert plan["entries"][0]["blocked"]
    assert plan["provenance"]["auxiliary_files"][role]["sha256"] != old["sha256"]

