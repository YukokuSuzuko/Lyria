"""Application-owned local management tasks, independent of dialogs/projects."""
from __future__ import annotations
import threading
import os
from pathlib import Path
from copy import deepcopy
from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QApplication


class ManagerTask(QThread):
    progress = Signal(int, str)
    outcome = Signal(object, str)
    def __init__(self, work, parent):
        super().__init__(parent)
        self.work = work
        self.cancelled = threading.Event()
    def run(self):
        try:
            self.outcome.emit(self.work(self.cancelled.is_set, self.progress.emit), '')
        except Exception as exc:
            self.outcome.emit(getattr(exc, 'partial_result', None), str(exc))


class ModelTasks(QObject):
    progress = Signal(int, str)
    outcome = Signal(object, str)
    finished = Signal()
    def __init__(self, app):
        super().__init__(app)
        self.task = None
        self.context = {}
        self.percent, self.message = 0, '就绪'
        self.value, self.error = None, ''
        self.separation_results = []
        self.log = []
        self.action = ''
        self.stop_reason = ''
        self.reading_probe_cache = {}
        self.resume_work = None
        app.aboutToQuit.connect(self.shutdown)

    def start(self, work, context, action='manage'):
        if self.task is not None:
            return False
        self.context = {key: context[key] for key in ("runtime_python", "reading_runtime_python", "effective_reading_python", "reading_tool", "cache_root", "device", "language", "download_source") if key in context}
        self.action = action
        self.stop_reason = ''
        self.resume_work = work if action == 'download' else None
        self.value, self.error = None, ''
        self.percent, self.message = 0, '正在开始任务…'
        self.task = ManagerTask(work, self)
        self.task.progress.connect(self._progress)
        self.task.outcome.connect(self._outcome)
        self.task.finished.connect(self._finished)
        self.task.start()
        return True

    def _progress(self, percent, message):
        self.percent, self.message = percent, message
        self.log.append(message)
        del self.log[:-500]
        self.progress.emit(percent, message)

    def _outcome(self, value, error):
        self.value, self.error = value, error
        if self.stop_reason:
            detail = error
            error = ('下载已暂停，点击「继续下载」即可断点续传。' if self.stop_reason == 'pause'
                     else '任务已取消。')
            if detail:
                error += "\n" + detail
            self.error = error
        if isinstance(value, dict) and value.get('runtime_python'):
            self.context['runtime_python'] = value['runtime_python']
        if not error and isinstance(value, dict) and value.get('kind') == 'separation_preview':
            self.separation_results.append(deepcopy(value))
            # Only in-memory audition history is bounded; no cached files deleted.
            del self.separation_results[:-12]
        if self.action in ('reading_probe','reading_install','reading_create'):
            result=value.get('reading_tool',{}) if isinstance(value,dict) else {}
            python=(value.get('reading_runtime_python') if isinstance(value,dict) else '') or self.context.get('effective_reading_python') or self.context.get('reading_runtime_python') or self.context.get('runtime_python','')
            tool=result.get('tool',self.context.get('reading_tool'))
            if python and tool in ('sudachi','pykakasi'):
                key=(os.path.normcase(str(Path(python).expanduser().resolve())),tool)
                self.reading_probe_cache[key]=deepcopy(result) if not error and result.get('ready') is True else {}
        self.message = error or '操作已完成。'
        self.log.append(self.message)
        self.outcome.emit(value, error)

    def _finished(self):
        old, self.task = self.task, None
        if old:
            old.deleteLater()
        self.finished.emit()

    def stop(self, reason='cancel'):
        if reason == 'pause' and self.action != 'download':
            return False
        if self.task:
            self.stop_reason = reason
            self.task.cancelled.set()
            self._progress(self.percent, '正在暂停下载…' if reason == 'pause' else '正在取消任务…')

    def resume(self):
        if self.task is None and self.stop_reason == 'pause' and self.resume_work is not None:
            return self.start(self.resume_work, self.context, 'download')
        return False

    def shutdown(self):
        # Application exit is different from closing a dialog. Own and join the
        # thread before Qt destroys it; readers/child trees stop in the backend.
        if self.task and self.task.isRunning():
            self.task.cancelled.set()
            self.task.wait()


def model_tasks():
    app = QApplication.instance()
    if app is None:
        raise RuntimeError('请先启动主程序后再运行模型任务。')
    if not hasattr(app, '_lyria_model_tasks'):
        app._lyria_model_tasks = ModelTasks(app)
    return app._lyria_model_tasks
