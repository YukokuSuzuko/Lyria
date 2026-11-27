"""Focused unit tests for Lyria v0.33 features: mirror sources, global cache sync, AMD ROCm/DirectML routing, fontvuer preview, native SRT."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import urllib.error
import pytest

from lyricsync import local_models as lm
from lyricsync import export as ex
from lyricsync.model import Lyric, Mapping, Project, Translation
from desktop_qt import local_alignment_settings as prefs


def test_mirror_and_official_download_urls_and_trust():
    q_mirror = lm.download_urls(lm.QWEN, lm.QWEN.files[0], 'mirror')
    assert q_mirror[0].startswith('https://hf-mirror.com/')
    assert any(u.startswith('https://modelscope.cn/') for u in q_mirror)
    assert any(u.startswith('https://huggingface.co/') for u in q_mirror)

    nf_mirror = lm.download_urls(lm.NEXTFIRE, lm.NEXTFIRE.files[0], 'mirror')
    assert nf_mirror[0].startswith('https://hf-mirror.com/')
    assert nf_mirror[-1].startswith('https://huggingface.co/')

    uvr_mirror = lm.download_urls(lm.UVR, lm.UVR.files[0], 'mirror')
    assert uvr_mirror[0].startswith('https://gh-proxy.com/https://github.com/')
    assert uvr_mirror[-1].startswith('https://github.com/TRvlvr/')

    assert lm.download_urls(lm.UVR, lm.UVR.files[0], 'official') == [
        'https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/' + lm.UVR.files[0].name
    ]
    assert lm._trusted_url('https://hf-mirror.com/Qwen/model.safetensors', 'mirror') is True
    assert lm._trusted_url('https://hf-mirror.com/Qwen/model.safetensors', 'official') is False
    assert lm._trusted_url('https://evil.example.com/file', 'mirror') is False


def test_mirror_download_fallback_still_requires_exact_sha256(tmp_path, monkeypatch):
    data = b'verified-qwen-mirror-payload'
    spec = replace(lm.QWEN, files=(lm.ModelFile('sample.bin', len(data), hashlib.sha256(data).hexdigest()),))
    monkeypatch.setitem(lm.CATALOG, spec.id, spec)
    seen_urls = []

    class Reply(io.BytesIO):
        def __init__(self, body, url):
            super().__init__(body)
            self.url = url
        def geturl(self):
            return self.url
        def getcode(self):
            return 200

    class FallbackOpener:
        def open(self, request, timeout):
            seen_urls.append(request.full_url)
            if 'hf-mirror.com' in request.full_url:
                raise urllib.error.URLError('simulated mirror glitch')
            return Reply(data, request.full_url)

    manager = lm.ModelManager(tmp_path / 'cache')
    out = manager.download(spec.id, source='mirror', opener=FallbackOpener())
    assert len(seen_urls) >= 2
    assert 'hf-mirror.com' in seen_urls[0] and 'modelscope.cn' in seen_urls[1]
    assert (out / 'sample.bin').read_bytes() == data

    bad_manager = lm.ModelManager(tmp_path / 'cache_bad')
    class TamperedOpener:
        def open(self, request, timeout):
            return Reply(b'tampered!' * 3, request.full_url)
    with pytest.raises(lm.ModelError):
        bad_manager.download(spec.id, source='mirror', opener=TamperedOpener())


def test_global_model_preferences_and_nested_cache_detection(tmp_path):
    storage = tmp_path / 'profile'
    storage.mkdir()
    assert prefs.effective_download_source(storage) == 'mirror'

    parent_dir = tmp_path / 'shared-models'
    nested = parent_dir / 'models-v1' / lm.UVR.id / lm.UVR.revision
    nested.mkdir(parents=True)
    prefs.save(storage, {'device': 'cuda:0', 'language': 'Japanese'})
    prefs.update_model_preferences(storage, cache_root=str(parent_dir), download_source='official')
    assert Path(prefs.effective_cache_root(storage)) == (parent_dir / 'models-v1').resolve()
    assert prefs.effective_download_source(storage) == 'official'
    assert prefs.load(storage)['language'] == 'Japanese'


def test_amd_rocm_pins_and_driver_validation(monkeypatch):
    from lyricsync import online_amd, split_pins
    assert len(split_pins.ROCM_WHEELS) == 3
    for info in split_pins.ROCM_WHEELS.values():
        assert len(info['sha256']) == 64 and info['bytes'] > 100_000_000
    assert online_amd.safe_url(split_pins.ROCM_WHEELS['torch']['url']).startswith('https://repo.radeon.com/')
    with pytest.raises(ValueError):
        online_amd.safe_url('https://untrusted.example.com/torch.whl')
    monkeypatch.setattr(online_amd, '_amd_display_adapters', lambda: ['AMD Radeon RX 6800 XT'])
    with pytest.raises(ValueError, match='DirectML'):
        online_amd.driver()


def test_native_srt_bilingual_source_only_and_bom(tmp_path):
    project = Project(
        lyrics=[Lyric('l1', '君の名を呼ぶ', 1.0, 3.0)],
        translations=[Translation('t1', '呼唤你的名字', 1.0, 3.0)],
        mappings=[Mapping('m1', ['t1'], ['l1'], start=1.0, end=3.0, confirmed=False)],
    )
    bom_file = tmp_path / 'bilingual_bom.srt'
    ex.export_project(project, bom_file, draft=True, srt_mode='bilingual_trans_first', srt_bom=True)
    raw_bom = bom_file.read_bytes()
    assert raw_bom.startswith(b'\xef\xbb\xbf')
    text_bom = raw_bom.decode('utf-8-sig').replace('\r\n', '\n')
    assert '1\n00:00:01,000 --> 00:00:03,000\n呼唤你的名字\n君の名を呼ぶ' in text_bom

    nobom_file = tmp_path / 'source_nobom.srt'
    ex.export_project(project, nobom_file, draft=True, srt_mode='bilingual_source_first', srt_bom=False)
    raw_nobom = nobom_file.read_bytes()
    assert not raw_nobom.startswith(b'\xef\xbb\xbf')
    assert '君の名を呼ぶ\n呼唤你的名字' in raw_nobom.decode('utf-8').replace('\r\n', '\n')

    source_only = tmp_path / 'source_only.srt'
    ex.export_project(project, source_only, draft=True, srt_mode='source_only', srt_bom=False)
    assert '呼唤你的名字' not in source_only.read_text('utf-8')
    assert '君の名を呼ぶ' in source_only.read_text('utf-8')


def test_fontvuer_favorites_and_missing_glyph_detection(tmp_path):
    from PySide6.QtWidgets import QApplication
    from desktop_qt.font_preview_widget import (
        FontPreviewWidget,
        count_missing_glyphs,
        load_favorite_fonts,
        save_favorite_fonts,
    )
    app = QApplication.instance() or QApplication([])
    save_favorite_fonts(tmp_path, ['Microsoft YaHei', 'Noto Sans'])
    assert load_favorite_fonts(tmp_path) == ['Microsoft YaHei', 'Noto Sans']
    # Rare Unicode PUA char should report missing on standard font
    assert count_missing_glyphs('Arial', 'ABC \ue001') >= 1
    widget = FontPreviewWidget(storage_dir=tmp_path, initial_sample='Lyria 月色')
    assert widget.model.rowCount() > 0
    widget.search_edit.setText('Arial')
    widget._apply_filter()
    assert widget.model.rowCount() >= 1
    widget.deleteLater()
    app.processEvents()
