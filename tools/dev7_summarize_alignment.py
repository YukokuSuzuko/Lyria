"""Summarize private local experiment receipts without publishing lyric text."""
from pathlib import Path
import argparse,json,statistics

def med(v):return statistics.median(v) if v else None

def summarize(root):
    records=[]
    runs=[('', 'results','baseline-word'),('', 'results-fixed','fixed-word'),('', 'results-sentence-experiment','research-sentence'),('user-katan','results','fixed-word'),('user-katan','results-sentence-experiment','research-sentence'),('user-into','results','fixed-word'),('user-into','results-sentence-experiment','research-sentence')]
    for sub,folder,label in runs:
        base=root/sub
        if not (base/'fixture.json').exists():continue
        fixture=json.loads((base/'fixture.json').read_text(encoding='utf-8'))
        clips={c['id']:c for c in fixture['clips']}
        for path in sorted((base/folder).glob('*.json')):
            data=json.loads(path.read_text(encoding='utf-8'))
            if 'model' not in data or 'clip' not in data:continue
            clip=clips[data['clip']];refs={r['id']:r for r in clip['rows']}
            record=dict(dataset=fixture.get('id','sanshoku'),clip=clip['id'],variant=data['variant'],
                        model=data['model'],prompt_strategy=label if data['model']=='qwen' else 'research-ctc',
                        reading_mode=data.get('reading_mode'),input_sha256=data['input_sha256'],
                        reference_is_acoustic_ground_truth=False,source_json=str(path.relative_to(root)),
                        status=data.get('status','completed'))
            if record['status']!='completed':
                record['error']=data.get('error');records.append(record);continue
            is_qwen=data['model']=='qwen'
            rows=data['raw_diagnostic_rows'] if is_qwen else data['rows']
            details=[]
            for row in rows:
                ref=refs[row['id']];start=row.get('start_time');end=row.get('end_time')
                value=dict(id=row['id'],start_project_seconds=None if start is None else start+clip['offset_seconds'],
                           end_project_seconds=None if end is None else end+clip['offset_seconds'])
                if start is not None and end is not None:
                    value.update(reference_start_difference_seconds=start+clip['offset_seconds']-ref['reference_start'],
                                 reference_end_difference_seconds=end+clip['offset_seconds']-ref['reference_end'])
                    if 'formal_translation_reference_start' in ref:
                        value.update(formal_subtitle_start_difference_seconds=start+clip['offset_seconds']-ref['formal_translation_reference_start'],formal_subtitle_end_difference_seconds=end+clip['offset_seconds']-ref['formal_translation_reference_end'])
                if is_qwen:
                    accepted=next(v for v in data['candidate_rows'] if v['sentence_id']==row['id'])
                    value.update(candidate_status=accepted['status'],withheld_reason=accepted['reasons'][-1] if accepted['status']!='ready' else None,crossed_boundary=row['crossed_boundary'],zero_duration_word_count=sum(w['end_time']<=w['start_time'] for w in row['words']),repaired_word_count=sum(bool(w.get('repaired')) for w in row['words']))
                else:value['path_score_not_confidence']=row['path_score']
                details.append(value)
            valid=[d for d in details if 'reference_start_difference_seconds' in d]
            record.update(row_count=len(rows),returned_path_count=len(valid),per_row=details,
                median_absolute_reference_start_difference_seconds=med([abs(v['reference_start_difference_seconds']) for v in valid]),
                median_absolute_reference_end_difference_seconds=med([abs(v['reference_end_difference_seconds']) for v in valid]),
                max_absolute_reference_start_difference_seconds=max((abs(v['reference_start_difference_seconds']) for v in valid),default=None),
                max_absolute_reference_end_difference_seconds=max((abs(v['reference_end_difference_seconds']) for v in valid),default=None),
                both_endpoints_within_500ms_of_reference=sum(abs(v['reference_start_difference_seconds'])<=.5 and abs(v['reference_end_difference_seconds'])<=.5 for v in valid))
            if is_qwen:
                record.update(strict_ready_count=sum(v['candidate_status']=='ready' for v in details),
                              strict_missing_count=sum(v['candidate_status']!='ready' for v in details),
                              cross_boundary_row_count=sum(v['crossed_boundary'] for v in details),wall_seconds=data['wall_seconds'],timings_seconds=data['response'].get('timings_seconds'))
            else:record.update(inference_seconds=data['inference_seconds'],ctc_seconds=data['ctc_seconds'],load_seconds_once=data['load_seconds_once'])
            if any('formal_subtitle_start_difference_seconds' in v for v in valid):
                record.update(median_absolute_formal_start_difference_seconds=med([abs(v['formal_subtitle_start_difference_seconds']) for v in valid]),median_absolute_formal_end_difference_seconds=med([abs(v['formal_subtitle_end_difference_seconds']) for v in valid]))
            records.append(record)
    return dict(scope='Reference deviations, not accuracy or user acceptance. Forced-path row counts cannot measure missing/extra sung lines.',records=records)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--output',required=True);args=p.parse_args()
    result=summarize(Path(args.root));Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    for record in result['records']:
        if record['variant']=='vocals16' and record.get('reading_mode')!='automatic':
            print({k:round(v,3) if isinstance(v,float) else v for k,v in record.items() if k in ('dataset','clip','prompt_strategy','model','strict_ready_count','row_count','median_absolute_reference_start_difference_seconds','median_absolute_reference_end_difference_seconds','both_endpoints_within_500ms_of_reference','median_absolute_formal_start_difference_seconds','median_absolute_formal_end_difference_seconds')})