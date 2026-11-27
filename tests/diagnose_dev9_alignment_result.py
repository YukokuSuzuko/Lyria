"""Count strict candidate timings from saved real worker JSON without inference."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from lyricsync.model import Mapping
from lyricsync.project_document import load_document
from lyricsync.candidate_tracks import capture_source
from lyricsync.local_alignment import candidate_from_alignment, alignment_result_summary, _normal


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recovery',type=Path,required=True)
    parser.add_argument('--request',type=Path,required=True)
    parser.add_argument('--response',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    original_bytes=args.recovery.read_bytes()
    document=load_document(args.recovery,recover=True)
    snapshot_before=document.snapshot()
    request=json.loads(args.request.read_text(encoding='utf-8'))
    response=json.loads(args.response.read_text(encoding='utf-8'))
    rows=request['rows']
    clone=deepcopy(document)
    # Worker-level request uses lyric IDs rather than editable timeline IDs.
    # This detached source timeline measures conversion only, not safe apply to
    # the recovery's different existing 66-row manual timeline.
    clone.project.mappings=[Mapping(row['id'],[],text_override=row['text'],text_role='source_import') for row in rows]
    missing_historical_media=any(not Path(path).is_file() for path in document.project.inputs.values() if path)
    # This is a new converter-only snapshot of the actual saved worker input.
    # Historical missing paths are reported, never silently restored/applied.
    clone.project.inputs={'audio':request['audio_wav']}
    candidate=candidate_from_alignment(capture_source(clone,'vocal'),rows,response)
    missing_reasons=Counter(reason for row in candidate['rows'] if row['start'] is None for reason in row['reasons']
                            if not reason.startswith(('local-model:','原词自动','参考分','模型精度')))
    valid=[row for row in candidate['rows'] if row['start'] is not None and row['end'] is not None]
    data=dict(**alignment_result_summary(candidate),
        model_duration=response['duration'],
        normalized_character_coverage=(''.join(_normal(row['text']) for row in rows)==''.join(_normal(word['text']) for word in response['words'])),
        valid_row_numbers=[i+1 for i,row in enumerate(candidate['rows']) if row['start'] is not None],
        timed_extent=[min(row['start'] for row in valid),max(row['end'] for row in valid)] if valid else None,
        missing_reasons=dict(missing_reasons),
        request_sha256=hashlib.sha256(args.request.read_bytes()).hexdigest(),
        response_sha256=hashlib.sha256(args.response.read_bytes()).hexdigest(),
        recovery_sha256=hashlib.sha256(original_bytes).hexdigest(),
        original_mapping_count=len(document.project.mappings),source_rows=len(rows),
        historical_media_paths_missing=missing_historical_media,
        original_file_unchanged=args.recovery.read_bytes()==original_bytes,
        original_project_unchanged=document.snapshot()==snapshot_before,
        converter_only=True,applied_to_user_timeline=False,model_rerun=False)
    assert data['original_file_unchanged'] and data['original_project_unchanged']
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(data,ensure_ascii=False))


if __name__=='__main__':main()
