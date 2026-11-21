"""Native Moonlit review: a short list, vertical reading order, one action row.

The controls are adapters over the existing Application commands. In particular,
text/frame variables, the six result values and all pairing trees retain their
old identities; styling never confirms subtitles or requests an API call.
"""
import tkinter as tk
from tkinter import ttk
from .appearance import (BORDER, FIELD, FONT, INK, PANEL, MOONLIT_ACCENT,
                         SELECTED, FitText, blend, selected_surface, valid_color)
from .widgets import button, Disclosure


def _surface(parent, **kwargs):
    return ttk.Frame(parent, style='Surface.TFrame', **kwargs)


def _menu(parent, text):
    control = ttk.Menubutton(parent, text=text)
    menu = tk.Menu(control, tearoff=False, font=(FONT, 10))
    control.configure(menu=menu)
    return control, menu


class _ReviewBody(ttk.Frame):
    """Keep long lyrics / large accessibility fonts reachable in a short pane."""
    def __init__(self, parent):
        super().__init__(parent, style='Surface.TFrame')
        self.canvas = tk.Canvas(self, background=PANEL, highlightthickness=0, width=420, height=230)
        scrollbar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.body = _surface(self.canvas, padding=(0, 4, 6, 0))
        self._body_item = self.canvas.create_window(0, 0, window=self.body, anchor='nw')
        self._canvas_width = None
        self._body_extent = None
        self.body.bind('<Configure>', self._body_configured)
        self.canvas.bind('<Configure>', self._canvas_configured)
        self.winfo_toplevel().bind('<MouseWheel>', self.wheel, add='+')
        self.winfo_toplevel().bind('<Button-4>', self.wheel, add='+')
        self.winfo_toplevel().bind('<Button-5>', self.wheel, add='+')

    def _canvas_configured(self, event):
        # Vertical sash movement does not change text wrapping. Reconfiguring the
        # window item anyway can cause a needless child geometry / FitText chain.
        width = int(event.width)
        if width != self._canvas_width:
            self._canvas_width = width
            self.canvas.itemconfigure(self._body_item, width=width)

    def _body_configured(self, event):
        extent = (0, 0, int(event.width), int(event.height))
        if extent != self._body_extent:
            self._body_extent = extent
            self.canvas.configure(scrollregion=extent)

    def wheel(self, event):
        widget = event.widget
        if isinstance(widget, (ttk.Treeview, ttk.Combobox, ttk.Spinbox)):
            return
        if isinstance(widget, tk.Text) and widget.yview() != (0.0, 1.0):
            return  # A bounded text field scrolls its own long content first.
        while widget is not None:
            if widget is self:
                if self.canvas.yview() != (0.0, 1.0):
                    units = -1 if getattr(event, 'num', None) == 4 else 1
                    if getattr(event, 'delta', 0):
                        units = -int(event.delta/120) or (-1 if event.delta > 0 else 1)
                    self.canvas.yview_scroll(units, 'units')
                    return 'break'
                return
            widget = getattr(widget, 'master', None)


def _field(parent, title, hint, *, chinese=False):
    field = _surface(parent)
    field.pack(fill='x', pady=(0, 9))
    heading = _surface(field)
    heading.pack(fill='x', pady=(0, 4))
    ttk.Label(heading, text=title, style='Section.Surface.TLabel').pack(side='left')
    ttk.Label(heading, text=hint, style='Muted.Surface.TLabel').pack(side='right')
    border = tk.Frame(field, background='#E6DCF4' if chinese else BORDER, padx=1, pady=1)
    border.pack(fill='x')
    box = FitText(border, state='disabled', font=(FONT, 16, 'bold') if chinese else (FONT, 16),
                  background='#FCFAFF' if chinese else FIELD,
                  foreground='#533A80' if chinese else INK, max_lines=4 if chinese else 3,
                  highlightthickness=1 if chinese else 0, highlightbackground='#E6DCF4',
                  highlightcolor='#8764C6', takefocus=chinese)
    box.pack(fill='both', expand=True)
    box.tag_configure('difference', background='#FFF0CC', foreground='#8B4E17')
    return box


def _wrap_review_heading(label, event):
    width = max(220, int(event.width)-12)
    if width != getattr(label, '_review_wrap_width', None):
        label._review_wrap_width = width
        label.configure(wraplength=width)


def build_review(app, review, actions, tree):
    review.configure(style='Surface.TFrame')
    actions.configure(style='Surface.TFrame')
    header = _surface(review)
    header.pack(fill='x', pady=(0, 4))
    ttk.Label(header, text='逐句核对', style='Heading.Surface.TLabel').pack(side='left')
    filter_button, filter_menu = _menu(header, '筛选 / 显示 ▾')
    filter_button.pack(side='right')
    app.review_filter_menu = filter_menu
    filter_menu.add_checkbutton(label='只看待确认', variable=app.only_issues, command=app.refresh)
    filter_menu.add_checkbutton(label='只看 0% 评分', variable=app.only_zero, command=app.refresh)
    app.count_label = ttk.Label(review, text='请结合画面与试听逐句核对。',
                                style='Section.Surface.TLabel', wraplength=480, justify='left')
    app.count_label.pack(fill='x', pady=(0, 5))
    app.count_label._review_wrap_width = 480
    review.bind('<Configure>', lambda e: _wrap_review_heading(app.count_label, e), add='+')
    app.current_hint = tk.StringVar(value='选择歌词后，画面与校对内容会同步定位。')
    status = _surface(review)
    status.pack(fill='x', pady=(0, 5))
    ttk.Label(status, textvariable=app.current_hint, style='Muted.Surface.TLabel').pack(side='left')
    ttk.Label(status, textvariable=app.save_status, style='Muted.Surface.TLabel').pack(side='right')

    vertical = ttk.Panedwindow(review, orient='vertical')
    vertical.pack(fill='both', expand=True)
    listing = _surface(vertical)
    content = _ReviewBody(vertical)
    vertical.add(listing, weight=1)
    vertical.add(content, weight=3)
    app.register_pane('review', vertical, [.28])
    app.results = tree(listing, ('编号', '中文译文', '开始帧', '结束帧', '自动评分', '状态'),
                       (42, 290, 72, 72, 72, 90), height=4)
    app.results.configure(displaycolumns=('编号', '中文译文', '状态'))
    app.results.column('编号', width=42, stretch=False)
    app.results.column('状态', width=90, stretch=False)
    app.results.tag_configure('overlap', foreground='#C62828')
    app.results.tag_configure('playing', background=SELECTED)
    app.results.bind('<<TreeviewSelect>>', app.select_mapping)
    horizontal_scroll = ttk.Scrollbar(listing, orient='horizontal', command=app.results.xview)
    app.results.configure(xscrollcommand=horizontal_scroll.set)
    detailed = tk.BooleanVar(master=review, value=False)
    app.review_detailed_columns = detailed
    def show_columns():
        app.results.configure(displaycolumns='#all' if detailed.get() else ('编号', '中文译文', '状态'))
        if detailed.get():
            horizontal_scroll.pack(side='bottom', fill='x')
        else:
            horizontal_scroll.pack_forget()
    filter_menu.add_separator()
    filter_menu.add_checkbutton(label='详细列表：帧数 / 自动评分', variable=detailed, command=show_columns)

    body = content.body
    app.review_cards = {
        'source': _field(body, '参考原文', '仅供对照参考'),
        'ocr': _field(body, '画面 OCR', '文字差异标色'),
        'chinese': _field(body, '中文译文', '直接编辑 · 修改立即保存', chinese=True),
    }
    # AI keeps its own compact, explicit region; it never replaces user text.
    ai = ttk.Notebook(body)
    ai.pack(fill='x', pady=(0, 5))
    advice = _surface(ai)
    reference = _surface(ai)
    ai.add(advice, text='AI 核对建议 / 需要留意')
    ai.add(reference, text='单句参考翻译')
    app.review_ai_tabs = ai
    app.details = FitText(advice, state='disabled', font=(FONT, 11), background='#FFFBF4',
                          foreground='#795B2D', max_lines=3)
    app.details.pack(fill='x')
    box = FitText(reference, state='disabled', font=(FONT, 12), background=FIELD, max_lines=3)
    box.pack(fill='x')
    app.reference_label = app.review_cards['reference'] = box
    def reference_changed(*_):
        box.configure(state='normal')
        box.delete('1.0', 'end')
        box.insert('1.0', app.reference_value.get())
        box.configure(state='disabled')
        box.schedule_fit()
    app.reference_value.trace_add('write', reference_changed)
    reference_changed()
    options = _surface(body)
    options.pack(fill='x', pady=(1, 2))
    def request_reference():
        ai.select(reference)
        app.reference_translation()
    app.reference_button = button(options, '本句参考翻译', request_reference,
        '调用文本 API 生成单句参考译文，不会覆盖当前译文。')
    app.reference_button.configure(style='Quiet.TButton')
    app.reference_button.pack(side='left')
    app.ai_settings_button = button(options, 'AI 设置', app.api_settings, '制作、单句参考翻译和成片质检共用设置。')
    app.ai_settings_button.configure(style='Quiet.TButton')
    app.ai_settings_button.pack(side='left', padx=5)
    ttk.Spinbox(options, from_=12, to=28, textvariable=app.review_size, width=3,
                command=app.update_review_fonts).pack(side='right')
    ttk.Label(options, text='校对字号', style='Muted.Surface.TLabel').pack(side='right', padx=5)

    _build_pairing_window(app, tree)
    _build_timing_window(app)
    _build_actions(app, actions)
    def apply_accent(_=None):
        accent = getattr(getattr(app, 'appearance', None), 'accent', MOONLIT_ACCENT)
        if not valid_color(accent):
            accent = MOONLIT_ACCENT
        soft = selected_surface(accent)
        app.results.tag_configure('playing', background=soft)
        for card in app.review_cards.values():
            card.configure(selectbackground=soft)
        chinese = app.review_cards['chinese']
        border = '#E6DCF4' if accent.upper() == MOONLIT_ACCENT else blend(accent, amount=.24)
        chinese.master.configure(background=border)
        chinese.configure(highlightcolor=accent, highlightbackground=border,
            background='#FCFAFF' if accent.upper() == MOONLIT_ACCENT else blend(accent, amount=.025),
            foreground='#533A80' if accent.upper() == MOONLIT_ACCENT else blend(accent, INK, .45))
    app.root.bind('<<AppearanceChanged>>', apply_accent, add='+')
    apply_accent()


def _build_pairing_window(app, tree):
    # One reusable advanced editor retains all original tree identities/commands.
    window = tk.Toplevel(app.root)
    window.withdraw()
    window.title('修改原文与中文配对')
    window.geometry('1080x460')
    window.protocol('WM_DELETE_WINDOW', window.withdraw)
    app.mapping_window = window
    app.mapping_editor = Disclosure(window, '原文 / 画面片段 / 中文配对', '按 Ctrl 或 Shift 可多选。')
    app.mapping_editor.pack(fill='both', expand=True, padx=10, pady=6)
    columns = ttk.Frame(app.mapping_editor.body)
    columns.pack(fill='both', expand=True)
    frames = []
    for i, label in enumerate(('原文文件里的歌词', '画面识别出的歌词', '你的中文译文')):
        columns.columnconfigure(i, weight=1)
        columns.rowconfigure(0, weight=1)
        frame = ttk.LabelFrame(columns, text=label)
        frame.grid(row=0, column=i, sticky='nsew', padx=2)
        frames.append(frame)
    app.lyrics_tree = tree(frames[0], ('原文', '参考时间'), (180, 90), height=7)
    app.visuals_tree = tree(frames[1], ('识别文字', '显示时间', '可信程度'), (150, 90, 65), height=7)
    app.trans_tree = tree(frames[2], ('中文译文', '旧时间'), (180, 90), height=7)
    more = ttk.Frame(app.mapping_editor.body)
    more.pack(fill='x', pady=4)
    for label, cmd, hint in (
        ('复用这条中文译文…', app.repeat_mapping, '给同一中文增加另一次出现，原条目保留。'),
        ('重新尝试配对', app.rematch, '已确认结果保持不变。'),
        ('取消本条确认', app.unlock, '允许重新配对这一条。'),
        ('使用选中画面时间', app.use_visual_times, '填入时间后仍需确认。')):
        button(more, label, cmd, hint).pack(side='left', padx=3)
    seconds = ttk.Frame(app.mapping_editor.body)
    seconds.pack(fill='x', pady=4)
    for title, var in (('开始秒', app.start), ('结束秒（不含）', app.end)):
        ttk.Label(seconds, text=title).pack(side='left')
        ttk.Entry(seconds, textvariable=var, width=12).pack(side='left', padx=3)
    button(seconds, '更新帧号', app.display_frames, '将秒数换算为对应视频帧号。').pack(side='left')
    button(seconds, '应用配对', app.apply_pairing, '单独应用配对，不标记已确认。').pack(side='left')


def _build_timing_window(app):
    from .hold import HoldAction
    from .dialogs import center_window
    window = tk.Toplevel(app.root)
    window.withdraw()
    window.title('字幕帧微调')
    window.geometry('640x210')
    app.boundary_window = window
    app.boundary_buttons = []
    def close():
        for control in app.boundary_buttons:
            control.hold_action.stop()
        window.withdraw()
    window.protocol('WM_DELETE_WINDOW', close)
    def show():
        center_window(window, app.root)
        window.focus_set()
    app.show_boundary_editor = show
    timing = ttk.Frame(window, padding=14)
    timing.pack(fill='both', expand=True)
    for column, (name, var, end) in enumerate((('开始帧', app.first_frame, False), ('结束帧', app.last_frame, True))):
        timing.columnconfigure(column, weight=1, uniform='boundaries')
        group = ttk.LabelFrame(timing, text=name, padding=8)
        group.grid(row=0, column=column, sticky='nsew', padx=4)
        ttk.Entry(group, textvariable=var, width=10, justify='right').pack(fill='x', pady=(0, 7))
        row = ttk.Frame(group)
        row.pack(fill='x', pady=3)
        for i, (label, delta) in enumerate((('−5', -5), ('−1', -1), ('＋1', 1), ('＋5', 5))):
            row.columnconfigure(i, weight=1, uniform='nudge')
            control = button(row, label, lambda e=end, d=delta: app.nudge_boundary(e, d),
                f'{name}调整 {delta:+d} 帧；长按连续调整，另一端不变。')
            control.grid(row=0, column=i, sticky='ew', padx=1)
            app.boundary_buttons.append(control)
            control.hold_action = HoldAction(control, app, end, delta)
        button(group, '当前画面设为'+('结束' if end else '开始'), lambda e=end: app.mark_frame(e),
               '设为当前预览画面所在帧。').pack(fill='x', pady=(5, 0))
    ttk.Label(timing, text='结束帧为字幕显示的最后一帧 · 长按可连续调整',
              style='Hint.TLabel').grid(row=1, column=0, columnspan=2, sticky='w', pady=(10, 0))


def _build_actions(app, actions):
    timing = _surface(actions)
    timing.pack(fill='x', pady=(5, 6))
    for column, (label, var) in enumerate((('开始帧', app.first_frame), ('结束帧', app.last_frame))):
        timing.columnconfigure(column, weight=1, uniform='review_times')
        group = ttk.Frame(timing, style='Card.TFrame', padding=(9, 5))
        group.grid(row=0, column=column, sticky='ew', padx=(0, 7) if column == 0 else (0, 0))
        ttk.Label(group, text=label, style='Muted.Surface.TLabel').pack(side='left')
        ttk.Entry(group, textvariable=var, justify='right', width=8).pack(side='right', padx=(6, 0))
    tools = _surface(actions)
    tools.pack(fill='x', pady=(0, 5))
    edit_button, edit = _menu(tools, '编辑与配对 ▾')
    edit_button.pack(side='left')
    app.review_edit_menu = edit
    for label, command in (('拆分中文…', app.split_dialog), ('合并选中的中文', app.merge_selected),
                           ('修改原文 / OCR / 中文配对…', app.show_mapping_editor),
                           ('复用这条中文译文…', app.repeat_mapping), ('取消本条确认', app.unlock)):
        edit.add_command(label=label, command=command)
    edit.add_separator()
    edit.add_command(label='查看工程提示', command=app.global_warnings)
    edit.add_command(label='导出字幕…', command=lambda: app.steps.select(3))
    time_button, times = _menu(tools, '时间工具 ▾')
    time_button.pack(side='left', padx=5)
    app.review_time_menu = times
    for label, command in (('逐帧微调（可长按）…', app.show_boundary_editor),
                           ('当前画面设为开始', lambda: app.mark_frame(False)),
                           ('当前画面设为结束', lambda: app.mark_frame(True)),
                           ('使用画面检测时间', app.use_visual_times),
                           ('使用轮廓辅助时间', app.apply_outline_time)):
        times.add_command(label=label, command=command)
    ttk.Label(tools, text='红色条目表示时间重叠', style='Muted.Surface.TLabel').pack(side='right')
    # Core workflow is one quiet horizontal footer, never a tall button column.
    navigation = _surface(actions)
    navigation.pack(fill='x', pady=(1, 3))
    app.review_navigation = {}
    for key, text, command, primary in (
        ('previous', '← 上一句', app.previous_mapping, False),
        ('next', '下一句 →', app.next_issue, False),
        ('confirm', '✓ 确认并下一句  →', app.confirm_next, True)):
        control = button(navigation, text, command, '标记本句已核对完成。', primary)
        control.pack(side='right' if primary else 'left', padx=(0, 6) if not primary else (10, 0))
        app.review_navigation[key] = control
    ttk.Label(actions, text='改轴后请点击确认标记完成 · Ctrl+Z 撤销',
              style='Muted.Surface.TLabel').pack(anchor='w', pady=(2, 0))
