"""Application-only model preferences, never project text/media or credentials."""
from __future__ import annotations
import json
from pathlib import Path
from lyricsync.storage import atomic_json
from copy import deepcopy

NAME = "local-alignment-settings.json"
FIELDS = {"runtime_python", "reading_runtime_python", "cache_root", "model_id", "separation_model_id", "language", "device", "download_source", "use_separation", "reading_tool"}

def clean(value):
    if not isinstance(value, dict):
        return {}
    result: dict[str, str | bool] = {key: item for key, item in value.items()
              if key in FIELDS - {"use_separation"} and isinstance(item, str)
              and len(item) <= 4096 and "\0" not in item}
    flag = value.get("use_separation")
    if isinstance(flag, bool) or flag in ("true", "false"):
        result["use_separation"] = flag is True or flag == "true"
    return result

def load(directory):
    try:
        path=Path(directory)/NAME
        if path.stat().st_size>32768:return {}
        return clean(json.loads(path.read_text(encoding="utf-8")))
    except (OSError,ValueError,TypeError):
        return {}

def save(directory,value):
    from lyricsync.portable_profile import portable_preferences
    result=portable_preferences(clean(value))
    atomic_json(Path(directory)/NAME,result)
    return result


def effective_cache_root(directory):
    """Resolve the active model cache directory across the entire application."""
    from lyricsync.local_models import CATALOG, default_cache
    raw = str(load(directory).get("cache_root", "") or "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if candidate.is_dir():
            nested = candidate / "models-v1"
            if nested.is_dir() and not any((candidate / mid).is_dir() for mid in CATALOG) and any((nested / mid).is_dir() for mid in CATALOG):
                return str(nested.resolve())
        return str(candidate.resolve()) if candidate.exists() else str(candidate)
    return str(default_cache())


def effective_download_source(directory):
    raw = str(load(directory).get("download_source", "") or "").strip()
    if raw in ("mirror", "official"):
        return raw
    if raw in ("huggingface",):
        return "official"
    if raw in ("modelscope",):
        return "mirror"
    return "mirror"


def update_model_preferences(directory, *, cache_root=None, download_source=None):
    """Update shared model cache path and download source without erasing other preferences."""
    current = load(directory)
    if cache_root is not None:
        current["cache_root"] = str(cache_root).strip()
    if download_source is not None:
        if download_source not in ("mirror", "official", "huggingface", "modelscope"):
            raise ValueError("不支持的下载源模式。")
        current["download_source"] = download_source
    return save(directory, current)


SOURCE_KEY = 'local_alignment_source'


def source_signature(document):
    """Guard a modeless/background change while a source dialog is open."""
    from lyricsync.candidate_tracks import _digest
    project = document.project.to_dict()
    return _digest({'document': document.id, **{name:project[name]
        for name in ('lyrics', 'translations', 'mappings')}})


def clean_source_state(value):
    """Project-local source snapshots; never place lyrics in global preferences."""
    if not isinstance(value, dict) or value.get('version') != 1:
        return {}
    if value.get('text_source') not in ('reference', 'main', 'external'):
        return {}
    try:
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')) > 1024 * 1024:
            return {}
        from lyricsync.local_alignment import _validate_source_rows
        external = value.get('external_rows', [])
        readings = value.get('reading_rows', [])
        def rows(items):
            if items == []:
                return []
            checked = _validate_source_rows(items)
            allowed = {'id', 'text', 'lyric_ids', 'reading', 'reading_segments', 'source_reading', 'reading_origin', 'reading_issues', 'generated_reading', 'reading_confirmed_for', 'alignment_units', 'reading_tool', 'reading_conversion'}
            result = [{key:deepcopy(item) for key, item in row.items() if key in allowed} for row in checked]
            for row in result:
                confirmation=row.get('reading_confirmed_for')
                if confirmation is not None:
                    if not isinstance(confirmation,list) or len(confirmation)!=2 or any(not isinstance(x,str) or len(x)>24000 for x in confirmation):
                        raise ValueError('读音确认记录无效。')
                    if confirmation != [row['text'],row.get('reading','')]:row.pop('reading_confirmed_for',None)
                if 'generated_reading' in row and (not isinstance(row['generated_reading'],str) or len(row['generated_reading'])>24000):
                    raise ValueError('生成读音记录无效。')
                problems=row.get('reading_issues',[])
                if not isinstance(problems,list) or len(problems)>100 or any(not isinstance(x,dict) for x in problems):
                    raise ValueError('读音预检记录无效。')
                for problem in problems:
                    if type(problem.get('requires_review')) is not bool or not isinstance(problem.get('message'),str) or len(problem['message'])>4000:
                        raise ValueError('读音预检说明无效。')
                if 'reading' in row and (not isinstance(row['reading'], str) or len(row['reading']) > 24000):
                    raise ValueError('读音无效。')
                ids = row.get('lyric_ids', [])
                if not isinstance(ids, list) or len(ids) > 1000 or any(not isinstance(i, str) or len(i) > 256 for i in ids):
                    raise ValueError('原词关联无效。')
            return result
        origin = value.get('origin', '')
        if not isinstance(origin, str) or len(origin) > 32768 or '\0' in origin:
            return {}
        pairs=value.get('split_pairs',[])
        if not isinstance(pairs,list) or len(pairs)>1000:raise ValueError('分流数据无效。')
        for pair in pairs:
            if not isinstance(pair,dict) or set(pair)-{'lyric_id','source','translation_id','translation'}:raise ValueError('分流数据无效。')
            if not all(isinstance(pair.get(k),str) and len(pair[k])<=24000 for k in ('translation_id','translation')):raise ValueError('译文数据无效。')
            if pair.get('lyric_id') and not isinstance(pair.get('source'),str):raise ValueError('原词数据无效。')
        return dict(version=1, text_source=value['text_source'], external_rows=rows(external),split_pairs=deepcopy(pairs),
                    origin=origin, reading_rows=rows(readings))
    except (ValueError, TypeError, RecursionError):
        return {}


def load_source_state(project):
    return clean_source_state(project.settings.get(SOURCE_KEY))


def save_source_state(project, value):
    result = clean_source_state(value)
    if not result or project.settings.get(SOURCE_KEY) == result:
        return False
    project.settings[SOURCE_KEY] = result
    return True
