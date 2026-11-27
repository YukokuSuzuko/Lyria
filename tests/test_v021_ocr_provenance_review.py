"""Raw/minimum and original spatial boxes survive multi-fragment assembly."""
from copy import deepcopy
import json
import numpy as np
from lyricsync.model import Detection
from lyricsync.ocr_spatial import component,spatial_lines


def test_duplicate_alternative_survives_neighbor_fragment_assembly():
 items=[Detection('RAW-A',[0,10,50,30],.95,{'source_channels':['raw']}),
        Detection('MIN-A',[1,10,49,30],.8,{'source_channels':['minimum']}),
        Detection('B',[55,10,90,30],.9,{'source_channels':['raw']})]
 before=deepcopy(items);result=spatial_lines(items,np.zeros((60,100,3),dtype=np.uint8))
 assert len(result)==1 and result[0].text=='RAW-AB'
 provenance=result[0].evidence['components'][0]['provenance']
 assert any(p['text']=='MIN-A' and p['box']==[1,10,49,30] and p['source_channels']==['minimum'] for p in provenance)
 assert items==before


def test_spatial_split_origin_survives_further_same_slot_assembly():
 origin={'text':'upper\\nlower','box':[0,0,50,60],'confidence':.9,'source_channels':['raw']}
 items=[Detection('upper',[0,0,50,20],.9,dict(source_channels=['raw'],spatial_split='observed_horizontal_edge_bands',line_index=0,block_original=origin)),
        Detection('tail',[55,0,85,20],.95,{'source_channels':['raw']})]
 result=spatial_lines(items,np.zeros((60,100,3),dtype=np.uint8))[0]
 part=result.evidence['components'][0]
 assert part['spatial_split']=='observed_horizontal_edge_bands' and part['line_index']==0
 assert origin in part['provenance']


def test_repeated_assembly_flattens_provenance_and_cycle_is_safe():
 d=Detection('selected',[0,0,40,20],.9,{'source_channels':['raw'],'alternatives':[{'text':'raw-old','box':[1,0,39,20],'confidence':.8,'source_channels':['minimum']}]})
 for _ in range(20):d=Detection(d.text,d.box,d.confidence,{'components':[component(d)],'source_channels':['raw']})
 record=component(d)
 assert len(record['provenance'])==1 and record['provenance'][0]['text']=='raw-old'
 assert all('provenance' not in p and 'components' not in p for p in record['provenance'])
 d.evidence['alternatives']=[d.evidence]
 assert component(d)['provenance']==record['provenance']
 json.dumps(component(d))
