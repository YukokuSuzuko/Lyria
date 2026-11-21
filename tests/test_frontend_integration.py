"""Actual desktop adapter methods with headless host objects, not rendered QA."""
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lyricsync.frontend import DesktopFrontend
from lyricsync.frames import FrameIndex
from lyricsync.model import Mapping, Project, Translation
from lyricsync.workspace import WorkspaceActions


class Variable:
    def __init__(self, value='', hook=lambda: None):
        self.value, self.hook = value, hook
    def get(self): return self.value
    def set(self, value):
        self.hook()
        self.value = value


class Tree:
    def __init__(self, identities):
        self.rows = {identity: {'tags': (), 'values': ()} for identity in identities}
        self.writes = []
        self.probes = []
        self.selected = ()
    def exists(self, identity):
        self.probes.append(identity)
        return identity in self.rows
    def item(self, identity, option=None, **options):
        if options:
            self.rows[identity].update(options)
            self.writes.append((identity, options))
        return self.rows[identity].get(option) if option else dict(self.rows[identity])
    def selection(self): return self.selected
    def selection_set(self, identity): self.selected = (identity,)
    def see(self, identity): pass


class Host(DesktopFrontend):
    def __init__(self, home):
        self.home = Path(home)
        self.root = None
        self.project = Project(translations=[Translation('t', '待保存的中文')],
            mappings=[Mapping('m', ['t'], start=.1, end=.5)])
        self.project_path = None
        self.project_saver = SimpleNamespace(submit=Mock(return_value=1), flush=Mock())
        self.save_status = Variable()
        self.status = Variable()
        self.history = object()
        self.drafts = {'m': 'draft'}
        self.detail_id = 'm'
        self.frame_index = FrameIndex([i / 10 for i in range(20)], 2)
        self.index_generation = 0
        self.audio_generation = 0
        self.audio = SimpleNamespace(close=Mock())
        self.ai_enabled = Variable(False)
        self.inputs = {key: Variable() for key in ('video', 'ktv', 'translation')}
        self.steps = SimpleNamespace(select=Mock())
        self.guard = Mock(return_value=True)
        self.error = Mock()
        self.autosave = Mock()
        self.refresh = Mock()
        self.draw = Mock()
        self.load_video = Mock()
        self.pause_playback = Mock()
        self.cancel_pending_seek = Mock()
        self.open = Mock()
        self.schedule_autosave = Mock()
        self.busy = self.close_requested = self.playing = False
        self.scrubbing = False
        self.flush_chinese = Mock(return_value=True)
        self.ui_settings = {}
        self.mode_switch = SimpleNamespace(cancel_transition=Mock(), transition=Mock())
        self.step_tabs = SimpleNamespace(cancel_transition=Mock(), transition=Mock())
    def ensure_project_path(self): return self.home / 'project.lyricsync.json'


class ReplacementSafetyTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.app = Host(self.folder.name)

    def drop(self, assignments):
        with patch('lyricsync.file_drop.resolve_dropped_files', return_value=assignments), \
                patch('lyricsync.frontend.messagebox.askyesno', return_value=True), \
                patch('lyricsync.ai_config.load', return_value={}):
            self.app.receive_dropped_files(['synthetic placeholder'])

    def test_submission_failure_cannot_be_mistaken_for_successful_empty_flush(self):
        before = deepcopy(self.app.project.to_dict())
        history = self.app.history
        self.app.project_saver.submit.side_effect = OSError('disk full')
        self.drop({'video': str(Path(self.folder.name) / 'new.mp4')})
        self.assertEqual(self.app.project.to_dict(), before)
        self.assertIs(self.app.history, history)
        self.assertEqual(self.app.inputs['video'].get(), '')
        self.app.project_saver.flush.assert_not_called()
        self.app.error.assert_called_once()
        self.app.load_video.assert_not_called()

    def test_flush_failure_preserves_project_inputs_and_history(self):
        before = deepcopy(self.app.project.to_dict())
        history = self.app.history
        self.app.project_saver.flush.side_effect = OSError('write failure')
        self.drop({'translation': str(Path(self.folder.name) / 'new.txt')})
        self.assertEqual(self.app.project.to_dict(), before)
        self.assertIs(self.app.history, history)
        self.app.error.assert_called_once()
        self.app.refresh.assert_not_called()

    def test_project_drop_also_requires_successful_persistence(self):
        original = self.app.project
        self.app.project_saver.submit.side_effect = OSError('disk full')
        self.drop({'project': 'new.lyricsync.json'})
        self.assertIs(self.app.project, original)
        self.app.open.assert_not_called()

    def test_success_saves_original_before_assigning_new_inputs(self):
        original = self.app.project
        snapshots = []
        self.app.project_saver.submit.side_effect = lambda project, *args: snapshots.append(deepcopy(project.to_dict())) or 7
        path = str(Path(self.folder.name) / 'new.mp4')
        self.drop({'video': path})
        self.assertEqual(snapshots, [original.to_dict()])
        self.assertEqual(self.app.save_revision, 7)
        self.app.project_saver.flush.assert_called_once()
        self.assertIsNot(self.app.project, original)
        self.assertEqual(self.app.project.mappings, [])
        self.assertEqual(self.app.inputs['video'].get(), path)
        self.app.load_video.assert_called_once()
        self.assertEqual(original.mappings[0].start, .1)

    def test_cancelling_roles_or_workspace_confirmation_is_nonmutating(self):
        original = self.app.project
        self.drop(None)
        with patch('lyricsync.file_drop.resolve_dropped_files', return_value={'video': 'new.mp4'}), \
                patch('lyricsync.frontend.messagebox.askyesno', return_value=False):
            self.app.receive_dropped_files(['new.mp4'])
        self.assertIs(self.app.project, original)
        self.app.project_saver.submit.assert_not_called()

    def test_native_new_and_open_actions_abort_on_failed_save_gate(self):
        from lyricsync.gui import Application
        self.app.project_saver.submit.side_effect = OSError('disk full')
        original = self.app.project
        Application.new(self.app)
        Application.open(self.app, 'another.lyricsync.json')
        self.assertIs(self.app.project, original)
        self.app.pause_playback.assert_not_called()
        self.assertEqual(self.app.error.call_count, 2)


class FrontendHotPathTests(unittest.TestCase):
    def test_drag_updates_only_affected_rows_and_no_video_or_text_redraw(self):
        app = Host('.')
        app.project = Project(
            translations=[Translation(f't{i}', f'句{i}') for i in range(1000)],
            mappings=[Mapping(f'm{i}', [f't{i}'], start=i, end=i+.5) for i in range(1000)])
        app.frame_index = FrameIndex([i / 10 for i in range(10010)], 1001)
        app.results = Tree(m.id for m in app.project.mappings)
        app.selected_mapping = lambda: app.project.mappings[500]
        app.overlapping = set()
        app.frame_label = lambda stamp, end: str(app.frame_index.last(stamp) if end else app.frame_index.first(stamp))
        app._loading_edit = False
        seen = []
        for field in ('start', 'end', 'first_frame', 'last_frame'):
            setattr(app, field, Variable(hook=lambda: seen.append(app._loading_edit)))
        app.timeline_edit_changed('m500')
        self.assertEqual(len(app.results.writes), 1)
        self.assertEqual(len(app.results.probes), 1)
        self.assertEqual(seen, [True] * 4)
        self.assertFalse(app._loading_edit)
        self.assertEqual(app.first_frame.get(), '5001')
        self.assertEqual(app.last_frame.get(), '5005')
        app.draw.assert_not_called()
        app.refresh.assert_not_called()
        app.schedule_autosave.assert_called_once()

    def test_playback_does_not_touch_unchanged_tree_rows(self):
        app = Host('.')
        app.project.mappings.append(Mapping('next', ['t'], start=.5, end=.9))
        app.results = Tree(['m', 'next'])
        app.current_hint = Variable()
        app.timeline = SimpleNamespace(set=Mock())
        app.overlapping = set()
        app.update_frontend_playhead = Mock()
        app.stamp = .2
        for _ in range(60):
            WorkspaceActions.sync_playhead(app, follow=False)
        self.assertEqual(len(app.results.writes), 1)
        self.assertEqual(app.results.rows['m']['tags'], ('playing',))
        app.stamp = .6
        WorkspaceActions.sync_playhead(app, follow=False)
        self.assertEqual(len(app.results.writes), 3)
        self.assertEqual(app.results.rows['m']['tags'], ())
        self.assertEqual(app.results.rows['next']['tags'], ('playing',))

    def test_playback_follow_cannot_steal_selection_during_edge_drag(self):
        app = Host('.')
        app.frame_timeline = SimpleNamespace(dragging=True)
        app.results = Tree(['m'])
        app.current_hint = Variable()
        app.timeline = SimpleNamespace(set=Mock())
        app.overlapping = set()
        app.stamp = .2
        app.update_frontend_playhead = Mock()
        app.select_mapping = Mock()
        WorkspaceActions.sync_playhead(app, follow=True)
        app.select_mapping.assert_not_called()
        self.assertEqual(app.results.selection(), ())

    def test_drag_start_cancels_stale_seek_and_animation(self):
        app = Host('.')
        app.playing = True
        self.assertTrue(app.frontend_can_edit())
        app.pause_playback.assert_called_once()
        app.cancel_pending_seek.assert_called_once()
        app.mode_switch.cancel_transition.assert_called_once()

    def test_marker_begin_cancels_stale_seek_once(self):
        app = Host('.')
        app.begin_marker_interaction()
        app.pause_playback.assert_called_once()
        app.cancel_pending_seek.assert_called_once()

    def test_busy_or_ime_composition_never_starts_editing(self):
        app = Host('.')
        app.busy = True
        self.assertFalse(app.frontend_can_edit())
        app.busy = False
        app.flush_chinese.return_value = False
        self.assertFalse(app.frontend_can_edit())
        app.cancel_pending_seek.assert_not_called()


class NavigationMotionTests(unittest.TestCase):
    def test_switch_animation_never_resolves_or_reconfigures_page_geometry(self):
        app = Host('.')
        app.notebook, app.steps = object(), object()
        app.root = Mock()
        WorkspaceActions.animate_page(app, SimpleNamespace(widget=app.steps))
        app.step_tabs.transition.assert_called_once()
        app.root.nametowidget.assert_not_called()
        app.root.after.assert_not_called()

    def test_disable_playback_seek_and_frame_drag_all_cancel_transition(self):
        for field in ('disabled', 'playing', 'scrubbing', 'timeline_dragging', 'frame_drag'):
            with self.subTest(field=field):
                app = Host('.')
                app.notebook, app.steps = object(), object()
                if field == 'disabled': app.ui_settings['animations'] = False
                elif field == 'frame_drag': app.frame_timeline = SimpleNamespace(dragging=True)
                else: setattr(app, field, True)
                WorkspaceActions.animate_page(app)
                app.mode_switch.cancel_transition.assert_called_once()
                app.mode_switch.transition.assert_not_called()

    def test_play_start_finishes_edits_and_stops_existing_transition(self):
        from lyricsync.gui import Application
        app = Host('.')
        app.preview_video = SimpleNamespace(info={'duration': 5})
        app.stamp = 1
        app.sound_enabled = Variable(False)
        app.play_button = SimpleNamespace(configure=Mock())
        app.play_tick = Mock()
        with patch('lyricsync.gui.PlaybackBuffer', return_value=object()):
            Application.toggle_play(app)
        self.assertTrue(app.playing)
        app.mode_switch.cancel_transition.assert_called_once()
        app.step_tabs.cancel_transition.assert_called_once()

    def test_invalid_saved_accent_has_consistent_moonlit_fallback(self):
        app = Host('.')
        for color in (None, '#bad', 'invalid'):
            app.ui_settings['accent'] = color
            self.assertEqual(app.accent_color(), '#8764C6')
        app.ui_settings['accent'] = '#66CCFF'
        self.assertEqual(app.accent_color(), '#66CCFF')


if __name__ == '__main__':
    unittest.main()
