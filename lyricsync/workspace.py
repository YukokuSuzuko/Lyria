"""Workspace navigation, presentation and reversible edit affordances."""
import json
import tkinter as tk
from tkinter import ttk, font as tkfont
from .dialogs import messagebox,colorchooser,center_window
from .appearance import DEFAULT_ACCENT,MOONLIT_ACCENT,valid_color
from .model import valid_time,active_at,TIME_EPSILON
from .editing import mapping_text
from .storage import atomic_json
from . import __version__
from .widgets import button


def overlap_ids(mappings,index=None):
    intervals=[]
    for m in mappings:
        if not valid_time(m.start,m.end):continue
        a,b=(index.first(m.start),index.last(m.end)) if index else (m.start,m.end-TIME_EPSILON)
        if b>=a:intervals.append((a,b,m.id))
    active=[];overlaps=set()
    for a,b,identity in sorted(intervals):
        active=[row for row in active if row[0]>=a]
        if active:overlaps.add(identity);overlaps.update(i for _,i in active)
        active.append((b,identity))
    return overlaps


def describe_suggestions(project,mapping):
    lines=[]
    lyrics={l.id:l.text for l in project.lyrics};translations={t.id:t.text for t in project.translations}
    for record in project.settings.get('api_suggestions',[]):
        if record.get('mapping_id')!=mapping.id:continue
        if record.get('context_signature'):
            from .api import review_signature
            if record['context_signature']!=review_signature(project,mapping):
                lines.append('文字或配对已修改：这条 AI 建议已过期，请重新核对。');continue
        if record.get('error'):lines.append('AI 核对未完成：'+record['error']);continue
        data=record.get('suggestion',{})
        for row in data.get('corrections',[]):
            lines.append('疑似识别错误：'+lyrics.get(row['lyric_id'],'未知原文')+'\n建议文字：'+row['suggestion']+'\n理由：'+row['reason'])
        for row in data.get('mappings',[]):
            lines.append('建议原文：'+' / '.join(lyrics.get(i,'未知原文') for i in row['lyric_ids'])+
                '\n对应中文：'+' / '.join(row.get('current_chinese') or [translations.get(i,'未知译文') for i in row['translation_ids']])+
                f"\n参考分：{row['confidence']:.0%} · 理由："+row['reason'])
        if not data.get('mappings') and not data.get('corrections'):lines.append('AI 未能给出可靠建议。')
    return '\n\n'.join(lines) or '暂无 AI 核对建议。可在工具菜单运行全文 AI 核对。'


class WorkspaceActions:
    def popup(self,key,title):
        current=self.windows.get(key)
        if current and current.winfo_exists():
            current.deiconify();current.lift();current.focus_set();return None
        window=tk.Toplevel(self.root);window.withdraw();window.title(title);window.transient(self.root)
        self.windows[key]=window
        self.root.after_idle(lambda:center_window(window,self.root))
        return window

    def build_menu(self, inline_parent=None):
        # The same real Tk menus/commands serve both the native fallback and
        # the handoff's inline menu strip. Native caption controls are untouched.
        groups={};self.menu_buttons=[]
        bar=None
        if inline_parent is None:
            bar=tk.Menu(self.root,tearoff=False);self.root.configure(menu=bar)
        else:
            self.root.configure(menu='')
        for name in ('文件','编辑','工具','设置','帮助'):
            if inline_parent is None:
                menu=tk.Menu(bar,tearoff=False);bar.add_cascade(label=name,menu=menu)
            else:
                control=ttk.Menubutton(inline_parent,text=name,style='Menu.TMenubutton',takefocus=True,direction='below')
                menu=tk.Menu(control,tearoff=False)
                control.configure(menu=menu);control.pack(side='left',padx=1)
                self.menu_buttons.append(control)
            groups[name]=menu
        self.menu_bar=bar;self.menu_groups=groups
        for label,callback in (('新建任务',self.new),('打开任务…',self.open),('保存进度',self.save),
                               ('导出中文字幕…',self.export),('退出',self.close)):
            groups['文件'].add_command(label=label,command=callback)
        groups['编辑'].add_command(label='撤销',accelerator='Ctrl+Z',command=self.undo)
        groups['编辑'].add_command(label='重做',accelerator='Ctrl+Shift+Z',command=lambda:self.undo(True))
        groups['编辑'].add_command(label='操作历史…',command=self.show_history)
        groups['编辑'].add_separator()
        for title,callback in (('在本条后新增空白字幕',self.add_blank_subtitle),('取消本条确认',self.cancel_confirmation),
                               ('使用前后句空档',self.fill_current_gap),('跳到本句开始',self.jump_to_current_start),
                               ('在当前帧添加标记点',self.add_current_marker)):
            groups['编辑'].add_command(label=title,command=callback)
        groups['文件'].add_command(label='恢复上次自动保存的任务',command=self.restore_last_task)
        groups['工具'].add_command(label='重新识别本句 OCR…',command=self.rescan_current_cue)
        groups['工具'].add_command(label='播放本句',command=self.play_current_cue)
        groups['工具'].add_command(label='全文 AI 配对核对',command=self.run_text_review)
        groups['工具'].add_command(label='卡拉 OK 字幕转普通字幕…',command=self.converter_ui)
        groups['工具'].add_command(label='运行记录…',command=self.show_log)
        groups['设置'].add_command(label='AI 辅助设置…',command=self.api_settings)
        groups['设置'].add_command(label='OCR 计算设置…',command=self.ocr_settings)
        groups['设置'].add_command(label='外观与动效…',command=self.appearance_settings)
        groups['设置'].add_command(label='恢复默认布局',command=self.reset_layout)
        groups['帮助'].add_command(label='使用说明',command=self.show_help)
        groups['帮助'].add_command(label='关于 Lyria',command=lambda:messagebox.showinfo(
            '关于',f'Lyria {__version__}\n本地歌词打轴、画面字幕识别与校对工具',parent=self.root))
        self.root.bind('<Control-z>',lambda _:self.undo())
        self.root.bind('<Control-y>',lambda _:self.undo(True))
        self.root.bind('<Control-Shift-Z>',lambda _:self.undo(True))
        self.root.bind('<m>',self.add_current_marker)
        self.root.bind('<M>',self.add_current_marker)
        self.root.bind('<Control-s>',lambda _:self.save())
        if self.menu_buttons:
            def focus_menu(index, post=False):
                control=self.menu_buttons[index % len(self.menu_buttons)]
                control.focus_set()
                if post:control.event_generate('<KeyPress-Down>')
                return 'break'
            for index,control in enumerate(self.menu_buttons):
                control.bind('<Left>',lambda _,i=index:focus_menu(i-1))
                control.bind('<Right>',lambda _,i=index:focus_menu(i+1))
                control.bind('<Escape>',lambda _:(self.root.focus_set(),'break')[1])
            self.root.bind('<F10>',lambda _:focus_menu(0))
            for index,key in enumerate(('f','e','t','s','h')):
                self.root.bind(f'<Alt-{key}>',lambda _,i=index:focus_menu(i,True))

    def show_log(self):
        center_window(self.log_window,self.root);self.log_window.focus_set()

    def ocr_settings(self):
        if not self.guard():return
        window=self.popup('ocr_compute','OCR 计算设置')
        if window is None:return
        import onnxruntime
        from .widgets import choice
        available=onnxruntime.get_available_providers()
        options=[('cpu','CPU（默认）')]
        for name,provider,label in (('directml','DmlExecutionProvider','GPU · DirectML'),('cuda','CUDAExecutionProvider','GPU · CUDA')):
            if provider in available:options.append((name,label))
        device=tk.StringVar(value=self.project.settings.get('ocr_device','cpu'))
        threads=tk.StringVar(value=str(self.project.settings.get('ocr_threads',2)))
        ttk.Label(window,text='RapidOCR 计算设备').grid(row=0,column=0,padx=12,pady=8)
        choice(window,device,options).grid(row=0,column=1,padx=12,pady=8)
        ttk.Label(window,text='CPU 推理线程数（1～32）').grid(row=1,column=0,padx=12,pady=8)
        ttk.Spinbox(window,from_=1,to=32,textvariable=threads,width=8).grid(row=1,column=1,padx=12,pady=8)
        ttk.Label(window,text='GPU 不一定更快，尤其是小字幕区域。Tesseract 仍使用 CPU。\n当前运行时：'+onnxruntime.__version__+'\n可用后端：'+', '.join(available),wraplength=520).grid(row=2,columnspan=2,padx=12,pady=8)
        def save():
            try:
                count=int(threads.get())
                if not 1<=count<=32:raise ValueError('线程数应在 1～32。')
                if device.get() not in {value for value,_ in options}:raise ValueError('请选择当前可用设备。')
                self.project.settings.update(ocr_device=device.get(),ocr_threads=count)
                self.autosave();window.destroy()
            except ValueError as exc:messagebox.showwarning('计算设置无效',str(exc),parent=window)
        button(window,'保存计算设置',save,'下一次 OCR 生效；不会修改现有字幕。').grid(row=3,columnspan=2,pady=12)

    def show_help(self):
        window=self.popup('help','使用说明')
        if window is None:return
        body=tk.Text(window,width=68,height=22,wrap='word',font=('Microsoft YaHei UI',11),padx=14,pady=14)
        body.pack(fill='both',expand=True)
        body.insert('1.0',('1. 导入视频、演唱原文（可选）和中文译文。\n2. 在画面中框选字幕区域并开始识别。\n3. 拖动进度条可同步定位右侧歌词，点击歌词行可跳至该句开始帧（时间重叠行将以红色提示）。\n4. 对照参考原文与画面 OCR 修改译文，点击“确认并下一句”标记完成。\n5. 开始帧与结束帧支持逐帧微调（结束帧为字幕显示的最后一帧）。\n6. 修改会自动备份；核对完成后导出 ASS 字幕（也可先导出初稿，或跳过缺时间条目并生成遗漏清单）。\n\n提示：\n· 拖动分隔线可调整面板宽度，布局会自动保存。\n· “编辑”菜单支持撤销 / 重做最近 60 步操作。\n· AI 辅助仅发送文本与时间信息，不会上传视频或截图。'








                                                                                                     ))
        body.configure(state='disabled')

    def appearance_settings(self):
        window=self.popup('appearance','外观与动效')
        if window is None:return
        color=tk.StringVar(value=self.ui_settings.get('accent',MOONLIT_ACCENT))
        animate=tk.BooleanVar(value=self.ui_settings.get('animations',True))
        ttk.Label(window,text='界面强调色（默认 #8764C6）').grid(row=0,column=0,padx=12,pady=12)
        ttk.Entry(window,textvariable=color,width=14).grid(row=0,column=1,padx=8)
        def choose():
            result=colorchooser.askcolor(color.get() if valid_color(color.get()) else MOONLIT_ACCENT,parent=window)
            if result and result[1]:color.set(result[1])
        button(window,'选择颜色…',choose,'选择界面的强调色。').grid(row=0,column=2,padx=10)
        ttk.Checkbutton(window,text='启用界面平滑过渡动效',variable=animate).grid(row=1,columnspan=3,padx=12,pady=10,sticky='w')
        button(window,'恢复默认颜色',lambda:color.set(MOONLIT_ACCENT),'恢复默认紫丁香强调色。').grid(row=2,column=0,padx=12,pady=12)
        def save():
            try:
                self.appearance.apply(color.get())
                self.ui_settings.update(accent=color.get(),animations=animate.get());self.stop_frontend_animations();self.mode_switch.render();self.refresh_frontend();self.save_ui();window.destroy()
            except ValueError as exc:messagebox.showwarning('颜色格式无效',str(exc),parent=window)
        button(window,'保存外观',save,'立即应用并保存到本机。',True).grid(row=2,column=2,padx=12,pady=12)

    def register_pane(self,name,pane,defaults):
        self.panes[name]=(pane,defaults)
        pane.bind('<ButtonRelease-1>',lambda _:self.save_layout(),add='+')

    def restore_layout(self):
        for name,(pane,defaults) in self.panes.items():
            size=pane.winfo_width() if str(pane['orient'])=='horizontal' else pane.winfo_height()
            if size<80:continue
            values=self.ui_settings.get('layout',{}).get(name,defaults)
            for i,ratio in enumerate(values):
                if i>=len(pane.panes())-1:break
                pane.sashpos(i,round(size*max(.10,min(.90,float(ratio)))))

    def save_layout(self):
        layout={}
        for name,(pane,_) in self.panes.items():
            size=pane.winfo_width() if str(pane['orient'])=='horizontal' else pane.winfo_height()
            if size>80:layout[name]=[pane.sashpos(i)/size for i in range(len(pane.panes())-1)]
        self.ui_settings.setdefault('layout',{}).update(layout);self.save_ui()

    def save_ui(self):atomic_json(self.home/'interface.json',self.ui_settings)

    def reset_layout(self):
        self.ui_settings['layout']={};self.restore_layout();self.save_ui()
        self.status.set('已恢复默认分区大小。')

    def animate_page(self,event=None):
        # Page-padding animation caused repeated whole-workspace relayouts in
        # v0.10. Animate only bounded canvas selection indicators instead.
        widget=event.widget if event else self.notebook
        control=self.step_tabs if widget is self.steps else self.mode_switch
        busy_motion=(self.playing or self.scrubbing or
            getattr(self,'timeline_dragging',False) or
            getattr(getattr(self,'frame_timeline',None),'dragging',False))
        if self.ui_settings.get('animations',True) and not busy_motion:
            control.transition()
        else:
            control.cancel_transition()

    def show_history(self):
        window=self.popup('history','操作历史 · 本次会话最近 60 次修改')
        if window is None:return
        listing=tk.Listbox(window,width=55,height=16,font=('Microsoft YaHei UI',11),activestyle='none')
        listing.pack(fill='both',expand=True,padx=12,pady=12)
        def refresh():
            listing.delete(0,'end')
            for label,_ in self.history.undo_stack:listing.insert('end','已应用  ·  '+label)
            listing.insert('end','──────── 当前状态 ────────')
            listing.itemconfigure('end',foreground='#007eae')
            for label,_ in reversed(self.history.redo_stack):listing.insert('end','可重做  ·  '+label)
            listing.see(max(0,len(self.history.undo_stack)-1))
        def restore(redo):self.undo(redo);refresh()
        row=ttk.Frame(window);row.pack(fill='x',padx=12,pady=8)
        button(row,'撤销上一步',lambda:restore(False),'退回最近一次修改。').pack(side='left',padx=4)
        button(row,'重做下一步',lambda:restore(True),'恢复刚撤销的修改。').pack(side='left',padx=4)
        self.history_refresh=refresh
        refresh()

    def previous_mapping(self):
        ids=list(self.results.get_children());current=self.results.selection()
        if not ids:return
        at=ids.index(current[0]) if current and current[0] in ids else len(ids)
        self.results.selection_set(ids[max(0,at-1)]);self.results.see(ids[max(0,at-1)]);self.select_mapping()

    def stash_draft(self):
        return  # Valid edits already belong to the project.

    def restore_draft(self,identity):
        return

    def sync_playhead(self,follow=True):
        self.update_frontend_playhead()
        # During a drag the pointer owns the thumb, not older decoded frames.
        if not getattr(self,'timeline_dragging',False):
            self.updating_timeline=True
            try:self.timeline.set(self.stamp)
            finally:self.updating_timeline=False
        active=[m.id for m in self.project.mappings if valid_time(m.start,m.end) and active_at(m.start,m.end,self.stamp)]
        active_set=set(active)
        previous=getattr(self,'_playing_row_ids',set())
        for identity in previous ^ active_set:
            if not self.results.exists(identity):continue
            tags=[]
            if identity in getattr(self,'overlapping',set()):tags.append('overlap')
            if identity in active_set:tags.append('playing')
            self.results.item(identity,tags=tuple(tags))
        self._playing_row_ids=active_set
        self.current_hint.set('当前没有字幕' if not active else f'当前画面：{len(active)} 条字幕同时显示' if len(active)>1 else '右侧已定位到当前画面歌词')
        if not follow or getattr(getattr(self,'frame_timeline',None),'dragging',False):return
        visible=[i for i in active if self.results.exists(i)]
        if not visible:
            if active:self.current_hint.set('当前句已被“只看待确认”筛选隐藏')
            return
        current=getattr(self,'detail_id',None)
        target=current if current in visible else visible[0]
        if self.results.selection()!=(target,):
            self.results.selection_set(target);self.results.see(target)
        if target!=current:self.select_mapping(auto=True)

    def nudge_boundary(self,end,delta,quiet=False):
        if not self.guard():return
        if not self.selected_mapping() or not self.frame_index:
            self.status.set('请先选择字幕并等待帧索引完成。');return
        variable=self.last_frame if end else self.first_frame
        try:
            value=int(variable.get())+delta
            first=int(self.first_frame.get()) if self.first_frame.get() else 1
            last=int(self.last_frame.get()) if self.last_frame.get() else len(self.frame_index.stamps)
            if end:last=value
            else:first=value
            self.frame_index.interval(first,last)
            variable.set(str(value))
            self.request_seek(self.frame_index.stamp(value),follow=False,intermediate=True)
            self.status.set(('结束' if end else '开始')+f'帧已调整至第 {value} 帧。')
            return True
        except (ValueError,TypeError):
            if not quiet:messagebox.showwarning('无法调整这一帧','请先补齐合法帧号。开始不能晚于结束，也不能超出视频范围。',parent=self.root)
            return False

    def ai_toggle(self):
        self.project.settings['api_enabled']=self.ai_enabled.get();self.autosave()

    def font_combo(self,parent,variable):
        fonts=sorted(set(tkfont.families(self.root)),key=str.casefold)
        if variable.get() not in fonts:fonts.insert(0,variable.get())
        combo=ttk.Combobox(parent,textvariable=variable,values=fonts,width=32)
        combo.bind('<KeyRelease>',lambda e:combo.configure(values=[f for f in fonts if variable.get().casefold() in f.casefold()] or fonts)
                   if e.keysym not in ('Up','Down','Return','Escape') else None)
        return combo

    def show_mapping_editor(self):
        if not self.mapping_editor.opened:self.mapping_editor.flip()
        center_window(self.mapping_window,self.root);self.mapping_window.focus_set()

    def offer_outline_assist(self):
        from .outline_assist import unreliable_ids,propose
        from .storage import Cache
        ids=unreliable_ids(self.project)
        if not ids:return
        window=self.popup('outline','部分歌词没有可靠识别')
        if window is None:return
        ttk.Label(window,text=f'有 {len(ids)} 句字幕尚未检出清晰时间或需复核进出点。',style='Heading.TLabel').pack(padx=16,pady=12)
        ttk.Label(window,text='可尝试根据相邻文字轮廓辅助推算起止时间。\n检测在本地完成，不会覆盖现有时间轴，生成后可在“时间工具”中选用。',wraplength=550).pack(padx=16,pady=8)
        def start():
            if not self.guard():return
            from .model import Project
            snapshot=Project.from_dict(self.project.to_dict());path=self.ensure_project_path().with_suffix('.cache.sqlite')
            def work():
                cache=Cache(path)
                try:return propose(snapshot,ids,cache,self.progress,self.cancel_event.is_set)
                finally:cache.close()
            def done(rows):
                self.project.settings['outline_suggestions']=rows;self.autosave()
                self.select_mapping(auto=True)
                self.status.set(f"轮廓辅助检测完成：已为 {sum(('start' in r for r in rows))} 句生成参考时间，其余请手动打轴。")
            window.destroy();self.steps.select(1);self.launch(work,lambda rows:(done(rows),self.steps.select(2)))
        row=ttk.Frame(window);row.pack(pady=12,padx=16,fill='x')
        button(row,'尝试辅助检测',start,'仅生成待确认候选时间。',True).pack(side='left',padx=5)
        button(row,'留待人工检查',window.destroy,'暂不检测，保留当前状态。').pack(side='left',padx=5)

    def apply_outline_time(self):
        m=self.selected_mapping()
        if not m:return
        candidate=next((r for r in self.project.settings.get('outline_suggestions',[]) if r['mapping_id']==m.id and 'start' in r),None)
        if not candidate:
            self.status.set('本句暂无轮廓辅助时间，请先运行辅助检测或手动定位。');return
        self.editor().set_time(m.id,start=candidate['start'],end=candidate['end'])
        self.start.set(repr(candidate['start']));self.end.set(repr(candidate['end']));self.display_frames()
        self.seek(candidate['start'],follow=False)
        self.status.set('已填入轮廓辅助时间，请结合画面核对后确认。')
