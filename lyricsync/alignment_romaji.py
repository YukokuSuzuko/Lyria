"""Independent kana-to-alignment spelling, written from linguistic rules.

No SUG/FA-Kara implementation or dictionary data is copied. Source offsets refer
only to input kana; callers retain morpheme ownership where character allocation
cannot be proven. This module has no optional/GPL dependency.
"""
import re
import unicodedata

_ROWS=(('あいうえお','a i u e o'),('かきくけこ','ka ki ku ke ko'),
 ('さしすせそ','sa shi su se so'),('たちつてと','ta chi tsu te to'),
 ('なにぬねの','na ni nu ne no'),('はひふへほ','ha hi fu he ho'),
 ('まみむめも','ma mi mu me mo'),('やゆよ','ya yu yo'),('らりるれろ','ra ri ru re ro'),
 ('わゐゑを','wa i e o'),('がぎぐげご','ga gi gu ge go'),('ざじずぜぞ','za ji zu ze zo'),
 ('だぢづでど','da ji zu de do'),('ばびぶべぼ','ba bi bu be bo'),('ぱぴぷぺぽ','pa pi pu pe po'),
 ('ぁぃぅぇぉゃゅょゎゕゖゔ','a i u e o ya yu yo wa ka ke vu'))
SINGLE={k:v for kana,romaji in _ROWS for k,v in zip(kana,romaji.split())}
PAIRS={}
for k,prefix in [('き','ky'),('ぎ','gy'),('し','sh'),('じ','j'),('ち','ch'),('ぢ','j'),('に','ny'),('ひ','hy'),('び','by'),('ぴ','py'),('み','my'),('り','ry')]:
 for small,vowel in zip('ゃゅょ','auo'):PAIRS[k+small]=prefix+vowel
PAIRS.update({'しぇ':'she','じぇ':'je','ちぇ':'che','てぃ':'ti','でぃ':'di','とぅ':'tu','どぅ':'du',
 'てゅ':'tyu','でゅ':'dyu','ふぁ':'fa','ふぃ':'fi','ふぇ':'fe','ふぉ':'fo','ふゅ':'fyu',
 'うぃ':'wi','うぇ':'we','うぉ':'wo','いぇ':'ye','ゔぁ':'va','ゔぃ':'vi','ゔぇ':'ve','ゔぉ':'vo',
 'くぁ':'kwa','くぃ':'kwi','くぇ':'kwe','くぉ':'kwo','ぐぁ':'gwa','つぁ':'tsa','つぃ':'tsi','つぇ':'tse','つぉ':'tso'})
STRUCTURAL=set('~〜～↑↓♪♫♬♡♥❤☆★✧✨')
# Independently authored common loanword pronunciations, not an imported lexicon.
ENGLISH_KANA={'love':'ラブ','stage':'ステージ','night':'ナイト','check':'チェック','take':'テイク',
 'baby':'ベイビー','you':'ユー','your':'ユア','my':'マイ','me':'ミー','i':'アイ','we':'ウィー',
 'dream':'ドリーム','dreams':'ドリームズ','happy':'ハッピー','yeah':'イェー','yes':'イエス','no':'ノー',
 'hello':'ハロー','goodbye':'グッバイ','world':'ワールド','music':'ミュージック','heart':'ハート',
 'sweet':'スイート','time':'タイム','forever':'フォーエバー','dance':'ダンス','dancing':'ダンシング',
 'star':'スター','stars':'スターズ','shine':'シャイン','smile':'スマイル','one':'ワン','two':'トゥー',
 'three':'スリー','four':'フォー','five':'ファイブ','six':'シックス','seven':'セブン','eight':'エイト',
 'nine':'ナイン','ten':'テン','oh':'オー','wow':'ワオ','hey':'ヘイ','go':'ゴー','let':'レット',
 'lets':'レッツ',"let's":'レッツ','all':'オール','right':'ライト','light':'ライト','blue':'ブルー',
 'color':'カラー','red':'レッド','white':'ホワイト','black':'ブラック','so':'ソー','and':'アンド',
 'the':'ザ','to':'トゥー','be':'ビー','with':'ウィズ','for':'フォー','in':'イン','on':'オン',
 'out':'アウト','up':'アップ','down':'ダウン','get':'ゲット','like':'ライク','feel':'フィール',
 'beautiful':'ビューティフル','shine':'シャイン','fly':'フライ','high':'ハイ','life':'ライフ'}


def hiragana(text):
    value=unicodedata.normalize('NFKC',text)
    return ''.join(chr(ord(c)-0x60) if 'ァ'<=c<='ヶ' else c for c in value)


def kana_alignment_units(text):
    value=hiragana(text);units=[];i=0
    while i<len(value):
        c=value[i];end=i+1;reading=''
        if c=='っ':
            nxt=PAIRS.get(value[i+1:i+3],SINGLE.get(value[i+1:i+2],''))
            reading='t' if nxt.startswith('ch') else nxt[0] if nxt and nxt[0] not in 'aeioun' else 'h'
        elif c=='ん':
            nxt=PAIRS.get(value[i+1:i+3],SINGLE.get(value[i+1:i+2],''))
            reading="n'" if nxt.startswith(tuple('aeiouy')) else 'n'
        elif c in 'ーｰ':
            previous=''.join(u['reading'] for u in units)
            vowels=[v for v in previous if v in 'aeiou']
            if not vowels:raise ValueError('Long-vowel mark has no preceding vowel')
            reading=vowels[-1]
        elif value[i:i+2] in PAIRS:reading=PAIRS[value[i:i+2]];end=i+2
        elif c in SINGLE:reading=SINGLE[c]
        elif c in STRUCTURAL or c.isspace() or unicodedata.category(c).startswith('P'):pass
        else:raise ValueError('Unconverted kana input: '+repr(c))
        units.append(dict(start=i,end=end,kana=value[i:end],reading=reading));i=end
    return units


def romanize_kana(text):
    return ''.join(u['reading'] for u in kana_alignment_units(text))


def japanese_integer(value):
    value=int(value)
    if not 0<=value<100000000:raise ValueError('Number requires explicit sung reading')
    if value==0:return '零'
    digits='零一二三四五六七八九'
    def small(n):
        result=''
        for power,label in ((1000,'千'),(100,'百'),(10,'十'),(1,'')):
            q,n=divmod(n,power)
            if q:result+=(digits[q] if q!=1 or power==1 else '')+label
        return result
    q,r=divmod(value,10000)
    return (small(q)+'万' if q else '')+small(r)


def expand_numbers(text):
    """Return temporary expanded text plus source ownership for every character."""
    parts=[];owners=[];cursor=0
    for m in re.finditer(r'([0-9０-９]+)([人日]?)',text):
        parts.append(text[cursor:m.start()]);owners.extend((i,i+1) for i in range(cursor,m.start()))
        number=int(unicodedata.normalize('NFKC',m.group(1)));counter=m.group(2)
        special={('人',1):'ひとり',('人',2):'ふたり',**{('日',n):v for n,v in {1:'ついたち',2:'ふつか',3:'みっか',4:'よっか',5:'いつか',6:'むいか',7:'なのか',8:'ようか',9:'ここのか',10:'とおか',14:'じゅうよっか',20:'はつか',24:'にじゅうよっか'}.items()}}
        replacement=special.get((counter,number),japanese_integer(number)+counter)
        parts.append(replacement);owners.extend([(m.start(),m.end())]*len(replacement));cursor=m.end()
    parts.append(text[cursor:]);owners.extend((i,i+1) for i in range(cursor,len(text)))
    return ''.join(parts),owners
