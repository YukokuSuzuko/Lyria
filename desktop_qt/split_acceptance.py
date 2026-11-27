"""Local packaged EXE acceptance. Fixed fixtures, no API and no model acquisition."""
import contextlib
from collections import Counter
import json
import os
from pathlib import Path
import sys
import traceback

def run(args):
    from .reading_dialog_probe import install_offline_guard
    install_offline_guard()
    assert getattr(sys,'frozen',False) and len(args)==3
    lab=Path(sys.executable).resolve().parents[2]
    out=(lab/'evidence'/args[0]).resolve()
    assert out.parent==lab/'evidence' and not out.exists()
    out.mkdir()
    report=dict(parent_pid=os.getpid(),exe=sys.executable,parts={},errors={})
    def save():
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),'utf8')
    with (out/'qa.log').open('w',encoding='utf8') as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
        from lyricsync.split_profiles import ACTIVE,REASON
        import onnxruntime as ort
        report.update(profile=ACTIVE,selection_reason=REASON,ort=ort.__version__,origins=ort.__file__)
        from lyricsync import local_alignment_runtime as runner
        original=runner.launch_owned
        launched=[]
        def capture(cmd,*a,**kw):
            child=original(cmd,*a,**kw)
            launched.append(dict(pid=child.pid,exe=cmd[0],script=cmd[4]))
            return child
        runner.launch_owned=capture
        report['workers']=launched
        def check(name,fn):
            try:report['parts'][name]=fn()
            except Exception:report['errors'][name]=traceback.format_exc()
            save()
        def ocr():
            from PIL import Image,ImageDraw,ImageFont
            import numpy as np
            from rapidocr.inference_engine.onnxruntime.main import OrtInferSession
            from lyricsync.vision import RapidOCRProvider
            original_options=OrtInferSession._init_sess_opts
            def profiling(cfg):
                options=original_options(cfg)
                options.enable_profiling=True
                profiling.counter+=1
                options.profile_file_prefix=str(out/f'ocr-profile-{profiling.counter}')
                return options
            profiling.counter=0
            OrtInferSession._init_sess_opts=staticmethod(profiling)
            device={'cpu':'cpu','nvidia':'cuda','directml':'directml'}[args[1]]
            provider=RapidOCRProvider(language='eng',device=device,preprocess='raw')
            image=Image.new('RGB',(720,120),'white')
            ImageDraw.Draw(image).text((15,20),'LYRIA AUDIO TEST',font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',58),fill='black')
            recognized=[x.text for x in provider.recognize(np.asarray(image))]
            assert any('LYRIA' in x.upper().replace(' ','') for x in recognized),recognized
            nodes={}
            for label,engine in [('det',provider.engines[0].text_det),('rec',provider.engines[0].text_rec)]:
                profile=Path(engine.session.session.end_profiling())
                counts=Counter(event['args']['provider'] for event in json.loads(profile.read_bytes()) if event.get('cat')=='Node' and event.get('args',{}).get('provider'))
                actual={'cpu':'CPUExecutionProvider','cuda':'CUDAExecutionProvider','directml':'DmlExecutionProvider'}[provider.signature['device']]
                assert counts[actual]>0,(actual,counts)
                nodes[label]=dict(counts)
            return dict(text=recognized,nodes=nodes,signature=provider.signature)
        def separate():
            from lyricsync.local_separation import prepare_vocals
            device={'cpu':'cpu','nvidia':'cuda:0','directml':'directml'}[args[1]]
            result=prepare_vocals(dict(audio_path=str(lab/'fixtures/alignment.wav'),offset_seconds=0.,duration_seconds=4.,
                runtime_python='',device=device,cache_root=str(lab/'models')))
            assert result['samples']==176400 and result['vocal_peak']>0 and Path(result['path']).is_file()
            return {k:result[k] for k in ('runtime','path','sha256','vocal_peak','samples','model_id','model_sha256')}
        def align():
            from lyricsync.local_models import NEXTFIRE
            from lyricsync import local_alignment as alignment
            from lyricsync.model import Project,Lyric
            from lyricsync.project_document import ProjectDocument
            rows=json.loads((lab/'fixtures/alignment-fixture.json').read_text('utf8'))['clips'][0]['rows']
            doc=ProjectDocument(Project(lyrics=[Lyric(r['id'],r['text'],None,None,reading=r['reading']) for r in rows]))
            before=doc.snapshot()
            calls=[]
            raw=alignment.run_worker
            def receipt(*a,**kw):
                response=raw(*a,**kw)
                calls.append({k:response.get(k) for k in ('model_id','revision','device','runtime','timings_seconds','resource_usage')})
                return response
            alignment.run_worker=receipt
            device='cuda:0' if args[1]=='nvidia' else 'cpu'
            result=alignment.generate_vocal_candidate(doc,options=dict(audio_path=str(lab/'fixtures/alignment.wav'),runtime_python='',cache_root=str(lab/'models'),model_id=NEXTFIRE.id,language='Japanese',device=device,offset_seconds=0.,duration_seconds=18.,text_source='reference',use_separation=False))
            assert doc.snapshot()==before and [r['text'] for r in rows]==[r['text'] for r in result['rows']]
            times=[[r['start'],r['end']] for r in result['rows']]
            assert all(isinstance(a,(float,int)) and b>a for a,b in times)
            return dict(worker_results=calls,times=times,source_rows=len(rows),unchanged=True)
        phases=args[2].split(',')
        for name,fn in [('ocr',ocr),('separate',separate),('align',align)]:
            if name in phases:check(name,fn)
        report['ok']=not report['errors'] and set(report['parts'])==set(phases)
        save()
        return 0 if report['ok'] else 1
