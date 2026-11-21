"""Synthetic identities/text and decoded PTS; never call a provider."""
from copy import deepcopy
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from lyricsync import api_alignment as alignment
from lyricsync import api_alignment_batches as batches
from lyricsync.api_client import TransportFailure
from lyricsync.alignment_candidate import candidate_record, restore_candidate, read_candidate
from lyricsync.editing import History, mapping_text
from lyricsync.frames import FrameIndex
from lyricsync.model import Project, Mapping, Translation, Lyric, Visual
from lyricsync.storage import Cache


def fixture(count=32):
    p = Project(); p.settings.update(api_enabled=True, api_retries=3)
    index = FrameIndex([n/4 for n in range(count*4+1)], count+.25)
    for n in range(count):
        # Identical repeated text must never become an identity/position match.
        mid, lid, vid = f'long_mapping_{n}', f'long_lyric_{n}', f'long_visual_{n}'
        p.translations.append(Translation(f't{n}', '重复e\u0301🙂重复'))
        p.lyrics.append(Lyric(lid, 'repeated synthetic lyric', n, n+1))
        p.visuals.extend([Visual(vid+'a', 'first', n, n+.5, [], .99, 2),
                          Visual(vid+'b', 'second', n+.5, n+1, [], .99, 2)])
        p.mappings.append(Mapping(mid, [f't{n}'], [lid], [vid+'a', vid+'b'], n, n+1))
    return p, index


def empty(): return {'operations': [], 'semantic_issues': []}


def split_first(body):
    wire = json.loads(body['messages'][1]['content'])
    if wire['scope']['mode'] != 'all': return empty()
    row = next(r for r in wire['mappings'] if r['editable'])
    return {'operations': [{'kind': 'split', 'mapping_ids': [row['id']],
        'parts': [{'spans': [{'mapping_id': row['id'], 'text': text}],
                   'lyric_ids': row['lyric_ids'], 'visual_ids': [vid]}
                  for text, vid in zip(('重复', 'e\u0301🙂重复'), row['visual_ids'])],
        'reason': 'Two independent observations.'}], 'semantic_issues': []}


def test_window_identity_trim_readonly_aliases_and_no_candidate_import(tmp_path):
    p, index = fixture(); original = deepcopy(p.to_dict())
    p.source_candidates = {'ocr': {'items': [{'text': 'UNAPPLIED_PRIVATE'}]}}
    p.lyrics.append(Lyric('unlinked', 'UNLINKED_UNTIMED', None, None))
    calls = []; cache = Cache(tmp_path/'windows.sqlite')
    def provider(body, key):
        wire = json.loads(body['messages'][1]['content']); calls.append(wire)
        return json.dumps(split_first(body), ensure_ascii=False), {}
    try:
        result = alignment.request_plan(p, index, cache, 'fake-local-key', transport=provider)
        assert result['status'] == 'partial'
        assert len(calls) == 3  # Only primaries; owned seams remain visibly incomplete.
        assert [len([m for m in w['mappings'] if m['editable']]) for w in calls[:3]] == [15, 15, 2]
        assert all(len(w['source']) < len(p.lyrics) for w in calls)
        assert all(w['protocol'] == 3 for w in calls)
        assert 'UNAPPLIED_PRIVATE' not in json.dumps(calls)
        assert all(any(r.get('unlinked_reference') and r['text']=='UNLINKED_UNTIMED' for r in w['source']) for w in calls)
        assert all('unlinked' not in row['lyric_ids'] for w in calls for row in w['mappings'])
        assert all('cut_offsets' not in m for w in calls for m in w['mappings'])
        assert result['coverage']['completed_mapping_ids'] == [m.id for m in p.mappings]
        assert result['coverage']['missing_mapping_ids'] == [] and len(result['coverage']['pending_boundaries']) == 2
        assert [op['mapping_ids'][0] for op in result['plan']['response']['operations']] == ['long_mapping_0','long_mapping_15','long_mapping_30']
        assert p.to_dict()['mappings'] == original['mappings']
        assert alignment.request_plan(p, index, cache, 'fake-local-key', transport=lambda *_: pytest.fail('cached'))['cached']
    finally: cache.close()


def test_cross_boundary_merge_is_restored_before_single_undoable_apply(tmp_path):
    p, index = fixture(18); cache = Cache(tmp_path/'seam.sqlite'); original = deepcopy(p.to_dict()); wires=[]
    def provider(body, _key):
        wire=json.loads(body['messages'][1]['content']); wires.append(wire)
        raw=empty()
        if wire['scope']['mode']=='crossing_merge_only':
            chosen=[wire['scope']['left_ids'][-1], wire['scope']['right_ids'][0]]
            rows={r['id']:r for r in wire['mappings']}
            raw['operations']=[{'kind':'merge','mapping_ids':chosen,'reason':'Cross-boundary evidence.',
                'parts':[{'spans':[{'mapping_id':i,'text':rows[i]['chinese']} for i in chosen],
                    'lyric_ids':[lid for i in chosen for lid in rows[i]['lyric_ids']],
                    'visual_ids':[vid for i in chosen for vid in rows[i]['visual_ids']]}]}]
        return json.dumps(raw,ensure_ascii=False),{}
    try:
        result=alignment.request_plan(p,index,cache,'fake-local-key',transport=provider)
        assert len(wires)==3 and result['status']=='proposed'
        plan=result['plan']; assert plan['response']['operations'][0]['mapping_ids']==['long_mapping_14','long_mapping_15']
        record=candidate_record(p,index,plan); assert record['version']==2
        assert restore_candidate(p,index,json.loads(json.dumps(record)))==plan
        history=History(); alignment.accept_plan(p,plan,index,history)
        assert len(p.mappings)==17 and len(history.undo_stack)==1
        restored,_=history.restore(p)
        restored.api_records=original['api_records']; assert restored.to_dict()==original
    finally:cache.close()


def test_partial_budget_persist_reopen_resume_and_exact_missing_range(tmp_path):
    p,index=fixture();p.settings['api_max_calls']=1; path=tmp_path/'budget.sqlite';cache=Cache(path);calls=[]
    def provider(body,_):calls.append(body);return json.dumps(split_first(body),ensure_ascii=False),{}
    try:
        first=alignment.request_plan(p,index,cache,'fake-local-key',transport=provider)
        assert first['status']=='partial' and first['coverage']['stop_code']=='budget_exhausted'
        assert len(first['coverage']['completed_mapping_ids'])==15 and len(first['coverage']['missing_mapping_ids'])==17
        assert 'resume' in first and len(cache.records())==1
        record=candidate_record(p,index,first['plan']); assert restore_candidate(p,index,record)==first['plan']
        # Applying only validated completed changes never touches a missing row.
        untouched=deepcopy(p.mappings[15:]); copy=Project.from_dict(p.to_dict())
        alignment.accept_plan(copy,first['plan'],index,History()); assert copy.mappings[16:]==untouched
        cache.close();cache=Cache(path)
        done=alignment.request_plan(p,index,cache,'fake-local-key',temporary_maximum=100,transport=provider)
        assert done['coverage']['missing_mapping_ids']==[] and done['coverage']['stop_code']=='boundary_owned'
        assert len(cache.records())==len(calls) and len(calls)==3
        assert p.settings['api_max_calls']==1
    finally:cache.close()


@pytest.mark.parametrize('failure',['read_timeout','invalid_response','cancelled'])
def test_partial_failure_does_not_blindly_replay_unknown_or_bad_request(tmp_path,failure):
    p,index=fixture();cache=Cache(tmp_path/'unknown.sqlite');calls=[]
    def provider(body,_):
        calls.append(body)
        if len(calls)==2:
            if failure=='cancelled':raise InterruptedError('mock')
            if failure=='invalid_response':return 'not JSON',{}
            raise TransportFailure('read_timeout',{'phase':'read_body','received_bytes':0})
        return json.dumps(split_first(body),ensure_ascii=False),{}
    try:
        first=alignment.request_plan(p,index,cache,'fake-local-key',transport=provider)
        assert first['status']=='partial' and len(first['coverage']['completed_mapping_ids'])==15
        second=alignment.request_plan(p,index,cache,'fake-local-key',transport=provider)
        assert len(calls)==2 and len(cache.records())==2
        assert second['coverage']['missing_mapping_ids']==first['coverage']['missing_mapping_ids']
        assert second['plan']['response']==first['plan']['response']
    finally:cache.close()


def test_restored_ledger_without_cache_never_repeats_paid_success(tmp_path):
    p,index=fixture(2);cache=Cache(tmp_path/'one.sqlite')
    alignment.request_plan(p,index,cache,'fake-local-key',transport=lambda *_:('{}',{}))
    records=cache.records(); cache.close()
    fresh=Cache(tmp_path/'copied.sqlite');fresh.restore_records(records)
    try:
        result=alignment.request_plan(p,index,fresh,'fake-local-key',transport=lambda *_:pytest.fail('no unknown replay'))
        assert result['code']=='unknown_outcome' and len(fresh.records())==1 and 'plan' not in result
    finally:fresh.close()


def test_cancel_between_windows_keeps_complete_batches_and_can_continue_unattempted(tmp_path):
    p,index=fixture(17);cache=Cache(tmp_path/'cancel.sqlite');calls=[];stop=False
    def provider(body,_):calls.append(body);return json.dumps(split_first(body),ensure_ascii=False),{}
    def progress(fraction,message):
        nonlocal stop
        if '校验返回' in message:pass
        # Trigger only after first successful journal, at next provider start is unsafe;
        # use cancel callback counting committed successful ledger records instead.
    cancel=lambda:len([r for r in cache.records() if r['status']=='success'])>=1
    try:
        first=alignment.request_plan(p,index,cache,'fake-local-key',transport=provider,cancel=cancel)
        assert first['code']=='cancelled' and len(first['coverage']['completed_mapping_ids'])==15
        second=alignment.request_plan(p,index,cache,'fake-local-key',transport=provider)
        assert second['coverage']['stop_code']=='boundary_owned' and len(calls)==2
    finally:cache.close()


def test_raw_substrings_unicode_graphemes_repetition_and_evidence_are_strict():
    p,index=fixture(1);payload=alignment.build_payload(p,index);wire=alignment.request_payload(payload)
    body={'messages':[{}, {'content':json.dumps(wire,ensure_ascii=False)}]}
    raw=split_first(body)
    alignment.validate_pairing_plan(raw,payload)
    normalized=alignment.normalize_pairing_plan(raw,payload)
    assert [s['start'] for part in normalized['operations'][0]['parts'] for s in part['spans']]==[0,2]
    for texts in [('重复e','\u0301🙂重复'),('重复','é🙂重复'),('重复','e\u0301🙂'),(' ','重复e\u0301🙂重复')]:
        bad=deepcopy(raw)
        for part,text in zip(bad['operations'][0]['parts'],texts):part['spans'][0]['text']=text
        with pytest.raises(ValueError):alignment.validate_pairing_plan(bad,payload)
    reused=deepcopy(raw);reused['operations'][0]['parts'][1]['visual_ids']=reused['operations'][0]['parts'][0]['visual_ids']
    with pytest.raises(ValueError):alignment.validate_pairing_plan(reused,payload)
    p.visuals[1].end=999
    payload=alignment.build_payload(p,index)
    assert any(not r['timing_usable'] for r in alignment.request_payload(payload)['ocr'])
    with pytest.raises(ValueError):alignment.validate_pairing_plan(raw,payload)


def test_context_cannot_be_changed_or_claimed_and_boundary_scope_cannot_split():
    p,index=fixture(20);payload=batches.scoped_payload(p,index,[m.id for m in p.mappings[:15]])
    wire=alignment.request_payload(payload);row=next(r for r in wire['mappings'] if not r['editable'])
    raw={'operations':[],'semantic_issues':[{'mapping_ids':[row['id']],'lyric_ids':[], 'visual_ids':[], 'kind':'uncertain','reason':'Context only.'}]}
    with pytest.raises(ValueError):alignment.validate_pairing_plan(raw,payload)
    scope={'mode':'crossing_merge_only','left_ids':[p.mappings[0].id],'right_ids':[p.mappings[1].id]}
    payload=batches.scoped_payload(p,index,[p.mappings[0].id,p.mappings[1].id],scope)
    wire=alignment.request_payload(payload); wire['scope']['mode']='all'
    raw=split_first({'messages':[{}, {'content':json.dumps(wire,ensure_ascii=False)}]})
    with pytest.raises(ValueError):alignment.validate_pairing_plan(raw,payload)


def test_adaptive_text_bound_stops_before_single_oversized_request(tmp_path):
    p,index=fixture(1);p.mappings[0].text_override='甲'*25000;cache=Cache(tmp_path/'size.sqlite')
    try:
        result=alignment.request_plan(p,index,cache,'fake-local-key',transport=lambda *_:pytest.fail('no oversized POST'))
        assert result['code']=='context_limit' and not cache.records()
    finally:cache.close()


def test_legacy_candidate_still_restores_and_coverage_tampering_rejected(tmp_path):
    from tests.test_api_migration_alignment import fixture as old_fixture, response, split
    p,index=old_fixture();plan=alignment.make_plan(p,index,response(split()));record=candidate_record(p,index,plan)
    assert record['version']==1 and restore_candidate(p,index,record)==plan
    cache=Cache(tmp_path/'new.sqlite')
    try:
        result=alignment.request_plan(p,index,cache,'fake-local-key',transport=lambda *_:(json.dumps(empty()),{}))
        changed=deepcopy(result['plan']);changed['coverage']['missing_mapping_ids']=['bogus']
        with pytest.raises(ValueError):alignment.validate_plan(p,changed,index)
        candidate=candidate_record(p,index,result['plan']);candidate['coverage']['stop_code']='PRIVATE_UNKNOWN'
        assert read_candidate(candidate)[0] is None
    finally:cache.close()


def test_concurrent_same_run_one_provider_call(tmp_path):
    p,index=fixture(2);path=tmp_path/'shared.sqlite';calls=[];lock=threading.Lock()
    def provider(body,_):
        with lock:calls.append(body)
        return json.dumps(empty()),{}
    def run(_):
        cache=Cache(path)
        try:return alignment.request_plan(Project.from_dict(p.to_dict()),index,cache,'fake-local-key',transport=provider)
        finally:cache.close()
    with ThreadPoolExecutor(max_workers=3) as pool: results=list(pool.map(run,range(3)))
    assert len(calls)==1 and sum(r['cached'] for r in results)==2


def test_concurrent_first_open_wal_is_bounded_and_shared(tmp_path):
    for attempt in range(20):
        path = tmp_path / ('first-open-%s.sqlite' % attempt)
        barrier = threading.Barrier(8)
        def create(n):
            barrier.wait(timeout=3)
            cache = Cache(path)
            try:
                return cache.db.execute('PRAGMA journal_mode').fetchone()[0]
            finally: cache.close()
        with ThreadPoolExecutor(max_workers=8) as pool:
            assert list(pool.map(create, range(8))) == ['wal']*8


def test_global_ocr_order_rejects_individually_valid_but_reversed_windows(tmp_path):
    p,index=fixture(2)
    p.mappings[0].visual_ids, p.mappings[1].visual_ids = p.mappings[1].visual_ids, p.mappings[0].visual_ids
    payload=alignment.build_payload(p,index)
    wire=alignment.request_payload(payload);both=empty()
    for n in (0,1):
        view=deepcopy(wire)
        for row in view['mappings']:row['editable']=row['id']==wire['mappings'][n]['id']
        raw=split_first({'messages':[{}, {'content':json.dumps(view,ensure_ascii=False)}]})
        both['operations'].extend(raw['operations'])
    with pytest.raises(ValueError,match='OCR 起始帧顺序异常'):
        alignment.validate_pairing_plan(both,payload)


def test_identical_substrings_use_cumulative_offset_not_find():
    p,index=fixture(1);p.mappings[0].text_override='重复重复'
    payload=alignment.build_payload(p,index);wire=alignment.request_payload(payload)
    row=wire['mappings'][0]
    raw={'operations':[{'kind':'split','mapping_ids':[row['id']],'reason':'Independent evidence.',
        'parts':[{'spans':[{'mapping_id':row['id'],'text':'重复'}],
                  'lyric_ids':row['lyric_ids'],'visual_ids':[vid]} for vid in row['visual_ids']]}], 'semantic_issues':[]}
    alignment.validate_pairing_plan(raw,payload)
    result=alignment.normalize_pairing_plan(raw,payload)
    assert [part['spans'][0]['start'] for part in result['operations'][0]['parts']]==[0,2]


def test_new_wire_namespace_does_not_reuse_old_response_cache(tmp_path):
    p,index=fixture(1);cache=Cache(tmp_path/'schema.sqlite');calls=[]
    old=Cache.key('pairing-alignment-v2',{'endpoint':'https://api.deepseek.com','body':{}})
    cache.put(old,empty())
    try:
        result=alignment.request_plan(p,index,cache,'fake-local-key',transport=lambda *args:(calls.append(args), (json.dumps(empty()),{}))[1])
        assert len(calls)==1 and result['cached'] is False
        assert all(row['request_hash'].startswith('pairing-alignment-v3:') for row in cache.records())
        assert cache.get(old)==empty()
    finally:cache.close()


def test_unlinked_untimed_originals_remain_semantic_reference_not_guessed_pairings(tmp_path):
    p,index=fixture(3)
    for row in p.mappings: row.lyric_ids=[]
    for row in p.lyrics: row.start=row.end=None
    original=deepcopy(p.to_dict());seen=[];cache=Cache(tmp_path/'unlinked.sqlite')
    def provider(body,_):
        wire=json.loads(body['messages'][1]['content']);seen.append(wire)
        assert len(wire['source'])==3 and all(r['unlinked_reference'] for r in wire['source'])
        assert all(row['lyric_ids']==[] for row in wire['mappings'])
        raw=empty(); raw['semantic_issues']=[{'mapping_ids':[wire['mappings'][0]['id']],
            'lyric_ids':[wire['source'][2]['id']], 'visual_ids':[], 'kind':'uncertain','reason':'Unlinked semantic reference.'}]
        return json.dumps(raw),{}
    try:
        result=alignment.request_plan(p,index,cache,'fake-local-key',transport=provider)
        assert result['plan']['response']['semantic_issues'][0]['lyric_ids']==['long_lyric_2']
        assert p.to_dict()['mappings']==original['mappings'] and len(seen)==1
    finally:cache.close()


def test_oversized_unlinked_reference_is_actionable_preflight_not_empty_success(tmp_path):
    p,index=fixture(1);p.mappings[0].lyric_ids=[];p.lyrics[0].start=p.lyrics[0].end=None
    p.lyrics[0].text='x'*13000;cache=Cache(tmp_path/'reference-limit.sqlite')
    try:
        result=alignment.request_plan(p,index,cache,'fake-local-key',transport=lambda *_:pytest.fail('no POST'))
        assert result['status']=='source_scope_required' and '明确关联原文' in result['message']
        assert 'plan' not in result and not cache.records()
    finally:cache.close()


def test_waiting_same_run_can_cancel_without_stealing_or_sending(tmp_path):
    import time
    p,index=fixture(1);path=tmp_path/'waiting.sqlite';started=threading.Event();release=threading.Event();stop=threading.Event();calls=[]
    def provider(body,_):
        calls.append(1);started.set();assert release.wait(3);return json.dumps(empty()),{}
    def owner():
        cache=Cache(path)
        try:return alignment.request_plan(p,index,cache,'fake-local-key',transport=provider)
        finally:cache.close()
    with ThreadPoolExecutor(max_workers=1) as pool:
        first=pool.submit(owner);assert started.wait(3)
        timer=threading.Timer(.1,stop.set);timer.start();cache=Cache(path);begin=time.monotonic()
        try:
            with pytest.raises(InterruptedError):
                alignment.request_plan(Project.from_dict(p.to_dict()),index,cache,'fake-local-key',transport=provider,cancel=stop.is_set)
            assert time.monotonic()-begin<1 and calls==[1]
        finally:cache.close();release.set();timer.join()
        assert first.result(timeout=3)['status']=='no_changes'


@pytest.mark.parametrize('role',['source_ocr','source_asr','source_import','source'])
def test_nontranslation_main_rows_are_not_sent_as_chinese(role,tmp_path):
    from lyricsync.api import suggest
    p,index=fixture(2);p.mappings[0].text_role=role;cache=Cache(tmp_path/'roles.sqlite')
    try:
        with pytest.raises(ValueError,match='仅支持中文译文'):
            alignment.request_plan(p,index,cache,'fake-local-key',transport=lambda *_:pytest.fail('no POST'))
        with pytest.raises(ValueError,match='仅支持中文译文'):
            suggest(p,cache,'fake-local-key',transport=lambda *_:pytest.fail('no POST'))
        payload=alignment.build_payload(p,index,[p.mappings[1].id])
        assert all(r['id']!=p.mappings[0].id for r in payload['mappings'])
        assert not cache.records()
    finally:cache.close()


def test_new_default_text_role_keeps_legacy_candidate_signature_but_source_invalidates():
    from dataclasses import dataclass, asdict
    from tests.test_api_migration_alignment import fixture as old_fixture, response, split
    @dataclass
    class RoleMapping(Mapping):
        text_role: str = 'translation'
    p,index=old_fixture();plan=alignment.make_plan(p,index,response(split()));record=candidate_record(p,index,plan)
    p.mappings=[RoleMapping(**asdict(row)) for row in p.mappings]
    assert restore_candidate(p,index,record)==plan
    p.mappings[0].text_role='source_ocr'
    with pytest.raises(ValueError):restore_candidate(p,index,record)


def test_explicit_command_after_fixed_auth_can_retry_known_refusal(tmp_path):
    from urllib.error import HTTPError
    p,index=fixture(1);cache=Cache(tmp_path/'auth-fixed.sqlite');calls=[]
    def provider(body,key):
        calls.append(1)
        if len(calls)==1:raise HTTPError('https://mock.invalid',401,'synthetic refusal',{},None)
        return json.dumps(empty()),{}
    try:
        failed=alignment.request_plan(p,index,cache,'fake-local-key-one',transport=provider)
        assert failed['code']=='authentication_failed' and len(calls)==1
        fixed=alignment.request_plan(p,index,cache,'fake-local-key-two',transport=provider)
        assert fixed['status']=='no_changes' and len(calls)==2 and len(cache.records())==2
    finally:cache.close()
