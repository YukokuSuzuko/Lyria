"""Local QWidget components for the four compact setup and editing windows."""
from pathlib import Path
from PySide6.QtCore import Qt,QRectF
from PySide6.QtGui import QColor,QPainter,QPen
from PySide6.QtWidgets import (QWidget,QLabel,QFrame,QPushButton,QVBoxLayout,QHBoxLayout,
                               QSizePolicy,QScrollArea,QMenu)
from .native_theme import apply_dialog_theme

_CHECK_ICON = (Path(__file__).parent / 'assets' / 'icons' / 'check-white.svg').as_posix()

STYLE = """
QDialog {background:#f7f5fb;}
QWidget#carbonBody {background:#f7f5fb;}
QFrame#carbonCard {background:#fff;border:1px solid #e5dff2;border-radius:10px;}
QFrame#carbonFooter {background:#fbf9fe;border:0;border-top:1px solid #e6e0f2;}
QFrame#carbonLine {background:#eee9f6;border:0;min-height:1px;max-height:1px;}
QLabel {font-size:13px;color:#2e263a;background:transparent;border:0;padding:0;}
QLabel[styleRole="heading"] {font-size:16px;font-weight:700;color:#29213a;}
QLabel[styleRole="title"] {font-size:13px;font-weight:600;color:#29213a;}
QLabel[styleRole="muted"], QLabel[styleRole="path"] {font-size:11.5px;color:#736985;}
QLabel[styleRole="verified"] {color:#1e7a56;font-size:12px;font-weight:600;}
QLabel[styleRole="found"] {color:#63419c;font-size:12px;font-weight:600;}
QLabel[styleRole="badge"] {background:#e6f5ee;color:#1e7a56;border:1px solid #bee6d2;border-radius:10px;padding:0px 8px;font-size:11px;font-weight:600;}
QLabel[styleRole="pill"] {background:#efe9fb;color:#5e3b9e;border:1px solid #d9ccf0;border-radius:10px;padding:0px 8px;font-size:11px;font-weight:600;}
QLabel[styleRole="count"] {background:#efeaf8;color:#63419c;border-radius:10px;padding:0px 7px;font-size:11px;font-weight:600;}
QPushButton {font-size:12px;color:#3d3352;background:#fff;border:1px solid #ded8ec;border-radius:7px;padding:4px 11px;min-height:19px;outline:none;}
QPushButton:hover {background:#f6f3fc;border-color:#c7b7e3;}
QPushButton:focus {border:1px solid #8562c5;outline:none;}
QPushButton[styleRole="quiet"] {background:transparent;border:1px solid transparent;padding:3px 7px;color:#6e6582;}
QPushButton[styleRole="link"] {background:transparent;border:1px solid transparent;padding:3px 7px;color:#63419c;font-weight:600;}
QPushButton[styleRole="quiet"]:hover,QPushButton[styleRole="link"]:hover {background:#f2ecfa;border-color:transparent;}
QPushButton[styleRole="quiet"]:focus,QPushButton[styleRole="link"]:focus {border-color:#9475b6;}
QPushButton[styleRole="soft"] {background:#f0eafa;color:#5e3b9e;border:1px solid #d9ccf0;font-weight:600;}
QPushButton[styleRole="soft"]:hover {background:#e8def9;border-color:#c6b4ea;}
QPushButton[styleRole="primary"] {background:#5e3b9e;color:white;border:1px solid #5e3b9e;padding:5px 15px;font-weight:600;}
QPushButton[styleRole="primary"]:hover {background:#4f2f8a;border-color:#4f2f8a;}
QPushButton:disabled {color:#aca4b7;background:#f8f6fb;border-color:#ece7f4;}
QPushButton[styleRole="primary"]:disabled {background:#c8bed8;color:#fff;border-color:#c8bed8;}
QPushButton::menu-indicator {width:0;height:0;}
QCheckBox {font-size:12px;color:#4f4663;spacing:6px;outline:none;}
QCheckBox::indicator {width:15px;height:15px;border-radius:4px;border:1px solid #c9bfe0;background:#fff;}
QCheckBox::indicator:hover {border-color:#8562c5;}
QCheckBox::indicator:checked {background:#5e3b9e;border-color:#5e3b9e;image:url("@checkIcon");}
QScrollArea {background:transparent;border:0;}
QComboBox,QSpinBox,QDoubleSpinBox,QLineEdit {font-size:13px;min-height:22px;border-radius:7px;}
QMenu {background:#fff;color:#2e263a;border:1px solid #dcd4e7;border-radius:8px;padding:4px;}
QMenu::item {padding:6px 18px 6px 10px;border-radius:5px;}
QMenu::item:selected {background:#f0eaf8;color:#5e3b9e;}
""".replace('@checkIcon', _CHECK_ICON)
def theme(dialog):
    apply_dialog_theme(dialog)
    dialog.setStyleSheet(dialog.styleSheet()+STYLE)

def layout(widget,vertical=True,margins=(0,0,0,0),space=0):
    box=QVBoxLayout(widget) if vertical else QHBoxLayout(widget)
    box.setContentsMargins(*margins);box.setSpacing(space)
    return box

def label(text='',role='',wrap=False):
    w=QLabel(text);w.setTextFormat(Qt.PlainText);w.setProperty('styleRole',role)
    w.setWordWrap(wrap)
    if role in ('badge', 'count', 'pill'):
        w.setAlignment(Qt.AlignmentFlag.AlignCenter)
        w.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        w.setFixedHeight(20)
    return w

class Elide(QLabel):
    def __init__(self,text='',role='path',middle=False):
        super().__init__();self.setTextFormat(Qt.PlainText);self.setProperty('styleRole',role)
        self.full='';self.middle=middle;self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred);self.setValue(text)
    def setValue(self,text):
        self.full=str(text);self.setToolTip(self.full);self.renderValue()
    def renderValue(self):
        self.setText(self.fontMetrics().elidedText(self.full,Qt.ElideMiddle if self.middle else Qt.ElideRight,max(1,self.contentsRect().width())))
    def resizeEvent(self,event):
        super().resizeEvent(event);self.renderValue()

class DeviceName(Elide):
    def sizeHint(self):
        hint=super().sizeHint()
        hint.setWidth(self.fontMetrics().horizontalAdvance(self.full)+6)
        return hint
    def minimumSizeHint(self):
        hint=super().minimumSizeHint();hint.setWidth(0)
        return hint


def button(text,action=None,role='',name=''):
    w=QPushButton(text);w.setProperty('styleRole',role);w.setObjectName(name)
    w.setAutoDefault(False)
    if action is not None:w.clicked.connect(action)
    return w

def menu_button(text,role='quiet',name=''):
    w=button(text,None,role,name);m=QMenu(w);w.setMenu(m)
    return w,m

def card(parent):
    w=QFrame();w.setObjectName('carbonCard');parent.addWidget(w)
    return layout(w)

def line(parent):
    bar=QFrame();bar.setObjectName('carbonLine');bar.setFixedHeight(1);parent.addWidget(bar)

def band(parent,margin=(13,7,13,7),gap=8):
    w=QWidget();row=layout(w,False,margin,gap);parent.addWidget(w)
    return row

def header(parent,title,note=''):
    row=band(parent);row.addWidget(label(title,'title'))
    sub=label(note,'muted');row.addWidget(sub);row.addStretch();line(parent)
    return row,sub

def footer(root):
    w=QFrame();w.setObjectName('carbonFooter');root.addWidget(w)
    return layout(w,False,(18,9,18,9),8)

class Glyph(QWidget):
    def __init__(self,kind='gpu'):
        super().__init__();self.kind=kind;self.setFixedSize(34,34)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        green=self.kind=='gpu';ink=QColor('#43856d' if green else '#815da7')
        p.setPen(Qt.NoPen);p.setBrush(QColor('#e9f5ee' if green else '#eee8f7'))
        p.drawRoundedRect(QRectF(1,1,32,32),7,7);p.setPen(QPen(ink,1.4));p.setBrush(Qt.NoBrush)
        if green:
            p.drawRoundedRect(QRectF(11,11,12,12),2,2);p.drawRect(QRectF(14,14,6,6))
            for i in (13,17,21):
                p.drawLine(i,8,i,11);p.drawLine(i,23,i,26);p.drawLine(8,i,11,i);p.drawLine(23,i,26,i)
        elif self.kind=='media':
            from PySide6.QtGui import QPolygonF
            from PySide6.QtCore import QPointF
            p.drawPolygon(QPolygonF([QPointF(13,10),QPointF(23,17),QPointF(13,24)]))
        elif self.kind=='import':
            p.drawLine(17,22,17,10);p.drawLine(13,14,17,10);p.drawLine(17,10,21,14);p.drawLine(11,24,23,24)
        else:
            p.drawLine(10,17,24,17);p.drawLine(20,13,24,17);p.drawLine(20,21,24,17)


from PySide6.QtCore import Signal
from PySide6.QtWidgets import QTextEdit
from PySide6.QtGui import QTextCursor,QTextBlockFormat
class ActivityText(QLabel):
    updated=Signal(bool)
    def __init__(self):
        super().__init__()
        self.setTextFormat(Qt.PlainText);self.setWordWrap(True)
    def setText(self,text):
        super().setText(text)
        self.updated.emit(bool(text and text.strip() not in ('就绪','操作已完成','操作已完成。','尚未开始','待命')))

class NumberedLyrics(QTextEdit):
    """Read-only lines keep their original text for selection and copy."""
    def __init__(self):
        super().__init__()
        self.setViewportMargins(33,0,0,0)
        self.setAcceptRichText(False)
        self.setStyleSheet("QTextEdit {padding:3px 6px;font-size:13px;}")
        self.setObjectName('sourceLyricsPreview')
    def setPlainText(self,text):
        if self.toPlainText()==text:return
        super().setPlainText(text)
        cur=QTextCursor(self.document());cur.select(QTextCursor.Document)
        fmt=QTextBlockFormat();fmt.setLineHeight(24,2);cur.mergeBlockFormat(fmt)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if not hasattr(self,'gutter'):
            self.gutter=_LyricNumbers(self)
            self.verticalScrollBar().valueChanged.connect(lambda *_:self.gutter.update())
            self.textChanged.connect(self.gutter.update)
        rect=self.viewport().geometry()
        self.gutter.setGeometry(rect.left()-30,rect.top(),28,rect.height())
        self.gutter.show()

class _LyricNumbers(QWidget):
    def __init__(self,editor):
        super().__init__(editor);self.editor=editor
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
    def paintEvent(self,event):
        e=self.editor;p=QPainter(self);p.setPen(QColor('#9b92a5'))
        font=e.font();font.setPixelSize(11);p.setFont(font)
        from PySide6.QtCore import QPoint
        block=e.cursorForPosition(QPoint(0,0)).block()
        while block.isValid():
            top=round(e.document().documentLayout().blockBoundingRect(block).top())-e.verticalScrollBar().value()
            if top>self.height():break
            if block.isVisible():p.drawText(0,top,24,e.fontMetrics().height(),Qt.AlignRight|Qt.AlignVCenter,f'{block.blockNumber()+1:02}')
            block=block.next()


