"""Text-only provider transport and persistent, bounded request coordination.

Each worker owns its Cache connection. SQLite leases deduplicate simultaneous
identical requests across connections/processes; every network attempt reserves
one persistent budget slot before transmission. No credential enters a hash,
cache, status message, usage record, project or log.
"""
from __future__ import annotations

import json
import math
import ssl
import sqlite3
import http.client
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager

from . import ai_config


class IncompleteResponse(ValueError):
    pass


SOCKET_TIMEOUT_SECONDS = 40
MAX_RESPONSE_BYTES = 500000


def _safe_diagnostics(value):
    """Only fixed phase labels and finite numeric measurements may escape."""
    if not isinstance(value, dict):
        return {}
    result = {}
    if value.get('phase') in ('connect_send_headers', 'read_body', 'parse_json', 'validate_envelope', 'read_stream'):
        result['phase'] = value['phase']
    for name in ('elapsed_seconds', 'received_bytes', 'expected_bytes', 'socket_timeout_seconds',
                 'total_elapsed_seconds', 'total_timeout_seconds', 'http_status',
                 'first_byte_seconds', 'content_chars', 'reasoning_events', 'heartbeat_count',
                 'limit_observed', 'limit_maximum'):
        number = value.get(name)
        if type(number) in (int, float) and 0 <= number <= 10**15 and math.isfinite(number):
            result[name] = number
    if value.get('limit_kind') in ('stream_bytes', 'event_chars', 'content_chars'):
        result['limit_kind'] = value['limit_kind']
    return result


def diagnostic_message(diagnostics):
    """Short safe transport detail for callers; never render unknown fields."""
    data = _safe_diagnostics(diagnostics)
    phases = {
        'connect_send_headers': '\u8fde\u63a5\u3001\u53d1\u9001\u6216\u7b49\u5f85\u54cd\u5e94\u5934',
        'read_stream': '读取 SSE 响应',
        'read_body': '\u8bfb\u53d6\u54cd\u5e94\u6b63\u6587',
        'parse_json': '\u89e3\u6790\u54cd\u5e94 JSON',
        'validate_envelope': '\u6821\u9a8c\u54cd\u5e94\u7ed3\u6784',
    }
    if 'phase' not in data:
        return ''
    parts = [phases[data['phase']]]
    if 'elapsed_seconds' in data:
        parts.append('\u9636\u6bb5\u8017\u65f6 %.2f \u79d2' % data['elapsed_seconds'])
    if 'received_bytes' in data:
        parts.append('\u5df2\u6536\u5230 %s \u5b57\u8282' % data['received_bytes'])
    if 'expected_bytes' in data:
        parts.append('\u54cd\u5e94\u5934\u58f0\u660e %s \u5b57\u8282' % data['expected_bytes'])
    if 'socket_timeout_seconds' in data:
        parts.append('socket \u7a7a\u95f2\u8d85\u65f6 %s \u79d2\uff08\u975e\u603b\u65f6\u9650\uff09' % data['socket_timeout_seconds'])
    for name, label in (('total_timeout_seconds', '请求总时限（秒）'), ('http_status', 'HTTP状态'),
                        ('first_byte_seconds', '首块等待（秒）'), ('content_chars', '已接收内容字符'),
                        ('reasoning_events', '思考活动块'), ('heartbeat_count', '保活事件')):
        if name in data: parts.append(label + ' ' + str(data[name]))
    if 'limit_kind' in data:
        label = {'stream_bytes': '响应累计字节', 'event_chars': '单个传输事件字符',
                 'content_chars': '核对正文字符'}[data['limit_kind']]
        parts.append('触发限制：' + label + ' ' + str(data.get('limit_observed', '?'))
                     + ' / ' + str(data.get('limit_maximum', '?')))
    return '\uff1b'.join(parts)


class TransportFailure(RuntimeError):
    """Safe phase-only diagnostic; never retain provider bodies or credentials."""
    def __init__(self, code, diagnostics=None):
        self.code = code
        self.diagnostics = _safe_diagnostics(diagnostics)
        super().__init__(code)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('接口地址发生了重定向，已拦截请求以保护密钥安全，请检查 Base URL')


class BudgetLimit(RuntimeError):
    def __init__(self, cache, maximum, request_hash):
        used = len(cache.records())
        self.state = {
            'status': 'budget_exhausted', 'used': used, 'limit': maximum,
            'resumable': True, 'request_hash': request_hash,
            'choices': ([{'action': 'continue_once', 'maximum': 100}]
                        if maximum < 100 and used < 100 else []) + [{'action': 'later'}],
            'message': '已达本工程 API 调用次数上限（含失败与重试），已完成的结果已保留。',
        }
        super().__init__(self.state['message'])


ERROR_HELP = {
    'credential_echo': '服务返回内容包含密钥信息，出于安全考虑已丢弃并清理缓存，请检查接口配置。',
    'local_storage_busy': '本地缓存数据库正忙，请求已中止，请稍后重试。',
    'lease_lost': '当前请求已被新任务接管，已完成的结果已保留。',
    'unknown_outcome': '本批结果未知，为避免重复计费未重发；已完成范围可复用，请先核对服务记录；仅在明确接受重复计费风险后才可单独重发本批。',
    'total_timeout': '请求超过总时长限制，为避免重复计费未自动重试，已完成进度已保留。',
    'response_too_large': '响应超过安全接收上限，本次结果未采用且不会自动重试。请导出诊断，核对具体限制后再决定是否重试。',
    'provider_stream_error': '服务商传输中途报错，未采用不完整结果，请稍后重试。',
    'tls_error': '安全连接（TLS/证书）校验失败，请检查接口地址、网络代理或系统证书设置。',
    'connection_timeout': '连接服务超时，为避免重复计费未自动重试，请检查网络后重试。',
    'read_timeout': '接收响应超时，为避免重复计费未自动重试，已完成进度已保留。',
    'response_incomplete': '响应传输中断，本批未生效，已完成进度已保留。',
    'unexpected_stream': '接口返回格式不兼容（需要标准 JSON 响应），请检查自定义服务商接口设置。',
    'authentication_failed': '密钥无效或已失效，请检查保存的密钥。',
    'access_denied': '服务商拒绝访问，请检查账号状态或模型权限。',
    'endpoint_or_model_not_found': '服务或模型不存在，请核对 Base URL 或手动填写模型 ID。',
    'rate_or_quota_limit': '请求过于频繁或账户余额不足，请检查额度或稍后重试。',
    'timeout': '请求超时，请稍后重试。',
    'invalid_response': '模型返回格式异常，无法解析，请重试或更换模型。',
    'response_truncated': '模型返回内容超长被截断，请缩小单次核对范围。',
    'network_error': '未能连接服务，请检查网络、代理和防火墙。',
    'server_error': '服务商接口异常，请稍后重试。',
    'retry_limit_reached': '重试次数已用完，已完成进度已保留，请检查网络或配置后再试。',
}

# An unknown transport outcome is not permission to repeat a possibly billed POST.
ERROR_HELP['timeout'] = '请求超时。' + ERROR_HELP['unknown_outcome']
ERROR_HELP['network_error'] = ERROR_HELP['unknown_outcome']


def error_status(exc):
    if isinstance(exc, (TransportFailure, ApiRequestError)):
        return exc.code
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, ssl.SSLError):
        return 'tls_error'
    if isinstance(exc, IncompleteResponse):
        return 'response_truncated'
    if isinstance(exc, urllib.error.HTTPError):
        return {401: 'authentication_failed', 403: 'access_denied',
                404: 'endpoint_or_model_not_found', 429: 'rate_or_quota_limit'}.get(
            exc.code, 'server_error' if exc.code >= 500 else 'http_' + str(exc.code))
    if isinstance(exc, TimeoutError) or (isinstance(exc, urllib.error.URLError)
                                        and isinstance(exc.reason, TimeoutError)):
        return 'timeout'
    if isinstance(exc, (ValueError, KeyError, TypeError, IndexError)):
        return 'invalid_response'
    return 'network_error'


class ApiRequestError(RuntimeError):
    def __init__(self, code, diagnostics=None):
        self.code = code
        self.diagnostics = _safe_diagnostics(diagnostics)
        super().__init__(code + '：' + ERROR_HELP.get(code, '请求失败，请稍后重试。'))


def strict_json(value, maximum=100000):
    if isinstance(value, str):
        if len(value) > maximum:
            raise ValueError('AI 返回内容过长')
        def unique(pairs):
            obj = {}
            for key, item in pairs:
                if key in obj:
                    raise ValueError('AI 返回的 JSON 包含重复字段')
                obj[key] = item
            return obj
        def bad_constant(_):
            raise ValueError('AI 返回的 JSON 包含无效数值')
        return json.loads(value, object_pairs_hook=unique, parse_constant=bad_constant)
    # Dict transports are convenient for local mocks; disallow NaN there too.
    json.dumps(value, allow_nan=False)
    return value



def contains_credential(value, key):
    """Inspect decoded strings, never JSON escapes. Short test/custom keys only
    match whole strings, avoiding one-character substrings in ordinary text.
    """
    if not isinstance(key, str) or not key:
        return False
    pending, seen = [value], set()
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if item == key or (len(key) >= 8 and key in item):
                return True
        elif isinstance(item, (dict, list, tuple)) and id(item) not in seen:
            seen.add(id(item))
            if isinstance(item, dict):
                pending.extend(item.keys()); pending.extend(item.values())
            else:
                pending.extend(item)
    return False


def reject_credential_echo(value, key):
    if contains_credential(value, key):
        raise TransportFailure('credential_echo')


def _db_execute(cache, sql, parameters=(), *, cancel=lambda: False, wait_seconds=5):
    """Only BUSY/LOCKED are retried, bounded, with connection-local short waits.

    Check cancel after any busy wait. Callers also check ownership/publication
    boundaries. Available writes may still record cancellation. Restore PRAGMA.
    """
    original = cache.db.execute('PRAGMA busy_timeout').fetchone()[0]
    cache.db.execute('PRAGMA busy_timeout=25')
    deadline = time.monotonic() + wait_seconds
    try:
        while True:
            try:
                return cache.db.execute(sql, parameters)
            except sqlite3.OperationalError as exc:
                if (getattr(exc, 'sqlite_errorcode', 0) & 255) not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                    raise
                if cancel():
                    raise InterruptedError('已取消 AI 请求') from None
                if time.monotonic() >= deadline:
                    raise TransportFailure('local_storage_busy') from None
                time.sleep(.01)
    finally:
        cache.db.execute('PRAGMA busy_timeout=%d' % original)



def quarantine_response(cache, request_hash, cancel=lambda: False):
    """Remove a known unsafe response and persist a non-billable stop marker."""
    _db_execute(cache, 'CREATE TABLE IF NOT EXISTS api_unsafe_results (request TEXT PRIMARY KEY)', cancel=cancel)
    cache.db.commit()
    _db_execute(cache, 'BEGIN IMMEDIATE', cancel=cancel)
    try:
        cache.db.execute('DELETE FROM cache WHERE key=?', (request_hash,))
        cache.db.execute('INSERT OR IGNORE INTO api_unsafe_results VALUES (?)', (request_hash,))
        cache.db.commit()
    except BaseException:
        cache.db.rollback(); raise


def response_quarantined(cache, request_hash):
    exists = cache.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='api_unsafe_results'").fetchone()
    return bool(exists and cache.db.execute('SELECT 1 FROM api_unsafe_results WHERE request=?', (request_hash,)).fetchone())

def _finish_call(cache, call, status):
    # Cleanup must not mask cancellation or wait for another 30-second writer.
    # A still-pending reservation remains unknown, never replayable.
    try:
        _db_execute(cache, 'UPDATE calls SET status=?,usage=? WHERE id=?',
                    (status, '{}', call), wait_seconds=.05)
        cache.db.commit()
    except TransportFailure as exc:
        cache.db.rollback()
        if exc.code != 'local_storage_busy': raise

def _read_json(response, state=None):
    started = time.monotonic()
    headers = getattr(response, 'headers', {})
    expected = headers.get('Content-Length', '')
    # Ignore malformed or absurd header lengths; never copy raw headers.
    expected = int(expected) if isinstance(expected, str) and expected.isascii() and expected.isdecimal() and len(expected) <= 15 else None
    received = 0
    if state is not None: state.expected = expected
    def diagnostic(phase):
        if state is not None: return state.diagnostic(phase)
        return dict(phase=phase, elapsed_seconds=round(time.monotonic()-started, 3),
                    received_bytes=received, expected_bytes=expected,
                    socket_timeout_seconds=SOCKET_TIMEOUT_SECONDS)
    content_type = headers.get('Content-Type', '')
    if isinstance(content_type, str) and 'text/event-stream' in content_type.lower():
        raise TransportFailure('unexpected_stream', diagnostic('read_body'))
    chunks = []
    # HTTPResponse.read(n) can lose already-consumed bytes on a later timeout.
    # read1 returns each available portion, without changing the socket timeout.
    incremental = callable(getattr(type(response), 'read1', None))
    read_chunk = response.read1 if incremental else response.read
    try:
        while received <= MAX_RESPONSE_BYTES:
            chunk = read_chunk(min(65536, MAX_RESPONSE_BYTES + 1 - received)
                               if incremental else MAX_RESPONSE_BYTES + 1)
            if not chunk:
                break
            chunks.append(chunk)
            received += len(chunk)
            if state is not None: state.received_chunk(chunk)
            if not incremental:
                break  # Preserve the legacy file-like read(n) adapter contract.
    except TimeoutError:
        raise TransportFailure('read_timeout', diagnostic('read_body')) from None
    except ssl.SSLError:
        raise TransportFailure('tls_error', diagnostic('read_body')) from None
    except http.client.IncompleteRead as exc:
        received += len(exc.partial)
        raise TransportFailure('response_incomplete', diagnostic('read_body')) from None
    except (http.client.HTTPException, ConnectionError):
        raise TransportFailure('response_incomplete', diagnostic('read_body')) from None
    if received > MAX_RESPONSE_BYTES:
        raise TransportFailure('invalid_response', diagnostic('read_body'))
    if expected is not None and received < expected:
        raise TransportFailure('response_incomplete', diagnostic('read_body'))
    try:
        return strict_json(b''.join(chunks).decode('utf8'), maximum=MAX_RESPONSE_BYTES)
    except (ValueError, TypeError):
        raise TransportFailure('invalid_response', diagnostic('parse_json')) from None


def _open(req):
    started = time.monotonic()
    def diagnostic():
        # urllib combines connect, TLS, send and response-header wait. Do not
        # claim to know whether a timed-out POST was delivered or billed.
        return dict(phase='connect_send_headers',
                    elapsed_seconds=round(time.monotonic()-started, 3),
                    socket_timeout_seconds=getattr(req, '_lyria_timeout', SOCKET_TIMEOUT_SECONDS))
    try:
        return urllib.request.build_opener(NoRedirect()).open(req, timeout=getattr(req, '_lyria_timeout', SOCKET_TIMEOUT_SECONDS))
    except urllib.error.HTTPError as exc:
        status = error_status(exc)
        data = diagnostic(); data['http_status'] = exc.code
        exc.close()
        raise TransportFailure(status, data) from None
    except (TimeoutError, urllib.error.URLError) as exc:
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        if isinstance(reason, TimeoutError):
            raise TransportFailure('connection_timeout', diagnostic()) from None
        if isinstance(reason, ssl.SSLError):
            raise TransportFailure('tls_error', diagnostic()) from None
        if isinstance(reason, ConnectionError):
            raise TransportFailure('response_incomplete', diagnostic()) from None
        raise
    except ssl.SSLError:
        raise TransportFailure('tls_error', diagnostic()) from None
    except (http.client.HTTPException, ConnectionError):
        raise TransportFailure('response_incomplete', diagnostic()) from None


def request(body, key, settings=None, *, cancel=lambda: False, on_progress=lambda data: None):
    """Text-only ChatCompletions, explicit protocol, no redirects/tools/media."""
    from .api_stream import ResponseState, supervised, read_sse, TOTAL_TIMEOUT_SECONDS
    body = ai_config.adapt_chat_body(body, settings)
    allowed = {'model', 'messages', 'response_format', 'temperature', 'max_tokens',
               'stream', 'thinking', 'reasoning_effort'}
    if not isinstance(body, dict) or set(body) - allowed:
        raise ValueError('AI 请求参数格式不受支持')
    if (not isinstance(body.get('messages'), list) or not body['messages'] or
            any(not isinstance(m, dict) or set(m) != {'role', 'content'} or
                m['role'] not in ('system', 'user', 'assistant') or
                not isinstance(m['content'], str) for m in body['messages'])):
        raise ValueError('发送给 AI 的消息内容格式无效')
    if type(body.get('stream')) is not bool:
        raise ValueError('流式传输（stream）选项格式无效')
    official = (settings or {}).get('api_provider', 'deepseek') == 'deepseek'
    if 'thinking' in body and (not official or body['thinking'] not in
                              ({'type': 'enabled'}, {'type': 'disabled'})):
        raise ValueError('当前服务商不支持该思考模式参数')
    if 'reasoning_effort' in body and (not official or body['reasoning_effort'] not in ('low', 'high', 'max')):
        raise ValueError('当前服务商不支持该思考深度选项')
    url = ai_config.base_url(settings or {}) + '/chat/completions'
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False, allow_nan=False).encode(),
          headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, method='POST')
    idle = (settings or {}).get('api_idle_timeout_seconds', SOCKET_TIMEOUT_SECONDS)
    total = (settings or {}).get('api_total_timeout_seconds', TOTAL_TIMEOUT_SECONDS)
    state = ResponseState(idle, total)
    req._lyria_timeout = min(idle, total)
    def exchange():
        with _open(req) as response:
            state.attach(response)
            content_type = getattr(response, 'headers', {}).get('Content-Type', '')
            if isinstance(content_type, str) and 'text/event-stream' in content_type.lower():
                if not body['stream']:
                    raise TransportFailure('unexpected_stream', state.diagnostic())
                return read_sse(response, state)
            envelope = _read_json(response, state)
        try:
            choice = envelope['choices'][0]
            if choice.get('finish_reason') == 'length':
                raise TransportFailure('response_truncated', state.diagnostic('validate_envelope'))
            if choice.get('finish_reason') != 'stop' or not isinstance(choice['message']['content'], str):
                raise ValueError('AI 回复未完整结束（可能因长度限制被截断）')
            state.content_chars = len(choice['message']['content'])
            return choice['message']['content'], safe_usage(envelope.get('usage', {}))
        except (ValueError, KeyError, TypeError, IndexError):
            raise TransportFailure('invalid_response', state.diagnostic('validate_envelope')) from None
    return supervised(exchange, state, cancel, on_progress)


def request_models(body, key, settings=None, *, cancel=lambda: False):
    """GET model IDs with the same total/cancellation body protection."""
    from .api_stream import ResponseState, supervised, TOTAL_TIMEOUT_SECONDS
    ai_config.transport_options(settings or {})
    req = urllib.request.Request(ai_config.base_url(settings or {}) + '/models',
                                 headers={'Authorization': 'Bearer ' + key}, method='GET')
    idle = (settings or {}).get('api_idle_timeout_seconds', SOCKET_TIMEOUT_SECONDS)
    total = (settings or {}).get('api_total_timeout_seconds', TOTAL_TIMEOUT_SECONDS)
    req._lyria_timeout = min(idle, total)
    state = ResponseState(idle, total)
    def exchange():
        with _open(req) as response:
            state.attach(response)
            return _read_json(response, state), {}
    return supervised(exchange, state, cancel)


def safe_usage(usage):
    allowed = {'prompt_tokens', 'completion_tokens', 'total_tokens',
               'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens'}
    if not isinstance(usage, dict):
        return {}
    return {k: v for k, v in usage.items() if k in allowed and type(v) in (int, float)
            and 0 <= v <= 10**12 and math.isfinite(v)}


class RequestLease:
    """Only its owner may renew a lease or publish the corresponding result."""
    def __init__(self, cache, request_hash, owner, cancel=lambda: False):
        self.cache, self.request_hash, self.owner = cache, request_hash, owner
        self.cancel = cancel

    def assert_owner(self):
        row = self.cache.db.execute('SELECT owner FROM api_inflight WHERE request=?',
                                    (self.request_hash,)).fetchone()
        if row is None or row[0] != self.owner:
            raise ApiRequestError('lease_lost')

    def renew(self):
        cursor = _db_execute(self.cache,
            'UPDATE api_inflight SET expires=? WHERE request=? AND owner=?',
            (time.time() + 300, self.request_hash, self.owner), cancel=self.cancel)
        self.cache.db.commit()
        if cursor.rowcount != 1:
            raise ApiRequestError('lease_lost')


@contextmanager
def _claim(cache, request_hash, cancel):
    """Do not hold a DB transaction while waiting on a network response."""
    owner = uuid.uuid4().hex
    _db_execute(cache, 'CREATE TABLE IF NOT EXISTS api_inflight '
                     '(request TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL)', cancel=cancel)
    cache.db.commit()
    while True:
        if cancel():
            raise InterruptedError('已取消 AI 请求，已完成的结果已保留')
        _db_execute(cache, 'BEGIN IMMEDIATE', cancel=cancel)
        try:
            if cancel(): raise InterruptedError('已取消 AI 请求')
            row = cache.db.execute('SELECT owner,expires FROM api_inflight WHERE request=?',
                                   (request_hash,)).fetchone()
            if row is None or row[1] < time.time():
                cache.db.execute('INSERT OR REPLACE INTO api_inflight VALUES (?,?,?)',
                                 (request_hash, owner, time.time() + 300))
                cache.db.commit()
                break
            cache.db.rollback()
        except BaseException:
            cache.db.rollback()
            raise
        time.sleep(.025)
    try:
        if cancel(): raise InterruptedError('已取消 AI 请求')
        yield RequestLease(cache, request_hash, owner, cancel)
    finally:
        try:
            _db_execute(cache, 'DELETE FROM api_inflight WHERE request=? AND owner=?',
                        (request_hash, owner), wait_seconds=.05)
            cache.db.commit()
        except TransportFailure as exc:
            cache.db.rollback()
            if exc.code != 'local_storage_busy': raise  # Lease can expire safely.


def _reserve_attempt(cache, request_hash, maximum, spent, attempt_limit, cancel=lambda: False):
    """Budget slot and retry progress are one crash-safe SQLite transaction."""
    _db_execute(cache, 'BEGIN IMMEDIATE', cancel=cancel)
    try:
        if cancel(): raise InterruptedError('已取消 AI 请求')
        count = cache.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]
        if count >= maximum:
            cache.db.execute('INSERT OR REPLACE INTO api_request_progress VALUES (?,?,?)',
                             (request_hash, spent, attempt_limit))
            cache.db.commit()
            limit = BudgetLimit(cache, maximum, request_hash)
            limit.state.update(attempts_spent=spent, remaining_attempts=attempt_limit-spent)
            raise limit
        cursor = cache.db.execute('INSERT INTO calls(stamp,request,status,usage) VALUES (?,?,?,?)',
                                  (time.time(), request_hash, 'pending', '{}'))
        cache.db.execute('INSERT OR REPLACE INTO api_request_progress VALUES (?,?,?)',
                         (request_hash, spent + 1, attempt_limit))
        cache.db.commit()
        return cursor.lastrowid
    except BaseException:
        cache.db.rollback()
        raise


def request_json(cache, key, body, validator, *, namespace, maximum=50, retries=3,
                 settings=None, transport=None, cancel=lambda: False,
                 cache_success=True, on_attempt=lambda stage,attempt,total:None):
    """Return {value,cached,request_hash}; raise safe ApiRequestError/BudgetLimit.

    An invocation-local override never changes settings or resets prior usage.
    SQLite call records remain the budget authority.
    Retries=3 means at most four counted attempts, not three total attempts.
    """
    if not key or not isinstance(key, str):
        raise ValueError('请先在设置中填写并保存 API 密钥。')
    if type(maximum) is not int or not 0 <= maximum <= 100:
        raise ValueError('单次调用上限需在 0～100 次之间')
    if type(retries) is not int or not 0 <= retries <= 10:
        raise ValueError('失败重试次数需在 0～10 次之间')
    body = ai_config.adapt_chat_body(body, settings)
    endpoint = ai_config.base_url(settings or {})
    request_hash = cache.key(namespace, {'endpoint': endpoint, 'body': body})
    with _claim(cache, request_hash, cancel) as lease:
        def notify(stage, attempt, total):
            lease.renew()
            on_attempt(stage, attempt, total)
        if response_quarantined(cache, request_hash):
            raise ApiRequestError('credential_echo')
        if cache_success:
            existing = cache.get(request_hash)
            if existing is not None:
                if contains_credential(existing, key):
                    quarantine_response(cache, request_hash, cancel)
                    raise ApiRequestError('credential_echo')
                try:
                    value=validator(existing)
                    if contains_credential(value, key):
                        quarantine_response(cache, request_hash, cancel)
                        raise ApiRequestError('credential_echo')
                    notify('cached',0,0)
                    return {'value': value, 'cached': True, 'request_hash': request_hash}
                except (ValueError, TypeError, KeyError):
                    pass  # Invalid cache is not a valid successful response.
        _db_execute(cache, 'CREATE TABLE IF NOT EXISTS api_request_progress '
                         '(request TEXT PRIMARY KEY, attempts INTEGER NOT NULL, attempt_limit INTEGER NOT NULL)', cancel=cancel)
        cache.db.commit()
        previous = cache.db.execute('SELECT status FROM calls WHERE request=? ORDER BY id DESC LIMIT 1',
                                    (request_hash,)).fetchone()
        if previous is not None and previous[0] in ('pending', 'network_error', 'read_timeout',
                'connection_timeout', 'response_incomplete', 'total_timeout', 'timeout', 'cancelled', 'credential_echo', 'local_storage_busy'):
            raise ApiRequestError('unknown_outcome')
        progress = cache.db.execute('SELECT attempts,attempt_limit FROM api_request_progress WHERE request=?',
                                    (request_hash,)).fetchone()
        spent, attempt_limit = progress if progress is not None else (0, retries + 1)
        if spent >= attempt_limit:
            _db_execute(cache, 'DELETE FROM api_request_progress WHERE request=?', (request_hash,), cancel=cancel)
            cache.db.commit()
            raise ApiRequestError('retry_limit_reached')
        for attempt in range(spent, attempt_limit):
            if cancel():
                raise InterruptedError('已取消 AI 请求，已完成的结果已保留')
            lease.renew()
            call = _reserve_attempt(cache, request_hash, maximum, attempt, attempt_limit, cancel)
            try:
                notify('requesting',attempt+1,attempt_limit)
                raw, usage = (transport(body, key) if transport is not None
                              else request(body, key, settings, cancel=cancel,
                                  on_progress=lambda data: notify(transport_progress(data), attempt+1, attempt_limit)))
                if cancel(): raise InterruptedError('已取消，本次返回结果未应用')
                notify('validating',attempt+1,attempt_limit)
                value = validator(raw)
                reject_credential_echo(value, key)
                serialized_value = json.dumps(value, ensure_ascii=False, allow_nan=False)
                lease.renew()
                _db_execute(cache, 'BEGIN IMMEDIATE', cancel=cancel)
                if cancel(): raise InterruptedError('已取消 AI 请求，本次结果未应用')
                lease.assert_owner()  # Atomic with publication; no takeover gap.
                # Cache and call status commit together so restart cannot observe
                # a cached success with a misleading unfinished attempt.
                if cache_success:
                    cache.db.execute('INSERT OR REPLACE INTO cache VALUES (?,?)',
                                     (request_hash, serialized_value))
                cache.db.execute('UPDATE calls SET status=?,usage=? WHERE id=?',
                                 ('success', json.dumps(safe_usage(usage)), call))
                _db_execute(cache, 'DELETE FROM api_request_progress WHERE request=?', (request_hash,), cancel=cancel)
                cache.db.commit()
                return {'value': value, 'cached': False, 'request_hash': request_hash}
            except InterruptedError:
                cache.db.rollback()
                _finish_call(cache, call, 'cancelled')
                raise
            except Exception as exc:
                cache.db.rollback()
                status = error_status(exc)
                _finish_call(cache, call, status)
                notify(status,attempt+1,attempt_limit)
                if cancel():raise InterruptedError('已取消后续 AI 请求，已完成的结果已保留')
                if attempt == attempt_limit - 1 or status.startswith('http_4') or status in ('authentication_failed', 'access_denied',
                                                             'endpoint_or_model_not_found', 'response_truncated',
                                                             'tls_error', 'unexpected_stream', 'read_timeout',
                                                             'connection_timeout', 'response_incomplete', 'total_timeout',
                                                             'response_too_large', 'provider_stream_error', 'invalid_response', 'timeout', 'lease_lost', 'network_error', 'credential_echo', 'local_storage_busy'):
                    _db_execute(cache, 'DELETE FROM api_request_progress WHERE request=?', (request_hash,), cancel=cancel)
                    cache.db.commit()
                    raise ApiRequestError(status, getattr(exc, 'diagnostics', None)) from None
    raise AssertionError('unreachable')


def attempt_message(stage, attempt, total):
    if stage == 'cached': return '读取缓存'
    phase = {'requesting':'等待服务响应','validating':'校验返回结果',
             'timeout':'请求超时','cancelled':'已取消'}.get(stage, ERROR_HELP.get(stage,stage))
    return f'尝试 {attempt}/{total} · {phase}'


class ProgressMessage(str):
    """String-compatible diagnostic text with a distinct, honest UI summary."""
    def __new__(cls,detail,summary):
        value=super().__new__(cls,detail);value.summary=summary
        return value
    def __reduce__(self):
        return (type(self),(str(self),self.summary))


def batch_progress(stage,attempt,total,current,batches,detail):
    summary=getattr(stage,'summary',None)
    if summary is None:
        summary={'cached':'读取已保存结果','requesting':'等待回复','validating':'检查回复内容',
                 'cancelled':'已取消'}.get(stage,'本批请求未完成')
    return ProgressMessage(detail,f'正在核对第 {current} / {batches} 批 · {summary}')


def transport_progress(data):
    safe = _safe_diagnostics(data)
    if safe.get('phase') == 'connect_send_headers':
        return ProgressMessage('正在连接服务…','等待回复')
    detail=('接收中（%s 字节 · 正文 %s 字 · 思考 %s 段 · 心跳 %s 次）' %
            tuple(safe.get(k, 0) for k in ('received_bytes', 'content_chars', 'reasoning_events', 'heartbeat_count')))
    summary='正在接收回复内容' if safe.get('content_chars',0)>0 else '等待回复内容'
    return ProgressMessage(detail,summary)
