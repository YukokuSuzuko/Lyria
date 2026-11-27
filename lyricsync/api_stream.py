"""Bounded SSE decoding and cancellable HTTP-body supervision.

No reasoning text is retained in the accumulated output, diagnostics or logs.
The network worker owns its response. A stop shuts down an exposed socket;
DNS/TLS/header acquisition may finish later under urllib's own timeout. A stop
is not proof that the provider cancelled generation or waived a charge.
"""
from __future__ import annotations

import codecs
import http.client
import queue
import socket
import ssl
import threading
import time

MAX_STREAM_BYTES = 2_000_000
MAX_EVENT_CHARS = 128_000
MAX_CONTENT_CHARS = 100_000
TOTAL_TIMEOUT_SECONDS = 180


class ResponseState:
    def __init__(self, idle, total):
        self.started = time.monotonic()
        self.phase_started = self.started
        self.phase = 'connect_send_headers'
        self.idle, self.total = idle, total
        self.received = self.content_chars = self.reasoning_events = self.heartbeats = 0
        self.first_byte = None
        self.status = self.expected = None
        self.response = None
        self.stopped = threading.Event()

    def attach(self, response):
        self.response = response
        self.phase = 'read_body'
        self.phase_started = time.monotonic()
        status = getattr(response, 'status', None)
        self.status = status if type(status) is int and 100 <= status <= 599 else None
        length = getattr(response, 'headers', {}).get('Content-Length', '')
        if isinstance(length, str) and length.isascii() and length.isdecimal() and len(length) <= 15:
            self.expected = int(length)
        if self.stopped.is_set():
            self.abort()
            raise InterruptedError('已停止接收接口响应')

    def received_chunk(self, chunk):
        self.received += len(chunk)
        if self.first_byte is None:
            self.first_byte = time.monotonic() - self.started
        if self.stopped.is_set():
            raise InterruptedError('已停止接收接口响应')

    def diagnostic(self, phase=None):
        return dict(phase=phase or self.phase,
                    elapsed_seconds=round(time.monotonic() - self.phase_started, 3),
                    total_elapsed_seconds=round(time.monotonic() - self.started, 3),
                    received_bytes=self.received, expected_bytes=self.expected,
                    socket_timeout_seconds=self.idle, total_timeout_seconds=self.total,
                    http_status=self.status, first_byte_seconds=self.first_byte,
                    content_chars=self.content_chars, reasoning_events=self.reasoning_events,
                    heartbeat_count=self.heartbeats)

    def abort(self):
        self.stopped.set()
        # close() may wait on BufferedReader's lock. shutdown wakes a blocked
        # read without taking it; the owner thread then closes the response.
        try:
            sock = self.response.fp.raw._sock
            if isinstance(sock, socket.socket):
                sock.shutdown(socket.SHUT_RDWR)
        except (AttributeError, OSError, ValueError):
            pass


def supervised(operation, state, cancel=lambda: False, on_progress=lambda data: None):
    """Bound caller wait; never let abandoned workers publish partial results."""
    from .api_client import TransportFailure
    if cancel():
        raise InterruptedError('已取消 AI 请求')
    result = queue.Queue(maxsize=1)
    def run():
        try:
            result.put((True, operation()))
        except BaseException as exc:
            result.put((False, exc))
    worker = threading.Thread(target=run, name='lyria-api-transport', daemon=True)
    worker.start()
    next_progress = 0.0
    try:
        while True:
            if cancel():
                raise InterruptedError('已取消 AI 请求')
            now = time.monotonic()
            if now - state.started >= state.total:
                raise TransportFailure('total_timeout', state.diagnostic())
            if now >= next_progress:
                on_progress(state.diagnostic())
                next_progress = now + .2
            try:
                ok, value = result.get(timeout=min(.05, max(.001, state.total - (now - state.started))))
            except queue.Empty:
                continue
            if cancel():
                raise InterruptedError('已取消，本次返回结果未采用')
            if ok:
                on_progress(state.diagnostic())
                return value
            raise value
    finally:
        state.abort()
        # Do not block on DNS/headers or a foreign adapter. Late worker results
        # stay in its private queue and cannot write cache, ledger or candidates.


class SSEDecoder:
    """Strict UTF-8, SSE CR/LF framing, one text choice and explicit completion."""
    def __init__(self, state):
        self.state = state
        self.decoder = codecs.getincrementaldecoder('utf-8')('strict')
        self.line = ''
        self.after_cr = False
        self.data = []
        self.data_chars = 0
        self.event = ''
        self.finished = self.done = False
        self.content = []
        self.usage = {}

    def fail(self, code='invalid_response', *, limit_kind=None, observed=None, maximum=None):
        from .api_client import TransportFailure
        diagnostic = self.state.diagnostic('read_stream')
        if limit_kind is not None:
            diagnostic.update(limit_kind=limit_kind, limit_observed=observed, limit_maximum=maximum)
        raise TransportFailure(code, diagnostic)

    def feed(self, chunk):
        try:
            text = self.decoder.decode(chunk)
        except UnicodeError:
            self.fail()
        for char in text:
            if self.after_cr:
                self.after_cr = False
                if char == '\n':
                    continue
            if char in '\r\n':
                self.consume_line(self.line)
                self.line = ''
                self.after_cr = char == '\r'
            else:
                self.line += char
                if len(self.line) + self.data_chars > MAX_EVENT_CHARS:
                    self.fail('response_too_large', limit_kind='event_chars',
                              observed=len(self.line) + self.data_chars, maximum=MAX_EVENT_CHARS)

    def consume_line(self, line):
        if not line:
            self.dispatch()
            return
        if line.startswith(':'):
            self.state.heartbeats += 1
            return
        name, sep, value = line.partition(':')
        if value.startswith(' '):
            value = value[1:]
        if name == 'data':
            self.data.append(value)
            self.data_chars += len(value) + 1
        elif name == 'event':
            self.event = value
        # SSE id/retry and unknown fields have no application semantics.

    def dispatch(self):
        from .api_client import strict_json, safe_usage
        if not self.data:
            self.event = ''
            return
        text = '\n'.join(self.data)
        event = self.event
        self.data, self.data_chars, self.event = [], 0, ''
        if self.done:
            self.fail()
        if event == 'error':
            self.fail('provider_stream_error')
        if text == '[DONE]':
            if not self.finished:
                self.fail('response_incomplete')
            self.done = True
            return
        try:
            value = strict_json(text, maximum=MAX_EVENT_CHARS)
            if not isinstance(value, dict):
                self.fail()
            if 'error' in value:
                self.fail('provider_stream_error')
            choices = value.get('choices')
            if not isinstance(choices, list) or len(choices) > 1:
                self.fail()
            if 'usage' in value:
                self.usage = safe_usage(value['usage'])
            if not choices:
                if not self.finished or 'usage' not in value:
                    self.fail()
                return
            choice = choices[0]
            if not isinstance(choice, dict) or choice.get('index', 0) != 0:
                self.fail()
            delta = choice.get('delta')
            if not isinstance(delta, dict) or set(delta) - {'role', 'content', 'reasoning_content'}:
                self.fail()
            if delta.get('role', 'assistant') != 'assistant':
                self.fail()
            if self.finished:
                self.fail()
            content, reasoning = delta.get('content'), delta.get('reasoning_content')
            if content is not None:
                if not isinstance(content, str):
                    self.fail()
                self.state.content_chars += len(content)
                if self.state.content_chars > MAX_CONTENT_CHARS:
                    self.fail('response_too_large', limit_kind='content_chars',
                              observed=self.state.content_chars, maximum=MAX_CONTENT_CHARS)
                self.content.append(content)
            if reasoning is not None:
                if not isinstance(reasoning, str):
                    self.fail()
                self.state.reasoning_events += 1
                # Deliberately do not accumulate or expose reasoning text.
            finish = choice.get('finish_reason')
            if finish == 'length':
                self.fail('response_truncated')
            if finish is not None:
                if finish != 'stop':
                    self.fail()
                self.finished = True
        except (ValueError, KeyError, TypeError, IndexError):
            self.fail()

    def finish(self):
        try:
            self.decoder.decode(b'', final=True)
        except UnicodeError:
            self.fail('response_incomplete')
        if self.line or self.data or not self.done or not self.finished:
            self.fail('response_incomplete')
        return ''.join(self.content), self.usage


def read_sse(response, state):
    from .api_client import TransportFailure
    parser = SSEDecoder(state)
    state.phase = 'read_stream'
    read = getattr(response, 'read1', response.read)
    try:
        while True:
            chunk = read(min(16384, MAX_STREAM_BYTES + 1 - state.received))
            if not chunk:
                break
            state.received_chunk(chunk)
            if state.received > MAX_STREAM_BYTES:
                parser.fail('response_too_large', limit_kind='stream_bytes',
                            observed=state.received, maximum=MAX_STREAM_BYTES)
            parser.feed(chunk)
            if parser.done and (state.expected is None or state.received >= state.expected):
                # [DONE] is the protocol terminal marker; don't wait for a
                # keep-alive HTTP connection to close. Trailing data in this
                # already received chunk has still been checked by feed().
                return parser.finish()
    except TimeoutError:
        raise TransportFailure('read_timeout', state.diagnostic()) from None
    except ssl.SSLError:
        raise TransportFailure('tls_error', state.diagnostic()) from None
    except http.client.IncompleteRead as exc:
        state.received += len(exc.partial)
        raise TransportFailure('response_incomplete', state.diagnostic()) from None
    except (http.client.HTTPException, ConnectionError):
        raise TransportFailure('response_incomplete', state.diagnostic()) from None
    if state.expected is not None and state.received < state.expected:
        parser.fail('response_incomplete')
    return parser.finish()
