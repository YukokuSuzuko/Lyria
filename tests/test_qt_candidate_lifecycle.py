from tests.ai_pairing_helpers import pairing_reply, pairing_plan_reply
"""Candidate persistence and real Qt/QML workflows, with network/key access blocked."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl, Qt, QPointF
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
# Match app_qt.py before any test engine creates controls.
QQuickStyle.setStyle('Basic')
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox
from lyricsync import api_client
from lyricsync.alignment_candidate import candidate_record, restore_candidate
from lyricsync.api_alignment import make_plan
from lyricsync.frames import FrameIndex
from lyricsync.project_document import load_document, write_document
from test_qt_api_review_workflows import (application, bridges, no_external_io, seed, enable,
    split_response, wait_for, modal_driver, KEY)
from test_qml_preview_regression import find


def prepare(b):
    p=seed(b)
    plan=make_plan(p,b.media_controller.index,split_response(),['m1'])
    b._alignment={'plan':plan}
    b.document.alignment_candidate=candidate_record(p,b.media_controller.index,plan)
    b._changed()
    return plan


def test_candidate_metadata_roundtrip_recovery_backup_original_and_no_credentials(application,bridges,tmp_path):
    b=bridges('before');plan=prepare(b);index=b.media_controller.index
    b.media_controller._state.update(ready=True,displayedFrame=3,positionSeconds=.08)
    assert b.command('alignment.preview',{'enabled':True})
    original=deepcopy(b.document.project.to_dict())
    path=tmp_path/'isolated.lyria'
    assert b.command('project.saveAs',{'path':str(path)})
    wait_for(application,lambda:b._save_running is None)
    saved=json.loads(path.read_text(encoding='utf-8'))
    assert saved['project']==original
    assert saved['alignment_candidate']['response']==plan['response']
    assert all(name not in saved['alignment_candidate'] for name in ('key','request','body','settings','project'))
    assert KEY not in path.read_text(encoding='utf-8')
    fresh=bridges('after')
    assert fresh.command('project.open',{'path':str(path)})
    wait_for(application,lambda:not fresh.state['task']['running'])
    assert not fresh.state['alignmentPreview']['active']
    assert not fresh.state['alignmentPreview']['available']
    assert '帧索引' in fresh.state['alignmentPreview']['reason']
    assert not fresh.command('alignment.accept',{})
    fresh.media_controller.index=FrameIndex(list(index.stamps),index.duration);fresh._refresh()
    assert fresh.state['alignmentPreview']['available']
    assert fresh.command('alignment.preview',{'enabled':True})
    assert fresh.document.project.to_dict()==original
    assert fresh.command('alignment.accept',{})
    assert fresh.document.alignment_candidate is None
    assert not fresh.command('alignment.accept',{})
    assert len(fresh.history.undo_stack)==1
    assert fresh.command('history.undo',{}) and fresh.document.project.to_dict()==original
    # Explicit backups and autosave carry the original + the same optional record.
    write_document(b.document.snapshot(),path)
    assert load_document(str(path)+'.bak').alignment_candidate==b.document.alignment_candidate
    b._save(False);wait_for(application,lambda:b._save_running is None)
    recovery=b.storage_dir/(b.document.id+'.recovery.lyria')
    recovered=bridges('recovery')
    assert recovered.command('project.recover',{'path':str(recovery)})
    wait_for(application,lambda:not recovered.state['task']['running'])
    assert recovered.path=='' and not recovered.state['alignmentPreview']['active']
    assert recovered.document.alignment_candidate==b.document.alignment_candidate
    assert recovered.document.project.to_dict()==original


@pytest.mark.parametrize('change',[
    lambda r:r.update(version=999),
    lambda r:r.update(version=True),
    lambda r:r.update(response='broken'),
    lambda r:r.update(mapping_ids=['m1','m1']),
    lambda r:r.update(api_key='never-retain-this-secret'),
    lambda r:r['response']['operations'][0].update(reason={'authorization':'never-retain-this-secret'}),
    lambda r:r['response']['operations'][0].update(reason='x'*270000),
])
def test_bad_optional_metadata_never_blocks_original_open(application,bridges,tmp_path,change):
    b=bridges();prepare(b);obj=b.document.snapshot();original=deepcopy(obj['project'])
    change(obj['alignment_candidate']);path=tmp_path/'bad-candidate.lyria'
    path.write_text(json.dumps(obj),encoding='utf-8')
    fresh=bridges('fresh');assert fresh.command('project.open',{'path':str(path)})
    wait_for(application,lambda:not fresh.state['task']['running'])
    assert fresh.document.project.to_dict()==original
    assert fresh.document.alignment_candidate is None and fresh.document.candidate_warning
    assert not fresh.command('alignment.preview',{'enabled':True})
    assert not fresh.command('alignment.accept',{})
    assert not fresh.history.undo_stack
    safe=tmp_path/'safe-copy.lyria';write_document(fresh.document.snapshot(),safe)
    assert 'never-retain-this-secret' not in safe.read_text(encoding='utf-8')


@pytest.mark.parametrize('change',[
    lambda b,r:r.update(plan_id='tampered'),
    lambda b,r:r.update(mapping_ids=['m1','m2']),
    lambda b,r:r['response']['operations'][0]['parts'][0]['spans'][0].update(end=3),
    lambda b,r:setattr(b.document.project.mappings[0],'text_override','new user text'),
    lambda b,r:b.document.project.video.update(content_hash='different-media'),
    lambda b,r:setattr(b.media_controller,'index',FrameIndex([n*.039 for n in range(25)],1.)),
])
def test_stale_tampered_source_frame_or_scope_cannot_apply(bridges,change):
    b=bridges();prepare(b);record=deepcopy(b.document.alignment_candidate)
    change(b,record);b.document.alignment_candidate=record;b._alignment={};b._refresh()
    before=b.document.project.to_dict()
    assert not b.state['alignmentPreview']['available']
    assert not b.command('alignment.preview',{'enabled':True})
    assert not b.command('alignment.accept',{})
    assert b.document.project.to_dict()==before and not b.history.undo_stack


def test_selection_roundtrip_preserves_manual_candidate_and_unchanged_row(bridges):
    b=bridges();plan=prepare(b);b.media_controller._state.update(displayedFrame=3,positionSeconds=.08,playing=False)
    original=b.document.snapshot();transport=b.media_controller.snapshot()
    assert b.command('alignment.preview',{'enabled':True})
    first=plan['diff'][0]['after'][0]['id']
    assert b.command('timeline.select',{'id':first})
    assert b.command('alignment.preview',{'enabled':False}) and b.selected=='m1'
    assert b.command('alignment.preview',{'enabled':True}) and b.selected==first
    assert b.command('timeline.select',{'id':'m3'})
    assert b.command('alignment.preview',{'enabled':False}) and b.selected=='m3'
    assert b.command('alignment.preview',{'enabled':True}) and b.selected=='m3'
    assert b.document.snapshot()==original and b.media_controller.snapshot()==transport


def test_discard_removes_saved_candidate_without_editing_original(application,bridges,tmp_path):
    b=bridges();prepare(b);original=b.document.project.to_dict();revision=b.document.revision
    assert b.command('alignment.preview',{'enabled':True})
    assert b.command('alignment.reject',{})
    assert b.document.alignment_candidate is None and b.document.project.to_dict()==original
    assert b.document.revision==revision+1 and not b.history.undo_stack
    assert not b.state['alignmentPreview']['hasCandidate']
    path=tmp_path/'discarded.lyria';write_document(b.document.snapshot(),path)
    assert load_document(path).alignment_candidate is None


def merge_response():
    return {'operations':[{'kind':'merge','mapping_ids':['m1','m2'],'parts':[
        {'spans':[{'mapping_id':'m1','start':0,'end':4},{'mapping_id':'m2','start':0,'end':5}],
         'lyric_ids':['l'],'visual_ids':['v0','v1']}],
        'confidence':.8,'reason':'本地模拟：同一连续片段的两句中文合并','reference_meaning':''}], 'semantic_issues':[]}


@pytest.mark.parametrize('window_width',[760,1400])
def test_real_merge_request_qml_entry_close_toggle_apply_undo(application,bridges,monkeypatch,tmp_path,window_width):
    # F47: normal UI is hidden and command dispatch rejects requests. Keep the
    # underlying offline lifecycle regression via an explicit internal call.
    b=bridges();seed(b);enable(b);b.stage='review'
    b.document.project.video.update(width=640,height=360)
    b.media_controller._state.update(ready=True,displayedFrame=3,requestedFrame=3,positionSeconds=.08,playing=False,totalFrames=25)
    b._refresh();before=deepcopy(b.document.project.to_dict());transport=b.media_controller.snapshot();calls=[]
    def provider(body,key,settings=None,*,cancel=lambda:False,on_progress=lambda data:None):
        assert callable(cancel) and callable(on_progress) and not cancel()
        on_progress({'phase':'read_response','received_bytes':32,'content_chars':16})
        calls.append(json.loads(body['messages'][1]['content']))
        assert 'Merge adjacent mappings' in body['messages'][0]['content']
        return json.dumps(pairing_plan_reply(merge_response()),ensure_ascii=False),{}
    monkeypatch.setattr(api_client,'request',provider)
    engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('bridge',b)
    warnings=[];engine.warnings.connect(lambda es:warnings.extend(str(e.toString()) for e in es))
    engine.load(QUrl.fromLocalFile(str(Path('desktop_qt/qml/Main.qml').resolve())))
    assert engine.rootObjects(),warnings
    window=engine.rootObjects()[0];window.setWidth(window_width);QTest.qWait(150)
    def item(name):return find(window.contentItem(),name)
    def choose(dialog):
        dialog.first.setValue(1);dialog.last.setValue(2)
        dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).click()
    def close(dialog):
        assert '合并 1 项' in dialog.text.toPlainText()
        dialog.preview.click();dialog.close()
    try:
        assert item('alignmentRequestButton') and not item('alignmentRequestButton').isVisible()
        assert not item('candidateActionRow').isVisible()
        assert not b.command('alignment.batch',{'firstNumber':1,'lastNumber':2}) and not calls
        with modal_driver(application,[('alignmentRangeDialog',choose),('alignmentPlanDialog',close)]):
            b._api_alignment({})
            wait_for(application,lambda:not b.state['task']['running'])
        assert len(calls)==1
        assert {r['id'] for r in calls[0]['mappings'] if r['editable']}=={'m1','m2'}
        assert b.state['alignmentPreview']['active'] and b.document.alignment_candidate
        assert b.document.project.to_dict()['mappings']==before['mappings']
        assert b.media['subtitleText']=='甲乙丙丁可靠译文2'
        assert len(b.timeline_model.items)==2 and len(b.model.items)==2
        assert [r['id'] for r in b.model.items]==[r['id'] for r in b.timeline_model.items]
        assert [r['number'] for r in b.timeline_model.items]==[1,2]
        wait_for(application,lambda:not b.media['subtitleLayoutPending'])
        assert window.grabWindow().save(str(tmp_path/f'internal-candidate-{window_width}.png'))
        assert b.command('alignment.preview',{'enabled':False}) and not b.state['alignmentPreview']['active']
        assert b.command('alignment.preview',{'enabled':True}) and b.state['alignmentPreview']['active']
        with modal_driver(application,[('alignmentPlanDialog',lambda d:d.keep.click())]):
            assert b.command('alignment.show',{})
        assert len(b.document.project.mappings)==2 and len(b.history.undo_stack)==1
        assert all(not r.confirmed for r in b.document.project.mappings)
        assert b.command('history.undo',{}) and b.document.project.to_dict()['mappings']==before['mappings']
        assert b.media_controller.snapshot()==transport
        assert not any(('Main.qml' in w or 'ReviewPanel.qml' in w) and ('TypeError' in w or 'ReferenceError' in w) for w in warnings),warnings
    finally:
        window.hide();engine.deleteLater();application.processEvents()


def test_failed_new_request_keeps_existing_candidate(application,bridges,monkeypatch):
    b=bridges();prepare(b);enable(b);record=deepcopy(b.document.alignment_candidate)
    from lyricsync.api_client import ApiRequestError
    def failure(*a,**k):raise ApiRequestError('test provider denied',code='http_401')
    monkeypatch.setattr(api_client,'request',failure)
    b._api_alignment({'firstNumber':1,'lastNumber':2})
    wait_for(application,lambda:not b.state['task']['running'])
    assert b.document.alignment_candidate==record and b.state['alignmentPreview']['available']


def test_reopen_candidate_after_actual_video_decode(application,bridges,tmp_path):
    import av
    import numpy as np
    video=tmp_path/'local-synthetic.mp4'
    with av.open(str(video),'w') as container:
        stream=container.add_stream('mpeg4',rate=25);stream.width=160;stream.height=90;stream.pix_fmt='yuv420p'
        for n in range(25):
            frame=av.VideoFrame.from_ndarray(np.full((90,160,3),n*8,dtype=np.uint8),format='rgb24')
            for packet in stream.encode(frame):container.mux(packet)
        for packet in stream.encode():container.mux(packet)
    b=bridges('real-before');seed(b);b.document.project.video={};b.document.project.inputs={'video':str(video)}
    b.media_controller.open(str(video))
    wait_for(application,lambda:b.media_controller.snapshot()['ready'])
    plan=make_plan(b.document.project,b.media_controller.index,split_response(),['m1'])
    b._alignment={'plan':plan}
    b.document.alignment_candidate=candidate_record(b.document.project,b.media_controller.index,plan);b._changed()
    assert b.command('media.seek',{'frame':3})
    wait_for(application,lambda:b.media_controller.snapshot()['displayedFrame']==3)
    assert b.command('alignment.preview',{'enabled':True})
    path=tmp_path/'real-candidate.lyria';assert b.command('project.save',{'path':str(path)})
    wait_for(application,lambda:b._save_running is None)
    fresh=bridges('real-after');assert fresh.command('project.open',{'path':str(path)})
    wait_for(application,lambda:fresh.media_controller.snapshot()['ready'])
    assert not fresh.state['alignmentPreview']['active']
    assert fresh.state['alignmentPreview']['available'],fresh.state['alignmentPreview']['reason']
    assert fresh._alignment['plan']==plan
    assert fresh.command('media.seek',{'frame':3})
    wait_for(application,lambda:fresh.media_controller.snapshot()['displayedFrame']==3)
    original=fresh.document.project.to_dict();transport=fresh.media_controller.snapshot()
    assert fresh.command('alignment.preview',{'enabled':True})
    assert fresh.media['subtitleText']=='丙丁'
    assert fresh.media_controller.snapshot()==transport and fresh.document.project.to_dict()==original


def test_removed_dialog_cannot_apply_or_discard_new_plan(bridges):
    from desktop_qt.api_review_dialogs import AlignmentPlanDialog
    b=bridges();old=prepare(b);dialog=AlignmentPlanDialog(b,old)
    try:
        assert dialog.keep.isEnabled()
        assert b.command('alignment.reject',{})
        assert not dialog.keep.isEnabled() and not dialog.preview.isEnabled()
        new=make_plan(b.document.project,b.media_controller.index,merge_response(),['m1','m2'])
        b._alignment={'plan':new};b._refresh(False)
        dialog.apply()
        assert not b.history.undo_stack and b._alignment['plan'] is new
        dialog.dismiss()
        assert b._alignment['plan'] is new
    finally:dialog.close()


def test_apply_from_original_keeps_corresponding_selection(bridges):
    b=bridges();seed(b);b.selected='m2'
    plan=make_plan(b.document.project,b.media_controller.index,merge_response(),['m1','m2'])
    b._alignment={'plan':plan};b._refresh()
    assert b.command('alignment.accept',{})
    assert b.selected==plan['diff'][0]['after'][0]['id']


def test_late_response_after_edit_never_overwrites_user_content(application,bridges,monkeypatch):
    import threading
    b=bridges();prepare(b);enable(b);old_record=deepcopy(b.document.alignment_candidate)
    started=threading.Event();release=threading.Event()
    def provider(*a,**k):
        started.set();assert release.wait(3)
        return json.dumps(pairing_plan_reply(merge_response()),ensure_ascii=False),{}
    monkeypatch.setattr(api_client,'request',provider)
    try:
        b._api_alignment({'firstNumber':1,'lastNumber':2})
        wait_for(application,started.is_set)
        assert b.command('row.edit',{'id':'m1','text':'用户最新修改'})
        release.set();wait_for(application,lambda:not b.state['task']['running'])
        assert b.document.project.mappings[0].text_override=='用户最新修改'
        assert b.document.alignment_candidate==old_record
        assert not b.state['alignmentPreview']['available']
        assert not b.command('alignment.accept',{})
    finally:release.set()


def test_import_pairing_entry_cancel_and_preview_never_apply(application,bridges,monkeypatch,tmp_path):
    b=bridges();seed(b);enable(b);b.stage='review'
    b.document.project.video.update(width=640,height=360)
    b.media_controller._state.update(ready=True,displayedFrame=3,totalFrames=25)
    b._refresh();before=deepcopy(b.document.project.to_dict());calls=[]
    def provider(body,key,settings=None,*,cancel=lambda:False,on_progress=lambda data:None):
        assert callable(cancel) and callable(on_progress) and not cancel()
        on_progress({'phase':'read_response','received_bytes':32,'content_chars':16})
        calls.append(body);return json.dumps(pairing_plan_reply(merge_response())),{}
    monkeypatch.setattr(api_client,'request',provider)
    engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('bridge',b)
    engine.load(QUrl.fromLocalFile(str(Path('desktop_qt/qml/Main.qml').resolve())))
    assert engine.rootObjects()
    window=engine.rootObjects()[0];window.setWidth(1400);window.requestActivate();QTest.qWait(150)
    button=find(window.contentItem(),'alignmentRequestButton')
    def click():
        assert button and not button.isVisible()
        before_calls=len(calls)
        assert not b.command('alignment.batch',{}) and len(calls)==before_calls
        b._api_alignment({})  # Explicit internal offline regression only.
    def choose(dialog):
        dialog.first.setValue(1);dialog.last.setValue(2);dialog.accept()
    try:
        with modal_driver(application,[('alignmentRangeDialog',lambda d:d.reject())]):click()
        assert not calls
        QTest.qWait(300);window.requestActivate()
        with modal_driver(application,[('alignmentRangeDialog',choose),('alignmentPlanDialog',lambda d:d.close())]):
            click();wait_for(application,lambda:not b.state['task']['running'])
        assert len(calls)==1 and b.document.alignment_candidate
        assert b.document.project.to_dict()['mappings']==before['mappings']
        assert b.document.project.to_dict()['translations']==before['translations']
        assert not b.history.undo_stack
    finally:
        window.hide();engine.deleteLater();application.processEvents()
