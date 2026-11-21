"""Backend timeline contracts with real Qt state, no video transport side effects."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
import time
import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication
from desktop_qt.controller import Bridge
from lyricsync.frames import FrameIndex
from lyricsync.model import Mapping, Translation


@pytest.fixture(scope='module')
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def bridge(application,tmp_path,monkeypatch):
    obj=Bridge(storage_dir=tmp_path/'recovery')
    p=obj.document.project
    p.video={'duration':2.0}
    p.translations=[Translation('t','前半后半')]
    p.mappings=[Mapping('overlap',['t'],start=0,end=2),Mapping('target',['t'],start=.2,end=1.)]
    obj.media_controller.index=FrameIndex([0,.1,.2,.4,.7,1.,1.4,1.8],2.)
    obj.selected='target';obj._changed()
    # Fail immediately if a timeline edit or history operation touches transport.
    monkeypatch.setattr(obj.media_controller,'seek',lambda *a:pytest.fail('unexpected seek'))
    monkeypatch.setattr(obj.media_controller,'pause',lambda *a:pytest.fail('unexpected pause'))
    yield obj
    monkeypatch.undo()
    obj.shutdown()
    deadline=time.monotonic()+3
    while obj.media_controller.worker.thread.is_alive() and time.monotonic()<deadline:
        application.processEvents();time.sleep(.005)


def frames(bridge,identity='target'):
    row=bridge._row(identity);index=bridge.media_controller.index
    return index.first(row.start),index.last(row.end)


def test_select_does_not_touch_transport_or_document(bridge):
    revision=bridge.document.revision
    assert bridge.command('timeline.select',{'id':'overlap'})
    assert bridge.selected=='overlap' and bridge._hold_cue_selection
    assert bridge.document.revision==revision and not bridge.history.undo_stack
    assert not bridge.command('timeline.select',{'id':'absent'})
    assert bridge.selected=='overlap'


@pytest.mark.parametrize('delta,expected',[(2,(5,7)),(100,(6,8)),(-100,(1,3))])
def test_move_vfr_atomic_clamped_and_reversible(bridge,monkeypatch,delta,expected):
    before=bridge.document.project.to_dict();revision=bridge.document.revision
    calls=[];original=bridge.editor.set_time
    def record(*args,**kwargs):
        calls.append(kwargs);return original(*args,**kwargs)
    monkeypatch.setattr(bridge.editor,'set_time',record)
    assert frames(bridge)==(3,5)
    assert bridge.command('timeline.move',{'id':'target','delta':delta})
    assert frames(bridge)==expected and expected[1]-expected[0]+1==3
    assert len(calls)==1 and set(calls[0])=={'start','end'}
    assert len(bridge.history.undo_stack)==1 and bridge.document.revision==revision+1
    after=bridge.document.project.to_dict()
    assert bridge.command('history.undo',{})
    assert bridge.document.project.to_dict()==before and bridge.selected=='target'
    assert bridge.command('history.redo',{})
    assert bridge.document.project.to_dict()==after and bridge.selected=='target'


@pytest.mark.parametrize('delta',[True,1.5,'2',None,float('nan')])
def test_move_rejects_noninteger_without_history(bridge,delta):
    before=bridge.document.snapshot()
    assert not bridge.command('timeline.move',{'id':'target','delta':delta})
    assert bridge.document.snapshot()==before and not bridge.history.undo_stack


@pytest.mark.parametrize('invalid',['locked','untimed','reversed','outside','no_index','missing'])
def test_move_validation_is_nonmutating(bridge,invalid):
    row=bridge._row('target');identity='target'
    if invalid=='locked':bridge.document.project.settings['manual_locked_ids']=['target']
    elif invalid=='untimed':row.start=None
    elif invalid=='reversed':row.end=.1
    elif invalid=='outside':row.end=3
    elif invalid=='no_index':bridge.media_controller.index=None
    else:identity='absent'
    before=bridge.document.snapshot()
    assert not bridge.command('timeline.move',{'id':identity,'delta':2})
    assert bridge.document.snapshot()==before and not bridge.history.undo_stack


def test_zero_and_bound_clamped_move_do_not_create_undo(bridge):
    assert bridge.command('timeline.move',{'id':'target','delta':0})
    assert not bridge.history.undo_stack
    assert bridge.command('timeline.move',{'id':'target','delta':100})
    revision=bridge.document.revision
    assert bridge.command('timeline.move',{'id':'target','delta':100})
    assert len(bridge.history.undo_stack)==1 and bridge.document.revision==revision


def test_split_history_restores_source_not_first_overlap(bridge):
    bridge.document.project.settings['frame_markers']=[4]
    assert bridge.command('row.split',{'id':'target','offsetUtf16':2,'frame':4})
    child=bridge.selected
    assert child.startswith('split-')
    for _ in range(3):
        assert bridge.command('history.undo',{})
        assert bridge.selected=='target'
        assert bridge.document.project.settings['frame_markers']==[4]
        assert bridge.command('history.redo',{})
        assert bridge.selected==child
    assert '_history_context' not in bridge.document.project.to_dict()


@pytest.mark.parametrize('command,payload',[('row.add',{}),('row.delete',{'id':'target'}),('timeline.createRange',{'first':6,'last':7})])
def test_structural_history_preserves_selection(bridge,command,payload):
    assert bridge.command(command,payload)
    result_selection=bridge.selected
    assert bridge.command('history.undo',{})
    assert bridge.selected=='target'
    assert bridge.command('history.redo',{})
    assert bridge.selected==result_selection


def test_timeline_model_is_unfiltered_and_hidden_selection_is_explicit(bridge):
    bridge._row('target').confirmed=True;bridge._changed()
    assert bridge.command('view.filter',{'unconfirmedOnly':True})
    assert bridge.rows.count==1 and bridge.timelineRows.count==2
    assert bridge.timelineRows.get(1)['cueId']=='target'
    assert bridge.command('timeline.select',{'id':'target'})
    assert bridge.selected=='target' and bridge.uiState['selectedVisibleIndex']==-1
    assert bridge.uiState['selectedTimelineIndex']==1
    assert bridge.uiState['selection']['current']['cueId']=='target'
    assert bridge.command('row.add',{})
    assert bridge.command('history.undo',{})
    assert bridge.selected=='target' and bridge.uiState['selectedVisibleIndex']==-1
    bridge._frame_presented(8,1.8)
    assert bridge.selected=='target'
    # Removing the review filter must not reset an explicit timeline selection.
    assert bridge.command('view.filter',{'unconfirmedOnly':False})
    assert bridge.selected=='target' and bridge.uiState['selectedVisibleIndex']==1


def test_timeline_model_get_is_detached_and_notifies_only_changes(bridge):
    model=bridge.timelineRows;events=[];inserted=[];removed=[]
    model.modelReset.connect(lambda:events.append('reset'))
    model.dataChanged.connect(lambda *args:events.append('change'))
    model.countChanged.connect(lambda:events.append('count'))
    model.rowsInserted.connect(lambda parent,first,last:(events.append('insert'),inserted.append((first,last))))
    model.rowsRemoved.connect(lambda parent,first,last:removed.append((first,last)))
    item=model.get(1);item['chinese']='outside mutation';item['reasons'].append('outside mutation')
    assert model.get(1)['chinese']=='前半后半' and not model.get(1)['reasons']
    assert model.get(-1)=={} and model.get(2)=={}
    assert bridge.command('row.edit',{'id':'target','text':'修改中文'})
    assert events==['change'] and model.get(1)['chinese']=='修改中文'
    events.clear()
    assert bridge.command('row.add',{})
    assert events==['insert','count'] and model.count==3 and inserted==[(2,2)]
    assert bridge.command('history.undo',{})
    assert model.count==2 and removed==[(2,2)] and 'reset' not in events


@pytest.mark.parametrize('selected,first,count',[(0,0,5),(1,0,5),(5,3,5),(8,5,5),(9,5,5)])
def test_timeline_context_has_at_most_five_full_rows(bridge,selected,first,count):
    p=bridge.document.project
    p.mappings=[Mapping(str(n),['t'],start=0,end=1,confirmed=n%2==0) for n in range(10)]
    bridge.selected=str(selected);bridge._changed()
    assert bridge.command('timeline.select',{'id':str(selected)})
    assert bridge.command('view.filter',{'unconfirmedOnly':True})
    state=bridge.uiState
    assert 'rows' not in state and 'rowsRevision' not in state
    assert state['selectedTimelineIndex']==selected
    assert state['timelineContextFirstIndex']==first
    assert [r['cueId'] for r in state['timelineContext']]==[str(n) for n in range(first,first+count)]
    assert state['timelineBounds']=={'firstFrame':1,'lastFrame':5}
    assert bridge.timelineRows.count==10 and bridge.rows.count==5


def test_frame_follow_does_not_rebuild_or_copy_full_timeline(bridge,monkeypatch):
    p=bridge.document.project
    p.mappings=[Mapping(str(n),['t'],start=0,end=.1) for n in range(1000)]
    p.mappings[-1].start=.2;p.mappings[-1].end=1
    bridge.selected='0';bridge._changed()
    model=bridge.timelineRows;model_items=model.items;all_rows=bridge._all_rows
    state_changes=[];model_changes=[]
    bridge.stateChanged.connect(lambda:state_changes.append(bridge.uiState))
    model.modelReset.connect(lambda:model_changes.append('reset'))
    model.dataChanged.connect(lambda *args:model_changes.append('change'))
    monkeypatch.setattr(bridge,'_rows',lambda:pytest.fail('frame presentation rebuilt all rows'))
    monkeypatch.setattr(model,'replace',lambda rows:pytest.fail('frame presentation replaced timeline model'))
    bridge._frame_presented(4,.4)
    assert bridge.selected=='999' and len(state_changes)==1
    state=state_changes[0]
    assert len(state['timelineContext'])==5 and state['selectedTimelineIndex']==999
    assert state['timelineContextFirstIndex']==995
    assert 'rows' not in state and model_changes==[]
    assert model.items is model_items and bridge._all_rows is all_rows
    bridge._frame_presented(5,.7)
    assert len(state_changes)==1


@pytest.mark.parametrize('command,payload',[
    ('row.add',{}),('row.delete',{'id':'target'}),
    ('timeline.createRange',{'first':6,'last':7}),
    ('row.split',{'id':'target','offsetUtf16':2,'frame':4}),
    ('row.merge',{'id':'target','ids':['target','tail']}),
    ('row.repeat',{'id':'target'}),('row.edit',{'id':'target','text':'改过的内容'}),
    ('row.setBoundary',{'id':'target','edge':'last','frame':6}),
    ('timeline.move',{'id':'target','delta':1}),
])
def test_edit_selection_survives_later_presented_frames_and_history(bridge,command,payload):
    p=bridge.document.project
    p.mappings.append(Mapping('tail',['t'],start=1,end=1.4))
    p.settings['frame_markers']=[4];bridge._changed()
    bridge.media_controller._state.update(playing=True,displayedFrame=4,requestedFrame=4,positionSeconds=.4)
    transport=bridge.media_controller.snapshot()
    assert bridge.command(command,payload)
    selection=bridge.selected
    assert bridge._hold_cue_selection
    bridge._frame_presented(8,1.8)
    assert bridge.selected==selection and bridge.media_controller.snapshot()==transport
    assert bridge.command('history.undo',{})
    assert bridge.selected=='target'
    bridge._frame_presented(8,1.8)
    assert bridge.selected=='target' and bridge.media_controller.snapshot()==transport
    assert bridge.command('history.redo',{})
    bridge._frame_presented(8,1.8)
    assert bridge.selected==selection and bridge.media_controller.snapshot()==transport


def test_grouped_timeline_moves_form_one_undo_step(bridge):
    before=bridge.document.project.to_dict()
    assert bridge.command('history.begin',{'label':'移动字幕'})
    assert bridge.command('timeline.move',{'id':'target','delta':1})
    assert bridge.command('timeline.move',{'id':'target','delta':1})
    assert bridge.command('history.end',{})
    assert len(bridge.history.undo_stack)==1 and frames(bridge)==(5,7)
    after=bridge.document.project.to_dict()
    assert bridge.command('history.undo',{}) and bridge.document.project.to_dict()==before
    assert bridge.command('history.redo',{}) and bridge.document.project.to_dict()==after


def test_empty_timeline_metadata_and_undo_after_deleting_last_row(bridge):
    bridge.document.project.mappings=[bridge._row('target')];bridge._changed()
    assert bridge.command('row.delete',{'id':'target'})
    assert bridge.selected=='' and bridge.timelineRows.count==0
    assert bridge.uiState['timelineContext']==[] and bridge.uiState['selectedTimelineIndex']==-1
    assert bridge.uiState['timelineBounds']=={'firstFrame':0,'lastFrame':0}
    assert bridge.command('history.undo',{})
    assert bridge.selected=='target' and bridge.timelineRows.count==1


def set_frame_ranges(bridge,ranges):
    index=FrameIndex([n*.1 for n in range(100)],10.)
    bridge.media_controller.index=index;bridge.document.project.video={'duration':10.}
    bridge.document.project.mappings=[Mapping(str(n),['t'],start=None,end=None) if span is None else
        Mapping(str(n),['t'],start=index.interval(*span)[0],end=index.interval(*span)[1])
        for n,span in enumerate(ranges)]
    bridge._changed()


@pytest.mark.parametrize('frame,expected',[
    (-5,1),(0,1),(9,1),(10,1),(20,1),(22,1),(25,1),(26,3),(29,3),(30,3),
    (40,3),(45,3),(46,0),(50,0),(60,0),(61,0),(1000,0),
])
def test_timeline_frame_lookup_exact_gap_outside_and_arbitrary_order(bridge,frame,expected):
    set_frame_ranges(bridge,[(50,60),(10,20),None,(30,40)])
    assert bridge.timelineIndexAtFrame(frame)==expected


@pytest.mark.parametrize('frame,expected',[(10,0),(30,0),(40,0),(75,3),(80,3),(81,3),(99,3)])
def test_timeline_frame_lookup_nested_overlaps_uses_prefix_max(bridge,frame,expected):
    set_frame_ranges(bridge,[(10,80),(30,40),(50,60),(70,90),None])
    # The latest starting interval need not cover the frame: F65 is in row 0.
    assert bridge.timelineIndexAtFrame(frame)==expected
    assert bridge.timelineIndexAtFrame(65)==0


def test_timeline_frame_lookup_untimed_empty_and_invalid_queries(bridge):
    set_frame_ranges(bridge,[None,None])
    assert bridge.timelineIndexAtFrame(1)==-1
    assert bridge.uiState['timelineBounds']=={'firstFrame':0,'lastFrame':0}
    set_frame_ranges(bridge,[])
    assert bridge.timelineIndexAtFrame(10)==-1
    set_frame_ranges(bridge,[(10,20)])
    for frame in (None,True,1.5,'10'):
        assert bridge.timelineIndexAtFrame(frame)==-1


def test_timeline_frame_lookup_cache_survives_filter_and_queries_and_updates_on_edit(bridge,monkeypatch):
    set_frame_ranges(bridge,[(50,60),(10,20),None,(30,40)])
    original=bridge._rebuild_timeline_index;rebuilt=[]
    def record(items):
        rebuilt.append(True);return original(items)
    monkeypatch.setattr(bridge,'_rebuild_timeline_index',record)
    monkeypatch.setattr(bridge.timelineRows,'get',lambda index:pytest.fail('lookup copied model row'))
    before=bridge.document.snapshot();selection=bridge.selected;cache=bridge._timeline_spans
    for _ in range(100):assert bridge.timelineIndexAtFrame(26)==3
    assert bridge.command('view.filter',{'unconfirmedOnly':True})
    assert bridge._timeline_spans is cache and rebuilt==[]
    assert bridge.selected==selection and bridge.document.snapshot()==before
    assert bridge.command('timeline.move',{'id':'3','delta':10})
    assert rebuilt==[True] and bridge.timelineIndexAtFrame(29)==1
    assert bridge.command('history.undo',{})
    assert len(rebuilt)==2 and bridge.timelineIndexAtFrame(29)==3


def test_timeline_frame_lookup_matches_interval_distance_for_random_overlaps(bridge):
    import random
    randomizer=random.Random(918)
    ranges=[]
    for _ in range(200):
        first=randomizer.randint(1,100);ranges.append((first,randomizer.randint(first,100)))
    ranges.extend([None]*20);randomizer.shuffle(ranges)
    set_frame_ranges(bridge,ranges)
    for frame in range(-10,111):
        chosen=bridge.timelineIndexAtFrame(frame)
        assert chosen>=0 and ranges[chosen] is not None
        distance=lambda span:max(span[0]-frame,frame-span[1],0)
        assert distance(ranges[chosen])==min(distance(span) for span in ranges if span is not None)


@pytest.mark.parametrize('command',['timeline.select','row.select'])
def test_explicit_selection_emits_after_refresh_even_for_same_identity(bridge,monkeypatch,command):
    transport=[]
    if command=='row.select':
        monkeypatch.setattr(bridge.media_controller,'pause',lambda:transport.append('pause'))
        monkeypatch.setattr(bridge.media_controller,'seek',lambda frame:transport.append(('seek',frame)))
    observed=[]
    bridge.selectionRequested.connect(lambda:observed.append((bridge.selected,bridge.uiState['selectedId'])))
    assert bridge.command(command,{'id':'target'})
    assert bridge.command(command,{'id':'target'})
    assert observed==[('target','target'),('target','target')]
    assert not bridge.command(command,{'id':'absent'})
    assert len(observed)==2
    assert transport==(['pause',('seek',3),'pause',('seek',3)] if command=='row.select' else [])


def test_nonexplicit_selection_changes_do_not_emit_selection_requested(bridge):
    requests=[];bridge.selectionRequested.connect(lambda:requests.append(True))
    assert bridge.command('row.edit',{'id':'target','text':'保留滚动位置'})
    bridge._refresh(False)
    assert bridge.command('history.undo',{})
    assert bridge.command('history.redo',{})
    bridge._hold_cue_selection=False
    bridge._frame_presented(8,1.8)
    assert bridge.selected=='overlap'
    assert requests==[]


def set_filtered_review_rows(bridge):
    p=bridge.document.project
    p.mappings=[Mapping('first',['t'],start=0,end=.1),Mapping('target',['t'],start=.2,end=.4),
                Mapping('confirmed',['t'],start=.4,end=.7,confirmed=True),
                Mapping('following',['t'],start=.7,end=1.)]
    bridge.selected='target';bridge._changed()
    assert bridge.command('view.filter',{'unconfirmedOnly':True})


def test_filtered_review_delete_selects_adjacent_visible_not_first_row(bridge):
    set_filtered_review_rows(bridge)
    transport=bridge.media_controller.snapshot()
    assert bridge.command('row.delete',{'id':'target'})
    assert bridge.selected=='following' and bridge.uiState['selectedVisibleIndex']==1
    assert bridge.media_controller.snapshot()==transport
    assert bridge.command('history.undo',{}) and bridge.selected=='target'


def test_filtered_timeline_delete_can_select_full_model_neighbor(bridge):
    set_filtered_review_rows(bridge)
    assert bridge.command('timeline.select',{'id':'target'})
    assert bridge.command('row.delete',{'id':'target'})
    assert bridge.selected=='confirmed' and bridge.uiState['selectedVisibleIndex']==-1


def test_standalone_confirm_preserves_selected_filtered_row_without_navigation(bridge):
    set_filtered_review_rows(bridge)
    requests=[];bridge.selectionRequested.connect(lambda:requests.append(True))
    transport=bridge.media_controller.snapshot()
    assert bridge.command('row.confirm',{'id':'target','value':True})
    assert bridge.selected=='target' and bridge.uiState['selectedVisibleIndex']==-1
    assert bridge.uiState['selection']['current']['confirmed'] is True
    assert bridge.media_controller.snapshot()==transport and requests==[]
    bridge._frame_presented(8,1.8)
    assert bridge.selected=='target'
    assert bridge.command('row.confirm',{'id':'target','value':False})
    assert bridge.selected=='target' and bridge.uiState['selectedVisibleIndex']==1


def test_delete_after_standalone_confirm_still_uses_review_visible_neighbors(bridge):
    set_filtered_review_rows(bridge)
    assert bridge.command('row.confirm',{'id':'target','value':True})
    assert bridge.command('row.delete',{'id':'target'})
    assert bridge.selected=='following' and bridge.uiState['selectedVisibleIndex']==1


def test_filtered_review_delete_last_visible_uses_previous_visible_neighbor(bridge):
    set_filtered_review_rows(bridge)
    bridge.selected='following';bridge._refresh(False)
    assert bridge.command('row.delete',{'id':'following'})
    assert bridge.selected=='target' and bridge.uiState['selectedVisibleIndex']==1


def test_failed_standalone_confirm_does_not_pin_or_change_context(bridge):
    set_filtered_review_rows(bridge)
    bridge._row('target').start=None;bridge._changed()
    before=bridge.document.snapshot()
    assert not bridge.command('row.confirm',{'id':'target','value':True})
    assert bridge.document.snapshot()==before and bridge.selected=='target'
    assert bridge._confirmed_selection=='' and not bridge._hold_cue_selection


def test_confirm_next_still_selects_next_visible_review_row(bridge,monkeypatch):
    set_filtered_review_rows(bridge)
    transport=[]
    monkeypatch.setattr(bridge.media_controller,'pause',lambda:transport.append('pause'))
    monkeypatch.setattr(bridge.media_controller,'seek',lambda frame:transport.append(('seek',frame)))
    assert bridge.command('row.confirmNext',{'id':'target'})
    assert bridge.selected=='following' and bridge._row('target').confirmed
    assert transport==['pause',('seek',5)]
