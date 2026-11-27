"""Fixed visual acceptance on the private desktop. No playback or network."""
from copy import deepcopy
from pathlib import Path
import json,os,sys,traceback
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import QDialog,QLabel,QPushButton,QCheckBox,QMenu,QProgressBar
from tools.qt_event_loop_wait import delay,wait_until

def run(app,bridge,window,tag):
    from lyricsync.split_profiles import ACTIVE
    from lyricsync.local_models import ModelManager,UVR
    from .local_alignment_settings import save
    from . import split_dialog
    lab=Path(sys.executable).resolve().parents[2];out=lab/'evidence'/tag;out.mkdir()
    report={'exe':sys.executable,'backend':ACTIVE,'scale':window.devicePixelRatio(),'screens':{}}
    def snap(d,name,width,height=None):
        d.resize(width,height or d.height());app.processEvents()
        image=d.grab();assert image.save(str(out/(name+'.png')))
        report['screens'][name]={'logicalWidth':d.width(),'logicalHeight':d.height(),'dpr':d.devicePixelRatioF()}
    def modal(opening,inspect):
        errors=[]
        def visit():
            d=app.activeModalWidget()
            try:assert isinstance(d,QDialog);inspect(d)
            except BaseException:errors.append(traceback.format_exc())
            finally:
                if d and d.isVisible():d.reject()
        QTimer.singleShot(180,visit);opening()
        if errors:raise AssertionError(errors[0])
    try:
        assert ACTIVE==('nvidia' if 'gpu' in tag else 'cpu')
        media=bridge.media_controller;media.mute(True);media.player.setAudioOutput(None)
        assert media.player.audioOutput() is None and app.platformName()=='windows'
        cache=lab/'qa/pending-models'
        if not ModelManager(cache).status(UVR.id)['verification_cached']:ModelManager(cache).verify(UVR.id)
        save(bridge.storage_dir,{'cache_root':str(cache),'reading_tool':'manual'})
        def inspect(d):
            snap(d,'first-launch',500 if 'narrow' in tag else 616,550 if 'narrow' in tag else 581)
            labels=' '.join(w.text() for w in d.findChildren(QLabel) if w.isVisible())
            ready=ACTIVE=='nvidia'
            if ready:
                name=d.findChild(QLabel,'guideDeviceName')
                report['adapter']={'full':name.full,'visible':name.text(),'width':name.width(),'hint':name.sizeHint().width()}
                if 'wide' in tag:assert name.text()==name.full,report['adapter']
                assert 'CUDA 已启用' in labels and 'NVIDIA' in labels and 'AMD' not in labels,labels
                assert not d.findChild(QPushButton,'installNvidiaOnline').isVisible() and not d.findChild(QPushButton,'splitCPU').isVisible()
            manage=d.findChild(QPushButton,'guideGpuManage')
            assert manage.menu() and any('离线' in a.text() for a in manage.menu().actions())
            report['guide']={'visible':labels,'rows':len(d.findChildren(QPushButton)), 'menu':[a.text() for a in manage.menu().actions()]}
            d.findChild(QCheckBox,'splitNever').setChecked(True)
            d.findChild(QPushButton,'splitBegin').click()
        modal(lambda:split_dialog.guide(bridge,'startup'),inspect)
        assert split_dialog.preferences(bridge)[1]['never']
        if tag.endswith('-101'):report['ok']=True;return
        from .carbon_workflow_probe import remaining
        remaining(app,bridge,window,tag,out,snap,modal,report)
        report['ok']=True
    except BaseException:report['error']=traceback.format_exc()
    finally:
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf8')
        app.exit(0 if report.get('ok') else 3)

