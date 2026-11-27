"""Detach media-owned OCR evidence without deleting editable subtitle content.

Caller archives and owns Undo before invoking. No files or candidates are deleted.
"""
from lyricsync.editing import mapping_text


def has_evidence(project):
    return bool(project.mappings or project.visuals or project.lyrics or project.inputs.get('ktv') or project.settings.get('local_alignment_source') or project.settings.get('ocr_regions')
                or project.settings.get('translation_capsules_v022')
                or any(l.id.startswith('ocr-source-') for l in project.lyrics))


def check_archive_capacity(project):
    from .timeline_views import archives
    if has_evidence(project) and len(archives(project)) >= 8:
        raise ValueError('已有 8 套历史快照，未更换或清除素材。请保留当前工程并新建工程继续；另存副本不会释放归档容量。')


def archive_evidence(project, label):
    from .timeline_views import archive_main
    if has_evidence(project):
        archive_main(project, label)


def detach_ocr_evidence(project):
    pseudo={l.id for l in project.lyrics if l.id.startswith('ocr-source-')}
    for row in project.mappings:
        if row.text_override is None and not row.translation_ids and (row.visual_ids or pseudo.intersection(row.lyric_ids)):
            row.text_override=mapping_text(project,row)
        row.visual_ids=[]
        row.lyric_ids=[identity for identity in row.lyric_ids if identity not in pseudo]
    project.visuals=[]
    project.lyrics=[l for l in project.lyrics if l.id not in pseudo]
    project.settings['roi']=None
    for key in ('ocr_multi_region','ocr_regions','translation_capsules_v022','cue_rescans'):
        project.settings.pop(key,None)
