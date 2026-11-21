"""Generate original test lyrics + reproducible PTS-labelled synthetic MV."""
from pathlib import Path
import json
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lyricsync.subtitles import ass_time


def generate(folder):
    import cv2
    import numpy as np
    from PIL import Image,ImageDraw,ImageFont
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    english=ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf",34)
    japanese=ImageFont.truetype(r"C:\Windows\Fonts\msgothic.ttc",36)
    cues=[{"text":"HELLO WORLD","zh":"你好，世界","start":.8,"end":4.2,"y":305,"font":english},
          {"text":"GOOD NIGHT","zh":"晚安","start":2.4,"end":6,"y":365,"font":english,"fade":True},
          {"text":"STAY WITH ME","zh":"请留在我身边","start":3,"end":5,"y":425,"font":english},
          {"text":"夜の空を見上げる","zh":"抬头望向夜空","start":7,"end":9.8,"y":365,"font":japanese},
          {"text":"またね SEE YOU","zh":"下次再见","start":8.4,"end":11.4,"y":425,"font":japanese},
          {"text":"HELLO WORLD","zh":"你好，世界","start":12.4,"end":14.8,"y":365,"font":english}]
    writer=cv2.VideoWriter(str(folder/"synthetic_mv.mp4"),cv2.VideoWriter_fourcc(*"mp4v"),20,(960,540))
    if not writer.isOpened():raise RuntimeError("Cannot create synthetic video")
    try:
        for frame in range(310):
            stamp=frame/20
            image=Image.new("RGB",(960,540),(13,20,30))
            draw=ImageDraw.Draw(image)
            draw.ellipse((int(stamp*30)%900,60,int(stamp*30)%900+40,100),fill=(35,50,70))
            for cue in cues:
                if cue["start"]<=stamp<cue["end"]:
                    alpha=min(1,(stamp-cue["start"])/.5,(cue["end"]-stamp)/.5) if cue.get("fade") else 1
                    layer=Image.new("RGB",image.size,(13,20,30))
                    layer_draw=ImageDraw.Draw(layer)
                    fill=(255,245,80) if stamp>2.5 and cue["text"]=="HELLO WORLD" else (245,245,245)
                    layer_draw.text((480,cue["y"]),cue["text"],font=cue["font"],anchor="mt",fill=fill,stroke_width=1,stroke_fill=(60,60,60))
                    # Blend only the text band, preserving independently displayed lines.
                    band=(0,cue["y"]-5,960,cue["y"]+53)
                    image.paste(Image.blend(image.crop(band),layer.crop(band),max(0,alpha)),band)
            writer.write(cv2.cvtColor(np.asarray(image),cv2.COLOR_RGB2BGR))
    finally:writer.release()
    header="[Script Info]\nScriptType: v4.00+\nPlayResX: 960\nPlayResY: 540\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    rows=[]
    for i,cue in enumerate(cues):
        body="".join("{\\kf12}"+char for char in cue["text"])
        a,b=cue["start"]+.55,cue["end"]+.8
        rows.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Default,,0,0,0,,{body}")
        if i==0:rows.append(f"Dialogue: 1,{ass_time(a)},{ass_time(b)},Glow,,0,0,0,,{{\\bord5}}{body}")
    (folder/"synthetic_ktv.ass").write_text(header+"\n".join(rows)+"\n",encoding="utf-8-sig")
    (folder/"synthetic_zh.txt").write_text("\n".join(c["zh"] for c in cues)+"\n",encoding="utf-8")
    (folder/"synthetic_bilingual.txt").write_text("\n".join(c["text"]+"\t"+c["zh"] for c in cues)+"\n",encoding="utf-8")
    truth=[{k:v for k,v in c.items() if k!="font"} for c in cues]
    (folder/"truth.json").write_text(json.dumps({"fps":20,"cues":truth,"timing_rule":"hard visible interval; fade support interval separately marked"},ensure_ascii=False,indent=2),encoding="utf-8")
    return truth


if __name__=="__main__":generate(Path(__file__).resolve().parents[1]/"examples")
