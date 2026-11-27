from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import time

from .api import suggest
from .export import export_project
from .matching import match_project
from .storage import Cache, file_hash, save_project
from .subtitles import import_translation, import_original
from .vision import scan
from .model import Lyric, stable_id


AUTO_AI_AFTER_OCR_ENABLED = False  # F47: paused; keep provider implementation/configuration.


def run(project, project_path, progress=lambda p,m:None, cancel=lambda:False, api_key="", *, use_imported_pools=False):
    project_path=Path(project_path)
    from .ocr_correspondence import reference_lyrics
    pooled_source = reference_lyrics(project) if use_imported_pools else []
    pooled_translation = list(project.translations) if use_imported_pools else []
    video_only = not project.inputs.get("translation") and not any(t.text.strip() for t in pooled_translation)
    for key in (("video","translation") if project.inputs.get("translation") else ("video",)):
        if not project.inputs.get(key) or not Path(project.inputs[key]).is_file():
            raise ValueError("缺少输入文件："+key)
    if project.inputs.get('ktv') and not Path(project.inputs['ktv']).is_file():raise ValueError('演唱原文文件不存在，请重新选择路径或清空该项')
    protected=project.inputs.values()
    from .storage import protect_output
    protect_output(project_path,protected)
    cache_path=project_path.with_suffix(".cache.sqlite")
    protect_output(cache_path,protected)
    cache=Cache(cache_path)
    metrics={'import_seconds':0.0,'matching_seconds':0.0,'api_seconds':0.0,'save_seconds':0.0}
    project.settings['pipeline_metrics']=metrics
    ledger_ready=False
    def checkpoint():
        tick=time.monotonic()
        try:
            # This worker may use a different local OCR cache from the desktop's
            # API cache. Its ledger must first be hydrated below; never replace
            # a portable project's spent budget with an empty cache ledger.
            project.api_records=cache.records()
            save_project(project,project_path)
        finally:metrics['save_seconds']+=time.monotonic()-tick
    try:
        cache.restore_records(project.api_records)
        ledger_ready=True
        tick=time.monotonic()
        progress(0,"解析原文歌词与中文译文…")
        project.stage="import"
        old_lyrics={l.id:l for l in project.lyrics}
        old_translations={t.id:t for t in project.translations}
        if pooled_source:
            project.lyrics,import_warnings=pooled_source,[]
        elif project.inputs.get('ktv'):
            project.lyrics,import_warnings=import_original(project.inputs['ktv'],project.settings.get('original_txt_groups',False))
        else:project.lyrics,import_warnings=[],[]
        current_lyric_ids={l.id for l in project.lyrics}
        imported = pooled_translation or ([] if video_only else import_translation(project.inputs['translation'],project.settings.get('txt_groups',False)))
        current_translation_ids={t.id for t in imported}
        protected_rows=[m for m in project.mappings if project.alignment_graph.manual_locked(m.id) or m.locked or m.confirmed or m.human_edited or m.text_segments]
        retained={i for m in project.mappings if project.alignment_graph.manual_locked(m.id) or m.locked or m.confirmed or m.human_edited or m.text_segments for i in m.translation_ids}
        project.translations=imported+[t for i,t in old_translations.items() if i in retained and i not in {t.id for t in imported}]
        # Import checkpoints retain every entity still referenced by the old
        # projection. Temporary automatic evidence is removed only inside the
        # detached matching transaction below, never in a half-valid save.
        old_refs_l={i for m in project.mappings for i in m.lyric_ids}
        old_refs_t={i for m in project.mappings for i in m.translation_ids}
        project.lyrics.extend(l for i,l in old_lyrics.items() if i in old_refs_l and i not in {l.id for l in project.lyrics})
        project.translations.extend(t for i,t in old_translations.items() if i in old_refs_t and i not in {t.id for t in project.translations})
        project.warnings=import_warnings
        hashes={key:file_hash(project.inputs[key],cancel) for key in ('ktv','translation') if project.inputs.get(key)}
        old_hashes=project.settings.get("input_hashes")
        if old_hashes and old_hashes!=hashes:
            project.warnings.append('原文或译文文件已变更，原有编辑已保留，请重新核对句子对应关系')
            for mapping in project.mappings:
                if mapping.locked or mapping.human_edited:
                    mapping.confirmed=False
                    mapping.reasons=list(dict.fromkeys(mapping.reasons+['导入文件已变更，请重新核对本句']))
        project.settings["input_hashes"]=hashes
        metrics['import_seconds']=time.monotonic()-tick
        checkpoint()
        if cancel():
            raise InterruptedError("已取消导入")
        project.stage="vision"
        old_visuals={v.id:v for v in project.visuals}
        old_video_hash=project.video.get("content_hash")
        scanner = scan
        if project.settings.get('ocr_multi_region') is True:
            from .multi_roi import scan_regions
            scanner = scan_regions
        project.visuals,project.video=scanner(project.inputs["video"],project.settings,cache,
            [l.text for l in project.lyrics if l.id in current_lyric_ids or any(l.id in m.lyric_ids for m in protected_rows)],progress=lambda p,m:progress(.05+.83*p,m),cancel=cancel)
        from .ocr_observations import split_visual_lines
        project.visuals = split_visual_lines(project.visuals)
        if old_video_hash and old_video_hash!=project.video.get("content_hash"):
            project.warnings.append('视频文件已变更，原有手调时间已保留，请逐句复核进出点')
            for mapping in project.mappings:
                if mapping.locked or mapping.human_edited:
                    mapping.confirmed=False
                    mapping.reasons=list(dict.fromkeys(mapping.reasons+['视频文件已变更，请重新核对本句时间轴']))
        # Locked manual decisions retain their referenced evidence after rescan.
        current_visual_ids={v.id for v in project.visuals}
        protected_ids={i for m in project.mappings for i in m.visual_ids}
        for vid in protected_ids-{v.id for v in project.visuals}:
            if vid in old_visuals:
                project.visuals.append(old_visuals[vid])
        protected_lyrics={i for m in project.mappings if project.alignment_graph.manual_locked(m.id) or m.locked or m.confirmed or m.human_edited or m.text_segments for i in m.lyric_ids}
        project.lyrics.extend(l for i,l in old_lyrics.items() if i in protected_lyrics and i not in {l.id for l in project.lyrics})
        checkpoint()
        progress(.9,'正在自动匹配原文与译文…')
        project.stage="matching"
        tick=time.monotonic()
        from copy import deepcopy
        matching_view=deepcopy(project)
        keep_l=current_lyric_ids|{i for m in protected_rows for i in m.lyric_ids}
        keep_t=current_translation_ids|{i for m in protected_rows for i in m.translation_ids}
        keep_v=current_visual_ids|{i for m in protected_rows for i in m.visual_ids}
        matching_view.lyrics=[l for l in matching_view.lyrics if l.id in keep_l]
        matching_view.translations=[t for t in matching_view.translations if t.id in keep_t]
        matching_view.visuals=[v for v in matching_view.visuals if v.id in keep_v]
        if video_only:
            from .video_only import build_empty_translations
            build_empty_translations(matching_view)
        else:
            matching_view.settings['input_mode']='translated'
            from .ocr_correspondence import match_visual_project
            match_visual_project(matching_view)
        # Adopt one complete graph/entity set; intermediate import/scan
        # checkpoints above remain valid if matching fails or is cancelled.
        if matching_view.settings.get('ocr_multi_region'):
            from .roi_attribution import inherit, KEY
            inherit(matching_view, matching_view.mappings)
            if KEY in matching_view.settings:
                project.settings[KEY] = matching_view.settings[KEY]
        matching_view.validate_timeline()
        project.lyrics=matching_view.lyrics
        project.translations=matching_view.translations
        project.visuals=matching_view.visuals
        project.alignment_graph=matching_view.alignment_graph
        project.warnings=matching_view.warnings
        project.settings['input_mode']=matching_view.settings['input_mode']
        metrics['matching_seconds']=time.monotonic()-tick
        checkpoint()
        if AUTO_AI_AFTER_OCR_ENABLED and project.settings.get("api_enabled") and not video_only:
            tick=time.monotonic()
            try:
                progress(.94,'正在进行 AI 歌词配对核对…')
                suggest(project,cache,api_key,cancel,checkpoint=checkpoint)
            except InterruptedError:
                raise
            except Exception as exc:
                # API errors must not discard successful local processing.
                project.warnings.append('AI 辅助核对未全部完成，可在核对页面查看详情或重试')
            finally:metrics['api_seconds']=time.monotonic()-tick
        project.api_records=cache.records()
        # An empty OCR scan is a legitimate inconclusive result, not a ready
        # subtitle project and not an automatic-export failure.
        if not project.mappings:
            project.warnings.append('未检测到可用歌词；请调整识别区域或手动创建字幕。')
        project.stage="review" if not project.mappings or project.unresolved() or project.warnings else "ready"
        checkpoint()
        if project.stage=="ready":
            target=project_path.with_name(project_path.stem+"_中文.ass")
            index=1
            base=target
            while target.exists():
                target=base.with_name(base.stem+f"_{index}.ass")
                index+=1
            export_project(project,target)
            project.settings["last_export"]=str(target)
            save_project(project,project_path)
        progress(1,f'识别完成：共 {len(project.mappings)} 句，待确认 {len(project.unresolved())} 句')
        return project
    except InterruptedError:
        project.stage="cancelled"
        checkpoint()
        raise
    except Exception:
        project.stage="failed"
        # A lineage conflict during hydration must preserve the document's
        # existing ledger, not overwrite it using the incompatible cache.
        if ledger_ready:checkpoint()
        else:save_project(project,project_path)
        raise
    finally:
        cache.close()
