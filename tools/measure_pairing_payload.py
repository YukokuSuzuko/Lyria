"""Offline payload measurement. No credentials, network, app restoration, or input writes."""
import argparse, ast, hashlib, json, subprocess, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lyricsync.model import Project
from lyricsync.frames import FrameIndex
from lyricsync.api_alignment import build_payload, request_payload, wire_aliases, SYSTEM


def extract_system(source_text):
    """Read a literal constant only. Never import or execute baseline code."""
    try:
        tree=ast.parse(source_text)
        values=[node.value for node in tree.body if isinstance(node,ast.Assign)
                and any(isinstance(target,ast.Name) and target.id=='SYSTEM' for target in node.targets)]
        if len(values)!=1: raise ValueError('Expected exactly one SYSTEM assignment')
        value=ast.literal_eval(values[0])
        if not isinstance(value,str) or not value.strip(): raise ValueError('SYSTEM must be a nonempty string literal')
        return value
    except (SyntaxError,ValueError,TypeError) as exc:
        raise ValueError('Cannot extract a literal SYSTEM constant from the baseline source.') from exc


def load_baseline_system(*, baseline_source=None, baseline_system=None, repository=None):
    if baseline_source and baseline_system:
        raise ValueError('Choose only one of --baseline-source and --baseline-system.')
    try:
        if baseline_source:
            path=Path(baseline_source)/'lyricsync'/'api_alignment.py'
            return extract_system(path.read_text(encoding='utf-8-sig')), 'explicit baseline source (AST literal only)'
        if baseline_system:
            value=Path(baseline_system).read_text(encoding='utf-8-sig')
            if not value.strip(): raise ValueError('The baseline SYSTEM text file is empty.')
            return value, 'explicit baseline SYSTEM text file'
        root=Path(repository) if repository is not None else Path(__file__).resolve().parents[1]
        old=subprocess.check_output(['git','-C',str(root),'show','b65b395:lyricsync/api_alignment.py'],
                                    encoding='utf-8',stderr=subprocess.PIPE)
        return extract_system(old), 'development Git baseline b65b395'
    except (OSError,UnicodeError,subprocess.CalledProcessError) as exc:
        raise ValueError('Baseline unavailable. Supply --baseline-source <original-0.12-source-directory> '
                         'or --baseline-system <plain-text-system-prompt-file>. '
                         'The optional Git fallback requires development commit b65b395; source ZIPs do not include it.') from exc


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project');parser.add_argument('output')
    baseline=parser.add_mutually_exclusive_group()
    baseline.add_argument('--baseline-source',help='Original 0.12 source root; AST-read lyricsync/api_alignment.py SYSTEM only')
    baseline.add_argument('--baseline-system',help='UTF-8 plain-text file containing the exact baseline system prompt')
    args=parser.parse_args(argv)
    try:
        old_system,baseline_origin=load_baseline_system(baseline_source=args.baseline_source,baseline_system=args.baseline_system)
    except ValueError as exc: parser.error(str(exc))
    # Validate baseline before opening the project or decoding its media.
    source=Path(args.project);p=Project.from_dict(json.loads(source.read_text(encoding='utf-8'))['project'])
    video=SimpleNamespace(path=p.inputs['video'],origin=p.video['origin'],info=p.video)
    index=FrameIndex.build(video)
    local=build_payload(p,index);wire=request_payload(local)
    size=lambda v:len(json.dumps(v,ensure_ascii=False,allow_nan=False))
    new_size=lambda v:len(json.dumps(v,ensure_ascii=False,allow_nan=False,separators=(',',':')))
    maps=wire_aliases(local)
    result={'basis':'Offline reconstruction from the same saved project and newly decoded local PTS; not a captured historical request. Characters are not tokens and do not establish online latency.',
            'baseline_system_origin':baseline_origin,'project_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'mapping_count':len(p.mappings),'source_count':len(p.lyrics),'ocr_count':len(p.visuals),'decoded_frames':len(index.stamps),
            'old_system_chars':len(old_system),'new_system_chars':len(SYSTEM),'old_body_reconstructed_chars':size(local),'new_body_exact_chars':new_size(wire),
            'old_source_ocr_chars':size({'source':local['source'],'ocr':local['ocr']}),'new_source_ocr_chars':new_size({'source':wire['source'],'ocr':wire['ocr']}),
            'same_full_source_ids':[maps['l'][r['id']] for r in local['source']]==[r['id'] for r in wire['source']],'same_full_mapping_ids':[maps['m'][r['id']] for r in local['mappings']]==[r['id'] for r in wire['mappings']],
            'body_reduction_percent':round((1-new_size(wire)/size(local))*100,2)}
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
