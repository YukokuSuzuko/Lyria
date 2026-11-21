from __future__ import annotations

from pathlib import Path
import copy
import json
import os
import time
import tkinter as tk
from tkinter import ttk
from .dialogs import filedialog,messagebox
import uuid

from .audit import local_audit,text_audit,snapshot,save_report
from .gui import tree
from .widgets import button, Disclosure
from .key_widget import KeyWidget
from .credentials import TEXT_SCOPE, KeyStore
from .storage import atomic_json


class AuditPanel:
    def __init__(self,app):
        self.app=app
        self.report=None
        self.output=None
        self.frame=ttk.Frame(app.notebook,padding=12)
        app.notebook.add(self.frame,text="检查成片")
        ttk.Label(self.frame,text="检查做好的视频：字幕有没有配错、漏字或时间偏移",style="Heading.TLabel").pack(anchor="w",pady=(0,8))
        form=ttk.LabelFrame(self.frame,text="1 · 选择要检查的视频和字幕",padding=6)
        form.pack(fill="x")
        self.paths={}
        for row,(key,label,patterns) in enumerate((("video","要检查的视频","*.mp4 *.mkv *.mov *.webm"),
            ("ass","中文字幕文件（已压进画面则不选）","*.ass *.ssa"),
            ("translation","你最初准备的中文译文","*.txt *.srt *.ass *.ssa"),
            ("project","之前的任务文件（选填，用于核对时间）","*.lyricsync.json"),
            ("ktv","原文歌词（任务文件未包含时选择）","*.ass *.ssa *.srt *.txt"))):
            var=tk.StringVar()
            self.paths[key]=var
            ttk.Label(form,text=label,width=34).grid(row=row,column=0,sticky="w",pady=2)
            ttk.Entry(form,textvariable=var).grid(row=row,column=1,sticky="ew",padx=5)
            ttk.Button(form,text="选择…",command=lambda v=var,p=patterns:self.pick(v,p)).grid(row=row,column=2)
        form.columnconfigure(1,weight=1)
        self.text_enabled=tk.BooleanVar(value=True)
        self.text_model=tk.StringVar(value="deepseek-flash")
        self.text_key=tk.StringVar()
        self.maximum=tk.StringVar(value="50")
        self.roi=tk.StringVar()
        settings=ttk.LabelFrame(self.frame,text="2 · 检查方式",padding=8)
        settings.pack(fill="x",pady=8)
        ttk.Checkbutton(settings,style="Switch.TCheckbutton",text="用文字 AI 帮我核对（DeepSeek）",variable=self.text_enabled).grid(row=0,column=0,sticky="w")
        ttk.Label(settings,text="API 密钥").grid(row=0,column=1,padx=8)
        ttk.Button(settings,text='AI 辅助设置…',command=self.app.api_settings).grid(row=0,column=2,sticky='w')
        settings.columnconfigure(2,weight=1)
        ttk.Label(settings,text="只发送识别文字、时间轴与邻近上下文；不发送截图或视频。密钥保存后清空输入框，留空不修改。",
                  style="Hint.TLabel",wraplength=900).grid(row=1,column=0,columnspan=3,sticky="w",pady=(5,0))
        advanced=Disclosure(self.frame,"高级本地检查设置","只发送文字、时间与邻近上下文；视频和截图始终保留本机。")
        advanced.pack(fill="x")
        options=advanced.body
        for row,label,var in ((1,"最多调用次数（失败和重试也计数）",self.maximum),
                              (2,"画面文字范围 x1,y1,x2,y2（空白代表全画面）",self.roi)):
            ttk.Label(options,text=label).grid(row=row,column=0,sticky="w",pady=3)
            ttk.Entry(options,textvariable=var,width=35).grid(row=row,column=1,sticky="ew",padx=8)
        ttk.Button(options,text='文字 AI 辅助设置…',command=self.app.api_settings).grid(row=4,columnspan=2,sticky='w',pady=5)
        button(options,'保存检查设置',self.save_settings,'保存模型、服务地址和调用上限；密钥使用单独的保存按钮。').grid(row=7,column=0,sticky='w')
        options.columnconfigure(1,weight=1)
        actions=ttk.Frame(self.frame);actions.pack(fill='x',pady=5)
        for col in range(4):actions.columnconfigure(col,weight=1,uniform='audit_actions')
        for i,(label,callback,hint) in enumerate((
            ('开始检查',self.start,'先本地检查，再按设置请求文字核对。'),
            ('打开检查报告…',self.open_report,'无需重新运行检查。'),
            ('使用当前任务作参考',self.use_project,'带入原文、译文和时间。'),
            ('停止检查',self.app.cancel,'保留已完成结果。'),
            ('生成选中字幕截图',self.make_shots,'仅本地抽帧，不上传。'),
            ('其他问题 / 缺失字幕',self.global_issues,'查看全局问题。'),
            ('查看调用记录',self.records,'查看实际调用及预算。'))):
            w=button(actions,label,callback,hint,i==0);w.configure(padding=(8,9))
            w.grid(row=i//4,column=i%4,sticky='ew',padx=3,pady=3)
        self.bar=ttk.Progressbar(self.frame,maximum=100);self.bar.pack(fill='x',pady=4)
        ttk.Label(self.frame,textvariable=app.status,style='Hint.TLabel').pack(anchor='w')
        panel=ttk.Panedwindow(self.frame,orient="horizontal")
        panel.pack(fill="both",expand=True,pady=6)
        left=ttk.Frame(panel)
        right=ttk.Frame(panel)
        panel.add(left,weight=5)
        panel.add(right,weight=4)
        self.summary=ttk.Label(left,text="尚未检查；勾选API并配置Key后才会实际调用。",wraplength=750)
        self.summary.pack(fill="x")
        self.table=tree(left,("字幕正文","显示时间","待检查","文字AI"),(250,115,80,85),height=10)
        self.table.bind("<<TreeviewSelect>>",self.select)
        self.canvas=tk.Canvas(right,bg="#131922",height=260,highlightthickness=0)
        self.canvas.pack(fill="both",expand=True)
        self.details=tk.Text(right,height=9,wrap="word",state="disabled")
        self.details.pack(fill="x")
        try:
            saved=json.loads((app.home/'audit_settings.json').read_text(encoding='utf-8'))
            for key in ('text_model','maximum'):
                if key in saved:getattr(self,key).set(saved[key])
        except (OSError,ValueError):pass

    def save_settings(self):
        atomic_json(self.app.home/'audit_settings.json',{key:getattr(self,key).get() for key in ('text_model','maximum')})
        self.app.status.set('检查设置已保存；密钥由 Windows 凭据管理器单独保管。')

    def pick(self,var,patterns):
        if not self.app.guard():return
        path=filedialog.askopenfilename(filetypes=[("素材",patterns),("全部","*.*")])
        if path:var.set(path)

    def use_project(self):
        if not self.app.guard():return
        self.app.save()
        if self.app.project_path:
            self.paths["project"].set(str(self.app.project_path))
            self.paths["ktv"].set(self.app.project.inputs.get("ktv",""))
            self.paths["translation"].set(self.app.project.inputs.get("translation",""))

    def start(self):
        if not self.app.guard():return
        try:
            video=self.paths["video"].get()
            translation=self.paths["translation"].get()
            if not Path(video).is_file() or not Path(translation).is_file():raise ValueError("请选择有效成片视频和用户原始译文。")
            self.save_settings()
            key=KeyStore().get(TEXT_SCOPE) if self.text_enabled.get() else ''
            self.app.audit_text_key=key
            if self.text_enabled.get() and not key:raise ValueError("文本API默认开启，请填写Key；或取消勾选，仅运行明确标注的本地检查。")
            roi=[int(x.strip()) for x in self.roi.get().split(",")] if self.roi.get().strip() else None
            maximum=int(self.maximum.get())
            if not 0<=maximum<=100:raise ValueError("API调用上限应为0～100。")
            output=self.app.home/"audits"/(time.strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:6])
            options={k:v.get() or None for k,v in self.paths.items() if k not in ("video","translation")}
            text_enabled=self.text_enabled.get()
            from . import ai_config
            shared=ai_config.load(self.app.home)
            model=shared['api_model'];maximum=shared['api_max_calls'];retry=shared['api_retries']
            def process():
                report=local_audit(video,translation,output,ass_path=options["ass"],project_path=options["project"],ktv_path=options["ktv"],
                    settings={"roi":roi},progress=self.app.progress,cancel=self.app.cancel_event.is_set)
                if text_enabled:
                    try:text_audit(report,output,key,model,maximum,retry,self.app.cancel_event.is_set,self.app.progress)
                    except InterruptedError:raise
                    except Exception as exc:
                        report["warnings"].append(str(exc).replace(key,"[REDACTED]"))
                        save_report(report,output)
                else:
                    report["api"]["text"]["status"]="disabled"
                    save_report(report,output)
                return report,output
            self.app.launch(process,self.completed)
        except Exception as exc:self.app.error(exc)

    def completed(self,result):
        self.report,self.output=result
        self.refresh()
        self.app.status.set("质检报告已保存："+str(self.output/"report.html"))

    def refresh(self):
        if not self.report:return
        self.table.delete(*self.table.get_children())
        text=self.report["api"]["text"]
        for cue in self.report["cues"]:
            problems=[i for i in self.report["issues"] if cue["id"] in i.get("cue_ids",[]) and i.get("origin","local")=="local"]
            self.table.insert("","end",iid=cue["id"],values=(cue["text"],f'{cue["start"]:.3f}–{cue["end"]:.3f}',len(problems),
                "已检查" if cue["id"] in text["checked_ids"] else "未检查"))
        labels={"not_run":"尚未检查","disabled":"未启用","running":"检查中","completed":"已完成本次检查","partial":"仅完成一部分","failed":"未成功"}
        self.summary.configure(text=f"检查了 {len(self.report['cues'])} 条字幕，发现 {len(self.report['issues'])} 项疑问。文字 AI：{labels.get(text['status'],text['status'])}。")

    def open_report(self):
        if not self.app.guard():return
        path=filedialog.askopenfilename(filetypes=[("质检报告JSON","*.json")])
        if not path:return
        try:
            report=json.loads(Path(path).read_text(encoding="utf-8"))
            if report.get("version") not in (1,2) or "cues" not in report or "api" not in report:raise ValueError("不是有效质检报告")
            self.report,self.output=report,Path(path).parent
            self.refresh()
        except Exception as exc:self.app.error(exc)

    def select(self,_=None):
        if not self.report:return
        ids=set(self.table.selection())
        cues=[c for c in self.report["cues"] if c["id"] in ids]
        if not cues:return
        cue=cues[0]
        issues=[i for i in self.report["issues"] if cue["id"] in i.get("cue_ids",[])]
        content="当前字幕："+cue["text"]+f"\n显示时间：{cue['start']:.3f} 至 {cue['end']:.3f} 秒"
        content+="\n原始中文："+(" / ".join(cue.get("expected_texts",[])) or "未找到可靠对应")
        content+="\n参考原文："+(" / ".join(cue.get("reference_lyrics",[])) or "未提供")
        content+="\n\n检查发现：\n"+("\n".join(i["reason"] for i in issues) or "此条未记录问题；不代表整片已验证无误。")
        if self.report["warnings"]:content+="\n\n还需留意：\n"+"\n".join(self.report["warnings"])
        self.details.configure(state="normal");self.details.delete("1.0","end")
        self.details.insert("1.0",content);self.details.configure(state="disabled")
        self.canvas.delete("all")
        if cue.get("screenshot") and Path(cue["screenshot"]).is_file():
            from PIL import Image,ImageTk
            image=Image.open(cue["screenshot"])
            image.thumbnail((max(100,self.canvas.winfo_width()),max(100,self.canvas.winfo_height())))
            self.photo=ImageTk.PhotoImage(image)
            self.canvas.create_image(self.canvas.winfo_width()/2,self.canvas.winfo_height()/2,image=self.photo)

    def make_shots(self):
        if not self.app.guard() or not self.report:return
        ids=self.table.selection()
        if not 1<=len(ids)<=4:
            messagebox.showinfo("选择截图","请先选择1～4个片段；会在本地真实抽帧/渲染，不发送API。")
            return
        report=copy.deepcopy(self.report)
        def process():
            for cue in report["cues"]:
                if cue["id"] in ids:snapshot(report,cue,self.output)
            save_report(report,self.output)
            return report,self.output
        def done(result):
            self.completed(result)
            self.table.selection_set(ids)
            self.select()
            self.app.status.set("本地证据截图已生成；不会上传图片或视频。")
        self.app.launch(process,done)

    def records(self):
        if self.report:messagebox.showinfo("质检API实际调用记录",json.dumps(self.report.get("api_records",[]),ensure_ascii=False,indent=2))

    def global_issues(self):
        if self.report:
            issues=[i for i in self.report["issues"] if not i.get("cue_ids")]
            text="\n".join(self.report["warnings"]+[i["reason"] for i in issues]) or "没有全局/缺失警告。"
            messagebox.showinfo("全局证据限制与未检测译文",text)
