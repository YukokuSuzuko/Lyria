"""Render plain-text ASS escapes through native FFmpeg/libass, never a mock.

Not VSFilter, Windows/font portability, or real-MV acceptance. Reference glyph
counts and visible rows catch silent hard breaks, missing characters, and doubled
slashes. Plain unknown escapes also compare byte-for-byte against independently
written ASS with one unescaped slash, which libass displays literally.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lyricsync.export import render_ass
from lyricsync.subtitles import parse_ass


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ffmpeg',default=shutil.which('ffmpeg'))
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if not args.ffmpeg:parser.error('A native FFmpeg with libass is required')
    if sys.platform!='win32' and Path(args.ffmpeg).suffix.lower()=='.exe':
        parser.error('Do not execute bundled Windows binaries on this host')
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    env=dict(os.environ,XDG_CACHE_HOME=str(out/'cache'))
    cases={'literal_N':r'A\NB','literal_n':r'A\nB','literal_h':r'A\hB',
           'literal_T':r'A\TB','repeated_slashes':'A'+'\\'*3+'B',
           'real_newline':'A\nB','braces':'A{TEXT}B',
           'override_looking':r'A{\alpha&HFF&}B', 'original_joiner':'A\\\u2060NB'}
    rows=[]
    def render(name,body):
        ass=out/(name+'.ass');ass.write_text(body,encoding='utf8')
        proc=subprocess.run([args.ffmpeg,'-nostdin','-hide_banner','-loglevel','error',
            '-filter_threads','1','-f','lavfi','-i','color=c=black:s=640x360:r=1',
            '-vf','ass='+ass.name,'-frames:v','1','-threads','1',
            '-pix_fmt','gray','-f','rawvideo','pipe:1'],cwd=out,env=env,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,timeout=30)
        a=np.frombuffer(proc.stdout,np.uint8).reshape(360,640)
        cv2.imwrite(str(out/(name+'.png')),a)
        return a
    for name,text in cases.items():
        body=render_ass([(0,2,text)],(640,360),font='DejaVu Sans',font_size=40)
        a=render(name,body);mask=(a>80).astype('uint8')
        count,labels,stats,centers=cv2.connectedComponentsWithStats(mask)
        components=[s for s in stats[1:] if s[cv2.CC_STAT_AREA]>=8]
        yy,xx=np.where(mask)
        bands=sum(bool(v) and (i==0 or not np.any(mask[i-1])) for i,v in enumerate(np.any(mask,axis=1)))
        row={'case':name,'round_trip_exact':parse_ass(out/(name+'.ass')).events[0].text==text,
             'glyph_components':len(components),'visible_row_bands':bands,
             'bbox':[int(xx.min()),int(yy.min()),int(xx.max()),int(yy.max())]}
        if name.startswith('literal_') or name=='original_joiner':
            row['expected_components']=4;row['passed']=len(components)==4 and bands==1
        elif name=='repeated_slashes':row['passed']=len(components)==5 and bands==1
        elif name=='real_newline':row['passed']=len(components)==2 and bands==2
        elif name=='braces':row['passed']=len(components)==8 and bands==1
        else:
            # All literal tag characters must remain painted, not hide the B.
            row['passed']=len(components)>=13 and bands==1
        if name in ('literal_T','repeated_slashes'):
            prefix,_,_=body.rpartition('}')
            reference=prefix+'}'+text+'\n'
            expected=render(name+'-independent-reference',reference)
            row['identical_to_raw_literal_reference']=bool(np.array_equal(a,expected))
            row['passed'] &= row['identical_to_raw_literal_reference']
        row['passed'] &= row['round_trip_exact'];rows.append(row)
    report={'scope':'Native Linux FFmpeg/libass synthetic literal-text render; not VSFilter or Windows acceptance',
            'font':'DejaVu Sans','ffmpeg':subprocess.check_output([args.ffmpeg,'-version'],text=True).splitlines()[0],
            'cases':rows,'passed':all(row['passed'] for row in rows),
            'sources':['https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/libavcodec/ass.c',
                       "https://github.com/libass/libass/wiki/Libass'-ASS-Extensions"]}
    (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
