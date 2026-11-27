"""Native Qt dialog lifecycle, no install/download/inference."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import time
import pytest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox
from lyricsync.project_document import ProjectDocument
from lyricsync.model import Project, Mapping
from desktop_qt.local_alignment_dialog import LocalAlignmentDialog
from lyricsync.local_models import ModelManager

@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])

@pytest.fixture(autouse=True)
def isolated_app_model_tasks(application):
    from PySide6.QtCore import QCoreApplication, QEvent
    from desktop_qt.local_model_tasks import model_tasks
    if hasattr(application, "_lyria_model_tasks"):
        old = application._lyria_model_tasks
        old.shutdown()
        application.processEvents()
        old.deleteLater()
        delattr(application, "_lyria_model_tasks")
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    yield
    service = model_tasks()
    service.shutdown()
    application.processEvents()
    for widget in application.topLevelWidgets():
        if isinstance(widget, LocalAlignmentDialog):
            widget.reject()
            widget.deleteLater()
    service.deleteLater()
    delattr(application, "_lyria_model_tasks")
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def doc():
    return ProjectDocument(Project(mappings=[Mapping("one", [], text_override="Hello")]))

def wait_for(app, condition):
    until=time.monotonic()+5
    while not condition() and time.monotonic()<until:
        app.processEvents()
        time.sleep(.01)
    assert condition()

def test_open_and_setup_accept_do_not_start_download_inference_or_install(application, monkeypatch):
    monkeypatch.setattr(ModelManager, "download", lambda *a, **k: pytest.fail("no download"))
    dialog = LocalAlignmentDialog(doc(), setup_only=True)
    before = dialog.document.snapshot()
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).text() == "关闭"
    dialog._accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.document.snapshot() == before
    assert dialog._task is None

def test_start_requires_original_lyrics_confirmation_and_valid_setup(application):
    default_dlg = LocalAlignmentDialog(doc(), setup_only=True)
    assert default_dlg.alignment_model.currentData() == "nextfire-ja-latn"
    default_dlg.reject()
    dialog = LocalAlignmentDialog(doc(), initial_options={"model_id": "qwen3-fa-hf"})
    dialog._accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "演唱原文" in dialog.status.text()
    dialog.text_source.setCurrentIndex(dialog.text_source.findData("main"))
    dialog.main_source_confirm.setChecked(True)
    dialog._accept()
    assert "找不到音频" in dialog.status.text()
    dialog.confirm.setChecked(True)
    dialog._accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog._task is None
    dialog.reject()

def test_closing_busy_dialog_keeps_owned_task_and_reopens_same_progress(application):
    dialog = LocalAlignmentDialog(doc(), setup_only=True)
    def work(cancel, progress):
        progress(35, "synthetic 3 MB / 9 MB · 1 MB/s · 6秒")
        while not cancel():
            time.sleep(.01)
        raise InterruptedError("synthetic cancelled")
    dialog._start_task(work, action="download")
    wait_for(application, lambda: dialog.progress.value() == 35)
    task = dialog._task
    dialog.reject()
    assert dialog._task is task and task.isRunning() and not task.cancelled.is_set()
    reopened = LocalAlignmentDialog(doc(), setup_only=True)
    assert reopened._task is task and reopened.progress.value() == 35
    assert "1 MB/s" in reopened.status.text() and reopened.pause_task.isEnabled()
    reopened._cancel_task()
    wait_for(application, lambda: reopened._task is None)
    assert "synthetic cancelled" in reopened.status.text()
    assert dialog.result() == QDialog.DialogCode.Rejected
    reopened.reject()


def test_repeated_start_does_not_create_second_task(application):
    dialog = LocalAlignmentDialog(doc(), setup_only=True)
    calls = []
    def work(cancel, progress):
        calls.append(1)
        while not cancel(): time.sleep(.01)
    dialog._start_task(work)
    task = dialog._task
    dialog._start_task(lambda *a: pytest.fail("second task"))
    assert dialog._task is task
    dialog._cancel_task()
    wait_for(application, lambda: dialog._task is None)
    assert len(calls) == 1
    dialog.reject()

def test_initial_options_roundtrip_not_persisted_into_project(application):
    initial = dict(language="English", device="cpu", runtime_python="fake-python", cache_root="fake-cache")
    original = doc()
    before = original.snapshot()
    dialog = LocalAlignmentDialog(original, setup_only=True, initial_options=initial)
    dialog._accept()
    assert dialog.options()["language"] == "English"
    assert dialog.options()["device"] == "cpu"
    assert original.snapshot() == before


def test_source_default_and_selection_never_trigger_network(application, monkeypatch):
    monkeypatch.setattr(ModelManager, "download", lambda *a, **k: pytest.fail("no implicit download"))
    dialog = LocalAlignmentDialog(doc(), setup_only=True)
    assert dialog.alignment_model.currentData() == "nextfire-ja-latn"
    assert dialog.download_source.currentData() == "mirror"
    dialog.download_source.setCurrentIndex(dialog.download_source.findData("modelscope"))
    assert "053506a54ad1c9deea6105050330d7c500b9f098" in dialog.license_page.toPlainText()
    assert "国内镜像源" in dialog.source_details.text()
    assert "断点续传" in dialog.source_details.text()
    assert dialog._task is None
    dialog._accept()
    assert dialog.options()["download_source"] == "modelscope"

def test_declining_selected_source_download_does_nothing(application, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    dialog = LocalAlignmentDialog(doc(), setup_only=True, initial_options={"model_id": "qwen3-fa-hf", "download_source":"modelscope"})
    notices = []
    def no(title, message):
        notices.append(message)
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(dialog, "_question", no)
    monkeypatch.setattr(dialog, "_model_task", lambda *a, **k: pytest.fail("declined"))
    dialog._download()
    assert "ModelScope" in notices[0] and "c07281df297b9905d24a508279258cccf987a064" in notices[0]
    assert dialog._task is None
    dialog.reject()

def test_explicit_download_passes_only_confirmed_source(application, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    dialog = LocalAlignmentDialog(doc(), setup_only=True, initial_options={"model_id": "qwen3-fa-hf"})
    dialog.download_source.setCurrentIndex(dialog.download_source.findData("modelscope"))
    monkeypatch.setattr(dialog, "_question", lambda *a: QMessageBox.StandardButton.Yes)
    seen = []
    monkeypatch.setattr(dialog, "_model_task", lambda *args, **kwargs: seen.append((args,kwargs)))
    dialog._download()
    assert seen == [(("download",), {"source":"modelscope", "model_id":"qwen3-fa-hf"})]
    dialog.reject()
    nf_dialog = LocalAlignmentDialog(doc(), setup_only=True)
    monkeypatch.setattr(nf_dialog, "_question", lambda *a: QMessageBox.StandardButton.Yes)
    nf_seen = []
    monkeypatch.setattr(nf_dialog, "_model_task", lambda *args, **kwargs: nf_seen.append((args, kwargs)))
    nf_dialog._download()
    assert nf_seen == [(("download",), {"source": "mirror", "model_id": "nextfire-ja-latn"})]
    nf_dialog.reject()


def lyrics_doc():
    from lyricsync.model import Lyric
    return ProjectDocument(Project(
        lyrics=[Lyric("original1", "こんにちは", None, None), Lyric("original2", "世界", None, None)],
        mappings=[Mapping("one", [], lyric_ids=["original1"], text_override="你好"),
                  Mapping("two", [], lyric_ids=["original2"], text_override="世界译文")]))

def test_fully_linked_nonempty_originals_default_to_lyrics(application):
    dialog = LocalAlignmentDialog(lyrics_doc(), setup_only=True)
    assert dialog.text_source.currentData() == "reference"
    assert dialog.language.currentData() == "Japanese"  # No language inference was performed.
    dialog.reject()

@pytest.mark.parametrize("missing", ["no_reference", "unknown_reference", "empty_original"])
def test_reference_source_does_not_depend_on_mapping_links(application, missing):
    document = lyrics_doc()
    if missing == "no_reference":
        document.project.mappings[1].lyric_ids = []
    elif missing == "unknown_reference":
        from copy import deepcopy
        document.project.mappings[1].lyric_ids = ["not-present"]
        try:
            with pytest.raises(ValueError,match='missing evidence'):
                deepcopy(document)  # dangling graph IDs must not become a dialog snapshot
        finally:
            document.project.mappings[1].lyric_ids = []
        # A valid but unbound original remains available from its source table.
    else:
        document.project.lyrics[1].text = "  "
    dialog = LocalAlignmentDialog(document, setup_only=True)
    assert dialog.text_source.currentData() == "reference"
    dialog.reject()

def test_explicit_main_choice_is_preserved_even_with_all_originals(application):
    dialog = LocalAlignmentDialog(lyrics_doc(), setup_only=True, initial_options={"text_source":"main"})
    assert dialog.text_source.currentData() == "main"
    dialog.first.setValue(2)
    assert dialog.text_source.currentData() == "main"
    dialog.text_source.setCurrentIndex(dialog.text_source.findData("reference"))
    dialog.first.setValue(1)
    assert dialog.text_source.currentData() == "reference"
    dialog.reject()



def test_long_error_scrolls_without_hiding_buttons_and_recovers(application):
    from PySide6.QtGui import QFontDatabase
    from pathlib import Path
    # The offscreen backend may not enumerate native Windows CJK fonts.
    # Load only the installed OS font into this test process, never install one.
    native_font = Path("C:/Windows/Fonts/msyh.ttc")
    font_id = QFontDatabase.addApplicationFont(str(native_font)) if native_font.is_file() else -1
    try:
        from PySide6.QtCore import QPoint, Qt
        dialog = LocalAlignmentDialog(doc(), setup_only=True,
            initial_options={"download_source":"modelscope"})
        before = dialog.document.snapshot()
        dialog.resize(760, 600)
        dialog.show()
        application.processEvents()
        error = "\n".join(f"错误行 {i:02d}：离线依赖检查失败，完整诊断必须保留，不截断。" for i in range(70))
        dialog._outcome(None, error)
        for _ in range(5): application.processEvents()
        assert dialog.status.text() == error
        assert dialog.height() <= 600
        assert dialog.minimumSizeHint().height() <= 600
        assert dialog.buttons.geometry().bottom() < dialog.rect().bottom()
        assert dialog.buttons.button(QDialogButtonBox.Cancel).isHidden()
        for button in (dialog.buttons.button(QDialogButtonBox.Ok),):
            assert button.isVisible()
            top = button.mapTo(dialog, QPoint(0, 0))
            bottom = button.mapTo(dialog, button.rect().bottomRight())
            assert dialog.rect().contains(top) and dialog.rect().contains(bottom)
        scroll = dialog.status_scroll.verticalScrollBar()
        assert scroll.maximum() > 0
        scroll.setValue(scroll.maximum())
        application.processEvents()
        assert scroll.value() == scroll.maximum()
        bottom = dialog.status.mapTo(dialog.status_scroll.viewport(), dialog.status.rect().bottomRight())
        assert bottom.y() <= dialog.status_scroll.viewport().rect().bottom() + 1
        assert dialog.status.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
        dialog.status.setSelection(0, len(error))
        # QLabel selectedText uses Unicode paragraph separators for newlines.
        assert dialog.status.selectedText().replace("\u2029", "\n") == error  # Do not change OS clipboard.
        dialog._progress(100, "检测完成")
        for _ in range(5): application.processEvents()
        assert dialog.status.text() == "检测完成"
        assert scroll.maximum() == 0 and scroll.value() == 0
        assert dialog.height() <= 600
        assert dialog.buttons.geometry().bottom() < dialog.rect().bottom()
        dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).click()
        application.processEvents()
        assert not dialog.isVisible()
        assert dialog.result() == QDialog.DialogCode.Rejected
        assert dialog.document.snapshot() == before
    finally:
        if "dialog" in locals():
            dialog.reject()
        if font_id >= 0:
            QFontDatabase.removeApplicationFont(font_id)


def test_pause_resume_keeps_original_work_and_context(application):
    from desktop_qt.local_model_tasks import model_tasks
    service = model_tasks()
    calls = []
    def work(cancel, progress):
        calls.append("original-source")
        while not cancel(): time.sleep(.005)
        raise InterruptedError("paused transfer")
    service.start(work, {"download_source":"modelscope", "cache_root":"original-cache", "audio_path":"private"}, "download")
    wait_for(application, lambda: len(calls) == 1)
    service.stop("pause")
    wait_for(application, lambda: service.task is None)
    dialog = LocalAlignmentDialog(doc(), setup_only=True, initial_options={"download_source":"huggingface"})
    assert dialog.download_source.currentData() == "modelscope"
    assert "audio_path" not in service.context
    assert dialog.resume_task.isEnabled()
    dialog.cache.setText("changed-cache")
    dialog.download_source.setCurrentIndex(0)
    dialog._resume_task()
    wait_for(application, lambda: len(calls) == 2)
    assert service.context["cache_root"] == "original-cache"
    assert service.context["download_source"] == "modelscope"
    service.stop()
    wait_for(application, lambda: service.task is None)


def test_completed_task_does_not_override_new_preferences(application):
    from desktop_qt.local_model_tasks import model_tasks
    service = model_tasks()
    service.start(lambda cancel, progress: "ok", {"language":"Japanese", "download_source":"huggingface"})
    wait_for(application, lambda: service.task is None)
    dialog = LocalAlignmentDialog(doc(), setup_only=True,
        initial_options={"language":"English", "download_source":"modelscope"})
    assert dialog.language.currentData() == "English"
    assert dialog.download_source.currentData() == "modelscope"


def test_shutdown_waits_for_owned_worker_and_preserves_cleanup_uncertainty(application):
    import threading
    from desktop_qt.local_model_tasks import model_tasks
    service = model_tasks()
    entered, exited = threading.Event(), threading.Event()
    def work(cancel, progress):
        entered.set()
        while not cancel(): time.sleep(.005)
        exited.set()
        raise RuntimeError("cannot confirm owned descendants stopped")
    service.start(work, {})
    wait_for(application, entered.is_set)
    service.stop("cancel")
    service.shutdown()
    assert exited.is_set()
    wait_for(application, lambda: service.task is None)
    assert "cannot confirm owned descendants stopped" in service.error
    assert "已取消" in service.error
    dialog = LocalAlignmentDialog(doc(), setup_only=True)
    assert "cannot confirm" in dialog.status.text()


@pytest.mark.parametrize("method,model_id,aligner_source,expected_source", [
    ("_download_separation", "uvr-mdx-inst-hq3", "modelscope", "mirror"),
    ("_download_asr", "qwen3-asr-hf", "modelscope", "mirror"),
    ("_download_separation", "uvr-mdx-inst-hq3", "huggingface", "official"),
    ("_download_asr", "qwen3-asr-hf", "huggingface", "official"),
])
def test_model_specific_download_source_maps_mirror_or_official(application, monkeypatch, method, model_id, aligner_source, expected_source):
    from PySide6.QtWidgets import QMessageBox
    dialog = LocalAlignmentDialog(doc(), setup_only=True, initial_options={"download_source": aligner_source})
    notices, calls = [], []
    def yes(title, notice):
        notices.append(notice)
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(dialog, "_question", yes)
    monkeypatch.setattr(dialog, "_model_task", lambda *a, **k: calls.append((a,k)))
    getattr(dialog, method)()
    assert calls == [(("download",), {"source": expected_source, "model_id": model_id})]
    assert "校验完整性" in notices[0]
    monkeypatch.setattr(dialog, "_question", lambda *a: QMessageBox.StandardButton.No)
    getattr(dialog, method)()
    assert len(calls) == 1


def test_preflight_and_plain_original_source_names(application):
    dialog = LocalAlignmentDialog(doc(), setup_only=True)
    assert [dialog.text_source.itemText(i) for i in range(2)] == ["工程中已导入的演唱原词", "外部原词 / 剪贴板副本（保留复用）"]
    assert "未配置" in dialog.runtime_status.text()
    assert dialog.text_source.currentData()=="external" and dialog.source_preview.toPlainText()==""
    dialog.text_source.setCurrentIndex(dialog.text_source.findData("main"))
    assert dialog.source_preview.toPlainText()==""
    dialog.main_source_confirm.setChecked(True)
    assert "共 1 句" == dialog.lyric_count.text() and dialog.source_preview.toPlainText() == "Hello"
    assert [dialog.download_source.itemData(i) for i in range(dialog.download_source.count())] == ["mirror", "official", "modelscope", "huggingface"]
    assert not dialog.asr_generate.isEnabled()
    assert not dialog.draft_use.isEnabled()
    assert not dialog.listen_original.isEnabled()


def make_draft(document, tmp_path):
    from lyricsync.local_models import ASR
    import hashlib
    source = tmp_path / "synthetic-source.bin"; source.write_bytes(b"synthetic-only")
    stem = tmp_path / "synthetic-stem.bin"; stem.write_bytes(b"synthetic-stem")
    return dict(kind="transcript_draft", version=1, id="draft-test", document_id=document.id,
        revision=document.revision, status="unreviewed", model_id=ASR.id, model_revision=ASR.revision,
        text="Hello\nHello", complete=True, source_path=str(source.resolve()),
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), stem_path=str(stem.resolve()),
        stem_sha256=hashlib.sha256(stem.read_bytes()).hexdigest(), offset_seconds=12., duration_seconds=5.,
        requested_duration_seconds=8., language="English", warning="synthetic draft: needs correction")


def draft_dialog(application, tmp_path, monkeypatch):
    from lyricsync import local_transcription as trans
    document = doc(); draft = make_draft(document, tmp_path)
    dialog = LocalAlignmentDialog(document, default_audio=draft["source_path"], initial_options={
        "model_id": "qwen3-fa-hf", "language":"English", "offset_seconds":12., "duration_seconds":8.})
    monkeypatch.setattr(trans, "validate_transcription_options", lambda v: dict(v))
    dialog._show_draft(draft)
    return dialog, draft


def test_draft_requires_explicit_confirmation_and_edits_clear_it(application, tmp_path, monkeypatch):
    dialog, draft = draft_dialog(application, tmp_path, monkeypatch)
    monkeypatch.setattr(ModelManager,"status",lambda self,identity:{"present":True,"verification_cached":True})
    before = dialog.document.snapshot()
    assert dialog._preview_player is None  # opening/showing a draft never plays audio
    assert not dialog.draft_confirm.isChecked() and not dialog.draft_use.isChecked()
    dialog.text_source.setCurrentIndex(dialog.text_source.findData("draft"))
    dialog._accept()
    assert dialog.result() != QDialog.DialogCode.Accepted and "采用" in dialog.status.text()
    dialog._adopt_draft()
    assert dialog._adopted_draft is not None
    dialog.draft_text.setPlainText("Corrected\nRepeated")
    assert dialog._adopted_draft is None
    dialog._adopt_draft()
    dialog._accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    confirmed = dialog.options()["transcript_draft"]
    assert confirmed["status"] == "confirmed"
    assert [r["text"] for r in confirmed["rows"]] == ["Corrected", "Repeated"]
    assert len(set(r["id"] for r in confirmed["rows"])) == 2
    assert dialog.document.snapshot() == before
    from desktop_qt.local_alignment_settings import clean
    assert "transcript_draft" not in clean(dialog.options())


@pytest.mark.parametrize("change", ["audio", "offset", "duration", "language"])
def test_changed_draft_inputs_require_new_generation(application, tmp_path, monkeypatch, change):
    dialog, draft = draft_dialog(application, tmp_path, monkeypatch)
    dialog._adopt_draft()
    if change == "audio": dialog.audio.setText(str(tmp_path / "other-source"))
    if change == "offset": dialog.offset.setValue(13.)
    if change == "duration": dialog.duration.setValue(7.)
    if change == "language": dialog.language.setCurrentIndex(dialog.language.findData("Japanese"))
    dialog.draft_use.setChecked(True); dialog.draft_confirm.setChecked(True)
    dialog._accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "重新生成" in dialog.status.text()


@pytest.mark.parametrize("change", ["document_id", "revision", "complete"])
def test_foreign_stale_or_incomplete_draft_never_accepted(application, tmp_path, monkeypatch, change):
    dialog, draft = draft_dialog(application, tmp_path, monkeypatch)
    dialog._transcript_draft = None
    if change == "document_id": draft[change] = "other-document"
    elif change == "revision": draft[change] += 1
    else: draft[change] = False
    dialog._show_draft(draft)
    if change != "complete": assert dialog._transcript_draft is None
    dialog.draft_use.setChecked(True); dialog.draft_confirm.setChecked(True)
    dialog._accept()
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_cancelled_result_is_not_restored_as_successful_draft(application, tmp_path):
    from desktop_qt.local_model_tasks import model_tasks
    document = doc(); service = model_tasks()
    service.value = make_draft(document, tmp_path)
    service.error = "cancelled before completion"
    dialog = LocalAlignmentDialog(document)
    assert dialog._transcript_draft is None


def test_explicit_asr_job_captures_document_and_runs_after_dialog_close(application, tmp_path, monkeypatch):
    import threading
    from lyricsync import local_transcription as trans
    document = doc(); draft = make_draft(document, tmp_path)
    initial = document.snapshot(); entered = threading.Event(); done = threading.Event(); seen = []
    dialog = LocalAlignmentDialog(document, default_audio=draft["source_path"])
    monkeypatch.setattr(trans, "validate_transcription_options", lambda v: dict(v))
    def generate(detached, options, cancel, progress):
        seen.append((detached, options)); entered.set(); progress(50,"synthetic ASR")
        while not done.is_set():
            if cancel(): raise InterruptedError("unexpected cancel")
            time.sleep(.005)
        return draft
    monkeypatch.setattr(trans, "generate_transcript_draft", generate)
    dialog._generate_asr()
    wait_for(application, entered.is_set)
    task = dialog._task
    dialog.reject()
    assert task.isRunning() and not task.cancelled.is_set()
    assert seen[0][0] is not document and seen[0][0].snapshot() == initial
    done.set(); wait_for(application, lambda: dialog._task is None)
    reopened = LocalAlignmentDialog(document, default_audio=draft["source_path"])
    assert reopened._transcript_draft["id"] == draft["id"]
    assert not reopened.draft_confirm.isChecked()
    assert document.snapshot() == initial


def test_preview_waits_for_load_maps_offset_and_stops_on_close(application, tmp_path, monkeypatch):
    import desktop_qt.media as transport  # import real Qt enums before replacing the player with a fake
    import PySide6.QtMultimedia as media
    from PySide6.QtCore import QUrl
    dialog, draft = draft_dialog(application, tmp_path, monkeypatch)
    class Signal:
        def connect(self, slot, *args): pass
    class Player:
        MediaStatus = media.QMediaPlayer.MediaStatus
        def __init__(self, parent):
            self.positionChanged = Signal(); self.errorOccurred = Signal(); self.mediaStatusChanged = Signal()
            self.url = QUrl(); self.calls = []; self.current_position=0
        def setAudioOutput(self, out): pass
        def setVideoSink(self, sink): self.sink=sink
        def mediaStatus(self): return self.MediaStatus.LoadedMedia
        def source(self): return self.url
        def setSource(self, url): self.url = url; self.calls.append(("source", url))
        def setPosition(self, value): self.current_position=value; self.calls.append(("position", value))
        def position(self): return self.current_position
        def play(self): self.calls.append(("play",))
        def stop(self): self.calls.append(("stop",))
    class Output:
        def __init__(self, parent): pass
        def setVolume(self, value): pass
    monkeypatch.setattr(media,"QMediaPlayer",Player); monkeypatch.setattr(media,"QAudioOutput",Output)
    # This unit test supplies a Python fake; real Qt thread/GIL behavior is tested separately.
    monkeypatch.setattr(transport,'stop_media_player',lambda player:player.stop())
    dialog._listen_draft(False); player=dialog._preview_player
    assert ("play",) not in player.calls and dialog._preview_seek_ms == 12000
    dialog._preview_loaded(Player.MediaStatus.LoadedMedia)
    assert player.calls[-2:] == [("position",12000),("play",)]
    assert dialog._preview_end_ms == 17000
    player.setPosition(17000)
    dialog._preview_position(17000)
    assert player.calls[-1] == ("stop",)
    dialog._listen_draft(True)
    assert dialog._preview_seek_ms == 0 and dialog._preview_end_ms == 5000
    dialog._preview_loaded(Player.MediaStatus.LoadedMedia)
    assert player.calls[-2:] == [("position",0),("play",)]
    dialog._preview_position(17000)  # stale queued tick from the original source
    assert player.calls[-1] == ("play",)
    dialog.reject()
    assert player.calls[-1] == ("stop",) and dialog._preview_seek_ms is None
    # Dispose this fake-backed dialog before monkeypatch restores the real native helper.
    from PySide6.QtCore import QCoreApplication,QEvent
    dialog.deleteLater();QCoreApplication.sendPostedEvents(dialog,QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("flow", ["idle", "drip"])
def test_application_quit_cancels_actual_localhost_download_body(application, monkeypatch, tmp_path, flow):
    import hashlib, threading, urllib.request
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from lyricsync import local_models as lm
    from desktop_qt.local_model_tasks import model_tasks
    entered, release = threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Length","64"); self.end_headers()
            self.wfile.write(b"x"); self.wfile.flush(); entered.set()
            for _ in range(63):
                if release.wait(.04 if flow == "drip" else 5): break
                try: self.wfile.write(b"x"); self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): break
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(("127.0.0.1",0), Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    class Response:
        def __init__(self, response): self.response=response
        def __getattr__(self, name): return getattr(self.response,name)
        def geturl(self): return "https://huggingface.co/synthetic/review/resolve/fixed/model.bin"
        def __enter__(self): return self
        def __exit__(self,*args): self.response.close()
    class LocalOnly:
        def open(self,request,timeout):
            return Response(urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/",timeout=timeout))
    spec=lm.ModelSpec("synthetic-qt-review","synthetic/review","fixed","synthetic",
        (lm.ModelFile("model.bin",64,hashlib.sha256(b"x"*64).hexdigest()),))
    monkeypatch.setitem(lm.CATALOG,spec.id,spec)
    # Production network timeout remains unchanged. No weights or outside hosts.
    service=model_tasks()
    try:
        service.start(lambda cancel, progress: lm.ModelManager(tmp_path).download(spec.id,
            cancel=cancel,progress=progress,opener=LocalOnly()), {}, "download")
        wait_for(application,entered.is_set)
        time.sleep(.08)
        started=time.monotonic()
        application.aboutToQuit.emit()
        assert time.monotonic()-started < 3
        assert not service.task.isRunning()
        wait_for(application,lambda: service.task is None)
        assert not lm.ModelManager(tmp_path).status(spec.id)["present"]
    finally:
        release.set()
        service.shutdown()
        server.shutdown(); server.server_close()


@pytest.mark.parametrize("size", [(760,600),(790,720),(980,760)])
def test_all_tabs_keep_fixed_actions_inside_window(application, size):
    from PySide6.QtCore import QPoint
    dialog=LocalAlignmentDialog(doc())
    dialog.resize(*size); dialog.show()
    for tab in range(dialog.tabs.count()):
        dialog.tabs.setCurrentIndex(tab)
        for _ in range(3): application.processEvents()
        assert dialog.height() <= size[1]
        for button in dialog.buttons.buttons():
            assert dialog.rect().contains(button.mapTo(dialog,QPoint(0,0)))
            assert dialog.rect().contains(button.mapTo(dialog,button.rect().bottomRight()))
    dialog.reject()


def test_source_import_is_original_not_translation_and_4a_uses_its_text(application, monkeypatch):
    from lyricsync.local_alignment import request_rows
    from desktop_qt import local_alignment_dialog as module
    document=ProjectDocument(Project(mappings=[Mapping("source",[],text_override="<b>Original</b>",text_role="source_import")]))
    dialog=LocalAlignmentDialog(document, initial_options={"model_id": "qwen3-fa-hf"})
    assert dialog.text_source.currentData()=="external"
    dialog.text_source.setCurrentIndex(dialog.text_source.findData("main"))
    assert dialog.source_preview.toPlainText()==""
    dialog.main_source_confirm.setChecked(True)
    assert "<b>Original</b>" in dialog.source_preview.toPlainText()
    assert "共 1 句" == dialog.lyric_count.text()
    assert request_rows(document,["source"],"main",main_source_confirmed=True)==[{"id":"source","text":"<b>Original</b>"}]
    seen=[]
    def validate_source(doc, value):
        seen.append(request_rows(doc, value["sentence_ids"], value["text_source"], main_source_confirmed=value["main_source_confirmed"]))
        return dict(value, duration_seconds=5)
    monkeypatch.setattr(module, "validate_options", validate_source)
    monkeypatch.setattr(module.ModelManager, "status", lambda self, model: {"present": True})
    dialog.confirm.setChecked(True); dialog._accept()
    assert dialog.result()==QDialog.DialogCode.Accepted and seen[0][0]["text"]=="<b>Original</b>"


def test_dynamic_errors_and_draft_content_are_plain_text(application, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    dialog,draft=draft_dialog(application,tmp_path,monkeypatch)
    literal='<b>literal</b><img src="file:///not-opened">'
    draft["text"]=literal;draft["warning"]=literal
    dialog._show_draft(draft);dialog._outcome(None,literal)
    assert dialog.status.text()==literal and dialog.status.textFormat()==Qt.TextFormat.PlainText
    assert dialog.draft_info.textFormat()==Qt.TextFormat.PlainText
    assert dialog.source_role.textFormat()==Qt.TextFormat.PlainText
    assert dialog.draft_text.toPlainText()==literal


def test_action_confirmation_is_plain_text_and_defaults_to_no(application, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox
    dialog=LocalAlignmentDialog(doc(),setup_only=True)
    seen=[]
    def inspect(box):
        seen.append((box.text(),box.textFormat(),box.standardButton(box.defaultButton())))
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox,"exec",inspect)
    assert dialog._question("synthetic","<b>literal path/error</b>")==QMessageBox.StandardButton.No
    assert seen==[("<b>literal path/error</b>",Qt.TextFormat.PlainText,QMessageBox.StandardButton.No)]

def test_prepare_disclosure_decline_never_starts(application, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    dialog=LocalAlignmentDialog(doc(), setup_only=True)
    monkeypatch.setattr(dialog,'_question',lambda *a:QMessageBox.StandardButton.No)
    monkeypatch.setattr(dialog,'_start_task',lambda *a,**k:pytest.fail('declined preparation'))
    dialog._prepare_all()
    dialog.reject()


def test_completed_preparation_recovers_environment_only_for_same_cache(application):
    from desktop_qt.local_model_tasks import model_tasks
    from lyricsync.local_models import default_cache
    service=model_tasks()
    service.context={'cache_root':str(default_cache()),'runtime_python':'prepared-python'}
    service.value={'runtime_python':'prepared-python'}
    dialog=LocalAlignmentDialog(doc(),setup_only=True)
    assert dialog.runtime.text()=='prepared-python'
    dialog.reject()
    dialog=LocalAlignmentDialog(doc(),setup_only=True,initial_options={'runtime_python':'user-choice'})
    assert dialog.runtime.text()=='user-choice'
    dialog.reject()
    dialog=LocalAlignmentDialog(doc(),setup_only=True,initial_options={'cache_root':'other-cache'})
    assert dialog.runtime.text()!='prepared-python'
    dialog.reject()


def test_failed_preparation_keeps_environment_and_never_claims_success(application):
    dialog=LocalAlignmentDialog(doc(),setup_only=True)
    dialog._outcome({'runtime_python':'created-python'},'model failed')
    assert dialog.runtime.text()=='created-python'
    assert dialog.status.text()=='model failed'
    dialog.reject()


def test_idle_management_controls_hide_progress_and_task_actions(application):
    dialog=LocalAlignmentDialog(doc(),setup_only=True)
    assert dialog.progress.isHidden()
    assert dialog.pause_task.isHidden() and not dialog.pause_task.isEnabled()
    assert dialog.cancel_task.isHidden() and not dialog.cancel_task.isEnabled()
    dialog.reject()

@pytest.mark.parametrize('font_points', [9,10])
@pytest.mark.parametrize('size,columns', [((790,720),4),((540,600),2),((420,600),2)])
def test_model_responsive_summary_and_scrollable_pages(application,size,columns,font_points):
    from PySide6.QtWidgets import QScrollArea
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QFont
    original_font=QFont(application.font())
    scoped_font=QFont(original_font);scoped_font.setPointSize(font_points)
    application.setFont(scoped_font)
    dialog=None
    try:
        dialog=LocalAlignmentDialog(doc(),setup_only=True)
        dialog.resize(*size);dialog.show()
        for _ in range(5):application.processEvents()
        assert dialog.readiness.isHidden() # dev8 management does not show business status cards
        assert not dialog.advanced_body.isVisible()
        dialog.advanced_toggle.click()
        assert dialog.advanced_body.isVisible()
        for tab in range(dialog.tabs.count()):
            dialog.tabs.setCurrentIndex(tab)
            for _ in range(5):application.processEvents()
            assert dialog.width()<=size[0] and dialog.height()<=size[1]
            for scroll in dialog.findChildren(QScrollArea):
                if scroll.isVisible():assert scroll.horizontalScrollBar().maximum()==0
            assert dialog.buttons.button(QDialogButtonBox.Cancel).isHidden()
            button=dialog.buttons.button(QDialogButtonBox.Ok)
            assert button.isVisible()
            assert dialog.rect().contains(button.mapTo(dialog,button.rect().bottomRight()))
        assert dialog.reading_advanced_note.wordWrap()
        assert "不改变模型/CUDA 环境" in dialog.reading_advanced_note.text()
    finally:
        if dialog is not None:dialog.reject()
        application.setFont(original_font)


def test_readiness_is_not_claimed_from_unconfigured_paths(application):
    dialog=LocalAlignmentDialog(doc(),setup_only=True)
    assert dialog.readiness.isHidden()
    assert '通过' not in dialog.runtime_status.text()
    assert all('已就绪' not in label.text() for label in dialog.model_labels.values())
    assert dialog.confirm.isHidden()
    assert not dialog.confirm.isChecked()
    dialog.reject()
