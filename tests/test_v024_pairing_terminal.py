import os
os.environ['QT_QPA_PLATFORM']='offscreen'
os.environ['QT_QUICK_BACKEND']='software'
from copy import deepcopy
from functools import partial
import pytest
from lyricsync import pairing_api, api_client
from tests.test_v023_pairing_real_controller import real_pairing, bind_first
from tools.qt_event_loop_wait import wait_until

@pytest.mark.parametrize('mode', ['success', 'network', 'cancel', 'budget'])
def test_real_bridge_pairing_terminal_paths(tmp_path, monkeypatch, mode):
    with real_pairing(tmp_path) as (bridge, open_dialog):
        dialog=open_dialog(); bind_first(dialog)
        original=deepcopy(dialog.session.data)
        monkeypatch.setattr(bridge, '_api_check', lambda: None)
        monkeypatch.setattr(bridge, '_budget_dialog', lambda result: None)
        bridge._api_key='local-test-key-no-network'
        bridge.api_settings.update(api_model='mock', api_retries=0,
                                   api_max_calls=0 if mode=='budget' else 5)
        progress=[]
        bridge.jobs.progressed.connect(lambda identity, value, label: progress.append((value,label)))
        def transport(body,key):
            if mode=='network': raise api_client.TransportFailure('network_error')
            if mode=='cancel': raise InterruptedError('已取消测试请求')
            return {'groups':[{'anchor_ids':[dialog.session.anchors[1].id],
                               'slices':[{'translation_id':'t2','start':0,'end':6}]}]}, {}
        monkeypatch.setattr(pairing_api,'request_json',partial(api_client.request_json,transport=transport))
        dialog.request_ai()
        wait_until(lambda: not bridge._task.get('running') and not dialog._ai_pending)
        assert dialog.revision==bridge.document.revision
        dialog.guard()
        if mode=='success':
            assert dialog.session.data['ai']
            assert progress and all(isinstance(v,(int,float)) for v,label in progress)
        else:
            assert dialog.session.data==original
            assert '仅允许 groups' not in dialog.status.text()
            assert {'network':'network_error','cancel':'已取消','budget':'上限'}[mode] in dialog.status.text()
        assert len(bridge.document.project.api_records)==(0 if mode=='budget' else 1)
        dialog.reject()