import tkinter as tk
from tkinter import ttk
from .dialogs import messagebox
from .credentials import KeyStore


class KeyWidget(ttk.Frame):
    def __init__(self,parent,scope,label,store=None):
        super().__init__(parent)
        self.scope=scope;self.label=label;self.store=store or KeyStore()
        self.entry=tk.StringVar();self.state=tk.StringVar()
        ttk.Entry(self,textvariable=self.entry,show='●',width=24).grid(row=0,column=0,sticky='ew')
        ttk.Button(self,text='保存密钥',command=self.save).grid(row=0,column=1,padx=4)
        ttk.Button(self,text='删除已保存密钥…',command=self.delete).grid(row=0,column=2)
        ttk.Label(self,textvariable=self.state).grid(row=1,column=0,columnspan=3,sticky='w',pady=3)
        self.columnconfigure(0,weight=1);self.refresh()
    def identity(self):
        scope=self.scope() if callable(self.scope) else self.scope
        if not scope:raise ValueError('请先选择或填写有效的 API 服务标识。')
        return scope
    def refresh(self):
        try:self.state.set('已保存密钥；留空不修改。' if self.store.get(self.identity()) else '未保存密钥；保存后输入框会清空。')
        except Exception:self.state.set('填写服务地址后可保存密钥；密钥不写入普通配置。')
    def save(self):
        try:
            if self.entry.get().strip():
                self.store.save(self.identity(),self.entry.get());self.entry.set('')
            self.refresh()
        except Exception as exc:messagebox.showerror('无法保存密钥',str(exc),parent=self)
    def get(self):
        if self.entry.get().strip():raise ValueError('密钥尚未保存，请先点击“保存密钥”。')
        return self.store.get(self.identity())
    def delete(self):
        if not messagebox.askyesno('再次确认删除密钥',f'删除本机保存的“{self.label}”密钥？\n不会注销提供商的密钥；删除后需要重新填写。',default='no',parent=self):return
        try:self.store.delete(self.identity());self.entry.set('');self.refresh()
        except Exception as exc:messagebox.showerror('无法删除密钥',str(exc),parent=self)
