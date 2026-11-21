"""Run a native-fatal regression in a subprocess so failure cannot kill pytest."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_import_button_survives_state_updates_inside_file_dialog(tmp_path):
    script = r'''
from pathlib import Path
import os, sys
from PySide6.QtCore import QEventLoop, QMetaObject, QTimer, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication, QFileDialog
from desktop_qt.controller import Bridge
QQuickStyle.setStyle('Basic')
app=QApplication([])
base=Path(sys.argv[1]); fixture=base/'translation.txt'
fixture.write_text('translation one\ntranslation two\n',encoding='utf-8')
bridge=Bridge(storage_dir=base/'recovery')
engine=QQmlApplicationEngine()
engine.rootContext().setContextProperty('bridge',bridge)
engine.load(QUrl.fromLocalFile(str(Path(os.environ.get('LYRIA_IMPORT_QML_ROOT','desktop_qt/qml'))/'Main.qml')))
window=engine.rootObjects()[0]
def picker(*args,**kwargs):
    loop=QEventLoop()
    def progress():
        bridge.document.project.inputs['video']='background-video-ready.mp4'
        bridge.media_controller._state['ready']=True
        bridge.mediaChanged.emit();bridge._refresh(False)
    QTimer.singleShot(20,progress)
    QTimer.singleShot(50,lambda:bridge._refresh(False))
    QTimer.singleShot(100,loop.quit)
    loop.exec()
    return str(fixture),''
QFileDialog.getOpenFileName=picker
def walk(item):
    yield item
    for child in item.childItems():yield from walk(child)
def run():
    button=next(i for i in walk(window.contentItem()) if i.property('text')=='导入字幕 / 译文')
    QMetaObject.invokeMethod(button,'clicked')
QTimer.singleShot(100,run)
QTimer.singleShot(1000,app.quit)
app.exec()
assert len(bridge.document.project.translations)==2
window.setProperty('allowClose',True);window.close();bridge.shutdown()
print('import completed after nested dialog updates')
'''
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software')
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)], cwd=ROOT,
                            env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'import completed' in result.stdout
