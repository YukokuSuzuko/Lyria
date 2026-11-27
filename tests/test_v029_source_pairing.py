from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog, QPushButton

from desktop_qt.pairing_workbench import pairing_dialog
from lyricsync.model import Lyric, Mapping, Project, Translation, Visual
from lyricsync.ocr_source_pairing import edit_source_pair, source_links, source_text
from lyricsync.project_document import write_document, load_document
from lyricsync.translation_pool import pairing_label
from test_qt_entry_initialization_dev5 import bridge


def sample(original=True):
    return Project(
        lyrics=[Lyric('source', 'original text', None, None)] if original else [],
        translations=[Translation('translation', 'translated text')],
        visuals=[Visual('observed', 'observed text', 1., 2., [0, 0, 20, 10], .95, 20)],
        mappings=[Mapping('cue', ['translation'], visual_ids=['observed'], method='candidate_ocr', start=1., end=2.)],
    )


def launch(bridge, project):
    bridge.command('project.newEntry', {'mode': 'ocr'})
    assert bridge._adopt_project(project)
    bridge._changed()
    dialog = pairing_dialog(bridge)
    dialog.show()
    return dialog


def test_ocr_automatic_alignment_is_not_source_pairing(bridge):
    p = sample(False)
    assert pairing_label(p, entry_mode='ocr') == '核对 OCR 与译文'
    assert pairing_label(Project(translations=p.translations), entry_mode='ocr') == '与原文配对'
    p.lyrics.append(Lyric('ocr-source-' + 'a' * 20, 'legacy OCR pseudo-source', None, None))
    dialog = launch(bridge, p)
    try:
        table = dialog.table
        assert table.columnCount() == 2
        assert table.item(0,0).text() == '1.000–2.000 s\nobserved text'
        assert table.item(0,1).text().startswith('1.000–2.000 s\ntranslated text')
        assert dialog.pool.count() == 0
        assert not table.item(0, 0).flags() & Qt.ItemFlag.ItemIsEditable
        assert not dialog.findChild(QPushButton,'ocrPairImportTranslation').isVisible()
        assert source_links(bridge.document.project) == {}
    finally:
        dialog.close()


def test_source_pair_ui_edit_roundtrip_and_undo(bridge, monkeypatch, tmp_path):
    p = sample()
    before = deepcopy(p.to_dict())
    dialog = launch(bridge, p)
    try:
        assert dialog.pool.item(0).data(Qt.ItemDataRole.UserRole) == 'source'
        dialog.pool.setCurrentRow(0)
        dialog.bind_selected()
        assert source_links(bridge.document.project) == {}
        assert source_links(dialog.draft) == {'translation': 'source'}
        monkeypatch.setattr(QInputDialog, 'getMultiLineText', lambda *_: ('corrected original', True))
        dialog.change_member('translation','source','edit')
        assert source_text(dialog.draft,dialog.draft.lyrics[0]) == 'corrected original'
        dialog.history(False)
        assert source_text(dialog.draft,dialog.draft.lyrics[0]) == 'original text'
        dialog.history(True)
        dialog.apply()
        assert bridge._rows()[0]['source'] == 'corrected original'
        assert bridge._rows()[0]['ocr'] == 'observed text'
        after = bridge.document.project
        for key in ('lyrics', 'translations', 'visuals', 'mappings', 'alignment_graph'):
            assert after.to_dict().get(key) == before.get(key)
        path = tmp_path / 'pairing.lyria'
        write_document(bridge.document.snapshot(explicit_save=True), path)
        reopened = load_document(path).project
        assert source_links(reopened) == {'translation': 'source'}
        assert source_text(reopened, reopened.lyrics[0]) == 'corrected original'
        bridge._history(False)
        assert source_links(bridge.document.project) == {}
    finally:
        dialog.close()


def test_locked_cue_disallows_manual_pairing_and_correction():
    p = sample()
    p.mappings[0].locked = True
    with pytest.raises(ValueError, match='受保护'):
        edit_source_pair(p, 'pair', translation_id='translation', source_id='source')
    p.mappings[0].locked = False
    p = edit_source_pair(p, 'pair', translation_id='translation', source_id='source')
    p.mappings[0].locked = True
    with pytest.raises(ValueError, match='受保护'):
        edit_source_pair(p, 'edit', source_id='source', text='correction')


def test_import_after_clean_ocr_fills_only_existing_intervals(bridge, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog
    p = sample(False)
    p.translations = [Translation('placeholder', '')]
    p.mappings[0].translation_ids = ['placeholder']
    p.mappings[0].text_override = ''
    p.settings['input_mode'] = 'video_only'
    path = tmp_path / 'after-ocr.txt'
    path.write_text('observed text\n', 'utf8')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *_: (str(path), ''))
    dialog = launch(bridge, p)
    try:
        assert dialog.table.item(0, 1).data(Qt.ItemDataRole.UserRole) is None
        button = dialog.findChild(QPushButton, 'ocrPairImportTranslation')
        assert button.isVisible()
        button.click()
        fixed = dialog.draft
        assert [(row.id, row.start, row.end, row.visual_ids) for row in fixed.mappings] == [
            ('cue', 1., 2., ['observed'])]
        assert fixed.visuals == p.visuals
        assert dialog.table.item(0, 1).text().startswith('1.000–2.000 s\nobserved text'), dialog.status.text()
        assert not button.isVisible()
        dialog.history(False)
        assert bridge.document.project.to_dict() == p.to_dict()
    finally:
        dialog.close()


def test_import_after_locked_ocr_does_not_retime_or_rewrite():
    from lyricsync.ocr_source_pairing import fill_ocr_placeholders
    p = sample(False)
    p.translations = [Translation('placeholder', '')]
    p.mappings[0].translation_ids = ['placeholder']
    p.mappings[0].text_override = ''
    p.mappings[0].locked = True
    before = p.to_dict()
    assert fill_ocr_placeholders(p, [Translation('new-translation', 'observed text')]) is None
    assert p.to_dict() == before
