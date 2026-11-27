"""Non-secret text-AI settings. Credentials belong only to KeyStore."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .storage import atomic_json

DEFAULTS = {
    'api_provider': 'deepseek',
    'api_provider_name': 'DeepSeek 官方',
    'api_base_url': 'https://api.deepseek.com',
    'api_model': 'deepseek-flash',
    'api_max_calls': 50,
    'api_retries': 3,
    'api_transport': 'auto',
    'api_thinking': 'provider_default',
    'api_reasoning_effort': 'provider_default',
    'api_idle_timeout_seconds': 40,
    'api_total_timeout_seconds': 180,
}
PROVIDERS = ({'id': 'deepseek', 'name': 'DeepSeek 官方'},
             {'id': 'custom', 'name': '自定义服务商'})
PRIVACY_NOTICE = '仅发送歌词文本与时间信息，不上传视频或截图；AI 建议仅供参考。'


def base_url(values):
    if values.get('api_provider','deepseek') not in ('deepseek','custom'):
        raise ValueError('仅支持 DeepSeek 官方或自定义服务商')
    if values.get('api_provider', 'deepseek') == 'deepseek':
        return DEFAULTS['api_base_url']
    value = values.get('api_base_url', '')
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError('请填写自定义服务的 Base URL')
    value = value.strip().rstrip('/')
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError('Base URL 无效') from None
    if (parsed.scheme not in ('https', 'http') or not parsed.hostname or
            parsed.username or parsed.password or parsed.query or parsed.fragment or
            any(ord(c) < 33 or c == '\\' for c in value)):
        raise ValueError('Base URL 请填写以 http:// 或 https:// 开头的纯接口地址，勿含账号密码或查询参数')
    if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('远程接口请使用 https:// 加密地址')
    if parsed.path.rstrip('/').endswith(('/chat/completions', '/models')):
        raise ValueError('Base URL 请勿包含末尾的 /chat/completions 或 /models')
    host = parsed.hostname.lower()
    if ':' in host:
        host = '[' + host + ']'
    if port is not None:
        host += ':' + str(port)
    return urlunsplit((parsed.scheme, host, parsed.path.rstrip('/'), '', ''))


def normalized(values=None):
    values = values or {}
    result = {k: values.get(k, v) for k, v in DEFAULTS.items()}
    validate(result)
    result['api_model'] = result['api_model'].strip()
    result['api_base_url'] = base_url(result)
    result['api_provider_name'] = result['api_provider_name'].strip()
    return result


def validate(values):
    if values.get('api_provider', 'deepseek') not in ('deepseek', 'custom'):
        raise ValueError('仅支持 DeepSeek 官方或自定义服务商')
    for key, label, maximum in (('api_model', '模型名称', 200),
                                ('api_provider_name', '服务商名称', 100)):
        value = values.get(key, DEFAULTS[key])
        if (not isinstance(value, str) or not value.strip() or len(value) > maximum or
                any(ord(c) < 32 for c in value)):
            raise ValueError('请填写有效的' + label)
    for key, limit in (('api_max_calls', 100), ('api_retries', 10)):
        value = values.get(key, DEFAULTS[key])
        if type(value) is not int or not 0 <= value <= limit:
            raise ValueError('单次调用上限需在 0～100 次之间，重试次数需在 0～10 次之间。')
    transport_options(values)
    base_url(values)


def load(home):
    try:
        raw = json.loads((Path(home) / 'ai_settings.json').read_text(encoding='utf8'))
        if not isinstance(raw, dict):
            raise ValueError('AI 配置文件格式错误')
        return normalized(raw)
    except (OSError, ValueError, TypeError):
        return dict(DEFAULTS)


def save(home, settings):
    values = normalized(settings)
    atomic_json(Path(home) / 'ai_settings.json', values)
    return values


def credential_scope(settings=None):
    """Keep the existing official credential identity; custom URLs are isolated."""
    return base_url(normalized(settings)) + '/chat/completions'


def delete_credentials(settings, keystore):
    """Explicit UI action; never reads a credential or copies it into settings."""
    keystore.delete(credential_scope(settings))


def delete_provider(home, settings, keystore):
    """Explicit delete action for the selected provider and its saved key."""
    delete_credentials(settings, keystore)
    return save(home, DEFAULTS)


def adopt_project(home, settings):
    if (Path(home) / 'ai_settings.json').is_file():
        return load(home)
    return save(home, settings)


def transport_options(values):
    """Explicit, bounded provider controls, never an arbitrary extra-body escape."""
    result = {}
    for key, allowed in (
        ('api_transport', ('auto', 'json', 'sse')),
        ('api_thinking', ('provider_default', 'enabled', 'disabled')),
        ('api_reasoning_effort', ('provider_default', 'low', 'high', 'max')),
    ):
        value = values.get(key, DEFAULTS[key])
        if value not in allowed:
            raise ValueError('API 传输方式或思考模式设置无效')
        result[key] = value
    for key, maximum in (('api_idle_timeout_seconds', 120), ('api_total_timeout_seconds', 240)):
        value = values.get(key, DEFAULTS[key])
        if type(value) not in (int, float) or not 1 <= value <= maximum:
            raise ValueError('API 空闲超时需在 1～120 秒之间，总超时需在 1～240 秒之间')
        result[key] = value
    return result


def adapt_chat_body(body, settings=None):
    """2026-11-12 DeepSeek ChatCompletions adapter; custom defaults stay JSON.

    Thinking defaults deliberately remain with the provider (currently enabled,
    high). Do not silently disable it for latency, or suggest temperature is
    effective while thinking. Custom services get no DeepSeek-specific fields.
    """
    config = settings or {}
    options = transport_options(config)
    result = dict(body)
    if 'messages' not in body:
        return result  # e.g. model-list cache identity, not a chat request.
    official = config.get('api_provider', 'deepseek') == 'deepseek'
    protocol = options['api_transport']
    result.setdefault('stream', protocol == 'sse' or (protocol == 'auto' and official))
    if official:
        thinking = options['api_thinking']
        if thinking != 'provider_default':
            result.setdefault('thinking', {'type': thinking})
        effort = options['api_reasoning_effort']
        if effort != 'provider_default':
            result.setdefault('reasoning_effort', effort)
        if result.get('thinking', {}).get('type') != 'disabled':
            result.pop('temperature', None)
    return result
