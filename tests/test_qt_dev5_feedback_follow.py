"""Dev5 feedback: isolated real QML, synthetic frames, no foreground acceptance."""
from copy import deepcopy
import pytest
from PySide6.QtCore import QMetaObject
from PySide6.QtTest import QTest
from test_qt_timeline_input import timeline_video, ui, find
from test_qt_timeline_snap import application, base_application
from lyricsync.model import Mapping, Project
from lyricsync.candidate_tracks import capture_source, candidate_from_project


def playback_frame(timeline, bridge, frame):
    media = dict(bridge.media)
    media.update(playing=True, playheadFrame=frame, displayedFrame=frame)
    timeline.setProperty('media', media)
    # Exercise the QML frame policy synchronously; the synthetic media map is
    # not a real transport clock and can be replaced by unrelated bridge signals.
    assert QMetaObject.invokeMethod(timeline, 'pagePlayhead')


@pytest.mark.parametrize('empty', [False, True])
def test_enabled_empty_or_untimed_axis_pages_at_edge_without_zoom(ui, empty):
    app, bridge, window, timeline = ui
    project = bridge.document.project
    if empty:
        project.mappings = []
    else:
        for row in project.mappings:
            row.start = row.end = None
    bridge._changed(); QTest.qWait(80)
    before = deepcopy(project.to_dict())
    timeline.setProperty('followEnabled', True)
    timeline.setProperty('viewFirst', 301); timeline.setProperty('viewLast', 501)
    playback_frame(timeline, bridge, 450)
    assert timeline.property('viewFirst') == 301
    playback_frame(timeline, bridge, 501)
    assert timeline.property('viewFirst') == 501
    playback_frame(timeline, bridge, 710)
    assert timeline.property('viewFirst') == 701
    assert timeline.property('viewLast') - timeline.property('viewFirst') == 200
    assert project.to_dict() == before


def test_disabled_follow_stays_fixed_even_at_edge_and_keeps_span(ui):
    app, bridge, window, timeline = ui
    bridge.document.project.mappings = []; bridge._changed(); QTest.qWait(80)
    timeline.setProperty('followEnabled', False)
    timeline.setProperty('viewFirst', 301); timeline.setProperty('viewLast', 501)
    playback_frame(timeline, bridge, 450)
    assert timeline.property('viewFirst') == 301
    playback_frame(timeline, bridge, 501)
    assert timeline.property('viewFirst') == 301
    assert timeline.property('viewLast') == 501


def test_long_timed_cue_does_not_trap_playhead_outside_view(ui):
    app, bridge, window, timeline = ui
    project = bridge.document.project
    index = bridge.media_controller.index
    project.mappings = [project.mappings[0]]
    project.mappings[0].start, project.mappings[0].end = index.interval(300, 800)
    bridge.command('timeline.select', {'id': project.mappings[0].id})
    bridge._changed(); QTest.qWait(80)
    timeline.setProperty('followEnabled', True)
    timeline.setProperty('viewFirst', 301); timeline.setProperty('viewLast', 501)
    playback_frame(timeline, bridge, 450)
    assert timeline.property('viewFirst') == 301  # timed axis keeps sentence mode
    playback_frame(timeline, bridge, 501)
    assert timeline.property('viewFirst') <= 501 < timeline.property('viewLast')
    assert timeline.property('viewLast') - timeline.property('viewFirst') == 200


@pytest.mark.parametrize('source', ['main', 'ocr'])
def test_new_overlapping_sentence_is_followed_in_main_and_candidate(ui, source):
    app, bridge, window, timeline = ui
    p = bridge.document.project
    p.mappings = [Mapping('long', [], text_override='long', start=1., end=20.),
                  Mapping('new', [], text_override='new', start=5., end=7.)]
    bridge.selected = 'long'; bridge._changed()
    if source == 'ocr':
        captured = capture_source(deepcopy(bridge.document), 'ocr', bridge.media_controller.index)
        record = candidate_from_project(captured, Project.from_dict(p.to_dict()))
        bridge._candidate_install(record)
        bridge.switchCandidateSource('ocr')
    before = deepcopy(p.to_dict())
    bridge._hold_cue_selection = False
    bridge._frame_presented(160, 5.3)
    assert bridge.selected == 'new'
    assert p.to_dict() == before
    if source == 'ocr':
        assert bridge.state['candidateReadOnly']


def test_explicit_play_cue_hold_survives_overlap(ui):
    app, bridge, window, timeline = ui
    bridge.document.project.mappings = [Mapping('long', [], text_override='long', start=1., end=20.),
                                        Mapping('new', [], text_override='new', start=5., end=7.)]
    bridge.selected = 'long'; bridge._changed()
    bridge._hold_cue_selection = True; bridge._held_playback_ids = None
    bridge._frame_presented(160, 5.3)
    assert bridge.selected == 'long'

@pytest.mark.parametrize('mode,label', [('waveform', '波形图'), ('spectrum', '频谱图')])
def test_visibility_button_names_only_the_current_plot(ui, mode, label):
    app, bridge, window, timeline = ui
    before = deepcopy(bridge.document.project.to_dict())
    for visible, verb in [(True, '隐藏'), (False, '显示')]:
        assert bridge.command('view.layout', {'audioMode': mode, 'audioVisible': visible})
        QTest.qWait(50)
        toggle=find(window,'audioVisibilityToggle')
        assert not toggle.property('text') and toggle.property('glyph')=='audio-wave'
        assert toggle.property('checked')==visible and toggle.property('tip')==verb+label
    assert bridge.document.project.to_dict() == before


def test_timed_follow_pages_real_playhead_in_gap_without_changing_selection(ui):
    app, bridge, window, timeline = ui
    p = bridge.document.project; index = bridge.media_controller.index
    p.mappings = [p.mappings[40]]
    p.mappings[0].start, p.mappings[0].end = index.interval(600, 607)
    bridge._changed()
    assert bridge.command('timeline.select', {'id': 'r40'})
    QTest.qWait(80)
    timeline.setProperty('followEnabled', True)
    timeline.setProperty('viewFirst', 501); timeline.setProperty('viewLast', 701)
    playback_frame(timeline, bridge, 1)
    assert timeline.property('viewFirst') == 1
    assert timeline.property('viewLast') == 201
    assert bridge.selected == 'r40'
    assert p.mappings[0].start == index.interval(600,607)[0]
