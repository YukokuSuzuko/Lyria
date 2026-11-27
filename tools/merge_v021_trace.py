"""Merge scoped dispositions without erasing any original requirement line."""
from pathlib import Path
import json,sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from audit_v021_trace import audit,audit_sources
ROOT=Path(__file__).resolve().parents[1]
DOCS=ROOT/'docs/v0.21'
FILES=('OCR_SPATIAL_TRACE.json','OCR_MAPPING_TRACE.json','alignment-core-trace.json','ALIGNMENT_UI_TRACE.json','SPECTROGRAM_TRACE.json')
ALLOWED={'implemented','partially_implemented','not_implemented','verified_existing','context','hypothesis_tested','hypothesis_unverified'}


def combined_status(statuses):
    active=set(statuses)-{'context'}
    if not active:return 'context'
    if active=={'not_implemented'}:return 'not_implemented'
    if 'not_implemented' in active or 'partially_implemented' in active:return 'partially_implemented'
    if 'hypothesis_unverified' in active:return 'hypothesis_unverified' if active<={'hypothesis_unverified','hypothesis_tested'} else 'partially_implemented'
    if active=={'hypothesis_tested'}:return 'hypothesis_tested'
    if active=={'verified_existing'}:return 'verified_existing'
    return 'implemented'


def merge():
    rows=json.loads((DOCS/'requirements_trace.json').read_text(encoding='utf-8'))
    scopes={row['id']:[] for row in rows}
    for filename in FILES:
        for item in json.loads((DOCS/filename).read_text(encoding='utf-8')):
            if item['id'] not in scopes:raise ValueError('Unknown original ID: '+item['id'])
            if item['status'] not in ALLOWED:raise ValueError('Unresolved status: '+item['id'])
            scopes[item['id']].append(dict(item,trace_file=filename))
    for row in rows:
        parts=scopes[row['id']]
        if not parts:raise ValueError('Original line missing scoped disposition: '+row['id'])
        for part in parts:
            if 'original' in part and part['original']!=row['original']:raise ValueError('Original was changed: '+row['id'])
        row['status']=combined_status(p['status'] for p in parts)
        for field in ('implementation','verification','limitations'):
            row[field]=list(dict.fromkeys(str(value) for part in parts for value in part.get(field,[])))
        row['scoped_dispositions']=[{'trace_file':p['trace_file'],'status':p['status'],'scope':p.get('scope','See the referenced scoped report')} for p in parts]
    ledger=json.loads((DOCS/'WORK_LEDGER.json').read_text(encoding='utf-8'))
    issues=audit(rows)+audit_sources(rows,DOCS/'requirements',ledger)
    if issues:raise ValueError(json.dumps(issues,ensure_ascii=False))
    (DOCS/'requirements_trace.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# v0.21 原文逐行实施追踪','', '每条保留原文；状态是实现/证据处置，不等于用户前台验收。跨专项有未完部分时保守合并，详见分项报告。','']
    for row in rows:
        lines += ['## '+row['id']+' · '+row['status'], '', '> '+row['original'].replace('\n','\n> '),'', '来源：'+row['source']+' 第'+str(row['line'])+'行']
        for label,field in [('实施','implementation'),('证据','verification'),('限制','limitations')]:
            lines += ['',label+'：']+['- '+x for x in row[field]]
        lines += ['', '分项：'+', '.join(x['trace_file']+'='+x['status'] for x in row['scoped_dispositions']), '']
    (DOCS/'REQUIREMENTS_TRACE.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'items':len(rows),'audit':'passed'},ensure_ascii=False))

if __name__=='__main__':merge()
