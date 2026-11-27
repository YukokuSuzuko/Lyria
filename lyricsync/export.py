"""Validated main-lyric event construction; source ASS is never copied."""
from __future__ import annotations

from pathlib import Path
import math
from decimal import Decimal, ROUND_HALF_UP
from bisect import bisect_left
import unicodedata
import os
import shutil
import tempfile
import json

from .model import valid_time, TIME_EPSILON
from .storage import protected_project_inputs, atomic_text, protect_output
from .subtitles import ass_time
from .editing import mapping_text, segments


def _exceeds_video_duration(end, duration):
    """Use one tolerance for export validation and explicit time omissions."""
    return bool(duration and end > duration + TIME_EPSILON)


def validate_project(project, draft=False):
    problems = []
    try:project.validate_timeline()
    except ValueError as exc:problems.append(str(exc))
    # Check each identity namespace before lookup dictionaries can hide records.
    for label, rows in (("译文", project.translations), ("原文", project.lyrics),
                        ('识别片段', project.visuals), ('字幕条目', project.mappings)):
        if len({row.id for row in rows}) != len(rows):
            problems.append(label + "存在重复 ID")
    translations = {u.id: u for u in project.translations}
    lyrics = {u.id for u in project.lyrics}
    visuals = {u.id for u in project.visuals}
    covered = set()
    spans={i:[] for i in translations}
    if project.warnings and not draft:
        # Parser warnings must be explicitly acknowledged in project review.
        if not project.settings.get("parser_acknowledged", False):
            problems.append('请先查看并确认工程提示：' + "; ".join(project.warnings))
    for mapping in project.mappings:
        if mapping.ignored:
            problems.append(mapping.id + '：该句译文处于忽略状态，正式导出前请恢复或处理')
            continue
        if (mapping.text_role == 'translation' and not mapping.translation_ids
                or not set(mapping.translation_ids) <= translations.keys()):
            problems.append(mapping.id + "：译文 ID 无效")
        if len(set(mapping.translation_ids)) != len(mapping.translation_ids):
            problems.append(mapping.id + "：重复译文 ID")
        covered.update(mapping.translation_ids)
        if not mapping_text(project,mapping).strip():problems.append(mapping.id+'：字幕内容为空')
        for segment in segments(project,mapping):
            i=segment.get('id');a=segment.get('start');b=segment.get('end')
            if i not in translations or type(a) is not int or type(b) is not int or not 0<=a<=b<=len(translations[i].text):
                problems.append(mapping.id+'：拆分片段关联异常，请重新检查该句')
            else:spans[i].append((a,b))
        if not set(mapping.lyric_ids) <= lyrics or not set(mapping.visual_ids) <= visuals:
            problems.append(mapping.id + '：关联的原文或识别记录无效')
        if not valid_time(mapping.start, mapping.end):
            problems.append(mapping.id + "：缺少合法视觉时间")
        elif _exceeds_video_duration(mapping.end, project.video.get("duration")):
            problems.append(mapping.id + "：时间超出视频")
        if not draft and not mapping.confirmed:
            problems.append(mapping.id + "：字幕尚未确认（可选择导出草稿）")
        if not draft and not mapping.visual_ids and not mapping.confirmed:
            problems.append(mapping.id + '：尚未人工确认且无识别时间参考')
    # Explicitly deleted cues are retained as provenance, not exported. Their
    # source ranges count as intentionally removed rather than accidental loss.
    from .model import Mapping
    for deleted in project.settings.get('deleted_mappings',[]):
        try:
            removed=Mapping(**deleted)
            for segment in segments(project,removed):
                i,a,b=segment.get('id'),segment.get('start'),segment.get('end')
                if i in translations and type(a) is int and type(b) is int and 0<=a<=b<=len(translations[i].text):
                    covered.add(i);spans[i].append((a,b))
        except (TypeError, ValueError, KeyError):
            problems.append('已删除字幕的原始记录无效')
    missing = translations.keys() - covered
    if missing:
        problems.append(f'仍有 {len(missing)} 句导入译文未排入时间轴')
    for i,ranges in spans.items():
        frontier=0
        for a,b in sorted(ranges):
            if a>frontier:break
            frontier=max(frontier,b)
        if frontier<len(translations[i].text) and i in covered:
            problems.append('译文拆分后仍有未分配的文字：'+translations[i].text)
    if not project.mappings:
        problems.append('当前没有可导出的字幕')
    return list(dict.fromkeys(problems))


def escape_text(text):
    # ASS has no escaped-backslash syntax: doubling one still lets the second
    # slash consume N/n/h. An invisible WORD JOINER separates each literal slash
    # from the next character (also used by FFmpeg's plain-text ASS encoder).
    # Keep an explicit header marker so our importer can remove exactly the
    # inserted joiner without stripping a user's original Unicode characters.
    # The empty block after an escaped opening brace prevents VSFilter from
    # swallowing the remaining text; literal braces still need libass to render
    # exactly. Never turn user-provided override-looking text into live tags.
    text=text.replace("\r\n", "\n").replace("\r", "\n")
    return ''.join('\\\u2060' if char=='\\' else r'\{{}' if char=='{' else
                   r'\N' if char=='\n' else char for char in text)


def _line_count(text, font_size, width):
    # Conservative display-width estimate; ASS WrapStyle=0 also wraps at render.
    columns = max(8, width / max(1, font_size))
    return sum(max(1, math.ceil(sum(1 if unicodedata.east_asian_width(c) in "WF" else .55
                                   for c in line) / columns)) for line in text.split("\n"))


def placement_options(project):
    height=project.video.get("height",1080)
    tops=[v.box[1] for v in project.visuals if len(v.box)==4]
    roi=project.settings.get("roi")
    from .multi_roi import export_top
    region_top = export_top(project.settings)
    source_top = region_top if region_top is not None else (min(tops) if tops else (roi[1] if roi else height*.9))
    return dict(position=project.settings.get("subtitle_position","above_original"),
                height_percent=project.settings.get("subtitle_height_percent",80),source_top=source_top,
                horizontal_percent=project.settings.get("subtitle_horizontal_percent",50),
                custom_alignment=project.settings.get("subtitle_custom_alignment",2),
                vertical=project.settings.get("subtitle_vertical",False))


def layout_anchor(resolution=(1920,1080),font_size=None,position="bottom",height_percent=80,source_top=None,horizontal_percent=50,custom_alignment=2,vertical=False):
    width,height=map(int,resolution)
    size=int(font_size or max(20,round(height*.044)))
    outline=max(1,round(height/540));margin=max(20,round(height*.055))
    if position not in {"top","bottom","above_original","custom"}:
        raise ValueError('不支持的字幕位置选项')
    if not isinstance(height_percent,(int,float)) or not math.isfinite(height_percent) or not 5<=height_percent<=95:
        raise ValueError("自定义高度应为画面从上往下的 5%～95%")
    if not isinstance(horizontal_percent,(int,float)) or isinstance(horizontal_percent,bool) or not math.isfinite(horizontal_percent) or not 5<=horizontal_percent<=95:
        raise ValueError("水平位置应为画面从左往右的 5%～95%")
    if type(vertical) is not bool:raise ValueError('竖排选项设置无效。')
    if custom_alignment not in (2,8):raise ValueError('字幕对齐方式设置无效。')
    align_top=position=="top" or (position=="custom" and custom_alignment==8)
    if position=="top":base=margin
    elif position=="custom":base=height*height_percent/100
    elif position=="above_original":base=min(height-margin,(source_top if source_top is not None else height*.9)-max(12,height*.02))
    else:base=height-margin
    return dict(x=width*horizontal_percent/100,y=base,alignment=8 if align_top else 2,font_size=size)


def layout_entries(entries,resolution=(1920,1080),font_size=None,position="bottom",height_percent=80,source_top=None,horizontal_percent=50,custom_alignment=2,vertical=False):
    """Faithful placement: overlapping cues retain the user's anchor and font."""
    anchor=layout_anchor(resolution,font_size,position,height_percent,source_top,horizontal_percent,custom_alignment,vertical)
    width,height=map(int,resolution);size=anchor['font_size']
    outline=max(1,round(height/540));margin=max(20,round(height*.055))
    align_top=anchor['alignment']==8
    active=[];result=[]
    for index,(start,end,text) in enumerate(sorted(entries,key=lambda e:(e[0],e[1]))):
        if not valid_time(start,end) or not text.strip():raise ValueError(f'第 {index + 1} 条字幕的时间或内容无效。')
        if vertical:
            from .graphemes import character_gaps
            compact=text.replace("\r\n","\n").replace("\r","\n").replace("\n","")
            boundaries=character_gaps(compact)
            text="\n".join(compact[a:b] for a,b in zip(boundaries,boundaries[1:]))
        if round(end*100)<=round(start*100):raise ValueError('导出 ASS 时长不足 0.01 秒（被四舍五入为 0），请拉长该句时长。')
        active=[(b,y,block) for b,y,block in active if b>start+TIME_EPSILON]
        block_height=math.ceil(_line_count(text,size,width*.87)*size*1.35)+outline*2+8
        block_extent=(math.ceil(size*1.35)+outline*2+8) if vertical else block_height
        # Overlap is valid subtitle timing, not permission to move the user's
        # text. Preview/export share the same anchor even when glyphs collide.
        x,y=anchor['x'],anchor['y']
        overflow=(y+block_height>height-margin if align_top else y-block_height<margin) or (vertical and x-block_extent/2<margin)
        overlapping=bool(active)
        active.append((end,0,block_extent))
        result.append(dict(start=start,end=end,text=text,x=round(x),y=round(y),
                           alignment=8 if align_top else 2,font_size=size,overflow=overflow,overlapping=overlapping))
    return result


def render_ass(entries, resolution=(1920, 1080), font="Microsoft YaHei", font_size=None,
               position="bottom",height_percent=80,source_top=None,frame_index=None,horizontal_percent=50,custom_alignment=2,vertical=False):
    if frame_index is not None:
        entries=[(frame_index.ass_boundary(a),frame_index.ass_boundary(b),text) for a,b,text in entries]
    width, height = map(int, resolution)
    if width < 64 or height < 64 or width > 16384 or height > 16384:
        raise ValueError("视频分辨率无效。")
    size = int(font_size or max(20, round(height * .044)))
    if not 8 <= size <= height // 3 or any(c in font for c in ",\r\n"):
        raise ValueError("字体或字号无效。")
    outline = max(1, round(height / 540))
    margin = max(20, round(height * .055))
    rows = ["[Script Info]", "Title: Lyria ordinary subtitles", "ScriptType: v4.00+",
            "LyriaTextEscapes: word-joiner-v1",
            f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 0",
            "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "", "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Chinese,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,0,0,0,0,100,100,0,0,1,{outline},1,2,{round(width*.06)},{round(width*.06)},{margin},1", "", "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for row in layout_entries(entries,resolution,size,position,height_percent,source_top,horizontal_percent,custom_alignment,vertical):
        position_tag="{\\an"+str(row['alignment'])+"\\pos("+f"{row['x']},{row['y']}"+")}"
        rows.append(f"Dialogue: 0,{ass_time(row['start'])},{ass_time(row['end'])},Chinese,,0,0,0,,{position_tag}{escape_text(row['text'])}")
    return "\n".join(rows) + "\n"



def project_layout_rows(project, frame_index=None, omitted_ids=()):
    """Shared style resolution for Qt preview and ASS export, stable cue identity."""
    from .speakers import style_for, speaker_name
    from .styles import project_profiles
    profiles=project_profiles(project)
    resolution=(project.video.get('width',1920) or 1920,project.video.get('height',1080) or 1080)
    rows=[]
    for mapping in sorted(project.mappings,key=lambda m:(m.start if m.start is not None else float('inf'),m.end if m.end is not None else float('inf'))):
        text=mapping_text(project,mapping)
        if mapping.id in omitted_ids or mapping.ignored or not valid_time(mapping.start,mapping.end) or not text.strip():continue
        a,b=mapping.start,mapping.end
        if frame_index is not None:a,b=frame_index.ass_boundary(a),frame_index.ass_boundary(b)
        style=style_for(project,mapping,profiles=profiles)
        if style['size']>resolution[1]//3:raise ValueError('字幕字号超过画面高度的三分之一，请缩小字号。')
        row=layout_entries([(a,b,text)],resolution,style['size'],**placement_options(project))[0]
        row.update(cue_id=mapping.id,speaker_name=speaker_name(project,mapping),style=style,
                   style_id=mapping.style_id, style_overrides=dict(mapping.style_overrides))
        rows.append(row)
    return rows


def render_project_ass(project, frame_index=None, omitted_ids=()):
    from .styles import project_profiles
    resolution=(project.video.get('width',1920) or 1920,project.video.get('height',1080) or 1080)
    width,height=resolution
    body=render_ass([],resolution,project.settings.get('font','Microsoft YaHei'),project.settings.get('font_size'),**placement_options(project))
    laid=project_layout_rows(project,frame_index,omitted_ids)
    style_lines=[];events=[];names={};used_names=set();detached={}
    actor_names={};used_actors=set()
    aliases={'format':'lyria-identity-aliases-v2','actors':{},'styles':{}}
    def safe_field(value):
        return ' '.join(value.splitlines()).strip().replace(',', '，').replace('\\','＼').replace('{','｛').replace('}','｝')
    def unique_alias(proposed, used):
        proposed=safe_field(proposed) or 'Unnamed';name=proposed[:80];number=2
        while name.casefold() in used:
            suffix=' ('+str(number)+')';number+=1
            name=proposed[:80-len(suffix)]+suffix
        used.add(name.casefold())
        return name
    def register(identity, proposed, style):
        name=unique_alias(proposed,used_names);names[identity]=name
        aliases['styles'][name]={'id':identity,'name':proposed}
        hexcolor=style['color'][1:]
        asscolor='&H00'+hexcolor[4:6]+hexcolor[2:4]+hexcolor[:2]
        style_lines.append(f"Style: {name},{style['font']},{style['size']},{asscolor},&H00FFFFFF,&H00101010,&H80000000,{-1 if style['bold'] else 0},{-1 if style['italic'] else 0},0,0,100,100,0,0,1,{max(1,round(height/540))},1,2,{round(width*.06)},{round(width*.06)},{max(20,round(height*.055))},1")
        return name
    for identity,profile in project_profiles(project).items():
        register(identity, profile['name'], profile['style'])
    for row in laid:
        if row['style_id']:
            name=names[row['style_id']]
        else:
            key=tuple(sorted(row['style'].items()))
            if key not in detached:
                number=len(detached)
                while 'detached-'+str(number) in names:number+=1
                detached[key]=register('detached-'+str(number), '个人样式 '+str(len(detached)+1), row['style'])
            name=detached[key]
        actor = row['speaker_name']
        if actor:
            if actor not in actor_names:
                alias = unique_alias(actor, used_actors)
                actor_names[actor] = alias
                aliases['actors'][alias] = {'name': actor}
            actor = actor_names[actor]
        overrides=row['style_overrides'] if row['style_id'] else {}
        tags=[]
        for key,value in overrides.items():
            if key=='font': tags.append('\\fn'+value)
            elif key=='size': tags.append('\\fs'+str(value))
            elif key=='color': tags.append('\\c&H'+value[5:7]+value[3:5]+value[1:3]+'&')
            elif key in ('bold','italic'): tags.append(('\\b' if key=='bold' else '\\i')+('1' if value else '0'))
        tag="{\\an"+str(row['alignment'])+"\\pos("+f"{row['x']},{row['y']}"+")"+''.join(tags)+"}"
        events.append(f"Dialogue: 0,{ass_time(row['start'])},{ass_time(row['end'])},{name},{actor},0,0,0,,{tag}{escape_text(row['text'])}")
    # Replace the one default declaration, preserving the shared header/format.
    body='\n'.join(line for line in body.splitlines() if not line.startswith('Style: '))+'\n'
    # Standard renderers ignore this Script Info key. Lyria uses it to recover
    # original names and distinct IDs without interpreting any text as code.
    metadata=json.dumps(aliases,ensure_ascii=True,separators=(',',':'))
    body=body.replace('[Script Info]\n','[Script Info]\nLyriaIdentityAliases: '+metadata+'\n',1)
    return body.replace('\n\n[Events]', '\n'+'\n'.join(style_lines)+'\n\n[Events]')+'\n'.join(events)+'\n'

def missing_time_rows(project):
    duration=project.video.get('duration')
    return [{'id':m.id,'text':mapping_text(project,m),'reason':
             '缺少合法起止时间' if not valid_time(m.start,m.end) else '时间超出视频范围'}
            for m in project.mappings if not valid_time(m.start,m.end) or _exceeds_video_duration(m.end,duration)]


def partial_export_problems(project):
    # Only time omissions are authorized. ID validity and full original text coverage
    # still have to pass, including split fragments that are being skipped.
    omitted={r['id'] for r in missing_time_rows(project)}
    allowed={i+'：'+reason for i in omitted for reason in ('缺少合法视觉时间','时间超出视频')}
    problems=[p for p in validate_project(project,draft=True) if p not in allowed]
    if len(omitted)==len(project.mappings):problems.append('所有字幕均未设置有效起止时间，无法导出')
    return problems


def _timestamp_units(stamp, units_per_second):
    if not math.isfinite(stamp) or stamp < 0:
        raise ValueError("字幕时间无效。")
    return max(0, int((Decimal(str(stamp)) * units_per_second).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))


def _frame_boundary(index, stamp, units):
    """Choose a timestamp that activates on the same decoded frame, if representable."""
    pos=bisect_left(index.stamps,stamp-TIME_EPSILON)
    upper=index.stamps[pos] if pos<len(index.stamps) else index.duration
    lower=index.stamps[pos-1] if pos else -1
    high=math.floor((upper+TIME_EPSILON)*units)
    low=max(0,math.floor((lower+TIME_EPSILON)*units)+1)
    if low>high:raise ValueError('当前字幕格式的时间精度不足以区分该帧边界，请微调起止时间或改导 ASS/SRT。')
    return max(low,min(high,_timestamp_units(stamp,units)))/units


def render_srt(entries):
    """SRT stores start and exclusive end to millisecond precision."""
    def stamp(value):
        ms = _timestamp_units(value, 1000)
        hours, rem = divmod(ms, 3600000)
        minutes, rem = divmod(rem, 60000)
        seconds, milli = divmod(rem, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milli:03d}"
    rows = []
    for n, (start, end, text) in enumerate(sorted(entries, key=lambda e: (e[0], e[1])), 1):
        if not valid_time(start, end) or not text.strip():
            raise ValueError("SRT 字幕时间或正文无效。")
        if _timestamp_units(end, 1000) <= _timestamp_units(start, 1000):
            raise ValueError('导出 SRT 时长不足 1 毫秒，请拉长该句时长。')
        rows.append(f"{n}\n{stamp(start)} --> {stamp(end)}\n{text.strip()}")
    return "\n\n".join(rows) + "\n"


def render_lrc(entries):
    """Standard line LRC stores a start timestamp only; it cannot encode cue ends."""
    rows = []
    for start, end, text in sorted(entries, key=lambda e: (e[0], e[1])):
        if not valid_time(start, end) or not text.strip():
            raise ValueError("LRC 歌词时间或正文无效。")
        cs = _timestamp_units(start, 100)
        minutes, rem = divmod(cs, 6000)
        seconds, hundredths = divmod(rem, 100)
        # Multi-line subtitles stay one timed lyric line, avoiding untimed lines.
        one_line = " ".join(text.replace("\r", "\n").splitlines()).strip()
        rows.append(f"[{minutes:02d}:{seconds:02d}.{hundredths:02d}]{one_line}")
    return "\n".join(rows) + "\n"


SRT_MODES = (
    'main',
    'bilingual_trans_first',
    'bilingual_source_first',
    'source_only',
)


def srt_entry_text(project, mapping, srt_mode='main', *, translations_lookup=None, lyrics_lookup=None):
    """Build per-cue text for native SRT export (single-track or bilingual)."""
    if srt_mode not in SRT_MODES:
        raise ValueError('不支持的 SRT 导出内容模式。')
    main_text = mapping_text(project, mapping, translations_lookup).strip()
    if srt_mode == 'main':
        return main_text
    if lyrics_lookup is None:
        lyrics_lookup = {row.id: row.text for row in project.lyrics}
    source_parts = [lyrics_lookup[i].strip() for i in mapping.lyric_ids if i in lyrics_lookup and lyrics_lookup[i].strip()]
    source_text = ' '.join(source_parts).strip()
    if not source_text and mapping.text_role != 'translation':
        source_text = main_text
    if srt_mode == 'source_only':
        return source_text
    if srt_mode == 'bilingual_trans_first':
        parts = [main_text, source_text]
    else:
        parts = [source_text, main_text]
    lines = []
    for part in parts:
        if part and part not in lines:
            lines.append(part)
    return '\n'.join(lines)


def export_project(project, path, overwrite=False, draft=False, skip_missing_times=False, frame_index=None, *, srt_mode='main', srt_bom=True):
    omitted=missing_time_rows(project) if skip_missing_times else []
    if skip_missing_times:draft=True
    problems = partial_export_problems(project) if skip_missing_times else validate_project(project,draft=draft)
    if problems:
        raise ValueError("导出校验未通过：\n" + "\n".join(problems[:30]))
    translations = {u.id: u.text for u in project.translations}
    lyrics_lookup = {u.id: u.text for u in project.lyrics}
    omitted_ids={r['id'] for r in omitted}
    fmt=Path(path).suffix.casefold()
    if fmt not in ('.ass','.srt','.lrc'):
        raise ValueError('字幕输出请使用 .ass、.srt 或 .lrc 扩展名。')
    if fmt == '.srt':
        if srt_mode not in SRT_MODES:
            raise ValueError('不支持的 SRT 导出内容模式。')
        if type(srt_bom) is not bool:
            raise ValueError('SRT 编码选项无效。')
        entries = []
        for m in project.mappings:
            if m.id in omitted_ids:
                continue
            text = srt_entry_text(project, m, srt_mode, translations_lookup=translations, lyrics_lookup=lyrics_lookup)
            if not text.strip():
                if srt_mode == 'source_only':
                    continue
                raise ValueError('SRT 字幕正文不能为空。')
            entries.append((m.start, m.end, text))
        if not entries:
            raise ValueError('当前工程缺少关联原文歌词，无法仅导出原文 SRT（请先导入原文或改选其他 SRT 模式）。')
    else:
        entries = [(m.start, m.end, mapping_text(project,m)) for m in project.mappings if m.id not in omitted_ids]
    text_encoding = 'utf-8-sig' if (fmt != '.srt' or srt_bom) else 'utf-8'
    if fmt == '.ass' and frame_index is None and project.inputs.get('video') and Path(project.inputs['video']).is_file():
        from .frames import FrameIndex
        from .vision import Video
        frame_index=FrameIndex.build(Video(project.inputs['video']))
    if fmt == '.ass':
        from .speakers import has_speaker_styles
        if has_speaker_styles(project):
            body = render_project_ass(project, frame_index=frame_index, omitted_ids=omitted_ids)
        else:
            body = render_ass(entries, (project.video.get("width", 1920), project.video.get("height", 1080)),
                              project.settings.get("font", "Microsoft YaHei"), project.settings.get("font_size"),
                              **placement_options(project),frame_index=frame_index)
    elif fmt == '.srt':
        timed=[(_frame_boundary(frame_index,a,1000),_frame_boundary(frame_index,b,1000),t) for a,b,t in entries] if frame_index else entries
        body=render_srt(timed)
    else:
        timed=[(_frame_boundary(frame_index,a,100),b,t) for a,b,t in entries] if frame_index else entries
        body=render_lrc(timed)
    if draft and fmt == '.ass':
        body=body.replace('Title: Lyria ordinary subtitles','Title: Lyria UNREVIEWED DRAFT')
        body=body.replace('[Script Info]','[Script Info]\n; 初稿字幕：部分时间轴或文本尚未最终确认')
    if omitted:
        report=Path(path).with_name(Path(path).stem+'_遗漏清单.json')
        # Validate both targets before writing either; source materials remain protected.
        protect_output(path,protected_project_inputs(project));protect_output(report,protected_project_inputs(project))
        if Path(path).exists() and not overwrite:raise FileExistsError('目标文件已存在，请更换文件名或确认覆盖。')
        if report.exists() and not overwrite:raise FileExistsError('同目录下的遗漏清单文件已存在，请更换导出文件名。')
        if fmt == '.ass':body=body.replace('[Script Info]',f'[Script Info]\n; 部分导出：跳过 {len(omitted)} 条，详见同目录遗漏清单')
        _write_partial_export(path,body,report,{'exported_count':len(entries),'skipped_count':len(omitted),'omitted':omitted,
                    'note':'本次导出已跳过缺少有效时间的句子，原工程中的全部字幕仍完整保留。'},protected_project_inputs(project),overwrite,encoding=text_encoding)
        return omitted
    if fmt == '.ass':write_ass(path, body, protected_project_inputs(project), overwrite)
    else:write_text_subtitle(path, body, protected_project_inputs(project), overwrite, encoding=text_encoding)
    return omitted


def _write_partial_export(path, body, report, report_data, inputs, overwrite, encoding='utf-8-sig'):
    """Stage both outputs and roll back a failed pair installation.

    Two filesystem names cannot be replaced atomically as a group. Do not claim
    otherwise: staging errors change neither target; an installation error
    restores already replaced targets. If restoration itself is denied, keep
    the exact old snapshots and tell the user where to recover them.
    """
    from .storage import atomic_json
    inputs=tuple(inputs)
    targets=[protect_output(path,inputs),protect_output(report,inputs)]
    for target in targets:
        if target.exists() and not overwrite:raise FileExistsError('目标文件或遗漏清单已存在，请更换导出文件名。')
    targets[0].parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='.lyria-export-',dir=targets[0].parent))
    installed=[];originals={};keep_recovery=False
    try:
        # User-controlled basenames must never collide with recovery snapshots.
        for directory in ('new','originals','restore'):(staging/directory).mkdir()
        staged_subtitle=staging/'new'/('subtitle'+targets[0].suffix)
        staged_report=staging/'new'/'omissions.json'
        if targets[0].suffix.casefold()=='.ass':write_ass(staged_subtitle,body,inputs)
        else:write_text_subtitle(staged_subtitle,body,inputs,encoding=encoding)
        atomic_json(staged_report,report_data,inputs)
        for n,target in enumerate(targets):
            protect_output(target,inputs)
            if target.exists():
                if not overwrite:raise FileExistsError('目标文件或遗漏清单已存在，请更换导出文件名。')
                backup=staging/'originals'/(str(n)+target.suffix)
                shutil.copyfile(target,backup)
                originals[target]=backup
            else:originals[target]=None
        try:
            for staged,target in zip((staged_subtitle,staged_report),targets):
                protect_output(target,inputs)
                if target.exists() and originals[target] is None:
                    raise FileExistsError('导出期间同名文件被其他程序创建，已停止写入以避免误覆盖。')
                os.replace(staged,target)
                installed.append(target)
        except (OSError,ValueError) as error:
            failed=[]
            for target in reversed(installed):
                try:
                    backup=originals[target]
                    if backup is None:target.unlink(missing_ok=True)
                    else:
                        # Preserve the original backup if the recovery replace
                        # fails too (for example a Windows sharing lock).
                        restore=staging/'restore'/str(targets.index(target))
                        shutil.copyfile(backup,restore)
                        protect_output(target,inputs);os.replace(restore,target)
                except (OSError,ValueError) as rollback_error:
                    failed.append(type(rollback_error).__name__)
            if failed:
                keep_recovery=True
                raise OSError('导出失败，原文件备份保存在：'+str(staging)) from error
            raise
    finally:
        if not keep_recovery:shutil.rmtree(staging)


def write_text_subtitle(path, body, inputs, overwrite=False, encoding='utf-8-sig'):
    path = protect_output(path, inputs)
    if path.suffix.casefold() not in ('.srt', '.lrc'):
        raise ValueError('输出文件必须为 .srt 或 .lrc。')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError('目标文件已存在，请更换文件名或确认覆盖。')
    atomic_text(path, body, inputs, encoding=encoding)


def write_ass(path, body, inputs, overwrite=False):
    path = protect_output(path, inputs)
    if path.suffix.casefold() != ".ass":
        raise ValueError("输出文件必须为 .ass。")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError('目标文件已存在，请更换文件名或确认覆盖。')
    atomic_text(path, body, inputs, encoding="utf-8-sig")


def convert_ktv(source, target, no_overlap=False, overwrite=False):
    from .subtitles import parse_ass, recover_lyrics
    doc = parse_ass(source)
    lyrics = recover_lyrics(doc)
    entries = [(u.start, u.end, u.text) for u in lyrics]
    if no_overlap:
        # Deliberate opt-in; never applied to visual timing in the main flow.
        entries = [(a, min(b, entries[i+1][0]) if i+1 < len(entries) else b, t)
                   for i, (a, b, t) in enumerate(entries)]
        if any(not valid_time(a, b) for a, b, _ in entries):
            raise ValueError('存在同一时刻开始的多行歌词，无法自动转为单句无重叠模式，请先手动错开时间。')
    resolution = (int(doc.info.get("playresx", 1920)), int(doc.info.get("playresy", 1080)))
    write_ass(target, render_ass(entries, resolution), (source,), overwrite)
    return lyrics, doc.warnings
