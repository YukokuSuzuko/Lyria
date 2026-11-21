"""Three real subtitle rows, inclusive frame geometry and isolated drag edits.

No video/decoder API is used here. Times remain Project's half-open PTS values.
A drag forwards exactly one snapshot to the shared History, never holds its
``group`` open. External edits therefore remain separate undo commands.
Release/focus loss/selection change commit. Escape restores only the dragged row
and its history entry if still uncontested; after an intervening edit it commits
instead, preserving that edit and its undo chain. Hosts should call finish_drag()
before replacing a project/index or starting another editing operation.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import math
import tkinter as tk
from tkinter import ttk
from typing import Callable

from .appearance import blend, ink_on, valid_color
from .editing import Editor, History, mapping_text
from .frames import FrameIndex
from .model import Mapping, Project, TIME_EPSILON, valid_time


PURPLE = '#8764C6'
PALE = '#C8BBE0'
INK = '#2B2638'
MUTED = '#928DA1'
LINE = '#ECE8F2'
ROSE = '#CB789F'
RED = '#BF4F68'


def timeline_palette(value):
    """Keep the prototype default, while honoring saved user accent colors."""
    accent = value if valid_color(value) else PURPLE
    pale = PALE if accent.upper() == PURPLE else blend(accent, amount=.4)
    return accent, pale, ink_on(accent)


def _known(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


@dataclass(frozen=True)
class FrameBounds:
    first: int | None
    last: int | None
    invalid: bool = False

    @property
    def complete(self):
        return not self.invalid and self.first is not None and self.last is not None


def frame_bounds(mapping: Mapping, index: FrameIndex | None) -> FrameBounds:
    """Read actual PTS; unknown/out-of-range timestamps never become fake bars."""
    if index is None:
        return FrameBounds(None, None)
    invalid = False
    first = last = None
    if mapping.start is not None:
        if _known(mapping.start) and mapping.start <= index.stamps[-1] + TIME_EPSILON:
            first = index.first(mapping.start)
        else:
            invalid = True
    if mapping.end is not None:
        if (_known(mapping.end) and mapping.end > index.stamps[0] + TIME_EPSILON
                and mapping.end <= index.duration + TIME_EPSILON):
            last = index.last(mapping.end)
        else:
            invalid = True
    if first is not None and last is not None:
        invalid |= not valid_time(mapping.start, mapping.end) or first > last
    return FrameBounds(first, last, bool(invalid))


@dataclass(frozen=True)
class TimelineRow:
    slot: str
    identity: str | None = None
    number: int | None = None
    text: str = ''
    first: int | None = None
    last: int | None = None
    status: str = ''
    overlap: bool = False

    @property
    def complete(self):
        return self.first is not None and self.last is not None and not self.status


def real_overlap_ids(project: Project, index: FrameIndex | None) -> set[str]:
    if index is None:
        return set()
    intervals = []
    for row in project.mappings:
        bounds = frame_bounds(row, index)
        if bounds.complete:
            intervals.append((bounds.first, bounds.last, row.id))
    active = []
    result = set()
    for first, last, identity in sorted(intervals):
        active = [(end, other) for end, other in active if end >= first]
        if active:
            result.add(identity)
            result.update(other for _, other in active)
        active.append((last, identity))
    return result


def timeline_rows(project: Project, index: FrameIndex | None,
                  selected: str | None) -> tuple[TimelineRow, ...]:
    """Always three slots, numbered in the full project, never filtered-renumbered."""
    position = next((i for i, row in enumerate(project.mappings) if row.id == selected), None)
    overlaps = real_overlap_ids(project, index)
    rows = []
    for offset, slot in ((-1, '上一句'), (0, '当前句'), (1, '下一句')):
        i = position + offset if position is not None else -1
        if not 0 <= i < len(project.mappings):
            rows.append(TimelineRow(slot, status='请先选择字幕' if position is None else '没有' + slot))
            continue
        row = project.mappings[i]
        bounds = frame_bounds(row, index)
        if index is None:
            status = '等待真实帧索引'
        elif bounds.invalid:
            status = '时间无效，请核对'
        elif bounds.first is None and bounds.last is None:
            status = '开始 / 结束待补齐'
        elif bounds.first is None:
            status = '开始待补齐'
        elif bounds.last is None:
            status = '结束待补齐'
        else:
            status = ''
        rows.append(TimelineRow(slot, row.id, i + 1, mapping_text(project, row),
                                bounds.first, bounds.last, status, row.id in overlaps))
    return tuple(rows)


def relation_text(previous: TimelineRow, current: TimelineRow) -> str:
    if previous.identity is None or current.identity is None:
        return ''
    if not previous.complete or not current.complete:
        return '边界待补齐'
    overlap = min(previous.last, current.last) - max(previous.first, current.first) + 1
    if overlap > 0:
        return f'重叠 {overlap} 帧'
    gap = current.first - previous.last - 1
    if gap >= 0:
        return f'间隔 {gap} 帧' if gap else '边界相接 · 无重叠'
    return '时间顺序需核对'


@dataclass
class FrameViewport:
    """Integer frame viewport [first, first+span), with inclusive clip ends."""
    total: int = 1
    first: int = 1
    span: int = 1

    def configure(self, total: int):
        if type(total) is not int or total < 1:
            raise ValueError('总帧数必须是正整数')
        self.total = total
        self.span = min(total, max(1, int(self.span)))
        self.first = min(total - self.span + 1, max(1, int(self.first)))

    @property
    def last(self):
        return self.first + self.span - 1

    def frame_x(self, frame: int | float, width: float) -> float:
        return (frame - self.first) * width / self.span

    def x_frame(self, x: float, width: float, *, end=False) -> int:
        value = math.floor(self.first + x * self.span / max(1, width) + .5) - int(end)
        return max(1, min(self.total, value))

    def delta_frames(self, pixels: float, width: float) -> int:
        # Symmetric nearest-frame rounding avoids a negative half-frame bias.
        value = pixels * self.span / max(1, width)
        return int(math.copysign(math.floor(abs(value) + .5), value))

    def pan(self, frames: int):
        self.first += int(frames)
        self.configure(self.total)

    def zoom(self, factor: float, anchor: float | None = None):
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError('缩放倍数必须大于零')
        anchor = self.first + self.span / 2 if anchor is None else anchor
        fraction = (anchor - self.first) / self.span
        self.span = max(1, min(self.total, round(self.span / factor)))
        self.first = round(anchor - fraction * self.span)
        self.configure(self.total)

    def fit(self, rows: tuple[TimelineRow, ...]):
        known = [frame for row in rows for frame in (row.first, row.last) if frame is not None]
        if not known:
            self.first, self.span = 1, self.total
        else:
            first, last = min(known), max(known)
            margin = max(2, math.ceil((last - first + 1) * .15))
            self.first = max(1, first - margin)
            self.span = min(self.total, last + margin) - self.first + 1
        self.configure(self.total)

    def ticks(self, count=6):
        return tuple(sorted({self.first + round(i * (self.span - 1) / max(1, count - 1))
                             for i in range(count)}))


class DragInterrupted(RuntimeError):
    """Another editor/history operation took ownership; the last valid edit stays."""


class _DragHistory:
    """Forward the first snapshot only, without opening a global History group."""
    def __init__(self, history: History):
        self.history = history
        self.before_undo = list(history.undo_stack)
        self.before_redo = list(history.redo_stack)
        self.record = None
        self.expected_undo = list(history.undo_stack)
        self.expected_redo = list(history.redo_stack)

    @staticmethod
    def _same(left, right):
        return len(left) == len(right) and all(a is b for a, b in zip(left, right))

    def uncontested(self):
        return (self.history.group is None
                and self._same(self.history.undo_stack, self.expected_undo)
                and self._same(self.history.redo_stack, self.expected_redo))

    def push(self, project, label):
        if not self.uncontested():
            raise DragInterrupted('其他编辑已开始；拖动已提交为独立撤销步骤。')
        if self.record is None:
            self.history.push(project, '拖动字幕边界')
            self.record = self.history.undo_stack[-1]
            self.expected_undo = list(self.history.undo_stack)
            self.expected_redo = list(self.history.redo_stack)

    def discard(self):
        if not self.uncontested():
            return False
        self.history.undo_stack[:] = self.before_undo
        self.history.redo_stack[:] = self.before_redo
        return True


@dataclass(frozen=True)
class DragUpdate:
    frame: int
    changed: bool
    snapped: bool = False
    snap_text: str = ''


class FrameDragController:
    """Headless single-edge transaction; use begin/update/finish or cancel.

    update clamps to video and a known opposite edge by default. ``clamp=False``
    rejects instead. Only the requested timestamp is passed to Editor.set_time;
    an unknown opposite edge remains None. A callback must be lightweight and
    must not run another editor operation reentrantly.
    """
    def __init__(self, project: Project, index: FrameIndex | None, history: History,
                 on_change: Callable[[str], None] = lambda identity: None):
        self.project, self.index, self.history = project, index, history
        self.on_change = on_change
        self.active = False
        self.interrupted = False
        self.changed = False

    def begin(self, identity: str, edge: str):
        if self.active:
            raise RuntimeError('已有边界拖动正在进行')
        if self.index is None:
            raise ValueError('请等待真实帧索引完成。')
        if edge not in ('start', 'end'):
            raise ValueError('边界必须是 start 或 end')
        if self.history.group is not None:
            raise DragInterrupted('请先结束正在进行的文字或帧数编辑。')
        row = next((row for row in self.project.mappings if row.id == identity), None)
        if row is None:
            raise ValueError('字幕已不存在，请重新选择。')
        opposite = row.end if edge == 'start' else row.start
        if opposite is not None:
            if not _known(opposite):
                raise ValueError('另一侧边界无效，请先修正。')
            if edge == 'start' and not self.index.stamps[0] + TIME_EPSILON < opposite <= self.index.duration + TIME_EPSILON:
                raise ValueError('结束边界超出真实帧范围，请先修正。')
            if edge == 'end' and opposite > self.index.stamps[-1] + TIME_EPSILON:
                raise ValueError('开始边界超出真实帧范围，请先修正。')
        self.identity, self.edge, self.row = identity, edge, row
        self.original = deepcopy(row)
        self.expected = asdict(row)
        self.transaction = _DragHistory(self.history)
        self.editor = Editor(self.project, self.transaction, lambda: None)
        self.active, self.interrupted, self.changed = True, False, False
        return self

    def _check(self):
        if not self.active:
            raise RuntimeError('没有正在进行的边界拖动')
        current = next((row for row in self.project.mappings if row.id == self.identity), None)
        if (current is not self.row or asdict(self.row) != self.expected
                or not self.transaction.uncontested()):
            self.active, self.interrupted = False, True
            raise DragInterrupted('其他编辑已介入；保留最后一次合法拖动，不覆盖其他修改。')

    def _limits(self):
        low, high = 1, len(self.index.stamps)
        if self.edge == 'start' and self.row.end is not None:
            if self.row.end <= self.index.stamps[0] + TIME_EPSILON:
                raise ValueError('结束边界之前没有可用视频帧。')
            high = self.index.last(self.row.end)
        elif self.edge == 'end' and self.row.start is not None:
            if self.row.start >= self.index.duration:
                raise ValueError('开始边界之后没有可用视频帧。')
            low = self.index.first(self.row.start)
        return low, high

    def snap_target(self) -> int | None:
        """Previous row's exclusive end, NOT its inclusive last visible frame."""
        if self.edge != 'start':
            return None
        position = self.project.mappings.index(self.row)
        if not position:
            return None
        previous = self.project.mappings[position - 1]
        bounds = frame_bounds(previous, self.index)
        if bounds.last is None or bounds.invalid:
            return None
        target = bounds.last + 1
        low, high = self._limits()
        return target if low <= target <= high else None

    def update(self, frame: int, *, clamp=True, snap=False, pixels_per_frame=1.0,
               snap_pixels=7.0, snap_frames=3, bypass_snap=False) -> DragUpdate:
        self._check()
        if type(frame) is not int:
            raise ValueError('帧号必须是从 1 开始的整数。')
        low, high = self._limits()
        if not low <= frame <= high:
            if not clamp:
                raise ValueError(f'边界需位于第 {low}～{high} 帧；另一侧边界保持不变。')
            frame = max(low, min(high, frame))
        snapped, text = False, ''
        if snap and not bypass_snap:
            target = self.snap_target()
            if (target is not None and math.isfinite(pixels_per_frame) and pixels_per_frame > 0
                    and abs(frame - target) <= snap_frames
                    and abs(frame - target) * pixels_per_frame <= snap_pixels):
                frame, snapped = target, True
                text = f'吸附前句末尾边界 F{target}（前句末帧 F{target - 1}） · Alt 临时绕过'
        start, end = self.index.interval(frame, frame)
        changed = self.editor.set_time(self.identity, **{self.edge: start if self.edge == 'start' else end})
        self.changed |= changed
        self.expected = asdict(self.row)
        if changed:
            self.on_change(self.identity)
        return DragUpdate(frame, changed, snapped, text)

    def finish(self):
        """Commit; a drag returning exactly to its starting state is a no-op."""
        if not self.active:
            return self.changed
        uncontested = self.transaction.uncontested() and asdict(self.row) == self.expected
        if (uncontested and self.row.start == self.original.start and self.row.end == self.original.end):
            self.cancel()
            return False
        self.active = False
        return self.changed

    def cancel(self):
        """Restore isolated drag. If other edits intervened, commit without damage."""
        if not self.active:
            return False
        uncontested = (self.transaction.uncontested() and asdict(self.row) == self.expected
                       and any(row is self.row for row in self.project.mappings))
        self.active = False
        if not uncontested:
            self.interrupted = True
            return False
        if self.changed:
            for name, value in asdict(self.original).items():
                setattr(self.row, name, deepcopy(value))
        self.transaction.discard()
        if self.changed:
            self.on_change(self.identity)
        self.changed = False
        return True


class FrameTimeline(ttk.Frame):
    """Moonlit Lilac canvas; getters always resolve the current host state.

    on_select(id): select the review mapping, preferably without seeking.
    on_change(id): sync frame fields/list row and schedule save, NOT decode/draw.
    on_commit(): optional full refresh/save after release/cancel.
    can_edit(): host guard (finish IME composition / reject busy jobs).

    refresh() after project, index, selection or text changes; pass fit=True
    for a deliberate review selection, not playback follow. update_playhead()
    moves a single Canvas line and does not rebuild rows. Missing edges can be
    explicitly entered by right-clicking a lane; no guessed interval is drawn.
    Keyboard: Up/Down selects neighbors; [ / ] chooses start/end; Left/Right
    nudges that edge (Shift = 5); Home/End scroll; +/- zoom; Escape cancels.
    """
    LABEL_WIDTH = 94
    RULER_HEIGHT = 29
    ROW_HEIGHT = 34
    HEIGHT = RULER_HEIGHT + ROW_HEIGHT * 3 + 6

    def __init__(self, master, *, get_project, get_index, get_history, get_selection,
                 on_select=lambda identity: None, on_change=lambda identity: None,
                 on_commit=lambda: None, can_edit=lambda: True,
                 on_status=lambda message: None, get_accent=lambda: PURPLE, **kwargs):
        kwargs.setdefault('style', 'Card.TFrame')
        kwargs.setdefault('padding', (16, 12))
        super().__init__(master, **kwargs)
        self.get_project, self.get_index = get_project, get_index
        self.get_history, self.get_selection = get_history, get_selection
        self.on_select, self.on_change, self.on_commit = on_select, on_change, on_commit
        self.can_edit, self.on_status = can_edit, on_status
        self.get_accent = get_accent
        self._context_menu = None
        self.viewport = FrameViewport()
        self._project = self._index = self._selected = None
        self.controller = None
        self._drag = self._pan = None
        self._playhead = None
        self._playhead_frame = None
        self._edge = 'start'
        self.collapsed = False
        self.rows = ()
        self._hits = []
        self._rendering = False
        self._destroyed = False
        self._resize_job = None
        self._render_size = None
        self._suppress_select_fit = False
        header = ttk.Frame(self, style='Surface.TFrame')
        header.pack(fill='x')
        ttk.Label(header, text='◈  帧级歌词编辑', style='Surface.TLabel', font=('Microsoft YaHei UI', 10, 'bold')).pack(side='left')
        self.range_label = ttk.Label(header, text='等待真实帧索引', style='Muted.Surface.TLabel', foreground=MUTED)
        self.collapse_button = ttk.Button(header, text='收起⌃', width=7, command=self.toggle)
        self.collapse_button.pack(side='right')
        for text, command in (('适应', self.fit), ('＋', lambda: self.zoom(1.5)), ('−', lambda: self.zoom(1 / 1.5))):
            ttk.Button(header, text=text, width=5, command=command).pack(side='right', padx=(0, 4))
        self.snap = tk.BooleanVar(master=self, value=True)
        ttk.Checkbutton(header, text='吸附前句', variable=self.snap, style='Surface.TCheckbutton').pack(side='right', padx=6)
        self.range_label.pack(side='right', padx=8)
        ttk.Label(self, text='AE 风格入点 / 出点调整 · 真实重叠允许存在 · 所有单位均为帧',
                  style='Muted.Surface.TLabel', foreground=MUTED, font=('Microsoft YaHei UI', 9)).pack(anchor='w', pady=(2, 6))
        self.body = ttk.Frame(self, style='Surface.TFrame')
        self.body.pack(fill='x')
        self.canvas = tk.Canvas(self.body, height=self.HEIGHT, background='#FFFFFF',
                                highlightthickness=0, takefocus=True, borderwidth=0)
        self.canvas.pack(fill='x', expand=True)
        self.scrollbar = ttk.Scrollbar(self.body, orient='horizontal', command=self._scroll)
        self.scrollbar.pack(fill='x', padx=(self.LABEL_WIDTH, 8), pady=(2, 3))
        self.hint = ttk.Label(self.body, style='Muted.Surface.TLabel', foreground=MUTED, font=('Microsoft YaHei UI', 9))
        self.hint.pack(anchor='w', pady=(3, 0))
        self.canvas.bind('<Configure>', self._on_canvas_configure)
        self.canvas.bind('<ButtonPress-1>', self._press)
        self.canvas.bind('<B1-Motion>', self._motion)
        self.canvas.bind('<ButtonRelease-1>', self._release)
        self.canvas.bind('<Motion>', self._hover)
        self.canvas.bind('<ButtonPress-2>', self._pan_start)
        self.canvas.bind('<B2-Motion>', self._pan_move)
        self.canvas.bind('<ButtonRelease-2>', self._pan_end)
        self.canvas.bind('<Button-3>', self._menu)
        self.canvas.bind('<MouseWheel>', self._wheel)
        self.canvas.bind('<Button-4>', lambda event: self._wheel_linux(-1))
        self.canvas.bind('<Button-5>', lambda event: self._wheel_linux(1))
        self.canvas.bind('<KeyPress>', self._key)
        self.canvas.bind('<FocusOut>', lambda event: self.finish_drag())
        self.bind('<Unmap>', lambda event: self.finish_drag() if event.widget is self else None)
        self.bind('<Destroy>', self._destroy, add='+')
        self.refresh(fit=True)

    @property
    def dragging(self):
        return self._drag is not None

    def refresh(self, *, fit=False):
        project, index, selected = self.get_project(), self.get_index(), self.get_selection()
        if isinstance(selected, Mapping):
            selected = selected.id
        replaced = project is not self._project or index is not self._index
        changed_selection = selected != self._selected
        if self.dragging and (replaced or changed_selection):
            self.finish_drag()
        self._project, self._index, self._selected = project, index, selected
        self.rows = timeline_rows(project, index, selected)
        if index is not None:
            self.viewport.configure(len(index.stamps))
            # Only deliberate fitting may reset zoom/pan. Playback selection
            # changes update the three rows without recentering every cue.
            if replaced or (fit and not self._suppress_select_fit):
                self.viewport.fit(self.rows)
        self._render()

    def _canvas_size(self):
        return self.canvas.winfo_width(), self.canvas.winfo_height()

    def _cancel_resize_render(self):
        job = getattr(self, '_resize_job', None)
        self._resize_job = None
        if job is not None:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass

    def _on_canvas_configure(self, event):
        """Ignore moves; coalesce a resize burst without waiting for it to stop."""
        if self._destroyed or event.widget is not self.canvas:
            return
        if self._canvas_size() == self._render_size:
            self._cancel_resize_render()
        elif self._resize_job is None:
            # Do not restart this timer on every event: live sash drags keep
            # receiving fresh geometry, at most once per display-sized batch.
            self._resize_job = self.after(16, self._flush_resize_render)

    def _flush_resize_render(self):
        self._resize_job = None
        if not self._destroyed and self._canvas_size() != self._render_size:
            self._render()

    def _width(self):
        return max(1, self.canvas.winfo_width() - self.LABEL_WIDTH - 8)

    def _x(self, frame):
        return self.LABEL_WIDTH + self.viewport.frame_x(frame, self._width())

    def _set_hint(self, text=None, error=False):
        if text is None:
            relations = [relation_text(a, b) for a, b in zip(self.rows, self.rows[1:])]
            text = ' · '.join(part for part in relations if part)
            text = (text + '　|　' if text else '') + '拖边缘调整；中键平移；右键补时间；[ / ] 选边缘，方向键微调'
        self.hint.configure(text=text, foreground=RED if error else MUTED)

    def _rounded(self, x1, y1, x2, y2, *, fill, outline=''):
        radius = min(5, (x2 - x1) / 2, (y2 - y1) / 2)
        return self.canvas.create_polygon(
            x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
            x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
            x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
            smooth=True, fill=fill, outline=outline, width=1)

    def _render(self):
        if self._rendering or getattr(self, '_destroyed', False) or not self.winfo_exists():
            return
        # A content edit/selection may paint before the resize timer. Its fresh
        # geometry satisfies that pending request; do not rebuild it twice.
        self._cancel_resize_render()
        self._render_size = self._canvas_size()
        self._rendering = True
        try:
            canvas, width = self.canvas, self._width()
            accent, pale, accent_ink = timeline_palette(self.get_accent())
            canvas.delete('all')
            self._hits = []
            canvas.create_text(0, 16, text='帧标尺', anchor='w', fill=MUTED, font=('Microsoft YaHei UI', 9))
            right = self.LABEL_WIDTH + width
            ticks = ()
            if self._index:
                self.range_label.configure(text=f'F {self.viewport.first} — F {self.viewport.last}')
                ticks = self.viewport.ticks()
                for frame in ticks:
                    x = self._x(frame)
                    canvas.create_line(x, 23, x, self.HEIGHT, fill=LINE)
                    canvas.create_text(max(self.LABEL_WIDTH + 20, min(right - 20, x)), 10,
                                       text=f'F{frame}', fill=MUTED, font=('Microsoft YaHei UI', 8))
                self.scrollbar.set((self.viewport.first - 1) / self.viewport.total,
                                   self.viewport.last / self.viewport.total)
            else:
                self.range_label.configure(text='等待真实帧索引')
                self.scrollbar.set(0, 1)
            for i, row in enumerate(self.rows):
                y = self.RULER_HEIGHT + i * self.ROW_HEIGHT + 3
                current = i == 1
                color = RED if row.overlap else accent if current else MUTED
                canvas.create_text(0, y + 13, anchor='w', fill=color,
                    text=row.slot + (f' · {row.number}' if row.number else ''),
                    font=('Microsoft YaHei UI', 9, 'bold' if current else 'normal'))
                self._rounded(self.LABEL_WIDTH, y, right, y + 27, fill='#F9F7FC')
                for frame in ticks:
                    x = self._x(frame)
                    canvas.create_line(x, y, x, y + 27, fill=LINE)
                if not row.identity:
                    canvas.create_text(self.LABEL_WIDTH + 10, y + 13, anchor='w', text=row.status,
                                       fill=MUTED, font=('Microsoft YaHei UI', 9))
                    continue
                if row.complete:
                    raw_left, raw_right = self._x(row.first), self._x(row.last + 1)
                    left, end = max(self.LABEL_WIDTH, raw_left), min(right, raw_right)
                    if end > left:
                        self._rounded(left, y + 4, end, y + 23, fill=accent if current else pale,
                                      outline=RED if row.overlap else '')
                        self._hits.append((left, y + 3, end, y + 24, row.identity, 'bar'))
                        # Clipped edges are not drag handles; never change an offscreen edge by accident.
                        for edge, pos in (('start', raw_left), ('end', raw_right)):
                            if self.LABEL_WIDTH <= pos <= right:
                                handle_width = min(8, max(2, (end - left) / 2))
                                hx1, hx2 = ((pos, pos + handle_width) if edge == 'start'
                                            else (pos - handle_width, pos))
                                canvas.create_line((hx1 + hx2) / 2, y + 8, (hx1 + hx2) / 2, y + 19,
                                                   fill=accent_ink if current else '#9476B5', width=2)
                                self._hits.append((hx1, y + 2, hx2, y + 25, row.identity, edge))
                        label = row.text.replace('\n', ' ') if current else f'F{row.first}–F{row.last}'
                        max_chars = max(0, int((end - left - 18) / 9))
                        if max_chars > 2:
                            label = label if len(label) <= max_chars else label[:max_chars - 1] + '…'
                            canvas.create_text(left + 9, y + 13, text=label, anchor='w',
                                               fill=accent_ink if current else '#4D3F67', font=('Microsoft YaHei UI', 9))
                    else:
                        canvas.create_text(self.LABEL_WIDTH + 10, y + 13, anchor='w',
                            text=f'F{row.first}–F{row.last} · 位于视口外（点击“适应”）', fill=MUTED, font=('Microsoft YaHei UI', 9))
                else:
                    known = []
                    for edge, frame in (('start', row.first), ('end', row.last)):
                        if frame is None:
                            continue
                        known.append(('开始' if edge == 'start' else '结束') + f' F{frame}')
                        x = self._x(frame + int(edge == 'end'))
                        if self.LABEL_WIDTH <= x <= right:
                            canvas.create_line(x, y + 3, x, y + 24, fill=accent if current else pale, width=3)
                            self._hits.append((x - 5, y, x + 5, y + 27, row.identity, edge))
                    caption = ' · '.join([row.status] + known)
                    canvas.create_text(self.LABEL_WIDTH + 10, y + 13, anchor='w', text=caption,
                                       fill=MUTED, font=('Microsoft YaHei UI', 9))
            # These are the project's immutable frame markers, not invented cues.
            for frame in self._project.settings.get('frame_markers', []):
                if (self._index and type(frame) is int and 1 <= frame <= self.viewport.total
                        and self.viewport.first <= frame <= self.viewport.last):
                    x = self._x(frame)
                    for i, row in enumerate(self.rows):
                        if row.identity:
                            y = self.RULER_HEIGHT + i * self.ROW_HEIGHT + 9
                            canvas.create_polygon(x, y - 4, x + 4, y, x, y + 4, x - 4, y,
                                                  fill=accent, outline='')
            self._playhead = canvas.create_line(0, 22, 0, self.HEIGHT, fill=ROSE, width=1, state='hidden')
            self.update_playhead(self._playhead_frame)
            if not self.dragging:
                self._set_hint()
        finally:
            self._rendering = False

    def update_playhead(self, frame=None, *, stamp=None):
        if stamp is not None:
            frame = self._index.current(stamp) if self._index and math.isfinite(stamp) else None
        self._playhead_frame = frame
        if self._playhead is None:
            return
        if (self._index is None or type(frame) is not int
                or not self.viewport.first <= frame <= self.viewport.last or self.collapsed):
            self.canvas.itemconfigure(self._playhead, state='hidden')
        else:
            x = self._x(frame)
            self.canvas.coords(self._playhead, x, 22, x, self.HEIGHT)
            self.canvas.itemconfigure(self._playhead, state='normal')

    def _hit(self, x, y):
        # Input arriving inside the 16 ms resize batch must never hit stale bars.
        if self._canvas_size() != getattr(self, '_render_size', None):
            self._render()
        return next(((identity, kind) for x1, y1, x2, y2, identity, kind in reversed(self._hits)
                     if x1 <= x <= x2 and y1 <= y <= y2), None)

    def _row_at(self, y):
        i = int((y - self.RULER_HEIGHT) // self.ROW_HEIGHT)
        return self.rows[i] if 0 <= i < len(self.rows) else None

    def _select(self, identity):
        self.finish_drag()
        self._suppress_select_fit = True
        try:
            result = self.on_select(identity)
            if result is False:
                return False
            self.refresh()
            return self._selected == identity
        finally:
            self._suppress_select_fit = False

    def _press(self, event):
        self.canvas.focus_set()
        hit = self._hit(event.x, event.y)
        row = self._row_at(event.y)
        if not hit:
            if row and row.identity:
                self._select(row.identity)
            return 'break'
        identity, edge = hit
        if not self._select(identity) or edge == 'bar':
            return 'break'
        if not self.can_edit():
            return 'break'
        try:
            controller = FrameDragController(self.get_project(), self.get_index(), self.get_history(), self.on_change)
            controller.begin(identity, edge)
            bounds = frame_bounds(controller.row, controller.index)
            origin = bounds.first if edge == 'start' else bounds.last
            if origin is None:
                return 'break'
            self.controller = controller
            self._drag = (event.x, origin, self.viewport.first, self.viewport.span)
            self._edge = edge
            self.canvas.grab_set()
            self.canvas.configure(cursor='sb_h_double_arrow')
        except (ValueError, RuntimeError, tk.TclError) as exc:
            self.finish_drag()
            self._set_hint(str(exc), error=True)
            self.on_status(str(exc))
        return 'break'

    def _motion(self, event):
        if not self.dragging:
            return 'break'
        x, origin, _, _ = self._drag
        frame = origin + self.viewport.delta_frames(event.x - x, self._width())
        try:
            update = self.controller.update(frame, snap=self.snap.get(),
                pixels_per_frame=self._width() / self.viewport.span, bypass_snap=bool(event.state & 0x0008))
            self.rows = timeline_rows(self.get_project(), self.get_index(), self._selected)
            self._render()
            bounds = frame_bounds(self.controller.row, self._index)
            display = f'开始 F{bounds.first}' if bounds.first is not None else '开始待补齐'
            display += f' — 结束 F{bounds.last}' if bounds.last is not None else ' — 结束待补齐'
            relations = ' · '.join(filter(None, (relation_text(a, b) for a, b in zip(self.rows, self.rows[1:]))))
            self._set_hint(update.snap_text if update.snapped else display + ('　|　' + relations if relations else ''))
            if update.snapped:
                x = self._x(update.frame)
                accent, _, _ = timeline_palette(self.get_accent())
                self.canvas.create_line(x, 22, x, self.HEIGHT, fill=accent, dash=(3, 3), width=2)
        except DragInterrupted as exc:
            self.finish_drag()
            self._set_hint(str(exc), error=True)
            self.on_status(str(exc))
        except (ValueError, RuntimeError) as exc:
            self._set_hint(str(exc), error=True)
        return 'break'

    def _release(self, event=None):
        self.finish_drag()
        return 'break'

    def finish_drag(self, commit=True):
        controller = self.controller
        self.controller, self._drag = None, None
        if controller is None:
            return
        try:
            changed = controller.finish() if commit else controller.cancel()
        finally:
            try:
                if self.canvas.grab_current() is self.canvas:
                    self.canvas.grab_release()
                self.canvas.configure(cursor='')
            except tk.TclError:
                pass
        if controller.interrupted:
            self.on_status('其他编辑已介入；最后一次合法拖动已保留为独立撤销步骤。')
        elif not commit:
            self.on_status('已取消边界拖动；原始时间与审核状态已恢复。')
        self.on_commit()
        if self.winfo_exists():
            self.refresh()
        return changed

    def _hover(self, event):
        if self.dragging:
            return
        hit = self._hit(event.x, event.y)
        self.canvas.configure(cursor='sb_h_double_arrow' if hit and hit[1] != 'bar' else 'hand2' if hit else '')

    def fit(self):
        self.finish_drag()
        self.refresh(fit=True)

    def zoom(self, factor):
        self.finish_drag()
        self.viewport.zoom(factor)
        self._render()

    def _scroll(self, operation, value, unit=None):
        self.finish_drag()
        if operation == 'moveto':
            self.viewport.first = round(float(value) * self.viewport.total) + 1
            self.viewport.configure(self.viewport.total)
        elif operation == 'scroll':
            self.viewport.pan(int(value) * max(1, round(self.viewport.span * (.8 if unit == 'pages' else .06))))
        self._render()

    def _pan_start(self, event):
        self.finish_drag()
        self._pan = (event.x, self.viewport.first)
        self.canvas.configure(cursor='fleur')
        return 'break'

    def _pan_move(self, event):
        if self._pan:
            x, first = self._pan
            self.viewport.first = first - self.viewport.delta_frames(event.x - x, self._width())
            self.viewport.configure(self.viewport.total)
            self._render()
        return 'break'

    def _pan_end(self, event=None):
        self._pan = None
        self.canvas.configure(cursor='')
        return 'break'

    def _wheel(self, event):
        if event.state & 0x0004:
            self.zoom(1.25 if event.delta > 0 else .8)
        else:
            self._wheel_linux(-1 if event.delta > 0 else 1)
        return 'break'

    def _wheel_linux(self, direction):
        self.finish_drag()
        self.viewport.pan(direction * max(1, round(self.viewport.span * .08)))
        self._render()
        return 'break'

    def _set_edge_at(self, identity, edge, frame):
        self.finish_drag()
        if not self._select(identity) or not self.can_edit():
            return
        controller = FrameDragController(self.get_project(), self.get_index(), self.get_history(), self.on_change)
        try:
            controller.begin(identity, edge)
            controller.update(frame, clamp=False)
            controller.finish()
        except (ValueError, RuntimeError) as exc:
            controller.cancel()
            self.on_status(str(exc))
        finally:
            self.on_commit()
            self.refresh()

    def _menu(self, event):
        self.finish_drag()
        row = self._row_at(event.y)
        if not row or not row.identity or self._index is None or event.x < self.LABEL_WIDTH:
            return 'break'
        frame = self.viewport.x_frame(event.x - self.LABEL_WIDTH, self._width())
        if self._context_menu is not None:
            self._context_menu.destroy()
        menu = self._context_menu = tk.Menu(self, tearoff=False)
        for edge, label in (('start', '开始'), ('end', '结束')):
            menu.add_command(label=f'设为{label}帧 F{frame}',
                             command=lambda edge=edge: self._set_edge_at(row.identity, edge, frame))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            # tk_popup posts asynchronously; destroying here would hide the
            # menu before the user can choose. Its parent owns its lifetime.
            menu.grab_release()
        return 'break'

    def _key(self, event):
        key = event.keysym
        if key == 'Escape':
            self.finish_drag(commit=False)
            return 'break'
        if self.dragging:
            # A global Undo shortcut must commit the drag before the host handles it.
            if event.state & 0x0004:
                self.finish_drag()
            return None
        if key in ('bracketleft', 'bracketright'):
            self._edge = 'start' if key == 'bracketleft' else 'end'
            self._set_hint(('开始' if self._edge == 'start' else '结束') + '边缘已选中；← / → 调整 1 帧，Shift 调整 5 帧')
        elif key in ('Up', 'Down'):
            row = self.rows[0 if key == 'Up' else 2]
            if row.identity:
                self._select(row.identity)
        elif key in ('Left', 'Right'):
            row = self.rows[1]
            frame = row.first if self._edge == 'start' else row.last
            if row.identity and frame is not None:
                amount = (5 if event.state & 0x0001 else 1) * (-1 if key == 'Left' else 1)
                self._set_edge_at(row.identity, self._edge, frame + amount)
            else:
                self._set_hint('此边缘待补齐；请在帧标尺对应位置右键，明确设置开始或结束帧。')
        elif key in ('plus', 'equal', 'KP_Add', 'minus', 'KP_Subtract'):
            self.zoom(1.5 if key in ('plus', 'equal', 'KP_Add') else 1 / 1.5)
        elif key in ('Home', 'End'):
            self.viewport.first = 1 if key == 'Home' else self.viewport.total - self.viewport.span + 1
            self._render()
        else:
            return None
        return 'break'

    def toggle(self):
        self.set_collapsed(not self.collapsed)

    def set_collapsed(self, collapsed):
        self.finish_drag()
        self.collapsed = bool(collapsed)
        if self.collapsed:
            self.body.pack_forget()
        else:
            self.body.pack(fill='x')
        self.collapse_button.configure(text='展开⌄' if self.collapsed else '收起⌃')
        self.update_playhead(self._playhead_frame)

    def _destroy(self, event):
        if event.widget is self:
            self._destroyed = True
            self._cancel_resize_render()
            controller = self.controller
            self.controller, self._drag = None, None
            if controller is not None:
                controller.finish()
