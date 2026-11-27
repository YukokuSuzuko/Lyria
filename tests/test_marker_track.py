"""Display-independent marker/controller/Unicode tests; not Windows GUI QA."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from lyricsync.editing import History, mapping_text
from lyricsync.frames import FrameIndex
from lyricsync.marker_track import (MarkerController, MarkerTrack, character_gaps,
    codepoint_at_index, index_at_codepoint, marker_x, hit_marker,
    nearest_character_gap, text_gap_at_point)
from lyricsync.model import Mapping, Project, Translation, Lyric


class FakeText:
    """Tk 8 UTF-16-like columns, deliberately unlike Python character offsets."""
    def __init__(self, text, insert='1.0'):
        self.text, self.insert = text, insert
        self.tk = SimpleNamespace(call=lambda command, subcommand, value: self.units(value))
        self.mapped = self.exists = True
        self.visible = True
        self.forced_index = None

    @staticmethod
    def units(value):return len(value.encode('utf-16-le', 'surrogatepass')) // 2
    def winfo_exists(self):return self.exists
    def winfo_ismapped(self):return self.mapped
    def winfo_rootx(self):return 100
    def winfo_rooty(self):return 200
    def winfo_width(self):return 300
    def winfo_height(self):return 80

    def index(self, index):
        if index == 'insert':return self.insert
        if index.endswith(' -1c'):
            line, column = map(int, index[:-4].split('.'))
            return f'{line}.{max(0, column-1)}'
        if index.startswith('@'):
            if self.forced_index:return self.forced_index
            x, y = map(int, index[1:].split(','))
            lines = self.text.split('\n')
            line = min(len(lines)-1, max(0, y // 20))
            char = min(len(lines[line]), max(0, (x-4) // 10))
            return f'{line+1}.{self.units(lines[line][:char])}'
        return index

    def get(self, first, last):
        assert first == '1.0'
        if last == 'end-1c':return self.text
        line, column = map(int, self.index(last).split('.'))
        lines = self.text.split('\n')
        before = ''.join(value+'\n' for value in lines[:line-1])
        partial = lines[line-1].encode('utf-16-le', 'surrogatepass')[:column*2].decode('utf-16-le', 'surrogatepass')
        return before+partial

    def bbox(self, index):
        if not self.visible:return None
        line, column = map(int, self.index(index).split('.'))
        local = self.text.split('\n')[line-1].encode('utf-16-le', 'surrogatepass')[:column*2].decode('utf-16-le', 'surrogatepass')
        return 4+len(local)*10, (line-1)*20, 10, 20


class MarkerGeometryTests(unittest.TestCase):
    def test_vfr_markers_follow_pts_not_average_fps(self):
        index = FrameIndex([0, .04, .20, .28], .40)
        self.assertAlmostEqual(marker_x(3, index, 224), 112)
        self.assertAlmostEqual(marker_x(2, index, 224), 32)
        self.assertEqual(marker_x(1, index, 224), 12)

    def test_invalid_frame_geometry_never_clamps_to_fake_frame(self):
        index = FrameIndex([0, .1], .2)
        for value in (0, 3, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):marker_x(value, index, 100)
        with self.assertRaises(ValueError):marker_x(1, None, 100)

    def test_hit_marker_requires_near_diamond(self):
        index = FrameIndex([0, .1, .2], .3)
        self.assertEqual(hit_marker([1, 2, 3], index, 324, 112), 2)
        self.assertIsNone(hit_marker([1, 3], index, 324, 112))
        self.assertIsNone(hit_marker([], index, 324, 12))

    def test_tiny_track_does_not_invent_movable_frames(self):
        index = FrameIndex([0, .1, .2], .3)
        self.assertEqual(marker_x(3, index, 10), 12)
        self.assertEqual(hit_marker([1, 2, 3], index, 10, 12, selected=2), 2)

    def test_python_offsets_do_not_use_utf16_columns(self):
        widget = FakeText('甲😀乙', '1.3')
        self.assertEqual(codepoint_at_index(widget, 'insert'), 2)
        self.assertEqual(index_at_codepoint(widget, widget.text, 2), '1.3')
        self.assertEqual(index_at_codepoint(widget, widget.text, 3), '1.4')

    def test_multiline_offsets_preserve_newlines(self):
        widget = FakeText('😀\n甲e\u0301乙', '2.1')
        self.assertEqual(codepoint_at_index(widget, 'insert'), 3)
        self.assertEqual(index_at_codepoint(widget, widget.text, 3), '2.1')
        self.assertEqual(index_at_codepoint(widget, widget.text, 1), '1.2')

    def test_caret_inside_surrogate_pair_rejected(self):
        widget = FakeText('甲😀乙', '1.2')
        with self.assertRaises(ValueError):codepoint_at_index(widget, 'insert')

    def test_mouse_half_glyph_hit_uses_python_character_gap(self):
        widget = FakeText('甲😀乙')
        before = text_gap_at_point(widget, 116, 210)
        after = text_gap_at_point(widget, 122, 210)
        self.assertEqual((before.character, before.index), (1, '1.1'))
        self.assertEqual((after.character, after.index), (2, '1.3'))
        self.assertEqual((after.x, after.y, after.height), (24, 0, 20))

    def test_mouse_surrogate_half_recovers_complete_prefix(self):
        widget = FakeText('甲😀乙')
        widget.forced_index = '1.2'
        gap = text_gap_at_point(widget, 122, 210)
        self.assertEqual((gap.character, gap.index), (2, '1.3'))

    def test_outside_hidden_or_unpainted_text_is_not_a_drop_target(self):
        widget = FakeText('甲乙')
        for point in ((99, 210), (400, 210), (110, 199), (110, 280)):
            with self.subTest(point=point):self.assertIsNone(text_gap_at_point(widget, *point))
        widget.mapped = False
        self.assertIsNone(text_gap_at_point(widget, 110, 210))
        widget.mapped = True;widget.visible = False
        self.assertIsNone(text_gap_at_point(widget, 110, 210))
        self.assertIsNone(text_gap_at_point(None, 110, 210))

    def test_common_clusters_are_indivisible(self):
        for cluster in ('e\u0301', '✈️', '👍🏽', '👨\u200d👩\u200d👧\u200d👦', '🇨🇳', '\u1100\u1161\u11a8'):
            with self.subTest(cluster=cluster):
                self.assertEqual(character_gaps('甲'+cluster+'乙'), [0, 1, len(cluster)+1, len(cluster)+2])

    def test_flag_pairs_do_not_merge_adjacent_flags(self):
        self.assertEqual(character_gaps('🇨🇳🇯🇵'), [0, 2, 4])

    def test_empty_crlf_and_safe_gap_snapping(self):
        self.assertEqual(character_gaps(''), [0])
        self.assertEqual(character_gaps('甲\r\n乙'), [0, 1, 3, 4])
        self.assertEqual(nearest_character_gap('甲e\u0301乙', 2), 3)


class MarkerControllerTests(unittest.TestCase):
    def setUp(self):
        self.index = FrameIndex([i/30 for i in range(600)], 20)
        self.project = Project(translations=[Translation('t', '甲😀乙e\u0301丙')],
            lyrics=[Lyric('original','Original evidence',None,None)],
            mappings=[Mapping('m', ['t'], lyric_ids=['original'], start=self.index.stamp(300),
                              end=self.index.interval(450, 450)[1], confirmed=True, locked=True)],
            settings={'frame_markers': [372]})
        self.history = History()
        self.selected = 'm'
        self.frame = 372
        self.editable = True
        self.changed, self.select = Mock(), Mock()
        self.controller = MarkerController(get_project=lambda: self.project, get_index=lambda: self.index,
            get_history=lambda: self.history, get_selection=lambda: self.selected,
            get_current_frame=lambda: self.frame, on_change=self.changed,
            on_select=self.select, can_edit=lambda: self.editable)

    def text(self):return mapping_text(self.project, self.project.mappings[0])

    def assert_unchanged(self, operation):
        before = deepcopy(self.project.to_dict())
        history = deepcopy(self.history.undo_stack)
        self.changed.reset_mock();self.select.reset_mock()
        with self.assertRaises(ValueError):operation()
        self.assertEqual(self.project.to_dict(), before)
        self.assertEqual(self.history.undo_stack, history)
        self.changed.assert_not_called();self.select.assert_not_called()

    def test_split_uses_inclusive_ui_frames_and_retains_original_marker(self):
        original = deepcopy(self.project.translations)
        drag = self.controller.begin(372, self.text())
        left, right = self.controller.split(drag, 2, self.text())
        self.assertEqual((self.index.first(left.start), self.index.last(left.end)), (300, 371))
        self.assertEqual((self.index.first(right.start), self.index.last(right.end)), (372, 450))
        self.assertEqual(mapping_text(self.project, left), '甲😀')
        self.assertEqual(mapping_text(self.project, right), '乙e\u0301丙')
        self.assertEqual(self.project.translations, original)
        self.assertEqual(self.project.settings['frame_markers'], [372])
        self.assertEqual(left.lyric_ids, ['original']);self.assertEqual(right.lyric_ids, ['original'])
        self.assertTrue(left.locked and right.locked)
        self.assertFalse(left.confirmed or right.confirmed)
        self.assertEqual(len(self.history.undo_stack), 1)
        self.changed.assert_called_once_with();self.select.assert_called_once_with(right.id)

    def test_split_undo_redo_restores_exact_snapshot(self):
        before = deepcopy(self.project.to_dict())
        self.controller.split(self.controller.begin(372, self.text()), 2, self.text())
        after = deepcopy(self.project.to_dict())
        self.project, label = self.history.restore(self.project)
        self.assertEqual(label, '按标记点切分字幕')
        self.assertEqual(self.project.to_dict(), before)
        self.project, _ = self.history.restore(self.project, redo=True)
        self.assertEqual(self.project.to_dict(), after)

    def test_missing_outside_boundaries_stay_unknown(self):
        for start, end in ((None, None), (None, 15), (9, None)):
            with self.subTest(start=start, end=end):
                self.setUp()
                row = self.project.mappings[0];row.start, row.end = start, end
                left, right = self.controller.split(self.controller.begin(372, self.text()), 2, self.text())
                self.assertEqual((left.start, right.end), (start, end))
                self.assertEqual((left.end, right.start), (self.index.stamp(372),)*2)

    def test_known_outside_or_touching_boundaries_rejected(self):
        for frame in (299, 300, 451, 452):
            with self.subTest(frame=frame):
                self.project.settings['frame_markers'] = [frame]
                self.assert_unchanged(lambda: self.controller.begin(frame, self.text()))

    def test_unknown_one_side_does_not_bypass_known_boundary(self):
        self.project.mappings[0].end = None
        self.project.settings['frame_markers'] = [300]
        self.assert_unchanged(lambda: self.controller.begin(300, self.text()))
        self.project.mappings[0].start = None
        self.project.mappings[0].end = self.index.stamp(300)
        self.assert_unchanged(lambda: self.controller.begin(300, self.text()))

    def test_invalid_cuts_do_not_create_history_or_change_project(self):
        drag = self.controller.begin(372, self.text())
        for cut in (0, len(self.text()), -1, 100, 4, True, 1.5):
            with self.subTest(cut=cut):self.assert_unchanged(lambda: self.controller.split(drag, cut, self.text()))

    def test_linebreak_cut_preserves_literal_source(self):
        self.project.translations[0].text = '甲😀\n乙丙'
        left, right = self.controller.split(self.controller.begin(372, self.text()), 3, self.text())
        self.assertEqual(mapping_text(self.project, left), '甲😀\n')
        self.assertEqual(mapping_text(self.project, right), '乙丙')

    def test_blank_half_cannot_be_split(self):
        self.project.translations[0].text = ' \n甲乙'
        drag = self.controller.begin(372, self.text())
        self.assert_unchanged(lambda: self.controller.split(drag, 2, self.text()))

    def test_new_selection_during_drag_is_not_silently_edited(self):
        drag = self.controller.begin(372, self.text())
        self.project.mappings.append(Mapping('other', ['t']))
        self.selected = 'other'
        self.assert_unchanged(lambda: self.controller.split(drag, 2, self.text()))

    def test_identical_reloaded_project_cancels_drag(self):
        drag = self.controller.begin(372, self.text())
        self.project = Project.from_dict(self.project.to_dict())
        self.assert_unchanged(lambda: self.controller.split(drag, 2, self.text()))

    def test_identical_rebuilt_index_cancels_drag(self):
        drag = self.controller.begin(372, self.text())
        self.index = FrameIndex(list(self.index.stamps), self.index.duration)
        self.assert_unchanged(lambda: self.controller.split(drag, 2, self.text()))

    def test_changed_text_or_boundary_cancels_drag(self):
        for changed in ('text', 'start', 'end'):
            with self.subTest(changed=changed):
                self.setUp();drag = self.controller.begin(372, self.text())
                row = self.project.mappings[0]
                if changed == 'text':row.text_override = '甲😀乙新文本'
                else:setattr(row, changed, getattr(row, changed)+.01)
                self.assert_unchanged(lambda: self.controller.split(drag, 2, self.text()))

    def test_changed_source_pairing_cancels_drag(self):
        drag = self.controller.begin(372, self.text())
        self.project.mappings[0].lyric_ids = ['new-pairing']
        self.assert_unchanged(lambda: self.controller.split(drag, 2, self.text()))

    def test_deleted_marker_cancels_drag(self):
        drag = self.controller.begin(372, self.text())
        self.project.settings['frame_markers'] = []
        self.assert_unchanged(lambda: self.controller.split(drag, 2, self.text()))

    def test_uncommitted_widget_text_never_overwrites_source(self):
        self.assert_unchanged(lambda: self.controller.begin(372, '不同中文'))
        drag = self.controller.begin(372, self.text())
        self.assert_unchanged(lambda: self.controller.split(drag, 2, '不同中文'))

    def test_busy_missing_index_or_selection_rejects_safely(self):
        self.editable = False
        self.assert_unchanged(lambda: self.controller.begin(372, self.text()))
        self.editable = True;self.selected = None
        self.assert_unchanged(lambda: self.controller.begin(372, self.text()))
        self.selected = 'm';self.index = None
        self.assert_unchanged(lambda: self.controller.begin(372, self.text()))
        self.assertEqual(self.controller.markers(), [])

    def test_add_delete_use_actual_integer_frame_and_history(self):
        self.frame = 390
        self.assertEqual(self.controller.add_current(), 390)
        self.assertEqual(self.project.settings['frame_markers'], [372, 390])
        self.assertEqual(len(self.history.undo_stack), 1)
        self.controller.add_current()
        self.assertEqual(len(self.history.undo_stack), 1)
        self.controller.delete(390)
        self.assertEqual(self.project.settings['frame_markers'], [372])
        self.assertEqual(len(self.history.undo_stack), 2)

    def test_invalid_current_frame_does_not_guess_or_clamp(self):
        for frame in (0, 601, 372.0, True, None):
            with self.subTest(frame=frame):
                self.frame = frame
                self.assert_unchanged(self.controller.add_current)

    def test_invalid_stored_markers_are_not_rendered_or_removed(self):
        self.project.settings['frame_markers'] = [True, 372, 601, -2, 372, 4.5]
        before = deepcopy(self.project.to_dict())
        self.assertEqual(self.controller.markers(), [372])
        self.assertEqual(self.project.to_dict(), before)

    def test_keyboard_shortcut_ignores_text_and_entry(self):
        for widget_class in ('Text', 'Entry', 'TEntry', 'TCombobox', 'TSpinbox'):
            event = SimpleNamespace(widget=SimpleNamespace(winfo_class=lambda: widget_class))
            self.assertIsNone(MarkerTrack.add_current_marker(None, event))

    def test_drag_cancel_clears_only_transient_ui_and_grab(self):
        ghost, indicator, canvas = Mock(), Mock(), Mock()
        canvas.grab_current.return_value = canvas
        harness = SimpleNamespace(_scroll_job='timer', _ghost=ghost, _indicator=indicator,
                                  _drag=object(), _press=(1, 2, 372), _pointer=(10, 20),
                                  canvas=canvas, after_cancel=Mock())
        before = deepcopy(self.project.to_dict())
        self.assertEqual(MarkerTrack.cancel_drag(harness), 'break')
        harness.after_cancel.assert_called_once_with('timer')
        ghost.destroy.assert_called_once_with();indicator.destroy.assert_called_once_with()
        canvas.grab_release.assert_called_once_with()
        self.assertIsNone(harness._drag);self.assertIsNone(harness._pointer)
        self.assertEqual(self.project.to_dict(), before)

    def test_caret_split_uses_actual_text_index_without_second_dialog(self):
        text = self.text()
        widget = FakeText(text, '1.3')  # after supplementary-plane emoji
        harness = SimpleNamespace(cancel_drag=Mock(), on_begin=Mock(), _text_ready=lambda: text,
            controller=self.controller, selected_frame=372, _target=widget, on_status=Mock(), refresh=Mock())
        self.assertEqual(MarkerTrack.split_at_caret(harness), 'break')
        harness.on_begin.assert_called_once_with()
        self.assertEqual(mapping_text(self.project, self.project.mappings[0]), '甲😀')
        self.assertEqual(len(self.history.undo_stack), 1)
        harness.refresh.assert_called_once_with()

    def test_invalid_drop_outside_text_has_no_side_effect(self):
        before = deepcopy(self.project.to_dict())
        text = self.text()
        harness = SimpleNamespace(_drag=self.controller.begin(372, text), _target=FakeText(text),
            _text_ready=lambda: text, controller=self.controller, cancel_drag=Mock(),
            on_status=Mock(), refresh=Mock())
        MarkerTrack._on_release(harness, SimpleNamespace(x_root=50, y_root=210))
        self.assertEqual(self.project.to_dict(), before)
        self.assertEqual(self.history.undo_stack, [])
        self.changed.assert_not_called()
        harness.cancel_drag.assert_called_once_with()
        self.assertIn('未拖放到当前字幕文字上，操作已取消', harness.on_status.call_args.args[0])

    def test_valid_drop_cleans_transients_before_edit_refresh(self):
        text = self.text()
        harness = SimpleNamespace(_drag=self.controller.begin(372, text), _target=FakeText(text),
            _text_ready=lambda: text, controller=self.controller, on_status=Mock(), refresh=Mock())
        harness.cancel_drag = lambda: setattr(harness, '_drag', None)
        self.controller.on_change = lambda: self.assertIsNone(harness._drag)
        MarkerTrack._on_release(harness, SimpleNamespace(x_root=122, y_root=210))
        self.assertEqual(mapping_text(self.project, self.project.mappings[0]), '甲😀')
        self.assertEqual(len(self.history.undo_stack), 1)
        self.assertEqual(self.project.settings['frame_markers'], [372])

    def test_marker_hit_pauses_once_and_empty_track_does_not_pause(self):
        canvas = Mock();canvas.winfo_width.return_value = 500
        harness = SimpleNamespace(cancel_drag=Mock(), canvas=canvas, controller=self.controller,
            selected_frame=372, on_begin=Mock(), select_marker=Mock(), _press=None)
        x = marker_x(372, self.index, 500)
        MarkerTrack._on_press(harness, SimpleNamespace(x=x, x_root=x+100, y_root=210))
        harness.on_begin.assert_called_once_with()
        self.assertEqual(harness._press, (x+100, 210, 372))
        harness.on_begin.reset_mock()
        MarkerTrack._on_press(harness, SimpleNamespace(x=12, x_root=112, y_root=210))
        harness.on_begin.assert_not_called()

    def test_autoscroll_is_target_local_and_has_no_decoder_callback(self):
        widget = FakeText(self.text());widget.yview_scroll = Mock()
        harness = SimpleNamespace(_drag=object(), _pointer=(110, 201), _target=widget,
                                  _scroll_job='timer', _update_gap=Mock(), cancel_drag=Mock())
        MarkerTrack._autoscroll(harness)
        widget.yview_scroll.assert_called_once_with(-1, 'units')
        widget.yview_scroll.reset_mock();harness._pointer = (99, 201)
        MarkerTrack._autoscroll(harness)
        widget.yview_scroll.assert_not_called()

    def test_focus_loss_cancels_without_editing(self):
        before = deepcopy(self.project.to_dict())
        harness = SimpleNamespace(_drag=object(), cancel_drag=Mock(), on_status=Mock())
        MarkerTrack._focus_out(harness)
        harness.cancel_drag.assert_called_once_with()
        self.assertIn('已取消拖动标记点', harness.on_status.call_args.args[0])
        self.assertEqual(self.project.to_dict(), before)


class MarkerConfigureSchedulingTests(unittest.TestCase):
    """Coalesce actual marker render calls without touching marker edit semantics."""
    def setUp(self):
        self.widget = widget = MarkerTrack.__new__(MarkerTrack)
        self.index = FrameIndex([i / 30 for i in range(120)], 4.0)
        self.size = [800, 27]
        widget.canvas = Mock()
        widget.canvas.winfo_width.side_effect = lambda: self.size[0]
        widget.canvas.winfo_height.side_effect = lambda: self.size[1]
        widget._destroyed = False
        widget._resize_job = widget._render_size = None
        widget.selected_frame = widget._press = None
        widget.get_accent = lambda: '#8764C6'
        widget.controller = SimpleNamespace(get_index=lambda: self.index,
            markers=lambda: [20, 40, 60], get_current_frame=lambda: 30)
        widget.label, widget.on_status, widget.on_begin = Mock(), Mock(), Mock()
        widget.cancel_drag, widget._unbind_target = Mock(), Mock()
        self.jobs = {}
        self.sequence = 0
        def schedule(delay, callback):
            self.sequence += 1
            token = f'resize-{self.sequence}'
            self.jobs[token] = callback
            return token
        widget.after = Mock(side_effect=schedule)
        widget.after_cancel = Mock(side_effect=lambda token: self.jobs.pop(token, None))
        widget.render()
        widget.canvas.reset_mock()

    def configure(self, width=None, height=None):
        if width is not None:self.size[0] = width
        if height is not None:self.size[1] = height
        self.widget._on_canvas_configure(SimpleNamespace(widget=self.widget.canvas,
            width=self.size[0], height=self.size[1], x=123, y=456))

    def flush(self):
        callback = self.jobs.pop(next(iter(self.jobs)))
        callback()

    def full_rebuilds(self):
        return sum(call.args == ('all',) for call in self.widget.canvas.delete.call_args_list)

    def test_position_only_and_same_size_events_do_not_rebuild(self):
        for _ in range(100):self.configure()
        self.assertEqual(self.jobs, {})
        self.widget.after.assert_not_called()
        self.assertEqual(self.full_rebuilds(), 0)

    def test_size_burst_has_one_non_restarting_job_and_paints_latest_size(self):
        for width in range(801, 901):self.configure(width)
        self.assertEqual(len(self.jobs), 1)
        self.widget.after.assert_called_once_with(16, self.widget._flush_resize_render)
        self.widget.after_cancel.assert_not_called()
        self.assertEqual(self.full_rebuilds(), 0)
        self.flush()
        self.assertEqual(self.full_rebuilds(), 1)
        self.assertEqual(self.widget._render_size, (900, 27))
        self.assertEqual(self.jobs, {})

    def test_ongoing_resize_keeps_painting_instead_of_waiting_until_stop(self):
        for batch in range(5):
            for offset in range(10):self.configure(801 + batch * 10 + offset)
            self.flush()
            self.assertEqual(self.full_rebuilds(), batch + 1)
        self.assertEqual(self.widget.after.call_count, 5)

    def test_immediate_content_paint_consumes_pending_resize(self):
        self.configure(1000)
        self.widget.render()
        self.assertEqual(self.jobs, {})
        self.widget.after_cancel.assert_called_once()
        self.assertEqual(self.full_rebuilds(), 1)
        self.configure(1000)
        self.assertEqual(self.widget.after.call_count, 1)

    def test_resize_returning_to_painted_size_cancels_unnecessary_job(self):
        self.configure(1000)
        self.configure(800)
        self.assertEqual(self.jobs, {})
        self.assertEqual(self.full_rebuilds(), 0)

    def test_marker_hit_uses_latest_size_and_content_render_consumes_resize(self):
        self.configure(1600)
        x = marker_x(40, self.index, 1600)
        self.widget._on_press(SimpleNamespace(x=x, x_root=x + 100, y_root=100))
        self.assertEqual(self.widget.selected_frame, 40)
        self.assertEqual(self.widget._press, (x + 100, 100, 40))
        self.assertEqual(self.full_rebuilds(), 1)
        self.assertEqual(self.jobs, {})

    def test_destroy_cancels_job_and_late_callback_is_safe(self):
        self.configure(1000)
        callback = next(iter(self.jobs.values()))
        self.widget._on_destroy(SimpleNamespace(widget=self.widget))
        self.assertEqual(self.jobs, {})
        callback()
        self.assertEqual(self.full_rebuilds(), 0)
        self.assertTrue(self.widget._destroyed)

    def test_height_changes_are_not_treated_as_position_only(self):
        self.configure(height=30)
        self.flush()
        self.assertEqual(self.full_rebuilds(), 1)


if __name__ == '__main__':unittest.main()
