import json
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
import pytest
from lyricsync import api_client, ai_config
from lyricsync.storage import Cache

@pytest.fixture
def server():
    class Handler(BaseHTTPRequestHandler):
        mode = 'ok'
        calls = 0
        def log_message(self, *args): pass
        def do_POST(self):
            type(self).calls += 1
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.mode == 'stream':
                data = b'data: {"choices":[]}\n\ndata: [DONE]\n\n'
            elif self.mode == 'review':
                from tests.ai_pairing_helpers import pairing_reply
                data=json.dumps({'choices':[{'finish_reason':'length' if self.calls==2 else 'stop','message':{'content':pairing_reply(body)}}]}).encode()
            else:
                data = json.dumps({'choices':[{'finish_reason':'length' if self.mode == 'length' else 'stop', 'message':{'content':'{}'}}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream' if self.mode == 'stream' else 'application/json')
            self.send_header('Content-Length', str(len(data)+(10 if self.mode=='incomplete' else 0)))
            self.end_headers()
            if self.mode=='slow': time.sleep(.15)
            try: self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError): pass
    http = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=http.serve_forever,daemon=True); thread.start()
    yield Handler, dict(api_provider='custom',api_base_url=f'http://127.0.0.1:{http.server_port}')
    http.shutdown(); http.server_close(); thread.join()

BODY = {'model':'local-test', 'messages':[{'role':'user','content':'synthetic test'}]}

def test_local_http_complete(server):
    handler, settings=server
    assert api_client.request(BODY,'synthetic-key',settings)[0]=='{}'

@pytest.mark.parametrize('mode,expected',[('length','response_truncated'),('stream','unexpected_stream')])
def test_local_http_no_blind_retries(server,tmp_path,mode,expected):
    handler, settings=server; handler.mode=mode
    cache=Cache(tmp_path/'calls.sqlite')
    try:
        with pytest.raises(api_client.ApiRequestError) as error:
            api_client.request_json(cache,'synthetic-key',BODY,api_client.strict_json,namespace='test',settings=settings,retries=3)
        assert error.value.code==expected
        assert handler.calls==1, 'deterministic bad response must not repeat paid POST'
    finally: cache.close()

def test_tls_distinguished_from_network():
    from urllib.error import URLError
    assert api_client.error_status(URLError(ssl.SSLCertVerificationError('synthetic'))) == 'tls_error'

@pytest.mark.parametrize("font_size", [9, 14, 20])
def test_settings_small_window_geometry(tmp_path,font_size):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication,QScrollArea,QLabel
    from desktop_qt.dialogs import ApiSettingsDialog
    class Bridge(QObject):
        stateChanged=Signal()
        api_settings=ai_config.DEFAULTS
        document=SimpleNamespace(project=SimpleNamespace(settings={}))
        def api_text_consent(self,config):return False
    app=QApplication.instance() or QApplication([]); bridge=Bridge(); dialog=ApiSettingsDialog(bridge)
    font=dialog.font();font.setPointSize(font_size);dialog.setFont(font)
    dialog.resize(430,360); dialog.show(); app.processEvents()
    scroll=dialog.findChild(QScrollArea); content=scroll.widget()
    controls=[dialog.provider,dialog.name,dialog.url,dialog.model,dialog.key]
    assert all(w.height()>=w.sizeHint().height() for w in controls), [(w.height(),w.sizeHint().height()) for w in controls]
    assert all(a.geometry().bottom()<b.geometry().top() for a,b in zip(controls,controls[1:]))
    dialog.grab().save(str(tmp_path/'api-settings-small.png'))
    dialog.close()

@pytest.mark.parametrize('mode,expected',[('slow','read_timeout'),('incomplete','response_incomplete')])
def test_local_body_failures(server,monkeypatch,mode,expected):
    import urllib.request
    handler,settings=server;handler.mode=mode
    monkeypatch.setattr(api_client,'_open',lambda req:urllib.request.build_opener(api_client.NoRedirect()).open(req,timeout=.05))
    with pytest.raises(api_client.TransportFailure) as error:
        api_client.request(BODY,'synthetic-key',settings)
    assert error.value.code==expected

def test_full_review_preserves_validated_batches_and_resumes(server,tmp_path):
    from copy import deepcopy
    from lyricsync.api import suggest
    from lyricsync.model import Project,Lyric,Translation,Mapping
    handler,settings=server;handler.mode='review'
    p=Project(lyrics=[Lyric(f'l{i}',f'original {i}',i,i+1) for i in range(13)],
        translations=[Translation(f't{i}',f'text {i}') for i in range(13)],
        mappings=[Mapping(f'm{i}',[f't{i}'],[f'l{i}'],start=i,end=i+1) for i in range(13)])
    p.settings.update(api_enabled=True,api_retries=3)
    before=deepcopy(p.mappings);cache=Cache(tmp_path/'review.sqlite')
    try:
        suggest(p,cache,'synthetic-key',settings=settings)
        coverage=p.settings['api_coverage']
        assert (coverage['processed'],coverage['failed'],coverage['pending'])==(6,6,1)
        assert coverage['error']=='response_truncated' and handler.calls==2
        assert p.mappings==before
        suggest(p,cache,'synthetic-key',settings=settings)
        assert p.settings['api_coverage']['status']=='completed'
        assert p.settings['api_coverage']['processed']==13 and handler.calls==4
        assert all(r['status']=='processed' for r in p.settings['api_suggestions'])
        assert p.mappings==before
    finally:cache.close()


def test_connection_phase_timeout_is_safe(monkeypatch):
    from urllib.error import URLError
    class Opener:
        def open(self,*args,**kwargs):raise URLError(TimeoutError('synthetic secret must not leak'))
    monkeypatch.setattr(api_client.urllib.request,'build_opener',lambda *args:Opener())
    with pytest.raises(api_client.TransportFailure) as error:
        api_client.request(BODY,'synthetic-key')
    assert error.value.code=='connection_timeout'
    assert 'secret' not in str(error.value)
