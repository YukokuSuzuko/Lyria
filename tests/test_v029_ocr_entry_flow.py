"""Regression for audio → OCR source scope and reseeding."""
from copy import deepcopy

import pytest

from lyricsync.frames import FrameIndex
from lyricsync.model import Lyric, Mapping, Translation, Visual
from test_qt_entry_initialization_dev5 import bridge, ready, settled
from desktop_qt import dialogs


@pytest.mark.parametrize('clear_media', [False, True])
@pytest.mark.parametrize('source_choice', ['archive', 'reuse', 'cancel'])
def test_audio_source_scope_before_first_ocr(bridge, monkeypatch, tmp_path, clear_media, source_choice):
    b = bridge
    ready(b, 'audio')
    source = tmp_path / 'old.txt'
    source.write_text('OLD_SOURCE_SENTINEL', encoding='utf-8')
    assert b.command('input.import', {'role': 'source', 'path': str(source)})
    settled(b)
    assert b.document.main_timeline
    if clear_media:
        b.document.project.inputs['video'] = str(tmp_path / 'original.mp4')
        b._clear_input({'role': 'media'})
        assert not b.document.main_timeline
    before = deepcopy(b.document.project.to_dict())
    monkeypatch.setattr(dialogs, 'confirm_mode_switch', lambda: True)
    monkeypatch.setattr(dialogs, 'choose_ocr_source_reuse', lambda: source_choice)
    assert b._start_editor({'mode': 'ocr'}) is (source_choice != 'cancel')
    if source_choice == 'cancel':
        assert b.document.project.to_dict() == before and b._entry_mode == 'audio'
        return
    p = b.document.project
    assert not b.document.main_timeline
    assert ('ktv' in p.inputs) is (source_choice == 'reuse')
    assert bool(p.lyrics) is (source_choice == 'reuse')
    if source_choice == 'archive':
        assert p.settings['timeline_archives'][-1]['project']['inputs']['ktv'] == str(source)
    video = tmp_path / 'controlled.mp4'
    video.write_bytes(b'controlled scan')
    b.document.project.inputs['video'] = str(video)
    b.document.project.video.update(width=320, height=180, duration=4.)
    b.document.project.settings['roi'] = [0, 0, 100, 30]
    b.media_controller.index = FrameIndex([n * .1 for n in range(40)], 4.)
    seen = []
    def scan(path, settings, cache, references, **kwargs):
        seen.extend(references)
        return ([Visual('fresh', 'FRESH OCR TEXT', .2, .8, [0,0,80,25], .99, 5)],
                {'width': 320, 'height': 180, 'duration': 4.})
    monkeypatch.setattr('lyricsync.pipeline.scan', scan)
    b._run_ocr()
    settled(b)
    assert seen == (['OLD_SOURCE_SENTINEL'] if source_choice == 'reuse' else [])
    assert len(b.document.main_timeline) == 1
    assert b.document.main_timeline[0].visual_ids == ['fresh']
    assert b.command('history.undo', {})
    assert b.command('history.undo', {})
    assert b.document.project.to_dict() == before and b._entry_mode == 'audio'


@pytest.mark.parametrize('kind', ['manual_lock', 'timed_import'])
def test_protected_axis_never_reaches_ocr_source_prompt(bridge, monkeypatch, kind):
    b = bridge
    ready(b, 'audio')
    p=b.document.project
    p.translations=[Translation('subtitle', 'keep')]
    p.mappings=[Mapping('row', ['subtitle'], method='import', start=.3, end=.7)]
    if kind == 'manual_lock':
        p.settings['manual_locked_ids']=['row']
    else:
        p.settings['main_timeline_kind']='timed-import'
    b._entry_accept_import()
    before=deepcopy(p.to_dict())
    monkeypatch.setattr(dialogs, 'choose_asset_clear', lambda: 'cancel')
    monkeypatch.setattr(dialogs, 'choose_ocr_source_reuse', lambda: pytest.fail('protected stage'))
    assert not b._start_editor({'mode': 'ocr'})
    assert b.document.project.to_dict() == before


@pytest.mark.parametrize('role', ['source', 'translation'])
def test_remove_pool_drops_inapplicable_pairs_and_undo_restores(bridge, role):
    from lyricsync.ocr_source_pairing import LINKS, CORRECTIONS
    b=bridge
    ready(b)
    p=b.document.project
    p.lyrics=[Lyric('original', 'imported text', None, None)]
    p.translations=[Translation('translation', 'translation text')]
    p.inputs.update(ktv='original.txt', translation='translation.txt')
    p.settings[LINKS]={'translation': 'original'}
    p.settings[CORRECTIONS]={'original': 'corrected text'}
    b._entry_accept_import()
    before=deepcopy(p.to_dict())
    b._clear_input({'role': role})
    assert LINKS not in p.settings
    assert (CORRECTIONS in p.settings) is (role=='translation')
    assert b.command('history.undo', {})
    assert b.document.project.to_dict() == before
