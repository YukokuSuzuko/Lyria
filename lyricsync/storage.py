"""Atomic project snapshots and versioned, content-addressed local caches."""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
import sqlite3
import time
import threading
import tempfile
from collections import OrderedDict
from copy import deepcopy
from contextlib import closing, contextmanager

from .model import Project


def canonical(path):
    return str(Path(path).resolve()).casefold()


def protected_project_inputs(project):
    """All current and explicitly archived source files remain read-only inputs."""
    paths=list(project.inputs.values())
    records=project.settings.get('timeline_archives',[])
    if isinstance(records,list):
        for record in records:
            archived=record.get('project',{}) if isinstance(record,dict) else {}
            values=archived.get('inputs',{}) if isinstance(archived,dict) else {}
            if isinstance(values,dict):
                paths.extend(value for value in values.values() if isinstance(value,str) and value)
    return list(dict.fromkeys(paths))


def protect_output(path, inputs):
    target = Path(path).resolve()
    sources = [Path(p) for p in inputs if p]
    if canonical(target) in {canonical(p) for p in sources}:
        raise ValueError('不能直接覆盖原导入素材，请另选保存文件名。')
    if Path(path).is_symlink():
        raise ValueError('不支持直接写入符号链接文件，请另选普通保存路径。')
    # Different names can still identify the same source file (hard links).
    # Resolving paths alone cannot protect such aliases.
    for source in sources:
        try:
            if target.samefile(source):
                raise ValueError('不能直接覆盖原导入素材，请另选保存文件名。')
        except FileNotFoundError:
            pass
    return target


class DocumentSizeError(ValueError):
    """A staged project is larger than the supported reader envelope."""


@contextmanager
def _atomic_writer(path, inputs=(), encoding="utf-8", *, max_bytes=None, before_replace=None):
    inputs = tuple(inputs)
    path = protect_output(path, inputs)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        # Exclusive creation prevents stale/shared .tmp names from truncating
        # another file, including an input linked under that temporary name.
        with tempfile.NamedTemporaryFile(mode="w", encoding=encoding,
                dir=path.parent, prefix=".lyria-", suffix=".tmp",
                delete=False) as stream:
            temp = Path(stream.name)
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        if max_bytes is not None and temp.stat().st_size > max_bytes:
            raise DocumentSizeError('工程超过可重新打开的大小上限；本次未保存，原文件和备份未改变。可先导出字幕或管理不需要的历史候选/归档。')
        if before_replace is not None:
            before_replace()
        # Windows can briefly deny a replacement when another writer is
        # replacing the same target. Keep the complete temporary snapshot and
        # retry only those sharing/access errors; never fall back to deleting
        # the target or writing it in place. Persistent failures still surface.
        for attempt in range(5):
            protect_output(path, inputs)
            try:
                os.replace(temp, path)
                break
            except OSError as error:
                if (os.name != 'nt' or getattr(error, 'winerror', None) not in (5, 32, 33)
                        or attempt == 4):
                    raise
                time.sleep(.01 * 2 ** attempt)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def atomic_json(path, obj, inputs=(), *, max_bytes=None, before_replace=None):
    with _atomic_writer(path, inputs, max_bytes=max_bytes, before_replace=before_replace) as stream:
        json.dump(obj, stream, ensure_ascii=False, indent=2, allow_nan=False)


def atomic_text(path, body, inputs=(), encoding="utf-8"):
    with _atomic_writer(path, inputs, encoding) as stream:
        stream.write(body)


def save_project(project, path):
    # The key never enters Project settings. Drop accidental secret-bearing keys.
    obj = project.to_dict()
    for key in list(obj["settings"]):
        if any(word in key.casefold() for word in ("key", "token", "secret")):
            del obj["settings"][key]
    # Validate authoritative links before touching either the destination or backup.
    Project.from_dict(obj)
    path=protect_output(path,protected_project_inputs(project))
    if path.is_file():
        try:
            previous=json.loads(path.read_text(encoding='utf-8-sig'))
            Project.from_dict(previous)
        except (ValueError,TypeError,KeyError):pass
        else:
            for key in list(previous.get('settings',{})):
                if any(word in key.casefold() for word in ('key','token','secret')):del previous['settings'][key]
            atomic_json(path.with_name(path.name+'.bak'),previous,protected_project_inputs(project))
    atomic_json(path, obj, protected_project_inputs(project))


def load_project(path,recover=False):
    try:project=Project.from_dict(json.loads(Path(path).read_text(encoding="utf-8-sig")))
    except (ValueError,TypeError,KeyError,OSError):
        if not recover:raise
        backup=Path(path).with_name(Path(path).name+'.bak')
        project=Project.from_dict(json.loads(backup.read_text(encoding='utf-8-sig')))
    parent=Path(path).resolve().parent
    project.inputs={key:str((parent/target).resolve()) if target and not Path(target).is_absolute() else target
                    for key,target in project.inputs.items()}
    return project


def copy_project_cache(source_project, target_project, inputs=()):
    """Copy a consistent SQLite snapshot, including committed WAL and call budgets.

    Opening a project makes a private working copy, not an empty cache and budget.
    Read-only backup also works while the original application holds the DB open.
    """
    source = Path(source_project).with_suffix('.cache.sqlite')
    target = protect_output(Path(target_project).with_suffix('.cache.sqlite'), inputs)
    if not source.is_file():
        return False
    if canonical(source) == canonical(target):
        return True
    if target.exists():
        raise FileExistsError('目标缓存文件已存在，无法覆盖。')
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as old:
        with closing(sqlite3.connect(target)) as new:
            old.backup(new)
    return True


class ProjectSaver:
    """One serialized writer; Tk hands off detached snapshots, never live objects.

    Pending writes for the same path coalesce. flush() is the barrier before an
    OCR worker takes over checkpoint writes and before closing the application.
    Failures remain observable until a later successful write for the same path.
    """
    def __init__(self, notify=lambda *args: None, writer=save_project):
        self.notify, self.writer = notify, writer
        self.condition = threading.Condition()
        self.pending = OrderedDict()
        self.thread = None
        self.errors = {}
        self.revision = 0

    def submit(self, project, path, recovery_path=None):
        snapshot = deepcopy(project.to_dict())
        with self.condition:
            self.revision += 1
            revision = self.revision
            self.pending[str(Path(path).resolve())] = (revision, snapshot, recovery_path)
            if self.thread is None:
                self.thread = threading.Thread(target=self._work, daemon=False)
                self.thread.start()
        return revision

    def _work(self):
        while True:
            with self.condition:
                if not self.pending:
                    self.thread = None
                    self.condition.notify_all()
                    return
                path, (revision, snapshot, recovery) = self.pending.popitem(last=False)
            error = None
            try:
                self.writer(Project.from_dict(snapshot), path)
                if recovery:
                    atomic_json(recovery, {'path': path})
            except Exception as exc:
                error = type(exc).__name__  # Never send arbitrary file contents or secrets to UI.
            with self.condition:
                if error:
                    self.errors[path] = error
                else:
                    self.errors.pop(path, None)
            self.notify(revision, path, error)

    def flush(self):
        with self.condition:
            while self.thread is not None:
                self.condition.wait()
            if self.errors:
                raise OSError('工程保存失败：' + ', '.join(sorted(set(self.errors.values()))))

    def close(self):
        self.flush()


def file_hash(path, cancel=lambda: False):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            if cancel():
                raise InterruptedError('任务已取消。')
            digest.update(block)
    return digest.hexdigest()


def relocate_inputs(project, paths):
    """Allow moved files only after proving identical contents; validate atomically."""
    if set(paths)!=set(project.inputs):raise ValueError('重新定位仅支持找回移动后的同一份素材；如需更换素材请新建工程。')
    for key,target in paths.items():
        if target==project.inputs.get(key):continue
        old=project.inputs.get(key)
        expected=project.video.get('content_hash') if key in ('video','audio') else project.settings.get('input_hashes',{}).get(key)
        if not expected and old and Path(old).is_file():expected=file_hash(old)
        if not expected or not Path(target).is_file() or file_hash(target)!=expected:
            raise ValueError('所选文件内容与原素材不一致（'+key+'），如需更换素材请新建工程。')
    project.inputs=dict(paths)


class Cache:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=30)
        # Concurrent first-open can return SQLITE_BUSY immediately for this
        # PRAGMA despite connect(timeout=30). Retry only BUSY/LOCKED, bounded;
        # never retry corruption, disk or permission errors as lock contention.
        for attempt in range(8):
            try:
                self.db.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                code = getattr(exc, 'sqlite_errorcode', None)
                if code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or attempt == 7:
                    self.db.close()
                    raise
                time.sleep(.01 * (attempt + 1))
        self.db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, body TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY, stamp REAL, request TEXT, status TEXT, usage TEXT)")
        self.db.commit()

    @staticmethod
    def key(namespace, obj):
        raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        return namespace + ":" + hashlib.sha256(raw).hexdigest()

    def get(self, key):
        row = self.db.execute("SELECT body FROM cache WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, obj):
        self.db.execute("INSERT OR REPLACE INTO cache VALUES (?,?)",
                        (key, json.dumps(obj, ensure_ascii=False, allow_nan=False)))
        self.db.commit()

    def reserve_call(self, request_hash, maximum):
        # Failures and retries also spend a slot, including across restarts.
        self.db.execute("BEGIN IMMEDIATE")
        count = self.db.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
        if count >= maximum:
            self.db.rollback()
            raise RuntimeError('已达当前工程的 API 调用次数上限（含重试），可在 AI 设置中调高上限。')
        cursor = self.db.execute("INSERT INTO calls(stamp,request,status,usage) VALUES (?,?,?,?)",
                                 (time.time(), request_hash, "pending", "{}"))
        self.db.commit()
        return cursor.lastrowid

    def finish_call(self, call_id, status, usage=None):
        self.db.execute("UPDATE calls SET status=?,usage=? WHERE id=?",
                        (status, json.dumps(usage or {}), call_id))
        self.db.commit()

    def records(self):
        return [dict(zip(("id", "time", "request_hash", "status", "usage"), row))
                for row in self.db.execute("SELECT id,stamp,request,status,usage FROM calls ORDER BY id")]

    def restore_records(self, records):
        """Hydrate a document's durable budget when its cache sidecar is absent.

        Repeated opening is idempotent. A conflicting event ID means a different
        document/cache lineage; refuse rather than erase or reset spent calls.
        Successful response bodies are not present in budget records, so this
        does not invent cached model responses.
        """
        import math
        validated=[];seen=set()
        if not isinstance(records,list):raise ValueError('工程中的 API 调用记录格式无效')
        for row in records:
            if not isinstance(row,dict):raise ValueError('工程中的 API 调用记录格式无效')
            identity=row.get('id');stamp=row.get('time');request=row.get('request_hash');status=row.get('status')
            if (type(identity) is not int or identity<=0 or identity in seen
                    or type(stamp) not in (int,float) or not math.isfinite(stamp) or stamp<0
                    or not isinstance(request,str) or not request or len(request)>512
                    or not isinstance(status,str) or not status or len(status)>64):
                raise ValueError('工程中的 API 调用记录无效或重复')
            usage=row.get('usage','{}')
            try: usage=json.loads(usage) if isinstance(usage,str) else usage
            except ValueError as exc:raise ValueError('工程中的 API 用量记录格式无效') from exc
            if not isinstance(usage,dict):raise ValueError('工程中的 API 用量记录格式无效')
            validated.append((identity,stamp,request,status,json.dumps(usage,allow_nan=False)))
            seen.add(identity)
        inserted=0
        self.db.execute('BEGIN IMMEDIATE')
        try:
            for row in validated:
                existing=self.db.execute('SELECT request,stamp FROM calls WHERE id=?',(row[0],)).fetchone()
                if existing:
                    if existing[0]!=row[2] or existing[1]!=row[1]:
                        raise ValueError('本地缓存中的 API 调用记录与工程不一致，请另存工程或清理缓存目录后重试')
                    continue
                self.db.execute('INSERT INTO calls(id,stamp,request,status,usage) VALUES (?,?,?,?,?)',row)
                inserted+=1
            self.db.commit()
        except Exception:
            self.db.rollback();raise
        return inserted

    def close(self):
        self.db.close()
