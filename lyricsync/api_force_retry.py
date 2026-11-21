"""Exact, one-window retry offers. No authorization is inferred from the journal."""
from copy import deepcopy
import hashlib
import json
import re
import uuid

UNKNOWN = {'unknown_outcome','cancelled','read_timeout','connection_timeout','timeout',
           'total_timeout','response_incomplete','network_error','local_storage_busy'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def offer(run_hash, revision, number, spec, entry):
    if entry.get('status') not in ('requesting','blocked') or entry.get('code','unknown_outcome') not in UNKNOWN:
        return None
    request_hash=entry.get('request_hash')
    if not isinstance(request_hash,str) or not request_hash.startswith('pairing-alignment-v3:'):
        return None
    return {'version':1,'run_hash':run_hash,'revision':revision,'window':number,
            'request_hash':request_hash,'entry_signature':digest(entry),
            'kind':spec['kind'],'targets':deepcopy(entry.get('targets',spec['ids']))}


def namespace(entry, base):
    value=entry.get('request_namespace',base)
    if value!=base and (not isinstance(value,str) or not re.fullmatch(re.escape(base)+r':forced:[0-9a-f]{32}',value)):
        raise ValueError('重发请求身份无效；未发送请求。')
    return value


def consume(entry, base, request_hash):
    """Caller owns the run lease and must durably save before starting the POST."""
    history=list(entry.get('previous_request_hashes',[]))
    history.append(request_hash)
    entry.clear()
    entry.update(status='requesting',code='unknown_outcome',
                 request_namespace=base+':forced:'+uuid.uuid4().hex,
                 previous_request_hashes=list(dict.fromkeys(history)))


def lineage(entry):
    return {k:deepcopy(entry[k]) for k in ('request_namespace','previous_request_hashes') if k in entry}
