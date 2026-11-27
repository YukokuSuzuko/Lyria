from unittest.mock import Mock

import numpy as np

from lyricsync.model import Detection
from lyricsync.ocr_preprocess import recover_empty_wide_subtitle
from lyricsync.vision import (RapidOCRProvider, Track, refine_text_boundary,
                            region_suggestions, text_presence)


def test_region_proposal_prioritizes_distinct_text_and_preserves_side_lanes():
    samples = [(i, [760, 397, 1071, 603], '11') for i in range(1, 7)]
    samples += [(i, [311 + 5*i, 948, 1441 - 10*i, 1030], f'lyric {i}') for i in range(4)]
    samples += [(i, [14, 220, 180, 260], f'left {i}') for i in range(4)]
    samples += [(i, [1690, 220, 1850, 260], f'right {i}') for i in range(4)]
    results = region_suggestions(samples, 1920, 1080, sample_count=7)
    assert all(not row['static_number'] for row in results[:3])
    assert results[-1]['static_number'] and results[-1]['hits'] == 6
    boxes = {row['examples'][0]:row['box'] for row in results}
    assert boxes['lyric 0'] == [269, 918, 1651, 1060]
    assert boxes['left 0'] == [0, 190, 210, 290]
    assert boxes['right 0'] == [1660, 190, 1880, 290]

    # Symmetry alone would leave a centered box at about x=449..1471.
    narrow_samples = [(i,[480,948,1440,1030],f'lyric {i}') for i in range(5)]
    assert region_suggestions(narrow_samples,1920,1080)[0]['box'] == [269,918,1651,1060]


def test_static_number_tolerates_box_breathing_not_countdown_or_motion():
    jittered = [(1,[783,426,1043,576],'11'), (2,[799,435,1034,567],'11'),
                (3,[795,429,1040,571],'11'), (4,[795,431,1036,571],'11'),
                (5,[794,428,1038,573],'11'), (6,[798,432,1035,569],'11')]
    def is_static(samples):
        return region_suggestions(samples,1920,1080,sample_count=7)[0]['static_number']
    assert is_static(jittered)
    assert not is_static([(t,b,str(10-t)) for t,b,_ in jittered])
    assert not is_static([(t,[b[0]+t*4,b[1],b[2]+t*4,b[3]],x) for t,b,x in jittered])
    same_center_low_overlap = jittered + [(7,[858,460,973,539],'11')]
    assert not is_static(same_center_low_overlap)
    duplicate_lane = jittered + [(3,[796,431,1039,572],'11')]
    assert not is_static(duplicate_lane)


def test_center_retry_translates_evidence_and_refuses_noise_or_fragments():
    reading = Detection('short line', [53,20,530,135], .95, {
        'components':[{'text':'short line','box':[53,20,530,135],'confidence':.95,
                       'provenance':[{'text':'short line','box':[55,21,529,136]}]}]})
    reads = Mock(return_value=[reading, reading,
        Detection('11', [55,20,250,135], .99),
        Detection('clipped', [0,20,250,135], .99),
        Detection('weak', [70,20,280,135], .83)])
    result = recover_empty_wide_subtitle(np.zeros((145, 1400, 3),dtype=np.uint8), reads)
    assert reads.call_args.args[0].shape == (145,588,3)
    assert len(result) == 1
    item = result[0]
    assert item.box == [459,20,936,135]
    assert item.evidence['components'][0]['provenance'][0]['box'] == [461,21,935,136]
    assert item.evidence['wide_empty_recovery_box'] == [406,0,994,145]
    assert reading.box == [53,20,530,135]
    assert reading.evidence['components'][0]['box'] == [53,20,530,135]
    reads.reset_mock()
    assert not recover_empty_wide_subtitle(np.zeros((145, 580, 3),dtype=np.uint8), reads)
    reads.assert_not_called()


def test_nonempty_main_read_does_not_add_a_split_duplicate():
    provider = RapidOCRProvider.__new__(RapidOCRProvider)
    provider.preprocess = 'subtitle'
    result = [Detection('full lyric', [110,20,1250,126], .96)]
    provider._read_subtitle_crop = Mock(return_value=result)
    frame = np.zeros((145,1400,3),dtype=np.uint8)
    assert provider.recognize(frame) == result
    assert provider._read_subtitle_crop.call_count == 1


def test_moderate_boundary_requires_anchored_agreement_and_real_shape_drop():
    track = Track(9, 9, [10,20,120,40], ['short lyric'], [.772], None, 9, 9)
    class Video:
        def frames(self, stamp, _end, _cancel):
            yield stamp, None
    def detect(stamp, _frame):
        return [Detection('short lyric', track.box, .746)] if 7 <= stamp <= 11 else []
    values = [(t, .995 if 7 <= t <= 11 else .705) for t in range(5,15)]
    assert refine_text_boundary(Video(),track,values,9,'start',detect,lambda:False) == (7,[6,7])
    assert refine_text_boundary(Video(),track,values,9,'end',detect,lambda:False) == (12,[11,12])
    assert refine_text_boundary(Video(),track,[(t,.995) for t,_ in values],9,'end',detect,lambda:False) is None
    assert refine_text_boundary(Video(),track,values,9,'end',
        lambda t,f: detect(t,f) if t==9 else [],lambda:False) is None
    assert text_presence(track,[Detection('short lyric',[250,30,400,50],.746)]) is None
    assert text_presence(track,[Detection('different',track.box,.746)]) is None
