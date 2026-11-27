"""Use LYRIA_V024_REAL_64M=1 to run against the production-size ceiling."""
import gc
import os

import pytest

from lyricsync import project_document as documents
from lyricsync.storage import DocumentSizeError


@pytest.mark.parametrize("explicit", [True, False], ids=["explicit", "recovery"])
def test_actual_utf8_bytes_checked_before_backup_and_replace(tmp_path, monkeypatch, explicit):
    original_limit = documents.MAX_DOCUMENT_BYTES
    limit = original_limit if os.getenv("LYRIA_V024_REAL_64M") == "1" else 8192
    monkeypatch.setattr(documents, "MAX_DOCUMENT_BYTES", limit)
    doc = documents.ProjectDocument()
    doc.project.settings["boundary_text"] = "界"
    path = tmp_path / ("target.lyria" if explicit else "test.recovery.lyria")

    def save():
        return documents.write_document(doc.snapshot(explicit_save=explicit), path)

    save()
    overhead = path.stat().st_size
    for delta in (-1, 0):
        doc.project.settings["boundary_text"] = "界" + "a" * (limit + delta - overhead)
        assert save() == doc.revision
        assert path.stat().st_size == limit + delta
        assert documents.load_document(path).project.settings["boundary_text"] == doc.project.settings["boundary_text"]
        gc.collect()
    bak = path.with_name(path.name + ".bak")
    before = (path.stat().st_size, path.stat().st_mtime_ns, bak.stat().st_size, bak.stat().st_mtime_ns)
    assert before[2] == limit - 1
    doc.project.settings["boundary_text"] += "x"
    with pytest.raises(DocumentSizeError, match="原文件和备份未改变"):
        save()
    assert before == (path.stat().st_size, path.stat().st_mtime_ns, bak.stat().st_size, bak.stat().st_mtime_ns)
    assert documents.load_document(path).project.settings["boundary_text"] == doc.project.settings["boundary_text"][:-1]
    assert not list(tmp_path.glob(".lyria-*.tmp"))