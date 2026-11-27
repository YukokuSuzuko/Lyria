import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QUICK_BACKEND'] = 'software'
from copy import deepcopy
import json
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from lyricsync.model import Translation
from lyricsync.pairing_session import PairingSession
from tests.test_v023_ocr_correspondence import project
from tests.test_v022_capsule_pairing_ui import Bridge
from desktop_qt.ocr_capsule_pairing_dialog import OcrCapsulePairingDialog

APP = QApplication.instance() or QApplication([])


def test_local_split_merge_history_preserves_originals():
    p = project(); p.translations = [Translation('x', '甲👩‍👩‍👧‍👦乙'), Translation('y', '第二句')]
    before = deepcopy(p.to_dict()); s = PairingSession(p)
    first = s.data['pieces'][0]['id']
    with pytest.raises(ValueError): s.operate('split', [first], offset=2)
    s.operate('split', [first], offset=1)
    assert [s.text(x) for x in s.data['pieces']] == ['甲', '👩‍👩‍👧‍👦乙', '第二句']
    s.history(); assert len(s.data['pieces']) == 2
    s.history(True); assert len(s.data['pieces']) == 3
    s.operate('merge', [x['id'] for x in s.data['pieces']])
    assert s.text(s.data['pieces'][0]) == '甲👩‍👩‍👧‍👦乙第二句'
    assert p.to_dict() == before


def test_draft_load_remainders_and_undo():
    s = PairingSession(project()); before = deepcopy(s.data)
    response = {'groups': [{'anchor_ids': [s.anchors[1].id], 'slices': [{'translation_id': 't1', 'start': 1, 'end': 3}]}]}
    s.load_response(response)
    assert s.data['ai'] and sorted(s.text(p) for p in s.data['pieces']) == sorted(['通的', '普', '歌词'])
    assert s.draft()['groups'][0]['slices'] == response['groups'][0]['slices']
    assert s.draft()['groups'][0]['piece_sizes'] == [1]
    s.history(); assert s.data == before


def dialog(frame_index=None):
    b = Bridge(); b.document.project = project(); b._entry_mode = 'ocr'
    b.media_controller.index = frame_index
    d = OcrCapsulePairingDialog(b)
    return b, d


def test_dialog_cancel_local_only_and_apply_single_transaction():
    b, d = dialog(); before = deepcopy(b.document.project.to_dict())
    assert d.change('source', ids=[d.session.anchors[1].id])
    assert d.change('bind', ids=[d.session.data['pieces'][0]['id']], target=d.session.data['groups'][0]['id'])
    assert b.document.project.to_dict() == before and not b.past
    d.history(False); assert not d.session.data['groups'][0]['piece_ids']
    d.history(True); d.apply()
    assert len(b.past) == 1 and b.document.project.visuals == project().visuals
    b._history(False); assert b.document.project.to_dict() == before
    d.deleteLater()
    b, d = dialog(); d.change('source', ids=[d.session.anchors[0].id]); d.reject()
    assert not b.past; d.deleteLater()


def test_stale_dialog_does_not_adopt():
    b, d = dialog(); b.document.revision += 1
    assert not d.change('source', ids=[d.session.anchors[0].id])
    assert not b.past; d.reject(); d.deleteLater()


def test_explicit_ai_uses_existing_job_only_and_waits_for_apply():
    b, d = dialog(); calls = []
    b._api_check = lambda: calls.append('consent')
    def job(label, operation, callback):
        calls.append('request')
        b.document.project.api_records.append({'test': 'ledger'})
        b.document.revision += 1
        callback({'groups': [{'anchor_ids': [d.session.anchors[1].id], 'slices': [{'translation_id': 't1', 'start': 0, 'end': 5}]}]})
    b._api_job = job
    assert calls == []
    d.request_ai(); assert calls == ['consent', 'request']
    assert d.session.data['ai'] and not b.past
    d.apply(); assert len(b.past) == 1
    d.deleteLater()


def test_api_request_validates_no_new_text(monkeypatch):
    from lyricsync import pairing_api
    def request(cache, key, body, validator, **kwargs):
        assert kwargs['maximum'] == 7 and kwargs['namespace'] == 'ocr-pairing-v023'
        payload = json.loads(body['messages'][1]['content'])
        response = {'groups': [{'anchor_ids': [payload['anchors'][1]['id']], 'slices': [{'translation_id': 't1', 'start': 0, 'end': 5}]}]}
        assert validator(json.dumps(response)) == response
        with pytest.raises(ValueError): validator({'groups': [], 'translation': 'new'})
        return {'value': response}
    monkeypatch.setattr(pairing_api, 'request_json', request)
    result = pairing_api.request_pairing(project(), None, 'not-real', {'api_model': 'mock', 'api_max_calls': 7})
    assert len(result['groups']) == 1


def test_split_frames_local_undo_apply_and_manual_adjustment(monkeypatch):
    from lyricsync.frames import FrameIndex
    from PySide6.QtWidgets import QInputDialog
    b, d = dialog(FrameIndex([0, 3.1, 3.4, 3.55, 4.2, 4.8, 5.1], 6))
    first = d.session.data['pieces'][0]['id']
    assert d.change('split', ids=[first], offset=2)
    assert d.change('source', ids=[d.session.anchors[1].id])
    gid = d.session.data['groups'][0]['id']
    assert d.change('bind', ids=[p['id'] for p in d.session.data['pieces']], target=gid)
    d.middle.item(0).setSelected(True)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **kw: ('5', True))
    d.split_frames()
    assert d.session.data['groups'][0]['split_frames'] == [5]
    d.history(False); assert 'split_frames' not in d.session.data['groups'][0]
    d.history(True); d.apply()
    assert [(m.start, m.end) for m in b.document.project.mappings] == [(3., 4.2), (4.2, 5.)]
    assert b.document.project.visuals == project().visuals
    d.deleteLater()


def test_drag_source_append_and_merge_groups():
    s = PairingSession(project())
    s.operate('source', [s.anchors[0].id])
    group = s.data['groups'][0]['id']
    s.operate('source', [s.anchors[1].id], target=group)
    assert len(s.data['groups']) == 1 and len(s.data['groups'][0]['anchor_ids']) == 2
    s.history(); s.operate('source', [s.anchors[1].id])
    s.operate('merge_groups', [g['id'] for g in s.data['groups']])
    assert len(s.data['groups']) == 1 and len(s.data['groups'][0]['anchor_ids']) == 2


def test_ocr_razor_pointer_and_actual_drag_events():
    from PySide6.QtCore import QPoint, QPointF, QMimeData
    from PySide6.QtGui import QDropEvent
    from PySide6.QtTest import QTest
    from desktop_qt.capsule_pairing_dialog import MIME
    b, d = dialog(); d.show(); APP.processEvents()
    d.razor.setChecked(True)
    rect = d.right.visualItemRect(d.right.item(0))
    point = QPoint(rect.left()+14+d.right.fontMetrics().horizontalAdvance('普'), rect.top()+19)
    QTest.mouseClick(d.right.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    APP.processEvents(); assert d.right.count() == 2, d.status.text()
    def drop(kind, ids):
        mime = QMimeData(); mime.setData(MIME, json.dumps({'document':d.identity, 'revision':d.revision, 'kind':kind, 'ids':ids}).encode())
        point = d.middle.visualItemRect(d.middle.item(0)).center() if d.middle.count() else QPoint(20,20)
        event = QDropEvent(QPointF(point), Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        d.middle.dropEvent(event)
        assert event.isAccepted(), d.status.text()
    drop('source', [d.session.anchors[1].id])
    drop('translation', [d.session.data['pieces'][0]['id']])
    assert len(d.session.data['groups'][0]['piece_ids']) == 1 and not b.past
    d.reject(); d.deleteLater()

def test_sequential_fill_is_local_undoable_and_untimed_slot():
    s = PairingSession(project()); before = deepcopy(s.data)
    s.sequential_fill()
    assert len(s.data['groups']) == 1
    assert s.data['groups'][0]['anchor_ids'] == [s.anchors[0].id]
    s.history(); assert s.data == before
    s.untimed([s.data['pieces'][0]['id']])
    assert s.response()['groups'][0]['anchor_ids'] == []
    s.history(); assert s.data == before


def test_ai_invalid_and_closed_response_do_not_apply():
    b, d = dialog(); b._api_check = lambda: None
    callbacks = []
    b._api_job = lambda label, operation, callback: callbacks.append(callback)
    d.request_ai(); callbacks.pop()({'groups': [], 'new_text': 'forbidden'})
    assert not d.session.data['ai'] and not b.past
    d.request_ai(); d.reject(); callbacks.pop()({'groups': []})
    assert not d.session.data['ai'] and not b.past
    d.deleteLater()


def test_ui_partial_pairing_keeps_unselected_translation_visible_and_undoable():
    from lyricsync.editing import mapping_text
    b, d = dialog(); d.reject(); d.deleteLater()
    b.document.project.translations = [Translation('t1','one'), Translation('t2','two')]
    before = deepcopy(b.document.project.to_dict())
    d = OcrCapsulePairingDialog(b)
    d.change('source', ids=[d.session.anchors[1].id])
    d.change('bind', ids=[d.session.data['pieces'][0]['id']], target=d.session.data['groups'][0]['id'])
    assert b.document.project.to_dict() == before
    d.apply()
    assert [mapping_text(b.document.project,m) for m in b.document.project.mappings] == ['one','two']
    assert b.document.project.mappings[1].start is None
    assert len(b.past) == 1
    b._history(False); assert b.document.project.to_dict() == before
    d.deleteLater()


def test_ui_merged_capsule_1m_preserves_two_output_pieces(monkeypatch):
    from lyricsync.frames import FrameIndex
    from lyricsync.editing import mapping_text
    from PySide6.QtWidgets import QInputDialog
    b, d = dialog(); d.reject(); d.deleteLater()
    b.document.project.translations = [Translation('t1','one'),Translation('t2','two'),Translation('t3','three')]
    before = deepcopy(b.document.project.to_dict())
    b.media_controller.index = FrameIndex([0,3.1,3.4,3.55,4.2,4.8,5.1],6)
    d = OcrCapsulePairingDialog(b)
    d.change('merge',ids=[p['id'] for p in d.session.data['pieces'][:2]])
    assert [d.session.text(p) for p in d.session.data['pieces']] == ['onetwo','three']
    d.change('source',ids=[d.session.anchors[1].id])
    d.change('bind',ids=[p['id'] for p in d.session.data['pieces']],target=d.session.data['groups'][0]['id'])
    d.middle.item(0).setSelected(True)
    monkeypatch.setattr(QInputDialog,'getText',lambda *a,**kw: ('5',True))
    d.split_frames()
    assert d.session.response()['groups'][0]['piece_sizes'] == [2,1]
    assert d.session.response()['groups'][0]['split_frames'] == [5]
    d.apply()
    assert [mapping_text(b.document.project,m) for m in b.document.project.mappings] == ['onetwo','three']
    assert [(m.start,m.end) for m in b.document.project.mappings] == [(3,4.2),(4.2,5)]
    assert len(b.past) == 1
    b._history(False); assert b.document.project.to_dict() == before
    d.deleteLater()

def test_reopen_hides_locked_slice_and_rejects_duplicate_use():
    from lyricsync.translation_pairing_draft import make_draft, apply_draft
    p = project(); p.translations=[Translation('t1','onetwo')]
    s = PairingSession(p); a,b = [x.id for x in s.anchors]
    response={'groups':[{'anchor_ids':[a],'slices':[{'translation_id':'t1','start':0,'end':3}]}]}
    first=apply_draft(p,make_draft(p,response),confirmed=True)
    first.mappings[0].locked=True
    reopened=PairingSession(first)
    assert [reopened.text(x) for x in reopened.data['pieces']] == ['two']
    assert a in reopened.reserved_anchors
    duplicate={'groups':[{'anchor_ids':[b],'slices':[{'translation_id':'t1','start':0,'end':3}]}]}
    with pytest.raises(ValueError):reopened.load_response(duplicate)
    with pytest.raises(ValueError):apply_draft(first,make_draft(first,duplicate),confirmed=True)
    suffix={'groups':[{'anchor_ids':[b],'slices':[{'translation_id':'t1','start':3,'end':6}]}]}
    moved=apply_draft(first,make_draft(first,suffix),confirmed=True)
    assert len(moved.mappings)==2
    assert moved.mappings[0] == first.mappings[0]


def test_unprotected_repair_moves_not_copies_and_keeps_other_rows():
    from lyricsync.model import Mapping
    from lyricsync.translation_pairing_draft import make_draft, apply_draft
    from lyricsync.editing import mapping_text
    p=project();p.translations=[Translation('t1','one'),Translation('t2','two')]
    p.mappings=[Mapping('auto',['t1'],visual_ids=['v0'],start=1,end=2),
                Mapping('other',['t2'],start=None,end=None)]
    s=PairingSession(p)
    response={'groups':[{'anchor_ids':[s.anchors[1].id],'slices':[{'translation_id':'t1','start':0,'end':3}]}]}
    result=apply_draft(p,make_draft(p,response),confirmed=True)
    # v0.24 P1-1: the detached v0 anchor must remain as a visible timed slot.
    assert len(result.mappings)==3
    assert sorted(mapping_text(result,m) for m in result.mappings if m.translation_ids)==['one','two']
    assert {v for m in result.mappings for v in m.visual_ids} == {'v0','v1'}
    assert any(m.visual_ids==['v0'] and not m.translation_ids and (m.start,m.end)==(1,2) for m in result.mappings)
    assert any(m.id=='other' for m in result.mappings)
    assert not any(m.id=='auto' for m in result.mappings)


@pytest.mark.parametrize('sizes', [[0,3],[True,2],[1],[1,1,1,1],[],[2,-1,2]])
def test_invalid_visible_piece_partition_rejected(sizes):
    from lyricsync.translation_pairing_draft import make_draft
    p=project();p.translations=[Translation('t1','one'),Translation('t2','two'),Translation('t3','three')]
    response={'groups':[{'anchor_ids':[],'slices':[{'translation_id':t.id,'start':0,'end':len(t.text)} for t in p.translations],'piece_sizes':sizes}]}
    with pytest.raises(ValueError):make_draft(p,response)


def test_existing_segment_coverage_does_not_hide_original_suffix():
    from lyricsync.model import Mapping
    from lyricsync.translation_pairing_draft import make_draft,apply_draft
    from lyricsync.editing import mapping_text
    p=project();p.translations=[Translation('t1','onetwo'),Translation('t2','three')]
    p.mappings=[Mapping('protected',['t1'],text_segments=[{'id':'t1','start':0,'end':3}],locked=True)]
    s=PairingSession(p)
    assert [s.text(x) for x in s.data['pieces']]==['two','three']
    response={'groups':[{'anchor_ids':[s.anchors[1].id],'slices':[{'translation_id':'t2','start':0,'end':5}]}]}
    result=apply_draft(p,make_draft(p,response),confirmed=True)
    assert sorted(mapping_text(result,m) for m in result.mappings)==['one','three','two']
