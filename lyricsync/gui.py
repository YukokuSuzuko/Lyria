from __future__ import annotations

from pathlib import Path
import copy
import json
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
from .dialogs import filedialog, messagebox
import uuid
import sys

from .widgets import button, Disclosure, choice, Tip, ScrollPage
from .editing import History, mapping_text, split_mapping, merge_mappings
from .frames import FrameIndex
from .credentials import KeyStore, TEXT_SCOPE
from .key_widget import KeyWidget
from .review import ReviewActions
from .live_edit import LiveEdits
from . import ai_config, __version__
from .workspace import WorkspaceActions, overlap_ids, describe_suggestions
from .appearance import Appearance, ModeSwitch, StepTabs, DEFAULT_ACCENT, MOONLIT_ACCENT, workflow_state
from .frontend import DesktopFrontend
from .audio import AudioPlayer, prepare_audio
from .playback import PlaybackBuffer
from .hold import PreviewHold
from .review_layout import build_review
from .model import Mapping, Project, valid_time, active_at
from .export import convert_ktv, export_project, validate_project, layout_entries, placement_options, missing_time_rows, partial_export_problems
from .matching import match_project
from .pipeline import run
from .storage import load_project, save_project, atomic_json, copy_project_cache, ProjectSaver
from .subtitles import time_seconds
from .vision import Video, make_ocr, propose_regions


def tree(parent, columns, widths, height=6, selectmode="extended"):
    panel=ttk.Frame(parent,style='Card.TFrame',padding=7)
    view=ttk.Treeview(panel,columns=tuple(columns),show="headings",height=height,selectmode=selectmode)
    for col,width in zip(columns,widths):
        view.heading(col,text=col)
        view.column(col,width=width,minwidth=40,stretch=True)
    scroll=ttk.Scrollbar(panel,orient="vertical",command=view.yview)
    view.configure(yscrollcommand=scroll.set)
    view.pack(side="left",fill="both",expand=True)
    scroll.pack(side="right",fill="y")
    panel.pack(fill="both",expand=True)
    return view


class Application(DesktopFrontend,LiveEdits,WorkspaceActions,ReviewActions):
    def __init__(self,root,home=None):
        self.root=root
        default_home=Path(sys.executable).parent/"data" if getattr(sys,"frozen",False) else Path(os.environ.get("LOCALAPPDATA",Path.home()))/"MV_LyricSync"
        self.home=Path(home or os.environ.get("LYRICSYNC_HOME") or default_home)
        try:self.home.mkdir(parents=True,exist_ok=True)
        except OSError:
            self.home=Path(os.environ.get("LOCALAPPDATA",Path.home()))/"MV_LyricSync"
            self.home.mkdir(parents=True,exist_ok=True)
        self.windows={};self.panes={};self.drafts={};self.detail_id=None
        self.ui_settings={}
        try:self.ui_settings=json.loads((self.home/'interface.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):pass
        self.audio=AudioPlayer();self.audio_state='未准备';self.audio_path=None
        self.audio_generation=0;self.audio_playing=False
        self.seek_generation=0;self.seek_lock=threading.Lock();self.seek_request=None;self.seek_worker_active=False
        self.seek_epoch=0;self.seek_presented=0;self.scrubbing=False
        self.sound_enabled=tk.BooleanVar(value=True)
        self.ai_enabled=tk.BooleanVar(value=False)
        self.project=Project()
        self.project.settings.update(ai_config.load(self.home))
        self.project.settings["engine"]="rapidocr"
        self.project_path=None
        self.history=History()
        self.drafts={};self.detail_id=None
        self.frame_index=None
        self.index_generation=0
        self._frame_display=('','')
        self.first_frame=tk.StringVar();self.last_frame=tk.StringVar()
        self.review_size=tk.IntVar(value=16)
        self.reference_value=tk.StringVar(value='选中原文后，可点击“翻译选中的原文”查看参考译文。')
        self.jobs=queue.Queue()
        self.project_saver=ProjectSaver(lambda *args:self.jobs.put(('saved',*args)))
        self.save_revision=0
        self.save_status=tk.StringVar(value='尚无修改')
        self.cancel_event=threading.Event()
        self.busy=False
        self.close_requested=False
        self.worker=None
        self.api_key=""
        self.frame=None
        self.playing=False
        self.play_after=None
        self.player=None
        self.stamp=0
        self.preview_video=None
        self.drag=None
        self.view_transform=(0,0,1)
        self.inputs={key:tk.StringVar() for key in ("video","ktv","translation")}
        self.engine=tk.StringVar(value="rapidocr")
        self.language=tk.StringVar(value="jpn+eng")
        self.sample_fps=tk.StringVar(value="3")
        self.groups=tk.BooleanVar()
        self.original_groups=tk.BooleanVar()
        self.order_confirmed=tk.BooleanVar()
        self.status=tk.StringVar(value="先选择视频、原文歌词和中文译文，再到“识别”页面开始处理。")
        self.only_issues=tk.BooleanVar(value=False)
        self.only_zero=tk.BooleanVar(value=False)
        self.preview_chinese=tk.BooleanVar(value=False)
        self.start=tk.StringVar()
        self.end=tk.StringVar()
        root.title(f"Lyria {__version__} · Moonlit Lilac")
        root.geometry("1380x960")
        root.minsize(1180,780)
        root.protocol("WM_DELETE_WINDOW",self.close)
        self.appearance=Appearance(root)
        try:self.appearance.apply(self.accent_color())
        except ValueError:self.appearance.apply(MOONLIT_ACCENT)
        self.build_moonlit_header()
        self.build_menu(self.inline_menu_host)
        self.appearance.style.layout('Hidden.TNotebook.Tab',[])
        self.notebook=ttk.Notebook(root,style="Hidden.TNotebook")
        self.notebook.pack(fill="both",expand=True,padx=16)
        main=ttk.Frame(self.notebook,padding=8)
        self.notebook.add(main,text="制作中文字幕")
        self.main_ui(main)
        self.audit_ui()
        self.mode_switch=ModeSwitch(self.workspace_switch_host,self.notebook,self.accent_color)
        self.mode_switch.pack(anchor='e')
        self.log_window=tk.Toplevel(root);self.log_window.withdraw();self.log_window.title('运行记录')
        self.log_window.geometry('900x500');self.log_window.protocol('WM_DELETE_WINDOW',self.log_window.withdraw)
        self.log=tk.Text(self.log_window,wrap='word',state='disabled',font=('Microsoft YaHei UI',10))
        self.log.pack(fill='both',expand=True)
        self.notebook.bind('<<NotebookTabChanged>>',self.animate_page,add='+')
        self.steps.bind('<<NotebookTabChanged>>',self.animate_page,add='+')
        root.after(400,self.restore_layout)
        root.after(16,self.poll)
        if (self.home/'last_project.json').is_file():root.after(600,self.offer_recovery)
        root.update_idletasks()
        self.install_native_drop()

    def main_ui(self,parent):
        stage_host=ttk.Frame(parent,style='Card.TFrame',padding=(14,7))
        stage_host.pack(fill='x',pady=(0,12))
        self.workspace_rows=ttk.Panedwindow(parent,orient='vertical')
        self.workspace_rows.pack(fill='both',expand=True)
        self.register_pane('workspace_rows',self.workspace_rows,[.70])
        horizontal=ttk.Panedwindow(self.workspace_rows,orient="horizontal")
        self.workspace_rows.add(horizontal,weight=5)
        self.register_pane("main",horizontal,[.53])
        left=ttk.Frame(horizontal,style='Card.TFrame',padding=14)
        right=ttk.Frame(horizontal,style='Card.TFrame',padding=12)
        horizontal.add(left,weight=11)
        horizontal.add(right,weight=10)
        ttk.Label(left,text="画面预览",style="Section.Surface.TLabel").pack(anchor="w",pady=(4,6))
        ttk.Label(left,text="拖动进度条查看画面；按住鼠标拖出方框，选择歌词位置。",
                  wraplength=620,style="Muted.Surface.TLabel").pack(anchor="w",pady=(0,8))
        self.canvas=tk.Canvas(left,background="#181724",height=280,highlightthickness=0)
        self.canvas.pack(fill="both",expand=True)
        self.canvas.bind("<Configure>",self.schedule_preview_draw)
        self.canvas.bind("<Destroy>",lambda _:self.cancel_preview_draw(),add='+')
        self.canvas.bind("<ButtonPress-1>",self.roi_down)
        self.canvas.bind("<B1-Motion>",self.roi_move)
        self.canvas.bind("<ButtonRelease-1>",self.roi_up)
        self.timeline=ttk.Scale(left,from_=0,to=1,command=self.seek_changed)
        self.timeline.pack(fill="x",pady=(8,0))
        marker_host=ttk.Frame(left,style="Surface.TFrame")
        marker_host.pack(fill="x")
        self.timeline.bind('<ButtonPress-1>',self.begin_timeline_drag,add='+')
        self.timeline.bind('<ButtonRelease-1>',self.end_timeline_drag,add='+')
        self.root.bind('<FocusOut>',lambda event:self.end_timeline_drag(event) if event.widget is self.root else None,add='+')
        playback=ttk.Frame(left,style="Surface.TFrame")
        playback.pack(fill="x")
        self.play_button=button(playback,"▶ 播放",self.toggle_play,
            "播放或暂停视频和声音；音频首次需在本机准备缓存。",True)
        self.play_button.pack(side="left",padx=(0,5))
        ttk.Checkbutton(playback,style="Switch.TCheckbutton",text="声音",variable=self.sound_enabled,command=self.pause_playback).pack(side="left",padx=5)
        button(playback,'▶ 本句',self.play_current_cue,'播放当前字幕区间，到末尾停止。').pack(side='left',padx=4)
        button(playback,'↤ 句首',self.jump_to_current_start,'一次性跳到当前字幕开始，不改变时间。').pack(side='left',padx=4)
        frame_steps=ttk.Frame(left,style='Surface.TFrame');frame_steps.pack(fill='x',pady=4)
        self.preview_holds=[]
        for column,(label,delta) in enumerate((('前 5 帧',-5),('前 1 帧',-1),('后 1 帧',1),('后 5 帧',5))):
            frame_steps.columnconfigure(column,weight=1,uniform='preview_steps')
            control=button(frame_steps,label,lambda delta=delta:self.request_step(delta),'暂停并按真实帧号移动画面；按住可连续移动，松开停止。')
            control.grid(row=0,column=column,sticky='ew',padx=2)
            self.preview_holds.append(PreviewHold(control,self,delta))
        preview_options=Disclosure(left,'预览设置 / 跳帧','显示中文叠加、字幕位置和精确跳转设置。')
        preview_options.pack(fill='x',pady=(3,0))
        preview_options.configure(style='Surface.TFrame');preview_options.body.configure(style='Surface.TFrame')
        ttk.Checkbutton(preview_options.body,style="Switch.TCheckbutton",text="叠加中文字幕预览（包含待确认的候选）",
                        variable=self.preview_chinese,command=self.draw).pack(anchor="w",pady=(6,0))
        button(preview_options.body,"调整中文字幕位置…",self.style_settings,"原文是视频画面，不能直接移动；可以把中文移到上方、顶部、底部或自定义高度。").pack(anchor='w',pady=4)
        jump=ttk.Frame(preview_options.body,style='Surface.TFrame');jump.pack(fill='x',pady=4)
        self.jump_frame=tk.StringVar()
        ttk.Label(jump,text='跳到第').grid(row=0,column=0,padx=(0,5))
        self.jump_entry=ttk.Entry(jump,textvariable=self.jump_frame,width=10)
        self.jump_entry.grid(row=0,column=1,sticky='nsew',padx=3)
        ttk.Label(jump,text='帧').grid(row=0,column=2,padx=3)
        self.jump_button=button(jump,'跳转',self.goto_frame,'输入从 1 开始的帧号，查看对应的真实视频帧。')
        self.jump_button.configure(width=10);self.jump_button.grid(row=0,column=3,sticky='nsew',padx=3)
        self.preview_label=tk.StringVar(value="选择 MV 后会自动显示预览。播放时可听原视频声音。")
        ttk.Label(left,textvariable=self.preview_label,wraplength=420,style="Hint.TLabel").pack(fill="x",pady=8)
        self.steps=ttk.Notebook(right,style="Hidden.TNotebook")
        self.steps.pack(fill="both",expand=True)
        pages=[]
        self.step_pages=[]
        for index,name in enumerate(("导入","识别","检查","导出")):
            page=ttk.Frame(self.steps,padding=8) if index==2 else ScrollPage(self.steps)
            self.steps.add(page,text=name)
            pages.append(page if index==2 else page.body)
            self.step_pages.append(page)
        self.step_tabs=StepTabs(stage_host,self.steps,self.accent_color,
            get_state=lambda:workflow_state(self.project))
        self.step_tabs.pack(anchor="w",fill="x")
        files,detect,review_page,output=pages
        review_actions=ttk.Frame(review_page,padding=(2,5));review_actions.pack(side='bottom',fill='x')
        review=ttk.Frame(review_page);review.pack(side='top',fill='both',expand=True)
        ttk.Label(files,text="开始工作",style="Heading.TLabel").pack(anchor="w",pady=(3,10))
        for key,label,patterns,hint in (
            ("video","① 原版 MV 视频","*.mp4 *.mkv *.mov *.avi *.webm","画面中应能看到原文歌词。"),
            ("ktv","② 原文歌词（可选）","*.ass *.ssa *.srt *.txt","支持 ASS / SRT / TXT；不提供时从视频 OCR 提取原文。"),
            ("translation","③ 你准备好的中文译文","*.txt *.srt *.ass *.ssa","使用现成翻译，软件不会改写或重新翻译。")):
            card=ttk.LabelFrame(files,text=label,padding=10)
            card.pack(fill="x",pady=7)
            ttk.Label(card,text=hint,style="Hint.TLabel").pack(anchor="w",pady=(0,5))
            row=ttk.Frame(card);row.pack(fill="x")
            ttk.Entry(row,textvariable=self.inputs[key]).pack(side="left",fill="x",expand=True,padx=(0,6))
            button(row,"选择文件…",lambda k=key,p=patterns:self.pick_input(k,p),hint).pack(side="right")
        ttk.Label(files,text="原始文件不会被覆盖。识别结果和你的修改会自动保存。",
                  wraplength=560,style="Hint.TLabel").pack(anchor="w",pady=12)
        button(files,"下一步",lambda:self.steps.select(1),"选择好视频和中文译文后，设置歌词位置并开始识别。",True).pack(anchor="e",pady=8)
        ttk.Label(detect,text="通过 OCR 确定大致时间轴",style="Heading.TLabel").pack(anchor="w",pady=(3,10))
        ttk.Label(detect,text="先确认左侧的歌词位置，再开始识别。若背景文字很多，建议只框住歌词。",
                  wraplength=560,style="Hint.TLabel").pack(anchor="w",pady=(0,12))
        region=ttk.LabelFrame(detect,text="歌词在哪里？",padding=12);region.pack(fill="x",pady=5)
        region_buttons=ttk.Frame(region);region_buttons.pack(fill='x')
        for i,(label,command,hint) in enumerate((('自动寻找歌词位置',self.regions,'从抽样画面提出候选区域，由你选择。'),
                                               ('识别整个画面',self.full_roi,'清除方框，识别整个画面。'))):
            region_buttons.columnconfigure(i,weight=1,uniform='region')
            button(region_buttons,label,command,hint).grid(row=0,column=i,sticky='ew',padx=3,pady=3)
        ttk.Label(region,text='建议自己画出字幕区域：在左侧视频画面按住鼠标拖出方框，尽量只框住原文歌词。',wraplength=550,style='Hint.TLabel').pack(anchor='w',pady=5)
        language=ttk.Frame(detect);language.pack(fill="x",pady=12)
        ttk.Label(language,text="原文语言").pack(side="left",padx=(0,8))
        choice(language,self.language,[("jpn+eng","日文 + 英文（推荐）"),("eng","英文"),("jpn","日文"),("jpn_vert","日文竖排（实验）")]).pack(side="left")
        advanced=Disclosure(detect,"高级设置","通常保留默认值即可；识别速度、引擎和文本分组设置在这里。")
        advanced.pack(fill="x",pady=3)
        ttk.Label(advanced.body,text="文字识别方式").grid(row=0,column=0,sticky="w",pady=4)
        choice(advanced.body,self.engine,[("rapidocr","RapidOCR（推荐）"),("tesseract","Tesseract（备选）")]).grid(row=0,column=1,sticky="w")
        ttk.Label(advanced.body,text="每秒检查几次画面").grid(row=1,column=0,sticky="w",pady=4)
        ttk.Entry(advanced.body,textvariable=self.sample_fps,width=7).grid(row=1,column=1,sticky="w")
        for row,label,var,hint in (
            (2,'中文 TXT 按空行分组',self.groups,'不勾选时每行一条；勾选后用空行分隔，每组可包含多行。'),
            (4,'原文 TXT 按空行分组',self.original_groups,'规则同上，仅对 TXT 生效，ASS、SRT 不受影响。'),
            (6,'原文与中文按顺序一一对应',self.order_confirmed,'仅在逐条对应且顺序一致时勾选；行数相同不代表对应，有拆句、合句或省略重复歌词时不要勾选。')):
            ttk.Checkbutton(advanced.body,text=label,variable=var).grid(row=row,columnspan=2,sticky='w',pady=3)
            ttk.Label(advanced.body,text=hint,wraplength=550,style='Hint.TLabel').grid(row=row+1,columnspan=2,sticky='w',padx=20,pady=(0,6))
        ttk.Checkbutton(detect,style='Switch.TCheckbutton',text='启用文字 AI 辅助核对',variable=self.ai_enabled,command=self.ai_toggle).pack(anchor='w',pady=(10,3))
        ttk.Label(detect,text='辅助检查错字和原文／中文配对。需要 API 密钥，仅发送文字，不发送截图或视频。',wraplength=550,style='Hint.TLabel').pack(anchor='w')
        button(detect,"AI 辅助设置…",self.api_settings,"可选的文字 API，只发送相关文字，不发送截图或视频。主流程默认关闭。").pack(anchor="w",pady=8)
        button(detect,"开始自动识别 / 继续上次进度",self.start_run,"检测原文显示时间并尝试对应中文；不确定的内容会进入“检查”页。",True).pack(anchor="w",pady=12)
        ttk.Label(detect,text="完成后会自动打开“检查疑问”。处理中可以停止，稍后接着做。",
                  wraplength=550,style="Hint.TLabel").pack(anchor="w")
        progress=ttk.LabelFrame(detect,text='识别进度',padding=10);progress.pack(fill='x',pady=14)
        self.bar=ttk.Progressbar(progress,maximum=100)
        self.bar.pack(fill='x',pady=5)
        ttk.Label(progress,textvariable=self.status,wraplength=550).pack(fill='x',pady=5)
        button(progress,'停止处理（保留进度）',self.cancel,'停止后仍可从缓存继续。').pack(anchor='e')
        build_review(self,review,review_actions,tree)
        self.install_live_edits()
        self.build_marker_workspace(marker_host)
        self.workspace_rows.add(self.build_frame_workspace(self.workspace_rows),weight=1)
        ttk.Label(output,text="导出可修改的.ASS字幕",style="Heading.TLabel").pack(anchor="w",pady=(3,10))
        self.export_hint=tk.StringVar(value="请先完成识别与检查。")
        ttk.Label(output,textvariable=self.export_hint,wraplength=540).pack(anchor="w",pady=12)
        ttk.Label(output,text="输出格式：ASS 字幕。只保留你的中文译文，整句显示，保留真实重叠。",
                  wraplength=540,style="Hint.TLabel").pack(anchor="w",pady=10)
        actions=ttk.Frame(output);actions.pack(anchor='w',pady=8)
        for i,(label,command,hint,primary) in enumerate((
            ('字体、字号和位置…',self.style_settings,'预览和导出使用相同的字体及位置。',False),
            ('保存中文字幕…',self.export,'严格校验后保存。',True),
            ('跳过检查，导出草稿…',lambda:self.export(True),'允许未审核；缺少合法时间时可主动选择跳过，并保存遗漏清单。',False),
            ('返回检查疑问',lambda:self.steps.select(2),'返回校对页面。',False))):
            w=button(actions,label,command,hint,primary);w.configure(width=25,padding=(12,10));w.grid(row=i,column=0,sticky='ew',pady=5)
        self.draw()

    def next_issue(self):
        ids=list(self.results.get_children())
        selected=self.results.selection()
        if not ids:
            self.status.set("没有待确认的字幕；还可检查其他问题，然后保存字幕。")
            return
        index=ids.index(selected[0])+1 if selected and selected[0] in ids else 0
        if index>=len(ids):
            self.status.set('已到列表末尾。');return
        self.results.selection_set(ids[index]);self.results.see(ids[index]);self.select_mapping()

    def write_log(self,text):
        self.log.configure(state="normal")
        self.log.insert("end",time.strftime("%H:%M:%S")+" "+text+"\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def guard(self):
        self.finish_frontend_edits()
        if self.busy:
            messagebox.showinfo("正在处理","请先取消或等待任务结束；当前输入和人工结果不会被并发修改。")
            return False
        if hasattr(self,'_pending_chinese') and not self.flush_chinese():
            self.status.set('请先完成中文输入，再执行此操作。')
            return False
        return True

    def pick_input(self,key,pattern):
        if not self.guard():return
        path=filedialog.askopenfilename(filetypes=[("素材",pattern),("所有文件","*.*")])
        if path:
            self.inputs[key].set(path)
            if key=="video":self.load_video()

    def sync(self):
        paths={key:str(Path(var.get()).resolve()) for key,var in self.inputs.items() if var.get().strip()}
        if self.project.mappings and paths!=self.project.inputs:
            from .storage import relocate_inputs
            relocate_inputs(self.project,paths)
        self.project.inputs=paths
        self.project.settings.update(api_enabled=self.ai_enabled.get(),engine=self.engine.get(),language=self.language.get(),
            sample_fps=float(self.sample_fps.get()),txt_groups=self.groups.get(),original_txt_groups=self.original_groups.get(),order_confirmed=self.order_confirmed.get())

    def new(self):
        if not self.guard():return
        if not self.persist_before_replacement():return
        self.pause_playback()
        if self.project.mappings and not messagebox.askyesno("新工程","当前结果已自动保存。新建空白工程？"):return
        self.project=Project()
        self.project.settings.update(ai_config.load(self.home))
        self.project.settings["engine"]="rapidocr"
        self.project_path=None
        self.history=History()
        self.drafts={};self.detail_id=None
        self.frame_index=None
        self.index_generation+=1
        self.steps.select(0)
        for var in self.inputs.values():var.set("")
        self.frame=None
        self.preview_video=None
        self.ai_enabled.set(False)
        self.refresh()
        self.draw()

    def open(self,path=None):
        if not self.guard():return
        if not self.persist_before_replacement():return
        path=path or filedialog.askopenfilename(filetypes=[("LyricSync工程","*.lyricsync.json"),("JSON","*.json")])
        if not path:return
        try:
            self.project_saver.flush()
            try:loaded=load_project(path)
            except (ValueError,TypeError,KeyError,OSError):
                if not Path(str(path)+'.bak').is_file() or not messagebox.askyesno('恢复备份','工程损坏。是否读取最近一次有效备份？',parent=self.root):return
                loaded=load_project(path,recover=True)
            previous_path=self.project_path
            self.project_path=None
            target=self.ensure_project_path()
            try:
                copy_project_cache(path,target,loaded.inputs.values())
                save_project(loaded,target)
            except Exception:
                self.project_path=previous_path
                raise
            self.project=loaded
            self.project.settings.update(ai_config.adopt_project(self.home,loaded.settings))
            self.history=History()
            self.drafts={};self.detail_id=None
            self.ai_enabled.set(self.project.settings.get("api_enabled",False))
            # Open an editable copy. Existing user projects are never overwritten.
            atomic_json(self.home/'last_project.json',{'path':str(target)})
            self.write_log('已从旧任务创建工作副本：'+str(self.project_path))
            for key,var in self.inputs.items():var.set(self.project.inputs.get(key,""))
            self.engine.set(self.project.settings.get("engine","rapidocr"))
            self.language.set(self.project.settings.get("language","jpn+eng"))
            self.sample_fps.set(str(self.project.settings.get("sample_fps",3)))
            self.groups.set(self.project.settings.get("txt_groups",False))
            self.original_groups.set(self.project.settings.get("original_txt_groups",False))
            self.order_confirmed.set(self.project.settings.get("order_confirmed",False))
            self.refresh()
            self.load_video()
            self.steps.select(2 if self.project.mappings else 0)
        except Exception as exc:self.error(exc)

    def ensure_project_path(self):
        if self.project_path is None:
            directory=self.home/"projects"/(time.strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:6])
            directory.mkdir(parents=True,exist_ok=True)
            self.project_path=directory/"project.lyricsync.json"
        return self.project_path

    def save(self):
        if not self.guard():return
        try:
            self.sync()
            self.autosave()
            self.project_saver.flush()
            self.status.set("工程已保存："+str(self.project_path))
        except Exception as exc:self.error(exc)

    def autosave(self):
        timer=getattr(self,'_autosave_timer',None)
        if timer:self.root.after_cancel(timer);self._autosave_timer=None
        try:
            if not self.busy and (self.project.mappings or self.project_path):
                self.save_revision=self.project_saver.submit(self.project,self.ensure_project_path(),self.home/'last_project.json')
                self.save_status.set('正在保存…')
        except OSError as exc:
            self.status.set('自动保存失败，请检查磁盘空间和目录权限；内存中的修改仍保留。')
            self.write_log('自动保存失败：'+type(exc).__name__)
        window=self.windows.get('history')
        if window and window.winfo_exists():self.history_refresh()

    def launch(self,operation,completion):
        if not self.guard():return
        self.autosave()
        try:self.project_saver.flush()
        except OSError as exc:self.error(exc);return
        self.busy=True
        self.cancel_event.clear()
        self.bar["value"]=0
        self.audit_panel.bar["value"]=0
        def worker():
            try:self.jobs.put(("done",completion,operation()))
            except InterruptedError as exc:self.jobs.put(("cancelled",str(exc)))
            except Exception as exc:self.jobs.put(("error",str(exc)))
        self.worker=threading.Thread(target=worker,daemon=False)
        self.worker.start()

    def progress(self,fraction,text):self.jobs.put(("progress",fraction,text))

    def start_run(self):
        if not self.guard():return
        try:
            self.sync()
            for key in ('video','translation'):
                if not Path(self.project.inputs.get(key,"")).is_file():raise ValueError("请选择有效的视频和中文译文。原文歌词文件可选。")
            if self.project.inputs.get('ktv') and not Path(self.project.inputs['ktv']).is_file():raise ValueError('原文文件不存在；可清空后使用视频 OCR 原文。')
            snapshot=Project.from_dict(self.project.to_dict())
            path=self.ensure_project_path()
            self.project_saver.flush()
            save_project(snapshot,path)
            self.history.push(self.project,'重新识别')
            key=KeyStore().get(TEXT_SCOPE) if snapshot.settings.get('api_enabled') else ''
            self.api_key=key
            self.launch(lambda:run(snapshot,path,self.progress,self.cancel_event.is_set,key),self.finished)
        except Exception as exc:self.error(exc)

    def finished(self,project):
        self.project=project
        self.refresh()
        self.steps.select(2)
        self.next_issue()
        self.status.set(f"完成：待审核 {len(project.unresolved())} 组，全局警告 {len(project.warnings)} 项；工程已自动保存。")
        self.write_log(self.status.get()+" "+str(self.project_path))
        self.write_log('OCR 实际后端：'+json.dumps(project.video.get('ocr_runtime',{}),ensure_ascii=False))
        self.write_log('OCR 阶段耗时（秒）：'+json.dumps(project.video.get('ocr_metrics',{}),ensure_ascii=False))
        self.write_log('其他处理耗时（秒）：'+json.dumps(project.settings.get('pipeline_metrics',{}),ensure_ascii=False))
        if not self.close_requested:self.root.after_idle(self.offer_outline_assist)

    def poll(self):
        # Never let a producer's backlog monopolize Tk's event loop. A single
        # expensive handler still needs its own optimization; this only bounds
        # draining and coalesces redundant consecutive progress notifications.
        deadline=time.perf_counter()+.004
        pending_progress=None
        pending_preview=None
        def preview_is_current(item):
            return (item[5]==self.seek_epoch and item[2] is self.preview_video
                    and (item[1]==self.seek_generation or (self.scrubbing and item[1]>self.seek_presented)))
        def flush_preview():
            nonlocal pending_preview
            if pending_preview is None:return
            item=pending_preview;pending_preview=None
            if preview_is_current(item):
                self.seek_presented=item[1]
                self.stamp,self.frame=item[3];self.sync_playhead(item[4]);self.draw()
        def flush_progress():
            nonlocal pending_progress
            if pending_progress is None:return
            item=pending_progress;pending_progress=None
            self.bar["value"]=item[1]*100
            self.audit_panel.bar["value"]=item[1]*100
            self.status.set(item[2])
        try:
            for handled in range(64):
                if handled and time.perf_counter()>=deadline:break
                item=self.jobs.get_nowait()
                if item[0]=="progress":
                    flush_preview()
                    pending_progress=item
                    continue
                if item[0]=='preview_frame':
                    flush_progress()
                    if (preview_is_current(item) and
                            (pending_preview is None or item[1]>=pending_preview[1])):
                        pending_preview=item
                    continue
                flush_progress();flush_preview()
                if item[0]=='saved':
                    if item[1]==self.save_revision:
                        if item[3]:
                            self.save_status.set('保存失败；修改仍在内存中，请重试保存')
                            self.write_log('自动保存失败：'+item[3])
                        else:self.save_status.set('已保存 '+time.strftime('%H:%M:%S'))
                elif item[0]=='frame_index':
                    if item[1]==self.index_generation:
                        self.frame_index=item[2]
                        self.refresh();self.display_frames();self.draw()
                        self.status.set(f'帧索引就绪：共 {len(self.frame_index.stamps)} 帧。')
                elif item[0]=='frame_index_error':
                    if item[1]==self.index_generation:self.status.set('帧索引未完成：'+item[2])
                elif item[0]=='audio_ready':
                    if item[1]==self.audio_generation:
                        self.audio_path=item[2]
                        self.audio_state='声音就绪' if item[2] else '视频无音轨'
                        self.status.set(self.audio_state);self.draw()
                elif item[0]=='audio_error':
                    if item[1]==self.audio_generation:
                        self.audio_state='音频未就绪，可静音预览'
                        self.status.set(item[2]);self.write_log(item[2])
                elif item[0]=='preview_error':
                    if item[1]==self.seek_generation:self.status.set(item[2])
                else:
                    self.busy=False
                    if item[0]=="done":
                        item[1](item[2])
                    elif item[0]=="cancelled":
                        if self.project_path and self.project_path.exists():
                            self.project=load_project(self.project_path)
                            self.refresh()
                        self.status.set(item[1])
                        self.write_log(item[1])
                    elif item[0]=="error":self.error(item[1])
                    if self.close_requested:
                        self.project_saver.close()
                        self.root.destroy()
                        return
            flush_progress();flush_preview()
        except queue.Empty:
            try:flush_progress();flush_preview()
            except Exception as exc:self.error(exc)
        except Exception as exc:self.error(exc)
        self.root.after(8 if not self.jobs.empty() else 16,self.poll)

    def error(self,exc):
        text=str(exc)
        # Defensive redaction before UI/file logs, even for unexpected errors.
        if self.api_key:text=text.replace(self.api_key,"[REDACTED]")
        if getattr(self,"audit_text_key",""):text=text.replace(self.audit_text_key,"[REDACTED]")
        self.status.set(text)
        self.write_log("失败："+text)
        logs=self.home/"logs"
        logs.mkdir(exist_ok=True)
        with (logs/"errors.log").open("a",encoding="utf-8") as stream:stream.write(time.strftime("%Y-%m-%d %H:%M:%S ")+text+"\n")
        messagebox.showerror("未完成",text)

    def cancel(self):
        if self.busy:
            self.cancel_event.set()
            self.status.set("正在取消；当前 OCR/API 调用返回后停止，缓存保留。")

    def close(self):
        self.finish_frontend_edits()
        if not self.busy and not self.flush_chinese():
            self.status.set('请先完成中文输入，再退出。');return
        self.save_layout()
        self.pause_playback()
        self.audio_generation+=1
        self.audio.close()
        self.index_generation+=1
        if self.busy:
            if messagebox.askyesno("退出","取消任务并在保存检查点后退出？"):
                self.close_requested=True
                self.cancel()
            return
        self.autosave()
        try:self.project_saver.close()
        except OSError as exc:self.error(exc);return
        self.root.destroy()

    def refresh(self):
        self._playing_row_ids=set()
        current=self.results.selection()
        picked={view:view.selection() for view in (self.lyrics_tree,self.visuals_tree,self.trans_tree)}
        self.overlapping=overlap_ids(self.project.mappings,self.frame_index)
        for view in (self.results,self.lyrics_tree,self.visuals_tree,self.trans_tree):view.delete(*view.get_children())
        trans={t.id:t for t in self.project.translations}
        unresolved={m.id for m in self.project.unresolved()}
        for number,mapping in enumerate(self.project.mappings,1):
            if self.only_issues.get() and mapping.id not in unresolved:continue
            if self.only_zero.get() and f'{mapping.confidence:.0%}'!='0%':continue
            body=mapping_text(self.project,mapping)
            state="待确认" if mapping.id in unresolved else "已确认" if mapping.confirmed else "自动匹配"
            self.results.insert("","end",iid=mapping.id,tags=("overlap",) if mapping.id in self.overlapping else (),values=(number,body,
                self.frame_label(mapping.start,False),self.frame_label(mapping.end,True),f"{mapping.confidence:.0%}",state))
        for l in self.project.lyrics:self.lyrics_tree.insert("","end",iid=l.id,values=(l.text,f"{l.start:.2f}–{l.end:.2f}" if l.start is not None else "无时间轴"))
        for v in self.project.visuals:self.visuals_tree.insert("","end",iid=v.id,values=(v.text,f"{v.start:.3f}–{v.end:.3f}",f"{v.confidence:.0%}"))
        for t in self.project.translations:self.trans_tree.insert("","end",iid=t.id,values=(t.text,f"{t.start:.2f}–{t.end:.2f}" if t.start is not None else "无"))
        self.count_label.configure(text=f"共 {len(self.project.mappings)} 组字幕 · {len(unresolved)} 组需要确认　请逐项确认，不要完全信任 AI。")
        self.export_hint.set(f"还有 {len(unresolved)} 组字幕需要确认。请回到“检查”页检查。" if unresolved else
            ("字幕已处理，可以保存。若有其他问题尚未确认，保存时会提示。" if self.project.mappings else "请先完成第 1、2 步，识别视频中的歌词。"))
        if current and self.results.exists(current[0]):self.results.selection_set(current[0])
        for view,ids in picked.items():view.selection_set([i for i in ids if view.exists(i)])
        self.refresh_frontend()

    def selected_mapping(self):
        selected=self.results.selection()
        return next((m for m in self.project.mappings if selected and m.id==selected[0]),None)

    def select_mapping(self,_=None,auto=False):
        m=self.selected_mapping()
        if not m:return
        if _ is not None and self.detail_id==m.id:return
        if self.detail_id!=m.id and not self.flush_chinese():
            if self.detail_id and self.results.exists(self.detail_id):self.results.selection_set(self.detail_id)
            self.status.set('请先完成中文输入，再切换字幕。');return
        previous_detail=self.detail_id
        if self.detail_id!=m.id:self.stash_draft()
        self.detail_id=m.id
        for view,ids in ((self.lyrics_tree,m.lyric_ids),(self.visuals_tree,m.visual_ids),(self.trans_tree,m.translation_ids)):
            view.selection_set([i for i in ids if view.exists(i)])
            if ids and view.exists(ids[0]):view.see(ids[0])
        self._loading_edit=True
        try:
            self.start.set(repr(m.start) if m.start is not None else "")
            self.end.set(repr(m.end) if m.end is not None else "")
        finally:self._loading_edit=False
        lookup=lambda units,ids:" / ".join(u.text for u in units if u.id in ids)
        self.show_review_cards(lookup(self.project.lyrics,m.lyric_ids),lookup(self.project.visuals,m.visual_ids),mapping_text(self.project,m))
        self.display_frames()
        self.reference_value.set('点击“翻译选中的原文”获取参考；不修改你的中文。')
        text="需要留意："+("；".join(m.reasons) or "没有已记录的问题；请结合左侧画面判断。")
        text+='\n\n'+describe_suggestions(self.project,m)
        for row in self.project.settings.get('outline_suggestions',[]):
            if row['mapping_id']==m.id:
                text+='\n轮廓辅助：'+row['reason']
                if 'start' in row:text+=f"\n候选帧：{self.frame_label(row['start'],False)}—{self.frame_label(row['end'],True)}"
        self.details.configure(state="normal")
        self.details.delete("1.0","end")
        self.details.insert("1.0",text)
        self.details.configure(state="disabled")
        self.details.schedule_fit()
        self.refresh_frontend(fit=previous_detail!=m.id and not auto)
        if auto:return
        if m.start is not None:
            if _ is not None:self.request_seek(m.start,follow=False)
            else:self.seek(m.start,follow=False)
        elif m.visual_ids:
            candidate=next((v for v in self.project.visuals if v.id in m.visual_ids),None)
            if candidate:self.seek(candidate.start,follow=False)

    def use_visual_times(self):
        if not self.guard():return
        ids=set(self.visuals_tree.selection())
        vs=[v for v in self.project.visuals if v.id in ids]
        if vs:
            m=self.selected_mapping()
            if m:self.editor().set_time(m.id,start=min(v.start for v in vs),end=max(v.end for v in vs))
            self.start.set(repr(min(v.start for v in vs)))
            self.end.set(repr(max(v.end for v in vs)))
            self.display_frames()

    def confirm_mapping(self):
        if not self.guard():return False
        return self.confirm_current()

    def apply_pairing(self):
        if not self.guard():return
        try:
            self.apply_frame_fields()
            tids=list(self.trans_tree.selection())
            if not tids:raise ValueError("请选择至少一条用户译文。")
            raw_start,raw_end=self.start.get().strip(),self.end.get().strip()
            missing=[name for name,value in (("出现时间",raw_start),("消失时间",raw_end)) if not value]
            if missing:
                message="这条字幕还缺少"+"、".join(missing)+"，暂时不能确认。\n\n先核对中文对应的原文，再选择正确的画面片段，点击“使用画面检测时间”；也可以查看视频后手动填写秒数。\n未检测到的时间不会自动猜测，你当前的内容会保留。"
                self.status.set("请补齐"+"、".join(missing)+"后再确认本条。")
                messagebox.showwarning("请先补齐字幕时间",message,parent=self.root)
                return False
            try:start,end=time_seconds(raw_start),time_seconds(raw_end)
            except (ValueError,TypeError,OverflowError):
                messagebox.showwarning("时间格式不正确","请填写秒数，例如 165.20；也支持 0:02:45.20。",parent=self.root)
                return False
            if not valid_time(start,end):
                messagebox.showwarning("请检查字幕时间","出现时间不能为负数，消失时间必须晚于出现时间；两项都必须是有限数字。",parent=self.root)
                return False
            duration=self.project.video.get("duration") or (self.preview_video.info["duration"] if self.preview_video else None)
            if duration and end>duration+.04:
                messagebox.showwarning("时间超出视频","消失时间超过视频总长度，请查看视频后修正。",parent=self.root)
                return False
            m=self.selected_mapping()
            original=set(m.translation_ids) if m else set()
            if m and m.text_segments and set(tids)!=set(m.translation_ids):
                raise ValueError('拆分/合并后的字幕保留原始文字片段；要更换中文，请先撤销该次拆分或合并。')
            for other in self.project.mappings:
                if other is m:continue
                overlap=set(tids)&set(other.translation_ids)
                if overlap and other.locked and (m is None or (m.method!="repeat" and not m.text_segments and not other.text_segments and tids!=m.translation_ids)):raise ValueError("所选译文属于其他锁定项；请先解锁，或使用“增加重复出现”。")
            self.history.push(self.project,"确认字幕")
            if m is None:
                m=Mapping("manual-"+uuid.uuid4().hex,tids)
                self.project.mappings.append(m)
            for other in list(self.project.mappings):
                if other is m:continue
                if m.method=="repeat" or m.text_segments or other.text_segments or set(other.translation_ids)<=original:continue
                other.translation_ids=[i for i in other.translation_ids if i not in tids]
                if not other.translation_ids:self.project.mappings.remove(other)
            m.translation_ids=tids
            m.lyric_ids=list(self.lyrics_tree.selection())
            m.visual_ids=list(self.visuals_tree.selection())
            m.start,m.end=start,end
            m.locked=True;m.confirmed=False;m.human_edited=True
            if m.method not in ("repeat","human_split","human_merge"):m.method="human"
            for tid in original-set(tids):
                if not any(tid in row.translation_ids for row in self.project.mappings):
                    self.project.mappings.append(Mapping("manual-"+uuid.uuid4().hex,[tid],reasons=["拆组后需重新配对"]))
            self.drafts.pop(m.id,None)
            self.autosave()
            self.refresh()
            return True
        except Exception as exc:self.error(exc)

    def unlock(self):
        if not self.guard():return
        m=self.selected_mapping()
        if m:
            self.history.push(self.project,"取消确认")
            m.locked=m.confirmed=False
            self.autosave()
            self.refresh()

    def repeat_mapping(self):
        if not self.guard():return
        m=self.selected_mapping()
        if not m:return
        if not messagebox.askyesno('复用中文译文',f'在另一次歌词出现时复用下面的中文？\n\n{mapping_text(self.project,m)}\n\n新条目需要重新选择原文和起止帧。',parent=self.root):return
        self.history.push(self.project,'复用中文')
        m.human_edited=True
        repeated=copy.deepcopy(m)
        repeated.id='repeat-'+uuid.uuid4().hex
        repeated.start=repeated.end=None;repeated.visual_ids=[];repeated.lyric_ids=[]
        repeated.locked=repeated.confirmed=False;repeated.method='repeat';repeated.human_edited=True
        repeated.reasons=['请选择这次重复歌词的原文和起止帧。']
        self.project.mappings.append(repeated)
        self.autosave();self.refresh();self.results.selection_set(repeated.id);self.select_mapping()

    def rematch(self):
        if not self.guard():return
        self.history.push(self.project,"重新配对")
        self.project.settings["order_confirmed"]=self.order_confirmed.get()
        match_project(self.project)
        self.autosave()
        self.refresh()

    def global_warnings(self):
        if not self.guard():return
        text="\n".join(self.project.warnings) or "没有全局警告。"
        if messagebox.askyesno("全局警告",text+"\n\n确认已检查以上问题？（不会自动确认逐条异常）"):
            self.project.settings["parser_acknowledged"]=True
            self.autosave()

    def export(self,draft=False,skip_missing_times=False):
        if not self.guard():return
        problems=partial_export_problems(self.project) if skip_missing_times else validate_project(self.project,draft=draft)
        if problems:
            missing=missing_time_rows(self.project)
            if missing and not partial_export_problems(self.project) and not skip_missing_times:
                window=self.popup('partial_export','这些字幕缺少合法时间')
                if window is None:return
                ttk.Label(window,text=f'{len(missing)} 条字幕缺少合法时间，可以返回补齐，或主动跳过后导出。',wraplength=620).pack(padx=16,pady=12)
                listing=tk.Text(window,width=62,height=min(12,max(4,len(missing)*2)),wrap='word',font=('Microsoft YaHei UI',11))
                listing.pack(fill='both',expand=True,padx=16,pady=6)
                listing.insert('1.0','\n\n'.join(f"{i+1}. {r['text']}\n{r['reason']}" for i,r in enumerate(missing)));listing.configure(state='disabled')
                ttk.Label(window,text='跳过的字幕仍保留在工程中；导出旁会保存遗漏清单，时间不会自动猜测。',wraplength=620,style='Hint.TLabel').pack(padx=16,pady=8)
                actions=ttk.Frame(window);actions.pack(fill='x',padx=16,pady=12)
                def back():window.destroy();self.steps.select(2)
                def proceed():window.destroy();self.export(True,True)
                button(actions,'返回补齐时间',back,'回到校对页面。').pack(side='left',padx=4)
                button(actions,f'仍要导出（跳过 {len(missing)} 条）',proceed,'导出其他字幕并保存遗漏清单。',True).pack(side='right',padx=4)
                return
            readable='\n'.join(problems[:16])
            for m in self.project.mappings:readable=readable.replace(m.id,'“'+mapping_text(self.project,m)[:60]+'”')
            messagebox.showwarning("仍需检查",readable)
            return
        path=filedialog.asksaveasfilename(defaultextension=".ass",initialfile="MV_中文_未审核草稿.ass" if draft else "MV_中文.ass",filetypes=[("ASS字幕","*.ass")])
        if path:
            try:
                omitted=export_project(self.project,path,overwrite=True,draft=draft,skip_missing_times=skip_missing_times,frame_index=self.frame_index)
                self.status.set("已导出中文普通 ASS："+path+(f'；跳过 {len(omitted)} 条，遗漏清单保存在同目录。' if omitted else ''))
                messagebox.showinfo('导出完成',self.status.get(),parent=self.root)
            except Exception as exc:self.error(exc)

    def load_video(self):
        self.pause_playback()
        try:
            path=self.inputs["video"].get()
            if not path:return
            self.preview_video=Video(path)
            self.prepare_preview_audio(self.preview_video)
            self.index_generation+=1
            self.frame_index=None
            self.refresh_frontend()
            self.build_frame_index(self.preview_video,self.index_generation)
            self.timeline.configure(to=self.preview_video.info["duration"])
            self.seek(min(3,self.preview_video.info["duration"]*.1))
        except Exception as exc:self.error(exc)

    def prepare_preview_audio(self,video):
        self.audio_generation+=1;generation=self.audio_generation
        self.audio.close();self.audio_path=None;self.audio_state='准备中'
        def prepare():
            try:
                path=prepare_audio(video,self.home/'audio',lambda:generation!=self.audio_generation or self.close_requested)
                self.jobs.put(('audio_ready',generation,path))
            except InterruptedError:pass
            except Exception as exc:self.jobs.put(('audio_error',generation,str(exc)))
        threading.Thread(target=prepare,daemon=True).start()

    def seek_changed(self,value):
        if getattr(self,"updating_timeline",False):return
        # Coalesce in the decoder, not by waiting for the user to stop dragging.
        self.request_seek(float(value),intermediate=True)

    def begin_timeline_drag(self,event=None):
        self.pause_playback();self.timeline_dragging=True

    def end_timeline_drag(self,event=None):
        if not getattr(self,'timeline_dragging',False):return
        target=float(self.timeline.get());self.timeline_dragging=False
        # Only the final target may settle the thumb after release.
        self.request_seek(target,intermediate=False)

    def request_seek(self,stamp,follow=True,intermediate=False):
        self.pause_playback();self.seek_pending=None
        if not self.preview_video:return
        self.scrubbing=intermediate
        with self.seek_lock:
            self.seek_generation+=1
            self.seek_request=(self.seek_generation,self.preview_video,stamp,follow,self.seek_epoch,
                               self.frame_index.stamps[-1] if getattr(self,'frame_index',None) else None)
            if self.seek_worker_active:return
            self.seek_worker_active=True
        def worker():
            while True:
                with self.seek_lock:
                    request=self.seek_request;self.seek_request=None
                    if request is None:self.seek_worker_active=False;return
                generation,video,position,follow,epoch,last_pts=request
                def superseded():
                    # During continuous scrubbing, allow one in-flight frame
                    # to finish so fast pointer events cannot starve preview.
                    # Release/explicit seek and context replacement cancel old
                    # work at the next decoder boundary; the final frame stays exact.
                    return (epoch!=self.seek_epoch or video is not self.preview_video
                            or self.close_requested
                            or (generation!=self.seek_generation and not self.scrubbing))
                try:
                    frame=video.at(position,cancel=superseded,last_pts=last_pts)
                    if not superseded():self.jobs.put(('preview_frame',generation,video,frame,follow,epoch))
                except InterruptedError:pass
                except Exception as exc:
                    if not superseded():self.jobs.put(('preview_error',generation,str(exc)))
        threading.Thread(target=worker,daemon=True).start()

    def seek(self,stamp,follow=True):
        self.cancel_pending_seek()
        self.pause_playback()
        if not self.preview_video:return
        self.stamp,self.frame=self.preview_video.at(stamp,
            last_pts=self.frame_index.stamps[-1] if self.frame_index else None)
        self.sync_playhead(follow)
        self.draw()

    def cancel_pending_seek(self):
        self.timeline_dragging=False
        if getattr(self,'seek_pending',None):
            self.root.after_cancel(self.seek_pending);self.seek_pending=None
        with self.seek_lock:
            self.seek_generation+=1;self.seek_request=None;self.seek_epoch+=1;self.scrubbing=False

    def pause_playback(self):
        self.stop_frontend_animations()
        self.playing=False
        self.play_stop_at=None
        if self.audio_playing:
            self.audio.pause();self.audio_playing=False
        if self.play_after:
            self.root.after_cancel(self.play_after);self.play_after=None
        if self.player is not None:
            self.player.stop();self.player=None
        if hasattr(self,"play_button"):self.play_button.configure(text="▶ 播放")

    def play_current_cue(self):
        if not self.guard():return
        row=self.selected_mapping()
        if not row or not valid_time(row.start,row.end):
            self.status.set('请先选择有完整起止时间的一句字幕。');return
        if not self.preview_video or row.end>self.preview_video.info['duration']:
            self.status.set('请先载入对应视频，并检查本句时间。');return
        self.seek(row.start,follow=False)
        self.toggle_play(stop_at=row.end)

    def toggle_play(self,stop_at=None):
        self.finish_frontend_edits()
        if self.playing:
            self.pause_playback();return
        if not self.preview_video:
            self.status.set("请先在“导入”页选择 MV 视频。");return
        if getattr(self,"seek_pending",None):
            self.root.after_cancel(self.seek_pending);self.seek_pending=None
        if stop_at is None and self.stamp>=self.preview_video.info["duration"]-.1:self.seek(0)
        if self.sound_enabled.get() and self.audio_state=='准备中':
            self.status.set('正在准备本地音频缓存，请稍等；也可关闭“声音”立即预览。');return
        self.cancel_pending_seek()
        self.play_stop_at=stop_at
        self.player=PlaybackBuffer(self.preview_video,self.stamp,stop_at)
        self.play_primed=False
        self.audio_playing=False
        self.playing=True
        self.play_button.configure(text="■ 暂停")
        self.play_tick()

    def play_tick(self):
        self.play_after=None
        if not self.playing:return
        try:
            player=self.player
            if player is None:return
            if player.error:raise RuntimeError(player.error)
            stop_at=getattr(self,'play_stop_at',None)
            if not self.play_primed:
                first=player.peek()
                if first is None:
                    if player.finished:self.pause_playback();return
                    self.play_after=self.root.after(8,self.play_tick);return
                # Start audio/clock only when the first decoded frame is ready.
                self.play_clock=time.perf_counter()-first[0]
                if self.sound_enabled.get() and self.audio_path:
                    try:
                        if not self.audio.opened:self.audio.open(self.audio_path)
                        if stop_at is None:self.audio.play(first[0])
                        else:self.audio.play(first[0],stop_at)
                        self.audio_playing=True
                    except Exception as exc:self.audio_state=str(exc);self.status.set(str(exc))
                self.play_primed=True
            if self.audio_playing and not self.audio.is_playing():self.audio_playing=False
            target=self.audio.position() if self.audio_playing else time.perf_counter()-self.play_clock
            frame=player.take_due(target)
            if frame is not None:
                self.stamp,self.frame=frame
                self.sync_playhead(follow=stop_at is None)
                self.draw()
            end=stop_at if stop_at is not None else self.preview_video.info['duration']
            if target>=end:
                self.pause_playback();return
            following=player.peek()
            delay=max(1,min(16,round(1000*(following[0]-target)))) if following is not None else 8
            if stop_at is not None:delay=min(delay,max(1,round((stop_at-target)*1000)))
            self.play_after=self.root.after(delay,self.play_tick)
        except Exception as exc:
            self.pause_playback();self.error(exc)

    def request_step(self,direction):
        """Accumulate exact frame requests even when decoding lags behind clicks."""
        if not self.preview_video or not self.frame_index:
            self.status.set('请先载入视频并等待真实帧索引完成。');return False
        current=(self._step_target if getattr(self,'_step_generation',None)==self.seek_generation
                 else self.frame_index.current(self.stamp))
        target=max(1,min(len(self.frame_index.stamps),current+direction))
        if target==current:return False
        self.request_seek(self.frame_index.stamp(target),intermediate=True)
        self._step_target=target;self._step_generation=self.seek_generation
        return True

    def step(self,direction):
        self.cancel_pending_seek()
        self.pause_playback()
        if not self.preview_video:return
        if self.frame_index:
            current=self.frame_index.current(self.stamp)
            target=max(1,min(len(self.frame_index.stamps),current+direction))
            if target==current:return False
            value=self.preview_video.at(self.frame_index.stamp(target),last_pts=self.frame_index.stamps[-1])
        elif abs(direction)!=1:
            self.status.set('请等待真实帧索引完成，再使用 5 帧跳转。');return False
        elif direction>0:
            value=next(self.preview_video.frames(self.stamp+.00001),None)
        else:
            values=list(self.preview_video.frames(max(0,self.stamp-2),self.stamp-.00001))
            if not values and self.stamp>2:
                values=list(self.preview_video.frames(0,self.stamp-.00001))
            value=values[-1] if values else None
        if value:
            self.stamp,self.frame=value
            self.sync_playhead()
            self.draw()
            return True
        return False

    def schedule_preview_draw(self,event=None):
        # Panedwindow movement emits bursts of Configure (including position-
        # only events). Paint at most once per timer turn using the newest size;
        # never debounce until the user stops moving the divider.
        size=(event.width,event.height) if event is not None else None
        if size is not None and size==getattr(self,'_preview_configure_size',None):return
        self._preview_configure_size=size
        if getattr(self,'_preview_draw_job',None) is not None:return
        def render_latest():
            self._preview_draw_job=None
            self.draw()
        self._preview_draw_job=self.root.after(16,render_latest)

    def cancel_preview_draw(self):
        job=getattr(self,'_preview_draw_job',None)
        if job is not None:
            self._preview_draw_job=None
            self.root.after_cancel(job)

    def draw(self):
        self.cancel_preview_draw()
        self.canvas.delete("all")
        if self.frame is None:
            self._preview_raster_frame=None;self._preview_raster_size=None;self.photo=None
            self.canvas.create_text(max(1,self.canvas.winfo_width())/2,max(1,self.canvas.winfo_height())/2,
                text="先选择你的 MV 视频\n这里会显示画面",fill="#b8c4d4",justify="center",font=("Microsoft YaHei UI",12))
            return
        import cv2
        from PIL import Image,ImageTk
        h,w=self.frame.shape[:2]
        cw,ch=max(1,self.canvas.winfo_width()),max(1,self.canvas.winfo_height())
        scale=min(cw/w,ch/h)
        size=(max(1,int(w*scale)),max(1,int(h*scale)))
        ox,oy=(cw-size[0])/2,(ch-size[1])/2
        self.view_transform=(ox,oy,scale)
        # Scale before colour conversion: the Tk preview only needs canvas pixels.
        if (self.frame is not getattr(self,'_preview_raster_frame',None)
                or size!=getattr(self,'_preview_raster_size',None)):
            preview=cv2.resize(self.frame,size,interpolation=cv2.INTER_LINEAR)
            self.photo=ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(preview,cv2.COLOR_BGR2RGB)),master=self.canvas)
            # Decoder frames are immutable to the UI; overlays never change them.
            self._preview_raster_frame=self.frame
            self._preview_raster_size=size
        self.canvas.create_image(ox,oy,image=self.photo,anchor="nw")
        roi=self.project.settings.get("roi")
        if roi:self.canvas.create_rectangle(ox+roi[0]*scale,oy+roi[1]*scale,ox+roi[2]*scale,oy+roi[3]*scale,outline="#2af2d8",width=2)
        if self.preview_chinese.get():
            text_lookup={t.id:t.text for t in self.project.translations}
            entries=[(m.start,m.end,mapping_text(self.project,m,lookup=text_lookup))
                     for m in self.project.mappings if valid_time(m.start,m.end)]
            try:
                opts=placement_options(self.project)
                if not self.project.video:opts['source_top']=(self.project.settings.get('roi') or [0,h*.9])[1]
                layout_key=(tuple(entries),w,h,self.project.settings.get('font_size'),tuple(opts.items()))
                if layout_key!=getattr(self,'_preview_layout_key',None):
                    self._preview_layout=layout_entries(entries,(w,h),self.project.settings.get('font_size'),**opts)
                    self._preview_layout_key=layout_key
                for row in self._preview_layout:
                    if active_at(row['start'],row['end'],self.stamp):
                        x,y=ox+row['x']*scale,oy+row['y']*scale
                        options=dict(text=row['text'],anchor='n' if row['alignment']==8 else 's',
                            font=(self.project.settings.get('font','Microsoft YaHei'),-max(9,round(row['font_size']*scale))),width=max(100,size[0]*.87))
                        self.canvas.create_text(x+1,y+1,fill='black',**options)
                        self.canvas.create_text(x,y,fill='white',**options)
            except ValueError:
                self.canvas.create_text(cw/2,20,anchor='n',text='所选位置空间不足，请调整字幕位置或字号。',fill='#ffd07a')
        self.preview_label.set(f"{self.frame_counter()} · {self.stamp:.2f} 秒 · {'只识别方框内歌词' if roi else '识别整个画面'} · {self.audio_state if self.sound_enabled.get() else '静音'}\n中文位置是预览示意，保存后以字幕实际显示为准。")

    def frame_pos(self,event):
        ox,oy,scale=self.view_transform
        h,w=self.frame.shape[:2]
        return [max(0,min(w,round((event.x-ox)/scale))),max(0,min(h,round((event.y-oy)/scale)))]

    def roi_down(self,event):
        if not self.busy and self.frame is not None:self.drag=self.frame_pos(event)

    def roi_move(self,event):
        if self.drag and not self.busy:
            x,y=self.frame_pos(event)
            a,b=self.drag
            self.project.settings["roi"]=[min(a,x),min(b,y),max(a,x),max(b,y)]
            self.draw()

    def roi_up(self,event):
        if not self.drag:return
        self.roi_move(event)
        self.drag=None
        roi=self.project.settings["roi"]
        if roi[2]-roi[0]<12 or roi[3]-roi[1]<12:self.project.settings["roi"]=None
        self.autosave()
        self.draw()

    def full_roi(self):
        if not self.guard():return
        self.project.settings["roi"]=None
        self.autosave()
        self.draw()

    def regions(self):
        if not self.guard():return
        try:
            self.sync()
            path=self.inputs["video"].get()
            settings=dict(self.project.settings)
            self.launch(lambda:propose_regions(path,make_ocr(settings),self.cancel_event.is_set),self.show_regions)
        except Exception as exc:self.error(exc)

    def show_regions(self,regions):
        window=self.popup("regions","候选区域 · 选择后可继续拖拽扩大")
        if window is None:return
        view=tree(window,("区域","命中次数","说明"),(250,80,400),height=8,selectmode="browse")
        for i,r in enumerate(regions):view.insert("","end",iid=str(i),values=(str(r["box"]),r["hits"],r["reason"]))
        def use():
            selected=view.selection()
            if selected:
                self.project.settings["roi"]=regions[int(selected[0])]["box"];self.draw()
                self.autosave();window.destroy()
        ttk.Button(window,text="使用选中的区域",command=use).pack(pady=8)

    def api_settings(self):
        if not self.guard():return
        window=self.popup('api','AI 辅助设置')
        if window is None:return
        enabled=self.ai_enabled
        ttk.Checkbutton(window,style='Switch.TCheckbutton',text='启用文字 AI 辅助核对（单句参考翻译不受此开关影响）',variable=enabled,command=self.ai_toggle).pack(anchor='w',padx=12,pady=8)
        key=KeyWidget(window,TEXT_SCOPE,'DeepSeek 文字 API');key.pack(fill='x',padx=12,pady=8)
        model=tk.StringVar(value=self.project.settings.get('api_model','deepseek-flash'))
        maximum=tk.StringVar(value=str(self.project.settings.get('api_max_calls',20)))
        retries=tk.StringVar(value=str(self.project.settings.get('api_retries',1)))
        for label,var in (('模型',model),('本任务最多调用次数（含失败和重试）',maximum),('失败重试次数',retries)):
            row=ttk.Frame(window);row.pack(fill='x',padx=12,pady=5)
            ttk.Label(row,text=label,width=36).pack(side='left');ttk.Entry(row,textvariable=var,width=24).pack(side='left')
        ttk.Label(window,text='密钥保存到本机 Windows 凭据管理器。输入框留空不修改已存密钥。\n仅发送文字，不发送截图或视频。',wraplength=600).pack(padx=12,pady=8)
        connection=tk.StringVar(value='测试只发送固定短文本，计入本任务调用预算。')
        ttk.Label(window,textvariable=connection,wraplength=600).pack(padx=12,pady=4)
        def test():
            if not self.guard():return
            try:
                from .api import test_connection
                from .storage import Cache
                token=key.get();count=int(maximum.get());name=model.get().strip()
                if not token:raise ValueError('请先保存文字 API 密钥。')
                if not 0<=count<=100:raise ValueError('调用上限应在 0～100。')
                self.api_key=token
                path=self.ensure_project_path().with_suffix('.cache.sqlite')
                def work():
                    cache=Cache(path)
                    try:return test_connection(cache,token,name,count)
                    finally:cache.close()
                def done(result):
                    connection.set(result['message'])
                    self.write_log('文字 AI 连接测试：'+('success' if result['ok'] else result['code']))
                connection.set('正在测试文字接口…');self.launch(work,done)
            except Exception as exc:messagebox.showwarning('无法测试',str(exc),parent=window)
        button(window,'测试文字 AI 连接',test,'仅发送固定测试文本；实际调用一次，不发送素材。').pack(pady=4)
        audit=self.audit_panel
        def save():
            try:
                count=int(maximum.get());retry=int(retries.get())
                if not 0<=count<=100 or not 0<=retry<=3:raise ValueError('次数上限0～100，重试0～3。')
                if key.entry.get().strip():key.save()
                if key.entry.get().strip():return
                self.project.settings.update(api_enabled=enabled.get(),api_model=model.get(),api_max_calls=count,api_retries=retry)
                ai_config.save(self.home,self.project.settings)
                audit.save_settings()
                self.autosave();window.destroy()
            except Exception as exc:messagebox.showerror('设置无效',str(exc),parent=window)
        button(window,'保存设置',save,'密钥留空不修改，其他设置会保存。').pack(pady=10)

    def style_settings(self):
        if not self.guard():return
        window=self.popup("subtitle_style","中文字幕字体与位置 · 预览和导出同步")
        if window is None:return
        font=tk.StringVar(value=self.project.settings.get("font","Microsoft YaHei"))
        size=tk.StringVar(value=str(self.project.settings.get("font_size",0)))
        ttk.Label(window,text="字体").grid(row=0,column=0,padx=10,pady=8)
        self.font_combo(window,font).grid(row=0,column=1,padx=10)
        ttk.Label(window,text="字号（0=按视频高度自动）").grid(row=1,column=0,padx=10,pady=8)
        ttk.Entry(window,textvariable=size,width=10).grid(row=1,column=1,padx=10,sticky="w")
        position=tk.StringVar(value=self.project.settings.get('subtitle_position','above_original'))
        percent=tk.StringVar(value=str(self.project.settings.get('subtitle_height_percent',80)))
        ttk.Label(window,text='中文字幕位置').grid(row=2,column=0,padx=10,pady=8)
        choice(window,position,[('above_original','原文上方（推荐）'),('top','画面顶部'),('bottom','画面底部'),('custom','自定义高度')]).grid(row=2,column=1,padx=10)
        ttk.Label(window,text='自定义高度（距顶部百分比）').grid(row=3,column=0,padx=10,pady=8)
        ttk.Spinbox(window,from_=5,to=95,textvariable=percent,width=10).grid(row=3,column=1,padx=10,sticky='w')
        ttk.Label(window,text='视频内原文无法移动；这里只调整中文。自动位置依据检测到的原文区域，背景文字误检时请改为自定义。',wraplength=480).grid(row=4,columnspan=2,padx=10,pady=8)
        def save():
            try:
                value=int(size.get())
                if value!=0 and not 8<=value<=300:raise ValueError("字号0或8～300。")
                if not font.get().strip() or any(c in font.get() for c in ",\r\n"):raise ValueError("字体名称不合法。")
                height=float(percent.get())
                if not 5<=height<=95:raise ValueError('高度应为 5%～95%。')
                self.history.push(self.project,'调整字幕样式')
                self.project.settings.update(font=font.get().strip(),font_size=value,subtitle_position=position.get(),subtitle_height_percent=height)
                self.preview_chinese.set(True)
                self.autosave();self.draw();window.destroy()
            except Exception as exc:messagebox.showerror("设置无效",str(exc),parent=window)
        ttk.Button(window,text="保存并更新预览",command=save).grid(row=5,columnspan=2,pady=10)

    def converter_ui(self):
        window=self.popup('converter','卡拉 OK 字幕转普通字幕')
        if window is None:return
        window.geometry('900x620')
        frame=ttk.Frame(window,padding=16);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text="卡拉 OK 字幕转普通字幕",style="Heading.TLabel").pack(anchor="w",pady=12)
        ttk.Label(frame,text="去掉逐字变色、跳动等特效，让整句歌词正常显示。保留原语言和字幕文件中的时间，不需要视频，也不调用 API。\n原本同时显示的歌词会保留。此功能不会根据视频校准时间；不确定的内容会列入报告。",wraplength=850).pack(anchor="w",pady=10)
        self.convert_no_overlap=tk.BooleanVar()
        ttk.Checkbutton(frame,text="一次只显示一句（会截短重叠歌词，默认关闭）",variable=self.convert_no_overlap).pack(anchor="w",pady=10)
        self.convert_report=tk.Text(frame,height=22,wrap="word")
        self.convert_report.pack(fill="both",expand=True,pady=8)
        ttk.Button(frame,text="选择 KTV ASS 并转换…",command=self.convert).pack(anchor="w",pady=10)

    def convert(self):
        if not self.guard():return
        source=filedialog.askopenfilename(filetypes=[("KTV ASS","*.ass *.ssa")])
        if not source:return
        target=filedialog.asksaveasfilename(defaultextension=".ass",initialfile=Path(source).stem+"_普通.ass",filetypes=[("普通ASS","*.ass")])
        if not target:return
        try:
            units,warnings=convert_ktv(source,target,self.convert_no_overlap.get(),overwrite=True)
            text=f"输出 {len(units)} 条：{target}\n"+"\n".join(warnings)
            text+="\n"+"\n".join(f"{u.start:.2f}–{u.end:.2f} {u.text}\n  {'；'.join(u.warnings)}" for u in units)
            self.convert_report.delete("1.0","end");self.convert_report.insert("1.0",text)
            atomic_json(Path(target).with_suffix(".conversion.json"),{"units":[u.__dict__ for u in units],"warnings":warnings},(source,))
            self.status.set("本地转换完成；请查看转换报告。")
        except Exception as exc:self.error(exc)

    def audit_ui(self):
        from .audit_gui import AuditPanel
        self.audit_panel=AuditPanel(self)


def main():
    root=tk.Tk()
    Application(root)
    root.mainloop()
