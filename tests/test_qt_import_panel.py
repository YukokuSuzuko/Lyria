"""Actual QML import/ROI/OCR route; synthetic silent media and offscreen only.

The file-picker return is controlled; OCR settings are accepted with real Qt input. Video
indexing, presentation, QML mouse dispatch, OCR and pipeline completion are real.
Both an empty-ROI scan and a full-line scan must complete without pipeline errors.
This is not native Windows input, GPU, audio or user-material acceptance.
"""
import json
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("OMP_THREAD_LIMIT", "2")

import av
from PIL import Image, ImageDraw, ImageFont
import pytest
from PySide6.QtCore import QObject, QPointF, Qt, QUrl, QTimer
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QDialog, QDialogButtonBox

from desktop_qt.controller import Bridge


ROOT = Path(__file__).resolve().parents[1]


def wait_for(app, predicate, *, timeout=10, detail="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.005)
    assert predicate(), "Timed out waiting for " + detail


def walk_items(item):
    yield item
    for child in item.childItems():
        yield from walk_items(child)


def button(window, text):
    matches = [item for item in walk_items(window.contentItem())
               if item.metaObject().indexOfProperty("down") >= 0
               and item.property("text") == text and item.isVisible()]
    assert len(matches) == 1, (text, len(matches))
    return matches[0]


def click(window, item):
    assert item.isEnabled() and item.isVisible()
    position = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(window, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, position.toPoint())


@pytest.fixture(scope="module")
def application():
    assert os.environ.get("QT_QPA_PLATFORM") == "offscreen"
    assert os.environ.get("QT_QUICK_BACKEND") == "software"
    QQuickStyle.setStyle("Basic")
    app = QApplication.instance() or QApplication([])
    assert QGuiApplication.platformName() == "offscreen"
    # Process-local registration works around this host's offscreen font lookup.
    # No system font installation or package-font redistribution occurs.
    font_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in ("msyh.ttc", "arial.ttf"):
        path = font_dir / name
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    return app


@pytest.fixture
def silent_text_video(tmp_path):
    path = tmp_path / "中文 日本語 silent OCR.mp4"
    # Use a known installed font on each supported test host.  The fixture is
    # Latin text, so an open-source Linux bold face is equivalent test input;
    # do not require or copy a proprietary Windows font into the cloud.
    font_candidates = [Path(os.environ.get("LYRIA_OCR_FONT", "")),
                       Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arialbd.ttf",
                       Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
                       Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf")]
    font_path = next((candidate for candidate in font_candidates if candidate.is_file()), None)
    assert font_path is not None, "Install a local test font or set LYRIA_OCR_FONT"
    frame = Image.new("RGB", (320, 180), "black")
    draw = ImageDraw.Draw(frame)
    font = ImageFont.truetype(str(font_path), 28)
    text = "HELLO WORLD"
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(((320 - (box[2] - box[0])) / 2, 75 - box[1]), text,
              font=font, fill="white")
    # A short static subtitle exercises actual OCR with little decode work.
    with av.open(str(path), "w") as container:
        stream = container.add_stream("libx264", rate=10)
        stream.width, stream.height = frame.size
        stream.pix_fmt = "yuv420p"
        stream.options = {"preset": "ultrafast", "crf": "18", "threads": "1"}
        for _ in range(20):
            for packet in stream.encode(av.VideoFrame.from_image(frame)):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


def test_qml_import_roi_boundaries_and_full_line_recognition(application, silent_text_video,
                                                            tmp_path, monkeypatch):
    bridge = Bridge(storage_dir=tmp_path / "recovery")
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(ROOT / "desktop_qt/qml/Main.qml")))
    assert engine.rootObjects(), "Actual Main.qml did not load"
    window = engine.rootObjects()[0]
    phases = {}
    picker_calls = []
    observed_jobs = []
    observed_errors = []
    bridge.message.connect(observed_errors.append)
    bridge.jobs.finished.connect(lambda identity, result, error:
                                observed_jobs.append((identity, error)))

    def picker(*args, **kwargs):
        picker_calls.append((args[1] if len(args) > 1 else "", kwargs))
        return silent_text_video.as_posix(), "Video"

    monkeypatch.setattr(QFileDialog, "getOpenFileName", picker)

    def evidence(phase):
        phases[phase] = {
            "error": bridge.state["error"],
            "notice": window.property("noticeText"),
            "ready": bridge.media["ready"],
            "displayed_frame": bridge.media["displayedFrame"],
            "task_running": bridge.state["task"]["running"],
            "task_label": bridge.state["task"]["label"],
        }

    try:
        wait_for(application, lambda: window.width() > 0, detail="QML layout")
        QTest.qWait(120)
        # Seed the exact old notice before importing. Successful subsequent
        # actions must clear bridge.error; the QML toast has its own lifetime.
        assert not bridge.command("roi.set", {"x": 0., "y": 0., "width": 1., "height": 1.})
        initial_error = bridge.state["error"]
        assert "请先载入视频" in initial_error
        evidence("before_import")
        click(window, button(window, "选择视频"))
        assert len(picker_calls) == 1, "The actual button must reach the file picker"
        wait_for(application, lambda: bridge.media["ready"] and bridge.media["displayedFrame"] > 0,
                 detail="real imported frame")
        assert bridge.document.project.video["width"] == 320
        assert bridge.document.project.video["height"] == 180
        assert not bridge.media_controller.metadata["has_audio"]
        assert not bridge.media["playing"]
        assert bridge.media_controller.sink.videoFrame().isValid()
        assert bridge.state["error"] == "", bridge.state["error"]
        evidence("after_import")
        # This test deliberately raised a pre-import error. Its clickable toast
        # can cover compact playback controls even after bridge.error clears.
        # Dismiss it as a user would, not by changing notification properties.
        toast=next(item for item in walk_items(window.contentItem()) if item.objectName()=="noticeToast")
        if toast.isVisible():
            click(window,toast)
            wait_for(application,lambda:not toast.isVisible(),detail="dismiss old notice before ROI input")
        evidence("after_notice_dismiss")

        preview = next(item for item in walk_items(window.contentItem())
                       if item.objectName() == "mainPreviewPanel")
        video = next(item for item in walk_items(preview)
                     if item.objectName() == "videoPreview")
        rect = video.property("contentRect")
        assert rect.width() > 0 and rect.height() > 0
        hints=[item for item in walk_items(window.contentItem())
               if item.objectName()=="ocrManualAiNotice"]
        assert len(hints)==1 and hints[0].isVisible()
        hint_text=hints[0].property("text")
        assert "仅运行本地 OCR" in hint_text and "不自动发送 AI 请求" in hint_text
        assert not button(window, "开始 OCR 识别").isEnabled()
        roi_hint=next(item for item in walk_items(preview) if item.objectName()=="roiSetupHint")
        assert roi_hint.isVisible() and roi_hint.property("text")=="尚未设置字幕选区。可自动检测硬字幕位置，也可直接在画面上手动框选。"

        def select_roi(top, bottom, expected):
            def control(name):
                return next(item for item in walk_items(window.contentItem())
                            if item.objectName()==name and item.isVisible())
            if bridge.state['regionCards']:
                click(window,control('clearOcrRegions'))
                wait_for(application,lambda:not bridge.state['regionCards'],detail='remove previous scan region')
            click(window,control('addOcrRegion'))
            wait_for(application,lambda:preview.property('roiSelecting'),detail='manual drawing from import card')
            assert preview.property("roiSelecting")
            # The import stage changes the QML layout asynchronously. Read the
            # current content rectangle after layout, not the first-frame one.
            QTest.qWait(100)
            rect = video.property("contentRect")
            start = video.mapToScene(QPointF(rect.x() + rect.width() * .08,
                                            rect.y() + rect.height() * top))
            end = video.mapToScene(QPointF(rect.x() + rect.width() * .92,
                                          rect.y() + rect.height() * bottom))
            QTest.mousePress(window, Qt.MouseButton.LeftButton,
                             Qt.KeyboardModifier.NoModifier, start.toPoint())
            for n in range(1, 5):
                QTest.mouseMove(window, (start + (end - start) * (n / 4)).toPoint(), 15)
            QTest.mouseRelease(window, Qt.MouseButton.LeftButton,
                               Qt.KeyboardModifier.NoModifier, end.toPoint())
            application.processEvents()
            roi = bridge.document.project.settings.get("roi")
            phases.setdefault("roi_geometry", []).append({
                "content_rect": [rect.x(), rect.y(), rect.width(), rect.height()],
                "video_size": [video.width(), video.height()],
                "start_scene": [start.x(), start.y()], "end_scene": [end.x(), end.y()],
                "actual_roi": roi, "expected_roi": expected})
            assert roi is not None, bridge.state["error"]
            assert roi == pytest.approx(expected, abs=2)
            assert not preview.property("roiSelecting")
            assert bridge.state["error"] == ""
            return list(roi)

        # Seed synthetic settings, then explicitly accept the actual start dialog.
        bridge.document.project.settings.update(engine="tesseract", language="eng",
                                                 sample_fps=1., ocr_threads=1,
                                                 api_enabled=False)
        bridge._changed()

        def run_actual_ocr(label):
            observed_errors.clear()
            started_at = time.monotonic()
            main_before=bridge.document.project.to_dict()
            candidate_count=len(bridge.document.source_candidates)
            stage_before=bridge.state["stage"]
            accepted=[];dialog_errors=[];started=[]
            def record_started():
                if bridge._candidate_job and not started:
                    started.append(bridge._candidate_job)
            bridge.stateChanged.connect(record_started)
            timer=QTimer();timer.setInterval(10)
            def accept_start_settings():
                dialog=QApplication.activeModalWidget()
                if not isinstance(dialog,QDialog) or dialog.windowTitle()!="OCR 识别设置":return
                timer.stop()
                try:
                    assert bridge._candidate_job is None
                    box=dialog.findChild(QDialogButtonBox)
                    start=box.button(QDialogButtonBox.StandardButton.Save)
                    assert start.text()=="开始 OCR"
                    accepted.append(True)
                    QTest.mouseClick(start,Qt.MouseButton.LeftButton)
                except BaseException as error:
                    dialog_errors.append(error);dialog.reject()
            timer.timeout.connect(accept_start_settings);timer.start()
            try:
                click(window, button(window, "开始 OCR 识别"))
                wait_for(application,lambda:bool(started) or bool(dialog_errors),detail="settings acceptance before OCR")
            finally:
                timer.stop();bridge.stateChanged.disconnect(record_started)
            if dialog_errors:raise dialog_errors[0]
            assert accepted==[True]
            assert bridge.state["stage"]==stage_before, "OCR must not navigate before results are ready"
            assert not bridge.state["task"]["running"], "OCR must not block editing"
            task_id=started[0]
            # Acceptance may persist setting defaults, but never edits source cues.
            for key in ('mappings','lyrics','translations','visuals'):
                assert bridge.document.project.to_dict()[key]==main_before[key]
            main_before=bridge.document.project.to_dict()
            evidence(label + "_started")
            wait_for(application, lambda: any(identity == task_id for identity, _ in observed_jobs)
                     and not bridge.state["candidateRunning"], timeout=50,
                     detail="real Tesseract/pipeline completion: " + str(tmp_path))
            evidence(label + "_finished")
            error = next(error for identity, error in observed_jobs if identity == task_id)
            assert error is None, repr(error)
            assert not observed_errors, observed_errors
            assert bridge.state["error"] == ""
            assert bridge.document.project.to_dict()==main_before
            from lyricsync.storage import load_project
            project=load_project(bridge.storage_dir/(bridge.document.id+'.pipeline.lyricsync.json'))
            if project.visuals:
                assert len(bridge.document.source_candidates)==candidate_count+1
                record=bridge.document.source_candidates[-1]
                assert record['source']=='ocr' and record['evidence']['visuals']==project.to_dict()['visuals']
                assert [(r['id'],r['start'],r['end']) for r in record['rows']]==[(m.id,m.start,m.end) for m in project.mappings]
                candidate_id=record['id']
            else:
                assert len(bridge.document.source_candidates)==candidate_count
                assert bridge.stage=='import' and bridge._candidate_task['resultStatus']=='empty'
                assert not bridge._candidate_task.get('resultId')
                candidate_id=None
            assert project.video["ocr_runtime"]["name"] == "tesseract"
            assert project.video["ocr_metrics"]["ocr_calls"] >= 1
            # Fingerprint the actual local model used by the real process, not
            # an assumed Windows-style sibling path or a mocked signature.
            import pytesseract
            from lyricsync.storage import file_hash
            from lyricsync.tesseract_process import installed_tessdata
            model_dir = Path(pytesseract.pytesseract.tesseract_cmd).parent / "tessdata"
            if not model_dir.is_dir():
                model_dir = installed_tessdata(pytesseract.pytesseract.tesseract_cmd)
            assert project.video["ocr_runtime"]["models"]["eng"] == file_hash(model_dir / "eng.traineddata")
            phases[label + "_result"] = {
                "roi": list(project.settings["roi"]),
                "recognized": [item.text for item in project.visuals],
                "elapsed_seconds": time.monotonic() - started_at,
                "ocr_metrics": project.video["ocr_metrics"],
                "ocr_runtime": project.video["ocr_runtime"],
                "pipeline_error": str(error) if error is not None else None,
                "candidate_id":candidate_id, "main_unchanged":True,
            }
            return project

        outside_roi = select_roi(.03, .24, [26, 5, 294, 43])
        evidence("outside_roi_selected")
        outside = run_actual_ocr("outside")
        assert outside.settings["roi"] == outside_roi
        assert not outside.visuals, [item.text for item in outside.visuals]
        assert any("未检测到" in warning for warning in outside.warnings)
        assert not outside.mappings

        # OCR starts in the common editor. Return through the actual merged
        # import entry before changing the region for the second independent scan.
        click(window, next(item for item in walk_items(window.contentItem())
                           if item.objectName()=="stageButton_import"))
        wait_for(application, lambda: bridge.state["stage"]=="import", detail="return to merged import page")
        QTest.qWait(120)
        inside_roi = select_roi(.25, .85, [26, 45, 294, 153])
        evidence("inside_roi_selected")
        project = run_actual_ocr("inside")
        recognized = [item.text for item in project.visuals]
        assert any("HELLO" in text.upper() and "WORLD" in text.upper() for text in recognized), recognized
        assert project.mappings and project.video["ocr_runtime"]["name"] == "tesseract"
        assert project.settings["roi"] == inside_roi
        assert not project.settings["api_enabled"]
        # Explicitly review and approve the real OCR output; generation alone
        # must not install its rows into the editable timeline.
        from desktop_qt.candidate_dialog import CandidateDialog
        from PySide6.QtWidgets import QMessageBox
        from lyricsync.editing import mapping_text
        record=bridge.document.source_candidates[-1]
        main_before=bridge.document.project.to_dict()
        depth=len(bridge.history.undo_stack)
        review=bridge._ocr_result_window
        assert review is not None and review.record['id']==record['id'] and bridge.stage=='review'
        review.reject()
        assert bridge.document.project.to_dict()==main_before and len(bridge.history.undo_stack)==depth
        review=CandidateDialog(bridge,record);review.show();review.check_visible(True)
        assert review.table.columnCount()==4 and review.apply.isEnabled()
        review.commit()
        wait_for(application,lambda:not review.alive,detail="verified checked-row commit")
        assert len(bridge.document.main_timeline)==len(record['rows'])
        assert [(m.start,m.end) for m in bridge.document.main_timeline]==[(r['start'],r['end']) for r in record['rows']]
        assert [mapping_text(bridge.document.project,m) for m in bridge.document.main_timeline]==[r['text'] for r in record['rows']]
        assert len(bridge.history.undo_stack)==depth+1
        assert bridge.command('history.undo',{})
        assert bridge.document.project.to_dict()==main_before
        assert bridge.document.source_candidates[-1]['id']==record['id']
        phases["result"] = {"recognized": recognized, "cue_count": len(project.mappings),
                            "outside_roi": outside_roi, "inside_roi": inside_roi,
                            "picker_calls": len(picker_calls),
                            "roi_boundary_and_full_line_assertions_passed": True,
                            "candidate_only_then_confirmed_apply_and_undo": True,
                            "empty_result_workflow_passed": phases["outside_result"]["pipeline_error"] is None,
                            "scope": "offscreen synthetic silent input; no native acceptance"}
        # The old toast was recorded and dismissed by real mouse input before
        # ROI selection; it was not treated as a new OCR command failure.
        print("IMPORT_PANEL_EVIDENCE " + json.dumps(phases, ensure_ascii=True))
    finally:
        (tmp_path / "import-panel-evidence.json").write_text(
            json.dumps(phases, ensure_ascii=False, indent=2), encoding="utf-8")
        bridge.shutdown()
        deadline = time.monotonic() + 3
        while (bridge.media_controller.worker.thread.is_alive() or bridge.jobs.active) and time.monotonic() < deadline:
            application.processEvents()
            time.sleep(.005)
        window.setProperty("allowClose", True)
        window.close()
        engine.deleteLater()
        application.processEvents()
