"""Research-only alternate Qwen prompt granularity, never the shipped worker.

Run by dev7_compare_alignment.py --qwen-sentence-experiment. This deliberately
uses one timing unit per supplied phrase while preserving strict timing checks.
No assertion is made that a complete set of timestamps proves accurate singing
alignment. The production default remains row-bounded WORD tokenization.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lyricsync'))
import local_alignment_worker as worker


def sentence_inputs(processor,audio,rows,language):
    units=[];counts=[]
    for row in rows:
        words=processor.split_words_for_alignment(row['text'],language)
        counts.append(int(bool(words)))
        if words:units.append(' '.join(words))
    if not units:raise ValueError('No transcript units')
    content=[{'type':'audio','audio':audio}]+[{'type':'text','text':text} for text in units]
    inputs=processor.apply_chat_template([[{'role':'user','content':content}]],tokenize=True,
        return_dict=True,sampling_rate=16000,return_tensors='pt')
    return inputs,[units],counts


original_align=worker.align
worker.prepare_row_inputs=sentence_inputs

def research_align(request):
    result=original_align(request)
    result['tokenization']['strategy']='research-sentence-units-not-default'
    result['research_only']=True
    return result

worker.align=research_align
if __name__=='__main__':raise SystemExit(worker.main())