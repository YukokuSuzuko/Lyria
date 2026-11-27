"""Explicit bilingual partitioning. Detection is advisory, never destructive."""
import re,uuid
from copy import deepcopy
from lyricsync.model import Lyric,Translation,Project

def suspected_translation(text):
    return bool(re.search(r'[的了在我你这那着吗]',text)) and bool(re.search(r'[一-鿿]',text)) and not re.search(r'[ぁ-ヿ]',text)

def partition(lines,mode):
    if mode=='tab':
        if any(line.count('\t')!=1 for line in lines):raise ValueError('每行需恰好一个 TAB，左侧原词、右侧译文。')
        pairs=[line.split('\t') for line in lines]
    elif mode=='alternating':
        if len(lines)%2:raise ValueError('交替双语须为偶数行；未删除或猜测最后一行。')
        pairs=list(zip(lines[::2],lines[1::2]))
    else:raise ValueError('未知的分流方式。')
    if not pairs or any(not a.strip() or not b.strip() for a,b in pairs):raise ValueError('原词或译文不能为空。')
    rows=[];staged=[]
    for source,translation in pairs:
        lid='split-source-'+uuid.uuid4().hex;tid='split-translation-'+uuid.uuid4().hex
        rows.append(dict(id=lid,text=source,lyric_ids=[lid]))
        staged.append(dict(lyric_id=lid,source=source,translation_id=tid,translation=translation))
    return rows,staged

def commit_partition(document,history,pairs):
    if not pairs:return False
    draft=Project.from_dict(document.project.to_dict());lyrics={x.id:x for x in draft.lyrics};translations={x.id:x for x in draft.translations};changed=False
    for pair in pairs:
        lid=pair.get('lyric_id');tid=pair['translation_id']
        if lid:
            if lid in lyrics and lyrics[lid].text!=pair['source']:raise ValueError('分流原词身份冲突。')
            if lid not in lyrics:
                item=Lyric(lid,pair['source'],None,None);draft.lyrics.append(item);lyrics[lid]=item;changed=True
        if tid in translations and translations[tid].text!=pair['translation']:raise ValueError('分流译文身份冲突。')
        if tid not in translations:
            item=Translation(tid,pair['translation']);draft.translations.append(item);translations[tid]=item;changed=True
        if lid and not any(lid in edge['lyric_ids'] for edge in draft.alignment_graph.unbound_edges):
            draft.alignment_graph.unbound_edges.append(dict(id='split-pair:'+lid,lyric_ids=[lid],translation_ids=[tid],visual_ids=[],state='uncertain'))
    if not changed:return False
    checked=document.validate_project(draft)
    if history.group is not None or history._transaction_active:raise ValueError('请先完成当前编辑。')
    history.push(document.project,'分流演唱原词与译文')
    document.project.__dict__.clear();document.project.__dict__.update(checked.__dict__);document.touch();return True
