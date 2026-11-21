"""Fixed PTS markers and a copy-only drag onto the real Chinese Text widget.

The controller is display independent.  All mutations go through Editor, never
through canvas coordinates.  Text cuts are Python code-point offsets measured
from ``Text.get('1.0', index)``; Tcl's character units are used only to position
the insertion indicator.  We conservatively keep combining marks, variation
selectors, emoji modifiers/ZWJ sequences, flag pairs and Hangul syllables
together.  This is not a full Unicode UAX #29 segmenter.  Newlines are preserved
verbatim and are legal gaps only when both resulting halves contain text.
"""
from dataclasses import asdict, dataclass
import math
import tkinter as tk
from tkinter import ttk
import unicodedata

from .editing import Editor, mapping_text
from .frames import FrameIndex
from .ime import composition_active


ACCENT = '#8764C6'
PANEL = '#FFFFFF'
MUTED = '#928DA1'
RULE = '#ECE8F2'


def _extend(char):
    value = ord(char)
    return (unicodedata.category(char).startswith('M')
            or 0xFE00 <= value <= 0xFE0F or 0xE0100 <= value <= 0xE01EF
            or 0x1F3FB <= value <= 0x1F3FF or 0xE0020 <= value <= 0xE007F)


def _hangul(char):
    value = ord(char)
    if 0x1100 <= value <= 0x115F or 0xA960 <= value <= 0xA97C:return 'L'
    if 0x1160 <= value <= 0x11A7 or 0xD7B0 <= value <= 0xD7C6:return 'V'
    if 0x11A8 <= value <= 0x11FF or 0xD7CB <= value <= 0xD7FB:return 'T'
    if 0xAC00 <= value <= 0xD7A3:return 'LV' if (value-0xAC00) % 28 == 0 else 'LVT'
    return ''


def character_gaps(text):
    """Conservative cluster-safe Python offsets, including the two endpoints."""
    gaps = [0]
    regional = 0
    for offset in range(1, len(text)):
        left, right = text[offset-1], text[offset]
        a, b = _hangul(left), _hangul(right)
        regional = regional + 1 if 0x1F1E6 <= ord(left) <= 0x1F1FF else 0
        joined = (_extend(right) or '\u200d' in (left, right)
                  or (left == '\r' and right == '\n')
                  or (0xD800 <= ord(left) <= 0xDBFF and 0xDC00 <= ord(right) <= 0xDFFF)
                  or (regional % 2 and 0x1F1E6 <= ord(right) <= 0x1F1FF)
                  or (a == 'L' and b in ('L', 'V', 'LV', 'LVT'))
                  or (a in ('LV', 'V') and b in ('V', 'T'))
                  or (a in ('LVT', 'T') and b == 'T')
                  or any(name in unicodedata.name(left, '') for name in ('VIRAMA', 'HALANT')))
        if not joined:gaps.append(offset)
    if text:gaps.append(len(text))
    return gaps


def nearest_character_gap(text, offset):
    gaps = character_gaps(text)
    return min(gaps, key=lambda gap: (abs(gap-offset), -gap))


def codepoint_at_index(widget, index, text=None):
    """Never treat a Tcl index column or count as a Python character offset."""
    if text is None:text = widget.get('1.0', 'end-1c')
    before = widget.get('1.0', index)
    if not text.startswith(before):
        raise ValueError('无法定位完整字符；请把光标放到两个字符之间。')
    return len(before)


def index_at_codepoint(widget, text, offset):
    """Adapt back to Tk/Tcl units only for visual positioning (Tk 8/9 safe)."""
    if not 0 <= offset <= len(text):raise ValueError('文字位置超出范围。')
    before = text[:offset]
    line = before.count('\n') + 1
    column = int(widget.tk.call('string', 'length', before.rsplit('\n', 1)[-1]))
    return f'{line}.{column}'


@dataclass(frozen=True)
class TextGap:
    character: int
    index: str
    x: int
    y: int
    height: int


def text_gap_at_point(widget, x_root, y_root):
    """Nearest cluster-safe gap within a real, visible Text; None outside it."""
    if widget is None or not widget.winfo_exists() or not widget.winfo_ismapped():return None
    x, y = x_root-widget.winfo_rootx(), y_root-widget.winfo_rooty()
    if not 0 <= x < widget.winfo_width() or not 0 <= y < widget.winfo_height():return None
    text = widget.get('1.0', 'end-1c')
    index = widget.index(f'@{int(x)},{int(y)}')
    # Tk 8's internal units may point into a surrogate pair.  Move back to a
    # complete prefix rather than forwarding that unit count to Python.
    for _ in range(3):
        try:
            offset = codepoint_at_index(widget, index, text)
            break
        except ValueError:index = widget.index(f'{index} -1c')
    else:return None
    rect = widget.bbox(index)
    if rect and offset < len(text) and x >= rect[0] + rect[2]/2:offset += 1
    offset = nearest_character_gap(text, offset)
    index = index_at_codepoint(widget, text, offset)
    rect = widget.bbox(index)
    if not rect:return None
    return TextGap(offset, index, int(rect[0]), int(rect[1]), max(1, int(rect[3])))


def marker_x(frame, index, width, padding=12):
    """Align to the seconds seek track using actual decoded PTS, including VFR."""
    if not isinstance(index, FrameIndex):raise ValueError('请等待视频帧索引加载完成。')
    span = max(0, width-2*padding)
    return padding + span * index.stamp(frame)/index.duration


def hit_marker(markers, index, width, x, tolerance=9, selected=None):
    candidates = [(abs(marker_x(frame, index, width)-x), frame) for frame in markers]
    candidates = [(distance, frame) for distance, frame in candidates if distance <= tolerance]
    return min(candidates, key=lambda pair: (pair[0], pair[1] != selected, pair[1]))[1] if candidates else None


@dataclass(frozen=True)
class MarkerDrag:
    project: object
    index: FrameIndex
    identity: str
    text: str
    start: object
    end: object
    frame: int
    row_state: dict


class MarkerController:
    """A stable drag token prevents a stale selection/project from being edited."""
    def __init__(self, *, get_project, get_index, get_history, get_selection,
                 get_current_frame, on_change, on_select=lambda identity: None,
                 can_edit=lambda: True):
        self.get_project = get_project
        self.get_index = get_index
        self.get_history = get_history
        self.get_selection = get_selection
        self.get_current_frame = get_current_frame
        self.on_change = on_change
        self.on_select = on_select
        self.can_edit = can_edit

    def _ready(self):
        if not self.can_edit():raise ValueError('当前无法修改；请先完成输入或等待后台任务。')
        index = self.get_index()
        if not isinstance(index, FrameIndex):raise ValueError('请等待视频帧索引加载完成后再使用标记点。')
        project = self.get_project()
        if project is None:raise ValueError('请先打开工程。')
        return project, index

    def markers(self):
        project, index = self.get_project(), self.get_index()
        if project is None or not isinstance(index, FrameIndex):return []
        return sorted({frame for frame in project.settings.get('frame_markers', [])
                       if type(frame) is int and 1 <= frame <= len(index.stamps)})

    def add_current(self):
        project, index = self._ready()
        frame = self.get_current_frame()
        index.stamp(frame)
        Editor(project, self.get_history(), self.on_change).add_marker(frame, index=index)
        return frame

    def delete(self, frame):
        project, _ = self._ready()
        if type(frame) is not int or frame not in project.settings.get('frame_markers', []):
            raise ValueError('请先选择一个标记点。')
        Editor(project, self.get_history(), self.on_change).delete_marker(frame)

    def begin(self, frame, widget_text):
        project, index = self._ready()
        if frame not in self.markers():raise ValueError('该标记点不存在或超出了当前视频帧范围。')
        identity = self.get_selection()
        row = next((row for row in project.mappings if row.id == identity), None)
        if row is None:raise ValueError('请先在右侧选择一句中文译文。')
        text = mapping_text(project, row)
        if text != widget_text:raise ValueError('请先完成当前字幕编辑后再试。')
        stamp = index.stamp(frame)
        for boundary in (row.start, row.end):
            if boundary is not None and (isinstance(boundary, bool) or not isinstance(boundary, (float, int))
                                         or not math.isfinite(boundary) or boundary < 0):
                raise ValueError('当前句起止时间无效，请先修正。')
        if (row.start is not None and stamp <= row.start) or (row.end is not None and stamp >= row.end):
            raise ValueError(f'F{frame} 不在当前句的起止帧内部，请在字幕片段内部切分。')
        return MarkerDrag(project, index, identity, text, row.start, row.end, frame, asdict(row))

    def validate(self, drag, character, widget_text):
        project, index = self._ready()
        if project is not drag.project or index is not drag.index or self.get_selection() != drag.identity:
            raise ValueError('当前选中的字幕或视频已变动，本次拖动已取消。')
        current = self.begin(drag.frame, widget_text)
        if current.text != drag.text or current.row_state != drag.row_state:
            raise ValueError('当前字幕内容已变动，请重新拖动标记点。')
        if (type(character) is not int or not 0 < character < len(drag.text)
                or not drag.text[:character].strip() or not drag.text[character:].strip()):
            raise ValueError('请拖放到两个字之间，确保切分后前后两段都有文字。')
        if character not in character_gaps(drag.text):
            raise ValueError('不能拆开组合字符或 emoji；请选完整字符之间的空隙。')
        return project, index

    def split(self, drag, character, widget_text):
        project, index = self.validate(drag, character, widget_text)
        result = Editor(project, self.get_history(), self.on_change).split_at_marker(
            drag.identity, character, drag.frame, index)
        self.on_select(result[1].id)
        return result


class MarkerTrack(ttk.Frame):
    """A compact fixed-marker lane immediately below the preview seek scale.

    ``get_selection`` returns the current mapping ID, not a row number.
    ``get_current_frame`` must return a 1-based actual FrameIndex frame integer.
    ``on_change`` is the normal Editor change/save hook; ``on_select`` receives
    the new right-hand mapping ID after a successful split.  Call ``refresh``
    after project/selection/marker changes, ``update_playhead`` during playback.
    """
    def __init__(self, master, *, get_project, get_index, get_history, get_selection,
                 get_current_frame, get_text_widget, on_change,
                 on_select=lambda identity: None, on_status=lambda message: None,
                 can_edit=lambda: True, get_accent=lambda: ACCENT, on_begin=lambda: None):
        super().__init__(master, style='Surface.TFrame')
        self.controller = MarkerController(get_project=get_project, get_index=get_index,
            get_history=get_history, get_selection=get_selection, get_current_frame=get_current_frame,
            on_change=on_change, on_select=on_select, can_edit=can_edit)
        self.get_text_widget = get_text_widget
        self.on_status, self.get_accent = on_status, get_accent
        self.on_begin = on_begin
        self.selected_frame = None
        self._drag = self._press = self._ghost = self._indicator = None
        self._scroll_job = self._pointer = None
        self._target = None
        self._target_bindings = []
        self._destroyed = False
        self._resize_job = None
        self._render_size = None
        self.canvas = tk.Canvas(self, height=27, highlightthickness=1, highlightbackground=RULE,
                                highlightcolor=ACCENT, background=PANEL, takefocus=True)
        self.canvas.pack(fill='x')
        controls = ttk.Frame(self, style='Surface.TFrame')
        controls.pack(fill='x', pady=(3, 0))
        ttk.Button(controls, text='＋ 添加标记', command=self.add_current_marker).pack(side='left')
        ttk.Button(controls, text='删除选中标记', command=self.delete_selected_marker).pack(side='left', padx=3)
        ttk.Button(controls, text='在光标处切分', command=self.split_at_caret).pack(side='left')
        self.label = ttk.Label(self, text='将标记点拖到译文两字之间（或将光标移至切分处按 Ctrl+Enter）即可拆句',
                               style='Muted.Surface.TLabel', wraplength=600)
        self.label.pack(fill='x', pady=(2, 0))
        for event, handler in (('<Configure>', self._on_canvas_configure),
                               ('<ButtonPress-1>', self._on_press), ('<B1-Motion>', self._on_motion),
                               ('<ButtonRelease-1>', self._on_release), ('<Escape>', self.cancel_drag),
                               ('<FocusOut>', self._focus_out), ('<Delete>', self.delete_selected_marker),
                               ('<BackSpace>', self.delete_selected_marker), ('<Return>', self.focus_text),
                               ('<Left>', lambda event: self._step(-1)), ('<Right>', lambda event: self._step(1)),
                               ('<Home>', lambda event: self._edge(False)), ('<End>', lambda event: self._edge(True))):
            self.canvas.bind(event, handler)
        self.bind('<Destroy>', self._on_destroy, add='+')
        self.bind('<Unmap>', self.cancel_drag, add='+')
        self.refresh()

    def _color(self):
        color = self.get_accent()
        return color if isinstance(color, str) and len(color) == 7 and color[0] == '#' and all(
            char in '0123456789abcdefABCDEF' for char in color[1:]) else ACCENT

    def _bind_target(self):
        target = self.get_text_widget()
        if target is self._target:return
        self.cancel_drag()
        self._unbind_target()
        self._target = target
        if target is not None:
            for sequence, handler in (('<Control-Return>', self.split_at_caret),
                                       ('<Destroy>', self.cancel_drag)):
                self._target_bindings.append((sequence, target.bind(sequence, handler, add='+')))

    def _unbind_target(self):
        for sequence, identity in self._target_bindings:
            try:self._target.unbind(sequence, identity)
            except tk.TclError:pass
        self._target_bindings.clear()
        self._target = None

    def refresh(self):
        if self._destroyed:return
        self._bind_target()
        markers = self.controller.markers()
        if self.selected_frame not in markers:self.selected_frame = None
        if self._drag:
            try:
                current = self.controller.begin(self._drag.frame, self._target.get('1.0', 'end-1c'))
                if (current.project is not self._drag.project or current.index is not self._drag.index
                        or current.identity != self._drag.identity or current.frame != self._drag.frame
                        or current.text != self._drag.text or current.row_state != self._drag.row_state):
                    raise ValueError('字幕内容已变动，本次拖动已取消。')
            except (ValueError, tk.TclError) as exc:
                self.cancel_drag();self.on_status(str(exc))
        self.render()

    def _canvas_size(self):
        return self.canvas.winfo_width(), self.canvas.winfo_height()

    def _cancel_resize_render(self):
        job = getattr(self, '_resize_job', None)
        self._resize_job = None
        if job is not None:
            try:self.after_cancel(job)
            except tk.TclError:pass

    def _on_canvas_configure(self, event):
        """Only actual size changes repaint; keep one non-restarting 16 ms job."""
        if self._destroyed or event.widget is not self.canvas:return
        if self._canvas_size() == self._render_size:self._cancel_resize_render()
        elif self._resize_job is None:
            self._resize_job = self.after(16, self._flush_resize_render)

    def _flush_resize_render(self):
        self._resize_job = None
        if not self._destroyed and self._canvas_size() != self._render_size:self.render()

    def render(self):
        if self._destroyed:return
        # Explicit content changes paint immediately and consume pending resize.
        self._cancel_resize_render()
        self._render_size = self._canvas_size()
        canvas, color = self.canvas, self._color()
        canvas.delete('all')
        width = canvas.winfo_width()
        canvas.configure(highlightcolor=color)
        canvas.create_line(12, 14, max(12, width-12), 14, fill=RULE, width=3)
        index = self.controller.get_index()
        markers = self.controller.markers()
        for frame in markers:
            x = marker_x(frame, index, width)
            selected = frame == self.selected_frame
            canvas.create_polygon(x, 6, x+7, 14, x, 22, x-7, 14,
                                  fill=color, outline='#2B2638' if selected else PANEL,
                                  width=2 if selected else 1)
        self.update_playhead()
        if not isinstance(index, FrameIndex):text = '标记点 · 正在建立视频帧索引…'
        elif self.selected_frame is not None:text = f'已选 F{self.selected_frame} · 拖到译文字间，或按 Enter 定位光标后 Ctrl+Enter 切分'
        else:text = f'{len(markers)} 个标记点 · ←/→ 切换，Delete 删除，拖到译文字间可切分'
        self.label.configure(text=text)

    def update_playhead(self):
        if self._destroyed:return
        self.canvas.delete('playhead')
        index = self.controller.get_index()
        if not isinstance(index, FrameIndex):return
        try:x = marker_x(self.controller.get_current_frame(), index, self.canvas.winfo_width())
        except (TypeError, ValueError):return
        self.canvas.create_line(x, 2, x, 26, fill='#C17DA8', width=1, tags='playhead')

    def select_marker(self, frame):
        if frame in self.controller.markers():
            self.selected_frame = frame
            self.render()
            self.on_status(f'已选中标记点 F{frame}，可拖到译文字间或在光标处按 Ctrl+Enter 切分。')

    def _step(self, direction):
        markers = self.controller.markers()
        if markers:
            position = markers.index(self.selected_frame) if self.selected_frame in markers else (-1 if direction > 0 else len(markers))
            self.select_marker(markers[max(0, min(len(markers)-1, position+direction))])
        return 'break'

    def _edge(self, last):
        markers = self.controller.markers()
        if markers:self.select_marker(markers[-1 if last else 0])
        return 'break'

    def add_current_marker(self, event=None):
        if event is not None and event.widget.winfo_class() in ('Entry', 'TEntry', 'Text', 'TCombobox', 'TSpinbox'):return
        self.cancel_drag()
        try:
            self.selected_frame = self.controller.add_current()
            self.on_status(f'已添加标记点 F{self.selected_frame}。')
        except (TypeError, ValueError) as exc:self.on_status(str(exc))
        self.refresh()
        return 'break'

    def delete_selected_marker(self, event=None):
        self.cancel_drag()
        try:
            frame = self.selected_frame
            self.controller.delete(frame)
            self.selected_frame = None
            self.on_status(f'已删除标记点 F{frame}。')
        except (TypeError, ValueError) as exc:self.on_status(str(exc))
        self.refresh()
        return 'break'

    def focus_text(self, event=None):
        self._bind_target()
        if self._target is not None:
            self._target.focus_set()
            self.on_status('请将光标移至译文切分位置，按 Ctrl+Enter 按当前标记点拆句。')
        return 'break'

    def _text_ready(self):
        self._bind_target()
        if self._target is None or not self._target.winfo_exists():raise ValueError('请先打开当前句的中文译文。')
        if not self._target.winfo_ismapped():raise ValueError('请先打开“逐句核对”中的当前中文译文。')
        if str(self._target.cget('state')) != 'normal':raise ValueError('当前中文译文不可编辑。')
        if composition_active(self._target):raise ValueError('请先完成输入法选词上屏后再切分。')
        return self._target.get('1.0', 'end-1c')

    def split_at_caret(self, event=None):
        self.cancel_drag()
        try:
            self.on_begin()
            text = self._text_ready()
            drag = self.controller.begin(self.selected_frame, text)
            character = codepoint_at_index(self._target, 'insert', text)
            self.controller.split(drag, character, text)
            self.on_status(f'已在 F{drag.frame}（第 {character} 字后）切分字幕，请核对前后两句。')
        except (TypeError, ValueError, tk.TclError) as exc:self.on_status(str(exc))
        self.refresh()
        return 'break'

    def _on_press(self, event):
        self.cancel_drag()
        self.canvas.focus_set()
        index = self.controller.get_index()
        if not isinstance(index, FrameIndex):return 'break'
        frame = hit_marker(self.controller.markers(), index, self.canvas.winfo_width(), event.x,
                           selected=self.selected_frame)
        if frame is None:return 'break'
        self.on_begin()
        self.select_marker(frame)
        # A click can select/delete even a marker outside the current cue.  Only
        # dragging requires a stable target and a legal split time.
        self._press = (event.x_root, event.y_root, frame)
        return 'break'

    def _on_motion(self, event):
        if not self._press:return 'break'
        if self._drag is None:
            x, y, frame = self._press
            if abs(event.x_root-x)+abs(event.y_root-y) < 5:return 'break'
            try:
                text = self._text_ready()
                self._drag = self.controller.begin(frame, text)
                self.canvas.grab_set()
                self._ghost = tk.Toplevel(self)
                self._ghost.overrideredirect(True)
                self._ghost.attributes('-topmost', True)
                tk.Label(self._ghost, text=f'◆ F{frame}', background='#EEE8FA', foreground=self._color(),
                         padx=7, pady=4, font=('Microsoft YaHei UI', 10, 'bold')).pack()
                self._indicator = tk.Frame(self._target, width=2, background=self._color(), takefocus=False)
            except (TypeError, ValueError, tk.TclError) as exc:
                self.cancel_drag();self.on_status(str(exc));return 'break'
        self._pointer = (event.x_root, event.y_root)
        if self._ghost:self._ghost.geometry(f'+{max(0, event.x_root+14)}+{max(0, event.y_root+14)}')
        self._update_gap()
        return 'break'

    def _update_gap(self):
        if not self._drag or not self._pointer:return
        try:
            gap = text_gap_at_point(self._target, *self._pointer)
            if gap:
                self.controller.validate(self._drag, gap.character, self._target.get('1.0', 'end-1c'))
                self._indicator.place(x=gap.x, y=gap.y, width=2, height=gap.height)
                self._indicator.lift()
            else:self._indicator.place_forget()
        except (ValueError, tk.TclError):
            if self._indicator:self._indicator.place_forget()
        # Scroll only while inside the target's top/bottom edge, without any
        # decoder requests.  The pointer remains grabbed by the marker canvas.
        if self._scroll_job is None:self._scroll_job = self.after(75, self._autoscroll)

    def _autoscroll(self):
        self._scroll_job = None
        if not self._drag or not self._pointer:return
        try:
            x, y = self._pointer
            target = self._target
            inside = (target.winfo_rootx() <= x < target.winfo_rootx()+target.winfo_width()
                      and target.winfo_rooty() <= y < target.winfo_rooty()+target.winfo_height())
            local = y-target.winfo_rooty()
            direction = -1 if local < 16 else 1 if local >= target.winfo_height()-16 else 0
            if inside and direction:target.yview_scroll(direction, 'units')
            self._update_gap()
        except tk.TclError:self.cancel_drag()

    def _on_release(self, event):
        if self._drag is None:
            self._press = None
            return 'break'
        drag = self._drag
        try:
            text = self._text_ready()
            gap = text_gap_at_point(self._target, event.x_root, event.y_root)
            if gap is None:raise ValueError('未拖放到当前字幕文字上，操作已取消。')
            self.controller.validate(drag, gap.character, text)
            # Release all transient UI before Editor's callback refreshes or
            # replaces the detail Text/selection.
            self.cancel_drag()
            self.controller.split(drag, gap.character, text)
            self.on_status(f'已在 F{drag.frame}（第 {gap.character} 字后）切分字幕，请核对前后两句。')
        except (TypeError, ValueError, tk.TclError) as exc:
            self.cancel_drag();self.on_status(str(exc))
        self.refresh()
        return 'break'

    def _focus_out(self, event=None):
        if self._drag:self.cancel_drag();self.on_status('已取消拖动标记点。')

    def cancel_drag(self, event=None):
        if self._scroll_job is not None:
            try:self.after_cancel(self._scroll_job)
            except tk.TclError:pass
        self._scroll_job = None
        for widget in (self._ghost, self._indicator):
            if widget is not None:
                try:widget.destroy()
                except tk.TclError:pass
        self._ghost = self._indicator = self._drag = self._press = self._pointer = None
        try:
            if self.canvas.grab_current() is self.canvas:self.canvas.grab_release()
        except tk.TclError:pass
        return 'break'

    def _on_destroy(self, event):
        if event.widget is self:
            self._destroyed = True
            self._cancel_resize_render()
            self.cancel_drag()
            self._unbind_target()
