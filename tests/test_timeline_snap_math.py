"""Deterministic bounds/tie tests for the production QML snapping search."""
import json
from pathlib import Path
import pytest
from PySide6.QtQml import QJSEngine
from test_qt_timeline_input import application


@pytest.fixture
def snap(application):
    engine=QJSEngine()
    source=(Path(__file__).resolve().parents[1]/'desktop_qt/qml/TimelineSnap.js').read_text('utf8')
    assert not engine.evaluate(source.replace('.pragma library','')).isError()
    def call(name,*args):
        result=engine.evaluate('JSON.stringify('+name+'('+','.join(json.dumps(a) for a in args)+'))')
        assert not result.isError(),result.toString()
        return json.loads(result.toString())
    yield call


def test_targets_exclude_self_group_and_unknown_or_outside_frames(snap):
    rows=[{'cueId':'self','firstFrame':11,'lastFrame':20,'number':1},
          {'cueId':'group','firstFrame':31,'lastFrame':40,'number':2},
          {'cueId':'other','firstFrame':51,'lastFrame':100,'number':3},
          {'cueId':'unknown','firstFrame':0,'lastFrame':0,'number':4}]
    assert [x['frame'] for x in snap('targets',rows,['self','group'],100)]==[51,101]


@pytest.mark.parametrize('edges,raw,low,high,target,expected',[
    ([10,21],-50,-9,79,1,-9),([90,101],50,-89,0,101,0),
    ([10],-3,-9,9,21,-3), # cannot invert or create a zero-frame interval
    ([21],3,-10,80,10,3),
])
def test_limits_preserve_first_last_and_nonempty_ranges(snap,edges,raw,low,high,target,expected):
    result=snap('resolve',edges,raw,low,high,[{'frame':target,'priority':1,'kind':'start','number':2}],0,1,True)
    assert result['delta']==expected


def test_equidistant_playhead_wins_then_earlier_cue_boundary(snap):
    candidates=[{'frame':99,'priority':1,'kind':'start','number':2},{'frame':101,'priority':1,'kind':'start','number':3}]
    assert snap('resolve',[100],0,-99,100,candidates,101,2,True)['delta']==1
    assert snap('resolve',[100],0,-99,100,candidates,0,2,True)['delta']==-1


def test_no_forced_one_frame_snap_when_zoomed_in(snap):
    assert not snap('resolve',[100],0,-99,100,[],101,20,True)['snapped']
    assert snap('resolve',[100],0,-99,100,[],104,2,True)['snapped']
    assert not snap('resolve',[100],0,-99,100,[],105,2,True)['snapped']


def test_fixed_markers_are_snap_targets_and_respect_disable(snap):
    targets = snap('targets', [], [], 100, [{'frame': 40}, {'frame': 0}, {'frame': 101}])
    assert targets == [{'frame': 40, 'kind': 'marker', 'number': 0, 'priority': 1}]
    result = snap('resolve', [38], 0, -37, 50, targets, 0, 2, True)
    assert result['delta'] == 2 and result['kind'] == 'marker'
    assert not snap('resolve', [38], 0, -37, 50, targets, 0, 2, False)['snapped']
