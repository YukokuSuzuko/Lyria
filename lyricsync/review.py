"""Review actions shared by the GUI; no OCR or source-file mutation here."""
from pathlib import Path
from difflib import SequenceMatcher
import json
import threading
import tkinter as tk
from tkinter import ttk
from .dialogs import messagebox
from .model import valid_time
from .editing import mapping_text,split_mapping,merge_mappings
from .frames import FrameIndex
from .storage import Cache,atomic_json
from .credentials import KeyStore,TEXT_SCOPE
from .api import translate_reference
from .widgets import button


class ReviewActions:
    def undo(self,redo=False):
        if not self.guard():return 'break'
        selected=self.selected_mapping()
        identity=selected.id if selected else None
        self.project,label=self.history.restore(self.project,redo)
        if label:
            self.drafts.clear();self.detail_id=None
            self.autosave();self.refresh()
            if identity and self.results.exists(identity):self.results.selection_set(identity)
            elif self.results.get_children():self.results.selection_set(self.results.get_children()[0])
            self.select_mapping();self.draw()
            self.status.set(('已重做：' if redo else '已撤销：')+label)
        else:self.status.set('没有可以重做的修改。' if redo else '没有可以撤销的修改。')
        return 'break'

    def confirm_next(self):
        current=self.selected_mapping()
        if current is None:
            self.status.set('请先选择一条中文字幕。');return
        order=list(self.results.get_children());index=order.index(current.id)
        if not self.confirm_mapping():return
        candidates=[i for i in order[index+1:] if self.results.exists(i)]
        if candidates:
            self.results.selection_set(candidates[0]);self.results.see(candidates[0]);self.select_mapping()
        else:
            self.status.set('当前字幕已确认，已到列表末尾。')

    def update_review_fonts(self):
        try:size=max(12,min(28,int(self.review_size.get())))
        except (ValueError,tk.TclError):return
        for box in self.review_cards.values():
            box.configure(font=('Microsoft YaHei UI',size));box.schedule_fit()
        self.reference_label.configure(font=('Microsoft YaHei UI',size))

    def show_review_cards(self,source,ocr,chinese):
        self._text_loading=True
        for key,text in (('source',source),('ocr',ocr),('chinese',chinese)):
            box=self.review_cards[key];box.configure(state='normal');box.delete('1.0','end')
            box.insert('1.0',text if key=='chinese' else text or '暂无内容')
            box.configure(state='normal' if key=='chinese' else 'disabled');box.edit_modified(False)
            box.schedule_fit()
        for operation,a,b,c,d in SequenceMatcher(None,source,ocr,autojunk=False).get_opcodes():
            if operation!='equal':
                self.review_cards['source'].tag_add('difference',f'1.0+{a}c',f'1.0+{b}c')
                self.review_cards['ocr'].tag_add('difference',f'1.0+{c}c',f'1.0+{d}c')

        self._text_loading=False

    def frame_counter(self):
        return f'第 {self.frame_index.current(self.stamp)} / {len(self.frame_index.stamps)} 帧' if self.frame_index else '正在建立视频帧索引…'

    def frame_label(self,stamp,end):
        if stamp is None:return '待补齐'
        if not self.frame_index:return f'{stamp:.3f}s（待索引）'
        return str(self.frame_index.last(stamp) if end else self.frame_index.first(stamp))

    def build_frame_index(self,video,generation):
        self.status.set('正在建立视频帧索引，可先预览视频，完成后即可逐帧修轴。')
        def work():
            try:
                stat=Path(video.path).stat()
                identity=Cache.key('pts-index-v1',dict(path=str(Path(video.path).resolve()),size=stat.st_size,mtime=stat.st_mtime_ns)).split(':')[1]
                path=self.home/'frames'/(identity+'.json')
                if path.is_file():
                    value=json.loads(path.read_text(encoding='utf-8'));index=FrameIndex(value['stamps'],value['duration'])
                else:
                    index=FrameIndex.build(video,lambda:generation!=self.index_generation or self.close_requested)
                    atomic_json(path,dict(stamps=index.stamps,duration=index.duration))
                self.jobs.put(('frame_index',generation,index))
            except InterruptedError:pass
            except Exception as exc:self.jobs.put(('frame_index_error',generation,str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def display_frames(self):
        first=last=''
        if self.frame_index:
            try:first=str(self.frame_index.first(float(self.start.get()))) if self.start.get().strip() else ''
            except ValueError:pass
            try:last=str(self.frame_index.last(float(self.end.get()))) if self.end.get().strip() else ''
            except ValueError:pass
        self._loading_edit=True
        try:self.first_frame.set(first);self.last_frame.set(last);self._frame_display=(first,last)
        finally:self._loading_edit=False

    def apply_frame_fields(self):
        fields=(self.first_frame.get().strip(),self.last_frame.get().strip())
        if fields==self._frame_display:return
        if not self.frame_index:raise ValueError('帧索引尚未就绪，请稍等后再填写帧号。')
        if not all(fields):raise ValueError('请补齐开始帧和结束帧。')
        try:first,last=map(int,fields)
        except ValueError:raise ValueError('帧号必须为整数，从第 1 帧开始。') from None
        start,end=self.frame_index.interval(first,last)
        self.start.set(repr(start));self.end.set(repr(end));self._frame_display=fields

    def goto_frame(self):
        if not self.frame_index:
            self.status.set('请等待视频帧索引建立完成。');return
        try:self.seek(self.frame_index.stamp(int(self.jump_frame.get())))
        except (ValueError,TypeError):messagebox.showwarning('帧号无效',f'请输入 1～{len(self.frame_index.stamps)} 之间的整数。',parent=self.root)

    def mark_frame(self,end):
        if not self.guard():return
        if not self.selected_mapping() or not self.frame_index:
            self.status.set('请先选择字幕，并等待视频帧索引完成。');return
        self.pause_playback()
        number=self.frame_index.current(self.stamp)
        start,stop=self.frame_index.interval(number,number)
        try:self.editor().set_time(self.selected_mapping().id,**{'end' if end else 'start':stop if end else start})
        except ValueError as exc:
            self.status.set(str(exc));return
        (self.end if end else self.start).set(repr(stop if end else start))
        self.display_frames()
        self.status.set(('结束' if end else '开始')+f'帧已设为第 {number} 帧。')

    def split_dialog(self):
        if not self.guard():return
        m=self.selected_mapping()
        if not m:
            messagebox.showinfo('请选择字幕','请选择一条中文字幕，再拆分。',parent=self.root);return
        if valid_time(m.start,m.end) and not self.frame_index:
            self.status.set('请等待帧索引完成后再拆分。');return
        window=self.popup('split','拆分字幕')
        if window is None:return
        ttk.Label(window,text='请点击文本，将光标放在需要拆成两句的位置。').pack(padx=12,pady=10)
        content=mapping_text(self.project,m)
        editor=tk.Text(window,height=5,width=44,font=('Microsoft YaHei UI',16),wrap='word')
        editor.insert('1.0',content);editor.pack(padx=12,pady=8)
        editor.bind('<Key>',lambda e:None if e.keysym in ('Left','Right','Up','Down','Home','End') else 'break')
        editor.bind('<<Paste>>',lambda _:'break');editor.bind('<<Cut>>',lambda _:'break')
        frame=tk.StringVar(value=str(self.frame_index.current((m.start+m.end)/2)) if valid_time(m.start,m.end) and self.frame_index else '')
        ttk.Label(window,text='后半句从第几帧开始？（前半句结束于前一帧）').pack(padx=12,pady=4)
        ttk.Entry(window,textvariable=frame).pack(pady=5)
        def apply():
            if not self.guard():return
            try:
                if editor.get('1.0','end-1c')!=content:raise ValueError('拆分不能改写正文，请重新打开拆分窗口。')
                cut=self.frame_index.stamp(int(frame.get())) if frame.get().strip() and self.frame_index else None
                character=len(editor.get('1.0','insert'))
                # Validate against a copy before adding an undo entry.
                from .model import Project
                candidate=Project.from_dict(self.project.to_dict())
                a,b=split_mapping(candidate,m.id,character,cut)
                self.history.push(self.project,'拆分中文');self.project=candidate
                self.autosave();self.refresh();self.results.selection_set(a.id);self.select_mapping();window.destroy()
            except Exception as exc:messagebox.showwarning('无法拆分',str(exc),parent=window)
        button(window,'按光标位置拆成两条',apply,'只分开文字，原开始和最终结束不变。',True).pack(pady=12)

    def merge_selected(self):
        if not self.guard():return
        ids=set(self.results.selection());rows=[m for m in self.project.mappings if m.id in ids]
        try:
            from .model import Project
            candidate=Project.from_dict(self.project.to_dict());merged=merge_mappings(candidate,ids)
            ordered=sorted([m for m in rows if valid_time(m.start,m.end)],key=lambda m:m.start);frontier=ordered[0].end if ordered else 0;gap=False
            for row in ordered[1:]:gap=gap or row.start>frontier+.000001;frontier=max(frontier,row.end)
            if gap and not messagebox.askyesno('合并会填满中间空档','这些字幕之间有空白时间。合并后中文会在整个起止范围持续显示，是否继续？',default='no',parent=self.root):return
            self.history.push(self.project,'合并中文');self.project=candidate
            self.autosave();self.refresh();self.results.selection_set(merged.id);self.select_mapping()
        except Exception as exc:messagebox.showwarning('无法合并',str(exc),parent=self.root)

    def reference_translation(self):
        if not self.guard():return
        selected=self.lyrics_tree.selection()
        if len(selected)!=1:
            messagebox.showinfo('请选择一句原文','请展开配对表，在“原文文件里的歌词”中选择一句再翻译。',parent=self.root);return
        lyric=next(l for l in self.project.lyrics if l.id==selected[0])
        try:
            key=KeyStore().get(TEXT_SCOPE)
            if not key:
                self.api_settings();return
            self.api_key=key;path=self.ensure_project_path().with_suffix('.cache.sqlite')
            settings=dict(self.project.settings)
            def work():
                cache=Cache(path)
                try:
                    result=translate_reference(lyric.text,lyric.id,cache,key,settings.get('api_model','deepseek-flash'),
                        int(settings.get('api_max_calls',20)),int(settings.get('api_retries',1)))
                    return result,cache.records()
                finally:cache.close()
            def done(value):
                result,records=value;self.project.api_records=records
                if self.lyrics_tree.selection()==(lyric.id,):
                    self.reference_value.set(result['translation']+('\n提示：'+result['note'] if result['note'] else ''))
                self.autosave();self.status.set('参考翻译已生成（仅供对照，不会改动当前译文）。')
            self.launch(work,done)
        except Exception as exc:self.error(exc)
