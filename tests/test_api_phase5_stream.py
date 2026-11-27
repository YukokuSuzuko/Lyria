"""Phase 5: real localhost TCP only; no API keys, accounts or remote calls."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from lyricsync import ai_config, api_client, api_stream
from lyricsync.storage import Cache

BODY = {'model': 'mock-model', 'messages': [{'role': 'user', 'content': 'synthetic'}]}


def event(delta=None, finish=None, **extra):
    return ('data: '+json.dumps({'choices': [{'index': 0, 'delta': delta or {}, 'finish_reason': finish}], **extra},
                               ensure_ascii=False)+'\r\n\r\n').encode('utf-8')


@pytest.fixture
def local_server():
    class Handler(BaseHTTPRequestHandler):
        mode = 'ok'
        calls = 0
        ended = threading.Event()
        entered = threading.Event()
        bodies = []
        def log_message(self, *_): pass
        def do_POST(self):
            type(self).calls += 1
            type(self).bodies.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.send_header('Content-Type', 'application/json' if self.mode == 'json' else 'text/event-stream')
            if self.mode == 'declared_incomplete': self.send_header('Content-Length', '100000')
            self.end_headers()
            self.entered.set()
            try:
                if self.mode == 'blocked':
                    self.ended.wait(2)
                    return
                if self.mode == 'heartbeats':
                    for _ in range(30):
                        self.wfile.write(b': keep-alive\n\n'); self.wfile.flush(); time.sleep(.02)
                    return
                if self.mode == 'json':
                    for _ in range(3):
                        self.wfile.write(b'\n'); self.wfile.flush(); time.sleep(.02)
                    self.wfile.write(json.dumps({'choices': [{'finish_reason': 'stop',
                        'message': {'content': '{"ok":true}', 'reasoning_content': 'PRIVATE_REASONING'}}]}).encode())
                    return
                chunks = [b': ping\r\n\r\n', event({'reasoning_content': 'PRIVATE_REASONING'}),
                          event({'content': '{"text":"e\u0301🙂"}'}), event(finish='stop'), b'data: [DONE]\n\n']
                if self.mode == 'incomplete': chunks = chunks[:-1]
                if self.mode == 'error': chunks = chunks[:2] + [b'event: error\ndata: {"error":"PRIVATE_BODY"}\n\n']
                if self.mode == 'length': chunks = chunks[:2] + [event(finish='length'), b'data: [DONE]\n\n']
                if self.mode == 'slow_first': time.sleep(.15)
                if self.mode == 'oversize': chunks = [b'data: '+b'x'*130000+b'\n\n']
                for chunk in chunks:
                    # Every UTF-8 code point and CRLF may straddle reads.
                    for byte in chunk:
                        self.wfile.write(bytes([byte])); self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    yield Handler, {'api_provider': 'custom', 'api_base_url': f'http://127.0.0.1:{server.server_port}', 'api_transport': 'sse'}
    Handler.ended.set(); server.shutdown(); server.server_close(); worker.join()


def test_sse_utf8_reasoning_keepalive_finish_and_safe_progress(local_server):
    handler, settings = local_server
    seen = []
    value, usage = api_client.request(BODY, 'fake-local-key', settings, on_progress=seen.append)
    assert json.loads(value) == {'text': 'e\u0301🙂'}
    assert seen[-1]['content_chars'] == len(value)
    assert seen[-1]['reasoning_events'] == 1 and seen[-1]['heartbeat_count'] == 1
    assert seen[-1]['http_status'] == 200 and seen[-1]['first_byte_seconds'] >= 0
    assert 'PRIVATE_' not in json.dumps(seen)+value+json.dumps(usage)
    assert handler.bodies[0]['stream'] is True


@pytest.mark.parametrize('mode,code', [('incomplete', 'response_incomplete'), ('error', 'provider_stream_error'),
    ('length', 'response_truncated'), ('declared_incomplete', 'response_incomplete'), ('oversize', 'response_too_large'), ('slow_first', 'read_timeout')])
def test_stream_failure_one_budget_slot_no_candidate_no_secrets(local_server, tmp_path, monkeypatch, mode, code):
    handler, settings = local_server; handler.mode = mode
    if mode == 'slow_first': monkeypatch.setattr(api_client, 'SOCKET_TIMEOUT_SECONDS', .04)
    cache = Cache(tmp_path/'ledger.sqlite')
    try:
        with pytest.raises(api_client.ApiRequestError) as caught:
            api_client.request_json(cache, 'fake-local-key', BODY, api_client.strict_json,
                                    namespace='sse', settings=settings, retries=3)
        assert caught.value.code == code and handler.calls == 1 and len(cache.records()) == 1
        assert cache.db.execute('SELECT COUNT(*) FROM cache').fetchone()[0] == 0
        assert 'PRIVATE_' not in str(caught.value)+json.dumps(caught.value.diagnostics)+str(cache.records())
    finally: cache.close()


def test_json_keepalive_compatibility_when_stream_requested(local_server):
    handler, settings = local_server; handler.mode = 'json'
    raw, _ = api_client.request(BODY, 'fake-local-key', settings)
    assert json.loads(raw) == {'ok': True} and 'PRIVATE_' not in raw


def test_heartbeats_do_not_extend_total_deadline(local_server, monkeypatch):
    handler, settings = local_server; handler.mode = 'heartbeats'
    monkeypatch.setattr(api_stream, 'TOTAL_TIMEOUT_SECONDS', .13)
    monkeypatch.setattr(api_client, 'SOCKET_TIMEOUT_SECONDS', .07)
    started = time.monotonic()
    with pytest.raises(api_client.TransportFailure) as caught:
        api_client.request(BODY, 'fake-local-key', settings)
    assert caught.value.code == 'total_timeout'
    assert caught.value.diagnostics['heartbeat_count'] > 0
    assert caught.value.diagnostics['content_chars'] == 0
    assert time.monotonic()-started < .8


def test_cancel_blocked_read_does_not_wait_for_next_chunk_or_publish_late(local_server, tmp_path):
    handler, settings = local_server; handler.mode = 'blocked'
    stop = threading.Event()
    timer = threading.Thread(target=lambda: (handler.entered.wait(2), time.sleep(.08), stop.set()), daemon=True)
    timer.start(); cache = Cache(tmp_path/'cancel.sqlite'); started = time.monotonic()
    try:
        with pytest.raises(InterruptedError):
            api_client.request_json(cache, 'fake-local-key', BODY, api_client.strict_json,
                namespace='cancel', settings=settings, cancel=stop.is_set)
        assert time.monotonic()-started < 1
        assert len(cache.records()) == 1 and cache.records()[0]['status'] == 'cancelled'
        assert cache.db.execute('SELECT COUNT(*) FROM cache').fetchone()[0] == 0
        handler.ended.set(); timer.join()
    finally: cache.close()


def test_cancel_before_connection_sends_nothing(local_server):
    handler, settings = local_server
    with pytest.raises(InterruptedError):
        api_client.request(BODY, 'fake-local-key', settings, cancel=lambda: True)
    assert handler.calls == 0


def test_header_wait_total_is_bounded_and_late_response_cannot_escape(monkeypatch):
    import io
    release = threading.Event()
    class Response(io.BytesIO):
        headers = {'Content-Type': 'application/json'}
        status = 200
    def slow_open(req):
        release.wait(2)
        return Response(b'{"choices":[{"finish_reason":"stop","message":{"content":"{}"}}]}')
    monkeypatch.setattr(api_client, '_open', slow_open)
    monkeypatch.setattr(api_stream, 'TOTAL_TIMEOUT_SECONDS', .08)
    started = time.monotonic()
    try:
        with pytest.raises(api_client.TransportFailure) as caught:
            api_client.request(BODY, 'fake-local-key')
        assert caught.value.code == 'total_timeout'
        assert caught.value.diagnostics['phase'] == 'connect_send_headers'
        assert time.monotonic()-started < .8
    finally: release.set()


@pytest.mark.parametrize('wire', [b'data: [DONE]\n\n', event({'content': '{}'})+b'data: [DONE]\n\n',
    event({'content': '{}'}, 'stop')+b'data: [DONE]', b'data: \xff\n\n',
    event({'tool_calls': []}), event({'content': '{}'}, 'stop')+event({'content': 'late'})])
def test_reject_incomplete_or_nontext_protocol(wire):
    state = api_stream.ResponseState(40, 180); parser = api_stream.SSEDecoder(state)
    with pytest.raises(api_client.TransportFailure):
        for n in range(0, len(wire), 3): parser.feed(wire[n:n+3])
        parser.finish()


def test_sse_multiline_data_and_cr_framing():
    state = api_stream.ResponseState(40, 180); parser = api_stream.SSEDecoder(state)
    parser.feed(b'data: {"choices":\rdata: [{"index":0,"delta":{"content":"{}"},"finish_reason":"stop"}]}\r\rdata: [DONE]\r\r')
    assert parser.finish()[0] == '{}'


def test_invalid_response_never_retries_even_nonzero_temperature(tmp_path):
    cache = Cache(tmp_path/'bad.sqlite')
    try:
        for temperature in (0, .7):
            with pytest.raises(api_client.ApiRequestError) as caught:
                api_client.request_json(cache, 'fake-local-key', {**BODY, 'temperature': temperature},
                    api_client.strict_json, namespace=str(temperature), transport=lambda *_: ('bad JSON', {}))
            assert caught.value.code == 'invalid_response'
        assert len(cache.records()) == 2
    finally: cache.close()


def test_provider_settings_are_explicit_allowlisted_and_roundtrip(tmp_path):
    default = ai_config.adapt_chat_body({**BODY, 'temperature': 0})
    assert default['stream'] and 'thinking' not in default and 'temperature' not in default
    assert ai_config.DEFAULTS['api_model'] == 'deepseek-flash'
    disabled = ai_config.adapt_chat_body({**BODY, 'temperature': 0}, {'api_thinking': 'disabled'})
    assert disabled['thinking'] == {'type': 'disabled'} and disabled['temperature'] == 0
    custom = ai_config.adapt_chat_body({**BODY, 'temperature': 0}, {'api_provider': 'custom'})
    assert custom['stream'] is False and 'thinking' not in custom and custom['temperature'] == 0
    for effort in ('low', 'high', 'max'):
        settings = ai_config.save(tmp_path, {**ai_config.DEFAULTS, 'api_reasoning_effort': effort, 'api_transport': 'sse'})
        assert ai_config.load(tmp_path) == settings
        assert ai_config.adapt_chat_body(BODY, settings)['reasoning_effort'] == effort
    for bad in ({'api_reasoning_effort': 'unbounded'}, {'api_total_timeout_seconds': 241}, {'api_idle_timeout_seconds': 0}):
        with pytest.raises(ValueError): ai_config.transport_options(bad)


def test_http_status_and_error_body_are_safely_separated(monkeypatch):
    import io
    from urllib.error import HTTPError
    body = io.BytesIO(b'PRIVATE_ERROR_BODY fake-local-key')
    class Opener:
        def open(self, *args, **kwargs):
            raise HTTPError('http://localhost/mock',401,'PRIVATE_ERROR_MESSAGE',{},body)
    monkeypatch.setattr(api_client.urllib.request,'build_opener',lambda *args:Opener())
    with pytest.raises(api_client.TransportFailure) as caught:
        api_client.request(BODY,'fake-local-key',{'api_provider':'custom','api_base_url':'http://localhost'})
    assert caught.value.code=='authentication_failed'
    assert caught.value.diagnostics['http_status']==401 and body.closed
    assert 'PRIVATE_' not in str(caught.value)+json.dumps(caught.value.diagnostics)


def test_expired_lease_owner_cannot_renew_or_remove_replacement(tmp_path,monkeypatch):
    now=[1000.0];monkeypatch.setattr(api_client.time,'time',lambda:now[0])
    cache=Cache(tmp_path/'lease.sqlite')
    try:
        with api_client._claim(cache,'same',lambda:False) as first:
            now[0]+=301
            with api_client._claim(cache,'same',lambda:False) as second:
                with pytest.raises(api_client.ApiRequestError) as caught:first.renew()
                assert caught.value.code=='lease_lost'
                assert cache.db.execute('SELECT owner FROM api_inflight').fetchone()[0]==second.owner
                second.renew()
    finally:cache.close()


def test_expired_pending_concurrent_request_never_reposts_or_publishes_late(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    now=[1000.0];monkeypatch.setattr(api_client.time,'time',lambda:now[0])
    path=tmp_path/'expired.sqlite';Cache(path).close()
    started=threading.Event();release=threading.Event();calls=[]
    def provider(*args):calls.append(1);started.set();assert release.wait(3);return '{}',{}
    def owner():
        cache=Cache(path)
        try:
            return api_client.request_json(cache,'fake-local-key',BODY,api_client.strict_json,namespace='expires',transport=provider)
        finally:cache.close()
    with ThreadPoolExecutor(max_workers=1) as pool:
        first=pool.submit(owner);assert started.wait(3);now[0]+=301;cache=Cache(path)
        try:
            with pytest.raises(api_client.ApiRequestError) as caught:
                api_client.request_json(cache,'fake-local-key',BODY,api_client.strict_json,namespace='expires',transport=provider)
            assert caught.value.code=='unknown_outcome' and calls==[1]
            release.set()
            with pytest.raises(api_client.ApiRequestError) as lost:first.result(timeout=3)
            assert lost.value.code=='lease_lost'
            assert cache.db.execute('SELECT COUNT(*) FROM cache').fetchone()[0]==0
            assert len(cache.records())==1
        finally:release.set();cache.close()


def test_generic_network_error_no_retry_or_restart_replay(tmp_path):
    cache=Cache(tmp_path/'network-unknown.sqlite');calls=[]
    def failure(*args):calls.append(1);raise OSError('PRIVATE_UNKNOWN_NETWORK')
    try:
        for expected in ('network_error','unknown_outcome'):
            with pytest.raises(api_client.ApiRequestError) as caught:
                api_client.request_json(cache,'fake-local-key',BODY,api_client.strict_json,namespace='unknown-net',transport=failure)
            assert caught.value.code==expected and 'PRIVATE_' not in str(caught.value)
        assert calls==[1] and len(cache.records())==1
    finally:cache.close()


def test_takeover_between_renew_and_publish_is_checked_atomically(tmp_path,monkeypatch):
    cache=Cache(tmp_path/'publication.sqlite');calls=[];renewals=[]
    original=api_client.RequestLease.renew
    def replaced(lease):
        original(lease);renewals.append(1)
        if len(renewals)==4:
            cache.db.execute('UPDATE api_inflight SET owner=? WHERE request=?',('replacement-owner',lease.request_hash))
            cache.db.commit()
    monkeypatch.setattr(api_client.RequestLease,'renew',replaced)
    try:
        with pytest.raises(api_client.ApiRequestError) as caught:
            api_client.request_json(cache,'fake-local-key',BODY,api_client.strict_json,namespace='atomic',
                transport=lambda *_:(calls.append(1), ('{}',{}))[1])
        assert caught.value.code=='lease_lost' and calls==[1]
        assert cache.db.execute('SELECT COUNT(*) FROM cache').fetchone()[0]==0
        assert cache.db.execute('SELECT owner FROM api_inflight').fetchone()[0]=='replacement-owner'
    finally:cache.close()
