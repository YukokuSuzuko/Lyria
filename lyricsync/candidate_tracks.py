"""Read-only OCR/vocal proposals, separately owned from the editable timeline.

All heavy hashing belongs on a worker with a detached document. Apply only checks
the verified file-stat token plus current identities and row fingerprints.
"""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import uuid
import time

from .editing import mapping_text
from .model import Mapping, Project, TEXT_ROLES
from .timebase import validate_boundaries

VERSION = 1
SOURCES = ('ocr', 'vocal')
MAX_RECORDS = 12
MAX_ROWS = 10000
MAX_BYTES = 8 * 1024 * 1024
DECISIONS = 'candidate_review_decisions'


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _stat(path):
    value = Path(path).stat()
    return [value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns]


def _paths(project):
    return {key: str(Path(path).resolve()).casefold()
            for key, path in project.inputs.items() if path}


def _clock(project, frame_index, *, refresh=False):
    return {'timebase': deepcopy(project.timebase),
            'frames': None if frame_index is None else
            (frame_index.candidate_signature(refresh=refresh) if hasattr(frame_index,'candidate_signature')
             else _digest({'stamps': list(frame_index.stamps), 'duration': frame_index.duration}))}


def _validate_file_table(files, *, auxiliary=False):
    maximum = 8 if auxiliary else 32
    if not isinstance(files, dict) or len(files) > maximum or auxiliary and not files:
        raise ValueError('附加音频素材列表无效' if auxiliary else '识别结果关联的素材列表无效')
    for role, item in files.items():
        if not _text(role) or not isinstance(item, dict) or set(item) != {'path', 'sha256', 'stat'}:
            raise ValueError('素材来源记录无效')
        if not _text(item['path'], 32768) or not _text(item['sha256'], 64) or len(item['sha256']) != 64 or any(c not in '0123456789abcdef' for c in item['sha256']):
            raise ValueError('素材路径或校验信息无效')
        if not isinstance(item['stat'], list) or len(item['stat']) != 5 or any(type(v) is not int for v in item['stat']):
            raise ValueError('素材文件校验信息无效')
        if auxiliary:
            path = item['path']
            if ('\x00' in path or '://' in path or not Path(path).is_absolute() or
                    '..' in Path(path).parts or item['stat'][2] < 0):
                raise ValueError('附加音频素材必须为有效的本地文件')


def _validate_provenance(provenance):
    if not isinstance(provenance, dict) or set(provenance) not in (
            {'files', 'clock'}, {'files', 'clock', 'auxiliary_files'}):
        raise ValueError('识别结果的素材校验信息无效')
    _validate_file_table(provenance['files'])
    if 'auxiliary_files' in provenance:
        _validate_file_table(provenance['auxiliary_files'], auxiliary=True)
    from .timebase import Timebase
    clock = provenance['clock']
    if not isinstance(clock, dict) or set(clock) != {'timebase', 'frames'}:
        raise ValueError('时间基准无效')
    Timebase.from_dict(clock['timebase'])
    if clock['frames'] is not None and (not _text(clock['frames'], 64) or len(clock['frames']) != 64):
        raise ValueError('视频帧索引校验信息无效')


def _auxiliary_paths(provenance):
    _validate_provenance(provenance)
    return {role: item['path'] for role, item in provenance.get('auxiliary_files', {}).items()}


def _normalize_auxiliary_paths(paths):
    if paths is None:
        return {}
    if not isinstance(paths, dict) or len(paths) > 8:
        raise ValueError('附加音频路径列表无效')
    result = {}
    for role, value in paths.items():
        if not _text(role) or not isinstance(value, (str, Path)):
            raise ValueError('附加音频路径无效')
        raw = str(value)
        if not raw or len(raw) > 32768 or '\x00' in raw or '://' in raw:
            raise ValueError('附加音频必须为本地文件，不支持网络地址')
        result[role] = str(Path(raw).resolve()).casefold()
    return result


def _hash_paths(paths, cancel, label='素材'):
    files = {}
    for role, path in paths.items():
        if cancel():
            raise InterruptedError('已取消素材验证。')
        try:
            before = _stat(path)
            digest = hashlib.sha256()
            with open(path, 'rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    if cancel():
                        raise InterruptedError('已取消素材验证。')
                    digest.update(chunk)
            after = _stat(path)
        except InterruptedError:
            raise
        except OSError as exc:
            raise ValueError(label + '无法读取，请重新选择文件。') from exc
        if before != after:
            raise ValueError(label + '在读取过程中发生了变化，请重试。')
        files[role] = {'path': path, 'sha256': digest.hexdigest(), 'stat': after}
    return files


def verify_provenance(document, frame_index=None, *, auxiliary_paths=None, cancel=lambda: False):
    """Worker-only full verification; auxiliary files never enter project.inputs."""
    if cancel():
        raise InterruptedError('已取消素材验证。')
    auxiliary_paths = _normalize_auxiliary_paths(auxiliary_paths)
    token = {'files': _hash_paths(_paths(document.project), cancel),
             'clock': _clock(document.project, frame_index, refresh=True)}
    if auxiliary_paths:
        token['auxiliary_files'] = _hash_paths(auxiliary_paths, cancel, '打轴音频/辅助素材')
    return token


def _source_signature(provenance):
    # Omission is deliberate: byte-identical legacy signatures for dev3 records.
    body = {'files': {role: {'path': item['path'], 'sha256': item['sha256']}
                      for role, item in provenance['files'].items()},
            'clock': provenance['clock']}
    if 'auxiliary_files' in provenance:
        body['auxiliary_files'] = {role: {'path': item['path'], 'sha256': item['sha256']}
                                  for role, item in provenance['auxiliary_files'].items()}
    return _digest(body)


def _check_token(document, token, frame_index):
    """UI/apply-safe validation: schemas and stat only, never open/hash files."""
    _validate_provenance(token)
    if token['clock'] != _clock(document.project, frame_index):
        raise ValueError('视频帧索引已变化，请重新运行识别或打轴。')
    paths = _paths(document.project)
    if paths != {role: item['path'] for role, item in token['files'].items()}:
        raise ValueError('素材路径已变动，请重新运行识别或打轴。')
    for field, label in (('files', '素材'), ('auxiliary_files', '打轴音频/辅助素材')):
        for item in token.get(field, {}).values():
            try:
                if _stat(item['path']) != item['stat']:
                    raise ValueError(label + '文件已变动，请重新校验后再应用。')
            except OSError as exc:
                raise ValueError(label + '无法读取，请重新选择文件。') from exc


def _verify_record_provenance(document, record, frame_index, current_provenance, cancel):
    """Worker preview rehashes recorded auxiliary files even with a primary token."""
    if cancel():
        raise InterruptedError('已取消素材验证。')
    expected_paths = _auxiliary_paths(record['provenance'])
    if current_provenance is None:
        return verify_provenance(document, frame_index, auxiliary_paths=expected_paths, cancel=cancel)
    token = deepcopy(current_provenance)
    supplied_paths = _auxiliary_paths(token)
    if supplied_paths and supplied_paths != expected_paths:
        raise ValueError('当前音频文件与生成该结果时不一致，请重新选择或重新打轴。')
    if expected_paths:
        token['auxiliary_files'] = _hash_paths(expected_paths, cancel, '打轴音频/辅助素材')
    return token


def verify_candidate_source(document, record, frame_index=None, *, current_provenance=None, cancel=lambda: False):
    """Worker-side verification before reusing stored evidence."""
    token = _verify_record_provenance(document, record, frame_index, current_provenance, cancel)
    _check_token(document, token, frame_index)
    if record['document_id'] != document.id or _source_signature(token) != record['source_signature']:
        raise ValueError('原素材文件已变动，请重新识别后再应用。')
    return token


def candidate_source_status(document, record, frame_index=None):
    """Cheap display-only invalidation hint, never hashes files or decoded PTS.

    Empty string means unchanged stat/clock, not a fresh content verification.
    Apply still requires its worker-verified provenance token.
    """
    if record['document_id'] != document.id:
        return '该识别结果属于其他工程，无法直接应用。'
    try:
        _check_token(document,record['provenance'],frame_index)
    except (ValueError,OSError,TypeError,KeyError,AttributeError) as exc:
        return str(exc) if isinstance(exc,ValueError) else '关联的音视频素材不可用或已变更，请重新识别。'
    return ''


def _project_index(project):
    """Build once per public operation, never once per sentence."""
    tables = {}
    for name in ('translations','lyrics','visuals'):
        tables[name] = {item.id:(position,item) for position,item in enumerate(getattr(project,name))}
    return {'tables':tables,
            'texts':{identity:item.text for identity,(_,item) in tables['translations'].items()}}


def _independent_edge_metadata(project,row):
    graph=project.alignment_graph
    defaults=dict(state='manual_locked' if row.locked or row.confirmed or (row.human_edited and not row.method.startswith('candidate_')) else 'auto_high' if row.confidence>=.85 else 'uncertain',confidence=row.confidence,reasons=list(row.reasons))
    result={}
    for name in ('visual_source_edges','source_translation_edges'):
        edge=getattr(graph,name).get(row.id)
        if edge:
            metadata={key:deepcopy(edge[key]) for key in defaults}
            if metadata!=defaults:result[name]=metadata
    return result


def _independent_edge_locked(project,row):
    return any(meta.get('state')=='manual_locked' for meta in _independent_edge_metadata(project,row).values())


def _fingerprint(project, row, index=None):
    index = _project_index(project) if index is None else index
    def referenced(table, identities):
        records = index['tables'][table]
        # Preserve legacy table-order hashing, not reference-list order. Duplicate
        # references previously selected a record once, so deduplicate here too.
        selected = [records[identity] for identity in set(identities) if identity in records]
        selected.sort(key=lambda item:item[0])
        result=[]
        for _,item in selected:
            value=asdict(item)
            if not value.get('subtitle_metadata'):value.pop('subtitle_metadata',None)
            result.append(value)
        return result
    row_data = asdict(row)
    legacy = project.settings.get('speaker_profiles', {}).get(row.speaker_id, {})
    if not row.speaker_name or row.speaker_id and row.speaker_name == legacy.get('name'):
        row_data.pop('speaker_name', None)
    if not row_data.get('alignment_tokens'):row_data.pop('alignment_tokens',None)
    # Preserve baseline hashes of pre-role version-1 candidates.
    if row.text_role == 'translation':
        row_data.pop('text_role', None)
    # Newly optional fields must not make unchanged persisted dev8 candidates
    # falsely stale. Nonempty styles/overrides/import evidence remain guarded.
    if not row.style_id:row_data.pop('style_id',None)
    if not row.style_overrides:row_data.pop('style_overrides',None)
    body={'row': row_data, 'text': mapping_text(project,row,index['texts']),
                    'translations':referenced('translations',row.translation_ids),
                    'lyrics':referenced('lyrics',row.lyric_ids),
                    'visuals':referenced('visuals',row.visual_ids)}
    metadata=_independent_edge_metadata(project,row)
    if metadata:body['graph_metadata']=metadata
    return _digest(body)


def capture_source(document, source, frame_index=None, *, current_provenance=None,
                   auxiliary_paths=None, cancel=lambda: False):
    if source not in SOURCES:
        raise ValueError('未知的识别结果类型。')
    if cancel():
        raise InterruptedError('已取消素材验证。')
    paths = _normalize_auxiliary_paths(auxiliary_paths)
    provenance = deepcopy(current_provenance) if current_provenance is not None else verify_provenance(
        document, frame_index, auxiliary_paths=paths, cancel=cancel)
    if current_provenance is not None and paths:
        supplied = _auxiliary_paths(provenance)
        if supplied and supplied != paths:
            raise ValueError('附加音频文件路径与校验信息不一致，请重新选择。')
        provenance['auxiliary_files'] = _hash_paths(paths, cancel, '打轴音频/辅助素材')
    _check_token(document, provenance, frame_index)
    index = _project_index(document.project)
    return {'document_id': document.id, 'revision': document.revision, 'source': source,
            'provenance': provenance, 'project': deepcopy(document.project.to_dict()),
            'base_rows': {row.id: _fingerprint(document.project, row, index) for row in document.main_timeline}}


def candidate_from_project(snapshot, generated_project):
    """Preserve generated unknown IDs as unbound proposals, never guess a match."""
    generated_project.validate_timeline()
    rows = []
    text_lookup = {item.id:item.text for item in generated_project.translations}
    for row in generated_project.project_timeline(detached=True):
        known = row.id in snapshot['base_rows']
        rows.append({'id': row.id, 'sentence_id': row.id if known else None,
                     'base_signature': snapshot['base_rows'].get(row.id),
                     'text': mapping_text(generated_project, row, text_lookup), 'start': row.start, 'end': row.end,
                     'confidence': float(row.confidence), 'reasons': list(row.reasons),
                     'status': 'ready' if known and row.start is not None and row.end is not None else
                               'missing' if known else 'new',
                     'provenance': {'lyric_ids': list(row.lyric_ids), 'visual_ids': list(row.visual_ids),
                                    'translation_ids': list(row.translation_ids)}})
        if getattr(row,'alignment_tokens',[]):rows[-1]['alignment_tokens']=deepcopy(row.alignment_tokens)
        role = 'source_asr' if row.method == 'asr_confirmed_source' else row.text_role
        if role != 'translation':
            rows[-1]['text_role'] = role
    from .project_document import _without_secrets
    record = {'version': VERSION, 'id': 'candidate-' + uuid.uuid4().hex,
              'source': snapshot['source'], 'document_id': snapshot['document_id'], 'created_at': int(time.time()),
              'revision': snapshot['revision'], 'source_signature': _source_signature(snapshot['provenance']),
              'provenance': deepcopy(snapshot['provenance']), 'rows': rows,
              'evidence': {name: _without_secrets(deepcopy(generated_project.to_dict()[name]))
                           for name in ('lyrics','translations','visuals')}}
    safe, warning = read_candidates([record])
    if warning:
        raise ValueError(warning)
    return safe[0]


def _text(value, maximum=256):
    return isinstance(value, str) and 0 < len(value) <= maximum


def validate_decisions(value):
    if not isinstance(value, dict) or len(value) > MAX_RECORDS * MAX_ROWS:
        raise ValueError('已忽略条目的记录格式无效。')
    for key, ignored in value.items():
        if not _text(key, 600) or ignored is not True:
            raise ValueError('已忽略条目的记录格式无效。')
    return value


def read_candidates(value):
    """Bounded optional metadata: reject corrupt candidates, preserve the project."""
    if value is None:
        return [], ''
    try:
        if not isinstance(value, list) or len(value) > MAX_RECORDS:
            raise ValueError('保存的历史识别结果数量超出上限')
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')) > MAX_BYTES:
            raise ValueError('识别结果数据超过 8 MB 大小上限')
        record_ids = set()
        for record in value:
            fields = {'version', 'id', 'source', 'document_id', 'revision', 'source_signature', 'provenance', 'rows', 'evidence'}
            if not isinstance(record, dict) or set(record) - {'created_at'} != fields or type(record['version']) is not int or record['version'] != VERSION:
                raise ValueError('识别结果的数据格式或版本不受支持')
            if record['source'] not in SOURCES or not _text(record['id']) or record['id'] in record_ids or not _text(record['document_id'],128):
                raise ValueError('识别结果编号无效')
            if 'created_at' in record and (type(record['created_at']) is not int or not 0 <= record['created_at'] <= 4102444800):
                raise ValueError('识别结果生成时间无效')
            record_ids.add(record['id'])
            if type(record['revision']) is not int or record['revision'] < 0:
                raise ValueError('识别结果版本记录无效')
            provenance = record['provenance']
            _validate_provenance(provenance)
            if record['source_signature'] != _source_signature(provenance):
                raise ValueError('素材校验信息不匹配')
            if not isinstance(record['rows'], list) or len(record['rows']) > MAX_ROWS:
                raise ValueError('识别结果总句数超出上限')
            evidence = record['evidence']
            if not isinstance(evidence,dict) or set(evidence) != {'lyrics','translations','visuals'}:
                raise ValueError('识别结果的原始记录格式无效')
            if any(not isinstance(rows,list) or len(rows)>MAX_ROWS for rows in evidence.values()):
                raise ValueError('识别结果的原始记录数量超出上限')
            from .project_document import ProjectDocument
            evidence_project = Project.from_dict({'version':2, **deepcopy(evidence)})
            ProjectDocument().validate_project(evidence_project)
            evidence_ids = {name: {row['id'] for row in records} for name,records in evidence.items()}
            row_ids = set()
            target_ids = set()
            for row in record['rows']:
                fields = {'id','sentence_id','base_signature','text','start','end','confidence','reasons','status','provenance'}
                if not isinstance(row,dict) or not fields <= set(row) or set(row)-fields-{'text_role','alignment_tokens'}:
                    raise ValueError('识别结果条目格式无效')
                from .alignment_tokens import validate_alignment_tokens
                validate_alignment_tokens(row.get('alignment_tokens',[]))
                if row.get('text_role', 'translation') not in TEXT_ROLES:
                    raise ValueError('识别结果的文本类型无效')
                # Text origin and timing origin are independent: e.g. vocal
                # alignment may propose times for an existing OCR source lyric.
                if not _text(row['id']) or row['id'] in row_ids:
                    raise ValueError('识别结果条目编号无效或重复')
                row_ids.add(row['id'])
                if row['sentence_id'] is not None and not _text(row['sentence_id']):
                    raise ValueError('关联的主轴字幕编号无效')
                if row['sentence_id'] is not None:
                    if row['sentence_id'] in target_ids:
                        raise ValueError('多条识别结果同时指向同一句主轴字幕')
                    target_ids.add(row['sentence_id'])
                if (row['sentence_id'] is None) != (row['base_signature'] is None):
                    raise ValueError('识别结果的基准记录无效')
                if row['base_signature'] is not None and (not _text(row['base_signature'],64) or len(row['base_signature']) != 64):
                    raise ValueError('识别结果的基准校验信息无效')
                if not isinstance(row['text'],str) or len(row['text']) > 100000 or row['status'] not in ('ready','missing','new'):
                    raise ValueError('识别结果的文字或状态无效')
                validate_boundaries(row['start'], row['end'])
                if type(row['confidence']) not in (float,int) or not math.isfinite(row['confidence']) or not 0 <= row['confidence'] <= 1:
                    raise ValueError('识别结果的置信度数值无效')
                if not isinstance(row['reasons'],list) or len(row['reasons']) > 100 or any(not isinstance(x,str) or len(x)>4000 for x in row['reasons']):
                    raise ValueError('识别结果的说明记录无效')
                refs = row['provenance']
                if not isinstance(refs,dict) or set(refs) != {'lyric_ids','visual_ids','translation_ids'}:
                    raise ValueError('识别结果的关联记录无效')
                if any(not isinstance(ids,list) or len(ids)>1000 or any(not _text(i) for i in ids) for ids in refs.values()):
                    raise ValueError('识别结果关联的记录编号无效')
                for field, table in (('lyric_ids','lyrics'),('translation_ids','translations'),('visual_ids','visuals')):
                    if any(identity not in evidence_ids[table] for identity in refs[field]):
                        raise ValueError('识别结果关联的原始记录不存在')
        from .project_document import _without_secrets
        safe = deepcopy(value)
        for record in safe:
            record['evidence'] = _without_secrets(record['evidence'])
        return safe, ''
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError, OverflowError) as exc:
        return [], '已忽略损坏的识别结果：' + str(exc)


def candidate_project(record, base_project=None):
    """Detached evidence reconstruction for rematching, never a live timeline.

    Source settings are taken only from the caller's detached base_project.
    No provider settings or credentials are stored in candidate metadata.
    """
    safe, warning = read_candidates([record])
    if warning:
        raise ValueError(warning)
    record = safe[0]
    project = Project.from_dict(deepcopy(base_project.to_dict())) if base_project is not None else Project()
    evidence = Project.from_dict({'version':2, **deepcopy(record['evidence'])})
    for name in ('lyrics','translations','visuals'):
        setattr(project,name,getattr(evidence,name))
    project.timebase = deepcopy(record['provenance']['clock']['timebase'])
    project.replace_timeline([Mapping(row['id'], list(row['provenance']['translation_ids']),
                                lyric_ids=list(row['provenance']['lyric_ids']),
                                visual_ids=list(row['provenance']['visual_ids']),
                                start=row['start'],end=row['end'],confidence=row['confidence'],
                                reasons=list(row['reasons']),text_override=row['text'],
                                text_role=row.get('text_role', 'translation'),alignment_tokens=deepcopy(row.get('alignment_tokens',[])))
                        for row in record['rows']])
    project.settings.pop('pending_text_splits',None)
    project.settings.pop(DECISIONS,None)
    if record['source'] == 'ocr':
        from .roi_attribution import inherit
        inherit(project, project.mappings)
    return project.validate_timeline()


def install_candidate(document, record):
    safe, warning = read_candidates([record])
    if warning:
        raise ValueError(warning)
    record = safe[0]
    if record['document_id'] != document.id:
        raise ValueError('该识别结果属于其他工程，无法应用。')
    if any(item['id'] == record['id'] for item in document.source_candidates):
        return False
    updated = deepcopy(document.source_candidates) + [record]
    if len(updated)>MAX_RECORDS:
        raise ValueError('历史识别结果已达 12 份上限，请先清理旧记录后再试。')
    safe, warning = read_candidates(updated)
    if warning:
        raise ValueError(warning)
    document.source_candidates = safe
    document.source_candidate_warning = ''
    document.touch()
    return True


def remove_candidates(document, ids):
    """Explicitly confirmed metadata deletion; never alter main/history.

    The caller must disclose that deletion is not undoable. Tiny review decision
    keys remain because ordinary main History snapshots may still reference them.
    Applied text/times/evidence are owned by main and are not removed here.
    """
    ids = list(ids)
    if len(ids) != len(set(ids)) or any(not isinstance(i,str) for i in ids):
        raise ValueError('待删除的记录列表无效。')
    known = {record['id'] for record in document.source_candidates}
    if any(identity not in known for identity in ids):
        raise ValueError('待删除的识别记录已变动，请刷新后重试。')
    if not ids:
        return 0
    removed = set(ids)
    document.source_candidates = [deepcopy(record) for record in document.source_candidates
                                  if record['id'] not in removed]
    document.touch()
    return len(ids)


def _record(document, identity):
    record = next((r for r in document.source_candidates if r['id'] == identity), None)
    if record is None:
        raise ValueError('所选识别结果已不存在，请重新选择。')
    return record


def _decision(record_id, row_id):
    return record_id + ':' + row_id


def view_rows(document, source, *, record=None):
    """Pure display projection, including unbound candidates and missing rows."""
    index = _project_index(document.project)
    if source == 'main':
        return [{'id': row.id, 'sentence_id': row.id, 'text': mapping_text(document.project,row,index['texts']),
                 'start': row.start, 'end': row.end, 'status': 'main'} for row in document.main_timeline]
    if source not in SOURCES:
        raise ValueError('未知的识别结果类型。')
    if record is None:
        record = next((r for r in reversed(document.source_candidates) if r['source']==source), None)
    by_target = {r['sentence_id']:r for r in record['rows'] if r['sentence_id']} if record else {}
    result = []
    decisions = document.project.settings.get(DECISIONS,{})
    live_ids = {r.id for r in document.main_timeline}
    for main in document.main_timeline:
        row = deepcopy(by_target.get(main.id))
        if row is None:
            row = {'id': main.id, 'sentence_id': main.id, 'text': mapping_text(document.project,main,index['texts']),
                   'start': None, 'end': None, 'status': 'missing'}
        else:
            row['ignored'] = decisions.get(_decision(record['id'],row['id']),False)
            row['stale'] = row['base_signature'] != _fingerprint(document.project,main,index)
        row['candidate_id'] = record['id'] if record else None
        result.append(row)
    if record:
        for original in record['rows']:
            if original['sentence_id'] not in live_ids:
                row = deepcopy(original)
                row.update(candidate_id=record['id'], ignored=decisions.get(_decision(record['id'],row['id']),False))
                if original['sentence_id'] is not None:
                    row['status'] = 'orphaned'
                result.append(row)
    return result


def can_regroup_imported_seed(document, record, *, allow_visual_extras=False):
    """Exact whole-translation partition only, never guessed text correspondence.

    Session authority is checked by EntryInitialization. This additional data
    guard also runs when a signed plan is applied, so changed seeds fail closed.
    """
    from collections import Counter
    seeds = document.main_timeline
    if record['source'] != 'ocr' or not seeds or not record['rows']:
        return False
    if document.project.settings.get('manual_locked_ids') or document.project.settings.get('pending_text_splits'):
        return False
    if any(row.method != 'import' or row.human_edited or row.locked or row.confirmed or row.ignored
           or row.start is not None or row.end is not None or row.text_override is not None
           or row.text_segments or row.text_role != 'translation' or row.lineage_ids
           or row.lyric_ids or row.visual_ids or row.reasons
           or row.speaker_name or row.speaker_id or row.speaker_style or row.style_id or row.style_overrides
           or not row.translation_ids for row in seeds):
        return False
    source = {t.id: asdict(t) for t in document.project.translations}
    evidence = {t['id']: t for t in record['evidence']['translations']}
    before = Counter(i for row in seeds for i in row.translation_ids)
    after = Counter(i for row in record['rows'] for i in row['provenance']['translation_ids'])
    if before != after or any(count != 1 for count in before.values()):
        return False
    if any(i not in source or evidence.get(i) != source[i] for i in before):
        return False
    if allow_visual_extras:
        if (set(before) != set(source) or set(source) != set(evidence)
                or len(source) != len(document.project.translations)
                or len(evidence) != len(record['evidence']['translations'])):
            return False
        # The one-step offer is for occasional unpaired visuals. Larger mismatches
        # stay available in the normal per-row comparison and pairing workbench.
        extra_count = sum(not r['provenance']['translation_ids'] for r in record['rows'])
        if extra_count > max(1, min(3, len(before) // 10)):
            return False
    visuals = {v['id']: v['text'] for v in record['evidence']['visuals']}
    for row in record['rows']:
        refs = row['provenance']['translation_ids']
        if allow_visual_extras and not refs:
            visual_ids = row['provenance']['visual_ids']
            if (not visual_ids or not all(visuals.get(i, '').strip() for i in visual_ids)
                    or row['start'] is None or row['end'] is None or row['end'] <= row['start']
                    or row['text'].strip() and row['text'] != '\n'.join(visuals[i] for i in visual_ids)):
                return False
            continue
        if (not refs or row.get('text_role', 'translation') != 'translation' or
                row['text'] != '\n'.join(source[i]['text'] for i in refs)):
            return False
        # A seed may merge as a whole, never be silently split across candidates.
        if any(set(seed.translation_ids) & set(refs) and not set(seed.translation_ids) <= set(refs)
               for seed in seeds):
            return False
    return True


def prepare_apply(document, candidate_id, sentence_ids, *, frame_index=None,
                  bindings=None, add_unmapped=False, apply_text=False, current_provenance=None,
                  cancel=lambda: False, time_overrides=None, initial_ocr_text=False, initial_asr_draft=False, initial_missing_time=False, initial_seed_regroup=False, explicit_visual_regroup=False):
    """Worker-safe preview; blocked rows stay explicit. No live mutations.

    sentence_ids identifies candidate row IDs. Explicit bindings may map an
    unbound row to an existing main sentence. add_unmapped opts into new cues.
    """
    if any(type(value) is not bool for value in (apply_text, add_unmapped, initial_ocr_text, initial_asr_draft, initial_missing_time, initial_seed_regroup, explicit_visual_regroup)):
        raise ValueError('应用选项设置无效。')
    record = _record(document, candidate_id)
    if initial_ocr_text and (record['source'] != 'ocr' or document.main_timeline or
                            document.project.translations or not add_unmapped):
        raise ValueError('仅空白主时间轴支持直接填入 OCR 识别文字。')
    if initial_asr_draft and (record['source'] != 'vocal' or document.main_timeline or
                              document.project.translations or not add_unmapped or
                              not record['rows'] or any(row.get('text_role') != 'source_asr' for row in record['rows'])):
        raise ValueError('只有全新空主轴可承接已确认的识别原文草稿。')
    visual_texts = {row['id']: row['text'] for row in record['evidence']['visuals']}
    bindings = dict(bindings or {})
    if initial_seed_regroup or explicit_visual_regroup:
        if not can_regroup_imported_seed(document, record, allow_visual_extras=explicit_visual_regroup):
            raise ValueError('导入种子已改变或译文分组不完整，未替换主轴。')
        if set(sentence_ids) != {row['id'] for row in record['rows']}:
            raise ValueError('初始分组必须完整保留所有译文。')
        bindings = {row['id']: None for row in record['rows']}
        add_unmapped = True
    ids = list(sentence_ids)
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('请先勾选要应用的识别结果。')
    rows = {row['id']:row for row in record['rows']}
    if any(identity not in rows for identity in ids) or any(k not in ids for k in bindings):
        raise ValueError('所选识别结果或关联的目标句无效。')
    time_overrides = dict(time_overrides or {})
    if any(identity not in ids for identity in time_overrides):
        raise ValueError('微调时间含未选择的候选句。')
    for bounds in time_overrides.values():
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            raise ValueError('微调时间格式无效。')
        validate_boundaries(*bounds, duration=getattr(frame_index, 'duration', None))
    # A failed worker verification keeps every preview entry blocked. The
    # fallback token is never fresh evidence; apply will reject its blocked plan.
    token = deepcopy(record['provenance'])
    source_error = ''
    try:
        token = _verify_record_provenance(document, record, frame_index, current_provenance, cancel)
        _check_token(document, token, frame_index)
        if _source_signature(token) != record['source_signature']:
            raise ValueError('素材文件或视频帧索引已变动，请重新识别。')
        if record['document_id'] != document.id:
            raise ValueError('该识别结果属于其他工程，无法应用。')
    except ValueError as exc:
        source_error = str(exc)
    index = _project_index(document.project)
    main = {row.id:row for row in document.main_timeline}
    decisions = document.project.settings.get(DECISIONS,{})
    entries = []
    targets = set()
    adopted = {reason for item in main.values() for reason in item.reasons
               if isinstance(reason,str) and reason.startswith('candidate:' + candidate_id + ':')}
    for identity in ids:
        if cancel():
            raise InterruptedError('已取消对比预览。')
        row = dict(rows[identity])
        if identity in time_overrides:row['start'], row['end'] = time_overrides[identity]
        candidate_text = row['text']
        candidate_role = row.get('text_role', 'translation')
        if explicit_visual_regroup and not row['provenance']['translation_ids']:
            candidate_text, candidate_role = '', 'translation'
        if initial_ocr_text and not candidate_text.strip():
            refs = row['provenance']['visual_ids']
            candidate_text = '\n'.join(visual_texts[i] for i in refs)
            candidate_role = 'source_ocr'
        target = bindings.get(identity, row['sentence_id'])
        reason = source_error
        if initial_ocr_text and not candidate_text.strip():
            reason = reason or '缺少对应的 OCR 识别文字，未生成空白主轴。'
        existing = main.get(target)
        if existing is None and candidate_role != 'translation' and not candidate_text.strip():
            reason = reason or '识别结果文字为空，无法作为新原文句加入主轴。'
        if decisions.get(_decision(candidate_id,identity)):
            reason = reason or '本句已被忽略，如需应用请先恢复。'
        if target is None and not add_unmapped:
            reason = reason or '请先关联主轴现有句子，或勾选作为新句加入。'
        if target is None and 'candidate:' + candidate_id + ':' + identity in adopted:
            reason = reason or '本句已加入主轴；请直接回主轴编辑，或关联其他句子。'
        if target is not None and existing is None:
            reason = reason or '原对应句已被删除或拆分，请重新选择关联句子。'
        if existing is not None:
            if apply_text and candidate_role != existing.text_role:
                reason = reason or '原文与译文类型不同，无法直接替换文字，请仅应用时间。'
            if _independent_edge_locked(document.project,existing):
                reason = reason or '目标句含独立人工锁定关联，请先在配对工作台解除锁定。'
            if existing.id in document.project.settings.get('manual_locked_ids',[]):
                reason = reason or '目标句已锁定，请先解除锁定。'
            if identity not in bindings and row['base_signature'] != _fingerprint(document.project,existing,index):
                reason = reason or '主轴对应句已被修改，请重新手动指定关联后再应用。'
            if target in targets:
                reason = reason or '多条结果指向同一主轴句，无法同时应用。'
            targets.add(target)
        try:
            if (initial_asr_draft or (initial_missing_time and (existing is None or (existing.start is None and existing.end is None)))) and row['start'] is None and row['end'] is None:
                pass  # Preserve unknown time; never synthesize or drop text.
            else:
                if row['start'] is None or row['end'] is None:
                    raise ValueError('该条识别结果缺少起止时间，无法应用。')
                validate_boundaries(row['start'],row['end'],duration=getattr(frame_index,'duration',None))
        except ValueError as exc:
            reason = reason or '起止时间不完整、无效或超出音视频时长。'
        entries.append({'id': identity, 'target_id': target, 'new_id': 'cue-'+uuid.uuid4().hex if target is None else None,
                        'before': None if existing is None else {'text':mapping_text(document.project,existing,index['texts']),'start':existing.start,'end':existing.end},
                        'after': {'text': candidate_text if existing is None or apply_text else mapping_text(document.project,existing,index['texts']),
                                  'text_role': candidate_role if existing is None or apply_text else existing.text_role,
                                  'start':row['start'],'end':row['end']},
                        'target_signature': _fingerprint(document.project,existing,index) if existing is not None else None,
                        'blocked': reason})
    plan = {'document_id':document.id,'candidate_id':candidate_id,'candidate_signature':_digest(record),
            'apply_text':apply_text,
            'provenance':token,'entries':entries}
    if explicit_visual_regroup:
        plan['explicit_visual_regroup'] = True
    if initial_seed_regroup or explicit_visual_regroup:
        plan['replace_seed_rows'] = {row.id: _fingerprint(document.project, row, index)
                                     for row in document.main_timeline}
    plan['signature'] = _digest(plan)
    return plan


def _merge_indexes(project, record):
    return {table:({item.id:item for item in getattr(project,table)},
                   {item['id']:item for item in record['evidence'][table]})
            for table in ('translations','lyrics','visuals')}


def _merge_evidence(project, record, row, indexes=None):
    """Copy only referenced records; conflicting IDs get stable scoped aliases."""
    from .model import Lyric, Translation, Visual
    result = {}
    if indexes is None:
        indexes = _merge_indexes(project,record)
    for field, table, kind in (('lyric_ids','lyrics',Lyric),
                               ('translation_ids','translations',Translation),
                               ('visual_ids','visuals',Visual)):
        existing,source = indexes[table]
        identities = []
        for identity in row['provenance'][field]:
            data = deepcopy(source[identity])
            if identity in existing and asdict(existing[identity]) != data:
                alias = 'evidence-' + _digest([record['id'],table,identity])[:32]
                data['id'] = alias
                if alias in existing and asdict(existing[alias]) != data:
                    raise ValueError('识别记录编号冲突，未能应用到主轴。')
            else:
                alias = identity
            if alias not in existing:
                item = kind(**data)
                getattr(project,table).append(item)
                existing[alias] = item
            identities.append(alias)
        result[field] = identities
    return result


def pairing_material(document, candidate_id, frame_index, cancel=lambda: False):
    """Add this run's observations to a detached pairing desk without amending main."""
    record = _record(document, candidate_id)
    token = verify_candidate_source(document, record, frame_index, cancel=cancel)
    draft = deepcopy(document.project)
    indexes = _merge_indexes(draft, record)
    refs = {'lyric_ids': [], 'translation_ids': [],
            'visual_ids': [v['id'] for v in record['evidence']['visuals']]}
    _merge_evidence(draft, record, {'provenance': refs}, indexes)
    return draft, token


def check_pairing_source(document, candidate_id, token, frame_index):
    record = _record(document, candidate_id)
    _check_token(document, token, frame_index)
    if _source_signature(token) != record['source_signature']:
        raise ValueError('识别素材已变更，请重新打开配对。')


def apply_plan(document, history, plan, *, frame_index=None, current_provenance=None):
    """Atomic single undo command. Any blocked/changed row rejects the batch."""
    body = deepcopy(plan)
    signature = body.pop('signature',None)
    if signature != _digest(body) or body['document_id'] != document.id:
        raise ValueError('对比预览已失效，请重新打开对比与应用。')
    record = _record(document,body['candidate_id'])
    if _digest(record) != body['candidate_signature']:
        raise ValueError('识别结果已更新，请重新预览后再应用。')
    token = current_provenance if current_provenance is not None else body['provenance']
    _check_token(document,token,frame_index)
    if _source_signature(token) != record['source_signature']:
        raise ValueError('关联素材已变动，请重新校验后再应用。')
    index = _project_index(document.project)
    main = {row.id:row for row in document.main_timeline}
    decisions = document.project.settings.get(DECISIONS,{})
    adopted = {reason for item in main.values() for reason in item.reasons
               if isinstance(reason,str) and reason.startswith('candidate:' + record['id'] + ':')}
    for entry in body['entries']:
        if entry['blocked']:
            raise ValueError(entry['blocked'])
        if decisions.get(_decision(record['id'],entry['id'])):
            raise ValueError('所选条目已被标记为忽略，请重新预览。')
        if entry['target_id'] is not None:
            target = main.get(entry['target_id'])
            if target is None or _fingerprint(document.project,target,index) != entry['target_signature']:
                raise ValueError('主轴目标句已被修改，请重新对比后再应用。')
            if _independent_edge_locked(document.project,target):
                raise ValueError('目标句含独立人工锁定关联，请先解除锁定。')
            if target.id in document.project.settings.get('manual_locked_ids',[]):
                raise ValueError('目标句已锁定，请先解除锁定。')
        elif (entry['new_id'] in main or
              'candidate:' + record['id'] + ':' + entry['id'] in adopted):
            raise ValueError('该条识别结果已添加到主轴，请勿重复添加。')
    seed_rows = body.get('replace_seed_rows')
    if seed_rows is not None:
        if (not can_regroup_imported_seed(document, record, allow_visual_extras=body.get('explicit_visual_regroup') is True) or
                seed_rows != {row.id: _fingerprint(document.project, row, index) for row in document.main_timeline} or
                {entry['id'] for entry in body['entries']} != {row['id'] for row in record['rows']} or
                any(entry['target_id'] is not None for entry in body['entries'])):
            raise ValueError('初始分组已过期或不完整，未替换主轴。')
    draft = Project.from_dict(deepcopy(document.project.to_dict()))
    if seed_rows is not None:
        draft.replace_timeline([])
    draft_rows = {row.id:row for row in draft.mappings}
    candidate_rows = {row['id']:row for row in record['rows']}
    merge_indexes = _merge_indexes(draft,record)
    region_targets = []
    changed_ids = []
    for entry in body['entries']:
        target = draft_rows.get(entry['target_id'])
        if target is None:
            target = Mapping(entry['new_id'], [], text_override=entry['after']['text'])
            draft.mappings.append(target)
        if record['source'] == 'ocr' and not target.human_edited and not target.locked and not target.confirmed:
            region_targets.append(target)
        prior_text = mapping_text(draft,target)
        prior_role = target.text_role
        preserved_edges={name:deepcopy(getattr(draft.alignment_graph,name)[target.id]) for name in ('visual_source_edges','source_translation_edges')}
        if body['apply_text']:
            target.text_override = entry['after']['text']
        target.start,target.end = entry['after']['start'],entry['after']['end']
        candidate_row = candidate_rows[entry['id']]
        if record['source']=='vocal':target.alignment_tokens=deepcopy(candidate_row.get('alignment_tokens',[]))
        if seed_rows is not None:
            refs = set(candidate_row['provenance']['translation_ids'])
            target.lineage_ids = [seed.id for seed in main.values() if set(seed.translation_ids) <= refs]
        imported = _merge_evidence(draft,record,candidate_row,merge_indexes)
        # Keep immutable imported source references and add candidate provenance.
        for field, identities in imported.items():
            setattr(target,field,list(dict.fromkeys(getattr(target,field)+identities)))
        # Adding translation references must not change visible imported text.
        target.text_override = entry['after']['text']
        target.text_role = entry['after'].get('text_role', 'translation')
        if (record['source'] == 'ocr' and prior_role != 'translation' and
                target.alignment_tokens and prior_text != target.text_override):
            from .alignment_token_editing import unknown
            target.alignment_tokens = unknown(target.alignment_tokens)
            target.reasons.append('原词已编辑，保留逐字声学证据但需重新确认字符归属')
        note = 'candidate:' + record['id'] + ':' + entry['id']
        if note not in target.reasons:
            target.reasons.append(note)
        if seed_rows is not None and not candidate_row['provenance']['translation_ids']:
            target.reasons.append('待补译')
        target.method = 'candidate_' + record['source']
        target.confirmed = False
        target.human_edited = (target.human_edited or body['apply_text'] or
                               (target.start,target.end)!=(candidate_row['start'],candidate_row['end']))
        if record['source']=='vocal':target.confidence=candidate_row['confidence']
        for name,old_edge in preserved_edges.items():
            edge=getattr(draft.alignment_graph,name)[target.id]
            refs=('visual_ids','lyric_ids') if name=='visual_source_edges' else ('translation_ids',)
            if all(edge[key]==old_edge[key] for key in refs):
                for key in ('state','confidence','reasons'):edge[key]=deepcopy(old_edge[key])
        changed_ids.append(target.id)
    if region_targets:
        from .roi_attribution import inherit
        inherit(draft, region_targets, newly_applied=True)
    checked = document.validate_project(draft)
    if checked.to_dict() == document.project.to_dict():
        return []
    if history.group is not None or history._transaction_active:
        raise RuntimeError('请先完成当前编辑操作，再应用识别结果。')
    history.push(document.project,'应用识别结果')
    document.project.__dict__.clear()
    document.project.__dict__.update(checked.__dict__)
    document.touch()
    return changed_ids


def set_ignored(document, history, candidate_id, sentence_ids, ignored=True):
    if type(ignored) is not bool:
        raise ValueError('忽略选项无效。')
    record = _record(document,candidate_id)
    ids = list(sentence_ids)
    known = {row['id'] for row in record['rows']}
    if not ids or len(ids) != len(set(ids)) or any(i not in known for i in ids):
        raise ValueError('所选识别结果不存在或重复。')
    draft = Project.from_dict(deepcopy(document.project.to_dict()))
    decisions = draft.settings.setdefault(DECISIONS,{})
    for identity in ids:
        key = _decision(candidate_id,identity)
        if ignored:
            decisions[key] = True
        else:
            decisions.pop(key,None)
    if not decisions:
        draft.settings.pop(DECISIONS,None)
    checked = document.validate_project(draft)
    if checked.to_dict() == document.project.to_dict():
        return False
    if history.group is not None or history._transaction_active:
        raise RuntimeError('请先完成当前编辑操作，再处理识别结果。')
    history.push(document.project,'忽略识别结果' if ignored else '恢复识别结果')
    document.project.__dict__.clear()
    document.project.__dict__.update(checked.__dict__)
    document.touch()
    return True
