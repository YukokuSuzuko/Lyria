"""Versioned, bounded AI candidate metadata outside the editable Project.

Only a normalized response and its provenance are saved. No provider settings,
request body, credentials, or derived editable candidate project are persisted.
"""
from copy import deepcopy
import json
import math

from .api_alignment import make_plan, revision_signature, validate_plan
from .storage import Cache

VERSION = 1
MAX_BYTES = 256 * 1024
_FIELDS = {'version', 'plan_id', 'base_signature', 'frame_signature', 'mapping_ids', 'response'}


def frame_signature(index):
    if index is None:
        raise ValueError('请等待视频帧索引加载完成后，再查看 AI 建议。')
    return Cache.key('alignment-frames-v1', {'stamps': list(index.stamps), 'duration': index.duration})


def read_candidate(value):
    """Reject optional corrupt metadata without failing the original document."""
    if value is None:
        return None, ''
    try:
        if not isinstance(value, dict) or set(value) not in (_FIELDS, _FIELDS | {'coverage'}):
            raise ValueError('AI 建议数据已损坏')
        if type(value['version']) is not int or value['version'] not in (VERSION, 2):
            raise ValueError('不支持该版本的 AI 建议数据')
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')) > MAX_BYTES:
            raise ValueError('AI 建议数据体积超出上限')
        for name in ('plan_id', 'base_signature', 'frame_signature'):
            if not isinstance(value[name], str) or not 1 <= len(value[name]) <= 160:
                raise ValueError('AI 建议校验信息无效')
        ids = value['mapping_ids']
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 250 or
                any(not isinstance(i, str) or not 1 <= len(i) <= 256 for i in ids) or len(ids) != len(set(ids))):
            raise ValueError('AI 建议的字幕范围无效')
        response = value['response']
        if not isinstance(response, dict) or set(response) != {'operations', 'semantic_issues'}:
            raise ValueError('AI 建议的数据格式无效')
        # Do not retain arbitrary provider metadata or request/secret fields.
        shapes = {'operations': {'kind', 'mapping_ids', 'parts', 'confidence', 'reason', 'reference_meaning'},
                  'semantic_issues': {'mapping_ids', 'lyric_ids', 'visual_ids', 'kind', 'confidence', 'reason', 'reference_meaning'}}
        for name, fields in shapes.items():
            rows = response[name]
            if not isinstance(rows, list) or len(rows) > 64:
                raise ValueError('AI 建议条数超出范围')
            for row in rows:
                if not isinstance(row, dict) or set(row) != fields:
                    raise ValueError('AI 建议包含无法识别的字段')
                confidence=row['confidence']
                if type(confidence) not in (float,int) or not math.isfinite(confidence) or not 0<=confidence<=1:
                    raise ValueError('AI 建议的置信度数值无效')
                for key,limit in (('reason',1200),('reference_meaning',2000),('kind',32)):
                    if not isinstance(row[key],str) or len(row[key])>limit:
                        raise ValueError('AI 建议的说明内容无效')
                id_lists=[row['mapping_ids']]
                if name=='semantic_issues':id_lists.extend([row['lyric_ids'],row['visual_ids']])
                for values in id_lists:
                    if not isinstance(values,list) or len(values)>250 or any(not isinstance(i,str) or len(i)>256 for i in values):
                        raise ValueError('AI 建议关联的字幕编号无效')
                if name == 'operations':
                    if not isinstance(row['parts'], list) or not 1 <= len(row['parts']) <= 16:
                        raise ValueError('AI 拆合建议的片段数量无效')
                    for part in row['parts']:
                        if not isinstance(part, dict) or set(part) != {'spans', 'lyric_ids', 'visual_ids'}:
                            raise ValueError('AI 拆合建议的片段格式无效')
                        if not isinstance(part['spans'], list) or not 1 <= len(part['spans']) <= 16:
                            raise ValueError('AI 拆合建议的文字区间无效')
                        for key in ('lyric_ids','visual_ids'):
                            values=part[key]
                            if not isinstance(values,list) or len(values)>250 or any(not isinstance(i,str) or len(i)>256 for i in values):
                                raise ValueError('AI 建议关联的原文或识别记录无效')
                        for span in part['spans']:
                            if not isinstance(span, dict) or set(span) != {'mapping_id', 'start', 'end'}:
                                raise ValueError('AI 建议的文字区间格式无效')
                            if (not isinstance(span['mapping_id'],str) or len(span['mapping_id'])>256
                                    or type(span['start']) is not int or type(span['end']) is not int):
                                raise ValueError('AI 建议的文字起止位置无效')
        if value['version'] == 2:
            from .api_alignment_batches import validate_coverage
            validate_coverage(value.get('coverage'), ids)
        elif 'coverage' in value:
            raise ValueError('旧版 AI 建议数据格式不兼容')
        return deepcopy(value), ''
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        return None, '已忽略失效的 AI 建议：' + str(exc) + '，不影响当前工程。'


def candidate_record(project, index, plan):
    validate_plan(project, plan, index)
    value = {'version': VERSION, 'plan_id': plan['id'], 'base_signature': plan['revision'],
             'frame_signature': frame_signature(index), 'mapping_ids': deepcopy(plan['mapping_ids']),
             'response': deepcopy(plan['response'])}
    if 'coverage' in plan:
        value.update(version=2, coverage=deepcopy(plan['coverage']))
    safe, warning = read_candidate(value)
    if warning:
        raise ValueError(warning)
    return safe


def restore_candidate(project, index, value):
    safe, warning = read_candidate(value)
    if warning or safe is None:
        raise ValueError(warning or '当前没有待确认的 AI 建议。')
    if safe['frame_signature'] != frame_signature(index):
        raise ValueError('视频帧索引已变化，当前 AI 建议已失效，请重新获取。')
    if safe['base_signature'] != revision_signature(project, index):
        raise ValueError('字幕或素材内容已变动，当前 AI 建议已失效，请重新获取。')
    plan = make_plan(project, index, safe['response'], safe['mapping_ids'], coverage=safe.get('coverage'))
    if plan['id'] != safe['plan_id']:
        raise ValueError('AI 建议校验未通过，请重新获取。')
    return plan
