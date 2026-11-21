"""Local, bounded diagnostics with an allowlist rather than best-effort redaction.

Never pass exception messages, Qt messages, locals, command payloads, project
state, filenames, URLs, environment variables or screenshots to the log. A
report is created only by an explicit user export; this module has no network.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import sys
import tempfile
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
EVENTS = frozenset({'session-start', 'session-end', 'startup-failed', 'qml-load-failed',
    'ui-error', 'command-failed', 'worker-failed', 'callback-failed', 'python-unhandled',
    'thread-unhandled', 'qt-warning', 'qt-critical', 'qt-fatal', 'qml-warning',
    'export-complete', 'export-failed', 'alignment-result', 'ocr-result'})
ERROR_TYPES = frozenset({'Exception', 'RuntimeError', 'ValueError', 'TypeError',
    'KeyError', 'IndexError', 'AttributeError', 'ImportError', 'ModuleNotFoundError',
    'OSError', 'FileNotFoundError', 'PermissionError', 'TimeoutError',
    'ConnectionError', 'UnicodeError', 'UnicodeDecodeError', 'JSONDecodeError',
    'MemoryError', 'InterruptedError', 'NotImplementedError', 'StartupFailure'})
STAGES = frozenset({'command','import','media','save','export','ocr','alignment','ai','qc','layout','worker'})
CODES = frozenset({'cancelled','file_missing','permission_denied','timeout','invalid_value','internal_error'})

def diagnostic_stage(operation):
    # Only code-owned constants survive. Never persist caller command text.
    groups={'import':('input.import','input.clear','导入文本'),
        'media':('media.open','audio-analysis','frame-index-cache'),
        'save':('save','project.save','project.saveAs'),
        'export':('export.ass','diagnostics.export','导出字幕'),
        'ocr':('ocr.start','单句本地 OCR','OCR candidate','OCR rematch candidate','OCR 候选识别','自动寻找歌词区域'),
        'alignment':('vocal.start','local vocal alignment','Vocal candidate','本地人声对齐'),
        'ai':('文字跨句对齐建议','单句参考翻译','AI 核对建议'),
        'layout':('subtitle-layout',)}
    return next((stage for stage,names in groups.items() if operation in names),'command')

MAX_LOG_BYTES = 256 * 1024
MAX_LOG_FILES = 4
MAX_MEMORY_EVENTS = 200


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def default_storage_dir():
    """Resolve app data without importing Qt, so import failures can be logged."""
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'Lyria'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'Lyria'
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share') / 'Lyria'


def safe_source(filename):
    """Only repository-owned code locations; never a user's media/file path."""
    try:
        candidate = Path(filename)
        # PyInstaller code filenames may be package-relative.
        path = candidate if not candidate.is_absolute() else candidate.resolve().relative_to(ROOT)
        if path.suffix not in ('.py', '.qml'):
            return None
        if path.parts[0] not in ('desktop_qt', 'lyricsync') and str(path) != 'app_qt.py':
            return None
        # Exclude dynamically supplied paths even if someone puts them in ROOT.
        if str(path).replace('\\', '/') not in SOURCE_FILES:
            return None
        return path.as_posix()
    except (OSError, ValueError, TypeError):
        return None


# Static allowlist also works in frozen/embedded builds without loose .py files.
# Keep it limited to shipped application code, never enumerate user inputs.
SOURCE_FILES = frozenset({
    'app_qt.py',
    'desktop_qt/__init__.py',
    'desktop_qt/api_review_dialogs.py',
    'desktop_qt/controller.py',
    'desktop_qt/diagnostics.py',
    'desktop_qt/entry_initialization.py',
    'desktop_qt/candidate_workspace.py',
    'desktop_qt/audio_source_analysis.py',
    'desktop_qt/local_model_tasks.py',
    'desktop_qt/local_alignment_dialog.py',
    'lyricsync/local_alignment.py',
    'lyricsync/local_transcription.py',
    'lyricsync/local_runtime_setup.py',
    'lyricsync/local_preparation.py',
    'lyricsync/local_alignment_runtime.py',
    'lyricsync/local_separation.py',
    'lyricsync/owned_process.py',
    'desktop_qt/dialogs.py',
    'desktop_qt/media.py',
    'desktop_qt/native_theme.py',
    'desktop_qt/qml/ActionButton.qml',
    'desktop_qt/qml/Badge.qml',
    'desktop_qt/qml/Check.qml',
    'desktop_qt/qml/ChoiceBox.qml',
    'desktop_qt/qml/ExportPage.qml',
    'desktop_qt/qml/Field.qml',
    'desktop_qt/qml/FrameTimeline.qml',
    'desktop_qt/qml/ImportPanel.qml',
    'desktop_qt/qml/Main.qml',
    'desktop_qt/qml/Marker.qml',
    'desktop_qt/qml/MotionColor.qml',
    'desktop_qt/qml/MotionMenu.qml',
    'desktop_qt/qml/MotionNumber.qml',
    'desktop_qt/qml/MotionSurface.qml',
    'desktop_qt/qml/Panel.qml',
    'desktop_qt/qml/PreviewPanel.qml',
    'desktop_qt/qml/QCPage.qml',
    'desktop_qt/qml/QCResults.qml',
    'desktop_qt/qml/ReviewPanel.qml',
    'desktop_qt/qml/SettingsPage.qml',
    'desktop_qt/qml/StepButton.qml',
    'desktop_qt/qml/TextAllocationPopup.qml',
    'desktop_qt/qml/Theme.qml',
    'desktop_qt/qml/TitlebarCaption.qml',
    'desktop_qt/qml/TransportButton.qml',
    'desktop_qt/qml/WindowControls.qml',
    'desktop_qt/qml/WindowResizeFrame.qml',
    'desktop_qt/roi_dialog.py',
    'desktop_qt/surface.py',
    'desktop_qt/tool_cursor.py',
    'desktop_qt/window_chrome.py',
    'lyricsync/__init__.py',
    'lyricsync/ai_config.py',
    'lyricsync/api.py',
    'lyricsync/api_alignment.py',
    'lyricsync/api_client.py',
    'lyricsync/appearance.py',
    'lyricsync/audio.py',
    'lyricsync/audit.py',
    'lyricsync/audit_gui.py',
    'lyricsync/audit_service.py',
    'lyricsync/credentials.py',
    'lyricsync/dialogs.py',
    'lyricsync/editing.py',
    'lyricsync/export.py',
    'lyricsync/file_drop.py',
    'lyricsync/frame_timeline.py',
    'lyricsync/frames.py',
    'lyricsync/frontend.py',
    'lyricsync/graphemes.py',
    'lyricsync/gui.py',
    'lyricsync/hold.py',
    'lyricsync/ime.py',
    'lyricsync/importing.py',
    'lyricsync/key_widget.py',
    'lyricsync/live_edit.py',
    'lyricsync/marker_track.py',
    'lyricsync/matching.py',
    'lyricsync/model.py',
    'lyricsync/outline_assist.py',
    'lyricsync/pipeline.py',
    'lyricsync/playback.py',
    'lyricsync/project_document.py',
    'lyricsync/rescan.py',
    'lyricsync/review.py',
    'lyricsync/review_layout.py',
    'lyricsync/storage.py',
    'lyricsync/subtitles.py',
    'lyricsync/tesseract_process.py',
    'lyricsync/tk_lifecycle.py',
    'lyricsync/video_only.py',
    'lyricsync/vision.py',
    'lyricsync/widgets.py',
    'lyricsync/workflow_check.py',
    'lyricsync/workspace.py',
})

def clean_event(raw):
    """Reapply the allowlist when reading disk, not just when writing it."""
    if not isinstance(raw, dict) or raw.get('event') not in EVENTS:
        return None
    stamp = raw.get('time', '')
    if not isinstance(stamp, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00', stamp):
        return None
    result = {'time': stamp, 'event': raw['event']}
    if isinstance(raw.get('stage'),str) and raw['stage'] in STAGES:result['stage']=raw['stage']
    if isinstance(raw.get('code'),str) and raw['code'] in CODES:result['code']=raw['code']
    if raw.get('error_type') in ERROR_TYPES:
        result['error_type'] = raw['error_type']
    if type(raw.get('errno')) is int and 0 <= raw['errno'] <= 65535:
        result['errno'] = raw['errno']
    if raw['event'] == 'alignment-result' and isinstance(raw.get('alignment'), dict):
        item = raw['alignment']; safe = {}
        for key in ('total', 'valid', 'missing', 'applied', 'applied_timed', 'blocked'):
            if type(item.get(key)) is int and 0 <= item[key] <= 1000000:
                safe[key] = item[key]
        if item.get('status') in ('empty', 'applied', 'retained', 'failed', 'discarded'):
            safe['status'] = item['status']
        if item.get('reason') in ('empty_result', 'not_pristine', 'stale_context', 'missing_index',
                'identity_mismatch', 'verification_failed', 'cancelled', 'blocked', 'apply_failed',
                'applied', 'no_change', 'duplicate', 'install_failed', 'confirmation_required',
                'interrupted_cancelled', 'interrupted_failed', 'interrupted_discarded'):
            safe['reason'] = item['reason']
        if type(item.get('review')) is bool:
            safe['review'] = item['review']
        result['alignment'] = safe
    if raw['event'] == 'ocr-result' and isinstance(raw.get('ocr'), dict):
        item = raw['ocr']
        safe = {}
        if item.get('status') in ('empty', 'applied', 'retained', 'failed', 'cancelled'):
            safe['status'] = item['status']
        if type(item.get('review')) is bool:
            safe['review'] = item['review']
        if type(item.get('records')) is int and 0 <= item['records'] <= 12:
            safe['records'] = item['records']
        for key in ('regions','samples','boxes','tracks','eligible','fragments','short_uncertain','timed','applied','main_rows'):
            if type(item.get(key)) is int and 0 <= item[key] <= 10**9:
                safe[key] = item[key]
        result['ocr'] = safe
    frames = []
    for frame in raw.get('frames', [])[:24] if isinstance(raw.get('frames'), list) else []:
        if not isinstance(frame, dict):
            continue
        source = frame.get('source')
        if isinstance(source, str) and source in SOURCE_FILES and type(frame.get('line')) is int and 0 < frame['line'] < 1000000:
            frames.append({'source': source, 'line': frame['line']})
    if frames:
        result['frames'] = frames
    return result


class DiagnosticStore:
    """Small per-process log, rotated by size; oldest sessions pruned on start."""
    def __init__(self, storage_dir=None):
        self._lock = threading.RLock()
        self._recent = deque(maxlen=MAX_MEMORY_EVENTS)
        self.directory = None
        self.path = None
        try:
            primary = Path(storage_dir or default_storage_dir()) / 'diagnostics'
        except (OSError, ValueError, RuntimeError):
            primary = None
        self.persistent = False
        self.fallback = False
        for position in range(2):
            try:
                candidate = primary if position == 0 else Path(tempfile.gettempdir()) / ('lyria-' + str(getattr(os, 'getuid', lambda: 'user')())) / 'diagnostics'
                if candidate is None:
                    continue
                candidate.mkdir(parents=True, exist_ok=True, mode=0o700)
                self.directory = candidate
                self.path = candidate / ('diagnostics-' + uuid.uuid4().hex + '.jsonl')
                with self.path.open('x', encoding='utf-8'):
                    pass
                if os.name != 'nt':
                    self.path.chmod(0o600)
                self.persistent = True
                self.fallback = bool(position)
                self._prune()
                break
            except OSError:
                self.persistent = False
                continue
        self.record('session-start')

    def _prune(self):
        files = [p for p in self.directory.glob('diagnostics-*.jsonl') if re.fullmatch(r'diagnostics-[0-9a-f]{32}\.jsonl', p.name)]
        files = sorted(files, key=lambda p: p.stat().st_mtime)
        for path in files[:-MAX_LOG_FILES]:
            if path != self.path:
                try:
                    path.unlink()
                except OSError:
                    pass

    def record(self, event, exception=None, *, source=None, line=None, stage=None, alignment=None, ocr=None):
        raw = {'time': now(), 'event': event, 'stage':stage, 'alignment':alignment, 'ocr':ocr}
        if exception is not None:
            raw['code']=next((code for cls,code in ((InterruptedError,'cancelled'),
                (FileNotFoundError,'file_missing'),(PermissionError,'permission_denied'),
                (TimeoutError,'timeout'),(ValueError,'invalid_value')) if isinstance(exception,cls)),'internal_error')
            name = type(exception).__name__
            raw['error_type'] = name if name in ERROR_TYPES else 'Exception'
            if isinstance(exception, OSError):
                raw['errno'] = exception.errno
            frames = []
            trace = exception.__traceback__
            while trace:
                location = safe_source(trace.tb_frame.f_code.co_filename)
                if location:
                    frames.append({'source': location, 'line': trace.tb_lineno})
                trace = trace.tb_next
            raw['frames'] = frames[-24:]
        if source:
            location = safe_source(source)
            if location:
                raw.setdefault('frames', []).append({'source': location, 'line': line})
        record = clean_event(raw)
        if record is None:
            return
        with self._lock:
            self._recent.append(record)
            if not self.persistent:
                return
            try:
                encoded = json.dumps(record, ensure_ascii=True, separators=(',', ':')) + '\n'
                if self.path.stat().st_size + len(encoded.encode('utf-8')) > MAX_LOG_BYTES:
                    self.path = self.directory / ('diagnostics-' + uuid.uuid4().hex + '.jsonl')
                    with self.path.open('x', encoding='utf-8'):
                        pass
                    if os.name != 'nt':
                        self.path.chmod(0o600)
                    self._prune()
                with self.path.open('a', encoding='utf-8') as stream:
                    stream.write(encoded)
                    stream.flush()
            except OSError:
                self.persistent = False

    def snapshot(self):
        with self._lock:
            events = []
            if self.directory is None:
                return list(self._recent)
            try:
                files = sorted(self.directory.glob('diagnostics-*.jsonl'), key=lambda p: p.stat().st_mtime)[-MAX_LOG_FILES:]
                for path in files:
                    if not re.fullmatch(r'diagnostics-[0-9a-f]{32}\.jsonl', path.name):
                        continue
                    with path.open('rb') as stream:
                        content = stream.read(MAX_LOG_BYTES)
                    for line in content.splitlines():
                        try:
                            event = clean_event(json.loads(line))
                        except (ValueError, UnicodeError, TypeError):
                            continue
                        if event:
                            events.append(event)
            except OSError:
                pass
            # Do not omit the newest records when the disk is full/unwritable.
            if not self.persistent:
                events.extend(self._recent)
            return events

    def export(self, target):
        """Explicit save only, atomic replacement; no other files are bundled."""
        target = Path(target).expanduser().resolve()
        # Keep the exact name selected/confirmed by the native save dialog.
        # Adding a suffix here could silently replace a different existing file.
        if self.directory is not None and target.parent == self.directory.resolve():
            raise ValueError('请将诊断日志保存到默认日志目录以外的其他位置。')
        from lyricsync import __version__
        report = {'kind': 'lyria-local-diagnostics', 'schema': 1, 'created_utc': now(),
            'app_version': __version__, 'python': platform.python_version(),
            'os_family': platform.system(), 'architecture_bits': 64 if sys.maxsize > 2**32 else 32,
            'persistent_log_available': self.persistent, 'temporary_log_location': self.fallback,
            'privacy': {'automatic_upload': False, 'content': 'Allowlisted stages, error codes, categories and internal code locations only.',
                        'excluded': ['credentials', 'exception messages', 'URLs', 'user paths', 'project/lyric text',
                                     'media', 'screenshots', 'environment variables', 'machine identifiers']},
            'events': self.snapshot()}
        qt = sys.modules.get('PySide6.QtCore')
        if qt:
            report['qt'] = qt.qVersion()
        handle, name = tempfile.mkstemp(prefix='.lyria-diagnostics-', suffix='.tmp', dir=target.parent)
        try:
            with os.fdopen(handle, 'w', encoding='utf-8') as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, target)
        finally:
            try:
                Path(name).unlink(missing_ok=True)
            except OSError:
                pass
        self.record('export-complete')
        return target


class RuntimeHooks:
    def __init__(self, store):
        self.store = store
        self._sys = sys.excepthook
        self._thread = threading.excepthook
        self._qt = None
        self._qt_installed = False

    def install(self):
        # Never forward raw exception text to a redirected console log.
        sys.excepthook = self.python_error
        threading.excepthook = self.thread_error

    def python_error(self, error_type, value, trace):
        self.store.record('python-unhandled', value.with_traceback(trace))
        safe_stderr('Lyria: an unexpected error occurred. Export diagnostics from Settings.')

    def thread_error(self, args):
        self.store.record('thread-unhandled', args.exc_value)
        safe_stderr('Lyria: a background task failed. Export diagnostics from Settings.')

    def install_qt(self):
        from PySide6.QtCore import qInstallMessageHandler
        self._qt = qInstallMessageHandler(self.qt_message)
        self._qt_installed = True

    def qt_message(self, kind, context, message):
        from PySide6.QtCore import QtMsgType
        event = {QtMsgType.QtWarningMsg: 'qt-warning', QtMsgType.QtCriticalMsg: 'qt-critical',
                 QtMsgType.QtFatalMsg: 'qt-fatal'}.get(kind)
        if event:
            self.store.record(event, source=context.file, line=context.line)
        # Qt may include API responses or absolute paths; never persist/forward text.

    def restore(self):
        sys.excepthook = self._sys
        threading.excepthook = self._thread
        if self._qt_installed:
            from PySide6.QtCore import qInstallMessageHandler
            qInstallMessageHandler(self._qt)


def safe_stderr(text):
    try:
        if sys.stderr:
            print(text, file=sys.stderr)
    except (OSError, AttributeError):
        pass


class StartupFailure(RuntimeError):
    pass


def startup_summary(error):
    if isinstance(error, StartupFailure):
        return '主界面未能加载。请完整解压 Lyria，再从原文件夹启动。'
    if isinstance(error, (ImportError, OSError)) and not isinstance(error, PermissionError):
        return '运行组件未能加载。请确认程序已完整解压，未移动或删除运行文件。'
    if isinstance(error, PermissionError):
        return '无法访问所需文件或目录。请检查文件夹权限，并尝试解压到可写的位置。'
    return '程序启动失败，请重试；若问题持续，可导出诊断日志以便排查。'


def show_startup_failure(error, store):
    """Works even without Qt imports on Windows; never shows exception payloads."""
    summary = startup_summary(error)
    detail = ('本地错误日志：' + str(store.directory)) if store.persistent else '错误日志暂时无法写入磁盘。'
    message = summary + '\n\n' + detail + '\n诊断日志仅保存在本地，不包含歌词、音视频、API 密钥或文件路径。'
    widgets = sys.modules.get('PySide6.QtWidgets')
    app = widgets.QApplication.instance() if widgets else None
    if app:
        from PySide6.QtCore import Qt
        box = widgets.QMessageBox()
        box.setWindowTitle('Lyria 启动失败')
        box.setIcon(widgets.QMessageBox.Icon.Critical)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(message)
        export = box.addButton('导出诊断日志…', widgets.QMessageBox.ButtonRole.ActionRole)
        box.addButton('关闭', widgets.QMessageBox.ButtonRole.RejectRole)
        while True:
            box.exec()
            if box.clickedButton() != export:
                break
            target, _ = widgets.QFileDialog.getSaveFileName(None, '导出诊断日志', 'Lyria-diagnostics.json', '诊断 JSON (*.json)')
            if not target:
                continue
            try:
                store.export(target)
            except Exception as exc:
                store.record('export-failed', exc)
                box.setText(summary + '\n\n诊断文件保存失败。请选择可写入的文件夹后重试。')
            else:
                box.setText(summary + '\n\n诊断日志已保存到指定位置。')
        return
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, 'Lyria 启动失败', 0x10)
            return
        except Exception:
            pass
    safe_stderr('Lyria 启动失败：' + message)
