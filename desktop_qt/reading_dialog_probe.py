"""Opt-in packaged Qt dialog + actual bundled Sudachi end-to-end probe.

No worker mocks, source-Python fallback, acoustic model, user project or network.
Programmatic Qt controls, not physical mouse/IME acceptance.
"""
from copy import deepcopy
from pathlib import Path
import ctypes
import hashlib
import json
import os
import sys
import time
from PySide6.QtCore import QObject, QTimer

QA_OFFLINE_ENV = "LYRIA_QA_READING_OFFLINE"
ROWS = [
    {"id":"qa-reading-1","text":"朝の空を見上げる"},
    {"id":"qa-reading-2","text":"小さな歌を重ねよう"},
    {"id":"qa-reading-3","text":"明日へ一緒に歩こう"},
    {"id":"qa-reading-4","text":"・・・"},
    {"id":"qa-reading-5","text":"(^o^)"},
]
REFERENCE_ROWS = [dict(row, id="reference-" + row["id"]) for row in ROWS]
_NETWORK_GUARD = False

def install_offline_guard():
    global _NETWORK_GUARD
    if _NETWORK_GUARD:return
    def audit(event,args):
        if event in ("socket.connect","socket.connect_ex","socket.getaddrinfo","urllib.Request"):
            raise RuntimeError("Reading QA forbids network access")
    sys.addaudithook(audit)
    _NETWORK_GUARD = True

def validate_private_packaged_environment():
    if not getattr(sys,"frozen",False) or sys.platform!="win32" or Path(sys.executable).suffix.lower()!=".exe":
        raise RuntimeError("Reading dialog QA requires the actual packaged Windows EXE")
    expected=os.environ.get("LYRIA_EXPECTED_TEST_DESKTOP","")
    if not expected.startswith("LyriaTest_"):
        raise RuntimeError("Reading dialog QA requires the existing private-desktop launcher")
    from ctypes import wintypes as W
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    user=ctypes.WinDLL("user32",use_last_error=True)
    kernel.GetCurrentThreadId.restype=W.DWORD
    user.GetThreadDesktop.argtypes=[W.DWORD];user.GetThreadDesktop.restype=W.HANDLE
    user.GetUserObjectInformationW.argtypes=[W.HANDLE,ctypes.c_int,ctypes.c_void_p,W.DWORD,ctypes.POINTER(W.DWORD)]
    user.GetUserObjectInformationW.restype=W.BOOL
    handle=user.GetThreadDesktop(kernel.GetCurrentThreadId());needed=W.DWORD()
    user.GetUserObjectInformationW(handle,2,None,0,ctypes.byref(needed))
    if not needed.value:raise ctypes.WinError(ctypes.get_last_error())
    name=ctypes.create_unicode_buffer(needed.value//ctypes.sizeof(ctypes.c_wchar)+1)
    if not user.GetUserObjectInformationW(handle,2,name,ctypes.sizeof(name),ctypes.byref(needed)):
        raise ctypes.WinError(ctypes.get_last_error())
    if name.value!=expected:raise RuntimeError("Private desktop mismatch; refusing GUI QA")
    return expected

def prepare_report(path):
    validate_private_packaged_environment()
    report=Path(path).expanduser()
    if not report.is_absolute() or report.suffix.lower()!=".json":
        raise ValueError("Reading QA requires an absolute .json report in a new directory")
    for node in (report.parent,*report.parent.parents):
        if node.exists() and (node.is_symlink() or getattr(node.stat(),"st_file_attributes",0)&0x400):
            raise ValueError("Reading QA output cannot use reparse points")
    report=report.resolve()
    report.parent.mkdir(parents=True,exist_ok=False)
    os.environ[QA_OFFLINE_ENV]="1"
    os.environ.update(HF_HUB_OFFLINE="1",TRANSFORMERS_OFFLINE="1",HF_HUB_DISABLE_TELEMETRY="1")
    install_offline_guard()
    return report

def validate_rows(rows):
    if not isinstance(rows,list) or [(r.get("id"),r.get("text")) for r in rows]!=[(r["id"],r["text"]) for r in REFERENCE_ROWS]:
        raise AssertionError("Generated rows do not preserve the entire synthetic song")
    if not all(isinstance(r.get("reading"),str) and r["reading"].strip() for r in rows[:3]):
        raise AssertionError("Japanese rows did not receive readings")
    if any(i.get("hard_error") for r in rows for i in r.get("reading_issues",[])):
        raise AssertionError("Reading worker reported a hard error")
    if any(r.get("reading")!="" or r.get("alignment_skip")!="nonvocal_symbols" for r in rows[3:]):
        raise AssertionError("Pure symbols were not skipped safely")

class ReadingDialogProbe(QObject):
    def __init__(self,app,bridge,window,report):
        super().__init__(bridge)
        self.app,self.bridge,self.window=app,bridge,window
        self.report=Path(report);self.dialog=None;self.events=[];self.done=False
        self.timer=QTimer(self);self.timer.setInterval(25);self.timer.timeout.connect(self.tick)
        self.started=time.monotonic();self.deadline=self.started+90
        self.reading_clicked=False
        from .local_model_tasks import model_tasks
        self.service=model_tasks()
        if self.service.task is not None:raise RuntimeError("QA must own a fresh ModelTasks service")
        self.service.outcome.connect(self.outcome)

    def start(self):
        try:
            validate_private_packaged_environment()
            from lyricsync.bundled_reading import bundled_available,BUNDLED_RUNTIME
            if not bundled_available():raise RuntimeError("Actual packaged reading bundle is absent")
            from lyricsync.model import Project,Lyric
            from lyricsync.project_document import ProjectDocument
            from lyricsync.local_models import NEXTFIRE
            from .local_alignment_dialog import LocalAlignmentDialog
            document=ProjectDocument(Project(lyrics=[Lyric(r["id"],r["text"],None,None) for r in ROWS]))
            self.dialog=LocalAlignmentDialog(document,initial_options={
                "cache_root":str(self.report.parent/"model-cache"),"runtime_python":"",
                "reading_runtime_python":"","reading_tool":"sudachi","device":"cpu",
                "language":"Japanese","model_id":NEXTFIRE.id,"text_source":"reference"})
            self.dialog.show()
            if self.dialog.reading_runtime.text().strip() or self.dialog._reading_python()!=BUNDLED_RUNTIME:
                raise AssertionError("Dialog did not select the actual bundled sentinel")
            self.timer.start()
        except Exception as exc:self.finish(False,type(exc).__name__+": "+str(exc))

    def outcome(self,value,error):
        self.events.append({"action":self.service.action,"context":deepcopy(self.service.context),
                            "value":deepcopy(value),"error":str(error or "")})

    def tick(self):
        if self.done:return
        try:
            if time.monotonic()>self.deadline:raise TimeoutError("Reading dialog QA exceeded 90 seconds")
            from lyricsync.bundled_reading import BUNDLED_RUNTIME
            if any(e["action"] not in ("reading_probe","readings") for e in self.events):
                raise AssertionError("Unexpected non-dictionary task")
            if any(e["error"] for e in self.events):
                raise AssertionError(next(e["error"] for e in self.events if e["error"]))
            probe=next((e for e in self.events if e["action"]=="reading_probe"),None)
            if not probe:return
            result=probe["value"] or {};tool=result.get("reading_tool",{})
            if (not tool.get("ready") or not tool.get("bundled")
                    or result.get("reading_runtime_python")!=BUNDLED_RUNTIME
                    or probe["context"].get("effective_reading_python")!=BUNDLED_RUNTIME
                    or probe["context"].get("reading_runtime_python")):
                raise AssertionError("Actual bundled probe/context was not accepted")
            if not self.dialog._reading_ready.get("sudachi"):
                raise AssertionError("Dialog discarded successful probe")
            generated=next((e for e in self.events if e["action"]=="readings"),None)
            if self.service.task is not None:return
            if not generated:
                if not self.reading_clicked:
                    if not self.dialog.reading_button.isEnabled():raise AssertionError("Reading control remains disabled")
                    self.reading_clicked=True;self.dialog.reading_button.click()
                return
            validate_rows(generated["value"].get("rows"))
            if generated["context"].get("effective_reading_python")!=BUNDLED_RUNTIME:
                raise AssertionError("Generation did not use bundled runtime")
            current=self.dialog._current_readings()
            if [(r.get("id"),r.get("text"),r.get("reading","")) for r in current] != [
                    (r["id"],r["text"],r["reading"]) for r in generated["value"]["rows"]]:
                raise AssertionError("Worker readings were not populated into actual dialog controls")
            if not self.dialog.isVisible() or self.dialog.reading_table.rowCount()!=len(ROWS):
                raise AssertionError("Actual dialog/table is not displayed")
            self.finish(True)
        except Exception as exc:self.finish(False,type(exc).__name__+": "+str(exc))

    def finish(self,ok,error=""):
        if self.done:return
        self.done=True;self.timer.stop()
        screenshot=False;current=[]
        if self.dialog is not None:
            current=self.dialog._current_readings()
            screenshot=bool(self.dialog.grab().save(str(self.report.parent/"reading-dialog.png")))
            self.dialog.reject()  # No subsequent auto-generation during shutdown.
        if self.service.task is not None:self.service.stop("cancel")
        ok=bool(ok and screenshot and self.service.task is None)
        from lyricsync import __display_version__
        result={"kind":"packaged-real-reading-dialog","ok":ok,"error":error,
                "app_version":__display_version__,"frozen":bool(getattr(sys,"frozen",False)),
                "executable":str(Path(sys.executable).resolve()),"seconds":time.monotonic()-self.started,
                "private_desktop":os.environ.get("LYRIA_EXPECTED_TEST_DESKTOP"),
                "actual_modeltasks":True,"worker_mocked":False,"network_guard":_NETWORK_GUARD,
                "external_reading_path":"","screenshot_saved":screenshot,"events":self.events,
                "dialog_rows":current,"scope":"Real Qt dialog, ModelTasks, packaged EXE Sudachi subprocess and control refill; synthetic lyrics only",
                "not_verified":["physical mouse/IME","acoustic alignment","full-song user lyrics","clean Windows without D:"]}
        try:
            with Path(sys.executable).open("rb") as stream:result["exe_sha256"]=hashlib.file_digest(stream,"sha256").hexdigest()
            self.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        finally:self.app.exit(0 if ok else 1)