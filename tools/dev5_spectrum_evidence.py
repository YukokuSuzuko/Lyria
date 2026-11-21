"""Read-only remeasurement of explicitly selected media from a baseline manifest.

Usage: python -B tools/dev5_spectrum_evidence.py --media-root DIR
       --baseline-statistics JSON --output DIR
No playback, separation, API calls or project writes. Output is offline evidence,
not a GUI screenshot, human annotation or Aegisub-equivalence claim.
"""
from pathlib import Path
import argparse
import hashlib
import json
import sys
import time
from dataclasses import asdict

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lyricsync.spectrogram import (AudioSource, RATE, DISPLAY_PRESETS,
                                  colourize, global_calibration, stft_columns)


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1048576),b''): h.update(block)
    return h.hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--media-root',type=Path,required=True)
    parser.add_argument('--baseline-statistics',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    manifest=json.loads(args.baseline_statistics.read_text(encoding='utf-8'))
    report={'scope':'offline source display evidence; not GUI, voice identification, onset truth or Aegisub comparison',
            'presets':DISPLAY_PRESETS,'samples':[]}
    font=ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf',16)
    freq=np.fft.rfftfreq(2048,1/RATE);band=(freq>=200)&(freq<4000)
    for item in manifest['samples']:
        path=(args.media_root/item['relative_path']).resolve()
        assert path.is_relative_to(args.media_root.resolve())
        original=path.stat();before=sha(path)
        assert before==item['sha256_before'], 'Input differs from baseline; stop rather than compare unrelated bytes'
        start=time.perf_counter()
        source=AudioSource.decode(path,item['duration_seconds'])
        sample={'id':item['id'],'sha256_before':before,'duration':source.duration,'windows':{}}
        renders={}
        try:
            times=np.arange(.005,source.duration,.01)
            excerpt=source.duration*.35+(np.arange(1000)+.5)*.004
            for window in (512,1024):
                t=time.perf_counter();c=global_calibration(source,window)
                elapsed=time.perf_counter()-t
                dense=stft_columns(source,times,window=window)[:,band]
                exact=float(np.percentile(dense,99.5))
                stats={'calibration':asdict(c),'calibrationSeconds':elapsed,
                       'q995_10ms':exact,'samplingErrorDb':float(20*np.log10(c.sampled_quantile/exact)),
                       'presets':{}}
                display_values=stft_columns(source,excerpt,window=window)
                for name,preset in [('dev4',dict(quantile=False,floor_db=-65,ceiling_db=-5,gamma=1.32)),*DISPLAY_PRESETS.items()]:
                    reference=c.reference if preset['quantile'] else source.reference
                    db=20*np.log10(np.maximum(dense/reference,1e-12))
                    intensity=np.clip((db-preset['floor_db'])/(preset['ceiling_db']-preset['floor_db']),0,1)**preset['gamma']
                    stats['presets'][name]={'blackPct':float(np.mean(intensity<=0)*100),
                        'brightPct':float(np.mean(intensity>=.78)*100),'saturatedPct':float(np.mean(intensity>=1)*100)}
                    renders[(window,name)]=colourize(display_values,reference,170,purple=True,
                        gamma=preset['gamma'],floor_db=preset['floor_db'],ceiling_db=preset['ceiling_db'])
                sample['windows'][str(window)]=stats
            labels=[(512,'dev4','dev4 reference, purple only: peak / 60 dB / gamma 1.32'),
                    (512,'detail','dev5 default detail: same peak / same 60 dB / gamma 1.15'),
                    (512,'balanced','optional balanced: own global sampled q99.5 / 50 dB / gamma 1.25'),
                    (512,'contrast','optional strong: own global sampled q99.5 / 35 dB / gamma 1.60'),
                    (1024,'detail','long-window detail: same peak scale, more temporal smear'),
                    (1024,'balanced','long-window balanced: own-window calibration, not absolute loudness')]
            canvas=Image.new('RGB',(1120,1310),(18,17,24));draw=ImageDraw.Draw(canvas)
            draw.text((12,8),f"{item['id']} | same 4 seconds at 35% | 0..10 kHz | offline, not voice labels",fill='white',font=font)
            for index,(window,name,label) in enumerate(labels):
                y=38+index*210
                draw.text((70,y),f'{window} Hann | {label}',fill=(225,220,240),font=font)
                canvas.paste(Image.fromarray(renders[(window,name)]),(70,y+24))
                for text,fraction in [('10000',0),('4000',.32),('2000',.66),('0',1)]:
                    draw.text((5,y+20+fraction*166),text,fill=(180,178,188),font=font)
            canvas.save(args.output/f"{item['id']}-dev5-presets.png")
        finally: source.close()
        sample.update(elapsedSeconds=time.perf_counter()-start,sha256_after=sha(path),mtimeUnchanged=path.stat().st_mtime_ns==original.st_mtime_ns)
        assert sample['sha256_after']==before and sample['mtimeUnchanged']
        report['samples'].append(sample)
        print(sample['id'],json.dumps(sample['windows']),flush=True)
    (args.output/'dev5-real-sample-statistics.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__': main()
