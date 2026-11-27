"""Frame timeline contracts; headless tests do not instantiate Tk or decode video."""
from copy import deepcopy
from fractions import Fraction
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lyricsync.editing import Editor, History
from lyricsync.frames import FrameIndex
from lyricsync.frame_timeline import (
    DragInterrupted, FrameDragController, FrameTimeline, FrameViewport,
    TimelineRow, frame_bounds, real_overlap_ids, relation_text, timeline_rows, timeline_palette,
)
from lyricsync.model import Mapping, Project, Translation


def make_project(index=None):
    index = index or FrameIndex([float(Fraction(i * 1001, 30000)) for i in range(120)], 4.004)
    rows = []
    for number, (first, last) in enumerate(((10, 30), (31, 50), (54, 75)), 1):
        start, end = index.interval(first, last)
        rows.append(Mapping(f'stable-{number}', [f't-{number}'], start=start, end=end,
                            confirmed=True, locked=True))
    project = Project(video={'duration': index.duration}, mappings=rows,
                      translations=[Translation(f't-{number}', f'真实项目字幕 {number}') for number in range(1, 4)])
    return project, index


class TimelineRowsTests(unittest.TestCase):
    def setUp(self):
        self.project, self.index = make_project()

    def test_exactly_three_slots_at_both_ends_and_without_selection(self):
        first = timeline_rows(self.project, self.index, 'stable-1')
        self.assertEqual(len(first), 3)
        self.assertEqual([row.identity for row in first], [None, 'stable-1', 'stable-2'])
        last = timeline_rows(self.project, self.index, 'stable-3')
        self.assertEqual([row.identity for row in last], ['stable-2', 'stable-3', None])
        none = timeline_rows(self.project, self.index, None)
        self.assertEqual(len(none), 3)
        self.assertTrue(all(row.identity is None for row in none))
        self.assertTrue(all(row.status == '请先选择字幕' for row in none))

    def test_selection_uses_stable_id_and_full_project_number(self):
        rows = timeline_rows(self.project, self.index, 'stable-2')
        self.assertEqual([row.number for row in rows], [1, 2, 3])
        self.assertEqual(rows[1].text, '真实项目字幕 2')
        self.project.mappings[1].text_override = ''
        self.assertEqual(timeline_rows(self.project, self.index, 'stable-2')[1].text, '')
        self.assertTrue(all(row.identity is None for row in timeline_rows(self.project, self.index, 'deleted')))

    def test_pts_not_average_fps(self):
        index = FrameIndex([0, .020, .055, .120, .140, .250], .320)
        row = Mapping('vfr', [], start=.055, end=.250)
        self.assertEqual((frame_bounds(row, index).first, frame_bounds(row, index).last), (3, 5))

    def test_single_frame_range_remains_exact(self):
        start, end = self.index.interval(50, 50)
        bounds = frame_bounds(Mapping('one', [], start=start, end=end), self.index)
        self.assertEqual((bounds.first, bounds.last), (50, 50))
        self.assertTrue(bounds.complete)

    def test_missing_or_partial_times_do_not_fabricate_bars(self):
        row = self.project.mappings[1]
        row.start = None
        partial = timeline_rows(self.project, self.index, row.id)[1]
        self.assertEqual(partial.last, 50)
        self.assertIsNone(partial.first)
        self.assertFalse(partial.complete)
        self.assertIn('待补齐', partial.status)
        row.end = None
        empty = timeline_rows(self.project, self.index, row.id)[1]
        self.assertIsNone(empty.first)
        self.assertIsNone(empty.last)

    def test_no_index_displays_waiting_not_guessed_frames(self):
        rows = timeline_rows(self.project, None, 'stable-2')
        self.assertTrue(all(row.first is None and row.last is None for row in rows))
        self.assertTrue(all(row.status == '等待真实帧索引' for row in rows))
        self.assertEqual(real_overlap_ids(self.project, None), set())

    def test_invalid_known_times_do_not_become_clamped_fake_bars(self):
        for start, end in ((-1, 2), (0, 999), (3, 2), (True, 2), (float('nan'), 2), (0, 0)):
            with self.subTest(start=start, end=end):
                row = Mapping('invalid', [], start=start, end=end)
                bounds = frame_bounds(row, self.index)
                self.assertFalse(bounds.complete)
                self.assertTrue(bounds.invalid)

    def test_true_shared_frame_overlap_red_even_if_only_one_frame(self):
        self.project.mappings[1].start = self.index.stamp(30)
        rows = timeline_rows(self.project, self.index, 'stable-2')
        self.assertTrue(rows[0].overlap)
        self.assertTrue(rows[1].overlap)
        self.assertEqual(relation_text(rows[0], rows[1]), '重叠 1 帧')
        self.assertEqual(relation_text(rows[1], rows[2]), '间隔 3 帧')
        self.project.mappings[1].start = self.index.stamp(31)
        rows = timeline_rows(self.project, self.index, 'stable-2')
        self.assertFalse(rows[0].overlap)
        self.assertEqual(relation_text(rows[0], rows[1]), '边界相接 · 无重叠')

    def test_overlap_checks_all_project_rows_not_just_three_visible(self):
        self.project.mappings.append(Mapping('distant', [], start=self.index.stamp(20), end=self.index.interval(40, 40)[1]))
        rows = timeline_rows(self.project, self.index, 'stable-1')
        self.assertTrue(rows[1].overlap)
        self.assertTrue(rows[2].overlap)
        self.assertNotIn('distant', [row.identity for row in rows])

    def test_relation_does_not_claim_gap_for_missing_edge(self):
        self.project.mappings[0].start = None
        rows = timeline_rows(self.project, self.index, 'stable-2')
        self.assertEqual(relation_text(rows[0], rows[1]), '边界待补齐')


class TimelineViewportTests(unittest.TestCase):
    def test_inclusive_geometry_gives_one_frame_positive_width(self):
        viewport = FrameViewport(100, 1, 100)
        self.assertEqual(viewport.frame_x(1, 1000), 0)
        self.assertEqual(viewport.frame_x(101, 1000), 1000)
        self.assertEqual(viewport.frame_x(2, 1000) - viewport.frame_x(1, 1000), 10)
        self.assertEqual(viewport.x_frame(10, 1000, end=True), 1)
        self.assertEqual(viewport.x_frame(10, 1000), 2)

    def test_no_frame_zero_and_viewport_clamps(self):
        viewport = FrameViewport(100, 1, 10)
        self.assertEqual(viewport.x_frame(-100, 100), 1)
        self.assertEqual(viewport.x_frame(9999, 100), 100)
        viewport.pan(-500)
        self.assertEqual(viewport.first, 1)
        viewport.pan(999)
        self.assertEqual((viewport.first, viewport.last), (91, 100))

    def test_zoom_pan_fit_and_integer_ticks(self):
        project, index = make_project()
        viewport = FrameViewport(120, 1, 120)
        viewport.fit(timeline_rows(project, index, 'stable-2'))
        self.assertLessEqual(viewport.first, 10)
        self.assertGreaterEqual(viewport.last, 75)
        previous_span = viewport.span
        viewport.zoom(2)
        self.assertLess(viewport.span, previous_span)
        viewport.zoom(1 / 1000)
        self.assertEqual((viewport.first, viewport.last), (1, 120))
        viewport.zoom(1000)
        self.assertEqual(viewport.span, 1)
        self.assertEqual(len(viewport.ticks()), 1)
        self.assertTrue(all(type(frame) is int and frame >= 1 for frame in viewport.ticks()))

    def test_fit_unknown_rows_shows_video_range_not_invented_intervals(self):
        viewport = FrameViewport(100, 30, 20)
        viewport.fit((TimelineRow('上一句'), TimelineRow('当前句'), TimelineRow('下一句')))
        self.assertEqual((viewport.first, viewport.span), (1, 100))

    def test_delta_rounds_symmetrically(self):
        viewport = FrameViewport(100, 1, 100)
        self.assertEqual(viewport.delta_frames(5, 1000), 1)
        self.assertEqual(viewport.delta_frames(-5, 1000), -1)
        self.assertEqual(viewport.delta_frames(4, 1000), 0)

    def test_bad_zoom_and_total_rejected(self):
        viewport = FrameViewport()
        for value in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                viewport.configure(value)
        for value in (0, -1, float('nan')):
            with self.assertRaises(ValueError):
                viewport.zoom(value)


class TimelineDragTests(unittest.TestCase):
    def setUp(self):
        self.project, self.index = make_project()
        self.history = History()
        self.changes = []
        self.controller = FrameDragController(self.project, self.index, self.history, self.changes.append)
        self.row = self.project.mappings[1]

    def test_begin_rejects_unknown_mapping_edge_missing_index_and_foreign_group(self):
        with self.assertRaises(ValueError):
            self.controller.begin('unknown', 'start')
        with self.assertRaises(ValueError):
            self.controller.begin(self.row.id, 'middle')
        with self.assertRaises(ValueError):
            FrameDragController(self.project, None, self.history).begin(self.row.id, 'start')
        self.history.begin('typing')
        with self.assertRaises(DragInterrupted):
            self.controller.begin(self.row.id, 'start')
        self.assertEqual(self.history.group, 'typing')
        self.assertEqual(self.history.undo_stack, [])

    def test_immediate_one_edge_mutation_not_neighbors_and_one_undo(self):
        original = deepcopy(self.project.to_dict())
        neighbors = deepcopy([self.project.mappings[0], self.project.mappings[2]])
        old_end = self.row.end
        self.controller.begin(self.row.id, 'start')
        for frame in (28, 25, 26, 27):
            result = self.controller.update(frame)
            self.assertEqual(result.frame, frame)
            self.assertEqual(self.row.start, self.index.stamp(frame))
            self.assertEqual(self.row.end, old_end)
            self.assertIsNone(self.history.group)
        self.controller.finish()
        self.assertEqual(len(self.history.undo_stack), 1)
        self.assertEqual(self.changes, [self.row.id] * 4)
        self.assertEqual(neighbors, [self.project.mappings[0], self.project.mappings[2]])
        self.assertTrue(self.row.human_edited)
        self.assertTrue(self.row.locked)
        self.assertFalse(self.row.confirmed)
        restored, label = self.history.restore(self.project)
        self.assertEqual(restored.to_dict(), original)
        redone, _ = self.history.restore(restored, redo=True)
        self.assertEqual(redone.mappings[1].start, self.index.stamp(27))

    def test_end_uses_next_pts_and_final_frame_uses_duration(self):
        self.controller.begin(self.row.id, 'end')
        self.controller.update(61)
        self.assertEqual(self.row.end, self.index.stamps[61])
        self.controller.update(120)
        self.assertEqual(self.row.end, self.index.duration)
        self.controller.finish()

    def test_vfr_edit_uses_exact_source_pts_not_fps(self):
        index = FrameIndex([0, .020, .055, .120, .140, .250], .320)
        row = Mapping('vfr', [], start=.020, end=.250)
        project = Project(mappings=[row], video={'duration': .320})
        controller = FrameDragController(project, index, History()).begin('vfr', 'end')
        controller.update(4)
        self.assertEqual(row.end, .140)
        self.assertEqual(row.start, .020)
        controller.finish()

    def test_single_frame_edit_and_clamp_to_opposite_edge(self):
        self.controller.begin(self.row.id, 'start')
        result = self.controller.update(999)
        self.assertEqual(result.frame, 50)
        self.assertEqual(frame_bounds(self.row, self.index).first, 50)
        self.assertEqual(frame_bounds(self.row, self.index).last, 50)
        self.controller.update(-10)
        self.assertEqual(self.row.start, self.index.stamp(1))
        self.controller.finish()
        controller = FrameDragController(self.project, self.index, self.history).begin(self.row.id, 'end')
        result = controller.update(-5)
        self.assertEqual(result.frame, 1)
        self.assertEqual(self.row.end, self.index.interval(1, 1)[1])

    def test_reject_mode_preserves_project_and_history_on_invalid_frame(self):
        original = self.project.to_dict()
        self.controller.begin(self.row.id, 'start')
        for frame in (0, -1, 51, 121, True, 31.0, '31'):
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                self.controller.update(frame, clamp=False)
            self.assertEqual(self.project.to_dict(), original)
            self.assertEqual(len(self.history.undo_stack), 0)
        self.controller.finish()

    def test_preserves_exact_unaligned_opposite_timestamp(self):
        self.row.end = self.index.stamp(51) - .0001
        original_end = self.row.end
        self.controller.begin(self.row.id, 'start').update(40)
        self.assertEqual(self.row.end, original_end)
        self.controller.finish()

    def test_missing_times_can_be_set_one_at_a_time_without_guessing_other(self):
        self.row.start = self.row.end = None
        self.controller.begin(self.row.id, 'start').update(35)
        self.assertEqual(self.row.start, self.index.stamp(35))
        self.assertIsNone(self.row.end)
        self.controller.finish()
        other = FrameDragController(self.project, self.index, self.history).begin(self.row.id, 'end')
        other.update(35)
        other.finish()
        self.assertEqual(frame_bounds(self.row, self.index).first, 35)
        self.assertEqual(frame_bounds(self.row, self.index).last, 35)
        self.assertEqual(len(self.history.undo_stack), 2)

    def test_missing_start_can_be_filled_while_preserving_known_end(self):
        self.row.start = None
        end = self.row.end
        self.controller.begin(self.row.id, 'start').update(45)
        self.assertEqual(self.row.end, end)
        self.assertEqual(self.row.start, self.index.stamp(45))

    def test_invalid_opposite_boundary_rejected_before_mutation(self):
        for value in (float('nan'), -1, 0, self.index.duration + 1):
            with self.subTest(end=value):
                self.row.end = value
                with self.assertRaises(ValueError):
                    self.controller.begin(self.row.id, 'start')
                self.assertFalse(self.controller.active)
        self.row.start = self.index.duration + 1
        with self.assertRaises(ValueError):
            self.controller.begin(self.row.id, 'end')

    def test_cancel_restores_time_review_flags_and_prior_redo(self):
        Editor(self.project, self.history).set_text(self.row.id, 'prior')
        restored, _ = self.history.restore(self.project)
        self.project, self.row = restored, restored.mappings[1]
        original = restored.to_dict()
        prior_redo = list(self.history.redo_stack)
        controller = FrameDragController(restored, self.index, self.history, self.changes.append)
        controller.begin(self.row.id, 'end').update(70)
        self.assertTrue(controller.cancel())
        self.assertEqual(restored.to_dict(), original)
        self.assertEqual(self.history.undo_stack, [])
        self.assertEqual(self.history.redo_stack, prior_redo)
        self.assertIsNone(self.history.group)
        self.assertEqual(self.changes, [self.row.id, self.row.id])

    def test_cancel_restores_undo_entry_displaced_by_history_limit(self):
        self.history.limit = 1
        Editor(self.project, self.history).set_text(self.row.id, 'existing edit')
        before = list(self.history.undo_stack)
        self.controller.begin(self.row.id, 'end').update(70)
        self.controller.cancel()
        self.assertEqual(self.history.undo_stack, before)

    def test_no_move_and_round_trip_drag_create_no_undo_and_restore_flags(self):
        self.controller.begin(self.row.id, 'start')
        self.controller.update(31)
        self.assertFalse(self.controller.finish())
        self.assertEqual(self.history.undo_stack, [])
        original = deepcopy(self.row)
        self.controller.begin(self.row.id, 'start')
        self.controller.update(34)
        self.controller.update(31)
        self.assertFalse(self.controller.finish())
        self.assertEqual(self.row, original)
        self.assertEqual(self.history.undo_stack, [])

    def test_external_edit_is_separate_and_prevents_destructive_cancel(self):
        self.controller.begin(self.row.id, 'start').update(35)
        Editor(self.project, self.history).set_text(self.row.id, 'external edit')
        self.assertEqual(len(self.history.undo_stack), 2)
        self.assertFalse(self.controller.cancel())
        self.assertEqual(self.row.start, self.index.stamp(35))
        self.assertEqual(self.row.text_override, 'external edit')
        restored, _ = self.history.restore(self.project)
        self.assertEqual(restored.mappings[1].start, self.index.stamp(35))
        self.assertIsNone(restored.mappings[1].text_override)
        restored, _ = self.history.restore(restored)
        self.assertEqual(restored.mappings[1].start, self.index.stamp(31))

    def test_external_edit_interrupts_further_drag_without_stranding_group(self):
        self.controller.begin(self.row.id, 'end').update(60)
        Editor(self.project, self.history).set_text('stable-1', 'foreign')
        with self.assertRaises(DragInterrupted):
            self.controller.update(65)
        self.assertFalse(self.controller.active)
        self.assertIsNone(self.history.group)
        self.assertEqual(self.row.end, self.index.interval(60, 60)[1])
        self.assertEqual(len(self.history.undo_stack), 2)

    def test_foreign_group_not_closed_by_our_finish_or_cancel(self):
        self.controller.begin(self.row.id, 'end').update(60)
        self.history.begin('foreign hold')
        self.assertFalse(self.controller.cancel())
        self.assertEqual(self.history.group, 'foreign hold')
        self.history.end()

    def test_row_replacement_or_direct_external_mutation_interrupts(self):
        self.controller.begin(self.row.id, 'end').update(60)
        self.row.text_override = 'background update'
        with self.assertRaises(DragInterrupted):
            self.controller.update(65)
        self.assertEqual(self.row.text_override, 'background update')
        controller = FrameDragController(self.project, self.index, self.history).begin(self.row.id, 'start')
        self.project.mappings[1] = deepcopy(self.row)
        with self.assertRaises(DragInterrupted):
            controller.update(35)

    def test_history_undo_during_drag_is_detected(self):
        self.controller.begin(self.row.id, 'end').update(60)
        restored, _ = self.history.restore(self.project)
        with self.assertRaises(DragInterrupted):
            self.controller.update(65)
        self.assertEqual(restored.mappings[1].end, self.index.interval(50, 50)[1])
        self.assertEqual(len(self.history.redo_stack), 1)

    def test_repeated_finish_and_cancel_safe(self):
        self.controller.begin(self.row.id, 'end').update(60)
        self.controller.finish()
        self.controller.finish()
        self.assertFalse(self.controller.cancel())
        self.assertEqual(len(self.history.undo_stack), 1)


class TimelineSnapTests(unittest.TestCase):
    def setUp(self):
        self.project, self.index = make_project()
        self.history = History()
        self.controller = FrameDragController(self.project, self.index, self.history)
        self.controller.begin('stable-2', 'start')

    def test_snaps_to_previous_exclusive_end_not_shared_visible_frame(self):
        result = self.controller.update(30, snap=True, pixels_per_frame=3)
        self.assertTrue(result.snapped)
        self.assertEqual(result.frame, 31)
        self.assertIn('F31', result.snap_text)
        self.assertIn('前句末帧 F30', result.snap_text)
        self.assertEqual(self.project.mappings[1].start, self.project.mappings[0].end)
        self.assertEqual(real_overlap_ids(self.project, self.index), set())

    def test_pixel_threshold_and_frame_threshold_both_apply(self):
        self.assertFalse(self.controller.update(30, snap=True, pixels_per_frame=8).snapped)
        self.assertFalse(self.controller.update(27, snap=True, pixels_per_frame=.1).snapped)
        self.assertTrue(self.controller.update(29, snap=True, pixels_per_frame=3).snapped)

    def test_bypass_and_toggle_allow_real_overlap(self):
        result = self.controller.update(30, snap=True, pixels_per_frame=3, bypass_snap=True)
        self.assertFalse(result.snapped)
        self.assertEqual(result.frame, 30)
        self.assertEqual(real_overlap_ids(self.project, self.index), {'stable-1', 'stable-2'})
        result = self.controller.update(29, snap=False)
        self.assertFalse(result.snapped)
        self.assertEqual(result.frame, 29)

    def test_not_all_movements_snap_and_no_neighbor_pushed(self):
        previous = deepcopy(self.project.mappings[0])
        result = self.controller.update(20, snap=True, pixels_per_frame=1)
        self.assertEqual(result.frame, 20)
        self.assertFalse(result.snapped)
        self.assertEqual(previous, self.project.mappings[0])

    def test_uses_mapping_full_project_predecessor(self):
        self.controller.finish()
        controller = FrameDragController(self.project, self.index, self.history).begin('stable-3', 'start')
        self.assertEqual(controller.snap_target(), 51)
        result = controller.update(52, snap=True, pixels_per_frame=3)
        self.assertEqual(result.frame, 51)
        self.assertEqual(self.project.mappings[2].start, self.project.mappings[1].end)

    def test_missing_previous_start_still_allows_known_end_boundary(self):
        self.project.mappings[0].start = None
        self.assertEqual(self.controller.snap_target(), 31)

    def test_unknown_or_invalid_previous_end_does_not_invent_target(self):
        for end in (None, float('nan'), -1, self.index.duration + 1):
            with self.subTest(end=end):
                self.project.mappings[0].end = end
                self.assertIsNone(self.controller.snap_target())
                self.assertFalse(self.controller.update(30, snap=True).snapped)

    def test_no_target_at_first_row_end_edge_or_beyond_current_end(self):
        self.controller.finish()
        first = FrameDragController(self.project, self.index, self.history).begin('stable-1', 'start')
        self.assertIsNone(first.snap_target())
        first.finish()
        end = FrameDragController(self.project, self.index, self.history).begin('stable-2', 'end')
        self.assertIsNone(end.snap_target())
        end.finish()
        self.project.mappings[0].end = self.index.interval(70, 70)[1]
        other = FrameDragController(self.project, self.index, self.history).begin('stable-2', 'start')
        self.assertIsNone(other.snap_target())


class PlayheadLightweightTests(unittest.TestCase):
    def test_playhead_only_moves_one_canvas_line_no_row_rebuild(self):
        widget = FrameTimeline.__new__(FrameTimeline)
        widget._index = FrameIndex([0, .02, .07], .12)
        widget._playhead = 99
        widget.viewport = FrameViewport(3, 1, 3)
        widget.collapsed = False
        widget.canvas = Mock()
        widget.canvas.winfo_width.return_value = 402
        widget._render = Mock(side_effect=AssertionError('playhead must not rebuild'))
        widget.refresh = Mock(side_effect=AssertionError('playhead must not refresh'))
        widget.update_playhead(stamp=.07)
        widget.canvas.coords.assert_called_once()
        self.assertEqual(widget._playhead_frame, 3)
        widget.canvas.itemconfigure.assert_called_once_with(99, state='normal')
        widget.canvas.reset_mock()
        widget.update_playhead(None)
        widget.canvas.coords.assert_not_called()
        widget.canvas.itemconfigure.assert_called_once_with(99, state='hidden')


class WidgetLifecycleHeadlessTests(unittest.TestCase):
    def widget(self):
        widget = FrameTimeline.__new__(FrameTimeline)
        project, index = make_project()
        widget.get_project = lambda: project
        widget.get_index = lambda: index
        widget.selection = 'stable-2'
        widget.get_selection = lambda: widget.selection
        widget._project, widget._index, widget._selected = project, index, 'stable-2'
        widget.viewport = FrameViewport(120, 25, 20)
        widget._drag = None
        widget._suppress_select_fit = False
        widget._render = Mock()
        return widget

    def test_playback_selection_refresh_preserves_pan_and_zoom(self):
        widget = self.widget()
        widget.selection = 'stable-3'
        widget.refresh()
        self.assertEqual((widget.viewport.first, widget.viewport.span), (25, 20))
        self.assertEqual(widget.rows[1].identity, 'stable-3')
        widget.refresh(fit=True)
        self.assertNotEqual((widget.viewport.first, widget.viewport.span), (25, 20))
        self.assertLessEqual(widget.viewport.first, 31)
        self.assertGreaterEqual(widget.viewport.last, 75)

    def test_timeline_neighbor_selection_suppresses_host_fit_request(self):
        widget = self.widget()
        widget.selection = 'stable-3'
        widget._suppress_select_fit = True
        widget.refresh(fit=True)
        self.assertEqual((widget.viewport.first, widget.viewport.span), (25, 20))

    def test_replacing_index_fits_even_with_selection_suppression(self):
        widget = self.widget()
        new_index = FrameIndex(widget._index.stamps[:], widget._index.duration)
        widget.get_index = lambda: new_index
        widget._suppress_select_fit = True
        widget.refresh()
        self.assertNotEqual((widget.viewport.first, widget.viewport.span), (25, 20))

    def test_right_click_menu_survives_post_and_is_replaced_next_time(self):
        widget = self.widget()
        widget.finish_drag = Mock()
        widget._row_at = Mock(return_value=TimelineRow('当前句', 'stable-2', 2))
        widget._width = Mock(return_value=300)
        widget._context_menu = None
        event = Mock(x=150, y=50, x_root=500, y_root=500)
        first, second = Mock(), Mock()
        with patch('lyricsync.frame_timeline.tk.Menu', side_effect=[first, second]):
            self.assertEqual(widget._menu(event), 'break')
            first.tk_popup.assert_called_once_with(500, 500)
            first.grab_release.assert_called_once()
            first.destroy.assert_not_called()
            self.assertEqual(first.add_command.call_count, 2)
            self.assertEqual(widget._menu(event), 'break')
            first.destroy.assert_called_once()
            second.destroy.assert_not_called()

    def test_selection_change_commit_is_safe_with_reentrant_host_refresh(self):
        widget = self.widget()
        history = History()
        controller = FrameDragController(widget._project, widget._index, history)
        controller.begin('stable-2', 'start').update(35)
        widget.controller, widget._drag = controller, (0, 31, 25, 20)
        widget.canvas = Mock()
        widget.canvas.grab_current.return_value = widget.canvas
        widget.winfo_exists = Mock(return_value=True)
        widget.on_status = Mock()
        widget.on_commit = Mock(side_effect=lambda: widget.refresh())
        widget.selection = 'stable-3'
        widget.refresh()
        self.assertIsNone(widget.controller)
        self.assertFalse(widget.dragging)
        widget.on_commit.assert_called_once()
        widget.canvas.grab_release.assert_called_once()
        self.assertEqual(len(history.undo_stack), 1)
        self.assertIsNone(history.group)
        self.assertEqual(widget.rows[1].identity, 'stable-3')
        self.assertEqual((widget.viewport.first, widget.viewport.span), (25, 20))

    def test_escape_restores_drag_and_reentrant_refresh_does_not_reopen_history(self):
        widget = self.widget()
        history = History()
        original = widget._project.to_dict()
        controller = FrameDragController(widget._project, widget._index, history)
        controller.begin('stable-2', 'start').update(35)
        widget.controller, widget._drag = controller, (0, 31, 25, 20)
        widget.canvas = Mock()
        widget.canvas.grab_current.return_value = widget.canvas
        widget.winfo_exists = Mock(return_value=True)
        widget.on_status = Mock()
        widget.on_commit = Mock(side_effect=lambda: widget.refresh())
        self.assertEqual(widget._key(Mock(keysym='Escape', state=0)), 'break')
        self.assertEqual(widget._project.to_dict(), original)
        self.assertEqual(history.undo_stack, [])
        self.assertFalse(widget.dragging)
        widget.on_commit.assert_called_once()

    def test_saved_accent_is_honored_with_readable_text_and_fallback(self):
        self.assertEqual(timeline_palette('#8764C6')[:2], ('#8764C6', '#C8BBE0'))
        accent, pale, ink = timeline_palette('#66CCFF')
        self.assertEqual(accent, '#66CCFF')
        self.assertNotEqual(pale, '#C8BBE0')
        self.assertEqual(ink, '#2B2638')
        for value in ('invalid', None, 42):
            self.assertEqual(timeline_palette(value)[0], '#8764C6')


class TimelineConfigureSchedulingTests(unittest.TestCase):
    """Actual canvas-builder calls with a fake timer; not a Windows FPS test."""
    def setUp(self):
        self.widget = widget = FrameTimeline.__new__(FrameTimeline)
        project, index = make_project()
        self.size = [800, widget.HEIGHT]
        widget.canvas = Mock()
        widget.canvas.winfo_width.side_effect = lambda: self.size[0]
        widget.canvas.winfo_height.side_effect = lambda: self.size[1]
        widget.winfo_exists = Mock(return_value=True)
        widget._destroyed = widget._rendering = False
        widget._resize_job = widget._render_size = None
        widget._index, widget._project = index, project
        widget.viewport = FrameViewport(120, 1, 120)
        widget.rows = timeline_rows(project, index, 'stable-2')
        widget.get_accent = lambda: '#8764C6'
        widget.range_label, widget.scrollbar = Mock(), Mock()
        widget._playhead_frame = None
        widget.controller = widget._drag = None
        widget.collapsed = False
        widget._set_hint = Mock()
        self.jobs = {}
        self.sequence = 0
        def schedule(delay, callback):
            self.sequence += 1
            token = f'resize-{self.sequence}'
            self.jobs[token] = callback
            return token
        widget.after = Mock(side_effect=schedule)
        widget.after_cancel = Mock(side_effect=lambda token: self.jobs.pop(token, None))
        widget._render()
        widget.canvas.reset_mock()

    def configure(self, width=None, height=None):
        if width is not None:self.size[0] = width
        if height is not None:self.size[1] = height
        self.widget._on_canvas_configure(SimpleNamespace(widget=self.widget.canvas,
            width=self.size[0], height=self.size[1], x=123, y=456))

    def flush(self):
        callback = self.jobs.pop(next(iter(self.jobs)))
        callback()

    def test_position_only_and_same_size_events_do_not_rebuild(self):
        for _ in range(100):self.configure()
        self.assertEqual(self.jobs, {})
        self.widget.after.assert_not_called()
        self.widget.canvas.delete.assert_not_called()

    def test_size_burst_has_one_non_restarting_job_and_paints_latest_size(self):
        for width in range(801, 901):self.configure(width)
        self.assertEqual(len(self.jobs), 1)
        self.widget.after.assert_called_once_with(16, self.widget._flush_resize_render)
        self.widget.after_cancel.assert_not_called()
        self.widget.canvas.delete.assert_not_called()
        self.flush()
        self.widget.canvas.delete.assert_called_once_with('all')
        self.assertEqual(self.widget._render_size, (900, self.widget.HEIGHT))
        self.assertEqual(self.jobs, {})

    def test_ongoing_resize_keeps_painting_instead_of_waiting_until_stop(self):
        for batch in range(5):
            for offset in range(10):self.configure(801 + batch * 10 + offset)
            self.flush()
            self.assertEqual(self.widget.canvas.delete.call_count, batch + 1)
        self.assertEqual(self.widget.after.call_count, 5)

    def test_immediate_content_paint_consumes_pending_resize(self):
        self.configure(1000)
        self.widget._render()
        self.assertEqual(self.jobs, {})
        self.widget.after_cancel.assert_called_once()
        self.widget.canvas.delete.assert_called_once_with('all')
        self.configure(1000)
        self.assertEqual(self.widget.after.call_count, 1)

    def test_resize_returning_to_painted_size_cancels_unnecessary_job(self):
        self.configure(1000)
        self.configure(800)
        self.assertEqual(self.jobs, {})
        self.widget.canvas.delete.assert_not_called()

    def test_hit_testing_flushes_latest_geometry_without_waiting_for_timer(self):
        self.configure(1600)
        x = self.widget._x(31) + 1
        y = self.widget.RULER_HEIGHT + self.widget.ROW_HEIGHT + 16
        self.assertEqual(self.widget._hit(x, y), ('stable-2', 'start'))
        self.widget.canvas.delete.assert_called_once_with('all')
        self.assertEqual(self.jobs, {})
        self.assertEqual(self.widget._hit(x, y), ('stable-2', 'start'))
        self.widget.canvas.delete.assert_called_once_with('all')

    def test_destroy_cancels_job_and_late_callback_is_safe(self):
        self.configure(1000)
        callback = next(iter(self.jobs.values()))
        self.widget._destroy(SimpleNamespace(widget=self.widget))
        self.assertEqual(self.jobs, {})
        callback()
        self.widget.canvas.delete.assert_not_called()
        self.assertTrue(self.widget._destroyed)

    def test_height_changes_are_not_treated_as_position_only(self):
        self.configure(height=self.widget.HEIGHT + 10)
        self.flush()
        self.widget.canvas.delete.assert_called_once_with('all')


@unittest.skipUnless(os.environ.get('DISPLAY') or os.name == 'nt', 'Tk desktop unavailable: no display in Linux cloud')
class TimelineTkSmokeTests(unittest.TestCase):
    """Needs real Tk display; intentionally skipped on the headless Linux runner."""
    def test_widget_refresh_collapse_selection_and_destroy(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        project, index = make_project()
        history = History()
        selected = ['stable-2']
        try:
            widget = FrameTimeline(root, get_project=lambda: project, get_index=lambda: index,
                get_history=lambda: history, get_selection=lambda: selected[0],
                on_select=lambda identity: selected.__setitem__(0, identity))
            widget.pack(fill='x')
            root.update_idletasks()
            self.assertEqual(len(widget.rows), 3)
            widget._select('stable-3')
            self.assertEqual(widget.rows[1].identity, 'stable-3')
            self.assertIsNone(widget.rows[2].identity)
            widget.set_collapsed(True)
            self.assertTrue(widget.collapsed)
            widget.set_collapsed(False)
            widget.update_playhead(60)
            widget.zoom(2)
            widget.fit()
            widget.destroy()
            self.assertIsNone(history.group)
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
