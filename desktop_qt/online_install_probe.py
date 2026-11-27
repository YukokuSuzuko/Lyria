"""Packaged settings-button acceptance on an isolated test desktop. Faults only fail closed."""
import json
from pathlib import Path
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication,QPushButton
from .local_model_tasks import model_tasks
from lyricsync import online_nvidia as installer
from lyricsync import shared_runtime

def exercise(app,bridge,window,case):
    out=bridge.storage_dir.parent/(case+'.json')
    out.parent.mkdir(parents=True,exist_ok=True)
    package=shared_runtime.application_dir().parent
    endpoint=shared_runtime.extension_root(shared_runtime.application_dir(),'nvidia')
    pref=package/'profiles/acceleration-choice.json'
    def snapshot():
        cache=endpoint.parent/'.lyria-downloads'
        manifest=endpoint/'shared-runtime'/shared_runtime.MANIFEST
        return dict(target=endpoint.exists(),manifest=shared_runtime._hash(manifest) if manifest.is_file() else None,
                    preference=pref.read_text('utf8') if pref.exists() else None,
                    wheel_cache=sorted(x.name for x in cache.glob('*.whl')))
    started=time.monotonic()
    log=dict(case=case,exe=str(shared_runtime.application_dir()/'Lyria.exe'),before=snapshot(),requests=[],events=[])
    real_fetch=installer.fetch
    def fail_traffic(url):
        host=urlsplit(url).hostname
        log['requests'].append(host)
        if case=='online-fallback' and host=='mirror.sjtu.edu.cn':
            raise URLError('QA controlled mirror outage')
        if case in ('online-offline','online-cancel'):
            raise URLError('QA controlled offline')
        if case=='online-hash':
            if host!='mirror.sjtu.edu.cn':raise URLError('QA controlled alternate unavailable')
            class Corrupt:
                status=200
                headers={'Content-Length':str(installer.WHEELS['torch'][1])}
                left=installer.WHEELS['torch'][1]
                def geturl(self):return url
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def read(self,n):
                    n=min(n,self.left);self.left-=n;return bytes(n)
            return Corrupt()
        return real_fetch(url)
    installer.fetch=fail_traffic
    if case=='online-incompatible':
        def unavailable():raise ValueError('QA controlled incompatible driver')
        installer.driver=unavailable
    if case=='online-trial-fail':
        def fail_trial(app,rt):raise ValueError('QA controlled device trial failure')
        installer.trial=fail_trial
    timer=QTimer(app);timer.setInterval(100)
    work=model_tasks()
    def save():
        out.write_text(json.dumps(log,ensure_ascii=False,indent=2,default=str),'utf8')
    def changed(percent,msg):
        log['last_progress']=msg
        if '未通过' in msg:log['events'].append(msg)
        if int(time.monotonic()-started)%5==0:save()
    work.progress.connect(changed)
    def done():
        if work.action!='nvidia_online':return
        try:
            log.update(result=work.value,error=work.error,after=snapshot(),seconds=round(time.monotonic()-started,2))
            fail=case in ('online-offline','online-hash','online-cancel','online-incompatible','online-trial-fail')
            if fail:
                assert work.error and log['after']['target']==log['before']['target'] and log['before']['manifest']==log['after']['manifest'] and log['before']['preference']==log['after']['preference']
                if case=='online-hash':assert any('SHA-256' in s for s in log['events'])
                if case=='online-cancel':assert work.stop_reason=='cancel'
            else:
                assert not work.error and work.value['device']['actual_matrix_provider']=='CUDAExecutionProvider'
                assert json.loads(log['after']['preference'])['profile']=='nvidia'
            log['ok']=True
        except Exception as exc:
            log.update(ok=False,assertion=repr(exc))
        save()
        dlg=QApplication.activeModalWidget()
        if dlg:dlg.accept()
        QTimer.singleShot(100,app.quit)
    work.finished.connect(done)
    def tick():
        dlg=QApplication.activeModalWidget()
        if dlg and dlg.objectName()=='splitDependencies':
            if 'opened-settings' not in log['events']:
                dlg.findChild(QPushButton,'splitLater').click()
                return
            b=dlg.findChild(QPushButton,'installNvidiaOnline')
            dlg.grab().save(str(out.with_suffix('.png')))
            log['events'].append('clicked '+b.text());save()
            timer.stop()
            b.click()
            if case=='online-cancel':dlg.findChild(QPushButton,'cancelNvidiaOnline').click()
        elif 'opened-settings' not in log['events'] and time.monotonic()-started>1:
            log['events'].append('opened-settings')
            QTimer.singleShot(0,lambda:bridge.command('settings.acceleration',{}))
    timer.timeout.connect(tick);timer.start()
