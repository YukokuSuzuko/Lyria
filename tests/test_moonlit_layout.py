"""Headless contracts only: these do not claim rendered Tk / Windows acceptance."""
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from lyricsync import appearance, review_layout


class MotionProbe(appearance._CanvasMotion):
    def __init__(self):
        self.jobs = {}
        self.moves = []
        self.sequence = 0

    def after(self, delay, callback):
        self.sequence += 1
        self.jobs[self.sequence] = (delay, callback)
        return self.sequence

    def after_cancel(self, identity):
        self.jobs.pop(identity, None)

    def _paint_motion(self, position, fraction):
        self.moves.append((position, fraction))

    def advance(self):
        identity = min(self.jobs)
        _, callback = self.jobs.pop(identity)
        callback()


class MoonlitMotionTests(unittest.TestCase):
    def test_motion_is_bounded_and_finishes_exactly(self):
        probe = MotionProbe()
        probe._prime_motion(0)
        probe._prime_motion(1)
        now = [0.0]
        with patch.object(appearance.time, 'monotonic', side_effect=lambda: now[0]):
            probe.transition()
            self.assertEqual(probe.moves[-1], (0, 0.0))
            frames = 0
            while probe.jobs:
                self.assertEqual(next(iter(probe.jobs.values()))[0], 16)
                now[0] += .016
                probe.advance()
                frames += 1
                self.assertLessEqual(frames, 12)
            self.assertEqual(probe.moves[-1], (1.0, 1.0))
            self.assertEqual(probe._motion_position, 1)

    def test_retarget_starts_at_visible_position_and_cancels_stale_tick(self):
        probe = MotionProbe()
        probe._prime_motion(0)
        probe._prime_motion(3)
        now = [0.0]
        with patch.object(appearance.time, 'monotonic', side_effect=lambda: now[0]):
            probe.transition()
            stale = next(iter(probe.jobs.values()))[1]
            now[0] = .06
            probe.advance()
            visible = probe._motion_position
            self.assertGreater(visible, 0)
            self.assertLess(visible, 3)
            probe._prime_motion(1)
            probe.transition()
            self.assertEqual(probe.moves[-1], (visible, 0.0))
            before = list(probe.moves)
            stale()
            self.assertEqual(probe.moves, before)
            self.assertEqual(len(probe.jobs), 1)
            probe.cancel_transition()
            self.assertEqual(probe.moves[-1], (1, 1.0))
            self.assertFalse(probe.jobs)

    def test_no_tick_when_selection_does_not_change(self):
        probe = MotionProbe()
        probe._prime_motion(2)
        probe.transition()
        self.assertEqual(probe.moves, [(2, 1.0)])
        self.assertFalse(probe.jobs)


class FakeVariable:
    def __init__(self, master=None, value=None):
        self.value = value
        self.traces = []

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        for callback in self.traces:
            callback()

    def trace_add(self, mode, callback):
        self.traces.append(callback)
        return str(len(self.traces))


class FakeWidget:
    def __init__(self, master=None, **kwargs):
        self.master = master
        self.options = kwargs
        self.children = []
        self.bindings = {}
        self.packed = None
        self.gridded = None
        self.menu_items = []
        self.tags = {}
        self.text = ''
        if master is not None:
            master.children.append(self)

    def configure(self, **kwargs): self.options.update(kwargs)
    def pack(self, **kwargs): self.packed = kwargs
    def pack_forget(self): self.packed = None
    def grid(self, **kwargs): self.gridded = kwargs
    def bind(self, event, callback, add=None): self.bindings.setdefault(event, []).append(callback)
    def winfo_toplevel(self): return self.master.winfo_toplevel() if self.master else self
    def rowconfigure(self, *args, **kwargs): pass
    def columnconfigure(self, *args, **kwargs): pass
    def column(self, *args, **kwargs): pass
    def add(self, widget, **kwargs): pass
    def select(self, widget): self.selected = widget
    def withdraw(self): self.hidden = True
    def title(self, value): self.window_title = value
    def geometry(self, value): self.window_geometry = value
    def protocol(self, key, callback): pass
    def tag_configure(self, key, **kwargs): self.tags[key] = kwargs
    def xview(self, *args): pass
    def set(self, *args): pass
    def delete(self, *args): self.text = ''
    def insert(self, index, value): self.text += value
    def schedule_fit(self): pass
    def add_command(self, **kwargs): self.menu_items.append(kwargs)
    def add_checkbutton(self, **kwargs): self.menu_items.append(kwargs)
    def add_separator(self): self.menu_items.append({'separator': True})


class FakeBody(FakeWidget):
    def __init__(self, master, *args, **kwargs):
        super().__init__(master, **kwargs)
        self.body = FakeWidget(self)


class FakeDisclosure(FakeBody):
    pass


class MoonlitReviewContractTests(unittest.TestCase):
    def build(self):
        app = SimpleNamespace(root=FakeWidget(), appearance=SimpleNamespace(accent=appearance.MOONLIT_ACCENT))
        variables = ('only_issues', 'only_zero', 'save_status', 'reference_value', 'review_size',
                     'start', 'end', 'first_frame', 'last_frame')
        for name in variables:
            setattr(app, name, FakeVariable(value='reference' if name == 'reference_value' else ''))
        commands = ('refresh', 'select_mapping', 'register_pane', 'reference_translation', 'api_settings',
                    'update_review_fonts', 'repeat_mapping', 'rematch', 'unlock', 'use_visual_times',
                    'display_frames', 'apply_pairing', 'nudge_boundary', 'mark_frame', 'split_dialog',
                    'merge_selected', 'show_mapping_editor', 'global_warnings', 'apply_outline_time',
                    'previous_mapping', 'next_issue', 'confirm_next')
        for name in commands:
            setattr(app, name, MagicMock())
        app.steps = FakeWidget()
        review, actions = FakeWidget(app.root), FakeWidget(app.root)
        def make_tree(parent, columns, widths, **kwargs):
            return FakeWidget(parent, columns=columns, widths=widths, **kwargs)
        def make_button(parent, text, command, help_text, primary=False):
            return FakeWidget(parent, text=text, command=command, primary=primary)
        with ExitStack() as stack:
            for name in ('Frame', 'Label', 'Menubutton', 'Panedwindow', 'Scrollbar', 'Notebook', 'Spinbox', 'LabelFrame', 'Entry'):
                stack.enter_context(patch.object(review_layout.ttk, name, FakeWidget))
            for name in ('Frame', 'Menu', 'Toplevel'):
                stack.enter_context(patch.object(review_layout.tk, name, FakeWidget))
            for name in ('StringVar', 'BooleanVar'):
                stack.enter_context(patch.object(review_layout.tk, name, FakeVariable))
            stack.enter_context(patch.object(review_layout, 'FitText', FakeWidget))
            stack.enter_context(patch.object(review_layout, '_ReviewBody', FakeBody))
            stack.enter_context(patch.object(review_layout, 'Disclosure', FakeDisclosure))
            stack.enter_context(patch.object(review_layout, 'button', make_button))
            review_layout.build_review(app, review, actions, make_tree)
        return app, review, actions

    def test_existing_adapters_survive_and_build_does_not_execute_commands(self):
        app, _, _ = self.build()
        self.assertEqual(set(app.review_cards), {'source', 'ocr', 'chinese', 'reference'})
        self.assertIs(app.reference_label, app.review_cards['reference'])
        self.assertEqual(len(app.results.options['columns']), 6)
        self.assertEqual(app.results.options['displaycolumns'], ('编号', '中文译文', '状态'))
        self.assertEqual(len(app.boundary_buttons), 8)
        self.assertEqual((app.boundary_buttons[3].hold_action.end, app.boundary_buttons[3].hold_action.delta), (False, 5))
        self.assertEqual((app.boundary_buttons[7].hold_action.end, app.boundary_buttons[7].hold_action.delta), (True, 5))
        for attribute in ('mapping_window', 'mapping_editor', 'lyrics_tree', 'visuals_tree', 'trans_tree', 'details'):
            self.assertTrue(hasattr(app, attribute), attribute)
        for name in ('reference_translation', 'confirm_next', 'nudge_boundary', 'mark_frame', 'refresh', 'apply_pairing'):
            getattr(app, name).assert_not_called()

    def test_navigation_is_one_horizontal_row_and_advanced_commands_remain(self):
        app, _, _ = self.build()
        controls = app.review_navigation
        self.assertIs(controls['previous'].master, controls['next'].master)
        self.assertIs(controls['next'].master, controls['confirm'].master)
        self.assertEqual([controls[k].packed['side'] for k in ('previous', 'next', 'confirm')], ['left', 'left', 'right'])
        edit_commands = [item.get('command') for item in app.review_edit_menu.menu_items]
        time_commands = [item.get('command') for item in app.review_time_menu.menu_items]
        for command in (app.split_dialog, app.merge_selected, app.show_mapping_editor, app.unlock, app.global_warnings):
            self.assertIn(command, edit_commands)
        self.assertIn(app.use_visual_times, time_commands)
        self.assertIn(app.apply_outline_time, time_commands)
        controls['confirm'].options['command']()
        app.confirm_next.assert_called_once()

    def test_reference_is_independent_explicit_and_still_live(self):
        app, _, _ = self.build()
        app.reference_value.set('独立参考，不改中文')
        self.assertEqual(app.reference_label.text, '独立参考，不改中文')
        self.assertEqual(app.review_cards['chinese'].text, '')
        app.reference_button.options['command']()
        app.reference_translation.assert_called_once()
        self.assertIs(app.review_ai_tabs.selected, app.reference_label.master)
        app.ai_settings_button.options['command']()
        app.api_settings.assert_called_once()

    def test_accent_changes_do_not_replace_saved_colour_with_purple(self):
        app, _, _ = self.build()
        app.appearance.accent = '#2277AA'
        for callback in app.root.bindings['<<AppearanceChanged>>']:
            callback()
        self.assertEqual(app.review_cards['chinese'].options['highlightcolor'], '#2277AA')
        self.assertEqual(app.results.tags['playing']['background'], appearance.selected_surface('#2277AA'))


class MoonlitPaletteTests(unittest.TestCase):
    def test_original_palette_and_legacy_default_are_distinct(self):
        self.assertEqual(appearance.DEFAULT_ACCENT, '#66CCFF')
        self.assertEqual(appearance.MOONLIT_ACCENT, '#8764C6')
        self.assertEqual((appearance.BACKGROUND, appearance.PANEL, appearance.INK, appearance.MUTED, appearance.BORDER),
                         ('#F8F7FB', '#FFFFFF', '#2B2638', '#928DA1', '#ECE8F2'))
        self.assertEqual(appearance.selected_surface('#8764c6'), '#EEE8FA')
        self.assertNotEqual(appearance.selected_surface('#66CCFF'), '#EEE8FA')
        for invalid in (None, [], 42, '#ABC', '8764C6'):
            self.assertFalse(appearance.valid_color(invalid))

    def test_theme_uses_given_accent_and_keeps_overlap_foreground_map_empty(self):
        theme = appearance.Appearance.__new__(appearance.Appearance)
        theme.root, theme.style = MagicMock(), MagicMock()
        theme.images = {name: MagicMock() for name in (
            'normal', 'hover', 'pressed', 'primary', 'primary_hover', 'disabled', 'check_off', 'check_on',
            'thumb', 'trough', 'panel', 'white_panel', 'scroll_v', 'scroll_h', 'progress', 'progress_bg',
            'switch_off', 'switch_on')}
        with patch.object(appearance.tkfont, 'Font') as font:
            font.return_value.metrics.return_value = 20
            theme.apply('#66CCFF')
        self.assertEqual(theme.accent, '#66CCFF')
        theme.style.map.assert_any_call('Treeview', background=[('selected', appearance.selected_surface('#66CCFF'))], foreground=[])
        theme.root.event_generate.assert_called_once_with('<<AppearanceChanged>>', when='tail')


class CanvasEventProbe:
    """Exercise the production render/motion methods without a display server."""
    def setup_canvas(self, selected=0):
        self.tk = SimpleNamespace()  # Avoid Tk's __getattr__ fallback on uncreated widgets.
        self.width = 252
        self.height = 42
        self.cell_width = 126
        self.control_height = 42
        self.label_font = 'regular'
        self.bold_font = 'bold'
        self.notebook = SimpleNamespace(index=lambda _: self.selected)
        self.selected = selected
        self.accent_value = appearance.MOONLIT_ACCENT
        self.accent = lambda: self.accent_value
        self.focused = False
        self.items = {}
        self.created = 0
        self.deleted = 0
        self.jobs = {}
        self.sequence = 0

    def __getitem__(self, key): return self.height if key == 'height' else self.width
    def winfo_width(self): return self.width
    def focus_get(self): return self if self.focused else None
    def create_image(self, *coords, **options): return self.create_item(coords, options)
    def create_text(self, *coords, **options): return self.create_item(coords, options)
    def create_line(self, *coords, **options): return self.create_item(coords, options)
    def create_rectangle(self, *coords, **options): return self.create_item(coords, options)
    def create_oval(self, *coords, **options): return self.create_item(coords, options)
    def create_item(self, coords, options):
        self.created += 1
        self.items[self.created] = dict(coords=coords, **options)
        return self.created
    def delete(self, key):
        self.deleted += 1
        self.items.clear()
    def itemconfigure(self, key, **options): self.items[key].update(options)
    def coords(self, key, *coords):
        if coords: self.items[key]['coords'] = coords
        return self.items[key]['coords']
    def after(self, delay, callback):
        self.sequence += 1
        self.jobs[self.sequence] = (delay, callback)
        return self.sequence
    def after_cancel(self, identity): self.jobs.pop(identity, None)
    def advance(self):
        identity = min(self.jobs)
        _, callback = self.jobs.pop(identity)
        callback()


class ModeEventProbe(CanvasEventProbe, appearance.ModeSwitch):
    def __init__(self): self.setup_canvas()


class StepEventProbe(CanvasEventProbe, appearance.StepTabs):
    def __init__(self):
        self.setup_canvas()
        self.width = 480
        self.cell_width = 120


class MoonlitRenderEventTests(unittest.TestCase):
    def test_repeated_render_reuses_images_and_canvas_items(self):
        with patch.object(appearance.ImageTk, 'PhotoImage') as images:
            control = ModeEventProbe()
            control.render()
            initial_images, initial_items = images.call_count, control.created
            for _ in range(30):
                control.render()
            self.assertEqual(images.call_count, initial_images)
            self.assertEqual(control.created, initial_items)
        step = StepEventProbe()
        step.render()
        initial_items = step.created
        for _ in range(30):
            step.render()
        self.assertEqual(step.created, initial_items)

    def test_configure_or_focus_render_does_not_snap_or_cancel_running_motion(self):
        for factory in (ModeEventProbe, StepEventProbe):
            with self.subTest(control=factory.__name__), patch.object(appearance.ImageTk, 'PhotoImage'), \
                    patch.object(appearance.time, 'monotonic') as clock:
                clock.return_value = 0.0
                control = factory()
                control.render()
                control.selected = 1
                control.render()
                control.transition()
                clock.return_value = .048
                control.advance()
                visible, job = control._motion_position, control._motion_job
                control.focused = True
                control.render()
                self.assertEqual(control._motion_position, visible)
                self.assertEqual(control._motion_job, job)
                self.assertEqual(len(control.jobs), 1)

    def test_duplicate_selection_event_does_not_restart_clock(self):
        with patch.object(appearance.ImageTk, 'PhotoImage'), patch.object(appearance.time, 'monotonic') as clock:
            clock.return_value = 0.0
            control = ModeEventProbe()
            control.render()
            control.selected = 1
            control.render()
            control.transition()
            clock.return_value = .048
            control.advance()
            job = control._motion_job
            control.render()  # duplicate NotebookTabChanged for the same selected page
            control.transition()
            self.assertEqual(control._motion_job, job)
            clock.return_value = .176
            control.advance()
            self.assertEqual(control._motion_position, 1)
            self.assertFalse(control.jobs)

    def test_resize_reuses_items_and_keeps_animation_origin(self):
        with patch.object(appearance.ImageTk, 'PhotoImage') as images, patch.object(appearance.time, 'monotonic') as clock:
            clock.return_value = 0.0
            control = ModeEventProbe()
            control.render()
            items = control.created
            control.selected = 1
            control.render()
            control.transition()
            clock.return_value = .032
            control.advance()
            visible, job = control._motion_position, control._motion_job
            control.width += 80
            control.render()
            self.assertEqual(control._motion_position, visible)
            self.assertEqual(control._motion_job, job)
            self.assertEqual(control.created, items)
            self.assertEqual(images.call_count, 4)  # initial two + genuinely resized two

    def test_accent_change_needs_no_new_raster_images(self):
        with patch.object(appearance.ImageTk, 'PhotoImage') as images:
            control = ModeEventProbe()
            control.render()
            initial = images.call_count
            control.accent_value = '#2277AA'
            control.render()
            self.assertEqual(images.call_count, initial)
            self.assertEqual(control.items[control._focus_line]['fill'], '#2277AA')


class FitEventProbe(appearance.FitText):
    def __init__(self):
        self.tk = SimpleNamespace()
        self.bindings = {}
        self.jobs = {}
        self.sequence = 0
        self.idle_requests = 0
        self.count_calls = 0
        self.width = 400
        self.options = {'height': 1}
        with patch.object(appearance.tk.Text, '__init__', return_value=None):
            super().__init__(None)

    def bind(self, event, callback, add=None): self.bindings[event] = callback
    def after_idle(self, callback):
        self.idle_requests += 1
        self.sequence += 1
        self.jobs[self.sequence] = callback
        return self.sequence
    def after_cancel(self, identity): self.jobs.pop(identity, None)
    def winfo_exists(self): return True
    def winfo_width(self): return self.width
    def count(self, *args):
        self.count_calls += 1
        return (1,)
    def __getitem__(self, key): return self.options[key]
    def configure(self, **kwargs):
        self.options.update(kwargs)
        if 'height' in kwargs:
            # Tk sends Configure when fit() changes its own requested height.
            self.resize(self.width, kwargs['height']*20)
    def resize(self, width, height):
        self.width = width
        self.bindings['<Configure>'](SimpleNamespace(width=width, height=height))
    def drain(self):
        loops = 0
        while self.jobs:
            identity = min(self.jobs)
            self.jobs.pop(identity)()
            loops += 1
            if loops > 20: raise AssertionError('fit geometry did not settle')


class MoonlitResizeWorkTests(unittest.TestCase):
    def test_vertical_resize_does_not_recount_wrapped_text(self):
        field = FitEventProbe()
        field.resize(400, 40)
        field.drain()
        self.assertEqual(field.count_calls, 1)  # no self-height Configure feedback
        for height in range(41, 101):
            field.resize(400, height)
        field.drain()
        self.assertEqual(field.count_calls, 1)
        self.assertEqual(field.idle_requests, 1)

    def test_width_resize_burst_has_one_pending_fit_and_content_changes_still_fit(self):
        field = FitEventProbe()
        for width in range(400, 460):
            field.resize(width, 50)
        self.assertEqual(field.idle_requests, 1)
        self.assertEqual(len(field.jobs), 1)
        field.drain()
        self.assertEqual(field.count_calls, 1)
        # Content/font edits need a fit even if width has not changed.
        field.schedule_fit()
        field.drain()
        self.assertEqual(field.count_calls, 2)
        field.bindings['<Destroy>']()
        self.assertFalse(field.jobs)

    def test_review_canvas_only_propagates_changed_width(self):
        body = SimpleNamespace(canvas=MagicMock(), _body_item=7, _canvas_width=None, _body_extent=None)
        for height in range(200, 260):
            review_layout._ReviewBody._canvas_configured(body, SimpleNamespace(width=480, height=height))
        body.canvas.itemconfigure.assert_called_once_with(7, width=480)
        review_layout._ReviewBody._canvas_configured(body, SimpleNamespace(width=500, height=260))
        self.assertEqual(body.canvas.itemconfigure.call_count, 2)
        for _ in range(30):
            review_layout._ReviewBody._body_configured(body, SimpleNamespace(width=500, height=400))
        body.canvas.configure.assert_called_once_with(scrollregion=(0, 0, 500, 400))

    def test_review_heading_ignores_height_and_duplicate_width(self):
        label = SimpleNamespace(configure=MagicMock(), _review_wrap_width=480)
        for height in range(200, 260):
            review_layout._wrap_review_heading(label, SimpleNamespace(width=492, height=height))
        label.configure.assert_not_called()
        review_layout._wrap_review_heading(label, SimpleNamespace(width=520, height=260))
        label.configure.assert_called_once_with(wraplength=508)

    def test_focus_only_changes_outline_and_destroy_stops_callbacks(self):
        with patch.object(appearance.ImageTk, 'PhotoImage') as images, patch.object(appearance.time, 'monotonic', return_value=0.0):
            control = ModeEventProbe()
            control.render()
            control.selected = 1
            control.render()
            control.transition()
            job = control._motion_job
            images_before = images.call_count
            items_before = control.created
            for _ in range(20):
                control.focused = not control.focused
                control._focus_changed()
            self.assertEqual(images.call_count, images_before)
            self.assertEqual(control.created, items_before)
            self.assertEqual(control._motion_job, job)
            self.assertEqual(control._motion_position, 0)
            stale = next(iter(control.jobs.values()))[1]
            control.cancel_transition(snap=False)  # Destroy binding's operation
            self.assertFalse(control.jobs)
            before = dict(control.items[control._pill])
            stale()
            self.assertEqual(control.items[control._pill], before)


class MoonlitWorkflowHeaderTests(unittest.TestCase):
    def test_completion_comes_from_pipeline_and_valid_manual_review_not_navigation(self):
        from lyricsync.model import Mapping, Project
        project = Project()
        self.assertEqual(appearance.workflow_state(project)['completed'], (False, False, False, False))
        project.stage = 'vision'
        self.assertEqual(appearance.workflow_state(project)['completed'], (True, False, False, False))
        project.stage = 'review'
        project.mappings = [Mapping('m', [], start=1, end=2)]
        self.assertEqual(appearance.workflow_state(project)['completed'], (True, True, False, False))
        project.mappings[0].confirmed = True
        project.settings['last_export'] = 'stale-export.ass'
        before = project.to_dict()
        self.assertEqual(appearance.workflow_state(project),
            {'completed': (True, True, True, False), 'confirmed': 1, 'total': 1})
        self.assertEqual(project.to_dict(), before)
        project.mappings[0].end = None
        self.assertEqual(appearance.workflow_state(project)['confirmed'], 0)
        self.assertFalse(appearance.workflow_state(project)['completed'][2])

    def test_selected_third_tab_does_not_invent_first_two_checks(self):
        control = StepEventProbe()
        control.selected = 2
        control.render()
        self.assertEqual(control.items[control._numbers[0]]['text'], '01')
        self.assertEqual(control.items[control._numbers[1]]['text'], '02')
        self.assertEqual(control.items[control._indicator_number]['text'], '03')

    def test_compact_steps_keep_left_positions_and_resize_does_not_rescan_state(self):
        control = StepEventProbe()
        control.get_state = MagicMock(return_value={'completed': (True, True, False, False), 'confirmed': 2, 'total': 6})
        control.render()
        original_positions = [control.coords(item) for item in control._labels]
        for width in range(1000, 1060):
            control.width = width
            control.render(refresh_state=False)
        self.assertEqual([control.coords(item) for item in control._labels], original_positions)
        control.get_state.assert_called_once()
        self.assertEqual(control.items[control._progress_label]['text'], '2 / 6 已确认')
        self.assertEqual(control.items[control._progress_label]['state'], 'normal')
        control.select = MagicMock()
        control._click(SimpleNamespace(x=900))
        control.select.assert_not_called()
        control._click(SimpleNamespace(x=control.cell_width*2+10))
        control.select.assert_called_once_with(2)

    def test_inline_menus_preserve_native_command_labels_accelerators_and_keyboard_access(self):
        from lyricsync.workspace import WorkspaceActions
        class Menu(FakeWidget):
            def add_cascade(self, **kwargs): self.menu_items.append(kwargs)
        class MenuButton(FakeWidget):
            def focus_set(self): self.focused = True
            def event_generate(self, event): self.generated = event
        class Host:
            def __init__(self): self.root = FakeWidget()
            def __getattr__(self, name):
                callback = MagicMock(name=name)
                setattr(self, name, callback)
                return callback
        native, inline = Host(), Host()
        with patch('lyricsync.workspace.tk.Menu', Menu), patch('lyricsync.workspace.ttk.Menubutton', MenuButton):
            WorkspaceActions.build_menu(native)
            WorkspaceActions.build_menu(inline, FakeWidget(inline.root))
        def manifest(host):
            return {name: [(item.get('label'), item.get('accelerator')) for item in menu.menu_items]
                    for name, menu in host.menu_groups.items()}
        self.assertEqual(manifest(native), manifest(inline))
        self.assertEqual(tuple(inline.menu_groups), ('文件', '编辑', '工具', '设置', '帮助'))
        self.assertEqual(inline.root.options['menu'], '')
        self.assertEqual(len(inline.menu_buttons), 5)
        for key in ('<Control-z>', '<Control-y>', '<Control-Shift-Z>', '<m>', '<M>', '<Control-s>'):
            self.assertIn(key, inline.root.bindings)
        inline.root.bindings['<F10>'][0](None)
        self.assertTrue(inline.menu_buttons[0].focused)
        inline.menu_buttons[0].bindings['<Right>'][0](None)
        self.assertTrue(inline.menu_buttons[1].focused)
        inline.root.bindings['<Alt-f>'][0](None)
        self.assertEqual(inline.menu_buttons[0].generated, '<KeyPress-Down>')
        inline.root.bindings['<Control-s>'][0](None)
        inline.save.assert_called_once()
