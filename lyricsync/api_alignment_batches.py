"""Resumable, identity-owned alignment windows; no paid unknown-result replay.

Primary windows own disjoint targets and two read-only neighbours. Separate
boundary windows can propose cross-boundary merges of up to 16 unchanged rows.
They cannot edit primary proposals, context, locked rows, or another boundary's
accepted rows. This deterministic conservative arbitration is not a claim of
optimal musical segmentation.
"""
from copy import deepcopy
import json

from . import ai_config
from .api_client import (ApiRequestError, BudgetLimit, _claim, request_json,
                         attempt_message, diagnostic_message, ERROR_HELP, _db_execute,
                         contains_credential, quarantine_response)
from .model import valid_time
from .storage import Cache

WINDOW_ROWS = 15
MAX_WINDOW_CHARS = 24000
MAX_UNLINKED_REFERENCE_CHARS = 12000
NAMESPACE = 'pairing-alignment-v3'
RUN_NAMESPACE = 'pairing-alignment-run-v1'
EXPLICIT_RETRY_CODES = {'authentication_failed', 'access_denied', 'endpoint_or_model_not_found',
                        'tls_error', 'server_error', 'rate_or_quota_limit'}
UNKNOWN_CODES = {'unknown_outcome', 'cancelled', 'read_timeout', 'connection_timeout', 'timeout',
                 'total_timeout', 'response_incomplete', 'network_error', 'lease_lost', 'local_storage_busy'}
STOP_CODES = {'', 'cancelled', 'budget_exhausted', 'context_limit', 'unknown_outcome',
              'invalid_response', 'response_truncated', 'read_timeout', 'total_timeout',
              'connection_timeout', 'timeout', 'response_incomplete', 'response_too_large',
              'provider_stream_error', 'tls_error', 'unexpected_stream', 'network_error',
              'authentication_failed', 'access_denied', 'endpoint_or_model_not_found',
              'rate_or_quota_limit', 'server_error', 'retry_limit_reached', 'merge_conflict', 'boundary_owned', 'lease_lost', 'credential_echo', 'local_storage_busy', 'retry_scope_complete'}


def unlinked_untimed_references(project):
    linked = {identity for row in project.mappings for identity in row.lyric_ids}
    return [row for row in project.lyrics if row.id not in linked and not valid_time(row.start, row.end)]


def reference_scope(project, reference_ids=None):
    """Explicit source subset, never a mapping or a persisted project mutation."""
    rows = unlinked_untimed_references(project)
    if reference_ids is None:
        return rows
    if (not isinstance(reference_ids, list) or not reference_ids
            or any(not isinstance(identity, str) for identity in reference_ids)
            or len(set(reference_ids)) != len(reference_ids)):
        raise ValueError('原文参考范围无效；没有发送请求。')
    allowed = {row.id for row in rows}
    if not set(reference_ids) <= allowed:
        raise ValueError('所选原文参考已变化，请重新选择；没有发送请求。')
    selected = set(reference_ids)
    return [row for row in rows if row.id in selected]


def reference_scope_size(rows):
    return len(json.dumps([{'id': row.id, 'text': row.text} for row in rows],
                          ensure_ascii=False, separators=(',', ':')))


def scoped_payload(project, index, ids, scope=None, reference_ids=None):
    from .api_alignment import build_payload
    payload = build_payload(project, index, ids)
    rows = payload['mappings']
    linked_source = {i for row in rows for i in row['lyric_ids']}
    linked_ocr = {i for row in rows for i in row['visual_ids']}
    ranges = [(row['start'], row['end']) for row in rows if valid_time(row['start'], row['end'])]
    def overlaps(start, end):
        return valid_time(start, end) and any(start < hi and end > lo for lo, hi in ranges)
    # Time selects relevant evidence, not a pairing. Untimed/unlinked records
    # cannot be guessed by row number, relative position, or matching lyrics.
    references = {row.id for row in reference_scope(project, reference_ids)}
    source_ids = linked_source | references | {row.id for row in project.lyrics if overlaps(row.start, row.end)}
    visual_ids = linked_ocr | {row['id'] for row in payload['ocr']
                             if overlaps(row['observed_start'], row['observed_end'])}
    payload['source'] = [{**row, **({'unlinked_reference': True} if row['id'] in references else {})}
                         for row in payload['source'] if row['id'] in source_ids]
    payload['ocr'] = [row for row in payload['ocr'] if row['id'] in visual_ids]
    if not linked_source <= {r['id'] for r in payload['source']} or not linked_ocr <= {r['id'] for r in payload['ocr']}:
        raise ValueError('所选字幕关联的原文或 OCR 识别结果缺失，无法核对')
    payload['scope'] = scope or {'mode': 'all'}
    return payload


def validate_coverage(value, mapping_ids):
    fields = {'version', 'scope_mapping_ids', 'completed_mapping_ids', 'missing_mapping_ids',
              'completed_windows', 'total_windows', 'counted_attempts', 'pending_boundaries', 'stop_code'}
    if not isinstance(value, dict) or set(value) != fields or type(value['version']) is not int or value['version'] != 1:
        raise ValueError('AI 分批核对记录格式无效')
    for name in ('scope_mapping_ids', 'completed_mapping_ids', 'missing_mapping_ids'):
        ids = value[name]
        if (not isinstance(ids, list) or len(ids) > 250 or len(set(ids)) != len(ids)
                or any(not isinstance(i, str) or not i or len(i) > 256 for i in ids)):
            raise ValueError('AI 分批核对范围无效')
    scope, done, missing = (value[k] for k in ('scope_mapping_ids', 'completed_mapping_ids', 'missing_mapping_ids'))
    if (not scope or done != list(mapping_ids) or set(done) & set(missing)
            or set(scope) != set(done) | set(missing)
            or done != [i for i in scope if i in done] or missing != [i for i in scope if i in missing]):
        raise ValueError('AI 分批核对进度记录不一致')
    completed, total = value['completed_windows'], value['total_windows']
    if type(completed) is not int or type(total) is not int or not 0 <= completed <= total <= 499:
        raise ValueError('AI 分批数量超出范围')
    if type(value['counted_attempts']) is not int or not 0 <= value['counted_attempts'] <= 1000000:
        raise ValueError('AI 调用次数记录无效')
    boundaries = value['pending_boundaries']
    if (not isinstance(boundaries, list) or len(boundaries) > 249
            or any(not isinstance(pair, list) or len(pair) != 2 or any(i not in scope for i in pair)
                   for pair in boundaries) or value['stop_code'] not in STOP_CODES):
        raise ValueError('AI 分批状态记录无效')
    return deepcopy(value)


def coverage_message(coverage):
    done, total = len(coverage['completed_mapping_ids']), len(coverage['scope_mapping_ids'])
    seams = len(coverage['pending_boundaries'])
    text = ((f"已核对 {done}/{total} 句（待处理 {total - done} 句，待查批次衔接 {seams} 处） · 批次进度 {coverage['completed_windows']}/{coverage['total_windows']} · 已调用 API {coverage['counted_attempts']} 次。"

                                                                                                                    ))
    if coverage['stop_code']:
        text += ' 停止原因：' + ({'retry_scope_complete':'已完成本次明确确认的重发；其余范围未自动请求', 'boundary_owned': '交界处句子已有拆合建议，请手动核对衔接'}.get(coverage['stop_code'], coverage['stop_code'])) + '。'
    return text + '请预览确认后再应用。'


def request_windowed_plan(project, index, cache, key, *, mapping_ids=None, settings=None,
                          temporary_maximum=None, transport=None, cancel=lambda: False,
                          reference_ids=None, progress=lambda fraction, message: None, force_retry=None):
    from .api_alignment import (build_payload, request_payload, revision_signature, SYSTEM,
                                validate_pairing_plan, normalize_pairing_plan, validate_response, make_plan)
    from . import api_force_retry
    config = {**project.settings, **(settings or {})}
    if not config.get('api_enabled', False):
        return {'status': 'disabled', 'message': ai_config.PRIVACY_NOTICE}
    if temporary_maximum not in (None, 100):
        raise ValueError('单次临时调用上限只能设为 100 次')
    references = reference_scope(project, reference_ids)
    if reference_scope_size(references) > MAX_UNLINKED_REFERENCE_CHARS:
        return {'status': 'source_scope_required', 'message':
            '未关联且没有时间的原文参考超过安全范围，本轮未发送请求。请明确选择本次原文参考范围，或先为所选字幕明确关联原文再核对；不能按行号猜配或静默丢弃原文。'}
    full = build_payload(project, index, mapping_ids)
    ids = [m['id'] for m in full['mappings'] if m['editable']]
    signature = revision_signature(project, index)
    positions = {m.id: n for n, m in enumerate(project.mappings)}
    # Split at discontiguous selections before applying the soft 15-row cap.
    groups = []
    for identity in ids:
        if not groups or len(groups[-1]) >= WINDOW_ROWS or positions[identity] != positions[groups[-1][-1]] + 1:
            groups.append([])
        groups[-1].append(identity)
    # Reduce oversized text windows without claiming a tokenizer estimate.
    def fit(group):
        payload = scoped_payload(project, index, group, reference_ids=reference_ids)
        size = len(json.dumps(request_payload(payload), ensure_ascii=False, separators=(',', ':')))
        if size > MAX_WINDOW_CHARS and len(group) > 1:
            middle = len(group) // 2
            return fit(group[:middle]) + fit(group[middle:])
        return [group]
    groups = [chunk for group in groups for chunk in fit(group)]
    specs = [{'kind': 'primary', 'ids': group} for group in groups]
    for left, right in zip(groups, groups[1:]):
        if positions[right[0]] == positions[left[-1]] + 1:
            # Include 15 targets on either side, including neighbouring small
            # chunks, so every allowed <=16-row cross-boundary merge is visible.
            cut = ids.index(right[0])
            lo, hi = max(0, cut - 15), min(len(ids), cut + 15)
            specs.append({'kind': 'boundary', 'ids': ids[lo:hi],
                          'left_ids': ids[lo:cut], 'right_ids': ids[cut:hi],
                          'boundary': [left[-1], right[0]]})
    from .api_client import ProgressMessage
    progress(0, ProgressMessage(f'计划 {len(specs)} 个主/边界窗口；缓存或已占用边界可跳过。每次实际尝试共用工程预算，失败也计数。',
        f'准备核对，共 {len(specs)} 批'))
    options = ai_config.transport_options(config)
    run_hash = Cache.key(RUN_NAMESPACE, {'revision': signature, 'ids': ids, 'specs': specs, 'reference_ids': [row.id for row in references],
        'endpoint': ai_config.base_url(config), 'model': config.get('api_model', 'deepseek-flash'),
        'options': options, 'protocol': NAMESPACE, 'system': SYSTEM})
    try:
        with _claim(cache, run_hash, cancel) as run_lease:
            state = cache.get(run_hash)
            if state is None:
                state = {'version': 1, 'windows': [{} for _ in specs]}
            if (not isinstance(state, dict) or state.get('version') != 1 or not isinstance(state.get('windows'), list)
                    or len(state['windows']) != len(specs)):
                raise ValueError('断点续传记录已损坏，请重新发起核对')
            approved_index = None
            if force_retry is not None:
                if not isinstance(force_retry,dict) or type(force_retry.get('window')) is not int:
                    raise ValueError('重发确认无效；未发送请求。')
                approved_index=force_retry['window']
                if not 0<=approved_index<len(specs) or force_retry != api_force_retry.offer(
                        run_hash,signature,approved_index,specs[approved_index],state['windows'][approved_index]):
                    raise ValueError('工程、服务、范围或未知请求已改变；旧重发确认不可使用。')
            def save_state():
                run_lease.renew()
                encoded = json.dumps(state, ensure_ascii=False, allow_nan=False)
                _db_execute(cache, 'BEGIN IMMEDIATE', cancel=cancel)
                try:
                    run_lease.assert_owner()
                    cache.db.execute('INSERT OR REPLACE INTO cache VALUES (?,?)', (run_hash, encoded))
                    cache.db.commit()
                except BaseException:
                    cache.db.rollback()
                    raise
            # Old decoded run entries may predate credential-echo protection.
            # Sanitize every affected entry before any journal is re-saved.
            unsafe = False
            for recovered in state['windows']:
                if isinstance(recovered, dict) and contains_credential(recovered.get('value'), key):
                    old_hash = recovered.get('request_hash')
                    if isinstance(old_hash, str) and old_hash.startswith(NAMESPACE + ':'):
                        quarantine_response(cache, old_hash, cancel)
                    history=api_force_retry.lineage(recovered)
                    recovered.clear(); recovered.update(history,status='blocked',code='credential_echo',request_hash=old_hash)
                    unsafe = True
            if unsafe: save_state()
            aggregate = {'operations': [], 'semantic_issues': []}
            completed_ids, completed_windows, pending_boundaries = [], 0, []
            used_rows = set()
            stop, diagnostics, budget = '', {}, None
            cached = True
            for n, spec in enumerate(specs):
                run_lease.renew()
                entry = state['windows'][n]
                if not isinstance(entry, dict):
                    raise ValueError('断点续传记录已损坏，请重新发起核对')
                if force_retry is not None and n != approved_index and entry.get('status') not in ('completed','owned_boundary'):
                    stop = stop or 'retry_scope_complete'
                if entry.get('status') == 'blocked' and entry.get('code') in EXPLICIT_RETRY_CODES:
                    # A new explicit command can retry a known refusal after the
                    # user fixes credentials/service. No same-invocation loop.
                    history=api_force_retry.lineage(entry)
                    entry.clear();entry.update(history)
                targets = spec['ids'] if spec['kind'] == 'primary' else [i for i in spec['ids'] if i not in used_rows]
                scope = {'mode': 'all'}
                if spec['kind'] == 'boundary':
                    scope = {'mode': 'crossing_merge_only',
                             'left_ids': [i for i in spec['left_ids'] if i in targets],
                             'right_ids': [i for i in spec['right_ids'] if i in targets]}
                    # A previously proposed edit is owned by its first window;
                    # never silently rewrite/retract it to satisfy a later seam.
                    if not all(i in targets for i in spec['boundary']):
                        # A conservative ownership conflict is NOT a completed
                        # semantic seam review. Keep it visible for manual review.
                        pending_boundaries.append(spec['boundary'])
                        entry.update(status='owned_boundary', targets=targets)
                        state['windows'][n] = entry; save_state()
                        continue
                if stop:
                    if spec['kind'] == 'boundary': pending_boundaries.append(spec['boundary'])
                    continue
                if cancel() and entry.get('status') != 'completed':
                    stop = 'cancelled'
                    if spec['kind'] == 'boundary': pending_boundaries.append(spec['boundary'])
                    continue
                payload = scoped_payload(project, index, targets, scope, reference_ids=reference_ids) if targets else None
                serialized = json.dumps(request_payload(payload), ensure_ascii=False, allow_nan=False,
                                        separators=(',', ':')) if payload else ''
                body = ai_config.adapt_chat_body({'model': config.get('api_model', 'deepseek-flash'),
                    'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': serialized}],
                    'response_format': {'type': 'json_object'}, 'temperature': 0, 'max_tokens': 8000}, config)
                request_namespace=api_force_retry.namespace(entry,NAMESPACE)
                request_hash = Cache.key(request_namespace, {'endpoint': ai_config.base_url(config), 'body': body})
                force_this = approved_index == n
                if force_this:
                    if cancel():raise InterruptedError('已取消重发；未发送请求。')
                    if force_retry!=api_force_retry.offer(run_hash,signature,n,spec,entry) or request_hash!=force_retry['request_hash']:
                        raise ValueError('请求内容已改变；旧重发确认不可使用。')
                    from .api_client import response_quarantined
                    if any(response_quarantined(cache,h) for h in [request_hash]+entry.get('previous_request_hashes',[])):
                        raise ValueError('该请求存在凭据回显隔离记录，不能通过强制重发绕过。')
                    if cache.get(request_hash) is not None:
                        raise ValueError('该请求已有缓存结果；请先重新检查结果，未重发。')
                    api_force_retry.consume(entry,NAMESPACE,request_hash)
                    request_namespace=entry['request_namespace']
                    request_hash=Cache.key(request_namespace, {'endpoint':ai_config.base_url(config),'body':body})
                    entry.update(request_hash=request_hash,targets=targets)
                    save_state()  # Consume first: reopening cannot turn this into an implicit POST.
                raw = None
                try:
                    if entry.get('status') == 'completed':
                        raw = entry['value']
                        if entry.get('targets') != targets:
                            raise ValueError('分批核对范围已变化，请重新发起核对')
                    elif entry.get('status') in ('requesting', 'blocked') and not force_this:
                        # Process may have stopped after cache commit, before the
                        # run journal commit. Only reuse a validated success.
                        raw = cache.get(request_hash)
                        if raw is None:
                            stop = entry.get('code', 'unknown_outcome')
                            if stop not in STOP_CODES: stop = 'unknown_outcome'
                    elif len(serialized) > MAX_WINDOW_CHARS:
                        stop = 'context_limit'
                    elif payload is not None:
                        previous = cache.db.execute('SELECT status FROM calls WHERE request=? ORDER BY id DESC LIMIT 1',
                                                    (request_hash,)).fetchone()
                        if previous is not None and previous[0] not in EXPLICIT_RETRY_CODES and cache.get(request_hash) is None:
                            # A copied/reopened document may restore only its ledger.
                            # Missing cache is not permission to repeat a paid POST.
                            stop = 'unknown_outcome'
                            entry.update(status='blocked', code=stop, targets=targets,request_hash=request_hash)
                            save_state()
                            raise ApiRequestError(stop)
                        entry.update(status='requesting', request_hash=request_hash, targets=targets)
                        save_state()  # Crash before/after POST is not permission to resend.
                        def report(stage, attempt, total):
                            run_lease.renew()
                            from .api_client import batch_progress
                            detail=f'AI 配对 第 {n + 1}/{len(specs)} 批 · '+attempt_message(stage, attempt, total)
                            progress(n / len(specs),batch_progress(stage,attempt,total,n+1,len(specs),detail))
                        result = request_json(cache, key, body, lambda raw: validate_pairing_plan(raw, payload),
                            namespace=request_namespace, settings=config, transport=transport,
                            maximum=temporary_maximum if temporary_maximum is not None else config.get('api_max_calls', 50),
                            retries=0 if force_this else config.get('api_retries', 3), cancel=cancel,
                            on_attempt=report)
                        raw = result['value']; cached = cached and result['cached']
                    if raw is not None:
                        if contains_credential(raw, key):
                            quarantine_response(cache, request_hash, cancel)
                            history=api_force_retry.lineage(entry)
                            entry.clear();entry.update(history,request_hash=request_hash)
                            raise ApiRequestError('credential_echo')
                        if payload is None:
                            normalized = {'operations': [], 'semantic_issues': []}
                        else:
                            validate_pairing_plan(raw, payload)
                            normalized = normalize_pairing_plan(raw, payload)
                        combined = deepcopy(aggregate)
                        combined['operations'].extend(normalized['operations'])
                        seen = {(tuple(i['mapping_ids']), i['kind']) for i in combined['semantic_issues']}
                        for issue in normalized['semantic_issues']:
                            identity = (tuple(issue['mapping_ids']), issue['kind'])
                            if identity not in seen:
                                combined['semantic_issues'].append(issue); seen.add(identity)
                        combined['operations'].sort(key=lambda op: positions[op['mapping_ids'][0]])
                        validate_response(combined, full)
                        aggregate = combined
                        used_rows.update(i for op in normalized['operations'] for i in op['mapping_ids'])
                        if spec['kind'] == 'primary': completed_ids.extend(spec['ids'])
                        completed_windows += 1
                        history=api_force_retry.lineage(entry)
                        entry.clear(); entry.update(history,status='completed', value=raw, targets=targets, request_hash=request_hash)
                        save_state()
                except BudgetLimit as exc:
                    budget = exc.state; stop = 'budget_exhausted'
                    history=api_force_retry.lineage(entry)
                    entry.clear()  # Retain all original paid attempts and consumed forced identity.
                    entry.update(history,request_hash=request_hash)
                    if force_this:entry.update(status='blocked',code='unknown_outcome')
                    save_state()
                except InterruptedError:
                    stop = 'cancelled'
                    entry.update(status='blocked', code='unknown_outcome'); save_state()
                except ApiRequestError as exc:
                    if exc.code == 'lease_lost': raise
                    stop = exc.code if exc.code in STOP_CODES else 'unknown_outcome'
                    diagnostics = deepcopy(exc.diagnostics)
                    entry.update(status='blocked', code=stop); save_state()
                except (ValueError, TypeError, KeyError, IndexError):
                    stop = 'merge_conflict'
                    entry.update(status='blocked', code=stop); save_state()
                if stop and spec['kind'] == 'boundary': pending_boundaries.append(spec['boundary'])
                if revision_signature(project, index) != signature:
                    return {'status': 'stale', 'message': '核对期间字幕已被修改，本次结果已失效，请重新运行核对。'}
            if pending_boundaries and not stop: stop = 'boundary_owned'
            run_requests = {entry.get('request_hash') for entry in state['windows']}
            run_requests.update(h for entry in state['windows'] for h in entry.get('previous_request_hashes',[]))
            counted = sum(row['request_hash'] in run_requests for row in cache.records())
            coverage = {'version': 1, 'scope_mapping_ids': ids, 'completed_mapping_ids': completed_ids,
                'missing_mapping_ids': [i for i in ids if i not in completed_ids],
                'completed_windows': completed_windows, 'total_windows': len(specs), 'counted_attempts': counted,
                'pending_boundaries': pending_boundaries, 'stop_code': stop}
            validate_coverage(coverage, completed_ids)
            message = coverage_message(coverage)
            if stop in ERROR_HELP: message += ' '+ERROR_HELP[stop]
            if stop in UNKNOWN_CODES and ERROR_HELP['unknown_outcome'] not in message:
                message += ' '+ERROR_HELP['unknown_outcome']
            if diagnostics: message += '\n诊断：' + diagnostic_message(diagnostics)
            plan = make_plan(project, index, aggregate, completed_ids, coverage=coverage) if completed_ids else None
            # A partial no-op is still useful coverage, not a fake complete plan.
            result = {'status': ('partial' if plan is not None else 'failed') if stop else plan['status'], 'coverage': coverage,
                      'message': message, 'cached': cached, 'request_hash': run_hash,
                      'diagnostics': diagnostics, 'code': stop}
            offers=[api_force_retry.offer(run_hash,signature,n,spec,entry) for n,(spec,entry) in enumerate(zip(specs,state['windows']))]
            retry_offer=next((item for item in offers if item),None)
            if retry_offer is not None:result['force_retry_offer']=retry_offer
            if plan is not None: result['plan'] = plan
            if budget is not None:
                if plan is None: result.update(budget)
                else: result['resume'] = budget
            return result
    finally:
        project.api_records = cache.records()
