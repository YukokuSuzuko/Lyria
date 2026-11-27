"""Moonlit Lilac for native Tk: quiet surfaces, readable text, user accents.

The legacy accent constant remains part of the saved-preference contract. A
caller may opt into MOONLIT_ACCENT for a *new* theme, but must not replace a
previously saved colour. Optional motion is bounded to the small navigation
canvases; it never resizes a page, decodes video, or runs an idle animation.
"""
import re
import time
import tkinter as tk
from tkinter import font as tkfont, ttk
from PIL import Image, ImageDraw, ImageTk

DEFAULT_ACCENT = '#66CCFF'
MOONLIT_ACCENT = '#8764C6'
BACKGROUND = '#F8F7FB'
PANEL = '#FFFFFF'
INK = '#2B2638'
MUTED = '#928DA1'
BORDER = '#ECE8F2'
SELECTED = '#EEE8FA'
FIELD = '#FAF9FC'
FONT = 'Microsoft YaHei UI'


def valid_color(value):
    return isinstance(value, str) and bool(re.fullmatch(r'#[0-9a-fA-F]{6}', value))


def ink_on(color):
    rgb = [int(color[i:i+2], 16)/255 for i in (1, 3, 5)]
    linear = [x/12.92 if x <= .04045 else ((x+.055)/1.055)**2.4 for x in rgb]
    luminance = sum(x*w for x, w in zip(linear, (.2126, .7152, .0722)))
    return INK if luminance > .35 else '#ffffff'


def blend(color, surface=PANEL, amount=.12):
    """A light, readable selected surface derived from the chosen accent."""
    rgb = [round(int(color[i:i+2], 16)*amount + int(surface[i:i+2], 16)*(1-amount))
           for i in (1, 3, 5)]
    return '#' + ''.join(f'{value:02X}' for value in rgb)


def selected_surface(accent):
    return SELECTED if accent.upper() == MOONLIT_ACCENT else blend(accent)


class Appearance:
    def __init__(self, root):
        self.root = root
        self.style = ttk.Style(root)
        self.style.theme_use('clam')
        self.images = {}
        for name in ('normal', 'hover', 'pressed', 'primary', 'primary_hover', 'disabled'):
            self.images[name] = ImageTk.PhotoImage(Image.new('RGBA', (28, 28)), master=root)
        for name, prefix in (('normal', 'Round'), ('primary', 'Accent')):
            hover = 'hover' if name == 'normal' else 'primary_hover'
            self.style.element_create(prefix+'.button', 'image', self.images[name],
                ('disabled', self.images['disabled']), ('pressed', self.images['pressed']),
                ('active', self.images[hover]), border=10, padding=0, sticky='nsew')
            self.style.layout('TButton' if name == 'normal' else 'Primary.TButton', [
                (prefix+'.button', {'sticky': 'nsew', 'children': [
                    ('Button.padding', {'sticky': 'nsew', 'children': [
                        ('Button.label', {'sticky': 'nsew'})]})]})])
        self.style.element_create('Round.tab', 'image', self.images['normal'],
            ('selected', self.images['hover']), ('active', self.images['hover']), border=10, sticky='nsew')
        self.style.layout('TNotebook.Tab', [('Round.tab', {'sticky': 'nsew', 'children': [
            ('Notebook.padding', {'sticky': 'nsew', 'children': [('Notebook.label', {'sticky': 'nsew'})]})]})])
        for name in ('check_off', 'check_on', 'thumb', 'trough'):
            size = (18, 18) if name.startswith('check') else (16, 16)
            self.images[name] = ImageTk.PhotoImage(Image.new('RGBA', size), master=root)
        self.style.element_create('Round.check', 'image', self.images['check_off'],
                                  ('selected', self.images['check_on']), sticky='w')
        self.style.layout('TCheckbutton', [('Checkbutton.padding', {'sticky': 'nsew', 'children': [
            ('Round.check', {'side': 'left', 'sticky': 'w'}),
            ('Checkbutton.label', {'side': 'left', 'sticky': 'w'})]})])
        self.style.element_create('Round.Scale.slider', 'image', self.images['thumb'])
        self.style.element_create('Round.trough', 'image', self.images['trough'], border=5, sticky='ew')
        self.style.layout('Horizontal.TScale', [('Round.trough', {'sticky': 'ew', 'children': [
            ('Round.Scale.slider', {'side': 'left', 'sticky': ''})]})])
        for name, size in (('panel', (32, 32)), ('white_panel', (32, 32)), ('switch_off', (44, 24)),
                           ('switch_on', (44, 24)), ('scroll_v', (10, 24)), ('scroll_h', (24, 10)),
                           ('progress', (24, 14)), ('progress_bg', (24, 14))):
            self.images[name] = ImageTk.PhotoImage(Image.new('RGBA', size), master=root)
        for element, image, style in (('Round.panel', 'panel', 'TLabelframe'),
                                      ('Round.white_panel', 'white_panel', 'White.TLabelframe')):
            self.style.element_create(element, 'image', self.images[image], border=12, padding=8, sticky='nsew')
            self.style.layout(style, [(element, {'sticky': 'nsew'})])
        self.style.layout('Card.TFrame', [('Round.white_panel', {'sticky': 'nsew'})])
        self.style.layout('Treeview', [('Treeview.padding', {'sticky': 'nsew', 'children': [
            ('Treeview.treearea', {'sticky': 'nsew'})]})])
        self.style.element_create('Round.entry', 'image', self.images['normal'],
                                  ('focus', self.images['hover']), border=10, padding=4, sticky='nsew')
        self.style.layout('TEntry', [('Round.entry', {'sticky': 'nsew', 'children': [
            ('Entry.padding', {'sticky': 'nsew', 'children': [('Entry.textarea', {'sticky': 'nsew'})]})]})])
        self.style.element_create('Switch.indicator', 'image', self.images['switch_off'],
                                  ('selected', self.images['switch_on']), sticky='w')
        self.style.layout('Switch.TCheckbutton', [('Checkbutton.padding', {'sticky': 'nsew', 'children': [
            ('Switch.indicator', {'side': 'left', 'sticky': 'w'}),
            ('Checkbutton.label', {'side': 'left', 'sticky': 'w'})]})])
        self.images['scroll_track'] = ImageTk.PhotoImage(Image.new('RGBA', (2, 2), PANEL), master=root)
        for direction in ('Vertical', 'Horizontal'):
            self.style.element_create(f'Round.{direction}.Scrollbar.trough', 'image', self.images['scroll_track'], sticky='nsew')
            self.style.element_create(f'Round.{direction}.Scrollbar.thumb', 'image',
                self.images['scroll_v' if direction == 'Vertical' else 'scroll_h'], border=4, sticky='nsew')
            self.style.layout(f'{direction}.TScrollbar', [(f'Round.{direction}.Scrollbar.trough', {
                'sticky': 'ns' if direction == 'Vertical' else 'ew', 'children': [
                    (f'Round.{direction}.Scrollbar.thumb', {'sticky': 'nsew', 'expand': 1})]})])
        for element, key in (('Round.Progressbar.trough', 'progress_bg'), ('Round.Progressbar.pbar', 'progress')):
            self.style.element_create(element, 'image', self.images[key], border=7, padding=0, sticky='nsew')
        self.style.layout('Horizontal.TProgressbar', [('Round.Progressbar.trough', {'sticky': 'ew', 'children': [
            ('Round.Progressbar.pbar', {'side': 'left', 'sticky': 'ns'})]})])
        self.style.layout('TNotebook', [])

    def apply(self, accent):
        if not valid_color(accent):
            raise ValueError('强调色格式为 #RRGGBB，例如 #8764C6。')
        self.accent = accent
        soft = selected_surface(accent)
        for name, fill, border in (
            ('normal', PANEL, BORDER), ('hover', soft, blend(accent, amount=.45)),
            ('pressed', soft, accent), ('primary', accent, accent),
            ('primary_hover', blend(accent, INK, .90), accent), ('disabled', '#F2EFF6', BORDER)):
            im = Image.new('RGBA', (84, 84), BACKGROUND)
            ImageDraw.Draw(im).rounded_rectangle((1, 1, 82, 82), radius=26, fill=fill, outline=border, width=2)
            self.images[name].paste(im.resize((28, 28), Image.Resampling.LANCZOS))
        for name in ('check_off', 'check_on'):
            im = Image.new('RGBA', (54, 54), BACKGROUND)
            draw = ImageDraw.Draw(im)
            draw.rounded_rectangle((3, 3, 47, 47), radius=12,
                fill=accent if name == 'check_on' else PANEL, outline=accent if name == 'check_on' else '#C8C0D4', width=2)
            if name == 'check_on':
                draw.line((12, 25, 21, 35, 39, 14), fill=ink_on(accent), width=5)
            self.images[name].paste(im.resize((18, 18), Image.Resampling.LANCZOS))
        im = Image.new('RGBA', (48, 48), BACKGROUND)
        ImageDraw.Draw(im).rounded_rectangle((5, 5, 43, 43), radius=19, fill=accent)
        self.images['thumb'].paste(im.resize((16, 16), Image.Resampling.LANCZOS))
        im = Image.new('RGBA', (48, 48), BACKGROUND)
        ImageDraw.Draw(im).rounded_rectangle((0, 20, 47, 28), radius=4, fill=BORDER)
        self.images['trough'].paste(im.resize((16, 16), Image.Resampling.LANCZOS))
        for name, size, fill, radius in (
            ('panel', (32, 32), PANEL, 11), ('white_panel', (32, 32), PANEL, 11),
            ('scroll_v', (10, 24), '#C8C0D4', 5), ('scroll_h', (24, 10), '#C8C0D4', 5),
            ('progress', (24, 14), accent, 7), ('progress_bg', (24, 14), BORDER, 7)):
            w, h = size
            im = Image.new('RGBA', (w*3, h*3), BACKGROUND)
            ImageDraw.Draw(im).rounded_rectangle((1, 1, w*3-2, h*3-2), radius=radius*3,
                fill=fill, outline=BORDER if name in ('panel', 'white_panel') else fill, width=2)
            self.images[name].paste(im.resize(size, Image.Resampling.LANCZOS))
        for name, on in (('switch_off', False), ('switch_on', True)):
            im = Image.new('RGBA', (132, 72), BACKGROUND)
            draw = ImageDraw.Draw(im)
            draw.rounded_rectangle((2, 3, 129, 68), radius=33, fill=accent if on else '#C8C0D4')
            x = 98 if on else 34
            draw.ellipse((x-25, 11, x+25, 61), fill=PANEL)
            self.images[name].paste(im.resize((44, 24), Image.Resampling.LANCZOS))
        s = self.style
        s.configure('.', font=(FONT, 10), background=BACKGROUND, foreground=INK)
        s.configure('TFrame', background=BACKGROUND)
        s.configure('TLabel', background=BACKGROUND)
        s.configure('Surface.TFrame', background=PANEL)
        s.configure('Surface.TLabel', background=PANEL, foreground=INK)
        s.configure('Muted.Surface.TLabel', background=PANEL, foreground=MUTED)
        s.configure('Section.Surface.TLabel', background=PANEL, foreground=INK, font=(FONT, 11, 'bold'))
        s.configure('Heading.Surface.TLabel', background=PANEL, foreground=INK, font=(FONT, 13, 'bold'))
        s.configure('TButton', padding=(11, 7), anchor='center', foreground='#6B617A')
        s.configure('Primary.TButton', padding=(14, 8), font=(FONT, 10, 'bold'), foreground=ink_on(accent))
        s.map('Primary.TButton', foreground=[('disabled', MUTED), ('pressed', INK), ('!disabled', ink_on(accent))])
        s.configure('Quiet.TButton', padding=(8, 4))
        s.configure('TMenubutton', padding=(9, 5), background=PANEL, foreground='#6B617A', borderwidth=1, relief='flat')
        s.map('TMenubutton', background=[('active', soft)])
        s.configure('Heading.TLabel', font=(FONT, 13, 'bold'), foreground=INK)
        s.configure('Hero.TLabel', font=(FONT, 20, 'bold'), foreground=INK)
        s.configure('Sparkle.TLabel', font=('Segoe UI Symbol', 20), foreground='#AC8EDB')
        s.configure('Brand.Surface.TLabel', background=PANEL, foreground=INK, font=('Segoe UI', 12, 'bold'))
        s.configure('Studio.Surface.TLabel', background=PANEL, foreground='#A49AB0', font=('Segoe UI', 8, 'bold'))
        s.configure('Local.Surface.TLabel', background=PANEL, foreground='#72B39B')
        s.configure('Menu.TMenubutton', background=PANEL, foreground='#777080', padding=(9, 6), font=(FONT, 10))
        s.map('Menu.TMenubutton', background=[('active', '#F1EDF7'), ('focus', '#F1EDF7')],
              foreground=[('active', '#68469F'), ('focus', '#68469F')])
        s.layout('Menu.TMenubutton', [('Menubutton.padding', {'sticky': 'nsew', 'children': [
            ('Menubutton.label', {'sticky': 'nsew'})]})])
        s.configure('Overline.TLabel', font=(FONT, 9, 'bold'), foreground='#A69BC3')
        s.configure('Hint.TLabel', foreground=MUTED)
        s.configure('Accent.TLabel', foreground=accent)
        s.configure('TLabelframe', background=PANEL, bordercolor=BORDER, relief='solid', borderwidth=1)
        s.configure('TLabelframe.Label', foreground='#7D748D', background=PANEL)
        s.configure('White.TLabelframe', background=PANEL)
        s.configure('White.TLabelframe.Label', foreground='#7D748D', background=PANEL)
        s.configure('TEntry', fieldbackground=PANEL, padding=5, bordercolor=BORDER)
        s.configure('TCombobox', padding=5, fieldbackground=PANEL, arrowsize=15, bordercolor=BORDER)
        s.configure('TSpinbox', padding=4, fieldbackground=PANEL, bordercolor=BORDER)
        s.configure('Surface.TCheckbutton', background=PANEL)
        # Point-size fonts follow Tk/Windows DPI; row height follows real font metrics.
        rowheight = max(32, tkfont.Font(root=self.root, font=(FONT, 10)).metrics('linespace') + 12)
        s.configure('Treeview', rowheight=rowheight, background=PANEL, fieldbackground=PANEL, borderwidth=0, foreground=INK)
        # Preserve overlap/error tag foregrounds even when a row is selected.
        s.map('Treeview', background=[('selected', soft)], foreground=[])
        s.configure('Treeview.Heading', background=FIELD, foreground='#7D748D', relief='flat', padding=(5, 5))
        s.map('Treeview.Heading', background=[('active', soft)])
        s.configure('TNotebook', background=PANEL, borderwidth=0)
        s.configure('TNotebook.Tab', padding=(12, 6), background=PANEL)
        s.map('TNotebook.Tab', background=[('selected', soft)], foreground=[('selected', INK)])
        s.configure('Switch.TNotebook', tabposition='n', borderwidth=0)
        s.configure('Switch.TNotebook.Tab', padding=(14, 5))
        s.configure('Horizontal.TProgressbar', background=accent, troughcolor=BORDER, borderwidth=0)
        for direction in ('Vertical', 'Horizontal'):
            s.configure(f'{direction}.TScrollbar', background=PANEL, troughcolor=PANEL, borderwidth=0, arrowsize=10)
        s.configure('Sash', sashthickness=7, gripcount=0)
        self.root.configure(background=BACKGROUND)
        self.root.event_generate('<<AppearanceChanged>>', when='tail')


class _CanvasMotion:
    """A bounded ease-out of existing canvas items, with latest-target ownership.

    Rendering/focus/layout events must not own the animation clock. Only a new
    selected target primes motion; repeated events for that target are no-ops.
    The workspace adapter then chooses transition() or cancel_transition().
    """
    duration = .176

    def _prime_motion(self, target):
        if hasattr(self, '_motion_target') and target == self._motion_target:
            return False
        origin = getattr(self, '_motion_position', target)
        self.cancel_transition(snap=False)
        self._motion_start = origin
        self._motion_target = target
        # Do not briefly paint the destination and then jump back at transition().
        self._motion_position = origin
        self._motion_primed = True
        self._motion_fraction = 1.0 if origin == target else 0.0
        return True

    def transition(self):
        if getattr(self, '_motion_job', None) is not None and not self._motion_primed:
            return  # Duplicate NotebookTabChanged must not restart the clock.
        origin = self._motion_start if getattr(self, '_motion_primed', False) else self._motion_position
        self.cancel_transition(snap=False)
        self._motion_primed = False
        target = self._motion_target
        if abs(origin-target) < .001:
            self._motion_position = target
            self._motion_fraction = 1.0
            self._paint_motion(target, 1.0)
            return
        self._motion_position = origin
        self._motion_fraction = 0.0
        self._paint_motion(origin, 0.0)
        began = time.monotonic()
        token = object()
        self._motion_token = token
        def tick():
            if token is not self._motion_token:
                return
            self._motion_job = None
            fraction = min(1.0, (time.monotonic()-began)/self.duration)
            eased = 1-(1-fraction)**3
            self._motion_position = origin+(target-origin)*eased
            self._motion_fraction = eased
            self._paint_motion(self._motion_position, eased)
            if fraction < 1.0:
                self._motion_job = self.after(16, tick)
            else:
                self._motion_position = target
        self._motion_job = self.after(16, tick)

    def cancel_transition(self, snap=True):
        self._motion_token = None
        job = getattr(self, '_motion_job', None)
        if job is not None:
            self.after_cancel(job)
        self._motion_job = None
        if snap and hasattr(self, '_motion_target'):
            self._motion_position = self._motion_target
            self._motion_fraction = 1.0
            self._motion_primed = False
            self._paint_motion(self._motion_target, 1.0)

    def _focus_changed(self, _=None):
        """A focus event changes one item's visibility, never the motion state."""
        if not hasattr(self, '_focus_item'):
            return
        state = 'normal' if self.focus_get() is self else 'hidden'
        if state != getattr(self, '_focus_state', None):
            self.itemconfigure(self._focus_item, state=state)
            self._focus_state = state


class ModeSwitch(_CanvasMotion, tk.Canvas):
    """Cached segmented surface; selection moves one existing pill image item."""
    def __init__(self, parent, notebook, accent):
        self.label_font = tkfont.Font(root=parent, family=FONT, size=10, weight='bold')
        self.cell_width = max(126, max(self.label_font.measure(s) for s in ('字幕制作', '成片质检')) + 50)
        self.control_height = max(42, self.label_font.metrics('linespace') + 22)
        super().__init__(parent, width=self.cell_width*2, height=self.control_height,
                         highlightthickness=0, bg=BACKGROUND, takefocus=True)
        self.notebook, self.accent = notebook, accent
        self.bind('<Button-1>', lambda e: self.select(0 if e.x < self.winfo_width()/2 else 1))
        self.bind('<Left>', lambda _: self.select(0))
        self.bind('<Right>', lambda _: self.select(1))
        self.bind('<space>', lambda _: self.select(1-self.notebook.index('current')))
        for event in ('<FocusIn>', '<FocusOut>'):
            self.bind(event, self._focus_changed)
        self.bind('<Configure>', lambda _: self.render())
        self.bind('<Destroy>', lambda _: self.cancel_transition(snap=False), add='+')
        notebook.bind('<<NotebookTabChanged>>', lambda _: self.render(), add='+')
        self.winfo_toplevel().bind('<<AppearanceChanged>>', lambda _: self.render(), add='+')
        self.render()

    def select(self, index):
        self.focus_set()
        self.notebook.select(index)
        return 'break'

    def _make_surfaces(self, width, height):
        """Only actual size changes rasterize. Selection/focus/accent do not."""
        im = Image.new('RGBA', (width*3, height*3), BACKGROUND)
        ImageDraw.Draw(im).rounded_rectangle((1, 1, width*3-2, height*3-2), radius=33,
                                             fill='#F0EDF6', outline=BORDER, width=3)
        self.photo = ImageTk.PhotoImage(im.resize((width, height), Image.Resampling.LANCZOS), master=self)
        pill_width, pill_height = round(width/2)-8, height-8
        pill = Image.new('RGBA', (pill_width*3, pill_height*3))
        ImageDraw.Draw(pill).rounded_rectangle((1, 1, pill_width*3-2, pill_height*3-2),
                                               radius=24, fill=PANEL, outline=BORDER, width=2)
        self.pill_photo = ImageTk.PhotoImage(pill.resize((pill_width, pill_height), Image.Resampling.LANCZOS), master=self)
        self._surface_size = (width, height)

    def render(self):
        selected = self.notebook.index('current')
        color = self.accent()
        if not valid_color(color):
            color = DEFAULT_ACCENT
        width = max(self.cell_width*2, self.winfo_width())
        height = self.control_height
        self._prime_motion(selected)
        signature = (width, height, selected, color)
        if signature == getattr(self, '_render_signature', None):
            self._focus_changed()
            return
        self._motion_color = blend(color, INK, .78) if ink_on(color) == INK else color
        self._draw_width = width
        resized = (width, height) != getattr(self, '_surface_size', None)
        if resized:
            self._make_surfaces(width, height)
        if not hasattr(self, '_pill'):
            self._surface_item = self.create_image(0, 0, image=self.photo, anchor='nw')
            self._pill = self.create_image(4, 4, image=self.pill_photo, anchor='nw')
            self._labels = [self.create_text(0, 0, text=label, font=self.label_font)
                            for label in ('字幕制作', '成片质检')]
            self._focus_line = self.create_line(0, 0, 1, 0, width=2, state='hidden')
            self._focus_item = self._focus_line
        elif resized:
            self.itemconfigure(self._surface_item, image=self.photo)
            self.itemconfigure(self._pill, image=self.pill_photo)
        for i, item in enumerate(self._labels):
            self.coords(item, width*(i+.5)/2, height/2)
        self.itemconfigure(self._focus_line, fill=color)
        self._paint_motion(self._motion_position, self._motion_fraction)
        self._focus_changed()
        self._render_signature = signature

    def _paint_motion(self, position, fraction):
        width, height = self._draw_width, self.control_height
        self.coords(self._pill, position*width/2+4, 4)
        self.coords(self._focus_line, position*width/2+24, height-8, (position+1)*width/2-24, height-8)
        for i, item in enumerate(self._labels):
            strength = max(0.0, 1-abs(position-i))
            self.itemconfigure(item, fill=blend(self._motion_color, MUTED, strength))


def workflow_state(project):
    """Read actual pipeline/manual-review evidence, never the selected UI tab.

    A stage check means the process reached its next stage, not that OCR/AI was
    correct. Export has no current-content signature in the existing project,
    so an old last_export path must not pretend the current edits were exported.
    """
    from .model import valid_time
    stage = getattr(project, 'stage', 'new')
    rows = [row for row in getattr(project, 'mappings', ()) if not row.ignored]
    confirmed = sum(bool(row.confirmed and valid_time(row.start, row.end)) for row in rows)
    total = len(rows)
    return {'completed': (stage in ('vision', 'matching', 'review', 'ready'),
                          stage in ('matching', 'review', 'ready'),
                          bool(total and confirmed == total), False),
            'confirmed': confirmed, 'total': total}


class StepTabs(_CanvasMotion, tk.Canvas):
    """Compact numbered/checkmarked workflow, anchored left like the handoff.

    Selection is navigation only. Completion comes exclusively from get_state;
    resize callbacks reuse that state instead of walking project mappings.
    """
    labels = ('导入素材', '画面识别', '逐句核对', '导出字幕')

    def __init__(self, parent, notebook, accent=None, get_state=None):
        self.label_font = tkfont.Font(root=parent, family=FONT, size=10)
        self.bold_font = tkfont.Font(root=parent, family=FONT, size=10, weight='bold')
        self.badge_font = tkfont.Font(root=parent, family='Segoe UI', size=9, weight='bold')
        self.badge_size = max(22, self.label_font.metrics('linespace')+4)
        self.cell_width = max(112, self.badge_size+max(self.bold_font.measure(label) for label in self.labels)+38)
        height = max(44, self.badge_size+16)
        super().__init__(parent, width=self.cell_width*4+10, height=height,
                         bg=PANEL, highlightthickness=0, takefocus=True)
        self.notebook, self.accent, self.get_state = notebook, accent, get_state
        self.bind('<Button-1>', self._click)
        self.bind('<Left>', lambda _: self.select(max(0, notebook.index('current')-1)))
        self.bind('<Right>', lambda _: self.select(min(3, notebook.index('current')+1)))
        self.bind('<Home>', lambda _: self.select(0))
        self.bind('<End>', lambda _: self.select(3))
        for event in ('<FocusIn>', '<FocusOut>'):
            self.bind(event, self._focus_changed)
        self.bind('<Configure>', lambda _: self.render(refresh_state=False))
        self.bind('<Destroy>', lambda _: self.cancel_transition(snap=False), add='+')
        notebook.bind('<<NotebookTabChanged>>', lambda _: self.render(), add='+')
        self.winfo_toplevel().bind('<<AppearanceChanged>>', lambda _: self.render(refresh_state=False), add='+')
        self.render()

    def _cell(self):
        return self.cell_width  # Deliberately never divide the full window by four.

    def _click(self, event):
        index = int((event.x-4)//self.cell_width)
        if 0 <= index < 4:
            return self.select(index)
        return 'break'  # Blank progress-bar space is not an oversized export tab.

    def select(self, index):
        self.focus_set()
        self.notebook.select(index)
        return 'break'

    def render(self, refresh_state=True):
        selected = self.notebook.index('current')
        color = self.accent() if self.accent else ttk.Style(self).lookup('Accent.TLabel', 'foreground')
        if not valid_color(color):
            color = DEFAULT_ACCENT
        getter = getattr(self, 'get_state', None)
        if refresh_state or not hasattr(self, '_progress_state'):
            state = getter() if getter else {}
            state = state or {}
            completed = tuple(bool(value) for value in state.get('completed', (False,)*4))[:4]
            completed += (False,)*(4-len(completed))
            total = max(0, int(state.get('total', 0)))
            confirmed = min(total, max(0, int(state.get('confirmed', 0))))
            self._progress_state = (completed, confirmed, total)
        completed, confirmed, total = self._progress_state
        self._prime_motion(selected)
        height, cell = int(self['height']), self._cell()
        width = max(1, self.winfo_width())
        badge = getattr(self, 'badge_size', 24)
        signature = (height, cell, width, badge, selected, color, self._progress_state)
        if signature == getattr(self, '_render_signature', None):
            self._focus_changed()
            return
        self._motion_color = color
        self._draw_height, self._draw_cell = height, cell
        self._badge_radius = badge/2
        self._badge_x = 5+badge/2
        self._badge_y = height/2
        badge_font = getattr(self, 'badge_font', self.bold_font)
        if not hasattr(self, '_indicator'):
            self._circles = [self.create_oval(0, 0, 1, 1, width=1) for _ in self.labels]
            self._numbers = [self.create_text(0, 0, font=badge_font) for _ in self.labels]
            self._indicator = self.create_oval(0, 0, 1, 1, width=0)
            self._indicator_number = self.create_text(0, 0, font=badge_font)
            self._labels = [self.create_text(0, 0, text=label, anchor='w') for label in self.labels]
            self._chevrons = [self.create_text(0, 0, text='›', fill='#CCC5D5', font=self.label_font) for _ in range(3)]
            self._focus_outline = self.create_oval(0, 0, 1, 1, dash=(2, 3), state='hidden')
            self._focus_item = self._focus_outline
            self._progress_label = self.create_text(0, 0, anchor='e', fill=MUTED, font=(FONT, 9))
            self._meter_bg = self.create_line(0, 0, 1, 0, fill=BORDER, width=5, capstyle='round')
            self._meter_fill = self.create_line(0, 0, 1, 0, fill=color, width=5, capstyle='round')
        radius, cy = self._badge_radius, self._badge_y
        for i, (circle, number, label) in enumerate(zip(self._circles, self._numbers, self._labels)):
            cx = self._badge_x+cell*i
            self.coords(circle, cx-radius, cy-radius, cx+radius, cy+radius)
            self.itemconfigure(circle, fill='#EDF7F3' if completed[i] else FIELD,
                               outline='#E1EFE8' if completed[i] else BORDER)
            self.coords(number, cx, cy)
            self.itemconfigure(number, text='✓' if completed[i] else f'{i+1:02}',
                               fill='#5D9C86' if completed[i] else '#AAA3B3')
            self.coords(label, cx+radius+8, cy)
            label_color = blend(color, INK, .68) if i == selected else '#847B90' if completed[i] else '#A6A0B0'
            self.itemconfigure(label, font=self.bold_font if i == selected else self.label_font, fill=label_color)
            if i < 3:
                self.coords(self._chevrons[i], cell*(i+1)-7, cy)
        self.itemconfigure(self._indicator, fill=color)
        self.itemconfigure(self._indicator_number, text=f'{selected+1:02}', fill=ink_on(color))
        self.itemconfigure(self._focus_outline, outline=color)
        show_progress = width >= cell*4+230
        progress_state = 'normal' if show_progress else 'hidden'
        self.itemconfigure(self._progress_label, text=f'{confirmed} / {total} 已确认', state=progress_state)
        self.coords(self._progress_label, width-116, cy)
        self.coords(self._meter_bg, width-96, cy, width-10, cy)
        self.itemconfigure(self._meter_bg, state=progress_state)
        ratio = confirmed/total if total else 0
        self.coords(self._meter_fill, width-96, cy, width-96+86*ratio, cy)
        self.itemconfigure(self._meter_fill, fill=color, state=progress_state if ratio else 'hidden')
        self._paint_motion(self._motion_position, self._motion_fraction)
        self._focus_changed()
        self._render_signature = signature

    def _paint_motion(self, position, fraction):
        # Moving this tiny existing badge never resizes/reflows the actual page.
        cx, cy, radius = self._badge_x+self._draw_cell*position, self._badge_y, self._badge_radius
        self.coords(self._indicator, cx-radius, cy-radius, cx+radius, cy+radius)
        self.coords(self._indicator_number, cx, cy)
        self.coords(self._focus_outline, cx-radius-3, cy-radius-3, cx+radius+3, cy+radius+3)

class FitText(tk.Text):
    """Size to wrapped content, bounded; long content remains scrollable."""
    def __init__(self, parent, *, min_lines=1, max_lines=4, **kwargs):
        defaults = dict(height=min_lines, width=1, wrap='word', relief='flat', borderwidth=0,
                        padx=10, pady=7, highlightthickness=0, foreground=INK,
                        insertbackground=INK, selectbackground=SELECTED, selectforeground=INK)
        defaults.update(kwargs)
        super().__init__(parent, **defaults)
        self.min_lines, self.max_lines = min_lines, max(min_lines, max_lines)
        self._fit_job = None
        self._configure_width = None
        self.bind('<Configure>', self._on_configure, add='+')
        self.bind('<Destroy>', self._cancel_fit, add='+')

    def _cancel_fit(self, _=None):
        if self._fit_job is not None:
            self.after_cancel(self._fit_job)
            self._fit_job = None

    def _on_configure(self, event):
        # A sash can move/resize this field vertically without changing wrapping.
        # fit() itself changes height: do not schedule a second count for that.
        width = int(event.width)
        if width != self._configure_width:
            self._configure_width = width
            self.schedule_fit()

    def schedule_fit(self, _=None):
        # Content/font changes still call this explicitly. Many Configure events
        # before the next idle share one count of the final wrapped geometry.
        if self._fit_job is None:
            self._fit_job = self.after_idle(self.fit)

    def fit(self):
        self._fit_job = None
        if not self.winfo_exists() or self.winfo_width() < 20:
            return
        count = self.count('1.0', 'end-1c', 'displaylines')
        lines = min(self.max_lines, max(self.min_lines, (count[0] if count else 0)+1))
        if int(self['height']) != lines:
            self.configure(height=lines)
