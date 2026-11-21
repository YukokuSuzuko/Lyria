"""GUI entry point plus deterministic offline diagnostic commands."""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys


def main():
    parser=argparse.ArgumentParser(description="Lyria")
    parser.add_argument("--self-check",metavar="JSON_PATH")
    parser.add_argument("--process",metavar="PROJECT_JSON")
    parser.add_argument("--convert",nargs=2,metavar=("KTV_ASS","OUTPUT_ASS"))
    parser.add_argument("--export",nargs=2,metavar=("PROJECT_JSON","OUTPUT_ASS"))
    parser.add_argument("--workflow-check",nargs=2,metavar=("PROJECT_JSON","REPORT_FOLDER"))
    args=parser.parse_args()
    if args.workflow_check:
        from lyricsync.workflow_check import check
        try:
            check(*args.workflow_check)
        except Exception as exc:
            # Diagnostic mode has a machine-readable failure channel. Avoid an
            # unhandled-exception GUI that blocks unattended packaged checks.
            import traceback
            from lyricsync.storage import atomic_json
            atomic_json(Path(args.workflow_check[1])/'workflow_check.json',
                        {'ok':False,'error':str(exc) or type(exc).__name__,'traceback':traceback.format_exc()})
            raise SystemExit(1)
    elif args.self_check:
        from lyricsync.vision import make_ocr,Video,resources
        from lyricsync.model import normalized
        import av,cv2,numpy,tkinter
        import io,os
        from PIL import Image,ImageDraw,ImageFont
        from lyricsync.storage import atomic_json
        provider=make_ocr({"engine":"rapidocr","language":"jpn+eng"},["HELLO WORLD","夜の空"])
        result=provider.recognize(numpy.zeros((160,640,3),dtype=numpy.uint8))
        test_image=Image.new("RGB",(700,220),(0,0,0))
        font=ImageFont.truetype(str(Path(os.environ.get("WINDIR",r"C:\Windows"))/"Fonts"/"msyh.ttc"),40)
        draw=ImageDraw.Draw(test_image)
        draw.text((30,20),"HELLO WORLD",font=font,fill="white")
        draw.text((30,110),"夜の空",font=font,fill="white")
        pixels=cv2.cvtColor(numpy.asarray(test_image),cv2.COLOR_RGB2BGR)
        recognized=provider.recognize(pixels)
        actual="".join(normalized(d.text) for d in recognized)
        if "helloworld" not in actual or "夜の空" not in actual:
            raise RuntimeError("便携 OCR 日英识别自检失败："+actual)
        root=tkinter.Tk();root.withdraw();root.update();root.destroy()
        tess=make_ocr({"engine":"tesseract","language":"jpn+eng"})
        tess_text=" ".join(d.text for d in tess.recognize(pixels))
        if "helloworld" not in normalized(tess_text):raise RuntimeError("便携 Tesseract 文字识别失败")
        buffer=io.BytesIO()
        with av.open(buffer,mode="w",format="mp4") as container:
            stream=container.add_stream("mpeg4",rate=10);stream.width=700;stream.height=220;stream.pix_fmt="yuv420p"
            for index in range(2):
                frame=av.VideoFrame.from_ndarray(pixels,format="bgr24")
                for packet in stream.encode(frame):container.mux(packet)
            for packet in stream.encode():container.mux(packet)
        buffer.seek(0)
        with av.open(buffer) as container:decoded=list(container.decode(video=0))
        if len(decoded)!=2 or decoded[0].pts is None:raise RuntimeError("便携 PTS 视频解码自检失败")
        from lyricsync import __version__
        atomic_json(args.self_check,{"ok":True,"app_version":__version__,"python":sys.version,"av":av.__version__,"cv2":cv2.__version__,
            "ocr":provider.signature,"tesseract":tess.signature,"blank_detections":len(result),
            "recognized":[d.text for d in recognized],"tesseract_text":tess_text,"decoded_frames":len(decoded),
            "tesseract_executable":tess.executable,"resources":str(resources())})
    elif args.process:
        from lyricsync.storage import load_project
        from lyricsync.pipeline import run
        run(load_project(args.process),args.process,progress=lambda p,m:print(f"{p:.0%} {m}",flush=True))
    elif args.convert:
        from lyricsync.export import convert_ktv
        convert_ktv(*args.convert)
    elif args.export:
        from lyricsync.storage import load_project
        from lyricsync.export import export_project
        export_project(load_project(args.export[0]),args.export[1])
    else:
        from lyricsync.gui import main as gui
        gui()


def run_main():
    try:
        main()
    except Exception as error:
        if '--self-check' not in sys.argv:
            raise
        # Unattended diagnostics must fail with a report rather than wait for a
        # PyInstaller exception dialog. GUI errors retain their existing path.
        import traceback
        from lyricsync.storage import atomic_json
        try:
            target=sys.argv[sys.argv.index('--self-check')+1]
            atomic_json(target,{'ok':False,'error':str(error),'traceback':traceback.format_exc()})
        except (OSError,IndexError):
            pass
        raise SystemExit(1)


if __name__=="__main__":
    run_main()
