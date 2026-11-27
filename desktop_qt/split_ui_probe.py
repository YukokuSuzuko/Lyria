"""Exercise the first-open and Settings buttons on a private packaged desktop."""
from pathlib import Path
import json,time
from PySide6.QtCore import QTimer,QObject
from PySide6.QtWidgets import QApplication,QPushButton
from .reading_dialog_probe import validate_private_packaged_environment
from .split_dialog import preferences

def verify(app,bridge,window,case):
    validate_private_packaged_environment()
    if case.startswith('online-'):
        from .online_install_probe import exercise
        return exercise(app,bridge,window,case)
    out=bridge.storage_dir.parent/(case+'.json')
    start=time.monotonic();events=[];timer=QTimer(app);timer.setInterval(80)
    def tick():
        try:
            if time.monotonic()-start>30:raise RuntimeError('guide UI timeout')
            dialog=QApplication.activeModalWidget()
            if case=='first' and dialog and dialog.objectName()=='splitDependencies':
                dialog.grab().save(str(out.with_suffix('.png')))
                dialog.findChild(QPushButton,'splitNever').click()
                events.append('first-run-never')
            elif case=='reopen' and not events and time.monotonic()-start>1:
                assert dialog is None and window.findChild(QObject,'settingsAcceleration')
                events.append('no-startup-dialog')
                QTimer.singleShot(0,lambda:bridge.command('settings.acceleration',{}))
                return
            elif case=='reopen' and dialog and dialog.objectName()=='splitDependencies':
                dialog.findChild(QPushButton,'splitLater').click();events.append('settings-reopened')
            else:return
            p,state=preferences(bridge)
            assert state.get('seen') and state.get('never')
            out.write_text(json.dumps(dict(ok=True,events=events,profile=str(p))),'utf8')
            timer.stop();app.quit()
        except Exception as exc:
            out.write_text(json.dumps(dict(ok=False,events=events,error=str(exc))),'utf8')
            timer.stop();app.quit()
    timer.timeout.connect(tick);timer.start()
