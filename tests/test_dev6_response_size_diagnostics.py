"""Offline size-boundary diagnostics; never call a service or retain content."""
import io
import json

import pytest
from lyricsync.api_client import TransportFailure, _safe_diagnostics, diagnostic_message
from lyricsync import api_stream


def event(content):
    return ('data: ' + json.dumps({'choices': [{'delta': {'content': content},
                                              'finish_reason': None}]}) + '\n\n').encode()


def test_event_limit_is_distinguished_without_body(monkeypatch):
    monkeypatch.setattr(api_stream, 'MAX_EVENT_CHARS', 32)
    decoder = api_stream.SSEDecoder(api_stream.ResponseState(1, 2))
    with pytest.raises(TransportFailure) as caught:
        decoder.feed(b'data: ' + b'SECRET_BODY' * 8)
    assert caught.value.code == 'response_too_large'
    assert caught.value.diagnostics['limit_kind'] == 'event_chars'
    assert caught.value.diagnostics['limit_observed'] == 33
    assert caught.value.diagnostics['limit_maximum'] == 32
    assert 'SECRET_BODY' not in json.dumps(caught.value.diagnostics)


def test_content_limit_is_distinguished(monkeypatch):
    monkeypatch.setattr(api_stream, 'MAX_CONTENT_CHARS', 8)
    decoder = api_stream.SSEDecoder(api_stream.ResponseState(1, 2))
    decoder.feed(event('12345'))
    with pytest.raises(TransportFailure) as caught:
        decoder.feed(event('67890'))
    assert caught.value.diagnostics['limit_kind'] == 'content_chars'
    assert caught.value.diagnostics['limit_observed'] == 10
    assert caught.value.diagnostics['limit_maximum'] == 8


def test_stream_limit_is_distinguished(monkeypatch):
    monkeypatch.setattr(api_stream, 'MAX_STREAM_BYTES', 16)
    response = io.BytesIO(b': ping\n\n' * 5)
    with pytest.raises(TransportFailure) as caught:
        api_stream.read_sse(response, api_stream.ResponseState(1, 2))
    assert caught.value.diagnostics['limit_kind'] == 'stream_bytes'
    assert caught.value.diagnostics['limit_observed'] == 17
    assert caught.value.diagnostics['limit_maximum'] == 16


def test_untrusted_limit_labels_and_numbers_are_discarded():
    clean = _safe_diagnostics({'phase': 'read_stream', 'limit_kind': 'SECRET',
                              'limit_observed': float('inf'), 'limit_maximum': 'SECRET'})
    assert clean == {'phase': 'read_stream'}
    message = diagnostic_message({'phase': 'read_stream', 'limit_kind': 'stream_bytes',
                                  'limit_observed': 17, 'limit_maximum': 16})
    assert '17 / 16' in message
    assert '响应累计字节' in message


def test_limit_failure_never_accepts_partial_content(monkeypatch):
    monkeypatch.setattr(api_stream, 'MAX_CONTENT_CHARS', 1)
    decoder = api_stream.SSEDecoder(api_stream.ResponseState(1, 2))
    with pytest.raises(TransportFailure):
        decoder.feed(event('private'))
    assert decoder.content == []
    assert not decoder.done
