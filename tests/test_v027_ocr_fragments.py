from lyricsync.model import Visual
from lyricsync.ocr_spatial import classify_events
from lyricsync.ocr_anchor_evidence import clipped_noise

def event(identity,text,box,start,end,owner):
    return Visual(identity,text,start,end,box,.95,6,evidence={'roi_owner':owner})

def test_nested_fragment_needs_matching_owner_band_text_and_time():
    parent=event('whole','abcdef',[30,40,310,82],1,4,'lower')
    child=event('part','ab',[32,43,65,78],1.6,3.3,'lower')
    separate=event('above','ab',[32,2,65,32],1.6,3.3,'lower')
    other=event('other','ab',[32,43,65,78],1.6,3.3,'upper')
    new=event('next','ab',[32,43,65,78],4.01,5,'lower')
    events=[parent,child,separate,other,new]
    classify_events(events,[i/30 for i in range(151)],5.)
    assert child.evidence['observation_status']=='subline_fragment' and not child.evidence['eligible_for_alignment']
    assert all(v.evidence['eligible_for_alignment'] for v in (parent,separate,other,new))

def test_digit_noise_respects_own_crop_and_fresh_support():
    v=event('r','11',[300,100,470,190],1,4,'upper')
    cards=[dict(id='lower',box=[15,600,800,720],enabled=True),dict(id='upper',box=[100,100,780,320],enabled=True)]
    comp={'text':'11','box':[200,0,370,90],'confidence':.7,'source_channels':['raw']}
    v.evidence.update(roi_configuration=cards,recognition_confidence=.7,components=[{'text':'11','confidence':.7,'evidence':{'source_channels':['raw'],'components':[comp]}}])
    assert clipped_noise(v,None)
    v.box=[300,112,470,202]
    assert not clipped_noise(v,None)
    v.box=[300,100,470,190];v.text='ああ'
    assert not clipped_noise(v,None)

def test_crop_verification_requires_clipped_compatible_evidence():
    import numpy as np
    from lyricsync.model import Detection
    from lyricsync.ocr_preprocess import recover_clipped_subtitle

    frame = np.zeros((100, 800, 3),dtype=np.uint8)
    part = Detection('ab',[310,25,430,80],.6,evidence={'alternatives':[
        {'text':'abd','box':[245,2,468,99],'confidence':.66,'source_channels':['raw']}]})
    restored = Detection('abcd',[54,27,249,80],.96)
    calls=[]
    def read(crop):
        calls.append(crop.shape)
        return [restored]
    result = recover_clipped_subtitle(frame,[part],read)
    assert calls==[(100,400,3)] and result[0].text=='abcd'
    assert result[0].box==[254,27,449,80] and part.box==[310,25,430,80]
    clear=Detection('clear',[310,25,440,80],.96)
    assert recover_clipped_subtitle(frame,[clear],read)==[clear] and len(calls)==1
    # A crop ending at the edge can itself be truncated and cannot replace a line.
    clipped=Detection('partial',[20,27,399,80],.99)
    assert recover_clipped_subtitle(frame,[part],lambda crop:[clipped])==[part]


def test_two_regions_keep_credits_blank_gaps_and_simultaneous_lines():
    import cv2
    import numpy as np
    from lyricsync.model import Detection
    from lyricsync.multi_roi import RegionTrackers

    tracker = RegionTrackers([{'id':'upper'}, {'id':'lower'}], .1, .62, .15)
    scenes = [
        [('upper','TITLE')], [('upper','TITLE')], [], [],
        [('upper','CREDIT')], [('upper','CREDIT'),('lower','VERSE')],
        [('lower','VERSE')], [], [('lower','VERSE')], [('lower','VERSE')]]
    for frame, scene in enumerate(scenes):
        image = np.zeros((110,240,3),np.uint8)
        detections = []
        for owner, word in scene:
            y = 10 if owner=='upper' else 65
            cv2.putText(image,word,(12,y+22),cv2.FONT_HERSHEY_SIMPLEX,.75,(255,255,255),2)
            detections.append(Detection(word,[10,y,128,y+26],.95,evidence={'roi_owner':owner}))
        tracker.feed(frame/10,image,detections)
    spans = [(track.observations[0]['evidence']['roi_owner'],track.texts[0],track.first,track.last)
             for track in tracker.finish()]
    assert sorted(spans)==sorted([('upper','TITLE',0.,.1),('upper','CREDIT',.4,.5),
                                  ('lower','VERSE',.5,.6),('lower','VERSE',.8,.9)])
