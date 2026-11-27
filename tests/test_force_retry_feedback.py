"""No remote traffic: one-window risk-confirmed retry and crash guards."""
from copy import deepcopy
import json
import pytest
from test_api_phase5_windows import fixture, split_first
from lyricsync import api_alignment as alignment
from lyricsync.storage import Cache
from lyricsync.api_client import TransportFailure


def unknown(tmp_path):
    p,index=fixture();cache=Cache(tmp_path/'retry.sqlite');calls=[]
    def provider(body,key):
        calls.append(deepcopy(body))
        if len(calls)==2:raise TransportFailure('read_timeout')
        return json.dumps(split_first(body),ensure_ascii=False),{}
    result=alignment.request_plan(p,index,cache,'offline-key',transport=provider)
    assert len(calls)==2 and result['force_retry_offer']['window']==1
    return p,index,cache,calls,provider,result


def test_confirmed_retry_only_one_window_new_identity_old_budget_and_done_preserved(tmp_path):
    p,index,cache,calls,provider,first=unknown(tmp_path)
    try:
        old=deepcopy(cache.records());offer=deepcopy(first['force_retry_offer'])
        retry=alignment.request_plan(p,index,cache,'offline-key',transport=provider,force_retry=offer)
        assert len(calls)==3 and calls[2]==calls[1]
        assert cache.records()[:2]==old
        assert len(cache.records())==3 and cache.records()[-1]['request_hash']!=old[-1]['request_hash']
        assert retry['coverage']['counted_attempts']==3
        assert len(retry['coverage']['completed_mapping_ids'])==30
        assert retry['coverage']['stop_code']=='retry_scope_complete'
        assert retry['plan']['response']['operations'][0]==first['plan']['response']['operations'][0]
        with pytest.raises(ValueError):alignment.request_plan(p,index,cache,'offline-key',transport=provider,force_retry=offer)
        assert len(calls)==3
        resumed=alignment.request_plan(p,index,cache,'offline-key',transport=provider)
        assert len(calls)==4 and resumed['coverage']['counted_attempts']==4
    finally:cache.close()


def test_failed_forced_retry_requires_new_confirmation_even_after_reopen(tmp_path):
    p,index,cache,calls,provider,first=unknown(tmp_path)
    try:
        offer=first['force_retry_offer']
        def fail(body,key):calls.append(body);raise TransportFailure('read_timeout')
        failed=alignment.request_plan(p,index,cache,'offline-key',transport=fail,force_retry=offer)
        assert len(calls)==3 and failed['force_retry_offer']['request_hash']!=offer['request_hash']
        cache.close();cache=Cache(tmp_path/'retry.sqlite')
        blocked=alignment.request_plan(p,index,cache,'offline-key',transport=fail)
        assert len(calls)==3 and blocked['force_retry_offer']==failed['force_retry_offer']
        with pytest.raises(ValueError):alignment.request_plan(p,index,cache,'offline-key',transport=fail,force_retry=offer)
        assert len(calls)==3
    finally:cache.close()


@pytest.mark.parametrize('change',['text','model','range','offer'])
def test_stale_or_changed_scope_offer_cannot_authorize_new_request(tmp_path,change):
    p,index,cache,calls,provider,first=unknown(tmp_path)
    try:
        args={'force_retry':deepcopy(first['force_retry_offer'])}
        if change=='text':p.mappings[0].text_override='changed'
        if change=='model':args['settings']={'api_model':'another-model'}
        if change=='range':args['mapping_ids']=[m.id for m in p.mappings[:30]]
        if change=='offer':args['force_retry']['targets']=['other']
        with pytest.raises(ValueError):alignment.request_plan(p,index,cache,'offline-key',transport=provider,**args)
        assert len(calls)==2
    finally:cache.close()


def test_cancel_does_not_send_and_budget_is_not_reset(tmp_path):
    p,index,cache,calls,provider,first=unknown(tmp_path)
    try:
        with pytest.raises(InterruptedError):
            alignment.request_plan(p,index,cache,'offline-key',transport=provider,force_retry=first['force_retry_offer'],cancel=lambda:True)
        assert len(calls)==2
        result=alignment.request_plan(p,index,cache,'offline-key',transport=provider,
            force_retry=first['force_retry_offer'],settings={'api_max_calls':2})
        assert len(calls)==2 and len(cache.records())==2
        assert result['coverage']['stop_code']=='budget_exhausted'
        # Even a later larger budget does not silently reuse consumed authorization.
        again=alignment.request_plan(p,index,cache,'offline-key',transport=provider,temporary_maximum=100)
        assert len(calls)==2 and 'force_retry_offer' in again
    finally:cache.close()


def test_crash_after_consumption_before_transport_is_not_replayed(tmp_path,monkeypatch):
    p,index,cache,calls,provider,first=unknown(tmp_path)
    import lyricsync.api_alignment_batches as batches
    original=batches.request_json
    def crash(*args,**kwargs):raise SystemExit('controlled process stop')
    try:
        monkeypatch.setattr(batches,'request_json',crash)
        with pytest.raises(SystemExit):alignment.request_plan(p,index,cache,'offline-key',transport=provider,force_retry=first['force_retry_offer'])
        monkeypatch.setattr(batches,'request_json',original)
        cache.close();cache=Cache(tmp_path/'retry.sqlite')
        result=alignment.request_plan(p,index,cache,'offline-key',transport=provider)
        assert len(calls)==2 and result['coverage']['stop_code']=='unknown_outcome'
    finally:cache.close()


def test_force_ui_decline_stale_context_and_exact_single_dispatch(bridge,monkeypatch):
    from desktop_qt import force_retry_dialog as ui
    offer={'window':1,'targets':['m'],'request_hash':'opaque'}
    calls=[];monkeypatch.setattr(bridge,'_api_alignment',lambda *a,**k:calls.append((a,k)))
    ui.remember(bridge,offer,{'firstNumber':1,'lastNumber':2},None)
    monkeypatch.setattr(ui,'confirm',lambda *a:False)
    ui.request(bridge);assert not calls and ui.available(bridge)
    monkeypatch.setattr(ui,'confirm',lambda *a:True)
    ui.request(bridge);assert len(calls)==1 and calls[0][1]['force_retry']==offer
    with pytest.raises(ValueError):ui.request(bridge)
    ui.remember(bridge,offer,{},None);bridge.document.touch()
    with pytest.raises(ValueError):ui.request(bridge)
    assert len(calls)==1


from test_qt_entry_initialization_dev5 import bridge


def test_duplicate_concurrent_confirmation_cannot_send_twice(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    p,index,cache,calls,provider,first=unknown(tmp_path)
    offer=deepcopy(first['force_retry_offer']);cache.close()
    started=threading.Event();release=threading.Event()
    def slow(body,key):
        calls.append(body);started.set();assert release.wait(5)
        return json.dumps(split_first(body),ensure_ascii=False),{}
    def run():
        local=Cache(tmp_path/'retry.sqlite')
        try:
            return alignment.request_plan(deepcopy(p),index,local,'offline-key',transport=slow,force_retry=deepcopy(offer))
        except ValueError:return 'rejected'
        finally:local.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        one=pool.submit(run);assert started.wait(5);two=pool.submit(run);release.set()
        results=[one.result(8),two.result(8)]
    assert len(calls)==3 and sum(r=='rejected' for r in results)==1


def test_actual_confirmation_defaults_to_cancel_and_names_risk(bridge,monkeypatch):
    from desktop_qt import force_retry_dialog as ui
    from PySide6.QtWidgets import QApplication
    from test_qt_api_review_workflows import modal_driver
    pending={'offer':{'window':1,'targets':['a','b']}}
    def decline(dialog):
        assert '重复计费' in dialog.buttons()[0].text()
        assert '可能再次收费' in dialog.informativeText()
        assert '上限不变' in dialog.informativeText()
        assert dialog.defaultButton().text().startswith('取消')
        assert dialog.escapeButton() is dialog.defaultButton()
        dialog.defaultButton().click()
    with modal_driver(QApplication.instance(),[('unknownResultRetryDialog',decline)]):
        assert ui.confirm(bridge,pending) is False
    def accept(dialog):
        next(b for b in dialog.buttons() if b.text().startswith('接受重复')).click()
    with modal_driver(QApplication.instance(),[('unknownResultRetryDialog',accept)]):
        assert ui.confirm(bridge,pending) is True


def test_bridge_worker_result_ledger_revision_and_retry_entry(bridge,monkeypatch):
    from test_qt_api_review_workflows import seed,enable
    from test_qt_entry_initialization_dev5 import settled
    from desktop_qt import force_retry_dialog as ui
    seed(bridge,2);enable(bridge);calls=[]
    def provider(body,key,settings=None,**kwargs):
        calls.append(deepcopy(body))
        if len(calls)==1:raise TransportFailure('read_timeout')
        return json.dumps({'operations':[],'semantic_issues':[]}),{}
    monkeypatch.setattr('lyricsync.api_client.request',provider)
    monkeypatch.setattr('desktop_qt.api_review_dialogs.show_alignment_plan',lambda *a:None)
    # F47 hides/disables normal dispatch, while retaining the internal engine.
    assert not bridge.command('alignment.batch',{'firstNumber':1,'lastNumber':2})
    assert not calls
    bridge._api_alignment({'firstNumber':1,'lastNumber':2})
    settled(bridge)
    assert len(calls)==1 and bridge.state['alignmentRetryAvailable']
    old=deepcopy(bridge.document.project.api_records)
    monkeypatch.setattr(ui,'confirm',lambda *a:True)
    assert not bridge.command('alignment.forceRetry',{})
    assert len(calls)==1
    ui.request(bridge)  # Explicit offline internal retry/ledger regression.
    settled(bridge)
    assert len(calls)==2 and calls[0]==calls[1]
    assert bridge.document.project.api_records[:1]==old
    assert len(bridge.document.project.api_records)==2
    assert not bridge.state['alignmentRetryAvailable']
    assert not bridge.command('alignment.forceRetry',{})
    with pytest.raises(ValueError):ui.request(bridge)
    assert len(calls)==2


def test_boundary_offer_names_only_actual_targets_and_reuses_primary_results(tmp_path):
    p,index=fixture(18);cache=Cache(tmp_path/'boundary.sqlite');calls=[]
    def provider(body,key):
        calls.append(deepcopy(body))
        if len(calls)==1:return json.dumps(split_first(body),ensure_ascii=False),{}
        if len(calls)==3:raise TransportFailure('read_timeout')
        return json.dumps({'operations':[],'semantic_issues':[]}),{}
    try:
        first=alignment.request_plan(p,index,cache,'offline-key',transport=provider)
        offer=first['force_retry_offer']
        assert offer['kind']=='boundary' and offer['window']==2
        assert 'long_mapping_0' not in offer['targets'] and len(offer['targets'])==17
        done=alignment.request_plan(p,index,cache,'offline-key',transport=provider,force_retry=offer)
        assert len(calls)==4 and calls[-1]==calls[-2]
        assert done['coverage']['completed_windows']==3 and done['coverage']['counted_attempts']==4
    finally:cache.close()


@pytest.mark.parametrize('new_evidence',['cache','quarantine'])
def test_late_cache_or_credential_quarantine_blocks_forced_new_identity(tmp_path,new_evidence):
    from lyricsync.api_client import quarantine_response
    p,index,cache,calls,provider,first=unknown(tmp_path)
    try:
        old_hash=first['force_retry_offer']['request_hash']
        if new_evidence=='cache':cache.put(old_hash,{'operations':[],'semantic_issues':[]})
        else:quarantine_response(cache,old_hash)
        with pytest.raises(ValueError):alignment.request_plan(p,index,cache,'offline-key',transport=provider,force_retry=first['force_retry_offer'])
        assert len(calls)==2 and len(cache.records())==2
    finally:cache.close()
