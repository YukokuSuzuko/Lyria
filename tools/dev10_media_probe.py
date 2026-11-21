"""Offscreen real-decoder replacement probe on generated example media."""
import os,sys,time,json
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from desktop_qt.controller import Bridge
from lyricsync.model import Mapping, Translation
app=QApplication.instance() or QApplication([])
root=Path(__file__).resolve().parents[1]
out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=False)
b=Bridge(storage_dir=out/'storage')
checks=[]
def wait_ready(path):
    until=time.monotonic()+30
    while time.monotonic()<until:
        app.processEvents();time.sleep(.02)
        if b.media_controller.snapshot()['ready'] and b.document.project.video.get('content_hash') and Path(b.media_controller.path).resolve()==path.resolve():return
    raise AssertionError('decoder did not become ready')
try:
    first=root/'examples/synthetic_mv.mp4';second=root/'examples/synthetic_burned_zh.mp4'
    b._open_video({'path':str(first)});wait_ready(first)
    p=b.document.project;old_hash=p.video['content_hash']
    p.translations=[Translation('t','preserved text')];p.mappings=[Mapping('m',['t'],start=1.,end=2.,human_edited=True)]
    b._changed()
    b._confirm_media_timing_reuse=lambda:'keep'
    b._open_video({'path':str(second)});wait_ready(second)
    assert p.video['content_hash']!=old_hash and p.mappings[0].start==1.
    checks.append('direct keep: new decoder/identity ready, old timing retained')
    b._confirm_media_timing_reuse=lambda:'discard'
    b._open_video({'path':str(first)});wait_ready(first)
    assert p.video['content_hash']==old_hash and p.mappings[0].start is None
    assert p.translations[0].text=='preserved text'
    checks.append('direct discard: old file reloaded without restart, lyric retained and time cleared')
    (out/'report.json').write_text(json.dumps({'ok':True,'checks':checks,'foreground_acceptance':False},indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'checks':checks}))
finally:
    b.shutdown();app.processEvents()
