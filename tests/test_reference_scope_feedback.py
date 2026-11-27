"""Source selection tests, fake transport only; never call a real AI provider."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
import json
import pytest
from test_api_phase5_windows import fixture,empty
from lyricsync import api_alignment as alignment
from lyricsync import api_alignment_batches as batches
from lyricsync.model import Lyric
from lyricsync.storage import Cache


def long_project():
    project,index=fixture(1)
    project.lyrics.extend([Lyric('ref-a','a'*7000,None,None),Lyric('ref-b','b'*7000,None,None)])
    return project,index


def test_explicit_subset_sent_without_pairing_or_project_mutation(tmp_path):
    project,index=long_project();before=deepcopy(project.to_dict());wires=[]
    cache=Cache(tmp_path/'scope.sqlite')
    def provider(body,key):
        wires.append(json.loads(body['messages'][1]['content']))
        return json.dumps(empty()),{}
    try:
        result=alignment.request_plan(project,index,cache,'fake-test-key',reference_ids=['ref-b'],transport=provider)
        assert result['status']=='no_changes'
        sources={r['id'] for r in wires[0]['source']}
        assert 'ref-b' in sources and 'ref-a' not in sources
        assert any(row['text']=='repeated synthetic lyric' for row in wires[0]['source'])
        after=project.to_dict()
        assert after.pop('api_records')  # Existing request auditing is intentionally retained.
        before.pop('api_records')
        assert after==before
        assert 'ref-b' not in wires[0]['mappings'][0]['lyric_ids']
    finally:cache.close()


def test_different_reference_scope_does_not_reuse_run_cache(tmp_path):
    project,index=long_project();calls=[];cache=Cache(tmp_path/'cache.sqlite')
    def provider(body,key):calls.append(json.loads(body['messages'][1]['content']));return json.dumps(empty()),{}
    try:
        for ref in ('ref-a','ref-b'):
            alignment.request_plan(project,index,cache,'fake-test-key',reference_ids=[ref],transport=provider)
        assert len(calls)==2
    finally:cache.close()


@pytest.mark.parametrize('ids',[[],['ref-a','ref-a'],['unknown'],[3],'ref-a'])
def test_invalid_scope_never_calls_provider(tmp_path,ids):
    project,index=long_project();cache=Cache(tmp_path/'reject.sqlite')
    try:
        with pytest.raises(ValueError):
            alignment.request_plan(project,index,cache,'fake-test-key',reference_ids=ids,transport=lambda *_:pytest.fail('no request'))
    finally:cache.close()


def test_explicit_oversized_scope_still_blocked(tmp_path):
    project,index=long_project();cache=Cache(tmp_path/'large.sqlite')
    try:
        result=alignment.request_plan(project,index,cache,'fake-test-key',reference_ids=['ref-a','ref-b'],transport=lambda *_:pytest.fail('no request'))
        assert result['status']=='source_scope_required'
    finally:cache.close()


def test_reference_dialog_requires_valid_range_and_keeps_originals():
    from PySide6.QtWidgets import QApplication,QDialogButtonBox
    from desktop_qt.reference_scope_dialog import ReferenceScopeDialog
    app=QApplication.instance() or QApplication([])
    project,_=long_project();before=deepcopy(project.to_dict())
    dialog=ReferenceScopeDialog(project)
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    dialog.last.setValue(1)
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    dialog.accept()
    assert dialog.reference_ids==['ref-a']
    assert project.to_dict()==before
    dialog.deleteLater();app.processEvents()


def test_reference_dialog_cancel_returns_false(monkeypatch):
    from PySide6.QtWidgets import QApplication,QDialog
    from desktop_qt.reference_scope_dialog import ReferenceScopeDialog,request_reference_scope
    app=QApplication.instance() or QApplication([])
    project,_=long_project()
    monkeypatch.setattr(ReferenceScopeDialog,'exec',lambda s:QDialog.DialogCode.Rejected)
    assert request_reference_scope(project,{}) is False
    app.processEvents()
