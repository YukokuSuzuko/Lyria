"""GUI-independent .lyria document envelope; revisions are owned by the UI thread.

Autosave recovery is distinct from explicit Save and from an exported subtitle.
Workers receive immutable snapshots and must not mutate the live document.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import uuid

from .model import Project
from .storage import atomic_json, protect_output, protected_project_inputs

FORMAT = "org.lyria.project"
FORMAT_VERSION = 4
READABLE_FORMAT_VERSIONS = (2, 3, 4)
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
_SECRET_NAMES = {"key", "api_key", "apikey", "api-key", "token", "access_token",
                 "refresh_token", "secret", "client_secret", "password",
                 "authorization", "credentials", "credential"}


def _without_secrets(value):
    if isinstance(value, dict):
        return {k: _without_secrets(v) for k, v in value.items()
                if str(k).casefold() not in _SECRET_NAMES}
    if isinstance(value, (list, tuple)):
        return [_without_secrets(v) for v in value]
    return value


def _revision(value, name, minimum=-1):
    if type(value) is not int or value < minimum:
        raise ValueError(f'工程版本记录无效：{name}')
    return value


@dataclass
class ProjectDocument:
    project: Project = field(default_factory=Project)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    revision: int = 0
    saved_revision: int = -1
    exported_revision: int = -1
    alignment_candidate: dict | None = None
    candidate_warning: str = ""
    source_candidates: list = field(default_factory=list)
    source_candidate_warning: str = ""
    entry_mode: str = 'ocr'

    @property
    def main_timeline(self):
        return self.project.main_timeline

    def replace_project(self, project, *, touch=True):
        """Validated single-owner replacement for undo/import/apply operations.

        The document identity remains stable. UI consumers must rebind their
        editor to document.project after replacement; saved revision is untouched.
        """
        checked=self.validate_project(project)
        if checked.to_dict()==self.project.to_dict():return False
        self.project=checked
        if touch:self.touch()
        return True

    def validate_project(self, project):
        """Return a fully checked detached project without mutating live state."""
        if not isinstance(project,Project):raise ValueError('无效的工程数据对象。')
        candidate=self.snapshot()
        candidate['project']=deepcopy(project.to_dict())
        return _validate_snapshot(candidate).project

    def touch(self):
        self.revision += 1
        return self.revision

    def mark_saved(self, revision):
        revision = _revision(revision, "saved", 0)
        if revision > self.revision:
            raise ValueError('保存状态版本号异常')
        self.saved_revision = max(self.saved_revision, revision)

    def mark_exported(self, revision):
        revision = _revision(revision, "exported", 0)
        if revision > self.revision:
            raise ValueError('导出状态版本号异常')
        self.exported_revision = max(self.exported_revision, revision)

    @property
    def close_reasons(self):
        reasons = []
        if self.revision != self.saved_revision:
            reasons.append("unsaved_changes")
        if self.project.mappings and self.revision != self.exported_revision:
            reasons.append("current_revision_not_exported")
        return reasons

    def snapshot(self, *, explicit_save=False):
        """Capture on the owner thread, then hand the result to a save worker.

        `explicit_save` records the revision contained in this saved file, without
        marking the live document saved until write success has been reported.
        """
        body = deepcopy(self.project.to_dict())
        # Do not filter user lyric strings; only secret-bearing metadata fields.
        body["settings"] = _without_secrets(body.get("settings", {}))
        body["api_records"] = _without_secrets(body.get("api_records", []))
        from .alignment_candidate import read_candidate
        candidate, _ = read_candidate(self.alignment_candidate)
        from .candidate_tracks import read_candidates
        source_candidates, _ = read_candidates(self.source_candidates)
        return {"alignment_candidate": candidate, "source_candidates": source_candidates, "format": FORMAT, "format_version": FORMAT_VERSION,
                "document_id": self.id, "revision": self.revision, "entry_mode": self.entry_mode,
                "saved_revision": self.revision if explicit_save else self.saved_revision,
                "exported_revision": self.exported_revision, "project": body}


def _validate_snapshot(obj):
    if not isinstance(obj, dict) or obj.get("format") != FORMAT:
        raise ValueError('不支持打开旧版或非 Lyria 格式的工程文件')
    if type(obj.get("format_version")) is not int or obj["format_version"] not in READABLE_FORMAT_VERSIONS:
        raise ValueError('当前软件版本不支持打开此工程文件')
    # v3 makes the association graph authoritative. Older applications reject
    # this envelope instead of silently rewriting only its compatibility mirror.
    if obj["format_version"] >= 3:
        body = obj.get("project")
        if not isinstance(body, dict) or not isinstance(body.get("alignment_graph"), dict):
            raise ValueError('v0.21 工程缺少权威关联图，不能从兼容时间轴静默恢复。')
    identity = obj.get("document_id")
    if not isinstance(identity, str) or not identity or len(identity) > 128:
        raise ValueError('工程文件编号无效')
    revision = _revision(obj.get("revision"), "current", 0)
    saved = _revision(obj.get("saved_revision"), "saved")
    exported = _revision(obj.get("exported_revision"), "exported")
    if max(saved, exported) > revision:
        raise ValueError('工程文件的保存版本号异常')
    try:
        if 'timebase' not in obj['project']:
            raise ValueError('工程文件缺少时间基准信息')
        project = Project.from_dict(obj["project"])
        project.validate_timeline()
        for rows in (project.lyrics, project.translations, project.visuals, project.mappings):
            identities = [r.id for r in rows]
            if any(not isinstance(i, str) or not i for i in identities) or len(set(identities)) != len(identities):
                raise ValueError('工程文件中存在空白或重复的条目编号')
            for row in rows:
                if hasattr(row, 'text') and not isinstance(row.text, str):
                    raise ValueError('工程文件中的歌词或识别文本格式无效')
                for value in (getattr(row,'start',None),getattr(row,'end',None)):
                    if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or value<0):
                        raise ValueError('工程文件中包含无效的时间数值')
        tables = {'translation_ids': {r.id:r for r in project.translations},
                  'lyric_ids': {r.id:r for r in project.lyrics},
                  'visual_ids': {r.id:r for r in project.visuals}}
        for row in project.mappings:
            for field_name,records in tables.items():
                refs=getattr(row,field_name)
                if not isinstance(refs,list) or any(not isinstance(i,str) or i not in records for i in refs):
                    raise ValueError('工程文件中的关联引用已损坏')
            if row.text_override is not None and not isinstance(row.text_override,str):
                raise ValueError('工程文件中的字幕文本格式无效')
            if not isinstance(row.text_segments,list):raise ValueError('工程文件中的拆分片段记录无效')
            for segment in row.text_segments:
                if not isinstance(segment,dict) or segment.get('id') not in tables['translation_ids']:
                    raise ValueError('工程文件中的拆分片段引用缺失')
                first,last=segment.get('start'),segment.get('end')
                text=tables['translation_ids'][segment['id']].text
                if type(first) is not int or type(last) is not int or not 0<=first<=last<=len(text):
                    raise ValueError('工程文件中的拆分文字位置超出范围')
        if not isinstance(project.inputs, dict) or any(not isinstance(v, str) for v in project.inputs.values()):
            raise ValueError('工程文件中的素材路径记录无效')
        if not isinstance(project.settings, dict):
            raise ValueError("工程设置无效")
        from .candidate_tracks import validate_decisions
        validate_decisions(project.settings.get('candidate_review_decisions', {}))
        links=project.settings.get('pending_text_splits',[]);mapping_ids={m.id for m in project.mappings}
        positions={m.id:n for n,m in enumerate(project.mappings)};outgoing=set();incoming=set()
        if not isinstance(links,list):raise ValueError('工程中的待分字记录格式无效')
        for link in links:
            if (not isinstance(link,dict) or set(link)!={'left','right'}
                    or not isinstance(link['left'],str) or not isinstance(link['right'],str)
                    or link['left'] not in mapping_ids or (link['right'] and link['right'] not in mapping_ids)
                    or link['left']==link['right']):raise ValueError('工程中的待分字引用无效')
            if (link['left'] in outgoing or (link['right'] and link['right'] in incoming)
                    or (link['right'] and positions[link['left']]>=positions[link['right']])):
                raise ValueError('工程中的待分字记录重复或顺序异常')
            outgoing.add(link['left'])
            if link['right']:incoming.add(link['right'])
        if any(not link['right'] and link['left'] in incoming for link in links):raise ValueError('工程中的待分字记录重复')
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('工程文件已损坏或结构无效') from exc
    from .alignment_candidate import read_candidate
    candidate, warning = read_candidate(obj.get("alignment_candidate"))
    from .candidate_tracks import read_candidates
    source_candidates, source_warning = read_candidates(obj.get("source_candidates"))
    mode = obj.get('entry_mode')
    if mode not in ('ocr', 'audio', 'edit', 'separate'):
        mode = infer_entry_mode(project)
    return ProjectDocument(project, identity, revision, saved, exported, candidate, warning, source_candidates, source_warning, mode)


def infer_entry_mode(project):
    if project.visuals or project.settings.get('roi') or project.settings.get('ocr_regions'):
        return 'ocr'
    if project.inputs.get('audio') or project.video.get('media_kind')=='audio':
        return 'audio' if project.lyrics or not project.mappings else 'edit'
    if project.inputs.get('video'):
        return 'audio' if project.lyrics and not project.mappings else 'ocr'
    return 'edit' if project.mappings or project.lyrics or project.translations or project.settings.get('editing_grid_seconds') else 'ocr'


def write_document(snapshot, path):
    """Atomic worker-side write. Return saved revision; live state is untouched."""
    snapshot = deepcopy(snapshot)
    doc = _validate_snapshot(snapshot)
    safe = doc.snapshot(explicit_save=False)
    target = protect_output(path, protected_project_inputs(doc.project))
    protected = protected_project_inputs(doc.project)

    def rotate_backup():
        if not target.is_file() or target.stat().st_size > MAX_DOCUMENT_BYTES:
            return
        try:
            previous = _validate_snapshot(json.loads(target.read_text(encoding="utf-8-sig")))
        except (OSError, ValueError, TypeError):
            return
        atomic_json(target.with_name(target.name + ".bak"), previous.snapshot(),
                    protected, max_bytes=MAX_DOCUMENT_BYTES)

    atomic_json(target, safe, protected, max_bytes=MAX_DOCUMENT_BYTES,
                before_replace=rotate_backup)
    return doc.revision


def load_document(path, *, recover=False):
    target = Path(path)
    try:
        if target.stat().st_size > MAX_DOCUMENT_BYTES:
            raise ValueError('工程文件过大，超出读取上限')
        result = _validate_snapshot(json.loads(target.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError, TypeError):
        if not recover:
            raise
        return load_document(target.with_name(target.name + ".bak"), recover=False)
    root = target.resolve().parent
    result.project.inputs = {key: str(root / value) if value and not Path(value).is_absolute() else value
                             for key, value in result.project.inputs.items()}
    return result
