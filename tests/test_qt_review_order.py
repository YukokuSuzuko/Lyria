"""Chronological review order is a view; project and timeline identity stay intact."""
from copy import deepcopy
from lyricsync.model import Mapping, Translation
from lyricsync.project_document import load_document, write_document
from test_qt_timeline_commands import application, bridge


def ids(model):
    return [r['id'] for r in model.items]


def test_review_order_changes_only_after_committed_time_and_survives_history(bridge, tmp_path):
    p = bridge.document.project
    p.translations = [Translation('a', '原始甲'), Translation('b', '原始乙'), Translation('c', '原始丙')]
    p.mappings = [Mapping('a', ['a'], start=.4, end=1.4),
                  Mapping('b', ['b'], start=.1, end=1.0),
                  Mapping('c', ['c'], start=.2, end=1.8)]
    bridge.selected = 'c'; bridge._changed()
    original = deepcopy(p.to_dict())
    assert ids(bridge.model) == ['b', 'c', 'a']
    assert ids(bridge.timeline_model) == ['a', 'b', 'c']
    assert [r['number'] for r in bridge.model.items] == [1, 2, 3]
    assert p.to_dict() == original
    assert bridge.command('row.setBoundary', {'id': 'c', 'edge': 'first', 'frame': 1})
    assert ids(bridge.model) == ['c', 'b', 'a'] and bridge.selected == 'c'
    assert bridge.uiState['selectedVisibleIndex'] == 0
    assert bridge.uiState['selectedTimelineIndex'] == 2
    assert ids(bridge.timeline_model) == ['a', 'b', 'c']
    assert [(m.id, m.translation_ids) for m in p.mappings] == [('a', ['a']), ('b', ['b']), ('c', ['c'])]
    assert [t.text for t in p.translations] == ['原始甲', '原始乙', '原始丙']
    assert bridge.command('history.undo', {})
    assert ids(bridge.model) == ['b', 'c', 'a'] and bridge.selected == 'c'
    assert bridge.command('history.redo', {})
    assert ids(bridge.model) == ['c', 'b', 'a'] and bridge.selected == 'c'
    path = tmp_path/'order.lyria'
    write_document(bridge.document.snapshot(), path)
    bridge.document = load_document(path)
    bridge._refresh()
    assert ids(bridge.model) == ['c', 'b', 'a']
    assert ids(bridge.timeline_model) == ['a', 'b', 'c']


def test_same_start_frame_preserves_source_order_and_filter_mapping(bridge):
    p = bridge.document.project
    p.mappings = [Mapping('z', ['t'], start=.2, end=1.),
                  Mapping('a', ['t'], start=.19, end=.7),
                  Mapping('missing', ['t'])]
    bridge.selected = 'a'; bridge._changed()
    assert ids(bridge.model) == ['z', 'a', 'missing']
    assert bridge.uiState['selectedVisibleIndex'] == 1
    p.mappings[0].confirmed = True
    bridge._unconfirmed_only = True; bridge._changed()
    assert ids(bridge.model) == ['a', 'missing']
    assert bridge.uiState['selectedVisibleIndex'] == 0
    assert bridge.uiState['selection']['current']['id'] == 'a'
    assert bridge.uiState['export']['omissions'][0]['id'] == 'missing'


def test_confirm_during_playback_does_not_freeze_following_selection(bridge):
    p=bridge.document.project
    p.mappings=[Mapping('a',['t'],start=0,end=.4),Mapping('b',['t'],start=.4,end=.7),Mapping('c',['t'],start=.7,end=1.)]
    bridge._changed()
    for filtered in (False,True):
        bridge._unconfirmed_only=filtered
        for turn in range(20):
            for row in p.mappings:row.confirmed=False
            bridge.selected='a';bridge._hold_cue_selection=False;bridge._changed()
            bridge.media_controller._state['playing']=True
            assert bridge.command('row.confirm',{'id':'a','value':True})
            bridge._frame_presented(4,.4)
            assert bridge.selected=='b'
            assert bridge.uiState['selectedTimelineIndex']==1
            assert bridge.command('row.confirm',{'id':'b','value':True})
            bridge._frame_presented(5,.7)
            assert bridge.selected=='c'
            assert bridge.command('row.confirm',{'id':'c','value':True})
            bridge._frame_presented(6,1.)
            assert bridge.selected=='c'
            assert bridge.media_controller.snapshot()['playing']


def test_completed_edit_or_timeline_selection_follows_next_playing_interval(bridge):
    p=bridge.document.project
    for operation,payload in [('timeline.select',{'id':'a'}),('row.edit',{'id':'a','text':'修改后的中文'}),('row.setBoundary',{'id':'a','edge':'first','frame':2})]:
        p.mappings=[Mapping('a',['t'],start=0,end=.4),Mapping('b',['t'],start=.4,end=.7),Mapping('c',['t'],start=.7,end=1.)]
        bridge.selected='a';bridge._hold_cue_selection=False;bridge._changed()
        bridge.media_controller._state.update(playing=True,displayedFrame=3,positionSeconds=.2)
        assert bridge.command(operation,payload)
        bridge._frame_presented(3,.2)
        assert bridge.selected=='a'
        bridge._frame_presented(4,.4)
        assert bridge.selected=='b',operation
        bridge._frame_presented(5,.7)
        assert bridge.selected=='c'
        assert bridge.media_controller.snapshot()['playing']


def test_paused_manual_selection_is_retained_across_late_frame_delivery(bridge):
    bridge.media_controller._state.update(playing=False,positionSeconds=0)
    assert bridge.command('timeline.select',{'id':'target'})
    bridge._frame_presented(1,0)
    assert bridge.selected=='target'


def test_follow_does_not_return_to_old_long_overlap_when_current_ends(bridge):
    p=bridge.document.project
    p.mappings=[Mapping('old-first',['t'],start=0,end=2.),Mapping('current',['t'],start=.2,end=.4),Mapping('next',['t'],start=.4,end=.7)]
    bridge.selected='current';bridge._hold_cue_selection=False;bridge._changed()
    bridge.media_controller._state.update(playing=True,displayedFrame=3,positionSeconds=.2)
    bridge._frame_presented(4,.4)
    assert bridge.selected=='next'
    bridge.selected='current';bridge._refresh(False)
    bridge.command('timeline.select',{'id':'current'})
    bridge._frame_presented(8,1.8)
    assert bridge.selected=='current'  # Only the old first row survives.
    bridge._frame_presented(4,.4)
    assert bridge.selected=='next'  # New playback interval resumes following.
