"""Japanese reading preparation with stable occurrence IDs and unchanged source text.

pykakasi is optional, loaded only inside the external model environment. It is
GPL-3.0-or-later, not bundled with the editor. Automatic readings are suggestions.
"""
from copy import deepcopy
import re
import unicodedata



_DECORATIONS = frozenset('↑↓♪♫♬♡♥❤☆★✧✨')
_KAOMOJI = re.compile(r'[（(₍][^()（）₍₎\n]{0,40}[ωΩДд艸▽▿_＾^ᐢᵔ꒳̫][^()（）₍₎\n]{0,40}[)）₎]')


def reading_input_from_annotations(row):
    """Extract only alignment input; never mutate or remove displayed lyrics.

    Plain brackets are inherently ambiguous. Adjacent kanji + kana/romaji is
    offered for review, not silently declared a definitive ruby annotation.
    """
    text=row.get('text','');provided=row.get('reading','')
    if isinstance(provided,str) and provided.strip():return provided,[],[]
    segments=row.get('reading_segments',[])
    if segments:
        cursor=0;parts=[];valid=True
        for segment in segments:
            if not isinstance(segment,dict):valid=False;break
            a,b=segment.get('start'),segment.get('end')
            if type(a) is not int or type(b) is not int or not cursor<=a<b<=len(text) or text[a:b]!=segment.get('text'):
                valid=False;break
            reading=segment.get('reading','')
            if not isinstance(reading,str):valid=False;break
            parts.extend([text[cursor:a],reading or text[a:b]]);cursor=b
        if valid:return ''.join(parts)+text[cursor:],[dict(kind='provided_segments',count=len(segments))],[]
        # Stale spans cannot authorize replacing a different occurrence.
        return text,[],['已有注音片段与当前原词位置不一致，请重新核对。']
    annotations=[];review=[]
    def ruby(match):
        base,reading=match.group(1),match.group(2)
        annotations.append(dict(kind='ruby',base=base,reading=reading));return reading
    # Explicit Aozora base delimiter may cover kana; implicit base is kanji only.
    value=re.sub(r'｜([^｜《》\n]+)《([^《》\n]+)》',ruby,text)
    value=re.sub(r'([一-鿿々]+)《([^《》\n]+)》',ruby,value)
    def html_ruby(match):
        body=re.sub(r'<rp>.*?</rp>','',match.group(1),flags=re.I|re.S)
        parts=re.fullmatch(r'\s*(?:<rb>)?([^<>]+?)(?:</rb>)?\s*<rt>([^<>]+)</rt>\s*',body,flags=re.I)
        return ruby(parts) if parts else match.group(0)
    value=re.sub(r'<ruby(?:\s[^>]*)?>(.*?)</ruby>',html_ruby,value,flags=re.I|re.S)
    value=re.sub(r'\{([^{}|]+)\|\|([^{}|]+)\}',ruby,value)
    value=re.sub(r'([一-鿿々]+)\|<([ぁ-ゖァ-ヺー]+)',ruby,value)
    def bracket(match):
        base,reading=match.group(1),match.group(2)
        annotations.append(dict(kind='bracket_candidate',base=base,reading=reading))
        review.append('检测到括号读音候选 '+base+'（'+reading+'）；请确认是注音而不是另唱的括号歌词。')
        return reading
    value=re.sub(r'([一-鿿々]+[ぁ-ゖ]*)[（(]([ぁ-ゖァ-ヺー]+|[A-Za-z]+(?:[ \'-][A-Za-z]+)*)[)）]',bracket,value)
    return value,annotations,review


def alignment_input_mapping(row):
    """Map only proven annotation replacements back to original source ranges.

    Unknown/unsupported markup keeps ownership unknown rather than distributing
    phonetic labels uniformly over characters. Latin inside explicit annotation
    is supplied romaji; Latin in ordinary lyrics remains an English loanword.
    """
    text=row['text']
    if row.get('reading'):return None
    value=text;owners=[(i,i+1,False) for i in range(len(text))]
    segments=row.get('reading_segments',[])
    if segments:
        cursor=0;parts=[];mapped=[]
        for seg in segments:
            a,b=seg.get('start'),seg.get('end');reading=seg.get('reading','')
            if type(a) is not int or type(b) is not int or not cursor<=a<b<=len(text) or text[a:b]!=seg.get('text') or not isinstance(reading,str):return None
            parts.extend([text[cursor:a],reading or text[a:b]])
            mapped.extend(owners[cursor:a]);mapped.extend([(a,b,True)]*len(reading) if reading else owners[a:b]);cursor=b
        value=''.join(parts)+text[cursor:];owners=mapped+owners[cursor:]
    else:
        def replace(pattern,flags=0):
            nonlocal value,owners
            parts=[];mapped=[];cursor=0
            for m in re.finditer(pattern,value,flags):
                a,b=m.span(1);owned=owners[a:b]
                if not owned:continue
                parts.extend([value[cursor:m.start()],m.group(2)])
                mapped.extend(owners[cursor:m.start()]);mapped.extend([(min(x[0] for x in owned),max(x[1] for x in owned),True)]*len(m.group(2)));cursor=m.end()
            parts.append(value[cursor:]);mapped.extend(owners[cursor:]);value=''.join(parts);owners=mapped
        replace(r'｜([^｜《》\n]+)《([^《》\n]+)》')
        replace(r'([一-鿿々]+)《([^《》\n]+)》')
        replace(r'<ruby(?:\s[^>]*)?>\s*(?:<rb>)?([^<>]+?)(?:</rb>)?\s*(?:<rp>.*?</rp>\s*)?<rt>([^<>]+)</rt>\s*(?:<rp>.*?</rp>\s*)?</ruby>',re.I|re.S)
        replace(r'\{([^{}|]+)\|\|([^{}|]+)\}')
        replace(r'([一-鿿々]+)\|<([ぁ-ゖァ-ヺー]+)')
        replace(r"([一-鿿々]+[ぁ-ゖ]*)[（(]([ぁ-ゖァ-ヺー]+|[A-Za-z]+(?:[ \'-][A-Za-z]+)*)[)）]")
    clean,kept=temporary_reading_input(value)
    if clean!=row['reading_input']:return None
    return [owners[i] for i in kept]


def _convert_preserving_romaji(text,converter):
    pieces=[]
    for part in re.split(r"([A-Za-z]+(?:['-][A-Za-z]+)*)",text):
        if not part:continue
        if re.fullmatch(r"[A-Za-z]+(?:['-][A-Za-z]+)*",part):pieces.append(dict(hepburn=part,source='provided_latin'))
        else:pieces.extend(converter(part))
    return pieces


def temporary_reading_input(text):
    """Idempotent pronunciation view with ownership in the untouched source.

    Repeated bounded-face removal handles nested wrappers in this one call.
    Only fullwidth Latin is folded, preserving Japanese source indexing.
    """
    value=text
    owners=list(range(len(text)))
    while True:
        removed=set()
        for match in _KAOMOJI.finditer(value):
            body=match.group()[1:-1]
            face_letters=frozenset('ωΩДд艸ᐢᵔ꒳')
            letters=[c for c in body if c.isalnum()]
            ascii_face=body.strip() in {'o_o','O_O','o_O','O_o','T_T','^o^','^O^','つд⊂'}
            if not ascii_face and (any(c not in face_letters for c in letters) or len(letters)>2):
                continue
            if not ascii_face and letters and not any(c in '._＾^≧≦▽▿・̫' for c in body):
                continue
            removed.update(range(*match.span()))
        result=[];kept=[];voiced=False
        for i,c in enumerate(value):
            if i in removed:continue
            if 'Ａ'<=c<='Ｚ' or 'ａ'<=c<='ｚ':
                c=unicodedata.normalize('NFKC',c)
            if c in '~〜～':
                if voiced:result.append('ー');kept.append(owners[i])
                continue
            if c in _DECORATIONS or c=='・' or unicodedata.category(c).startswith('S'):
                continue
            result.append(c);kept.append(owners[i])
            # Latin / romaji is emitted outside the kana stream. A trailing
            # wave must not create an orphan long-vowel token in that stream.
            if c.isascii() and c.isalpha():voiced=False
            elif c.isalnum() or ('ぁ'<=c<='ヿ' and c not in '・ー'):voiced=True
            elif not c.isspace() and c not in 'ーｰ':voiced=False
        cleaned=''.join(result)
        if cleaned==value:return cleaned,kept
        value,owners=cleaned,kept


def pure_symbol_row(row):
    text,_=temporary_reading_input(row.get('text',''))
    return bool(row.get('text','').strip()) and not any(c not in 'ーｰ・' and (c.isalnum() or 'ぁ'<=c<='ヿ') for c in text)


def scan_reading_inputs(rows):
    """Full-song scan using the same temporary input as actual conversion."""
    output=[];issues=[]
    for index,row in enumerate(rows,1):
        item=deepcopy(row);supplied=row.get('reading','')
        supplied=supplied if isinstance(supplied,str) else ''
        before,annotations,annotation_review=reading_input_from_annotations(row)
        after,_=temporary_reading_input(before) if not supplied.strip() else (before,[])
        item['reading_annotations']=annotations
        found=[deepcopy(x) for x in row.get('reading_issues',[])
               if row.get('reading_origin')!='manual' and (x.get('hard_error') or
                   (x.get('requires_review') and x.get('kind') not in ('elongation','uncertain_emoticon','empty_after_decoration','stale_confirmation'))) ]
        def report(kind,message,review=False):
            found.append(dict(index=index,id=row.get('id'),kind=kind,before=before,after=after,
                              message=message,requires_review=review))
        if pure_symbol_row(row):
            found=[];report('nonvocal_symbols','纯符号/颜文字，自动跳过对时；原样保留歌词行。')
            item['alignment_skip']='nonvocal_symbols'
        else:
            item.pop('alignment_skip',None)
            if row.get('reading_confirmed_for') is not None and not reading_is_confirmed(row):
                report('stale_confirmation','原词或读音已改变，请重新确认当前内容。',True)
            for message in annotation_review:report('annotation_review',message,True)
            if after!=before:report('temporary_normalization','仅注音临时副本移除颜文字/装饰并规范长音，原词不变。')
            if re.search(r'[0-9０-９]',before):report('spoken_number','数字按实际演唱读法转换，不删除。')
        item['reading_input']=after;item['reading_issues']=found
        output.append(item);issues.extend(found)
    return dict(rows=output,issues=issues,counts=dict(rows=len(output),issues=len(issues),review=sum(bool(x.get('requires_review')) for x in issues)))


def reading_is_confirmed(row):
    return row.get('reading_confirmed_for') in ([row.get('text',''),row.get('reading','')],(row.get('text',''),row.get('reading','')))


def invalid_reading_rows(rows):
    """Collect every issue rather than stopping at the first bad line."""
    if __package__:
        from .experimental_nextfire_alignment import normalize_reading
    else:
        from experimental_nextfire_alignment import normalize_reading
    issues=[]
    rows=scan_reading_inputs(rows)['rows']
    for index,row in enumerate(rows,1):
        if pure_symbol_row(row):continue
        reading=row.get('reading','')
        try:normalize_reading(reading)
        except ValueError as exc:
            issues.append(dict(index=index,id=row.get('id'),kind='invalid_labels',message='读音为空、含数字或未转换字符：'+str(exc)))
        hard=[x['message'] for x in row.get('reading_issues',[]) if x.get('hard_error')]
        if hard and row.get('reading_origin')!='manual':
            issues.append(dict(index=index,id=row.get('id'),kind='invalid_labels',message='；'.join(hard)))
        pending=[x['message'] for x in row.get('reading_issues',[]) if x.get('requires_review')]
        stale=row.get('reading_confirmed_for') is not None and not reading_is_confirmed(row)
        if pending and not reading_is_confirmed(row) and (row.get('reading_origin')!='manual' or stale):
            issues.append(dict(index=index,id=row.get('id'),kind='review_input',message='；'.join(pending)))
    return issues

def prepare_reading_rows(rows, converter=None, *, cancel=lambda: False, tool='pykakasi', reset=False):
    if __package__:
        from .local_models import check_cancel
    else:
        from local_models import check_cancel
    check_cancel(cancel)
    if not rows or len(rows) > 1000:
        raise ValueError('请选择 1 至 1000 句原词。')
    if len({row.get('id') for row in rows}) != len(rows):
        raise ValueError('歌词句子标识重复，请重新导入。')
    if tool not in ('pykakasi','sudachi','provided'):raise ValueError('未知日语读音工具。')
    result = []
    if reset:
        rows=deepcopy(rows)
        for row in rows:
            for key in ('reading','reading_origin','reading_confirmed_for','generated_reading','reading_issues','alignment_units'):row.pop(key,None)
    rows=deepcopy(rows)
    for row in rows:
        if row.get('reading_origin')=='automatic_suggestion' and not reading_is_confirmed(row) and any(x.get('hard_error') for x in row.get('reading_issues',[])):
            row.pop('reading',None);row.pop('reading_confirmed_for',None);row['reading_issues']=[]
    scanned=scan_reading_inputs(rows)
    for row in scanned['rows']:
        check_cancel(cancel)
        if not isinstance(row.get('id'), str) or not row['id'] or not isinstance(row.get('text'), str):
            raise ValueError('歌词原文或句子标识无效。')
        item = deepcopy(row)
        provided = row.get('reading') if isinstance(row.get('reading'), str) else ''
        if pure_symbol_row(row):
            item.update(alignment_skip='nonvocal_symbols',reading='');result.append(item);continue
        if provided.strip() and (reading_is_confirmed(row) or (row.get('reading_origin')=='manual' and row.get('reading_confirmed_for') is None)):
            # Confirmation is bound to exact text and reading, and survives a
            # repeated fill without regenerating or changing original text.
            result.append(item);continue
        if row.get('reading_confirmed_for') is not None:
            item.pop('reading_confirmed_for',None)
        if tool=='provided':result.append(item);continue
        if provided.strip() and not re.search(r'[ぁ-ヿ一-鿿]', provided):
            item['reading_origin'] = row.get('reading_origin', 'provided')
            result.append(item)
            continue
        if tool=='sudachi':
            if __package__:
                from .sudachi_readings import convert_sudachi
            else:
                from sudachi_readings import convert_sudachi
            ownership=alignment_input_mapping(row)
            literal=[i for i,x in enumerate(ownership or []) if x[2]]
            try:
                converted=convert_sudachi(row['reading_input'],literal_latin=literal)
            except ValueError as exc:
                converted=dict(reading='',units=[],spelling='lyria-kana-alignment-v1',issues=[dict(kind='unconverted_input',message=str(exc),hard_error=True)])
            item['reading']=converted['reading'];item['generated_reading']=converted['reading']
            item['reading_origin']='automatic_suggestion'
            item['reading_tool']='sudachi';item['reading_conversion']=dict(spelling=converted['spelling'])
            item['reading_issues'].extend(dict(x,id=row['id'],requires_review=bool(x.get('requires_review'))) for x in converted['issues'])
            item['alignment_units']=converted['units']
            for unit in item['alignment_units']:
                a,b=unit['source_start'],unit['source_end']
                owned=ownership[a:b] if ownership is not None else []
                if owned:
                    unit.update(source_start=min(x[0] for x in owned),source_end=max(x[1] for x in owned))
                    unit['source_text']=row['text'][unit['source_start']:unit['source_end']]
                    if any(x[2] for x in owned):unit['ownership']='provided_segment'
                elif row['reading_input']!=row['text']:
                    unit.update(source_start=None,source_end=None,ownership='annotation_input')
            result.append(item);continue
        if converter is None:
            try:
                import pykakasi
                converter = pykakasi.kakasi().convert
            except ImportError as exc:
                raise RuntimeError('自动注音需要独立模型环境中的 pykakasi 2.3.0（GPL-3.0-or-later）；请安装后再试，或逐句填写罗马字读音。') from exc
        # Aozora ruby is explicit source reading; preserve exact source text.
        text = row['reading_input']
        if provided:
            item['source_reading'] = provided
        pieces = _convert_preserving_romaji(text,converter)
        check_cancel(cancel)
        if not isinstance(pieces, list) or any(not isinstance(p, dict) or not isinstance(p.get('hepburn'), str) for p in pieces):
            raise ValueError('自动注音返回格式异常。')
        # pykakasi may expose long vowels as punctuation; retain the actual
        # kana vowel duration when that phonetic result is available.
        if __package__:
            from .alignment_romaji import romanize_kana
        else:
            from alignment_romaji import romanize_kana
        for piece in pieces:
            kana=piece.get('hira') or piece.get('kana','')
            if 'ー' in kana:
                try:piece['hepburn']=romanize_kana(kana)
                except ValueError:pass
        item['reading'] = ' '.join(p['hepburn'] for p in pieces)
        item['generated_reading']=item['reading']
        item['reading_origin'] = 'provided_ruby_converted' if provided else 'automatic_suggestion'
        item['reading_conversion'] = deepcopy(pieces)
        item['reading_warning'] = '自动读音可能影响对齐准确率，请检查并纠正；不会替换原歌词。'
        result.append(item)
    check_cancel(cancel)
    return result
