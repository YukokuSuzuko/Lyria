"""Optional Sudachi adapter: morphology and independently authored spelling only.

No model, editor state, network, SUG code or GPL converter is imported here.
"""
import re
import unicodedata
if __package__:
    from .alignment_romaji import (ENGLISH_KANA,STRUCTURAL,expand_numbers,hiragana,kana_alignment_units)
else:
    from alignment_romaji import (ENGLISH_KANA,STRUCTURAL,expand_numbers,hiragana,kana_alignment_units)


def create_tokenizer():
    from importlib.metadata import version
    import sudachipy,sudachidict_small,sys
    from pathlib import Path
    root = Path(sys._MEIPASS).resolve() / 'reading_bundle' if getattr(sys, 'frozen', False) else Path(sys.prefix).resolve()
    if not all(Path(m.__file__).resolve().is_relative_to(root) for m in (sudachipy,sudachidict_small)):
        raise RuntimeError('Sudachi 与字典必须来自所选独立注音环境。')
    if version('sudachipy')!='0.6.11' or version('sudachidict_small')!='20260116':
        raise RuntimeError('Sudachi 生产适配需要固定版本 0.6.11；请在独立环境安装匹配版本。')
    return sudachipy.Dictionary(dict='small').create(),sudachipy.SplitMode.A


def convert_sudachi(text,tokenizer=None,*,literal_latin=()):
    tokenizer,mode=create_tokenizer() if tokenizer is None else tokenizer
    expanded,owners=expand_numbers(text);units=[];issues=[];previous_kana='';kana_stream=[];kana_owners=[];structural=[]
    # Isolate written Latin words so a dictionary cannot silently spell an OOV
    # acronym as Japanese. Kana loanwords are explicit, reviewable suggestions.
    spans=list(re.finditer(r"[A-Za-z]+(?:'[A-Za-z]+)*|[^A-Za-z]+",expanded))
    for match in spans:
        fragment=match.group();base=match.start()
        if re.fullmatch(r"[A-Za-z]+(?:'[A-Za-z]+)*",fragment):
            if all(i in literal_latin for a,b in owners[base:match.end()] for i in range(a,b)):
                owned=owners[base:match.end()];start=min(x[0] for x in owned);end=max(x[1] for x in owned)
                structural.append(dict(source_start=start,source_end=end,source_text=text[start:end],kana='',reading=fragment.lower(),checkpoint=0,ownership='provided_segment'))
                continue
            kana=ENGLISH_KANA.get(fragment.casefold())
            morphs=[(0,len(fragment),fragment,kana,'english_loanword')]
            if kana is None:
                issues.append(dict(kind='unknown_english',message='英文词 '+fragment+' 尚无表音词条，请填写实际演唱读音。',hard_error=True))
                continue
        else:
            morphs=[]
            for m in tokenizer.tokenize(fragment,mode):
                surface=m.surface();kana=m.reading_form();kind='morpheme'
                original=text[owners[base+m.begin()][0]:owners[base+m.end()-1][1]]
                numeric_origin=any(c.isdigit() for c in original)
                if numeric_origin and all('ぁ'<=c<='ゖ' for c in surface):kana=surface
                if not numeric_origin and m.part_of_speech()[0]=='助詞' and surface in ('は','へ','を'):
                    kana={'は':'ワ','へ':'エ','を':'オ'}[surface]
                if surface=='々' and previous_kana:kana=previous_kana
                if all(c.isspace() or c in STRUCTURAL or unicodedata.category(c).startswith('P') for c in surface):kana='';kind='structural'
                morphs.append((m.begin(),m.end(),surface,kana,kind))
        for a,b,surface,kana,kind in morphs:
            owned=owners[base+a:base+b]
            start=min((x[0] for x in owned),default=0);end=max((x[1] for x in owned),default=start)
            if kana:previous_kana=kana
            # Build one continuous kana sequence so a sokuon/long vowel at a
            # morphology boundary sees the following/preceding actual mora.
            precise=hiragana(surface)==hiragana(kana) and len(surface)==len(kana) and end-start==len(surface)
            if not kana:
                structural.append(dict(source_start=start,source_end=end,source_text=text[start:end],kana='',reading='',checkpoint=0,ownership='structural'))
            for offset,char in enumerate(kana or ''):
                kana_stream.append(char)
                kana_owners.append(dict(source_start=start+offset if precise else start,
                    source_end=start+offset+1 if precise else end,ownership='kana' if precise else kind))
    # Reject unsupported dictionary output locally, preserving every source span;
    # never keep ASCII fragments from inside an untranscribed kaomoji.
    try:
        parsed=kana_alignment_units(''.join(kana_stream))
    except ValueError as exc:
        issues.append(dict(kind='unconverted_dictionary_reading',message=str(exc),hard_error=True));parsed=[]
    for checkpoint,part in enumerate(parsed):
        owned=kana_owners[part['start']:part['end']]
        start=min(x['source_start'] for x in owned);end=max(x['source_end'] for x in owned)
        ownership=owned[0]['ownership'] if all(x['ownership']==owned[0]['ownership'] for x in owned) else 'morpheme'
        units.append(dict(source_start=start,source_end=end,source_text=text[start:end],kana=part['kana'],reading=part['reading'],checkpoint=checkpoint,ownership=ownership))
    units.extend(structural)
    units.sort(key=lambda u:(u['source_start'],u['checkpoint']))
    return dict(reading=''.join(u['reading'] for u in units),units=units,issues=issues,
        tool='sudachi',spelling='lyria-kana-alignment-v1')
