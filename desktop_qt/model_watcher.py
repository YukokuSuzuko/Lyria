"""Debounced filesystem watcher for external model and accelerator drops."""
from __future__ import annotations

from pathlib import Path
from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal
from lyricsync.local_models import CATALOG, ModelManager


class ModelDirectoryWatcher(QObject):
    """Watch cache_root & accelerators/ and auto-verify newly dropped complete models."""

    changed = Signal()

    def __init__(self, parent=None, *, on_ready=lambda: None):
        super().__init__(parent)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._schedule)
        self._watcher.fileChanged.connect(self._schedule)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(850)
        self._timer.timeout.connect(self._reconcile)
        self._cache_root = None
        self._accel_root = None
        self._verifying = False
        self._on_ready = on_ready

    def configure(self, cache_root, accel_root=None):
        old_dirs = self._watcher.directories()
        if old_dirs:
            self._watcher.removePaths(old_dirs)
        old_files = self._watcher.files()
        if old_files:
            self._watcher.removePaths(old_files)
        self._cache_root = Path(cache_root) if cache_root else None
        self._accel_root = Path(accel_root) if accel_root else None
        candidates = []
        if self._cache_root and self._cache_root.is_dir():
            candidates.append(self._cache_root)
            for mid, spec in CATALOG.items():
                for folder in (self._cache_root / mid, self._cache_root / mid / spec.revision):
                    if folder.is_dir():
                        candidates.append(folder)
        if self._accel_root and self._accel_root.is_dir():
            candidates.append(self._accel_root)
            for variant in ('nvidia', 'amd', 'directml'):
                sub = self._accel_root / variant
                if sub.is_dir():
                    candidates.append(sub)
        unique = [str(p) for p in dict.fromkeys(candidates)]
        if unique:
            self._watcher.addPaths(unique)

    def _schedule(self, path=''):
        name = Path(path).name.lower() if path else ''
        if name.endswith(('.part', '.tmp', '.crdownload', '.lock')) or name.startswith('.download-'):
            return
        self._timer.start()

    def _reconcile(self):
        if self._cache_root and self._cache_root.is_dir():
            self.configure(self._cache_root, self._accel_root)
            try:
                manager = ModelManager(self._cache_root)
                for mid in CATALOG:
                    st = manager.status(mid)
                    if st['present'] and not st['verification_cached']:
                        from .local_model_tasks import model_tasks
                        tasks = model_tasks()
                        if tasks.task is None and not self._verifying:
                            self._verifying = True
                            tasks.start(
                                lambda cancel, progress, m=manager, target=mid: m.ensure_verified(target, cancel=cancel, progress=progress),
                                {'cache_root': str(self._cache_root)},
                                'auto_verify',
                            )
                            tasks.finished.connect(self._verify_finished)
                            break
            except Exception:
                pass
        self.changed.emit()
        self._on_ready()

    def _verify_finished(self):
        self._verifying = False
        try:
            from .local_model_tasks import model_tasks
            model_tasks().finished.disconnect(self._verify_finished)
        except Exception:
            pass
        self.changed.emit()
        self._on_ready()
