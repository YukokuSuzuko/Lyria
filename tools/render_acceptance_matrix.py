"""Validate and render Lyria's evidence-based acceptance matrix (stdlib only)."""
from __future__ import annotations
from pathlib import Path
import argparse,json
from collections import Counter

def validate(data):
    rows=data['rows']; ids=[r['id'] for r in rows]
    if len(set(ids))!=len(ids): raise ValueError('Duplicate acceptance IDs')
    for version,count in [(3,24),(4,27)]:
        required={f'D{version}-{n:02d}' for n in range(1,count+1)}
        if not required.issubset(ids):raise ValueError('Feedback coverage missing: '+str(required-set(ids)))
    allowed={'未做','已改待验收','已验证','受阻'}
    for row in rows:
        if row['status'] not in allowed:raise ValueError('Invalid status '+row['id'])
        if row['status']=='已验证':
            if not row.get('test_evidence'):raise ValueError('Verified without evidence: '+row['id'])
            if row.get('requires_actual_windows_visual') and not row.get('actual_windows_evidence'):raise ValueError('Visual claim without actual Windows evidence: '+row['id'])
        if row['status']=='受阻' and not row.get('blockers'):raise ValueError('Blocked without reason: '+row['id'])
    return rows


def evidence_lines(values, empty):
    if not values:
        return ['  - ' + empty]
    output=[]
    for item in values:
        if not isinstance(item, dict):
            output.append('  - ' + str(item))
            continue
        output.append('  - ' + str(item.get('kind', '证据')) + ': ' + str(item.get('path', item)))
        if item.get('test_cases'):output.append('    - 用例：' + ', '.join(map(str,item['test_cases'])))
        if item.get('scope'):output.append('    - 范围：' + str(item['scope']))
        if item.get('sha256'):output.append('    - SHA256：' + str(item['sha256']))
    return output

def render(data):
    rows=validate(data)
    lines=['# Lyria dev5 逐项可追溯验收矩阵','','**状态：'+data['status']+'。不等于全部修复或用户验收通过。**','',f'共{len(rows)}项：dev3完整24条、dev4完整27条及后续更正、细分子检查和已确定阶段/安全/交付要求。原话与截图ID、完整旧条文和覆盖链见配套JSON及反馈快照。','','## 证据规则','','- 未做：本轮尚无完成回执，不表示旧版没有该能力。已改待验收：实现或部分测试完成，实际场景/视觉尚未齐。已验证：仅限声明范围、实际证据齐备。受阻：列明具体原因。','- 实际Windows视觉必须记录版本、尺寸、缩放、状态、原图与结果截图/运行证据；离屏、代码几何断言、静态SVG或算法图不能替代。原生动效须实际运行检查。','- mock、离屏、合成音、真实媒体、真实模型、真实API分开记；未跑、跳过、失败不能算通过。旧dev4历史通过不能自动结清dev5重复反馈。','- 只读分析项的已验证仅证明分析完成，不代表产品修复；强度统计不代表人声准确率。','- 交付前逐项对账，未满足实际验收门槛不得勾掉。','','## 总览','','| ID | 优先级 | 最新要求 | 负责 | 状态 | Windows视觉门槛 | 覆盖/更正 |','|---|---|---|---|---|---|---|']
    counts=Counter(row['status'] for row in rows)
    lines.insert(6, '当前分布：' + '；'.join(f'{status} {counts[status]}项' for status in ('未做','已改待验收','已验证','受阻')) + '。')
    for row in rows:
        visual='已留实际证据' if row.get('actual_windows_evidence') else '必须，待验' if row.get('requires_actual_windows_visual') else '非本项门槛'
        cells=[row['id'],row['priority'],row['current_requirement'],row['owner'],row['status'],visual,','.join(row.get('superseded_or_refined_by',[])) or '—']
        lines.append('| '+' | '.join(str(x).replace('|','/').replace('\n',' ') for x in cells)+' |')
    lines+=['','## 逐项变更与证据','','每条均显示当前验收范围；空栏就是尚未完成，不以摘要省略。']
    for row in rows:
        lines+=['',f"### {row['id']} · {row['title']}",'',
                '- 当前状态：'+row['status']+'；'+row['status_scope'],
                '- 来源：'+row.get('source_document','')+'；'+', '.join(row.get('conversation_references',[])),
                '- 原话/完整上下文：见配套JSON同ID的original_quotes/original_section或parent_id。',
                '- 代码：'+('; '.join(str(x) for x in row.get('code_changes',[])) or '待回执'),
                '- 提交：'+(', '.join(row.get('commit_ids',[])) or '待回执'),'- 测试证据：']
        lines+=evidence_lines(row.get('test_evidence',[]),'未提交本轮证据')
        lines+=['- 实际Windows证据：']
        lines+=evidence_lines(row.get('actual_windows_evidence',[]),
                            '待验，不得以离屏代替' if row.get('requires_actual_windows_visual') else '非本项门槛')
        for check in row.get('acceptance_checks',[]):lines.append('- 验收点：'+check)
        for blocker in row.get('blockers',[]):lines.append('- 阻碍/限制：'+blocker)
        for note in row.get('notes',[]):lines.append('- 备注：'+str(note))
    lines+=['','更新时间（UTC）：'+data.get('updated_utc',data['created_utc']),'']
    return '\n'.join(lines)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('matrix',type=Path);parser.add_argument('--output',type=Path);args=parser.parse_args();data=json.loads(args.matrix.read_text(encoding='utf-8'));text=render(data)
    if args.output:args.output.write_text(text,encoding='utf-8')
    print('Validated',len(data['rows']),'items; dev3/dev4 coverage complete; evidence gates checked.')

if __name__=='__main__':main()
