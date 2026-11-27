"""Existing Tk adapter for the shared editing commands; no HTML dependencies."""
from .editing import Editor, mapping_text
from .dialogs import messagebox
from .ime import composition_active


class LiveEdits:
    def editor(self):
        return Editor(self.project,self.history,self.edit_changed)

    def install_live_edits(self):
        self._loading_edit=False
        self._text_loading=False
        self._pending_chinese=None
        self._chinese_timer=None
        self.first_frame.trace_add('write',lambda *_:self.commit_frame_field(False))
        self.last_frame.trace_add('write',lambda *_:self.commit_frame_field(True))
        self.start.trace_add('write',lambda *_:self.commit_seconds(False))
        self.end.trace_add('write',lambda *_:self.commit_seconds(True))
        box=self.review_cards['chinese']
        box.bind('<<Modified>>',self.commit_chinese)
        box.edit_modified(False)

    def edit_changed(self):
        self.drafts.clear()
        self.refresh();self.draw();self.schedule_autosave()

    def schedule_autosave(self):
        timer=getattr(self,'_autosave_timer',None)
        if timer:self.root.after_cancel(timer)
        self._autosave_timer=self.root.after(300,self.autosave)

    def commit_frame_field(self,end):
        if self._loading_edit or not self.frame_index or self.busy:return
        row=self.selected_mapping()
        if not row:return
        raw=(self.last_frame if end else self.first_frame).get().strip()
        try:
            frame=int(raw)
            first,last=self.frame_index.interval(frame,frame)
            self.editor().set_time(row.id,**{'end' if end else 'start':last if end else first})
        except (ValueError,TypeError):
            self.status.set('帧号超出范围或格式有误，已保留原时间。');return
        (self.end if end else self.start).set(repr(row.end if end else row.start))
        self._frame_display=(self.first_frame.get(),self.last_frame.get())
        self.status.set('时间已更新。')

    def commit_chinese(self,_=None):
        box=self.review_cards['chinese']
        if not box.edit_modified():return
        box.edit_modified(False)
        if self._text_loading or self.busy:return
        self._pending_chinese=self.detail_id
        self.flush_chinese()

    def flush_chinese(self):
        """A composition is draft text, not a completed user edit."""
        box=self.review_cards['chinese']
        if composition_active(box):
            if self._pending_chinese and self._chinese_timer is None:
                def retry():
                    self._chinese_timer=None
                    self.flush_chinese()
                self._chinese_timer=self.root.after(40,retry)
            return False
        identity=self._pending_chinese
        if identity and identity==self.detail_id:
            self._pending_chinese=None
            if any(m.id==identity for m in self.project.mappings):
                self.editor().set_text(identity,box.get('1.0','end-1c'))
        return True

    def commit_seconds(self,end):
        if self._loading_edit or self.busy:return
        row=self.selected_mapping()
        if not row:return
        try:
            value=float((self.end if end else self.start).get())
            duration=self.project.video.get('duration') or (self.preview_video.info['duration'] if self.preview_video else None)
            if duration and value>duration:raise ValueError('时间超出视频总长度')
            changed=self.editor().set_time(row.id,**{'end' if end else 'start':value})
            if changed:self.display_frames()
        except (ValueError,TypeError):self.status.set('时间格式有误或超出范围，已保留原时间。')

    def confirm_current(self):
        row=self.selected_mapping()
        if not row:return False
        try:
            if not self.start.get().strip() or not self.end.get().strip():raise ValueError('请先填写出现时间和消失时间。')
            if (float(self.start.get()),float(self.end.get()))!=(row.start,row.end):raise ValueError('输入的时间格式有误，请修正后再确认。')
            self.editor().confirm(row.id)
        except ValueError as exc:
            messagebox.showwarning('请先补齐字幕',str(exc),parent=self.root);return False
        return True

    def jump_to_current_start(self):
        row=self.selected_mapping()
        if not row or row.start is None:
            self.status.set('请选中字幕并补齐开始时间。');return
        self.seek(row.start,follow=False)

    def add_blank_subtitle(self):
        if not self.guard():return
        row=self.selected_mapping()
        added=self.editor().insert_blank(row.id if row else None)
        self.results.selection_set(added.id);self.select_mapping(auto=True)

    def fill_current_gap(self):
        if not self.guard():return
        row=self.selected_mapping()
        if not row:return
        try:self.editor().fill_gap(row.id);self.select_mapping(auto=True)
        except ValueError as exc:messagebox.showwarning('没有可用空档',str(exc),parent=self.root)

    def restore_last_task(self):
        import json
        try:
            record=json.loads((self.home/'last_project.json').read_text(encoding='utf8'))
            self.open(record['path'])
        except (OSError,ValueError,KeyError):self.status.set('没有可恢复的上次任务。')

    def offer_recovery(self):
        if self.busy or self.project.mappings:return
        if messagebox.askyesno('恢复上次任务','发现上次自动保存的任务。是否继续？将创建工作副本，保留原工程。',parent=self.root):self.restore_last_task()

    def cancel_confirmation(self):
        if not self.guard():return
        row=self.selected_mapping()
        if row:self.editor().confirm(row.id,False)

    def add_current_marker(self,event=None):
        if event and event.widget.winfo_class() in ('Entry','TEntry','Text','TCombobox','TSpinbox'):return
        if hasattr(self,'marker_track'):
            return self.marker_track.add_current_marker(event)
        if not self.guard() or not self.frame_index:return
        frame=self.frame_index.current(self.stamp)
        self.editor().add_marker(frame,index=self.frame_index);self.status.set(f'已添加标记点：第 {frame} 帧。')
        return 'break'

    def run_text_review(self):
        if not self.guard() or not self.project.mappings:return
        from .api import suggest
        from .model import Project
        from .storage import Cache,save_project
        from .credentials import KeyStore,TEXT_SCOPE
        from . import ai_config
        key=KeyStore().get(TEXT_SCOPE)
        if not key:self.api_settings();return
        snapshot=Project.from_dict(self.project.to_dict());snapshot.settings.update(ai_config.load(self.home))
        snapshot.settings['api_enabled']=True
        path=self.ensure_project_path().with_suffix('.cache.sqlite')
        def work():
            cache=Cache(path)
            try:suggest(snapshot,cache,key,self.cancel_event.is_set,checkpoint=lambda:save_project(snapshot,self.project_path));return snapshot
            finally:
                snapshot.api_records=cache.records();cache.close()
                save_project(snapshot,self.project_path)
        def done(result):
            self.project=result;self.ai_enabled.set(True);self.autosave();self.select_mapping(auto=True)
            coverage=result.settings.get('api_coverage',{})
            self.status.set(f"AI 核对完成：已处理 {coverage.get('processed', 0)} 句，失败 {coverage.get('failed', 0)} 句，待处理 {coverage.get('pending', 0)} 句。")
        self.launch(work,done)

    def rescan_current_cue(self,window=None,identity=None):
        if not self.guard():return
        row=next((m for m in self.project.mappings if m.id==identity),None) if identity else self.selected_mapping()
        if not row:self.status.set('请先选中一句字幕。');return
        from .model import Project
        from .rescan import rescan_cue,adopt_rescan,suggested_window
        from .storage import Cache
        from .gui import tree
        from tkinter import ttk
        import tkinter as tk
        if not self.project.inputs.get('video'):
            messagebox.showwarning('请先选择视频','请先导入视频后再重新识别。',parent=self.root);return
        duration=self.project.video.get('duration') or (self.preview_video.info['duration'] if self.preview_video else 0)
        if not duration:
            messagebox.showwarning('视频尚未就绪','请先载入视频，读取时长后再重新识别。',parent=self.root);return
        if window is None:
            dialog=self.popup('rescan_range','本句重新 OCR · 选择搜索范围')
            if dialog is None:return
            index=self.frame_index
            hint=suggested_window(self.project,row.id,duration)
            if hint is None:
                left=max(0,min(self.stamp,duration)-5)
                hint=(left,min(duration,left+10))
            a,b=(index.first(hint[0]),index.last(hint[1])) if index else hint
            first=tk.StringVar(value=str(a));last=tk.StringVar(value=str(b))
            ttk.Label(dialog,text='在指定时间范围内重新识别本句字幕（使用当前框选区域），识别后可选择是否采用。',wraplength=500).grid(row=0,column=0,columnspan=2,padx=12,pady=10)
            for n,(name,var) in enumerate((('开始帧' if index else '开始秒',first),('结束帧（含）' if index else '结束秒（不含）',last)),1):
                ttk.Label(dialog,text=name).grid(row=n,column=0,padx=12,pady=5)
                ttk.Entry(dialog,textvariable=var,width=20).grid(row=n,column=1,padx=12,pady=5)
            def begin():
                try:
                    bounds=index.interval(int(first.get()),int(last.get())) if index else (float(first.get()),float(last.get()))
                    if not 0<=bounds[0]<bounds[1]<=duration:raise ValueError('搜索范围应在视频内，开始须早于结束。')
                except ValueError as exc:
                    messagebox.showwarning('搜索范围无效',str(exc),parent=dialog);return
                dialog.destroy();self.rescan_current_cue(bounds,row.id)
            ttk.Button(dialog,text='开始本句识别',command=begin).grid(row=3,columnspan=2,padx=12,pady=12)
            return
        snapshot=Project.from_dict(self.project.to_dict());identity=row.id
        path=self.ensure_project_path().with_suffix('.cache.sqlite')
        def work():
            cache=Cache(path)
            try:return rescan_cue(snapshot,identity,cache,window,progress=self.progress,cancel=self.cancel_event.is_set)
            finally:cache.close()
        def done(result):
            self.project.settings.setdefault('cue_rescans',{})[identity]=result;self.autosave()
            if not result['visuals']:
                messagebox.showinfo('本句未识别到文字',result['reason'],parent=self.root);return
            window=self.popup('rescan','本句重识别结果')
            if window is None:return
            if result.get('reason'):ttk.Label(window,text=result['reason'],wraplength=580).pack(fill='x',padx=10,pady=8)
            view=tree(window,('识别文字','开始秒','结束秒','评分'),(340,90,90,75),height=7)
            for v in result['visuals']:view.insert('','end',iid=v['id'],values=(v['text'],f"{v['start']:.3f}",f"{v['end']:.3f}",f"{v['confidence']:.0%}"))
            def use():
                if not self.guard():return
                try:
                    adopt_rescan(self.editor(),identity,list(view.selection()))
                    if self.results.exists(identity):self.results.selection_set(identity);self.select_mapping(auto=True)
                    window.destroy()
                except ValueError as exc:messagebox.showwarning('无法采用',str(exc),parent=window)
            ttk.Button(window,text='采用选中片段的时间',command=use).pack(pady=8)
        self.launch(work,done)
