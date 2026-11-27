"""Moonlit desktop adapters: real project state, no demo data or web bridge."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from .appearance import MOONLIT_ACCENT, valid_color
from .editing import History, mapping_text
from .model import Project, valid_time
from .workspace import overlap_ids
from .dialogs import messagebox


class DesktopFrontend:
    def accent_color(self):
        color = self.ui_settings.get('accent', MOONLIT_ACCENT)
        return color if valid_color(color) else MOONLIT_ACCENT

    def build_moonlit_header(self):
        # Keep the native caption: moving/maximizing/accessibility stay with
        # Windows. The original handoff's brand/menu row is an in-client strip.
        from tkinter import font as tkfont
        from .appearance import BORDER, PANEL
        bar = ttk.Frame(self.root, style='Surface.TFrame', padding=(24, 7, 24, 7))
        bar.pack(side='top', fill='x')
        self.brand_bar = bar
        brand = ttk.Frame(bar, style='Surface.TFrame')
        brand.pack(side='left')
        size = max(28, round(tkfont.Font(root=self.root, family='Segoe UI', size=14).metrics('linespace')*1.35))
        mark = tk.Canvas(brand, width=size, height=size, background=PANEL, highlightthickness=0, takefocus=False)
        mark.pack(side='left', padx=(0, 10))
        edge, radius = size-1, size*.30
        outline = mark.create_polygon(radius, 0, edge-radius, 0, edge, 0, edge, radius,
            edge, edge-radius, edge, edge, edge-radius, edge, radius, edge, 0, edge,
            0, edge-radius, 0, radius, 0, 0, smooth=True, splinesteps=24,
            fill=self.accent_color(), outline='')
        center, long, short = size/2, size*.28, size*.085
        mark.create_polygon(center, center-long, center+short, center-short,
            center+long, center, center+short, center+short, center, center+long,
            center-short, center+short, center-long, center, center-short, center-short,
            fill=PANEL, outline='')
        self.root.bind('<<AppearanceChanged>>',
            lambda _: mark.itemconfigure(outline, fill=self.accent_color()), add='+')
        ttk.Label(brand, text='Lyria', style='Brand.Surface.TLabel').pack(side='left')
        ttk.Label(brand, text='S T U D I O', style='Studio.Surface.TLabel').pack(side='left', padx=(10, 0))
        self.inline_menu_host = ttk.Frame(bar, style='Surface.TFrame')
        self.inline_menu_host.pack(side='left', padx=(26, 0))
        local = ttk.Frame(bar, style='Surface.TFrame')
        local.pack(side='right')
        ttk.Label(local, text='●', style='Local.Surface.TLabel').pack(side='left', padx=(0, 6))
        ttk.Label(local, text='本地桌面工作区', style='Muted.Surface.TLabel').pack(side='left')
        divider = tk.Frame(self.root, height=1, background=BORDER)
        divider.pack(side='top', fill='x')

        header = ttk.Frame(self.root, padding=(26, 19, 26, 17))
        header.pack(side='top', fill='x')
        titles = ttk.Frame(header)
        titles.pack(side='left', fill='x', expand=True)
        ttk.Label(titles, text='MOONLIT LILAC / WORKSPACE', style='Overline.TLabel').pack(anchor='w')
        title = ttk.Frame(titles)
        title.pack(anchor='w', pady=(4, 0))
        ttk.Label(title, text='歌词与画面，恰好相遇', style='Hero.TLabel').pack(side='left')
        ttk.Label(title, text='✧', style='Sparkle.TLabel').pack(side='left', padx=(7, 0))
        self.project_meta = tk.StringVar(value='本地字幕工作区 · 导入你的素材开始')
        ttk.Label(titles, textvariable=self.project_meta, style='Hint.TLabel').pack(anchor='w', pady=(5, 0))
        self.workspace_switch_host = ttk.Frame(header)
        self.workspace_switch_host.pack(side='right', padx=(12, 0))
        self.frontend_status_bar = ttk.Frame(self.root, padding=(22, 5))
        self.frontend_status_bar.pack(side='bottom', fill='x')
        ttk.Label(self.frontend_status_bar, textvariable=self.status, style='Hint.TLabel').pack(side='left', fill='x', expand=True)
        ttk.Label(self.frontend_status_bar, textvariable=self.save_status, style='Hint.TLabel').pack(side='right', padx=8)

    def build_frame_workspace(self, parent):
        from .frame_timeline import FrameTimeline
        self.frame_timeline = FrameTimeline(parent,
            get_project=lambda: self.project, get_index=lambda: self.frame_index,
            get_history=lambda: self.history, get_selection=self.frontend_selection,
            on_select=self.select_from_timeline, on_change=self.timeline_edit_changed,
            on_commit=self.timeline_edit_committed, can_edit=self.frontend_can_edit,
            on_status=self.status.set, get_accent=self.accent_color)
        return self.frame_timeline

    def build_marker_workspace(self, parent):
        from .marker_track import MarkerTrack
        self.marker_track = MarkerTrack(parent,
            get_project=lambda: self.project, get_index=lambda: self.frame_index,
            get_history=lambda: self.history, get_selection=self.frontend_selection,
            get_current_frame=lambda: self.frame_index.current(self.stamp) if self.frame_index else None,
            get_text_widget=lambda: self.review_cards.get('chinese'),
            on_change=self.marker_edit_changed, on_select=self.select_from_timeline,
            on_status=self.status.set, can_edit=self.marker_can_edit, get_accent=self.accent_color,
            on_begin=self.begin_marker_interaction)
        self.marker_track.pack(fill='x', pady=(1, 3))

    def frontend_selection(self):
        selected = self.selected_mapping()
        return selected.id if selected else None

    def frontend_can_edit(self):
        if self.busy or self.close_requested:
            self.status.set('正在处理任务，请先停止或等待完成再编辑。')
            return False
        if not self.flush_chinese():
            self.status.set('请先完成中文输入，再编辑帧或固定标记。')
            return False
        if self.playing:
            self.pause_playback()
        self.cancel_pending_seek()
        self.stop_frontend_animations()
        return True

    def marker_can_edit(self):
        return (not self.busy and not self.close_requested and
                self.frontend_selection() == self.detail_id)

    def begin_marker_interaction(self):
        self.pause_playback()
        self.cancel_pending_seek()

    def select_from_timeline(self, identity):
        if not any(m.id == identity for m in self.project.mappings):
            return
        if not self.results.exists(identity):
            # Explicit action, not silent filter changes or hidden-row selection.
            if not messagebox.askyesno('此句已被筛选隐藏',
                    '要关闭当前筛选并查看这条字幕吗？', default='no', parent=self.root):
                self.refresh_frontend()
                return
            self.only_issues.set(False)
            self.only_zero.set(False)
            self.refresh()
        self.results.selection_set(identity)
        self.results.see(identity)
        self.select_mapping(auto=True)
        self.steps.select(2)

    def timeline_edit_changed(self, identity):
        """Drag hot path: no video draw/seek, no tree rebuild or text replacement."""
        self.drafts.clear()
        old_overlapping = getattr(self, 'overlapping', set())
        self.overlapping = overlap_ids(self.project.mappings, self.frame_index)
        affected = (old_overlapping ^ self.overlapping) | {identity}
        for number, mapping in enumerate(self.project.mappings, 1):
            if mapping.id not in affected:
                continue
            if not self.results.exists(mapping.id):
                continue
            tags = tuple(tag for tag in self.results.item(mapping.id, 'tags') if tag != 'overlap')
            if mapping.id in self.overlapping:
                tags += ('overlap',)
            if mapping.id == identity:
                state = '已确认' if mapping.confirmed and valid_time(mapping.start, mapping.end) else '待确认'
                self.results.item(mapping.id, tags=tags, values=(number, mapping_text(self.project, mapping),
                    self.frame_label(mapping.start, False), self.frame_label(mapping.end, True),
                    f'{mapping.confidence:.0%}', state))
            elif tuple(self.results.item(mapping.id, 'tags')) != tags:
                self.results.item(mapping.id, tags=tags)
        row = self.selected_mapping()
        if row and row.id == identity:
            self._loading_edit = True
            try:
                self.start.set(repr(row.start) if row.start is not None else '')
                self.end.set(repr(row.end) if row.end is not None else '')
                first = str(self.frame_index.first(row.start)) if row.start is not None else ''
                last = str(self.frame_index.last(row.end)) if row.end is not None else ''
                self.first_frame.set(first)
                self.last_frame.set(last)
                self._frame_display = (first, last)
            finally:
                self._loading_edit = False
        self.schedule_autosave()

    def timeline_edit_committed(self):
        self.refresh()
        self.display_frames()
        self.draw()
        self.schedule_autosave()

    def marker_edit_changed(self):
        self.drafts.clear()
        self.refresh()
        self.draw()
        self.schedule_autosave()

    def refresh_frontend(self, fit=False):
        if hasattr(self, 'step_tabs'):
            self.step_tabs.render()
        if hasattr(self, 'frame_timeline'):
            self.frame_timeline.refresh(fit=fit)
        if hasattr(self, 'marker_track'):
            self.marker_track.refresh()
        if hasattr(self, 'project_meta'):
            video = self.inputs['video'].get().strip() or self.project.inputs.get('video')
            title = Path(video).stem if video else '未载入视频'
            frames = f" · {len(self.frame_index.stamps):,} 帧" if self.frame_index else ''
            self.project_meta.set(f'{title} · {len(self.project.mappings)} 条字幕{frames}')

    def update_frontend_playhead(self):
        if hasattr(self, 'frame_timeline'):
            self.frame_timeline.update_playhead(stamp=self.stamp)
        if hasattr(self, 'marker_track'):
            self.marker_track.update_playhead()

    def stop_frontend_animations(self):
        for name in ('mode_switch', 'step_tabs'):
            control = getattr(self, name, None)
            if control is not None and hasattr(control, 'cancel_transition'):
                control.cancel_transition()

    def finish_frontend_edits(self):
        if hasattr(self, 'frame_timeline'):
            self.frame_timeline.finish_drag(commit=True)
        if hasattr(self, 'marker_track'):
            self.marker_track.cancel_drag()
        self.stop_frontend_animations()

    def install_native_drop(self):
        from .file_drop import NativeFileDrop
        self.native_file_drop = NativeFileDrop(self.root, self.receive_dropped_files,
            lambda text: self.status.set(text))

    def persist_before_replacement(self):
        """A destructive workspace switch requires proof, not best-effort autosave.

        autosave() intentionally catches immediate submission errors for typing
        responsiveness. A subsequent empty flush would not prove data was saved.
        """
        if not (self.project.mappings or self.project_path):
            return True
        try:
            path = self.ensure_project_path()
            self.save_revision = self.project_saver.submit(self.project, path, self.home / 'last_project.json')
            self.save_status.set('正在保存…')
            self.project_saver.flush()
        except OSError as exc:
            self.error(exc)
            return False
        return True

    def receive_dropped_files(self, paths):
        if not self.guard():
            return
        from .file_drop import resolve_dropped_files
        choices = resolve_dropped_files(self.root, paths)
        if not choices:
            return
        if 'project' in choices:
            if not self.persist_before_replacement():
                return
            self.open(choices['project'])
            return
        existing = {key: variable.get().strip() for key, variable in self.inputs.items() if variable.get().strip()}
        if self.project.mappings or self.project_path:
            if not messagebox.askyesno('导入到新任务',
                    '当前任务会先保存。将这些素材用于新任务？现有校对结果不会被覆盖。',
                    default='no', parent=self.root):
                return
            if not self.persist_before_replacement():
                return
            self.finish_frontend_edits()
            self.pause_playback()
            self.audio_generation += 1
            self.audio.close()
            from . import ai_config
            self.project = Project()
            self.project.settings.update(ai_config.load(self.home))
            self.project.settings['engine'] = 'rapidocr'
            self.project_path = None
            self.history = History()
            self.drafts = {}
            self.detail_id = None
            self.frame_index = None
            self.index_generation += 1
            self.frame = self.preview_video = None
            self.ai_enabled.set(self.project.settings.get('api_enabled', False))
            for variable in self.inputs.values():
                variable.set('')
        elif any(key in existing and Path(existing[key]) != Path(value) for key, value in choices.items()):
            if not messagebox.askyesno('替换待导入素材', '替换相同用途下已有的文件选择？',
                    default='no', parent=self.root):
                return
        for role, path in choices.items():
            self.inputs[role].set(path)
        self.steps.select(0)
        self.refresh()
        if 'video' in choices:
            self.load_video()
        else:
            self.draw()
        self.status.set('文件已归位；请检查用途和歌词区域，再开始识别。')
