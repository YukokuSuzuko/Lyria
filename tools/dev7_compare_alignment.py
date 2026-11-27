"""Explicit local experiment: Qwen production worker vs research NextFire.

Caller owns the manifest and supplies same-source PCM excerpts and references.
No network downloads, remote model code, project mutation or automatic adoption.
Run --help for input/cache requirements. Evidence includes actual inputs/hashes,
raw word output, production withheld rows, distinct inference and loading times.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', DO_NOT_TRACK='1')


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def digest(path):
    with open(path, 'rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def ffmpeg(source, output, gain=1.0):
    subprocess.run([str(ROOT/'resources/ffmpeg.exe'), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                    '-i', str(source), '-vn', '-ar', '16000', '-ac', '1', '-af', f'volume={gain:.12f}',
                    '-c:a', 'pcm_s16le', str(output)], check=True)


def qwen_case(clip, variant, audio, args):
    from lyricsync.local_models import QWEN
    from lyricsync.local_alignment_runtime import run_worker
    from lyricsync.model import Project, Mapping
    from lyricsync.project_document import ProjectDocument
    from lyricsync.candidate_tracks import capture_source
    from lyricsync.local_alignment import candidate_from_alignment, _normal
    request = dict(operation='align', model_id=QWEN.id, cache_root=args.cache, device='cuda:0',
                   language=clip.get('language','Japanese'), rows=[dict(id=r['id'], text=r['text']) for r in clip['rows']], audio_wav=str(audio))
    start=time.perf_counter()
    response=run_worker(request,args.runtime,script=(ROOT/'tools/dev7_qwen_sentence_worker.py') if args.qwen_sentence_experiment else None,progress=lambda p,m:print('qwen',clip['id'],variant,p,m,flush=True))
    wall=time.perf_counter()-start
    document=ProjectDocument(Project(mappings=[Mapping(r['id'],[],text_override=r['text']) for r in clip['rows']]))
    candidate=candidate_from_alignment(capture_source(document,'vocal'),request['rows'],response,
                                       offset_seconds=clip['offset_seconds'])
    # Diagnostic raw endpoints are NOT accepted candidates. Track exactly which
    # token flags caused sentence withholding rather than calling it separation failure.
    raw_rows=[];pos=0
    expected=''.join(_normal(r['text']) for r in clip['rows'])
    observed=''.join(_normal(w['text']) for w in response['words'])
    spans=[]
    for word in response['words']:
        size=len(_normal(word['text']));spans.append((pos,pos+size,word));pos+=size
    pos=0
    for row in clip['rows']:
        end=pos+len(_normal(row['text']));words=[w for a,b,w in spans if a<end and b>pos]
        crossed=any(a<pos or b>end for a,b,w in spans if a<end and b>pos)
        raw_rows.append(dict(id=row['id'],start_time=words[0]['start_time'] if words and expected==observed else None,
            end_time=words[-1]['end_time'] if words and expected==observed else None,
            crossed_boundary=crossed,words=words))
        pos=end
    result=dict(model='qwen',variant=variant,clip=clip['id'],input_sha256=digest(audio),transcript=request['rows'],
                wall_seconds=wall,word_text_coverage_exact=expected==observed,response=response,
                candidate_rows=candidate['rows'],raw_diagnostic_rows=raw_rows)
    save(Path(args.evidence)/f"{clip['id']}-{variant}-qwen.json",result)
    return result


def nextfire_cases(clips,args):
    import numpy as np
    import torch
    from transformers import AutoProcessor, AutoModelForCTC
    from lyricsync.experimental_nextfire_alignment import align_reading_rows, MODEL_SHA256, MODEL_REVISION
    model_path=Path(args.nextfire_model)
    if digest(model_path/'model.safetensors')!=MODEL_SHA256:
        raise ValueError('NextFire model content does not match the pinned official SHA256')
    start=time.perf_counter()
    processor=AutoProcessor.from_pretrained(str(model_path),local_files_only=True,trust_remote_code=False)
    model=AutoModelForCTC.from_pretrained(str(model_path),local_files_only=True,trust_remote_code=False,
                                         use_safetensors=True,dtype=torch.float32,attn_implementation='sdpa').to('cuda:0').eval()
    torch.cuda.synchronize();load_seconds=time.perf_counter()-start
    results=[]
    for clip in clips:
        for variant in ('mix16','vocals16'):
            audio=Path(clip[variant])
            with wave.open(str(audio),'rb') as f:
                assert (f.getnchannels(),f.getsampwidth(),f.getframerate())==(1,2,16000)
                samples=f.getnframes();values=np.frombuffer(f.readframes(samples),dtype='<i2').astype(np.float32)/32768
            start=time.perf_counter()
            inputs=processor(audio=values,sampling_rate=16000,return_tensors='pt').to('cuda:0')
            with torch.inference_mode():
                output=model(**inputs)
                emissions=output.logits.log_softmax(-1)[0].float().cpu().numpy()
            torch.cuda.synchronize();inference=time.perf_counter()-start
            np.save(Path(args.evidence)/f"{clip['id']}-{variant}-nextfire-emissions.npy",emissions)
            for reading_mode in ('reviewed','automatic'):
                rows=deepcopy(clip['rows'])
                if reading_mode=='automatic':
                    for row in rows:row['reading']=row['reading_auto']
                start=time.perf_counter()
                try:
                    aligned=align_reading_rows(emissions,rows,processor.tokenizer.get_vocab(),
                                              blank_id=model.config.pad_token_id,duration=samples/16000)
                except ValueError as exc:
                    # A converter failure is evidence, not consent to drop input
                    # characters or silently retry using manually corrected text.
                    save(Path(args.evidence)/f"{clip['id']}-{variant}-nextfire-{reading_mode}.json",
                         dict(model='nextfire_research_only',variant=variant,clip=clip['id'],
                              input_sha256=digest(audio),reading_mode=reading_mode,
                              status='reading_rejected',error=str(exc),original_reading_rows=rows))
                    print('nextfire reading rejected',clip['id'],variant,reading_mode,str(exc),flush=True)
                    continue
                align_seconds=time.perf_counter()-start
                result=dict(model='nextfire_research_only',revision=MODEL_REVISION,variant=variant,clip=clip['id'],
                            input_sha256=digest(audio),reading_mode=reading_mode,load_seconds_once=load_seconds,
                            inference_seconds=inference,ctc_seconds=align_seconds,rows=aligned,
                            frames=len(emissions),duration=samples/16000,device=str(next(model.parameters()).device),
                            warning='A forced CTC path always covers the supplied text if feasible; this is not evidence of no missing or extra sung lines.')
                save(Path(args.evidence)/f"{clip['id']}-{variant}-nextfire-{reading_mode}.json",result)
                results.append(result)
                print('nextfire',clip['id'],variant,reading_mode,inference,align_seconds,flush=True)
    return results


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixture',required=True);p.add_argument('--evidence',required=True)
    p.add_argument('--runtime',required=True);p.add_argument('--cache',required=True)
    p.add_argument('--source-cache',help='Read-only original verified Qwen/HQ3 cache, copied into --cache')
    p.add_argument('--nextfire-model',required=True)
    p.add_argument('--stage',choices=['prepare','qwen','nextfire','all'],default='all')
    p.add_argument('--qwen-sentence-experiment',action='store_true',help='Research only: one timing unit per phrase; production default unchanged')
    p.add_argument('--inference-worker',action='store_true',help=argparse.SUPPRESS)
    args=p.parse_args();Path(args.evidence).mkdir(parents=True,exist_ok=True)
    fixture=json.loads(Path(args.fixture).read_text(encoding='utf-8-sig'))
    from lyricsync.local_models import ModelManager,QWEN,UVR
    from lyricsync.local_alignment_runtime import run_worker
    manager=ModelManager(args.cache)
    if args.stage in ('all','prepare'):
        if not args.source_cache:raise ValueError('--source-cache is required for prepare')
        original=ModelManager(args.source_cache)
        for spec in (QWEN,UVR):
            manager.import_folder(original.path(spec.id),spec.id)
        for clip in fixture['clips']:
            source=Path(clip['mix441']);output=Path(args.evidence)/f"{clip['id']}-hq3.wav"
            request=dict(operation='separate',audio_wav=str(source),output_wav=str(output),cache_root=args.cache,
                         device='cuda:0',separation_model_id=UVR.id)
            start=time.perf_counter()
            result=run_worker(request,args.runtime,progress=lambda p,m:print('separate',clip['id'],p,m,flush=True))
            result.update(wall_seconds=time.perf_counter()-start,input_sha256=digest(source),output_sha256=digest(output))
            result['alignment_gain']=1/max(1,result['vocal_peak'])
            save(Path(args.evidence)/f"{clip['id']}-separation.json",result)
            vocals=Path(args.evidence)/f"{clip['id']}-vocals16.wav"
            ffmpeg(output,vocals,result['alignment_gain'])
            clip['vocals16']=str(vocals);clip['vocals16_sha256']=digest(vocals)
        save(args.fixture,fixture)
    if args.stage in ('all','qwen'):
        for clip in fixture['clips']:
            for variant in ('mix16','vocals16'):qwen_case(clip,variant,clip[variant],args)
    if args.stage in ('all','nextfire'):
        if args.inference_worker:
            nextfire_cases(fixture['clips'],args)
        else:
            command=[args.runtime,'-I',str(Path(__file__).resolve()),'--stage','nextfire','--inference-worker']
            for key in ('fixture','evidence','runtime','cache','nextfire_model'):
                command += ['--'+key.replace('_','-'),str(getattr(args,key))]
            subprocess.run(command,check=True)
    assert digest(fixture['source'])==fixture['source_sha256']
    assert digest(fixture['reference'])==fixture['reference_sha256']
    save(Path(args.evidence)/('source-protection-'+args.stage+'.json'),dict(source_unchanged=True,reference_unchanged=True))

if __name__=='__main__':main()