"""Dev8 workflow acceptance contracts: synthetic data, no network/inference."""
import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import pytest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QDialog
from lyricsync.model import Project, Lyric, Mapping
from lyricsync.project_document import ProjectDocument
from lyricsync.local_models import QWEN, NEXTFIRE, ModelManager
from lyricsync.local_alignment import request_rows, validate_options
from desktop_qt.local_alignment_dialog import LocalAlignmentDialog, _TimeSpinBox

class MemoryClipboard:
    """Python-path clipboard only; never obtains the OS QClipboard object."""
    def __init__(self):self.value=''
    def setText(self,text):self.value=text
    def text(self):return self.value


@pytest.fixture(autouse=True)
def memory_clipboard(monkeypatch):
    clipboard=MemoryClipboard()
    monkeypatch.setattr(QApplication,'clipboard',staticmethod(lambda:clipboard))
    return clipboard


@pytest.fixture(scope="module")
def app():return QApplication.instance() or QApplication([])

@pytest.fixture
def make(app,tmp_path):
    dialogs=[]
    def create(project=None,**kwargs):
        kwargs.setdefault("initial_options",{"cache_root":str(tmp_path/"cache")})
        d=LocalAlignmentDialog(ProjectDocument(project or Project()),**kwargs);dialogs.append(d);return d
    yield create
    for d in dialogs:d.reject();d.deleteLater()
    app.processEvents()

def test_manage_has_only_three_tabs_and_close(make):
    d=make(setup_only=True)
    assert [d.tabs.tabText(i) for i in range(d.tabs.count())]==["模型与环境","安装与许可","任务日志"]
    assert d.buttons.button(QDialogButtonBox.StandardButton.Ok).text()=="关闭"
    assert d.buttons.button(QDialogButtonBox.StandardButton.Cancel).isHidden()
    assert d.readiness.isHidden()
    d._accept();assert d.result()==QDialog.DialogCode.Accepted

def test_business_first_tab_and_chinese_languages(make):
    d=make()
    assert d.windowTitle()=="AI 自动打轴" and d.tabs.currentIndex()==0
    assert [d.language.itemText(i) for i in range(d.language.count())]==["日语","英语","中文","粤语","韩语"]
    assert d.confirm.isHidden() and d.draft_confirm.isHidden() and d.draft_use.isHidden()

def test_reference_works_without_timeline(make):
    p=Project(lyrics=[Lyric("a","同じ",None,None),Lyric("b","同じ",None,None)])
    d=make(p)
    assert d.text_source.currentData()=="reference"
    rows=d._source_rows();assert len(rows)==2 and rows[0]["id"]!=rows[1]["id"]
    assert "共 2 句" in d.source_role.text()

def test_paste_preserves_lines_and_repetitions(make,app):
    d=make();app.clipboard().setText("一行目\n同じ\n同じ")
    d._paste_lyrics();rows=d._source_rows()
    assert [r["text"] for r in rows]==["一行目","同じ","同じ"]
    assert len({r["id"] for r in rows})==3 and not d.document.project.mappings

def test_han_only_is_nonblocking_warning(make):
    d=make(Project(lyrics=[Lyric("a","夢幻",None,None)]))
    assert d.language_warning.text()==""  # v0.22: no Chinese-specific signal in Japanese 夢幻.
    assert d._source_rows()[0]["text"]=="夢幻"
    assert d.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()

def test_short_song_default_and_time_format(make):
    d=make(Project(video={"duration":112}))
    assert d.offset.value()==0 and d.duration.value()==112
    assert d.duration.text()=="01:52" and not d.custom_range.isChecked()
    assert "整首歌曲" in d.range_summary.text()

def test_long_song_defaults_to_whole_media_not_first_300_seconds(make):
    d=make(Project(video={"duration":430}))
    assert not d.range_required and "整首歌曲" in d.range_summary.text()
    assert d.duration.value() == 430 and not d.custom_range.isChecked()
    assert d.options()['duration_seconds'] is None
    d.custom_range.setChecked(True)
    assert d.options()['duration_seconds'] == 430

def test_nextfire_selectable_and_readings_identity(make):
    d=make(Project(lyrics=[Lyric("a","同じ",None,None),Lyric("b","同じ",None,None)]))
    d.alignment_model.setCurrentIndex(d.alignment_model.findData(NEXTFIRE.id))
    d.reading_table.item(0,1).setText("onaji");d.reading_table.item(1,1).setText("onaji")
    rows=d.options()["reading_rows"]
    assert rows[0]["id"]!=rows[1]["id"] and all(r["reading"]=="onaji" for r in rows)
    assert "CC-BY-NC-SA-4.0" in d.license_page.toPlainText()

def test_missing_models_not_reported_ready(make):
    d=make(setup_only=True)
    assert all("已就绪" not in label.text() for label in d.model_labels.values())
    assert "检测通过" not in d.runtime_status.text()

def test_direct_audio_default_does_not_require_separation_checkbox(make):
    d=make();assert d.options()["use_separation"] is False

def test_advanced_arrow_small_and_toggle(make,app):
    d=make(setup_only=True);d.show();app.processEvents()
    assert d.advanced_toggle.iconSize().width()==10
    d.advanced_toggle.setChecked(True);assert not d.advanced_body.isHidden()
    d.advanced_toggle.setChecked(False);assert d.advanced_body.isHidden()

def test_validation_reference_with_empty_main(tmp_path,monkeypatch):
    import lyricsync.local_alignment as module
    audio=tmp_path/"a.wav";audio.write_bytes(b"audio")
    monkeypatch.setattr(module,"validate_runtime_path",lambda value:tmp_path/"python.exe")
    d=ProjectDocument(Project(lyrics=[Lyric("a","歌",None,None)]))
    options=validate_options(d,dict(audio_path=str(audio),text_source="reference",duration_seconds=20,use_separation=False))
    assert options["rows"][0]["text"]=="歌" and not d.project.mappings

def test_source_reading_change_rejected(tmp_path,monkeypatch):
    import lyricsync.local_alignment as module
    monkeypatch.setattr(module,"validate_runtime_path",lambda value:tmp_path/"python.exe")
    audio=tmp_path/"a.wav";audio.write_bytes(b"audio")
    d=ProjectDocument(Project(lyrics=[Lyric("a","歌",None,None)]))
    with pytest.raises(ValueError,match="原词已改变"):
        validate_options(d,dict(audio_path=str(audio),text_source="reference",reading_rows=[{"id":"reference-a","text":"别的","reading":"uta"}]))

@pytest.mark.parametrize("source",["reference","external"])
def test_empty_timeline_full_candidate_install_and_preview(source,tmp_path,monkeypatch):
    import lyricsync.local_alignment as module
    import lyricsync.local_separation as separation
    from lyricsync.candidate_tracks import read_candidates,install_candidate,prepare_apply
    audio=tmp_path/"audio.wav";audio.write_bytes(b"synthetic media identity")
    monkeypatch.setattr(module,"validate_runtime_path",lambda value:tmp_path/"python.exe")
    monkeypatch.setattr(module.ModelManager,"ensure_verified",lambda *a,**k:None)
    monkeypatch.setattr(module,"_extract_audio",lambda *a,**k:1.)
    monkeypatch.setattr(module,"audio_activity",lambda *a:dict(digital_silence=False))
    monkeypatch.setattr(separation,"prepare_vocals",lambda *a,**k:pytest.fail("direct mode must not separate"))
    def worker(request,*a,**k):
        return dict(model_id=NEXTFIRE.id,revision=NEXTFIRE.revision,duration=1.,rows=[dict(row,start_time=.1+i*.4,end_time=.4+i*.4,path_score=.5) for i,row in enumerate(request["rows"])])
    monkeypatch.setattr(module,"run_worker",worker)
    document=ProjectDocument(Project(lyrics=[Lyric("a","歌",None,None,reading="uta"),Lyric("b","歌",None,None,reading="uta")]))
    before=document.snapshot()
    options=dict(audio_path=str(audio),runtime_python="fake",cache_root=str(tmp_path/"models"),model_id=NEXTFIRE.id,text_source=source,use_separation=False,duration_seconds=1.)
    if source=="external":options["source_rows"]=[{"id":"ext-a","text":"歌","reading":"uta"},{"id":"ext-b","text":"歌","reading":"uta"}]
    record=module.generate_vocal_candidate(document,options=options)
    assert document.snapshot()==before
    safe,warning=read_candidates([record]);assert not warning and len(safe)==1
    assert len(record["rows"])==2 and all(r["text_role"]=="source_import" for r in record["rows"])
    install_candidate(document,record)
    plan=prepare_apply(document,record["id"],[r["id"] for r in record["rows"]],add_unmapped=True,apply_text=True)
    assert len(plan["entries"])==2 and all(not item["blocked"] for item in plan["entries"])
    assert not document.project.mappings

def test_management_hidden_pages_have_dialog_ownership(app,tmp_path):
    from PySide6.QtCore import QCoreApplication,QEvent
    before={id(widget) for widget in app.topLevelWidgets()}
    d=LocalAlignmentDialog(ProjectDocument(Project()),setup_only=True,initial_options={"cache_root":str(tmp_path/"cache")})
    created=[widget for widget in app.topLevelWidgets() if id(widget) not in before]
    assert all(widget is d or widget.parent() is not None for widget in created)
    assert d.settings_scroll.parent() is d and d.draft_page.parent() is d
    assert d.readiness.parent() is d and d.confirm.parent() is d
    assert all(button.parent() is d for button in (d.verify_button,d.separation_verify,d.asr_verify))
    d.reject();d.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    app.processEvents()
