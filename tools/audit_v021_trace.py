"""Audit requirement trace completeness, not product correctness."""
from pathlib import Path
import hashlib,json,sys
ROOT=Path(__file__).resolve().parents[1]
def audit(rows):
    issues=[]; seen=set()
    allowed={'implemented','partially_implemented','not_implemented','verified_existing','context','hypothesis_tested','hypothesis_unverified'}
    for row in rows:
        key=row.get('id')
        if not key or key in seen: issues.append(f'{key}: missing/duplicate id')
        seen.add(key)
        if not row.get('original'): issues.append(f'{key}: original text missing')
        if row.get('status') not in allowed: issues.append(f'{key}: disposition missing')
        if row.get('status') in {'implemented','verified_existing','hypothesis_tested'} and not row.get('verification'): issues.append(f'{key}: evidence missing')
        if row.get('status') in {'partially_implemented','not_implemented','hypothesis_unverified'} and not row.get('limitations'): issues.append(f'{key}: explicit limitation missing')
        if row.get('status') in {'implemented','partially_implemented','verified_existing'} and not row.get('implementation'): issues.append(f'{key}: implementation mapping missing')
    return issues
def audit_sources(rows, requirements, ledger):
    issues=[]
    hashes={r['name']:r['sha256'] for r in ledger['requirements']}
    sources={row.get('source') for row in rows}
    expected={}
    for source in sorted(s for s in sources if s):
        path=requirements/source
        if not path.is_file():
            issues.append(f'{source}: source file missing'); continue
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=hashes.get(source):
            issues.append(f'{source}: original source hash mismatch')
        for line,text in enumerate(raw.decode('utf-8-sig').splitlines(),1):
            if text.strip(): expected[(source,line)]=text
    seen=set()
    for row in rows:
        key=(row.get('source'),row.get('line'))
        if key in seen: issues.append(f'{key}: duplicated original line')
        seen.add(key)
        if expected.get(key)!=row.get('original'):
            issues.append(f'{key}: original line changed or unknown')
    for key in expected.keys()-seen: issues.append(f'{key}: original line omitted')
    required={name for name in hashes if name!='DEV10_USER_TEST_FEEDBACK_20261116.md'}
    if sources!=required: issues.append('source document inventory mismatch')
    if len(rows)!=ledger.get('requirements_items'): issues.append('source line count mismatch')
    return issues

if __name__=='__main__':
    rows=json.loads((ROOT/'docs/v0.21/requirements_trace.json').read_text(encoding='utf-8'))
    issues=audit(rows)+audit_sources(rows,ROOT/'docs/v0.21/requirements',json.loads((ROOT/'docs/v0.21/WORK_LEDGER.json').read_text(encoding='utf-8')))
    print(json.dumps({'items':len(rows),'ok':not issues,'issues':issues},ensure_ascii=False,indent=2))
    raise SystemExit(bool(issues))
