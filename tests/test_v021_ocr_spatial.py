"""Spatial OCR contracts; real-movie acceptance is reported separately."""
import numpy as np
from unittest.mock import patch
from lyricsync.model import Detection,Visual
from lyricsync.ocr_spatial import spatial_lines,tagged,classify_events,deduplicate
from lyricsync.vision import Tracker


def d(text,box,confidence=.95):return Detection(text,box,confidence)

def test_same_frame_fragments_form_one_line_before_tracking():
    image=np.zeros((120,500,3),np.uint8)
    found=spatial_lines([d('first',[10,50,150,80]),d('second',[165,51,300,81])],image)
    assert len(found)==1 and found[0].text=='firstsecond'
    assert found[0].box==[10,50,300,81]
    assert len(found[0].evidence['components'])==2


def test_same_y_widely_separated_blocks_remain_independent():
    image=np.zeros((120,800,3),np.uint8)
    assert len(spatial_lines([d('left',[0,50,80,80]),d('right',[600,50,680,80])],image))==2


def test_two_y_slots_never_join_even_when_text_is_identical():
    image=np.zeros((180,400,3),np.uint8)
    found=spatial_lines([d('same',[10,20,150,50]),d('same',[10,65,150,95])],image)
    assert len(found)==2
    assert found[0].evidence['line_slot']!=found[1].evidence['line_slot']


def test_raw_minimum_near_duplicate_keeps_alternatives_and_provenance():
    items=tagged([d('first',[10,50,150,80],.8)],'minimum')+tagged([d('f1rst',[12,51,152,81],.95)],'raw')
    clean=deduplicate(items)
    assert len(clean)==1 and set(clean[0].evidence['source_channels'])=={'minimum','raw'}
    assert clean[0].evidence['alternatives']
    assert items[0].text=='first'


def test_real_union_crop_recognition_not_reference_rewrite():
    image=np.zeros((120,400,3),np.uint8);calls=[]
    def recognize(box):calls.append(box);return 'read from full crop',.97
    found=spatial_lines([d('first',[10,50,150,80]),d('second',[165,51,300,81])],image,recognize)
    assert calls==[[10,50,300,81]]
    assert found[0].text=='read from full crop'
    assert found[0].evidence['component_text']=='firstsecond'


def test_unresolved_newline_block_does_not_get_equal_height_fake_boxes():
    image=np.zeros((120,400,3),np.uint8)
    found=spatial_lines([d('upper\nlower',[10,10,200,100])],image)
    assert len(found)==1 and found[0].text=='upper\nlower'
    assert found[0].evidence['eligible_for_alignment'] is False


def test_actual_pixel_bands_split_newline_before_tracker():
    image=np.zeros((150,400,3),np.uint8)
    # Distinct spatial strokes separated by substantial blank rows.
    for x in range(30,200,10):
        image[20:40,x:x+6]=255;image[80:100,x:x+6]=255
    found=spatial_lines([d('upper\nlower',[10,5,220,120])],image)
    assert len(found)==2 and found[0].box[3]<found[1].box[1]
    assert [x.text for x in found]==['upper','lower']


def test_staggered_two_lines_have_independent_lifetimes():
    image=np.zeros((150,400,3),np.uint8);tracker=Tracker(.1,max_gap=.15)
    upper=d('upper',[10,20,200,50]);lower=d('lower',[10,75,200,105])
    with patch('lyricsync.vision.visibility_score',return_value=0.):
        tracker.feed(0.,image,[upper])
        tracker.feed(.1,image,[upper,lower])
        tracker.feed(.2,image,[upper,lower])
        tracker.feed(.3,image,[lower])
        tracker.feed(.4,image,[])
    tracks=tracker.finish()
    a=next(t for t in tracks if t.texts[0]=='upper');b=next(t for t in tracks if t.texts[0]=='lower')
    assert (a.first,a.last)==(0.,.2) and (b.first,b.last)==(.1,.3)


def test_low_confidence_single_frame_is_evidence_not_alignment_target():
    v=Visual('fragment','broken',.1,.2,[10,20,200,50],.6,1)
    classify_events([v],[0.,.1,.2,.3],.4)
    assert v.evidence['frame_count']==1 and not v.evidence['eligible_for_alignment']
    assert (v.start,v.end)==(.1,.2)


def test_high_confidence_short_flash_is_flagged_not_deleted_or_stretched():
    v=Visual('flash','yes!',.1,.2,[10,20,200,50],.98,1)
    classify_events([v],[0.,.1,.2,.3],.4)
    assert v.evidence['eligible_for_alignment'] and v.warnings
    assert (v.start,v.end)==(.1,.2)


def test_variable_frame_pts_count_not_average_fps():
    v=Visual('v','a',.10,.25,[1,2,20,10],.7,1)
    classify_events([v],[0.,.1,.15,.25,.9],1.)
    assert v.evidence['frame_count']==2


def test_current_scanner_unresolved_block_not_split_again_downstream():
    from lyricsync.ocr_observations import split_visual_lines
    v=Visual('v','upper\nlower',.1,.5,[1,2,20,10],.7,1,
             evidence={'observation_status':'unresolved_multiline_block','eligible_for_alignment':False})
    assert split_visual_lines([v])==[v]


def test_qc_cache_accepts_old_and_new_but_rejects_malformed_evidence():
    from dataclasses import asdict
    from lyricsync.qc_ocr_cache import CachedOCR
    old={'text':'old','box':[0,0,20,10],'confidence':.9}
    assert CachedOCR.valid([old])
    newer=spatial_lines(tagged([d('new',[0,0,20,10])],'raw'),np.zeros((20,30,3),np.uint8))
    assert CachedOCR.valid([asdict(newer[0])])
    for bad in ({'unexpected':1},{'line_slot':[1,float('nan')]},{'eligible_for_alignment':'yes'},
                {'components':[{'text':'bad'}]},{'source_channels':['invented']},[]):
        assert not CachedOCR.valid([{**old,'evidence':bad}])
    assert not CachedOCR.valid([{**old,'unexpected':1}])


def test_only_partial_adjacent_short_text_is_classed_as_transition_noise():
    fragment=Visual('a','hello',0.,.1,[0,0,100,20],.6,1)
    stable=Visual('b','hello world',.1,1.,[0,0,200,20],.95,9)
    isolate=Visual('c','other',2.,2.1,[0,0,100,20],.6,1)
    classify_events([fragment,stable,isolate],[i/10 for i in range(23)],2.3)
    assert fragment.evidence['observation_status']=='transition_noise'
    assert fragment.evidence['neighbor_stable_event_ids']==['b']
    assert isolate.evidence['observation_status']=='short_uncertain'
    assert not isolate.evidence['eligible_for_alignment']


def test_timing_warning_does_not_erase_raw_confidence_of_genuine_flash():
    v=Visual('flash','yes!',.1,.2,[10,20,200,50],.7,1,evidence={'recognition_confidence':.99})
    classify_events([v],[0.,.1,.2,.3],.4)
    assert v.evidence['eligible_for_alignment'] and v.evidence['observation_status']=='normal'


def test_scan_collects_actual_decoded_pts_for_frame_count():
    from test_dev7_ocr_reuse import run_frames
    frame=np.zeros((30,80,3),np.uint8)
    _,visuals,_=run_frames([(i/10,frame) for i in range(10)],.7)
    assert len(visuals)==1 and visuals[0].evidence['frame_count']==10
    assert visuals[0].evidence['eligible_for_alignment']
