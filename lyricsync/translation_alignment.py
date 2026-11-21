"""Offline structural translation proposals. No semantic correctness claims."""
from difflib import SequenceMatcher
import re
from .model import normalized,valid_time

INTERJECTIONS={'oh','ah','yeah','lalala','lala','la','hey','wow','ああ','ねえ','おー','はい','啦','啊','哦','喔','耶','嘿'}

def interjection(text):
    # Closed vocabulary, not a language/length classifier: ordinary short lyrics
    # and arbitrary English sentences must never be silently treated as cheers.
    import unicodedata
    phrase = unicodedata.normalize('NFKC', text).lower()
    separators = r"[~〜～ー↑↓↗↘!！?？\s'’.,、。]+"
    value = re.sub(separators, '', phrase)
    if re.fullmatch(r'[あぁアァ][~〜～ー↑↓↗↘个]*', unicodedata.normalize('NFKC', text)):
        return True  # narrow observed arrow-as-个 OCR variant; evidence unchanged
    known = {normalized(x) for x in INTERJECTIONS} | {
        'あ', 'ああ', 'ぁ', 'いえ', 'いぇ', 'いえい', 'いぇい',
        'うお', 'うおお', 'おお', 'れっつご', 'レッツゴ',
        'letsgo', 'woo', 'woah', 'wow', 'yeah', 'yay', 'hey', 'oh', 'ah'}
    if value in known:
        return True
    # Only isolated, individually recognised exclamations form a compound cheer.
    # A Japanese vowel with an explicit elongation also counts as a token.
    tokens = re.sub(r'(?<![ぁ-んァ-ン一-龯])([おオ])[~〜～ー]+', ' oh ', phrase)
    words = [word for word in re.split(separators, tokens) if word]
    return len(words) >= 2 and all(word in known for word in words)


def source_cheer(row):
    return getattr(row, 'interjection_evidence', False) or interjection(row.text)


def crosses_interlude(rows, seconds=8.0):
    # A source-only long gap is a grouping barrier, not a bilingual semantic
    # anchor: without translated timestamps it cannot prove which stanza aligns.
    return any(valid_time(a.start, a.end) and valid_time(b.start, b.end)
               and b.start - a.end > seconds for a, b in zip(rows, rows[1:]))

def paragraph(row):return getattr(row,'subtitle_metadata',{}).get('paragraph_index')

def repetition_topology(rows, *, translation=False):
    """Within-language self-similarity matrix and ordered occurrence signatures.

    Cross-language scores compare graph structure, never spelling across languages.
    Near matches require >=4 characters to avoid treating short unrelated lines as
    equivalent. These remain soft evidence: translations may paraphrase choruses.
    """
    texts=[normalized((getattr(x,'reference',None) or x.text) if translation else x.text) for x in rows]
    size=len(texts);matrix=[[False]*size for _ in texts]
    for i,left in enumerate(texts):
        for j in range(i,size):
            right=texts[j]
            equal=bool(left and right) and (left==right or (min(len(left),len(right))>=4 and SequenceMatcher(None,left,right,autojunk=False).ratio()>=.9))
            matrix[i][j]=matrix[j][i]=equal
    signatures=[]
    for i,row in enumerate(matrix):
        peers=[j for j,match in enumerate(row) if match]
        gaps=[(b-a)/max(1,size-1) for a,b in zip(peers,peers[1:])]
        # Repeated-neighbour edges preserve local chorus shape, not lexical labels.
        neighbours=[]
        for j in peers:
            if j==i:continue
            neighbours.append(sum(0<=i+d<size and 0<=j+d<size and matrix[i+d][j+d] for d in (-1,1)))
        signatures.append({'peers':peers,'rank':peers.index(i) if i in peers else -1,'gaps':gaps,'context':sum(neighbours)})
    return {'matrix':matrix,'signatures':signatures}


def topology_score(left,right):
    a,b=len(left['peers']),len(right['peers'])
    if a<2 or b<2:return 0.,False
    if a!=b:return -.05,False  # paraphrased repeats are not forced into equal classes
    if left['rank']!=right['rank']:return -.45,False
    gap_error=sum(abs(x-y) for x,y in zip(left['gaps'],right['gaps']))/max(1,len(left['gaps']))
    if gap_error>.2:return 0.,False
    context=min(left['context'],right['context'])
    return .18+.12*min(1,context/2),True


def short_length_anomaly(a,b):
    left=len(normalized(''.join(x.text for x in a)))
    right=len(normalized(''.join((getattr(x,'reference',None) or x.text) for x in b)))
    return bool(left and right and min(left,right)<=5 and max(left,right)>=10 and max(left,right)/min(left,right)>=4)


def _features(a,b,li,ti,n,m,source_topology,target_topology):
    left=''.join(x.text for x in a);right=''.join(x.reference or x.text for x in b)
    shared=set(re.findall(r'[\u4e00-\u9fff]|[a-zA-Z]{2,}',left.lower())) & set(re.findall(r'[\u4e00-\u9fff]|[a-zA-Z]{2,}',right.lower()))
    sa=all(source_cheer(x) for x in a);sb=all(interjection(x.reference or x.text) for x in b)
    lexical=min(.45,.15*len(shared))
    structural=.65-.35*abs((li+len(a)/2)/max(1,n)-(ti+len(b)/2)/max(1,m))
    if sa != sb:structural-=.8
    if all(valid_time(x.start,x.end) for x in a+b):
        low=max(min(x.start for x in a),min(x.start for x in b));high=min(max(x.end for x in a),max(x.end for x in b))
        union=max(x.end for x in a+b)-min(x.start for x in a+b)
        structural+=1.2*max(0,high-low)/max(.001,union)
    if len(a)==len(b)==1:
        bonus,_=topology_score(source_topology['signatures'][li],target_topology['signatures'][ti])
        structural+=bonus
    # Extreme short/long disagreement is a review signal, not proof of mistranslation.
    if short_length_anomaly(a,b) and sa!=sb:structural-=.2
    return structural+lexical-.30*(len(a)+len(b)-2),bool(shared)

def structural_groups(lyrics,translations,order_confirmed=False):
    n,m=len(lyrics),len(translations);width=max(12,abs(n-m)+3)
    src=repetition_topology(lyrics);dst=repetition_topology(translations,translation=True)
    dp={(0,0):(0.,None)}
    def put(i,j,v,back):
        if n and abs(j-i*m/n)>width and (i,j)!=(n,m):return
        if (i,j) not in dp or v>dp[i,j][0]+1e-9:dp[i,j]=(v,back)
    for i in range(n+1):
        for j in range(m+1):
            if (i,j) not in dp:continue
            v=dp[i,j][0]
            if i<n:put(i+1,j,v-(.12 if source_cheer(lyrics[i]) else .65),(i,j,1,0,False))
            if j<m:put(i,j+1,v-(.12 if interjection(translations[j].text) else .85),(i,j,0,1,False))
            for a in range(1,min(3,n-i)+1):
                for b in range(1,min(3,m-j)+1):
                    ls,ts=lyrics[i:i+a],translations[j:j+b]
                    if crosses_interlude(ls):continue
                    # A cheer cannot absorb a substantive translation merely
                    # because matching neighbouring lines as 2:1 scores higher.
                    source_cheers = [source_cheer(x) for x in ls]
                    target_cheers = [interjection(x.reference or x.text) for x in ts]
                    if (any(source_cheers) and not all(source_cheers)
                            or any(target_cheers) and not all(target_cheers)
                            or all(source_cheers) != all(target_cheers)):
                        continue
                    if any(len({paragraph(x) for x in rows if paragraph(x) is not None})>1 for rows in (ls,ts)):continue
                    score,shared=_features(ls,ts,i,j,n,m,src,dst)
                    if a > b and not (shared or order_confirmed or all(source_cheers)
                            or all(valid_time(x.start, x.end) for x in ls + ts)):
                        continue
                    put(i+a,j+b,v+score,(i,j,a,b,shared))
    result=[];cursor=(n,m)
    if cursor not in dp:return [([],translations,0.,['结构配对超出安全范围，保留译文未定位'])]
    while cursor!=(0,0):
        i,j,a,b,shared=dp[cursor][1]
        if b:
            ls,ts=lyrics[i:i+a],translations[j:j+b]
            reasons=['离线结构候选：没有跨语言句意依据，不是跨语言语义确认，请核对句意']
            if not a:reasons+=['译文未绑定原词；不挤入相邻时间']
            elif a!=1 or b!=1:reasons+=['原词与译文多对多分组，时间独立来自画面证据']
            if shared:reasons+=['存在汉字/英文共现线索，不作为单独硬锚点']
            if any(interjection(x.text) for x in ls+ts):reasons+=['短语气词局部核对']
            if a==b==1:
                _,topology_match=topology_score(src['signatures'][i],dst['signatures'][j])
                if topology_match:reasons+=['原词/译文自相似矩阵的重复次数、遍次与间距拓扑相符；仅结构软线索，非语义硬锚点']
            if a and short_length_anomaly(ls,ts):reasons+=['极短句长度比异常：可能是单侧语气词或意译，请人工核对，不自动删除']
            result.append((ls,ts,.5 if a else 0.,reasons))
        cursor=(i,j)
    return list(reversed(result))

def paragraph_groups(lyrics,translations,order_confirmed=False):
    # Only matching explicit paragraph counts establish isolated paragraph blocks.
    def blocks(rows):
        out=[]
        for row in rows:
            p=paragraph(row)
            if p is None:return None
            if not out or out[-1][0]!=p:out.append((p,[]))
            out[-1][1].append(row)
        return out
    a,b=blocks(lyrics),blocks(translations)
    if a and b and len(a)==len(b) and len(a)>1:
        result=[]
        for (_,ls),(_,ts) in zip(a,b):
            for gl,gt,c,notes in structural_groups(ls,ts,order_confirmed):
                result.append((gl,gt,c,notes+['按双方显式空行段落隔离；段内仍需确认']))
        return result
    return structural_groups(lyrics,translations,order_confirmed)
