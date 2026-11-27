"""Explicit conflict rebuild: immutable evidence, full old-axis archive, one undo."""
from copy import deepcopy
from lyricsync.candidate_tracks import prepare_apply,apply_plan,_digest,_record,_check_token
from lyricsync.editing import History
from lyricsync.model import Project
from .timeline_views import archive_main


def prepare_rebuild(document,record_id,index,cancel=lambda:False):
    record=_record(document,record_id)
    if record['source']!='vocal' or not record['rows'] or any(r['sentence_id'] is not None for r in record['rows']):
        raise ValueError('只有未配对的演唱原词结果可建立新的原词主轴。')
    if any(r.get('text_role','translation')=='translation' for r in record['rows']):
        raise ValueError('候选不是演唱原词，未重建主轴。')
    if not any(r['start'] is not None and r['end'] is not None for r in record['rows']):
        raise ValueError('本次没有有效时间，未重建主轴。')
    signature=_digest(document.project.to_dict())
    draft=deepcopy(document)
    archive_main(draft.project,'建立演唱原词主轴前的完整时间轴')
    draft.project.replace_timeline([])
    plan=prepare_apply(draft,record_id,[r['id'] for r in record['rows']],frame_index=index,
                       add_unmapped=True,initial_missing_time=True,cancel=cancel)
    if any(x['blocked'] for x in plan['entries']):
        raise ValueError('无法安全建立主轴：'+'；'.join(dict.fromkeys(x['blocked'] for x in plan['entries'] if x['blocked'])))
    apply_plan(draft,History(),plan,frame_index=index)
    used={identity for row in draft.project.mappings for identity in row.translation_ids}
    draft.project.settings['pending_translation_ids']=[row.id for row in draft.project.translations if row.id not in used]
    checked=draft.validate_project(draft.project)
    result=dict(document_id=document.id,revision=document.revision,signature=signature,
                candidate_id=record_id,candidate_signature=_digest(record),provenance=plan['provenance'],project=checked.to_dict())
    result['plan_signature']=_digest(result)
    return result


def commit_rebuild(document,history,result,index):
    result=deepcopy(result);signature=result.pop('plan_signature',None)
    if signature!=_digest(result):raise ValueError('重建预览校验失败，主轴未改变。')
    if (result['document_id']!=document.id or result['revision']!=document.revision or
            result['signature']!=_digest(document.project.to_dict()) or
            result['candidate_signature']!=_digest(_record(document,result['candidate_id']))):
        raise ValueError('工程或识别结果已改变，未替换主轴，请重新预览。')
    _check_token(document,result['provenance'],index)
    checked=document.validate_project(Project.from_dict(deepcopy(result['project'])))
    if history.group is not None or history._transaction_active:raise ValueError('请先完成当前编辑。')
    history.push(document.project,'以演唱原词建立主轴并保留译文')
    document.project.__dict__.clear();document.project.__dict__.update(checked.__dict__);document.touch()
    return [row.id for row in document.main_timeline]
