from tests.ai_pairing_helpers import pairing_reply, pairing_plan_reply
"""Real Qt retained AI methods/native dialogs and worker jobs; providers/keys mocked.
Production AI commands stay disabled; a separate test verifies their closed gate.

Cloud offscreen contract tests, not live model quality or Windows UI acceptance.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from contextlib import contextmanager
from copy import deepcopy
import json
import time
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QTextEdit, QMessageBox
from desktop_qt.controller import Bridge
from desktop_qt.api_review_dialogs import AlignmentPlanDialog, scope_from_numbers
from lyricsync import ai_config, api_client, credentials
from lyricsync.api import review_signature
from lyricsync.api_alignment import make_plan
from lyricsync.editing import mapping_text
from lyricsync.frames import FrameIndex
from lyricsync.model import Lyric, Mapping, Translation, Visual

KEY='strictly-fake-review-test-key'

@pytest.fixture(scope='module')
def application():
    return QApplication.instance() or QApplication([])

@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    import urllib.request
    def forbidden(*args, **kwargs):
        raise AssertionError('Real API or credential access is forbidden in this test')
    monkeypatch.setattr(urllib.request.OpenerDirector, 'open', forbidden)
    for name in ('api','get','save','delete'):
        monkeypatch.setattr(credentials.KeyStore,name,forbidden)
    monkeypatch.setattr(api_client,'request',forbidden)
    monkeypatch.setattr(api_client,'request_models',forbidden)

@pytest.fixture
def bridges(application,tmp_path):
    items=[]
    def make(name='bridge'):
        b=Bridge(storage_dir=tmp_path/name);items.append(b);return b
    yield make
    for b in items:b.shutdown()
    deadline=time.monotonic()+3
    while any(b.jobs.active or b.media_controller.worker.thread.is_alive() for b in items) and time.monotonic()<deadline:
        application.processEvents();time.sleep(.002)


def wait_for(app,predicate):
    deadline=time.monotonic()+5
    while not predicate() and time.monotonic()<deadline:
        app.processEvents();time.sleep(.002)
    assert predicate(),'real Qt job did not complete'


@contextmanager
def modal_driver(app,actions):
    """Drive actual nested modal event loops; propagate callback assertions."""
    pending=list(actions);errors=[];seen=[];timer=QTimer();timer.setInterval(3)
    def tick():
        if not pending:return
        name,action=pending[0]
        dialog=next((d for d in app.topLevelWidgets() if isinstance(d,QDialog) and d.isVisible() and d.objectName()==name),None)
        if dialog is None:return
        pending.pop(0);seen.append(name)
        try:action(dialog)
        except BaseException as exc:errors.append(exc);dialog.reject()
    timer.timeout.connect(tick);timer.start()
    try:yield seen
    finally:
        deadline=time.monotonic()+2
        while pending and time.monotonic()<deadline:
            app.processEvents();time.sleep(.003)
        timer.stop()
    if errors:raise errors[0]
    assert not pending, f'expected real dialog not shown: {pending}'


def seed(b,count=3):
    p=b.document.project
    p.video={'duration':max(1,count*.12),'content_hash':'synthetic-test-index'}
    p.lyrics=[Lyric('l','actual QA original',0,.2)]
    p.visuals=[Visual('v0','actual OCR first',0,.08,[],.98,2),Visual('v1','actual OCR second',.08,.2,[],.98,2)]
    p.translations=[Translation(f't{n}', '甲乙丙丁' if n==1 else f'可靠译文{n}') for n in range(1,count+1)]
    p.mappings=[Mapping(f'm{n}',[f't{n}'],['l'],['v0','v1'],(n-1)*.12,(n-1)*.12+.08) for n in range(1,count+1)]
    p.mappings[0].end=.2
    b.selected='m1'
    b.media_controller.index=FrameIndex([n*.04 for n in range(max(25,count*3))],max(1,count*.12))
    b._changed()
    return p


def retained_ai_for_test(b,name,payload):
    """Private retained feature test seam, not a bypass installed on the Bridge.

    All providers are mocked by no_external_io. Production command remains closed;
    source guards and real consent/range/budget methods are still exercised.
    """
    try:
        b._candidate_guard(name)
        if name=='alignment.batch':b._api_alignment(payload)
        elif name=='row.align':b._api_review({**payload,'id':b._row(payload.get('id')).id})
        else:raise AssertionError('Unexpected retained AI test operation')
        return True
    except ValueError as exc:
        b._error(str(exc));return False


def enable(b):
    # In-memory key only. No KeyStore call and no persisted secret.
    b._api_key=KEY;b.api_settings={**ai_config.DEFAULTS,'api_enabled':True}
    b._api_scope=ai_config.credential_scope(b.api_settings);b._api_text_consents.add(b._api_scope)


def split_response():
    return {'operations':[{'kind':'split','mapping_ids':['m1'],'parts':[
        {'spans':[{'mapping_id':'m1','start':0,'end':2}],'lyric_ids':['l'],'visual_ids':['v0']},
        {'spans':[{'mapping_id':'m1','start':2,'end':4}],'lyric_ids':['l'],'visual_ids':['v1']}],
        'confidence':.8,'reason':'QA two actual OCR observations','reference_meaning':'QA独立参考'}],
        'semantic_issues':[{'mapping_ids':['m1'],'lyric_ids':['l'],'visual_ids':['v0'],
        'kind':'uncertain','confidence':.6,'reason':'QA可能为配对或译意差异，不是确定OCR错误','reference_meaning':'QA语义参考'}]}


def test_large_project_real_range_dialog_selects_scope_keeps_neighbors_and_excludes_locks(application,bridges,monkeypatch):
    b=bridges();p=seed(b,301);enable(b)
    p.settings['manual_locked_ids']=['m260'];p.mappings[269].ignored=True
    captured=[]
    def provider(body,key,settings=None,**callbacks):
        assert key==KEY;captured.append(json.loads(body['messages'][1]['content']))
        return json.dumps({'operations':[],'semantic_issues':[]}),{}
    monkeypatch.setattr(api_client,'request',provider)
    def select(dialog):
        dialog.first.setValue(251);dialog.last.setValue(300)
        assert '可处理 48 句' in dialog.summary.text()
        dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).click()
    def preview(dialog):
        assert not dialog.keep.isEnabled()
        assert '共 0 项调整' in dialog.text.toPlainText()
        dialog.reject_button.click()
    with modal_driver(application,[('alignmentRangeDialog',select),('alignmentPlanDialog',preview)]):
        assert retained_ai_for_test(b,'alignment.batch',{})
        wait_for(application,lambda:not b.state['task']['running'])
    assert len(captured)==5  # Four disjoint primary windows plus one eligible seam.
    primary=[wire for wire in captured if wire['scope']['mode']=='all']
    editable=[row['id'] for wire in primary for row in wire['mappings'] if row['editable']]
    assert len(editable)==len(set(editable))==48
    assert set(editable)=={f'm{n}' for n in range(251,301)}-{'m260','m270'}
    rows={r['id']:r for wire in captured for r in wire['mappings']}
    assert not rows['m249']['editable'] and not rows['m250']['editable']
    assert all(not row['editable'] for wire in captured for row in wire['mappings'] if row['id']=='m260')
    assert not rows['m301']['editable']
    assert len(p.api_records)==5 and all(r['status']=='success' for r in p.api_records)
    assert not b.history.undo_stack


def test_real_range_cancel_without_key_does_not_start_request_or_mutate(application,bridges):
    b=bridges();p=seed(b,301);before=deepcopy(p.to_dict());revision=b.document.revision
    with modal_driver(application,[('alignmentRangeDialog',lambda d:d.reject())]):
        assert retained_ai_for_test(b,'alignment.batch',{})
    assert not b.jobs.active and p.to_dict()==before
    assert b.document.revision==revision and not b._api_key


@pytest.mark.parametrize('payload',[{'firstNumber':1,'lastNumber':251},{'firstNumber':0,'lastNumber':1},{'firstNumber':True,'lastNumber':1}])
def test_invalid_explicit_scope_fails_before_key_or_provider(bridges,payload):
    b=bridges();p=seed(b,301);before=deepcopy(p.to_dict())
    assert not retained_ai_for_test(b,'alignment.batch',payload)
    assert not b.jobs.active and p.to_dict()==before


def test_all_locked_scope_rejected_without_request(bridges):
    b=bridges();p=seed(b);p.settings['manual_locked_ids']=['m1','m2','m3']
    assert not retained_ai_for_test(b,'alignment.batch',{'firstNumber':1,'lastNumber':3})
    assert not p.api_records and not b.jobs.active


def test_real_request_preview_accept_exact_text_single_undo_no_human_confirmation(application,bridges,monkeypatch):
    b=bridges();p=seed(b);enable(b);original=deepcopy(p.to_dict())
    monkeypatch.setattr(api_client,'request',lambda *a,**k:(json.dumps(pairing_plan_reply(split_response()),ensure_ascii=False),{}))
    def accept(dialog):
        text=dialog.text.toPlainText()
        assert '受影响句数：1 → 2' in text and '第 1 句（ID：m1）' in text
        assert 'actual QA original' in text and 'actual OCR first' in text and 'F1–F2' in text
        assert 'QA可能为配对或译意差异' in text and '"semantic_issues"' not in text
        assert dialog.keep.isEnabled();dialog.keep.click()
        assert dialog.result()==QDialog.DialogCode.Accepted
    with modal_driver(application,[('alignmentPlanDialog',accept)]):
        assert retained_ai_for_test(b,'alignment.batch',{'firstNumber':1,'lastNumber':1})
        wait_for(application,lambda:not b.state['task']['running'])
    assert len(p.mappings)==4 and len(b.history.undo_stack)==1
    assert ''.join(mapping_text(p,m) for m in p.mappings[:2])=='甲乙丙丁'
    assert all(not m.confirmed for m in p.mappings[:2])
    assert p.to_dict()['translations']==original['translations']
    assert b.command('history.undo',{})
    assert b.document.project.to_dict()['mappings']==original['mappings']
    assert len(b.document.project.api_records)==1,'undo must not refund the spent request'


def test_stale_native_preview_disables_adoption_and_command_rejects(bridges):
    b=bridges();p=seed(b);plan=make_plan(p,b.media_controller.index,split_response(),['m1']);b._alignment={'plan':plan}
    dialog=AlignmentPlanDialog(b,plan)
    try:
        assert dialog.keep.isEnabled()
        p.mappings[0].text_override='用户后来的可靠中文';b._changed()
        assert not dialog.keep.isEnabled() and '已失效' in dialog.status.text()
        before=deepcopy(p.to_dict())
        assert not b.command('alignment.accept',{})
        assert p.to_dict()==before and not b.history.undo_stack
    finally:dialog.reject()


def seed_saved(p):
    signature=review_signature(p,p.mappings[0])
    suggestion={'mappings':[{'mapping_ids':['m1'],'lyric_ids':['l'],'translation_ids':['t1'],
        'current_chinese':['甲乙丙丁'],'reason':'QA已保存历史建议','reference_meaning':'QA历史参考译意'}],'corrections':[]}
    p.settings['api_suggestions']=[{'mapping_id':'m1','status':'pending',
        'previous_suggestion':{'mapping_id':'m1','status':'processed','historical':True,'context_signature':signature,'suggestion':suggestion}}]
    p.settings['reference_translations']={'m1':{'translation':'QA单句参考','note':'QA翻译存在不确定性','context_signature':signature,'source_text':'actual QA original'}}
    p.mappings[0].text_override='用户已手改的可靠中文'


def test_saved_history_reopen_real_offline_command_no_key_no_network_no_adoption(application,bridges,tmp_path):
    b=bridges('old');p=seed(b);seed_saved(p);b._changed();path=tmp_path/'history.lyria'
    assert b.command('project.save',{'path':str(path)})
    wait_for(application,lambda:b._save_running is None)
    assert KEY.encode() not in path.read_bytes()
    reopened=bridges('fresh')
    assert reopened.command('project.open',{'path':str(path)})
    wait_for(application,lambda:not reopened.state['task']['running'])
    assert reopened.document.id==b.document.id and not reopened._api_key
    before=deepcopy(reopened.document.project.to_dict());revision=reopened.document.revision
    def inspect(dialog):
        editor=dialog.findChild(QTextEdit,'savedAiReviewText');text=editor.toPlainText()
        assert editor.isReadOnly() and '不会发起新请求' in text
        for phrase in ('不确定','尚未核对'):
            assert phrase in text
        box=dialog.findChild(QDialogButtonBox)
        assert box.standardButtons()==QDialogButtonBox.StandardButton.Close
        dialog.reject()
    for payload in ({'all':True},{'id':'m1'}):
        with modal_driver(application,[('savedAiReviewDialog',inspect)]):
            assert reopened.command('api.reviewSaved',payload)
    assert reopened.document.project.to_dict()==before and reopened.document.revision==revision
    assert not reopened.jobs.active and not reopened.history.undo_stack


def test_empty_saved_command_is_safe_without_key(application,bridges):
    b=bridges();seed(b)
    def inspect(dialog):
        assert '不会发起新请求' in dialog.findChild(QTextEdit).toPlainText();dialog.reject()
    with modal_driver(application,[('savedAiReviewDialog',inspect)]):
        assert b.command('api.reviewSaved',{'all':True})
    assert not b.jobs.active and not b.document.project.api_records


@pytest.mark.parametrize('approve',[False,True])
def test_manual_review_native_first_send_consent_independent_of_auto(application,bridges,monkeypatch,approve):
    from desktop_qt import dialogs
    b=bridges();p=seed(b,60)
    b.configure_api(ai_config.DEFAULTS,KEY,False)
    # Importing the old toggle cannot authorize a provider transmission.
    p.settings.update(api_enabled=True,api_auto_review=False)
    calls=[];shown=[]
    def provider(body,key,settings=None,**callbacks):
        calls.append(json.loads(body['messages'][1]['content']))
        return pairing_reply(body),{}
    monkeypatch.setattr(api_client,'request',provider)
    monkeypatch.setattr(dialogs,'show_text',lambda title,text,**kwargs:shown.append(text))
    def consent(box):
        assert box.standardButton(box.defaultButton())==QMessageBox.StandardButton.No
        assert 'https://api.deepseek.com' in box.text()
        assert '字幕与时间轴信息' in box.informativeText() and '消耗接口额度' in box.informativeText()
        box.button(QMessageBox.StandardButton.Yes if approve else QMessageBox.StandardButton.No).click()
    with modal_driver(application,[('textApiConsentDialog',consent)]):
        assert retained_ai_for_test(b,'row.align',{'id':'m30'}) is approve
        if approve:wait_for(application,lambda:not b.state['task']['running'])
    assert len(calls)==int(approve)
    assert p.settings['api_auto_review'] is False
    if approve:
        assert len(calls[0]['current_pairings'])==3
        assert [row['id'] for row in calls[0]['current_pairings'] if not row['context_only']]==['m30']
        assert '1/1' in shown[-1]
    else:
        assert not p.api_records and not b.api_text_consent()


@pytest.mark.parametrize('legacy_auto',[False,True])
def test_native_entering_key_does_not_check_consent_and_preserves_legacy_setting(application,bridges,legacy_auto):
    from desktop_qt.dialogs import ApiSettingsDialog
    from PySide6.QtWidgets import QLabel,QCheckBox
    b=bridges();b.document.project.settings['api_auto_review']=legacy_auto
    dialog=ApiSettingsDialog(b)
    try:
        assert not dialog.enabled.isChecked()
        assert dialog.findChild(QLabel,'ocrManualAiNotice').text()=='AI 核对入口暂时隐藏；保存配置不会启用请求，OCR 完成后也不会自动发送。'
        assert all('识别后自动核对' not in item.text() for item in dialog.findChildren(QCheckBox))
        dialog.key.setText(KEY)
        assert not dialog.enabled.isChecked()
        assert dialog.apply()
        assert b.document.project.settings['api_auto_review'] is legacy_auto
        assert not b.api_text_consent() and not b.document.project.api_records
        from lyricsync.project_document import load_document
        saved=b.storage_dir/'legacy-setting.lyria';b._save(True,str(saved))
        wait_for(application,lambda:b._save_running is None and saved.is_file())
        assert load_document(saved).project.settings['api_auto_review'] is legacy_auto
        dialog.enabled.setChecked(True)
        dialog.provider.setCurrentIndex(1);dialog.url.setText('https://changed.invalid/v1')
        assert not dialog.enabled.isChecked()
    finally:dialog.reject()


def test_production_hidden_ai_dispatch_never_enters_retained_methods(bridges,monkeypatch):
    b=bridges();seed(b);enable(b)
    calls=[]
    monkeypatch.setattr(b,'_api_review',lambda *a,**k:calls.append('review'))
    monkeypatch.setattr(b,'_api_alignment',lambda *a,**k:calls.append('alignment'))
    before=deepcopy(b.document.project.to_dict())
    for name in ('row.align','api.review','alignment.batch','row.referenceTranslation','alignment.forceRetry'):
        assert not b.command(name,{})
        assert '暂时停用' in b.state['error']
    assert calls==[]
    assert b.document.project.to_dict()==before
