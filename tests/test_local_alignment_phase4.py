"""Contract tests use explicit synthetic worker data, NOT model inference."""
from copy import deepcopy
import sys
import threading
import time
from pathlib import Path
import pytest
from lyricsync.model import Mapping, Project, Lyric
from lyricsync.project_document import ProjectDocument
from lyricsync.candidate_tracks import capture_source, install_candidate, prepare_apply, apply_plan
from lyricsync.editing import History
from lyricsync import local_alignment as la
from lyricsync import local_alignment_runtime as lr
from lyricsync.local_models import QWEN

def document():
    return ProjectDocument(Project(mappings=[
        Mapping("chorus-one", [], start=8, end=9, text_override="Hello, world!"),
        Mapping("chorus-two", [], start=20, end=21, text_override="Hello, world!")]))
def response(words=None):
    return {"version": 1, "model_id": QWEN.id, "revision": QWEN.revision, "duration": 10,
            "words": words if words is not None else [
                {"text":"Hello", "start_time":1., "end_time":2., "score":.8},
                {"text":"world", "start_time":2., "end_time":3., "score":.7},
                {"text":"Hello", "start_time":5., "end_time":6., "score":.9},
                {"text":"world", "start_time":6., "end_time":7., "score":.6}]}
def candidate(doc=None, result=None, **kwargs):
    doc = doc or document()
    return la.candidate_from_alignment(capture_source(doc, "vocal"),
        la.request_rows(doc,main_source_confirmed=True), result or response(), **kwargs)

def test_repeated_lyrics_have_stable_ids_and_original_exact_text():
    doc = document()
    before = deepcopy(doc.snapshot())
    record = candidate(doc)
    assert doc.snapshot() == before
    assert [r["sentence_id"] for r in record["rows"]] == ["chorus-one", "chorus-two"]
    assert [r["text"] for r in record["rows"]] == ["Hello, world!"] * 2
    assert [(r["start"], r["end"]) for r in record["rows"]] == [(1.,3.), (5.,7.)]
    assert all(r["source"] == "vocal" for r in [record])

def test_explicit_apply_only_and_one_undo_restores():
    doc = document()
    before = deepcopy(doc.project.to_dict())
    record = candidate(doc)
    install_candidate(doc, record)
    assert doc.project.to_dict() == before
    history = History()
    plan = prepare_apply(doc, record["id"], [r["id"] for r in record["rows"]])
    apply_plan(doc, history, plan)
    assert doc.project.mappings[0].start == 1.
    # Existing History owns the undo contract.
    restored, _ = history.restore(doc.project, validator=doc.validate_project)
    assert restored.to_dict() == before

@pytest.mark.parametrize("mutator", [
    lambda words: words.pop(1),
    lambda words: words.reverse(),
    lambda words: words.__setitem__(1, {**words[1], "text":"unexpected"}),
])
def test_missing_or_changed_word_never_shifts_repeated_sentence(mutator):
    data = response()
    mutator(data["words"])
    record = candidate(result=data)
    assert all(r["start"] is None and r["end"] is None for r in record["rows"])
    assert all(r["status"] == "missing" for r in record["rows"])

@pytest.mark.parametrize("changes", [
    {"score": .0001}, {"repaired": True}, {"start_time": -1},
    {"end_time": 0}, {"start_time": float("nan")}, {"end_time": 999},
])
def test_untrustworthy_word_withholds_sentence_but_preserves_other(changes):
    data = response()
    data["words"][0].update(changes)
    record = candidate(result=data)
    assert record["rows"][0]["start"] is None
    assert record["rows"][1]["start"] == 5. or changes.get("end_time") == 999

def test_cross_sentence_word_never_gets_interpolated():
    doc = ProjectDocument(Project(mappings=[Mapping("a", [], text_override="sun"), Mapping("b", [], text_override="shine")]))
    result = response([{"text":"sunshine","start_time":1.,"end_time":4.,"score":.8}])
    record = candidate(doc, result)
    assert all(row["start"] is None for row in record["rows"])

def test_unknown_confidence_is_not_invented():
    data = response()
    for word in data["words"]:
        word.pop("score")
    record = candidate(result=data)
    assert record["rows"][0]["confidence"] == 0
    assert record["rows"][0]["start"] == 1.
    assert "未知" in "".join(record["rows"][0]["reasons"])

def test_source_lyrics_do_not_replace_main_text():
    doc = document()
    doc.project.lyrics = [Lyric("l1", "Hello world", None, None), Lyric("l2", "Hello world", None, None)]
    doc.project.mappings[0].lyric_ids = ["l1"]
    doc.project.mappings[1].lyric_ids = ["l2"]
    doc.project.mappings[0].text_override = "保留我手改的译文"
    rows = la.request_rows(doc, text_source="lyrics")
    record = la.candidate_from_alignment(capture_source(doc, "vocal"), rows, response())
    assert record["rows"][0]["text"] == "保留我手改的译文"
    assert record["rows"][0]["start"] == 1.

def test_offset_added_without_proportional_scaling():
    record = candidate(offset_seconds=50)
    assert record["rows"][0]["start"] == 51
    assert record["rows"][1]["end"] == 57

def test_invalid_model_result_rejected():
    data = response()
    data["revision"] = "untrusted"
    with pytest.raises(ValueError, match="对齐模型版本校验不匹配"):
        candidate(result=data)

def test_requests_reject_unknown_duplicate_ids_and_absent_original():
    doc = document()
    for ids in (["missing"], ["chorus-one", "chorus-one"]):
        with pytest.raises(ValueError):
            la.request_rows(doc, ids)
    with pytest.raises(ValueError, match="关联"):
        la.request_rows(doc, text_source="lyrics")

def test_cancel_before_generation_does_not_probe_or_download(monkeypatch):
    monkeypatch.setattr(la, "validate_options", lambda *a: pytest.fail("work must not start"))
    doc = document()
    before = doc.snapshot()
    with pytest.raises(InterruptedError):
        la.generate_vocal_candidate(doc, options={}, cancel=lambda: True)
    assert doc.snapshot() == before

@pytest.fixture
def fake_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(lr, "validate_runtime_path", lambda path, **kwargs: Path(sys.executable))
    script = tmp_path / "explicit_test_worker.py"
    return script

def test_process_success_and_progress(fake_worker):
    fake_worker.write_text('''import argparse,json,pathlib
p=argparse.ArgumentParser();p.add_argument("--request");p.add_argument("--result");a=p.parse_args()
print(json.dumps({"progress":42,"message":"explicit test process"}),flush=True)
pathlib.Path(a.result).write_text(json.dumps({"version":1,"test_only":True}))
''')
    progress = []
    result = lr.run_worker({}, "unused", script=fake_worker, progress=lambda *x: progress.append(x))
    assert result["test_only"] and progress[0][0] == 42

def test_process_failure_has_actionable_error(fake_worker):
    fake_worker.write_text('import json;print(json.dumps({"error":"Missing explicit dependency"}),flush=True);raise SystemExit(1)')
    with pytest.raises(RuntimeError, match="Missing explicit dependency"):
        lr.run_worker({}, "unused", script=fake_worker)

@pytest.mark.parametrize("cancelled", [True, False])
def test_worker_cancel_and_timeout_stop_process(fake_worker, cancelled):
    fake_worker.write_text("import time;time.sleep(60)")
    event = threading.Event()
    timer = threading.Timer(.2, event.set)
    if cancelled: timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(InterruptedError if cancelled else TimeoutError):
            lr.run_worker({}, "unused", script=fake_worker, cancel=event.is_set,
                          timeout=10 if cancelled else .2)
    finally:
        timer.cancel()
    assert time.monotonic() - started < 6

def test_reject_editor_runtime_and_system_python():
    with pytest.raises(ValueError):
        lr.validate_runtime_path(sys.executable)





def test_worker_progress_keeps_protocol_channel_during_library_redirection(monkeypatch):
    import io
    import json
    import runpy
    from contextlib import redirect_stdout
    before = list(sys.path)
    try:
        namespace = runpy.run_path(str(lr.worker_script()), run_name="explicit_worker_contract_test")
        emit = namespace["emit"]
        protocol, library_noise = io.StringIO(), io.StringIO()
        monkeypatch.setitem(emit.__globals__, "_PROTOCOL_STDOUT", protocol)
        with redirect_stdout(library_noise):
            print("third-party library chatter")
            emit(45, "真实模型前向计算")
        assert json.loads(protocol.getvalue()) == {"progress":45,"message":"真实模型前向计算"}
        assert library_noise.getvalue() == "third-party library chatter\n"
    finally:
        sys.path[:] = before


def test_real_worker_pipe_roundtrips_cjk_under_gbk_locale(tmp_path):
    """Real stdio process, no ML imports/downloads: oversize request fails first."""
    import json
    import subprocess
    request = tmp_path / "oversize.json"
    request.write_bytes(b" " * (2 * 1024 * 1024 + 1))
    result = tmp_path / "unused-result.json"
    message = "中文进度 / 日本語 / \U00020bb7"
    code = """import runpy,sys
sys.stdout.reconfigure(encoding='gbk', errors='strict')
sys.stderr.reconfigure(encoding='gbk', errors='strict')
worker, request, result, message = sys.argv[1:]
namespace = runpy.run_path(worker, run_name='explicit_encoding_contract')
namespace['emit'](45, message)
sys.argv = [worker, '--request', request, '--result', result]
raise SystemExit(namespace['main']())
"""
    process = subprocess.run([sys.executable, "-I", "-B", "-c", code,
        str(lr.worker_script()), str(request), str(result), message],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert process.returncode == 1
    assert process.stdout.isascii()
    events = [json.loads(line) for line in process.stdout.decode("utf-8").splitlines()]
    assert events == [{"progress":45,"message":message}, {"error":"单次提交给本地模型的数据量过大，请缩小歌词范围。"}]
    assert not result.exists()


def test_windows_stop_uses_only_owned_active_pid_tree(monkeypatch):
    from types import SimpleNamespace
    commands = []
    process = SimpleNamespace(pid=123456, returncode=None)
    process.poll = lambda: process.returncode
    def taskkill(command, **kwargs):
        commands.append((command, kwargs))
        process.returncode = -9
        return SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(lr, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(lr.subprocess, "run", taskkill)
    lr._stop_worker(process)
    assert commands[0][0] == ["taskkill", "/PID", "123456", "/T", "/F"]
    assert not commands[0][1].get("shell", False)
    assert commands[0][1]["timeout"] == 10
    lr._stop_worker(process)
    assert len(commands) == 1  # Never act on an already-exited, potentially reused PID.


def test_windows_tree_stop_failure_is_not_reported_as_complete(monkeypatch):
    from types import SimpleNamespace
    process = SimpleNamespace(pid=123456, returncode=None)
    process.poll = lambda: process.returncode
    def taskkill(*args, **kwargs):
        process.returncode = -9
        return SimpleNamespace(returncode=1, stdout="controlled failure")
    monkeypatch.setattr(lr, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(lr.subprocess, "run", taskkill)
    with pytest.raises(RuntimeError, match="未能确认后台模型进程已全部退出"):
        lr._stop_worker(process)

@pytest.mark.parametrize("mode", ["failure", "cancel", "timeout"])
def test_partial_worker_result_survives_cleanup_only_after_checkpoint(fake_worker,mode):
    fake_worker.write_text("""import argparse,json,pathlib,time
p=argparse.ArgumentParser();p.add_argument('--request');p.add_argument('--result');a=p.parse_args()
r=json.loads(pathlib.Path(a.request).read_text(encoding='utf-8'))
pathlib.Path(r['_partial_result_path']).write_text(json.dumps({'version':1,'chunking':{'batches':[{'row_ids':['first']}]},'words':[]}))
print(json.dumps({'progress':50,'message':'checkpoint-ready'}),flush=True)
""" + ("raise SystemExit(1)" if mode=="failure" else "time.sleep(60)"))
    event=threading.Event()
    def progress(*args):
        if mode=='cancel':event.set()
    with pytest.raises(lr.PartialAlignmentError) as error:
        lr.run_worker({},'unused',script=fake_worker,cancel=event.is_set,progress=progress,timeout=.5 if mode=='timeout' else 10)
    assert error.value.reason==('cancelled' if mode=='cancel' else 'failed')
    assert error.value.result['chunking']['batches'][0]['row_ids']==['first']
