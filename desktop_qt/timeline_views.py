"""Explicit timeline copies; ordinary cue edits never create another timeline."""
from copy import deepcopy
import uuid
from lyricsync.model import Project, valid_time

KEY = 'timeline_archives'
CANDIDATE_HISTORY = 'historical_vocal_candidate_ids'


def current_candidate(document, record):
    return ((record.get('source') != 'ocr' or any(valid_time(r.get('start'), r.get('end')) for r in record.get('rows', [])))
            and record.get('id') not in document.project.settings.get('historical_asset_candidate_ids', [])
            and (record.get('source') != 'vocal' or record.get('id') not in document.project.settings.get(CANDIDATE_HISTORY, [])))


def isolate_vocal_candidates(document):
    # Caller owns undo; retain every original result/evidence unchanged.
    old = list(document.project.settings.get(CANDIDATE_HISTORY, []))
    ids = old + [r['id'] for r in document.source_candidates if r['source'] == 'vocal' and r['id'] not in old]
    if ids != old:
        document.project.settings[CANDIDATE_HISTORY] = ids
        return True
    return False



def archives(project):
    records = project.settings.get(KEY, [])
    if not isinstance(records, list):
        return []
    return [r for r in records if isinstance(r, dict) and isinstance(r.get('id'), str)
            and isinstance(r.get('project'), dict)]


def archive_main(project, label):
    """Caller owns a history transaction; preserve the whole editable dataset."""
    records = deepcopy(archives(project))
    if len(records) >= 8:
        raise ValueError('已有 8 套保留时间轴，请新建工程后再添加；现有归档完整保留，另存副本不会释放容量。现有时间轴未改动。')
    snapshot = project.to_dict()
    snapshot['settings'].pop(KEY, None)
    # No recursive copies. A view is data, not a replacement of the live project.
    record = {'id': uuid.uuid4().hex, 'label': str(label), 'project': snapshot}
    records.append(record)
    project.settings[KEY] = records
    return record


def archived_project(project, identity):
    record = next((r for r in archives(project) if r['id'] == identity), None)
    if record is None:
        raise ValueError('这套时间轴已不存在。')
    return Project.from_dict(deepcopy(record['project']))


def timeline_choices(document):
    p = document.project
    choices = [{'id': 'main', 'label': '主时间轴（可编辑）'}]
    records = archives(p)
    choices.extend({'id': 'saved:' + r['id'], 'label': r.get('label', '保留时间轴') + '（只读）'} for r in records)
    # An applied result and its editable main are one timeline, including later
    # single-cue edits. A fresh, unapplied candidate is independent even when it
    # is the first run in an older hand-edited project.
    explicit = bool(records) or p.settings.get('main_timeline_kind') in ('manual-copy', 'timed-import')
    by_source = {}
    for record in document.source_candidates:
        if not current_candidate(document, record):continue
        if any(valid_time(row.get('start'), row.get('end')) for row in record.get('rows', [])):
            by_source.setdefault(record['source'], []).append(record)
    adopted={reason for row in p.mappings for reason in row.reasons if isinstance(reason,str) and reason.startswith('candidate:')}
    for source in ('ocr','vocal'):
        if source not in by_source:continue
        record=by_source[source][-1]
        unapplied=any(f"candidate:{record['id']}:{row['id']}" not in adopted
                      for row in record['rows'] if valid_time(row.get('start'),row.get('end')))
        if explicit or len(by_source)>1 or unapplied:
            choices.append({'id':source,'label':('OCR 结果' if source=='ocr' else '自动打轴结果')+'（只读）'})
    return choices
