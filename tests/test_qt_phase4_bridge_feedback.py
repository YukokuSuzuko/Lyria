"""Phase-four bridge regressions; offline fixtures, never model or paid API calls."""
import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
os.environ.setdefault("QT_QUICK_BACKEND","software")
import time, threading
from copy import deepcopy
import pytest
from PySide6.QtWidgets import QApplication
from desktop_qt.controller import Bridge
from lyricsync.frames import FrameIndex
from lyricsync.model import Mapping, Lyric, Visual, Project, Translation
from lyricsync.candidate_tracks import capture_source, candidate_from_project

@pytest.fixture
def bridge(tmp_path):
    app=QApplication.instance() or QApplication([])
    obj=None
    try:
        obj=Bridge(storage_dir=tmp_path/"storage")
        obj.document.project.video={"duration":4.}
        obj.document.project.mappings=[Mapping("a",[],text_override="chorus",start=.2,end=1.),Mapping("u",[],text_override="untimed"),Mapping("v",[],text_override="untimed again"),Mapping("b",[],text_override="chorus",start=1.2,end=2.)]
        obj.media_controller.index=FrameIndex([n*.1 for n in range(40)],4.)
        obj.selected="b";obj._changed()
        yield obj
    finally:
        if obj is not None:
            obj.shutdown();obj.wait_for_shutdown(app)
            obj.media_controller.worker.thread.join(10)
            assert not obj.media_controller.worker.thread.is_alive()

def wait(predicate):
    app=QApplication.instance();end=time.monotonic()+8
    while not predicate() and time.monotonic()<end:
        app.processEvents();time.sleep(.003)
    assert predicate()

def setup_ocr(bridge,tmp_path):
    media=tmp_path/"fixture.mp4";media.write_bytes(b"not real media; pipeline mocked")
    bridge.document.project.inputs["video"]=str(media)
    bridge.document.project.settings["roi"]=[0,0,10,10]
    bridge.document.project.video.update(width=320,height=180)

def test_previous_timed_skips_multiple_without_changing_adjacent_semantics(bridge):
    selection=bridge.state["selection"]
    assert selection["previous"]["id"]=="v"
    assert selection["previousTimed"]["id"]=="a"
    before=deepcopy(bridge.document.project.to_dict())
    bridge.selected="a";bridge._refresh()
    assert bridge.state["selection"]["previousTimed"]=={}
    assert bridge.document.project.to_dict()==before

def test_candidate_evidence_uses_candidate_tables_not_main_colliding_ids(bridge):
    project=bridge.document.project
    project.lyrics=[Lyric("l","main original",None,None)]
    project.visuals=[Visual("v","main OCR",.2,1.,[0,0,10,10],.9,2)]
    project.mappings[0].lyric_ids=["l"];project.mappings[0].visual_ids=["v"]
    generated=Project.from_dict(project.to_dict())
    generated.lyrics[0].text="candidate original";generated.visuals[0].text="candidate OCR"
    record=candidate_from_project(capture_source(deepcopy(bridge.document),"ocr",bridge.media_controller.index),generated)
    before=deepcopy(project.to_dict());bridge._candidate_install(record);bridge.switchCandidateSource("ocr")
    row=next(row for row in bridge.model.items if row["id"]=="a")
    assert row["source"]=="candidate original" and row["ocr"]=="candidate OCR"
    assert bridge.document.project.to_dict()==before
    bridge.switchCandidateSource("main")
    row=next(row for row in bridge.model.items if row["id"]=="a")
    assert row["source"]=="main original" and row["ocr"]=="main OCR"

def test_unbound_candidate_evidence_survives_selection_and_rebuild(bridge):
    generated=Project(lyrics=[Lyric("lx","original",None,None)],visuals=[Visual("vx","OCR result",.5,1.,[0,0,10,10],.9,1)],mappings=[Mapping("new",[],lyric_ids=["lx"],visual_ids=["vx"],text_override="new text",start=.5,end=1.)])
    record=candidate_from_project(capture_source(deepcopy(bridge.document),"ocr",bridge.media_controller.index),generated)
    bridge._candidate_install(record);bridge.switchCandidateSource("ocr")
    assert bridge.command("timeline.select",{"id":"new"})
    assert bridge.state["selection"]["current"]["source"]=="original"
    assert bridge.state["selection"]["current"]["ocr"]=="OCR result"
    assert not bridge.command("row.edit",{"id":"new","text":"bad"})

def test_ocr_progress_stays_visible_after_unrelated_foreground_failure(bridge,tmp_path,monkeypatch):
    import lyricsync.pipeline as pipeline
    setup_ocr(bridge,tmp_path);entered=threading.Event();release=threading.Event()
    def run(project,*args,**kwargs):
        kwargs["progress"](.42,"local OCR 42%");entered.set();assert release.wait(5)
    monkeypatch.setattr(pipeline,"run",run)
    assert bridge.command("ocr.start",{});wait(entered.is_set)
    wait(lambda:bridge.state["candidateTask"]["progress"]==.42)
    def fail(cancel,progress):raise TimeoutError("offline AI timeout fixture")
    bridge._start_job("AI fixture",fail,lambda result,error:bridge._error(str(error)))
    wait(lambda:not bridge.state["task"]["running"])
    assert bridge.state["candidateTask"]["running"]
    assert bridge.state["candidateTask"]["label"]=="local OCR 42%"
    assert not bridge.command("ocr.start",{})
    release.set();wait(lambda:not bridge.state["candidateTask"]["running"])
    assert bridge._candidate_job is None

def test_cancel_candidate_task_and_keep_main_unchanged(bridge,tmp_path,monkeypatch):
    import lyricsync.pipeline as pipeline
    setup_ocr(bridge,tmp_path);entered=threading.Event()
    def run(project,*args,**kwargs):
        entered.set()
        while not kwargs["cancel"]():time.sleep(.003)
        raise InterruptedError("cancel fixture")
    monkeypatch.setattr(pipeline,"run",run)
    before=bridge.document.project.to_dict()
    assert bridge.command("ocr.start",{});wait(entered.is_set)
    bridge.cancelCandidateJob()
    assert bridge.state["candidateTask"]["cancelling"]
    wait(lambda:bridge._candidate_job is None and not bridge._task['running'] and not getattr(bridge,'_ocr_settings_pending',False))
    assert not bridge.state["candidateTask"]["running"]
    assert bridge.document.project.to_dict()==before

def test_candidate_failure_clears_running_and_allows_retry(bridge,tmp_path,monkeypatch):
    import lyricsync.pipeline as pipeline
    setup_ocr(bridge,tmp_path)
    def fail(*a,**k):raise RuntimeError("OCR fixture error")
    monkeypatch.setattr(pipeline,"run",fail)
    assert bridge.command("ocr.start",{});wait(lambda:bridge._candidate_job is None and not bridge._task['running'] and not getattr(bridge,'_ocr_settings_pending',False))
    assert not bridge.state["candidateRunning"]
    assert bridge.state["candidateTask"]["resultStatus"]=="failed"
    assert "本轮识别未能生成" in bridge.state["candidateTask"]["label"]
    assert "OCR fixture error" in bridge.state["error"]
    assert bridge.command("ocr.start",{});wait(lambda:bridge._candidate_job is None and not bridge._task['running'] and not getattr(bridge,'_ocr_settings_pending',False))

def test_new_project_cancels_old_candidate_and_ignores_its_completion(bridge,tmp_path,monkeypatch):
    import lyricsync.pipeline as pipeline
    setup_ocr(bridge,tmp_path);entered=threading.Event();cancelled=threading.Event()
    def run(project,*a,**kwargs):
        entered.set()
        while not kwargs["cancel"]():time.sleep(.003)
        cancelled.set();raise InterruptedError("old document")
    monkeypatch.setattr(pipeline,"run",run)
    assert bridge.command("ocr.start",{});wait(entered.is_set)
    bridge._new();wait(cancelled.is_set)
    wait(lambda:not bridge.jobs.active)
    assert not bridge.document.source_candidates and not bridge.state["candidateRunning"]

def test_missing_roi_never_starts_worker(bridge,tmp_path,monkeypatch):
    setup_ocr(bridge,tmp_path);bridge.document.project.settings["roi"]=None
    monkeypatch.setattr(bridge,"_start_job",lambda *a,**k:pytest.fail("must not start"))
    assert not bridge.command("ocr.start",{})
    assert "字幕识别区域" in bridge.state["error"]

@pytest.mark.parametrize("mode",["ocr","audio","edit","separate"])
def test_entry_switch_never_infers_and_detaches_only_explicitly_confirmed_axis(bridge,tmp_path,monkeypatch,mode):
    project=bridge.document.project
    source=tmp_path/"entry-source.txt";source.write_text("retained original",encoding="utf-8")
    project.inputs["ktv"]=str(source)
    project.lyrics=[Lyric("original","retained original",None,None)]
    project.translations=[Translation("translation","retained translation")]
    project.visuals=[Visual("ocr","retained OCR",.2,1.,[0,0,10,10],.9,2)]
    project.settings["frame_markers"]=[3]
    project.mappings[0].confirmed=True;project.mappings[0].human_edited=True
    bridge._changed()
    before=deepcopy(project.to_dict());snapshot=bridge.document.snapshot();identity=bridge.document.id
    history=deepcopy(bridge.history.undo_stack)
    def forbidden(*args,**kwargs):pytest.fail("entry selection must not start inference")
    monkeypatch.setattr(bridge,"_start_job",forbidden)
    monkeypatch.setattr(bridge,"_start_candidate_job",forbidden)
    monkeypatch.setattr("desktop_qt.local_model_tasks.ModelTasks.start",forbidden)
    if mode=="ocr":
        monkeypatch.setattr("desktop_qt.dialogs.confirm_mode_switch",forbidden)
        assert bridge.command("editor.start",{"mode":mode})
        assert bridge.document.snapshot()==snapshot and bridge.history.undo_stack==history
    else:
        answers=iter(["cancel","detach"]);prompts=[]
        def confirm():
            # Both cancelling and confirming observe the untouched old axis.
            assert bridge.document.snapshot()==snapshot and bridge._entry_mode=="ocr"
            prompts.append(mode);return next(answers)
        monkeypatch.setattr("desktop_qt.dialogs.choose_asset_clear",confirm)
        assert not bridge.command("editor.start",{"mode":mode})
        assert bridge.document.snapshot()==snapshot and bridge.history.undo_stack==history
        assert bridge._entry_mode=="ocr" and project.mappings[0].confirmed
        assert bridge.command("editor.start",{"mode":mode})
        assert prompts==[mode,mode]
        assert bridge.document.project.to_dict()["mappings"]==before["mappings"]
        assert bridge.document.project.inputs==before["inputs"]
        assert len(bridge.history.undo_stack)==len(history)+1
    assert bridge.state["entryMode"]==("ocr" if mode=="ocr" else "edit") and bridge.document.id==identity
    assert bridge.stage==("import" if mode=="ocr" else "review")
    assert source.read_text(encoding="utf-8")=="retained original"
    assert not bridge.jobs.active and bridge._candidate_job is None
    assert not bridge.state["task"]["running"] and not bridge.state["candidateTask"]["running"]

def test_vocal_real_and_fixture_status_are_distinct(bridge):
    capture=capture_source(deepcopy(bridge.document),"vocal",bridge.media_controller.index)
    generated=Project.from_dict(bridge.document.project.to_dict())
    generated.mappings[0].reasons=["local alignment; verify endings"]
    bridge._candidate_install(candidate_from_project(capture,generated));bridge.switchCandidateSource("vocal")
    assert "模型未接入" not in bridge.state["candidateStatus"]
    assert "测试样例数据" not in bridge.state["candidateStatus"]
    generated.mappings[0].reasons=["显式测试 fixture，非真实模型识别"]
    bridge._candidate_install(candidate_from_project(capture,generated))
    # Normal recognition returns to the editable main axis. Inspect the retained
    # private candidate view explicitly when testing its fixture provenance.
    assert bridge.state["candidateStatus"] == "主时间轴"
    bridge.switchCandidateSource("vocal")
    assert "测试样例数据" in bridge.state["candidateStatus"]


def test_spectrum_pixel_centres_interpolate_vfr_and_extrapolate_padding(bridge):
    bridge.media_controller.index=FrameIndex([0,.04,.12,.17],.3)
    assert bridge.spectrumColumnTimes(1.,4.,4)==pytest.approx([.02,.08,.145,.235])
    assert bridge.spectrumColumnTimes(0.,1.,1)==pytest.approx([-.02])
    assert bridge.spectrumColumnTimes(5.,1.,1)==pytest.approx([.365])
    assert bridge.spectrumColumnTimes(1.,float('nan'),3)==[]
    assert bridge.spectrumColumnTimes(1.,2.,0)==[]
    assert bridge.spectrumColumnTimes(1.,2.,40000)==[]

def test_close_warns_about_candidate_task_even_when_main_clean(bridge):
    bridge.document.mark_saved(bridge.document.revision)
    bridge.document.mark_exported(bridge.document.revision)
    bridge._candidate_job='pending-fixture'
    assert bridge._has_close_reasons()
    bridge._show_close()
    assert bridge._close_dialog['taskRunning']
    bridge._candidate_job=None


def test_vocal_worker_percent_is_normalized_and_project_stays_detached(bridge,monkeypatch):
    from PySide6.QtWidgets import QDialog
    import desktop_qt.local_alignment_dialog as dialogs
    import lyricsync.local_alignment as alignment
    entered=threading.Event();release=threading.Event()
    class Dialog:
        def __init__(self,document,**kwargs):
            assert document is not bridge.document
        def exec(self):return QDialog.DialogCode.Accepted
        def options(self):return {"fixture":True}
    def generate(document,index,*,options,cancel,progress):
        assert document is not bridge.document
        document.project.mappings[0].text_override="detached only"
        progress(42,"alignment 42%");entered.set();assert release.wait(5)
        raise InterruptedError("test ended without inference")
    monkeypatch.setattr(dialogs,"LocalAlignmentDialog",Dialog)
    monkeypatch.setattr(alignment,"generate_vocal_candidate",generate)
    before=deepcopy(bridge.document.project.to_dict())
    try:
        assert bridge.command("vocal.start",{})
        wait(entered.is_set)
        wait(lambda:bridge.state["candidateTask"]["progress"]==.42)
        assert bridge.state["candidateTask"]["label"]=="alignment 42%"
        assert bridge.document.project.to_dict()==before
    finally:
        release.set();wait(lambda:bridge._candidate_job is None and not bridge._task['running'] and not getattr(bridge,'_ocr_settings_pending',False))
    assert not bridge.document.source_candidates


def test_model_setup_roundtrip_persists_only_application_preferences(bridge,monkeypatch):
    from PySide6.QtWidgets import QDialog
    import desktop_qt.local_alignment_dialog as dialogs
    from desktop_qt import local_alignment_settings as settings
    captured=[]
    preferences=dict(runtime_python="selected-runtime",cache_root="selected-cache",device="cpu",language="English",download_source="modelscope",model_id="qwen3-fa-hf")
    class Dialog:
        def __init__(self,document,**kwargs):captured.append(kwargs)
        def exec(self):return QDialog.DialogCode.Accepted if len(captured)==1 else QDialog.DialogCode.Rejected
        def options(self):return dict(preferences,audio_path="PRIVATE_AUDIO",sentence_ids=["a"],offset_seconds=23,duration_seconds=5,api_key="PRIVATE_KEY")
    monkeypatch.setattr(dialogs,"LocalAlignmentDialog",Dialog)
    monkeypatch.setattr(bridge,"_start_candidate_job",lambda *a,**k:pytest.fail("setup/cancel never infer"))
    before=deepcopy(bridge.document.project.to_dict())
    assert bridge.command("vocal.configure",{})
    assert settings.load(bridge.storage_dir)==preferences
    assert bridge.command("vocal.start",{})
    assert captured[1]["initial_options"]==preferences
    assert "audio_path" not in captured[1]["initial_options"]
    assert bridge.document.project.to_dict()==before
    saved=(bridge.storage_dir/settings.NAME).read_text(encoding="utf-8")
    assert "PRIVATE" not in saved


def test_model_settings_corruption_and_unexpected_keys_are_ignored(tmp_path):
    from desktop_qt import local_alignment_settings as settings
    assert settings.load(tmp_path)=={}
    (tmp_path/settings.NAME).write_text("broken",encoding="utf-8")
    assert settings.load(tmp_path)=={}
    assert settings.save(tmp_path,{"runtime_python":"valid","device":123,"api_key":"secret","audio_path":"media","cache_root":"bad\0path"})=={"runtime_python":"valid"}


def test_api_presets_and_failed_model_listing_keep_manual_selection(bridge,monkeypatch):
    from desktop_qt.dialogs import ApiSettingsDialog
    from lyricsync import ai_config
    assert [item["id"] for item in ai_config.PROVIDERS]==["deepseek","custom"]
    dialog=ApiSettingsDialog(bridge)
    try:
        assert [dialog.provider.itemData(i) for i in range(dialog.provider.count())]==["deepseek","custom"]
        assert dialog.model.isEditable()
        dialog.model.setCurrentText("manual-current-model")
        bridge.api_state={"models":[],"status":"model listing failed; offline fixture"}
        dialog.refresh_status()
        assert dialog.model.currentText()=="manual-current-model"
        dialog.model.setEditText("manual-after-failure")
        assert dialog.values()["api_model"]=="manual-after-failure"
        bridge.api_state={"models":["server-model-a","server-model-b"],"status":"fixture list"}
        dialog.refresh_status()
        assert dialog.model.currentText()=="manual-after-failure"
    finally:dialog.reject()


@pytest.fixture(autouse=True)
def accepted_settings_for_downstream_ocr_contracts(monkeypatch):
    # These older suites exercise work AFTER a user's settings acceptance.
    # Actual modal acceptance/cancel/reentrancy is covered in dev8 entry tests.
    monkeypatch.setattr('desktop_qt.dialogs.ocr_settings', lambda *args, **kwargs: True)
