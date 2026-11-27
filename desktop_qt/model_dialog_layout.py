"""Small responsive widgets for native model dialogs; no model/task state."""
from PySide6.QtCore import QSize,Qt
from PySide6.QtWidgets import QWidget,QGridLayout,QLabel,QFrame,QVBoxLayout,QHBoxLayout,QSizePolicy

class ResponsiveRow(QWidget):
    """Wrap complete controls rather than truncate or overlap them."""
    def __init__(self,parent=None):
        super().__init__(parent)
        self.grid=QGridLayout(self);self.grid.setContentsMargins(0,0,0,0)
        self.items=[];self.columns=0
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred)
    def addWidget(self,widget):
        self.items.append(widget);self._reflow()
    def _reflow(self):
        if not self.items:return
        minimum=max(w.minimumSizeHint().width() for w in self.items)+self.grid.horizontalSpacing()
        columns=max(1,min(len(self.items),self.width()//max(1,minimum)))
        if columns!=self.columns:
            self.columns=columns
            for i,w in enumerate(self.items):self.grid.addWidget(w,i//columns,i%columns)
            for i in range(len(self.items)):self.grid.setColumnStretch(i,1 if i<columns else 0)
            self.updateGeometry()
    def resizeEvent(self,event):
        super().resizeEvent(event);self._reflow()
    def minimumSizeHint(self):
        return QSize(0,self.grid.minimumSize().height())

class ReadinessSummary(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.grid=QGridLayout(self);self.grid.setContentsMargins(0,0,0,0)
        self.cards=[];self.values={};self.columns=0
        for key,title in [('audio','音频'),('text','原词'),('models','模型文件'),('runtime','运行环境')]:
            card=QFrame();card.setObjectName('modelReadinessCard')
            box=QVBoxLayout(card);box.setContentsMargins(10,6,10,6)
            heading=QLabel(title);heading.setTextFormat(Qt.TextFormat.PlainText)
            value=QLabel('待检查');value.setTextFormat(Qt.TextFormat.PlainText);value.setWordWrap(True)
            value.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
            box.addWidget(heading);box.addWidget(value)
            self.values[key]=value;self.cards.append(card)
        self._reflow()
    def set_status(self,key,text,detail=''):
        self.values[key].setText(text);self.values[key].setToolTip(detail or text)
    def _reflow(self):
        width=max(170,self.fontMetrics().horizontalAdvance('模型文件尚未准备')+45)
        columns=4 if self.width()>=width*4 else (2 if self.width()>=width*2 else 1)
        if columns!=self.columns:
            self.columns=columns
            for i,card in enumerate(self.cards):self.grid.addWidget(card,i//columns,i%columns)
            for i in range(4):self.grid.setColumnStretch(i,1 if i<columns else 0)
            self.updateGeometry()
    def resizeEvent(self,event):
        super().resizeEvent(event);self._reflow()
    def minimumSizeHint(self):return QSize(0,self.grid.minimumSize().height())


class _CheckLabel(QLabel):
    def __init__(self,box):
        super().__init__(box.text())
        self.box=box
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and self.box.isEnabled():
            self.box.setFocus();self.box.toggle();event.accept()
        else:super().mousePressEvent(event)


def wrapped_checkbox(box):
    row=QWidget();layout=QHBoxLayout(row);layout.setContentsMargins(0,0,0,0)
    text=_CheckLabel(box)
    box.setAccessibleName(box.text())
    box.setText('')
    layout.addWidget(box,0,Qt.AlignmentFlag.AlignTop);layout.addWidget(text,1)
    return row
