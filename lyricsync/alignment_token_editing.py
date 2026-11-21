"""Keep original acoustic evidence through edits, never invent split token times."""
from copy import deepcopy
import json

def unknown(tokens):
    result=deepcopy(tokens)
    for token in result:
        token.update(source_start=None,source_end=None,ownership='unknown')
    return result

def split_tokens(tokens,cut):
    left=[];right=[];ambiguous=False
    for original in tokens:
        token=deepcopy(original)
        if cut is not None and token['end']<=cut:left.append(token)
        elif cut is not None and token['start']>=cut:right.append(token)
        else:
            # A token crossing the razor belongs to neither exact text piece.
            # Keep the original full acoustic interval once, explicitly unknown.
            left.extend(unknown([token]));ambiguous=True
    return left,right,ambiguous

def merged_tokens(rows):
    seen=set();tokens=[]
    for row in rows:
        for token in row.alignment_tokens:
            key=json.dumps(token,sort_keys=True,ensure_ascii=False)
            if key not in seen:seen.add(key);tokens.append(deepcopy(token))
    # source_start/source_end still refer to original source_row_id, not the
    # newly concatenated visible text. Do not offset immutable source indices.
    return sorted(tokens,key=lambda t:(t['start'],t['end'],t['source_row_id'],t['reading_index']))
