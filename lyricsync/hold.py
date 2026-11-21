"""Repeat a boundary action; one press is one undo entry."""
class HoldAction:
    def __init__(self,widget,app,end,delta):
        self.widget,self.app,self.end,self.delta=widget,app,end,delta
        self.timer=None;self.active=False
        widget.bind('<ButtonPress-1>',self.start)
        widget.winfo_toplevel().bind('<FocusOut>',lambda event:(self.stop(),None)[1],add='+')
        for event in ('<ButtonRelease-1>','<Leave>','<FocusOut>','<Destroy>'):
            widget.bind(event,self.stop,add='+')
    def start(self,event=None):
        self.stop()
        row=self.app.selected_mapping()
        if not row or self.app.busy:return 'break'
        self.identity=row.id;self.active=True
        self.app.history.begin('连续调整字幕帧数')
        if self.step():self.timer=self.widget.after(350,self.tick)
        return 'break'
    def step(self):
        row=self.app.selected_mapping()
        if not self.active or not row or row.id!=self.identity or self.app.busy:
            self.stop();return False
        if not self.app.nudge_boundary(self.end,self.delta,quiet=True):
            self.stop();return False
        return True
    def tick(self):
        self.timer=None
        if self.step():self.timer=self.widget.after(80,self.tick)
    def stop(self,event=None):
        if self.timer is not None:self.widget.after_cancel(self.timer);self.timer=None
        if self.active:
            self.app.history.end();self.app.scrubbing=False
        self.active=False
        return 'break'


class PreviewHold:
    """Repeat preview navigation without adding subtitle edit history."""
    def __init__(self,widget,app,delta):
        self.widget,self.app,self.delta=widget,app,delta
        self.timer=None;self.active=False
        widget.bind('<ButtonPress-1>',self.start)
        widget.winfo_toplevel().bind('<FocusOut>',lambda event:(self.stop(),None)[1],add='+')
        for event in ('<ButtonRelease-1>','<Leave>','<FocusOut>','<Destroy>'):
            widget.bind(event,self.stop,add='+')

    def start(self,event=None):
        self.stop()
        self.video=self.app.preview_video;self.active=True
        if self.step():self.timer=self.widget.after(350,self.tick)
        return 'break'

    def step(self):
        if not self.active or self.video is not self.app.preview_video or self.app.busy:
            self.stop();return False
        if not self.app.request_step(self.delta):
            self.stop();return False
        return True

    def tick(self):
        self.timer=None
        if self.step():self.timer=self.widget.after(80,self.tick)

    def stop(self,event=None):
        if self.timer is not None:self.widget.after_cancel(self.timer);self.timer=None
        if self.active:self.app.scrubbing=False
        self.active=False
        return 'break'
