"""Media detach/replacement preserves edit ownership and clears stale media identity."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
import pytest
from PySide6.QtWidgets import QApplication
from desktop_qt.controller import Bridge
from lyricsync.model import Mapping, Translation


@pytest.fixture
def bridge(tmp_path):
    app = QApplication.instance() or QApplication([])
    instance = Bridge(storage_dir=tmp_path/'storage')
    yield instance
    instance.shutdown()
    app.processEvents()


def seed(bridge):
    p = bridge.document.project
    p.inputs = {'video': 'old.mp4', 'translation': 'lyrics.ass'}
    p.video = {'content_hash': 'old-hash', 'width': 1920, 'height': 1080, 'duration': 112.}
    p.settings['input_hashes'] = {'video': 'old-hash', 'translation': 'text-hash'}
    p.translations = [Translation('t', '保留的译文')]
    p.mappings = [Mapping('m', ['t'], start=2., end=4., human_edited=True)]
    return p


def test_detach_clears_media_identity_but_preserves_rows(bridge):
    p = seed(bridge)
    rows = [row.__dict__.copy() for row in p.mappings]
    bridge._clear_input({'role': 'media'})
    assert p.video == {}, 'old fingerprint/metadata must not reject the next video'
    assert 'video' not in p.inputs and 'audio' not in p.inputs
    assert p.inputs['translation'] == 'lyrics.ass'
    assert p.settings['input_hashes'] == {'translation': 'text-hash'}
    assert [row.__dict__ for row in p.mappings] == rows
    assert p.settings.get('media_detached_with_timing') is True


def test_attach_after_detach_requires_explicit_timing_choice(bridge, tmp_path, monkeypatch):
    p = seed(bridge)
    bridge._clear_input({'role': 'media'})
    path = tmp_path/'new.mp4'; path.write_bytes(b'media picker fixture')
    calls=[]
    monkeypatch.setattr(bridge.media_controller, 'open', calls.append)
    monkeypatch.setattr(bridge, '_confirm_media_timing_reuse', lambda: False)
    before=p.to_dict()
    bridge._open_video({'path': str(path)})
    assert p.to_dict() == before and calls == []
    monkeypatch.setattr(bridge, '_confirm_media_timing_reuse', lambda: True)
    bridge._open_video({'path': str(path)})
    assert calls == [str(path)]
    assert (p.mappings[0].start,p.mappings[0].end) == (2.,4.)
    assert not p.settings.get('media_detached_with_timing')


@pytest.fixture(autouse=True)
def explicit_detach_choice(monkeypatch):
    from desktop_qt import dialogs
    monkeypatch.setattr(dialogs,'choose_asset_clear',lambda:'detach')
