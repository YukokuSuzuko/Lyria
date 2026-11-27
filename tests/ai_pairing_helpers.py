import json

def pairing_reply(body):
    payload=json.loads(body['messages'][1]['content'])
    return json.dumps({'reviews':[{'mapping_id':r['id'],'verdict':'uncertain','reason':'Evidence is insufficient.'} for r in payload['current_pairings'] if not r['context_only']]})

def pairing_plan_reply(value):
    return {k:[{f:v for f,v in row.items() if f not in ('confidence','reference_meaning')} for row in rows] for k,rows in value.items()}

