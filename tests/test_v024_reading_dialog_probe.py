"""Pure validators/CLI isolation only: these are NOT packaged dialog acceptance."""
from copy import deepcopy
from pathlib import Path
import pytest
from desktop_qt import reading_dialog_probe as probe

def good_rows():
    return [dict(row,reading=("asa" if i<3 else ""),
                 **({"alignment_skip":"nonvocal_symbols"} if i>=3 else {}))
            for i,row in enumerate(probe.REFERENCE_ROWS)]

def test_full_generated_song_contract():
    probe.validate_rows(good_rows())
    for change in ("missing","text","reading","symbols","hard"):
        rows=good_rows()
        if change=="missing":rows.pop()
        elif change=="text":rows[0]["text"]="changed"
        elif change=="reading":rows[0]["reading"]=""
        elif change=="symbols":rows[-1]["alignment_skip"]=""
        else:rows[0]["reading_issues"]=[{"hard_error":True}]
        with pytest.raises(AssertionError):probe.validate_rows(rows)

def test_source_interpreter_is_never_packaged_acceptance():
    with pytest.raises(RuntimeError,match="actual packaged"):
        probe.validate_private_packaged_environment()

def test_existing_report_parent_preserved(tmp_path,monkeypatch):
    # Environment mocked ONLY to unit-test file guards, no dialog or worker runs.
    monkeypatch.setattr(probe,"validate_private_packaged_environment",lambda:"unit-test")
    sentinel=tmp_path/"keep.txt";sentinel.write_text("unchanged")
    with pytest.raises(FileExistsError):probe.prepare_report(tmp_path/"result.json")
    assert sentinel.read_text()=="unchanged"
    assert not (tmp_path/"result.json").exists()

def test_report_requires_absolute_json(tmp_path,monkeypatch):
    monkeypatch.setattr(probe,"validate_private_packaged_environment",lambda:"unit-test")
    for target in ("relative/result.json",tmp_path/"new"/"result.txt"):
        with pytest.raises(ValueError):probe.prepare_report(target)
    assert not (tmp_path/"new").exists()

@pytest.mark.parametrize("other",["--project","--video","--storage-dir","--self-check",
                                  "--qc-self-check","--qc-reference","--foundation-self-check",
                                  "--editing-self-check","--screenshot"])
def test_cli_rejects_mixed_user_or_qa_inputs(tmp_path,other):
    import app_qt
    report=tmp_path/"new"/"result.json"
    with pytest.raises(SystemExit) as failure:
        app_qt.main(["--reading-dialog-self-check",str(report),other,str(tmp_path/"untouched")])
    assert failure.value.code==2
    assert not report.parent.exists()

def test_cli_refuses_source_before_creating_profile(tmp_path):
    import app_qt
    report=tmp_path/"new"/"result.json"
    with pytest.raises(SystemExit) as failure:
        app_qt.main(["--reading-dialog-self-check",str(report)])
    assert failure.value.code==2
    assert not report.parent.exists()

def test_existing_version_and_worker_dispatch_unchanged(monkeypatch,capsys):
    import app_qt
    with pytest.raises(SystemExit) as done:app_qt.main(["--version"])
    from lyricsync import __display_version__
    assert done.value.code==0 and __display_version__ in capsys.readouterr().out
    from lyricsync import bundled_reading
    calls=[]
    monkeypatch.setattr(bundled_reading,"main",lambda args:calls.append(args) or 7)
    assert app_qt.main(["--reading-worker","--request","in","--result","out"])==7
    assert calls==[["--request","in","--result","out"]]

def test_unprefixed_or_duplicate_ids_rejected():
    rows=good_rows()
    rows[0]['id']=probe.ROWS[0]['id']
    with pytest.raises(AssertionError):probe.validate_rows(rows)
    rows=good_rows();rows[1]['id']=rows[0]['id']
    with pytest.raises(AssertionError):probe.validate_rows(rows)
