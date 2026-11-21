"""0.13 pairing-only contracts, offline providers and isolated storage only."""
import json
from copy import deepcopy
import pytest
from lyricsync.api import validate_pairing_review, suggest, format_review_suggestions
from lyricsync.api_alignment import build_payload, request_payload, validate_pairing_plan, request_plan
from lyricsync.api_client import request_json, ApiRequestError
from lyricsync.storage import Cache
from tests.ai_pairing_helpers import pairing_reply, pairing_plan_reply
from tests.test_api_migration_alignment import fixture, split, response

@pytest.mark.parametrize('verdict',['match','mismatch','uncertain'])
def test_three_verdicts(verdict):
    raw={'reviews':[{'mapping_id':'m','verdict':verdict,'reason':'One sentence.'}]}
    assert validate_pairing_review(raw,{'m'})==raw

@pytest.mark.parametrize('change',[
    lambda v:v.update(corrections=[]),
    lambda v:v['reviews'][0].update(reference_meaning='translation'),
    lambda v:v['reviews'][0].update(verdict='yes'),
    lambda v:v['reviews'][0].update(mapping_id='other'),
    lambda v:v['reviews'][0].update(reason='long'*100),
    lambda v:v['reviews'].clear(),
    lambda v:v['reviews'].append(deepcopy(v['reviews'][0])),
])
def test_bad_review_contract(change):
    raw={'reviews':[{'mapping_id':'m','verdict':'uncertain','reason':'Insufficient evidence.'}]}
    change(raw)
    with pytest.raises(ValueError):validate_pairing_review(raw,{'m'})

def test_wire_smaller_full_scope_and_no_text_mutation(tmp_path):
    p,index=fixture();p.settings.update(api_enabled=True,api_retries=0)
    original=deepcopy(p.to_dict());local=build_payload(p,index);wire=request_payload(local)
    assert set(wire)=={'source','ocr','mappings','protocol','scope'}
    assert wire['source']==local['source']
    assert [r['id'] for r in wire['mappings']]==[r['id'] for r in local['mappings']]
    assert all(set(v)=={'id','text','timing_usable','time_order','boundary_uncertain'} for v in wire['ocr'])
    assert len(json.dumps(wire))<len(json.dumps(local))
    cache=Cache(tmp_path/'cache.sqlite');calls=[]
    def provider(body,key):
        calls.append(body);return json.dumps(pairing_plan_reply(response(split()))),{}
    try:
        result=request_plan(p,index,cache,'fake',transport=provider)
        assert result['status']=='proposed'
        assert p.to_dict()['mappings']==original['mappings']
        assert p.to_dict()['lyrics']==original['lyrics']
        assert p.to_dict()['translations']==original['translations']
        assert request_plan(p,index,cache,'fake',transport=provider)['cached']
        assert len(calls)==1
    finally:cache.close()

def test_pairing_plan_rejects_removed_fields():
    p,index=fixture();raw=pairing_plan_reply(response(split()));payload=build_payload(p,index)
    validate_pairing_plan(raw,payload)
    raw['operations'][0]['reference_meaning']='Forbidden'
    with pytest.raises(ValueError):validate_pairing_plan(raw,payload)

def test_cancellation_discards_late_response(tmp_path):
    cache=Cache(tmp_path/'cancel.sqlite');cancelled=False;stages=[]
    def provider(body,key):
        nonlocal cancelled
        cancelled=True
        return '{"ok":true}',{}
    try:
        with pytest.raises(InterruptedError):
            request_json(cache,'fake',{},lambda r:r,namespace='cancel',transport=provider,
                         cancel=lambda:cancelled,on_attempt=lambda *a:stages.append(a))
        assert cache.records()[0]['status']=='cancelled'
        assert not cache.db.execute('SELECT * FROM cache').fetchall()
    finally:cache.close()

def test_timeout_stops_after_one_attempt_without_inventing_cause(tmp_path):
    p,index=fixture();p.settings.update(api_enabled=True,api_retries=3);cache=Cache(tmp_path/'timeout.sqlite');updates=[]
    def provider(*args):raise TimeoutError()
    try:
        result=request_plan(p,index,cache,'fake',transport=provider,progress=lambda f,m:updates.append(m))
        assert result['code']=='timeout' and len(cache.records())==1
        assert any('尝试 1/4' in m and '请求超时' in m for m in updates)
        assert '请求超时' in result['message']
    finally:cache.close()


def test_long_alias_roundtrip_rejects_unknown_and_duplicate(tmp_path):
    from lyricsync.api_alignment import wire_aliases, restore_wire_ids
    p,index=fixture()
    remap={r.id:'long_original_identity_'+r.id for rows in (p.mappings,p.lyrics,p.visuals) for r in rows}
    for rows in (p.mappings,p.lyrics,p.visuals):
        for row in rows: row.id=remap[row.id]
    for row in p.mappings:
        row.lyric_ids=[remap[i] for i in row.lyric_ids]
        row.visual_ids=[remap[i] for i in row.visual_ids]
    payload=build_payload(p,index);wire=request_payload(payload);maps=wire_aliases(payload)
    assert 'long_original_identity_' not in json.dumps(wire)
    value=pairing_plan_reply(response(split()))
    def convert(node):
        if isinstance(node,str):
            if node in remap:
                kind=node[0];return maps[kind][remap[node]]
            return node
        if isinstance(node,list):return [convert(v) for v in node]
        if isinstance(node,dict):return {k:convert(v) for k,v in node.items()}
        return node
    raw=convert(value);validate_pairing_plan(raw,payload)
    restored=restore_wire_ids(raw,payload)
    assert restored['operations'][0]['mapping_ids']==[remap['m0']]
    cache=Cache(tmp_path/'alias.sqlite')
    try:
        result=request_plan(p,index,cache,'fake',transport=lambda *_:(json.dumps(raw),{}))
        assert result['status']=='proposed'
        assert result['plan']['diff'][0]['mapping_ids']==[remap['m0']]
        assert request_plan(p,index,cache,'fake',transport=lambda *_:pytest.fail('cached'))['cached']
    finally:cache.close()
    bad=deepcopy(raw);bad['operations'][0]['mapping_ids']=['unknown-alias']
    with pytest.raises(ValueError):validate_pairing_plan(bad,payload)
    bad=deepcopy(raw);bad['operations'][0]['mapping_ids']*=2
    with pytest.raises(ValueError):validate_pairing_plan(bad,payload)
    bad=deepcopy(raw);bad['operations'][0]['mapping_ids']=[remap['m0']]
    with pytest.raises(ValueError):validate_pairing_plan(bad,payload)


def test_long_review_aliases_restore_only_selected_row(tmp_path):
    p,index=fixture();p.settings.update(api_enabled=True,api_retries=0)
    p.mappings[0].id='mapping_with_long_original_identity'
    calls=[];cache=Cache(tmp_path/'review-alias.sqlite')
    def provider(body,key):
        calls.append(body)
        assert 'mapping_with_long_original_identity' not in body['messages'][1]['content']
        return pairing_reply(body),{}
    try:
        result=suggest(p,cache,'fake',mapping_ids=[p.mappings[0].id],transport=provider)
        assert result[0]['mapping_id']==p.mappings[0].id
        assert result[0]['suggestion']['mapping_id']==p.mappings[0].id
        assert result[0]['status']=='processed'
        suggest(p,cache,'fake',mapping_ids=[p.mappings[0].id],transport=provider)
        assert len(calls)==1
    finally:cache.close()
