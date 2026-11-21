"""ASS syntax and lyric recovery. Karaoke durations are metadata, never visual time."""
from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import re

from .model import Lyric, Translation, normalized, stable_id, valid_time


def read_text(path):
    data = Path(path).read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    for encoding in ("utf-8-sig", "cp932", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeError:
            pass
    raise ValueError('无法识别文件文本编码，请将文件另存为 UTF-8 格式后重新导入。')


def time_seconds(value):
    parts = value.strip().replace(",", ".").split(":")
    if len(parts) > 3:
        raise ValueError("无效时间：" + value)
    result = 0.0
    for part in parts:
        result = result * 60 + float(part)
    if not valid_time(result, result + 1):
        raise ValueError('时间数值无效（不能为负数）。')
    return result


def ass_time(value):
    cs = round(value * 100)
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def _top_tags(block):
    """Extract tags outside transform/clip parentheses, preserving unknown tags."""
    tags, start, depth = [], None, 0
    for i, char in enumerate(block):
        if char == "\\" and depth == 0:
            if start is not None:
                tags.append(block[start:i])
            start = i + 1
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
    if start is not None:
        tags.append(block[start:])
    return tags


def ass_plain(text, karaoke_readings=False, *, lyria_escapes=False, _reading_segments=None):
    """Stateful text decoder: override blocks, drawing mode and ASS escapes."""
    out, warnings, tags = [], [], []
    i, drawing = 0, False
    syllables=[0]
    while i < len(text):
        char = text[i]
        if char == "{" :
            end = text.find("}", i + 1)
            if end < 0:
                warnings.append("未闭合的 ASS 标签，需检查原文")
                if not drawing:
                    out.append(text[i:])
                break
            current = _top_tags(text[i + 1:end])
            tags.extend(current)
            if any(re.fullmatch(r'(?:k|K|kf|ko|kt)\s*\d+',tag.strip()) for tag in current):syllables.append(len(out))
            for tag in current:
                p = re.fullmatch(r"p\s*(\d+)", tag.strip())
                if p:
                    drawing = int(p[1]) > 0
                elif tag.startswith("r"):
                    drawing = False
            i = end + 1
            continue
        if char == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if lyria_escapes and nxt == '\u2060':
                if not drawing:out.append('\\')
                i += 2
                continue
            if nxt in "Nnh{}\\":
                if not drawing:
                    out.append({"N": "\n", "n": "\n", "h": "\u00a0"}.get(nxt, nxt))
                i += 2
                continue
        if not drawing:
            out.append(char)
        i += 1
    if karaoke_readings and len(syllables)>1:
        chunks=[''.join(out[a:b]) for a,b in zip(syllables,syllables[1:]+[len(out)])]
        # Aegisub karaskel: syllable body | reading; # is a continuation highlight.
        # Require kana readings so ordinary literal pipes are not silently removed.
        ruby=any(re.search(r'[|｜][!！<＜]?[ぁ-ヿ]+',chunk) for chunk in chunks)
        if ruby:
            out=[]
            segments=[]
            cursor=0
            for chunk in chunks:
                pair=re.split('[|｜]',chunk,maxsplit=1)
                body=pair[0]
                reading=pair[1].lstrip('!！<＜') if len(pair)>1 else ''
                if body.lstrip().startswith(('#','＃')):
                    if segments and reading:
                        segments[-1]['reading'] += reading
                    continue
                out.append(body)
                segments.append(dict(text=body,reading=reading,start=cursor,end=cursor+len(body)))
                cursor+=len(body)
            if _reading_segments is not None:
                _reading_segments.extend(segments)
            warnings.append('已自动提取卡拉 OK 歌词正文（注音另存供原词打轴使用）')
    return "".join(out), tags, warnings


@dataclass
class AssEvent:
    id: str
    start: float
    end: float
    text: str
    raw_text: str
    layer: str
    style: str
    name: str
    effect: str
    position: list[float] | None
    tags: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    margins: tuple[int | None, int | None, int | None] = (0, 0, 0)
    extra_fields: dict = field(default_factory=dict)


@dataclass
class AssDocument:
    events: list[AssEvent]
    info: dict
    styles: dict
    warnings: list[str]
    identity_aliases: dict = field(default_factory=dict)


def _identity_aliases(info):
    raw=info.get('lyriaidentityaliases')
    if raw is None:return {}
    if len(raw)>2*1024*1024:raise ValueError('ASS 身份名称映射超过大小限制。')
    try:
        data=json.loads(raw)
        if not isinstance(data,dict) or set(data)!={'format','actors','styles'} or data['format'] not in ('lyria-identity-aliases-v1','lyria-identity-aliases-v2'):
            raise ValueError()
        for kind in ('actors','styles'):
            table=data[kind]
            if not isinstance(table,dict) or len(table)>4096:raise ValueError()
            ids=set()
            for alias,value in table.items():
                if kind == 'actors' and data['format'] == 'lyria-identity-aliases-v2':
                    from .speakers import valid_name
                    if (not valid_name(alias) or not isinstance(value, dict) or set(value) != {'name'}
                            or not valid_name(value['name']) or value['name'] in ids):
                        raise ValueError()
                    ids.add(value['name'])
                    continue
                if (not isinstance(alias,str) or not 1<=len(alias)<=80 or any(ord(c)<32 for c in alias)
                        or not isinstance(value,dict) or set(value)!={'id','name'}):raise ValueError()
                identity,name=value['id'],value['name']
                if (not isinstance(identity,str) or not 1<=len(identity)<=80 or any(ord(c)<32 for c in identity)
                        or identity in ids or not isinstance(name,str) or not name.strip() or len(name)>80
                        or any(ord(c)<32 for c in name)):raise ValueError()
                if kind=='actors' and not re.fullmatch(r'speaker-[0-9a-f]{32}',identity):raise ValueError()
                ids.add(identity)
        return data
    except (ValueError,TypeError,KeyError) as exc:
        raise ValueError('ASS 身份名称映射无效，请检查文件；未按相同显示名称合并人物或样式。') from exc


def parse_ass(path) -> AssDocument:
    content = read_text(path)
    fingerprint = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    events, info, styles, warnings = [], {}, {}, []
    section, fmt, style_fmt = "", [], []
    default = "Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text"
    for number, line in enumerate(content.splitlines(), 1):
        row = line.strip()
        if not row or row.startswith(";"):
            continue
        if row.startswith("["):
            section = row.casefold()
            continue
        if ":" not in row:
            continue
        key, value = row.split(":", 1)
        key, value = key.casefold(), value.lstrip()
        if section == "[script info]":
            info[key] = value
        elif section in ("[v4+ styles]", "[v4 styles]"):
            if key == "format":
                style_fmt = [x.strip().casefold() for x in value.split(",")]
            elif key == "style" and style_fmt:
                style = dict(zip(style_fmt, value.split(",", len(style_fmt) - 1)))
                styles[style.get("name", "").strip()] = style
        elif section == "[events]":
            if key == "format":
                fmt = [x.strip().casefold() for x in value.split(",")]
            elif key == "dialogue":
                fields = fmt or default.lower().split(",")
                if not fmt:
                    warnings.append(f'第 {number} 行缺少 ASS 字段定义，已按默认格式解析')
                # Text can contain commas. Non-terminal Text is ambiguous and rejected.
                if fields[-1] != "text" or not {"start", "end"}.issubset(fields):
                    raise ValueError(f'第 {number} 行：ASS [Events] 格式缺少 Start/End 列，或 Text 不在最后一列。')
                values = value.split(",", len(fields) - 1)
                if len(values) != len(fields):
                    warnings.append(f'第 {number} 行格式不完整，已跳过')
                    continue
                data = dict(zip(fields, values))
                try:
                    start, end = time_seconds(data["start"]), time_seconds(data["end"])
                    if not valid_time(start, end):
                        raise ValueError("结束时间必须大于开始时间")
                except ValueError as exc:
                    warnings.append(f'第 {number} 行：{exc}，已跳过')
                    continue
                plain, tags, notes = ass_plain(data["text"],
                    lyria_escapes=info.get('lyriatextescapes')=='word-joiner-v1')
                if not normalized(plain):
                    continue
                position = None
                for tag in tags:
                    match = re.match(r"(?:pos|move)\(\s*([-\d.]+)\s*,\s*([-\d.]+)", tag)
                    if match:
                        position = [float(match[1]), float(match[2])]
                if any(t.startswith(("move(", "t(", "clip(", "iclip(")) for t in tags):
                    notes.append('原字幕含移动或裁剪特效，时间轴仅供参考')
                margins=[]
                for field_name in ('marginl','marginr','marginv'):
                    try:margins.append(int(data.get(field_name,'0')))
                    except ValueError:margins.append(None)
                known_fields={'layer','marked','start','end','style','name','actor',
                              'marginl','marginr','marginv','effect','text'}
                events.append(AssEvent(stable_id("event", fingerprint, number), start, end,
                                       plain, data["text"], data.get("layer", data.get("marked", "0")),
                                       data.get("style", "Default").strip(), data.get("name", data.get("actor", "")).strip(),
                                       data.get("effect", ""), position, tags, notes, tuple(margins),
                                       {key:value for key,value in data.items() if key not in known_fields}))
    if not events:
        raise ValueError('该 ASS/SSA 字幕中没有可读取的对话行（Dialogue）。')
    return AssDocument(events, info, styles, list(dict.fromkeys(warnings)), _identity_aliases(info))


def _ass_metadata(doc, event):
    from .styles import ass_style, validate_overrides, validate_profiles
    if len(event.name)>80 or any(ord(c)<32 for c in event.name):
        raise ValueError('ASS 说话人名称须为不超过 80 个字符的单行文本。')
    profile = doc.styles.get(event.style)
    result = {'actor': event.name, 'style_name': event.style,
              'warnings': ['ASS 样式仅保留字体、字号、主颜色、粗体和斜体；位置、边框、阴影及特效不保证还原。']}
    actor_alias=doc.identity_aliases.get('actors',{}).get(event.name)
    style_alias=doc.identity_aliases.get('styles',{}).get(event.style)
    if actor_alias:
        result['actor'] = actor_alias['name']
        if 'id' in actor_alias: result['actor_source_id'] = actor_alias['id']
    if style_alias: result.update(style_name=style_alias['name'],style_source_id=style_alias['id'])
    if profile:
        result['style'] = ass_style(profile)
        validate_profiles({'imported': {'name': result['style_name'], 'style': result['style']}})
    else:
        result['warnings'].append('ASS 引用的样式定义缺失，已保留名称并使用默认外观。')
    # Preserve the supported whole-cue leading overrides. Mixed-span effects
    # are deliberately not flattened to an inaccurate whole-cue appearance.
    prefix = re.match(r'^(?:\{[^}]*\})*', event.raw_text).group()
    overrides = {}
    for block in re.findall(r'\{([^}]*)\}', prefix):
        for tag in _top_tags(block):
            if tag.startswith('r'):
                reset = tag[1:].strip()
                if not reset: overrides = {}
                elif reset in doc.styles: overrides = ass_style(doc.styles[reset])
                else: result['warnings'].append('句首重置引用了缺失样式，请核对个人覆盖。')
            elif tag.startswith('fn'): overrides['font'] = tag[2:]
            elif re.fullmatch(r'fs\d+(?:\.\d+)?', tag): overrides['size'] = round(float(tag[2:]))
            elif re.fullmatch(r'[bi]-?\d+', tag): overrides[{'b':'bold','i':'italic'}[tag[0]]] = int(tag[1:]) != 0
            elif re.fullmatch(r'(?:1?c)&H[0-9a-fA-F]+&?', tag):
                n = int(tag.split('&H')[1].rstrip('&'), 16)
                overrides['color'] = f'#{n & 255:02X}{(n >> 8) & 255:02X}{(n >> 16) & 255:02X}'
    remaining = event.raw_text[len(prefix):]
    if re.search(r'\{[^}]*(?:\\fn|\\fs|\\[bi]-?\d|\\1?c|\\r)', remaining):
        result['warnings'].append('句内含分段样式覆盖，仅保留句首支持的覆盖属性，请核对。')
    if overrides: result['overrides'] = validate_overrides(overrides)
    return result


def _same_position(a, b):
    return a is None and b is None or (a is not None and b is not None and
            abs(a[0] - b[0]) < 8 and abs(a[1] - b[1]) < 8)


def _without_decoration(raw_text):
    """Remove only explicit border/shadow/blur tags for effect-layer evidence."""
    changed=False
    def block(match):
        nonlocal changed
        tags=_top_tags(match[1])
        if not tags:return match[0]
        kept=[]
        for tag in tags:
            if re.fullmatch(r'(?:[xy]?(?:bord|shad)|blur|be)[+-]?(?:\d+(?:\.\d*)?|\.\d+)',tag):
                changed=True
            else:kept.append(tag)
        return '{'+''.join('\\'+tag for tag in kept)+'}' if kept else ''
    return re.sub(r'\{([^}]*)\}',block,raw_text),changed


def _source_effect_duplicate(doc, earlier, event, previous_metadata, metadata):
    """Cross-style recovery requires actual decorative-layer evidence.

    This is used only for source-lyric recovery, never editable ASS import.
    Exact timing, distinct layers, unchanged text/karaoke/position syntax and
    the same actor are required; style names such as 'Glow' prove nothing.
    """
    if (previous_metadata.get('actor')!=metadata.get('actor') or
            previous_metadata.get('actor_source_id')!=metadata.get('actor_source_id')):
        return False
    # Explicitly exported independent style identities must remain independent.
    left_id=previous_metadata.get('style_source_id');right_id=metadata.get('style_source_id')
    if (left_id or right_id) and left_id!=right_id:return False
    # Event-level margins override style margins. Unknown/nonzero overrides or
    # event columns are not a proven shared position, so keep those events.
    if earlier.margins!=(0,0,0) or event.margins!=(0,0,0):return False
    if earlier.extra_fields or event.extra_fields:return False
    try:
        if int(earlier.layer)==int(event.layer):return False
    except ValueError:return False
    if (earlier.start!=event.start or earlier.end!=event.end or earlier.effect.strip() or event.effect.strip()
            or earlier.raw_text==event.raw_text):return False
    left_style=doc.styles.get(earlier.style,{});right_style=doc.styles.get(event.style,{})
    # Different placement/font definitions are not evidence of one rendered line.
    decoration_fields={'name','outline','shadow','outlinecolour','backcolour'}
    for key in left_style.keys() | right_style.keys():
        if key in decoration_fields:continue
        if left_style.get(key)!=right_style.get(key):return False
    left,left_changed=_without_decoration(earlier.raw_text)
    right,right_changed=_without_decoration(event.raw_text)
    return (left_changed or right_changed) and left==right


def recover_lyrics(doc: AssDocument) -> list[Lyric]:
    """Conservative duplicate union. Distinct text and repeated occurrences survive."""
    lyrics = []
    events_by_id={event.id:event for event in doc.events}
    ordered = sorted(doc.events, key=lambda e: (e.start, e.end, e.id))
    for event in ordered:
        subtitle_metadata = _ass_metadata(doc, event)
        reading_segments=[]
        lyric_text,_,reading_notes=ass_plain(event.raw_text,karaoke_readings=True,
            lyria_escapes=doc.info.get('lyriatextescapes')=='word-joiner-v1',
            _reading_segments=reading_segments)
        line_cursor=0
        for line_index, text in enumerate(lyric_text.split("\n")):
            line_start,line_end=line_cursor,line_cursor+len(text)
            line_cursor=line_end+1
            parts=[dict(piece, start=piece['start']-line_start,end=piece['end']-line_start)
                   for piece in reading_segments
                   if piece['start']>=line_start and piece['end']<=line_end and piece['text']]
            reading=''.join(piece['reading'] or piece['text'] for piece in parts)
            if ''.join(piece['text'] for piece in parts)!=text:
                parts=[];reading=''
            if not normalized(text):
                continue
            position = list(event.position) if event.position else None
            if position:
                # Preserve line identity without inventing exact font metrics.
                position[1] += line_index * 32
            match = None;effect_duplicate=False
            for unit in reversed(lyrics):
                overlap = min(unit.end, event.end) - max(unit.start, event.start)
                ratio = overlap / max(.01, min(unit.end - unit.start, event.end - event.start))
                if (normalized(unit.text) == normalized(text) and ratio >= .7
                        and _same_position(unit.position, position) and None not in event.margins
                        and all(events_by_id[key].margins==event.margins for key in unit.event_ids)):
                    same_metadata=unit.subtitle_metadata==subtitle_metadata
                    effect_duplicate=not same_metadata and any(
                        _source_effect_duplicate(doc,events_by_id[key],event,unit.subtitle_metadata,subtitle_metadata)
                        for key in unit.event_ids)
                    if same_metadata or effect_duplicate:
                        match = unit
                        break
            if match:
                if effect_duplicate:
                    known_layers=[]
                    for key in match.event_ids:
                        try:known_layers.append(int(events_by_id[key].layer))
                        except ValueError:pass
                    if int(event.layer)<min(known_layers):match.subtitle_metadata=deepcopy(subtitle_metadata)
                    match.warnings.append('原词恢复已合并同时间同人物的边框/光晕装饰层；原事件编号保留，编辑字幕导入仍保留各事件。')
                match.start, match.end = min(match.start, event.start), max(match.end, event.end)
                match.event_ids.append(event.id)
                if reading and not match.reading:
                    match.reading,match.reading_segments=reading,parts
                elif reading and match.reading and reading!=match.reading:
                    match.warnings.append('重叠特效事件的注音不同，请核对读音')
                match.warnings = list(dict.fromkeys(match.warnings + event.warnings + reading_notes))
                if "特效重复事件已合并（重叠且同文同位置）" not in match.warnings:
                    match.warnings.append("特效重复事件已合并（重叠且同文同位置）")
            else:
                notes = list(dict.fromkeys(event.warnings + reading_notes))
                if len(event.text.split("\n")) > 1:
                    notes.append('双行/多行字幕已按行拆分，请核对各行起止时间')
                lyrics.append(Lyric(stable_id("lyric", event.id, line_index), text,
                                    event.start, event.end, [event.id], position, notes, reading, parts,
                                    deepcopy(subtitle_metadata)))
    # Suppress positioned per-character overlays only when a complete parent text
    # exists with substantially the same interval and same vertical position.
    suppressed = set()
    for fragment in lyrics:
        if not fragment.position or len(normalized(fragment.text)) > 4:
            continue
        for parent in lyrics:
            if parent is fragment or not parent.position:
                continue
            if (fragment.subtitle_metadata == parent.subtitle_metadata
                    and normalized(fragment.text) in normalized(parent.text)
                    and len(normalized(parent.text)) >= 3 * len(normalized(fragment.text))
                    and parent.start <= fragment.start + .1 and parent.end >= fragment.end - .1
                    and abs(parent.position[1] - fragment.position[1]) < 12):
                parent.event_ids.extend(fragment.event_ids)
                parent.warnings.append('已自动合并同位置的逐字特效层')
                suppressed.add(fragment.id)
                break
    lyrics = [u for u in lyrics if u.id not in suppressed]
    # Explicitly positioned fragments with identical interval and baseline can
    # be reconstructed; ambiguous staggered fragments remain separate for review.
    groups = {}
    for unit in lyrics:
        if unit.position and len(normalized(unit.text)) <= 4:
            key = (round(unit.start, 2), round(unit.end, 2), round(unit.position[1] / 5), repr(unit.subtitle_metadata))
            groups.setdefault(key, []).append(unit)
    combined = []
    for group in groups.values():
        if len(group) < 3 or len({u.position[0] for u in group}) != len(group):
            continue
        group.sort(key=lambda u: u.position[0])
        text = "".join(u.text for u in group)
        combined.append(Lyric(stable_id("lyric", *[u.id for u in group]), text,
                              group[0].start, group[0].end,
                              [eid for u in group for eid in u.event_ids], group[0].position,
                              ['已自动拼接同时间同高度的拆分字词，请核对歌词是否完整'] ))
        combined[-1].subtitle_metadata = deepcopy(group[0].subtitle_metadata)
        if any(u.reading for u in group):
            combined[-1].reading=''.join(u.reading or u.text for u in group)
            cursor=0
            for unit in group:
                for piece in unit.reading_segments:
                    combined[-1].reading_segments.append(dict(piece,
                        start=piece['start']+cursor,end=piece['end']+cursor))
                cursor+=len(unit.text)
        suppressed.update(u.id for u in group)
    lyrics = [u for u in lyrics if u.id not in suppressed] + combined
    for u in lyrics:
        if len(normalized(u.text)) <= 2:
            u.warnings.append('包含极短单字/逐字特效片段，请检查是否需要手动合并')
        elif any(other.id!=u.id and normalized(u.text) in normalized(other.text)
                 and len(normalized(u.text))<len(normalized(other.text))
                 and min(u.end,other.end)>max(u.start,other.start) for other in lyrics):
            u.warnings.append('与相邻字幕时间重叠且文字部分相同（可能是逐字渐显特效），请留意核对')
    return sorted(lyrics, key=lambda u: (u.start, u.position[1] if u.position else 0, u.id))


def parse_lrc(path):
    """Basic LRC: repeated timestamps, global ms offset, explicit blank end tags.

    Ends use the next strictly later timestamp. The last cue end stays unknown
    when no later marker exists; no invented tail. Enhanced word LRC excluded.
    """
    content=read_text(path)
    offset_tags=re.findall(r"\[offset:([+-]?\d+)\]",content,re.I)
    if len(set(offset_tags))>1: raise ValueError('LRC 文件中包含多个冲突的 [offset] 时间偏移标签。')
    offset=int(offset_tags[0])/1000 if offset_tags else 0
    entries=[]
    for line in content.splitlines():
        tags=re.findall(r"\[(\d+):([0-5]\d(?:\.\d{1,3})?)\]",line)
        if not tags:
            if re.search(r"\[\d",line): raise ValueError('LRC 时间标签格式无效。')
            continue
        body=re.sub(r"\[(\d+):([0-5]\d(?:\.\d{1,3})?)\]",'',line).strip()
        if re.search(r"\[\d",body): raise ValueError('LRC 时间标签格式无效。')
        if re.search(r"<\d+:\d",body): raise ValueError('暂不支持逐字时间标签（增强型 LRC），请导入普通逐行 LRC。')
        for minutes,seconds in tags:
            stamp=round(int(minutes)*60+float(seconds)+offset,6)
            if stamp<0: raise ValueError('应用 LRC [offset] 偏移后出现负时间，请检查时间标签。')
            entries.append((stamp,body))
    entries.sort(key=lambda row:row[0])
    times=sorted(set(t for t,_ in entries))
    following={a:b for a,b in zip(times,times[1:])}
    return [(body,stamp,following.get(stamp),None) for stamp,body in entries if body]


def import_translation(path, groups=False) -> list[Translation]:
    path = Path(path)
    fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
    suffix = path.suffix.casefold()
    rows = []
    metadata = []
    if suffix in (".ass", ".ssa"):
        document = parse_ass(path)
        for event in document.events:
            rows.append((event.text, event.start, event.end, None))
            metadata.append(_ass_metadata(document, event))
    elif suffix == ".lrc":
        rows = parse_lrc(path)
    elif suffix == ".srt":
        for block in re.split(r"\n[ \t]*\n", read_text(path).replace("\r\n", "\n").strip("\n")):
            lines = block.splitlines()
            index = next((i for i, x in enumerate(lines) if "-->" in x), None)
            if index is None:
                continue
            timing = re.match(r"\s*(\S+)\s*-->\s*(\S+)", lines[index])
            if not timing:
                raise ValueError("SRT 时间格式无效。")
            body = "\n".join(lines[index + 1:])
            if body.strip():
                rows.append((body, time_seconds(timing[1]), time_seconds(timing[2]), None))
    elif suffix == ".txt":
        text = read_text(path).replace("\r\n", "\n")
        chunks = re.split(r"\n[ \t]*\n", text.strip("\n")) if groups else text.splitlines()
        paragraph_index = 0
        saw_content = False
        for chunk in chunks:
            if not chunk.strip():
                if saw_content:paragraph_index += 1;saw_content = False
                continue
            saw_content = True
            reference = None
            # An explicit TSV pair is never inferred from language alone.
            if "\t" in chunk and "\n" not in chunk:
                reference, chunk = chunk.split("\t", 1)
            rows.append((chunk, None, None, reference))
            metadata.append({'paragraph_index': len(metadata) if groups else paragraph_index,
                             'paragraph_source': 'explicit_txt_blank_lines'})
    else:
        raise ValueError("中文译文支持 TXT、SRT、LRC、ASS、SSA；对照文本使用 原文<TAB>译文。")
    if not rows:
        raise ValueError("译文中没有可用文字。")
    result = []
    for index, (body, start, end, reference) in enumerate(rows):
        if start is not None and not (suffix == ".lrc" and end is None) and not valid_time(start, end):
            raise ValueError(f"译文第 {index + 1} 条时间无效。")
        result.append(Translation(stable_id("zh", fingerprint, index), body, start, end, reference,
                                  deepcopy(metadata[index]) if metadata else {}))
    return result


def import_original(path, groups=False):
    """KTV/ordinary ASS, SRT or untimed TXT; never fabricate reference times."""
    path=Path(path)
    if path.suffix.casefold() in (".ass", ".ssa"):
        document=parse_ass(path)
        return recover_lyrics(document),document.warnings
    rows=import_translation(path,groups)
    units=[Lyric(stable_id("lyric",row.id),row.text,row.start,row.end,[row.id],
                 subtitle_metadata=deepcopy(row.subtitle_metadata)) for row in rows]
    return units,[]
