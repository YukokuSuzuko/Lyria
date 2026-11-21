"""Validated optional token timing evidence persisted with each editable cue."""
import math

FIELDS={'label','token_id','start','end','start_frame','end_frame','score','source_row_id',
        'reading_index','source_start','source_end','ownership'}
OWNERSHIP={'morpheme','kana','provided_segment','annotation_input','english_loanword','structural','unknown'}

def validate_alignment_tokens(tokens):
    if not isinstance(tokens,list) or len(tokens)>24000:raise ValueError('Invalid alignment token list')
    last=-1.
    for t in tokens:
        if not isinstance(t,dict) or set(t)!=FIELDS:raise ValueError('Invalid alignment token fields')
        if not isinstance(t['label'],str) or len(t['label'])!=1 or t['label'] not in "abcdefghijklmnopqrstuvwxyz'":raise ValueError('Invalid alignment label')
        for key in ('token_id','start_frame','end_frame','reading_index'):
            if type(t[key]) is not int or t[key]<0:raise ValueError('Invalid alignment token index')
        if t['end_frame']<=t['start_frame']:raise ValueError('Invalid token frame range')
        for key in ('start','end','score'):
            if type(t[key]) not in (int,float) or not math.isfinite(t[key]):raise ValueError('Invalid token number')
        if not 0<=t['start']<t['end'] or t['start']<last-.001 or not 0<=t['score']<=1:raise ValueError('Invalid token timing/score')
        # Persisted evidence may contain overlapping source rows after an edit.
        # Only ordering of starts is a storage invariant; raw CTC order is
        # checked before conversion.
        last=t['start']
        if not isinstance(t['source_row_id'],str) or not t['source_row_id'] or len(t['source_row_id'])>256:raise ValueError('Invalid token source ID')
        a,b=t['source_start'],t['source_end']
        if (a is None)!=(b is None) or (a is not None and (type(a) is not int or type(b) is not int or not 0<=a<b<=24000)):raise ValueError('Invalid token source range')
        if t['ownership'] not in OWNERSHIP:raise ValueError('Invalid token ownership')
    return tokens
