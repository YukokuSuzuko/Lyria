"""Small, shared desktop controls with plain-language help."""
import tkinter as tk
from tkinter import ttk


class Tip:
    def __init__(self, widget, text):
        self.widget, self.text, self.popup, self.timer = widget, text, None, None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        widget.bind("<Destroy>", self.hide, add="+")

    def schedule(self, _=None):
        self.hide()
        self.timer = self.widget.after(550, self.show)

    def show(self):
        self.timer = None
        self.popup = tk.Toplevel(self.widget)
        self.popup.wm_overrideredirect(True)
        x = min(self.widget.winfo_rootx(), self.widget.winfo_screenwidth()-360)
        y = min(self.widget.winfo_rooty()+self.widget.winfo_height()+6,
                self.widget.winfo_screenheight()-100)
        self.popup.geometry(f"+{max(0,x)}+{max(0,y)}")
        tk.Label(self.popup, text=self.text, wraplength=330, justify="left",
                 background="#fffbe8", foreground="#24334a", padx=12, pady=8,
                 relief="solid", borderwidth=1).pack()

    def hide(self, _=None):
        if self.timer:
            self.widget.after_cancel(self.timer)
            self.timer = None
        if self.popup:
            self.popup.destroy()
            self.popup = None


def button(parent, text, command, help_text, primary=False):
    widget = ttk.Button(parent, text=text, command=command,
                        style="Primary.TButton" if primary else "TButton")
    widget.tip = Tip(widget, help_text)
    return widget


class Disclosure(ttk.Frame):
    def __init__(self, parent, title, hint=""):
        super().__init__(parent)
        self.title, self.opened = title, False
        self.toggle = button(self, "＋ " + title, self.flip, hint or "展开或收起更多选项")
        self.toggle.pack(anchor="w", pady=4)
        self.body = ttk.Frame(self, padding=(6, 4))

    def flip(self):
        self.opened = not self.opened
        if self.opened:
            self.body.pack(fill="both", expand=True)
        else:
            self.body.pack_forget()
        self.toggle.configure(text=("－ " if self.opened else "＋ ") + self.title)


def choice(parent, variable, options, width=24):
    """Show friendly names while keeping persisted engine/language codes stable."""
    labels = dict(options)
    reverse = {label: code for code, label in options}
    display = tk.StringVar(master=parent, value=labels.get(variable.get(), variable.get()))
    combo = ttk.Combobox(parent, textvariable=display, values=list(reverse),
                         state="readonly", width=width)
    combo.bind("<<ComboboxSelected>>", lambda _: variable.set(reverse[display.get()]))
    token = variable.trace_add("write", lambda *_: display.set(labels.get(variable.get(), variable.get())))
    combo.bind("<Destroy>", lambda _: variable.trace_remove("write", token))
    combo.display = display
    return combo


class ScrollPage(ttk.Frame):
    def __init__(self,parent):
        super().__init__(parent)
        self.canvas=tk.Canvas(self,highlightthickness=0,width=620,height=500)
        bar=ttk.Scrollbar(self,orient='vertical',command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side='right',fill='y');self.canvas.pack(side='left',fill='both',expand=True)
        self.body=ttk.Frame(self.canvas,padding=12)
        item=self.canvas.create_window(0,0,window=self.body,anchor='nw')
        self.body.bind('<Configure>',lambda _:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',lambda e:self.canvas.itemconfigure(item,width=e.width))
        self.winfo_toplevel().bind('<MouseWheel>',self.wheel,add='+')
    def wheel(self,event):
        widget=event.widget
        if isinstance(widget,(ttk.Treeview,ttk.Combobox)):return
        if isinstance(widget,tk.Text) and widget.yview()!=(0.0,1.0):return
        while widget is not None:
            if widget is self:
                self.canvas.yview_scroll(-int(event.delta/120),'units');return
            widget=getattr(widget,'master',None)
