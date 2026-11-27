"""Private original inventory audit plus portable synthetic hash/line tests.

Missing private history is an explicit skip, never a pass. Existing but corrupt
history must fail. Run tools/audit_v021_trace.py in the full local workspace to
require the real historical evidence; its fail-closed CLI remains unchanged.
"""
from copy import deepcopy
import pytest
import hashlib,json,importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('audit_trace',ROOT/'tools/audit_v021_trace.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)


def test_original_requirement_inventory_is_exact():
    required=[ROOT/'docs/v0.21/requirements_trace.json',ROOT/'docs/v0.21/WORK_LEDGER.json',ROOT/'docs/v0.21/requirements']
    if not all(p.exists() for p in required):
        pytest.skip('私有历史证据未随源码分发；此项实际历史审计未执行，不计通过')
    rows=json.loads((ROOT/'docs/v0.21/requirements_trace.json').read_text(encoding='utf-8'))
    ledger=json.loads((ROOT/'docs/v0.21/WORK_LEDGER.json').read_text(encoding='utf-8'))
    assert audit.audit_sources(rows,ROOT/'docs/v0.21/requirements',ledger)==[]
    missing=deepcopy(rows);missing.pop()
    assert audit.audit_sources(missing,ROOT/'docs/v0.21/requirements',ledger)
    changed=deepcopy(rows);changed[0]['original']+=' changed'
    assert audit.audit_sources(changed,ROOT/'docs/v0.21/requirements',ledger)


def test_completion_claim_needs_verification_and_incomplete_needs_limits():
    row={'id':'X','original':'original','status':'implemented','implementation':['x.py']}
    assert audit.audit([row])
    row['verification']=['test_x.py'];assert not audit.audit([row])
    row['status']='not_implemented';assert audit.audit([row])
    row['limitations']=['not done'];assert not audit.audit([row])
    assert audit.audit([row,row])


def test_synthetic_source_hash_inventory_and_line_integrity(tmp_path):
    # Newly generated text, never an altered copy of private historical advice.
    raw=b'Synthetic alpha\n\nSynthetic beta\n';name='synthetic.md'
    (tmp_path/name).write_bytes(raw)
    rows=[{'id':f'S-{i}','source':name,'line':i,'original':t}
          for i,t in enumerate(raw.decode().splitlines(),1) if t.strip()]
    ledger={'requirements':[{'name':name,'sha256':hashlib.sha256(raw).hexdigest()}],
            'requirements_items':len(rows)}
    assert audit.audit_sources(rows,tmp_path,ledger)==[]
    assert audit.audit_sources(rows[:-1],tmp_path,ledger)
    changed=deepcopy(rows);changed[0]['original']+=' changed'
    assert audit.audit_sources(changed,tmp_path,ledger)
    assert audit.audit_sources(rows+rows[:1],tmp_path,ledger)
    wrong_hash=deepcopy(ledger);wrong_hash['requirements'][0]['sha256']='0'*64
    assert audit.audit_sources(rows,tmp_path,wrong_hash)
