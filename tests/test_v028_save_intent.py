"""Review-new's save continuation belongs solely to the save the user approved."""
from copy import deepcopy
from lyricsync.model import Mapping
from lyricsync.project_document import load_document
from test_qt_entry_initialization_dev5 import bridge, settled


def test_rejected_review_save_cannot_clear_after_ordinary_save_as(bridge, tmp_path, monkeypatch):
    b = bridge
    assert b.command('project.newEntry', {'mode': 'edit'})
    doc = b.document
    doc.project.mappings = [Mapping('keep', [], text_override='保留这一句', start=.2, end=1.7, locked=True)]
    b.stage = 'review'
    b._changed()
    snapshot = deepcopy(doc.project.to_dict())
    assert b.command('project.newFromReview', {})
    monkeypatch.setattr(b, '_path', lambda *_args, **_kw: str(tmp_path / 'invalid.txt'))
    assert not b.command('close.resolve', {'choice': 'save'})
    assert b.document is doc and '.lyria' in b._error_text
    saved = tmp_path / 'ordinary.lyria'
    monkeypatch.setattr(b, '_path', lambda *_args, **_kw: str(saved))
    assert b.command('project.saveAs', {'path': str(saved)})
    settled(b)
    assert load_document(saved).project.to_dict() == snapshot
    assert b.document is doc and b.stage == 'review' and not b._startup_visible
    assert b.document.project.to_dict() == snapshot
