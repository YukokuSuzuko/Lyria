"""Lossless import of an untimed translation pool into a timed original timeline."""
from copy import deepcopy

import pytest
from PySide6.QtWidgets import QFileDialog

from lyricsync.capsule_pairing import sources, state
from lyricsync.model import Lyric, Mapping, Visual
from lyricsync.project_document import load_document, write_document
from test_qt_entry_initialization_dev5 import bridge as bridge, settled


@pytest.mark.parametrize('edited', [False, True])
@pytest.mark.parametrize('linked_lyrics', [True, False])
def test_import_preserves_all_fifty_originals(bridge, tmp_path, monkeypatch, edited, linked_lyrics):
    project = bridge.document.project
    speaker='speaker-'+'a'*32
    project.settings['speaker_profiles']={speaker:{'name':'合唱'}}
    project.lyrics = [Lyric(f'l{i}', f'original {i}', i, i + .8) for i in range(50)]
    project.mappings = [
        Mapping(f'm{i}', [], [lyric.id], start=lyric.start, end=lyric.end,
                text_override=lyric.text, text_role='source_import', human_edited=edited,
                speaker_id=speaker, style_overrides={'size':38})
        for i, lyric in enumerate(project.lyrics)
    ]
    if not linked_lyrics:
        project.lyrics = []
        for row in project.mappings:
            row.lyric_ids = []
            row.text_role = 'source_asr'
        project.visuals = [Visual('unbound-old-ocr', 'older evidence', 0., 1., [0, 0, 20, 10], .9, 3)]
    bridge._changed()
    before = deepcopy(project.to_dict())
    old_rows = deepcopy(bridge.document.main_timeline)
    depth = len(bridge.history.undo_stack)
    path = tmp_path / 'translations.txt'
    path.write_text('\n'.join(f'中文{i}' for i in range(31)), 'utf8')
    payload = {'role': 'translation', 'path': str(path)}
    assert bridge.command('input.import', payload), bridge.state['error']
    settled(bridge)
    project = bridge.document.project
    assert bridge.document.main_timeline == old_rows
    assert len(state(project)['pieces']) == 31
    after = deepcopy(project.to_dict())
    assert len(bridge.history.undo_stack) == depth + 1
    assert bridge.command('input.import', payload)
    settled(bridge)
    assert bridge.document.project.to_dict() == after
    assert len(bridge.history.undo_stack) == depth + 1
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args, **kwargs: ('', ''))
    assert bridge.command('input.import', {'role': 'translation'})
    assert bridge.document.project.to_dict() == after
    saved = tmp_path / 'timed-with-pool.lyria'
    write_document(bridge.document.snapshot(explicit_save=True), saved)
    assert load_document(saved).project.to_dict() == after
    assert bridge.command('history.undo', {})
    assert bridge.document.project.to_dict() == before
    assert bridge.command('history.redo', {})
    assert bridge.document.project.to_dict() == after
    from desktop_qt.pairing_workbench import pairing_dialog
    dialog=pairing_dialog(bridge)
    try:
        assert dialog.change('source',ids=['m0'])
        staged=deepcopy(bridge.document.project.to_dict())
        group=state(bridge.document.project)['groups'][0]
        translations=state(bridge.document.project)['pieces']
        assert dialog.change('bind',ids=[translations[0]['id']],target=group['id'])
        assert [(r.id,r.start,r.end,r.speaker_id,r.style_overrides) for r in bridge.document.main_timeline] == [
            (r.id,r.start,r.end,r.speaker_id,r.style_overrides) for r in old_rows]
        assert sources(bridge.document.project)[0]['text']=='original 0'
        paired = tmp_path / 'confirmed-pair.lyria'
        write_document(bridge.document.snapshot(explicit_save=True), paired)
        assert sources(load_document(paired).project)[0]['text']=='original 0'
        assert bridge.document.project.to_dict()['lyrics']==before['lyrics']
        assert bridge.command('history.undo',{}) and bridge.document.project.to_dict()==staged
        assert bridge.command('history.undo',{}) and bridge.document.project.to_dict()==after
    finally:
        dialog.close()
