"""Factorial diagnostic: Application/no Video versus Video/no Application."""
import json,os,sys,tempfile,threading,traceback
from pathlib import Path
import tkinter as tk
from tools.private_desktop import desktop_name
from lyricsync.tk_lifecycle import destroy_root
assert desktop_name()==os.environ['LYRIA_EXPECTED_TEST_DESKTOP']
mode=sys.argv[1];failures=[];retained=[]
for i in range(30):
    root=None;app=None;folder=tempfile.TemporaryDirectory()
    try:
        print(json.dumps(dict(event='before',iteration=i,mode=mode,threads=[t.name for t in threading.enumerate()])),flush=True)
        root=tk.Tk();root.withdraw()
        if mode in ('application','combined'):
            from lyricsync.gui import Application
            app=Application(root,folder.name)
            if mode=='combined':
                from lyricsync.vision import Video
                app.preview_video=Video(Path(__file__).resolve().parents[1]/'examples/synthetic_mv.mp4');app.timeline.configure(to=app.preview_video.info['duration']);app.seek(1)
            root.update_idletasks()
            app.project_saver.close();app.pause_playback()
        elif mode=='video':
            from lyricsync.vision import Video
            video=Video(Path(__file__).resolve().parents[1]/'examples/synthetic_mv.mp4');video.at(1)
        destroy_root(root)
        # Match test objects retained until ordinary GC; no special teardown intervention.
        retained.append((root,app))
    except Exception:
        failures.append(i);traceback.print_exc()
        if root is not None:
            try:destroy_root(root)
            except Exception:traceback.print_exc()
    finally:folder.cleanup()
print(json.dumps(dict(total=30,mode=mode,failures=failures,threads=[t.name for t in threading.enumerate()])),flush=True)
