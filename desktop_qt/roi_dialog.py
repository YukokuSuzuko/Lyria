"""Real-pixel dual ROI editor. All drafts stay local until explicit confirmation.

Native adjunct to the QML app, not a second media player. Coordinates are source
pixel edges (right/bottom exclusive); widget geometry and display DPR never leak
into audit-service coordinates. No OCR/network work runs on the GUI thread.
"""
from __future__ import annotations
from copy import deepcopy
import math
from PySide6.QtCore import Qt, QRectF, QPointF, Signal, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import (QWidget,QDialog,QVBoxLayout,QHBoxLayout,QGridLayout,
    QLabel,QComboBox,QPushButton,QDoubleSpinBox,QSpinBox,QListWidget,QLineEdit,
    QCheckBox,QDialogButtonBox,QSizePolicy,QScrollArea,QFrame,QSlider)
from .native_theme import COLORS, apply_dialog_theme


def plain(text=''):
    result=QLabel(text);result.setTextFormat(Qt.TextFormat.PlainText);result.setWordWrap(True);return result


class RoiCanvas(QWidget):
    boxDrawn=Signal(str,object)
    boxPreview=Signal(str,object)
    def __init__(self,width,height,parent=None):
        super().__init__(parent);self.source_width=width;self.source_height=height
        self.image=None;self.boxes={};self.role='chinese';self.candidate=None;self._start=None;self._end=None
        self.setObjectName('qcRoiCanvas');self.setMinimumSize(360,200);self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.CrossCursor);self.setMouseTracking(True)

    def content_rect(self):
        scale=min(self.width()/self.source_width,self.height()/self.source_height)
        w=self.source_width*scale;h=self.source_height*scale
        return QRectF((self.width()-w)/2,(self.height()-h)/2,w,h)

    def source_point(self,position,*,clamp=False):
        rect=self.content_rect()
        if not clamp and not rect.contains(position):return None
        x=max(0.,min(float(self.source_width),(position.x()-rect.x())*self.source_width/rect.width()))
        y=max(0.,min(float(self.source_height),(position.y()-rect.y())*self.source_height/rect.height()))
        return QPointF(x,y)

    def pixel_box(self,start,end):
        # Floor the leading edge and ceil the trailing edge: include every touched pixel.
        x1=max(0,min(self.source_width,math.floor(min(start.x(),end.x())+1e-8)))
        y1=max(0,min(self.source_height,math.floor(min(start.y(),end.y())+1e-8)))
        x2=max(0,min(self.source_width,math.ceil(max(start.x(),end.x())-1e-8)))
        y2=max(0,min(self.source_height,math.ceil(max(start.y(),end.y())-1e-8)))
        return [x1,y1,x2,y2] if x1<x2 and y1<y2 else None

    def display_box(self,box):
        r=self.content_rect();x1,y1,x2,y2=box
        return QRectF(r.x()+x1/self.source_width*r.width(),r.y()+y1/self.source_height*r.height(),(x2-x1)/self.source_width*r.width(),(y2-y1)/self.source_height*r.height())

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and self.image is not None:
            self._start=self.source_point(event.position());self._end=self._start;self.update()
    def mouseMoveEvent(self,event):
        if self._start is not None:
            self._end=self.source_point(event.position(),clamp=True);self.boxPreview.emit(self.role,self.pixel_box(self._start,self._end));self.update()
    def mouseReleaseEvent(self,event):
        if event.button()!=Qt.MouseButton.LeftButton or self._start is None:return
        end=self.source_point(event.position(),clamp=True);box=self.pixel_box(self._start,end)
        self._start=None;self._end=None
        if box:self.boxDrawn.emit(self.role,box)
        else:self.boxPreview.emit(self.role,None)
        self.update()

    def paintEvent(self,event):
        painter=QPainter(self);painter.fillRect(self.rect(),QColor(COLORS['canvas']))
        if self.image is None:
            painter.setPen(QColor(COLORS['label']));painter.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,'暂无预览画面');return
        painter.drawImage(self.content_rect(),self.image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        boxes=dict(self.boxes)
        if self._start is not None and self._end is not None:
            current=self.pixel_box(self._start,self._end)
            if current:boxes[self.role]=current
        for role,box in boxes.items():
            if len(box)!=4 or box[2]<=box[0] or box[3]<=box[1]:continue
            rect=self.display_box(box);color=QColor('#CDB2FF' if role=='chinese' else '#EDA8CF')
            pen=QPen(color,2,Qt.PenStyle.DashLine);painter.setPen(pen);painter.setBrush(Qt.BrushStyle.NoBrush);painter.drawRoundedRect(rect,7,7)
            painter.setPen(QPen(QColor('#F4E9FF'),3,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
            n=min(16.,rect.width()/3,rect.height()/3)
            for points in [((rect.left(),rect.top()+n),(rect.left(),rect.top()),(rect.left()+n,rect.top())),((rect.right()-n,rect.bottom()),(rect.right(),rect.bottom()),(rect.right(),rect.bottom()-n))]:
                path=QPainterPath(QPointF(*points[0]));path.lineTo(QPointF(*points[1]));path.lineTo(QPointF(*points[2]));painter.drawPath(path)
            title='中文 · 待确认' if role=='chinese' else '原文 · 待确认'
            painter.fillRect(QRectF(rect.left(),max(self.content_rect().top(),rect.top()-22),110,21),QColor('#F4ECFC'))
            painter.setPen(QColor('#513F67'));painter.drawText(QPointF(rect.left()+5,max(self.content_rect().top()+15,rect.top()-7)),title)
        if self.candidate:
            painter.setPen(QPen(QColor('#FFFFFF'),2,Qt.PenStyle.DotLine));painter.drawRect(self.display_box(self.candidate))


class RegionDialog(QDialog):
    def __init__(self,bridge):
        super().__init__();self.bridge=bridge;self.context=bridge.qc_region_context();self.alive=True;self.job=None;self.serial=0
        self.setObjectName('qcRegionDialog');self.setWindowTitle('框选字幕识别区域（中文 / 原文）')
        available=self.screen().availableGeometry();self.resize(min(920,max(480,available.width()-40)),min(790,max(360,available.height()-80)))
        qc=bridge._qc;self.width_pixels=qc.get('width',0);self.height_pixels=qc.get('height',0)
        if not self.width_pixels or not self.height_pixels:raise ValueError('请先导入并加载质检视频。')
        self.busy=False;self.selected={r for r in ('chinese','source') if qc.get(r+'Roi')};self.show_boxes=False
        self.origins={role:(qc.get(role+'Roi') or {}).get('origin','user') for role in ('chinese','source')}
        outer=QVBoxLayout(self);self.scroll=QScrollArea();self.scroll.setObjectName('qcRoiScroll');self.scroll.setWidgetResizable(True);self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        body=QWidget();root=QVBoxLayout(body);self.scroll.setWidget(body);outer.addWidget(self.scroll,1)
        stage='当前为源视频 + 外挂字幕渲染预览' if qc.get('inputKind')=='raw' else '当前为压制硬字幕成片画面'
        root.addWidget(plain(stage+'。请在画面上拖动鼠标分别框选中文与原文字幕区域，确认无误后点击下方保存。'))
        self.canvas=RoiCanvas(self.width_pixels,self.height_pixels);self.canvas.image=bridge._qc_preview;root.addWidget(self.canvas,1)
        toolbar=QHBoxLayout();root.addLayout(toolbar)
        self.role=QComboBox();self.role.addItem('框选中文区域','chinese');self.role.addItem('框选原文区域','source');toolbar.addWidget(self.role)
        self.role.currentIndexChanged.connect(lambda:self._set_role())
        self.seek=QDoubleSpinBox();self.seek.setRange(0,max(0,qc.get('duration',0)-.001));self.seek.setDecimals(3);self.seek.setValue(qc.get('previewSeconds',0));self.seek.setSuffix(' 秒');toolbar.addWidget(self.seek)
        self.actual=plain('画面时间 '+str(round(qc.get('previewSeconds',0),3)));toolbar.addWidget(self.actual)
        self.load=QPushButton('跳转至此时间');self.load.clicked.connect(self.load_frame);toolbar.addWidget(self.load)
        timeline=QHBoxLayout();root.addLayout(timeline)
        timeline.addWidget(plain('视频时间轴'))
        self.timeline=QSlider(Qt.Orientation.Horizontal);self.timeline.setObjectName('qcRoiTimeline');self.timeline.setRange(0,round(self.seek.maximum()*1000));self.timeline.setValue(round(self.seek.value()*1000));timeline.addWidget(self.timeline,1)
        self.timeline.valueChanged.connect(lambda value:self.seek.setValue(value/1000))
        self.seek.valueChanged.connect(lambda value:self.timeline.setValue(round(value*1000)))
        self.timeline.sliderReleased.connect(self.load_frame)
        self.show_regions=QCheckBox('显示区域框');self.show_regions.setObjectName('qcShowRegions');self.show_regions.setChecked(False);timeline.addWidget(self.show_regions)
        self.show_regions.toggled.connect(self._show_regions)
        grid=QGridLayout();root.addLayout(grid);self.controls={}
        for col,title in enumerate(('区域','左 X','上 Y','右 X','下 Y')):grid.addWidget(plain(title),0,col)
        for row,(role,title,default) in enumerate([('chinese','中文',[0,round(self.height_pixels*.7),self.width_pixels,self.height_pixels]),('source','原文',[0,round(self.height_pixels*.5),self.width_pixels,round(self.height_pixels*.7)])],1):
            grid.addWidget(plain(title),row,0);boxes=[]
            initial=(qc.get(role+'Roi') or {}).get('box',[0,0,0,0])
            for n in range(4):
                spin=QSpinBox();spin.setObjectName('roi_'+role+'_'+str(n));spin.setRange(0,self.width_pixels if n%2==0 else self.height_pixels);spin.setValue(initial[n]);grid.addWidget(spin,row,n+1);boxes.append(spin)
                spin.valueChanged.connect(lambda _value,role=role:self.manual_changed(role))
            self.controls[role]=boxes
        self.canvas.boxDrawn.connect(self.drawn);self.canvas.boxPreview.connect(self.preview_box);self._sync_boxes()
        row=QHBoxLayout();root.addLayout(row);row.addWidget(plain('探测识别语言：'))
        self.language=QLineEdit(bridge.document.project.settings.get('language','jpn+eng'));self.language.setObjectName('qcCandidateLanguage');row.addWidget(self.language)
        self.detect=QPushButton('自动探测字幕区域');self.detect.setObjectName('qcSuggestRegions');self.detect.clicked.connect(self.propose);row.addWidget(self.detect)
        self.cancel_job=QPushButton('取消探测');self.cancel_job.clicked.connect(self.cancel_pending);self.cancel_job.setEnabled(False);row.addWidget(self.cancel_job)
        self.candidates=QListWidget();self.candidates.setObjectName('qcRegionCandidates');self.candidates.setMaximumHeight(105);root.addWidget(self.candidates)
        self.candidates.currentRowChanged.connect(self.highlight)
        assignment=QHBoxLayout();root.addLayout(assignment)
        for role,title in [('chinese','设为中文区域'),('source','设为原文区域')]:
            button=QPushButton(title);button.setObjectName('qcAssign_'+role);button.clicked.connect(lambda checked=False,role=role:self.assign_candidate(role));assignment.addWidget(button)
        self.enable_source=QCheckBox('同时框选原文区域（用于双语对照质检）');self.enable_source.setChecked(True);root.addWidget(self.enable_source);self.enable_source.toggled.connect(lambda:self._validate_draft())
        self.status=plain('可直接在画面中拖出选区，或微调上方像素坐标；自动探测时注意避开台标与水印。');self.status.setObjectName('qcRegionStatus');root.addWidget(self.status)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('确认选区');self.buttons.accepted.connect(self.confirm);self.buttons.rejected.connect(self.reject);self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消');outer.addWidget(self.buttons)
        self.finished.connect(self.closed);bridge.stateChanged.connect(self.check_source)
        self.poll=QTimer(self);self.poll.setInterval(250);self.poll.timeout.connect(self.check_source);self.poll.start()
        apply_dialog_theme(self);self._validate_draft()

    def valid(self):return self.alive and not self.bridge._shutdown and self.context==self.bridge.qc_region_context()
    def check_source(self):
        if self.alive and not self.valid():self.reject()
        elif self.alive and self.job and self.job not in self.bridge.jobs.active:
            self.job=None;self._busy(False)
    def closed(self,*args):
        if not self.alive:return
        self.alive=False;self.serial+=1;self.poll.stop()
        if self.job:self.bridge.jobs.cancel(self.job)
        try:self.bridge.stateChanged.disconnect(self.check_source)
        except RuntimeError:pass
    def _set_role(self):
        if self.role.currentData()=='source' and 'chinese' not in self.selected:
            self.role.setCurrentIndex(0)
            if hasattr(self,'status'):self.status.setText('请先框选中文区域，再选择原文区域。')
        self.canvas.role=self.role.currentData();self.canvas.update()
    def _show_regions(self,value):self.show_boxes=value;self._sync_boxes()
    def _sync_boxes(self):
        self.canvas.boxes={r:[s.value() for s in boxes] for r,boxes in self.controls.items() if r in self.selected} if self.show_boxes else {}
        self.canvas.update()
        if hasattr(self,'buttons'):self._validate_draft()
    def _validate_draft(self):
        from lyricsync.audit_service import validate_regions
        error=''
        if self.canvas.image is None:error='尚未加载视频画面，无法保存选区。'
        elif 'chinese' not in self.selected:error='请先框选中文区域，再选择原文区域。'
        elif self.enable_source.isChecked() and 'source' not in self.selected:error='已框选中文区域，请继续框选原文区域（如仅检查中文字幕，可取消勾选上方的原文区域选项）。'
        else:
            try:
                regions={r:{'box':[s.value() for s in controls],'confirmed':True,'origin':self.origins[r]} for r,controls in self.controls.items()}
                validate_regions(regions['chinese'],regions['source'] if self.enable_source.isChecked() else None,self.width_pixels,self.height_pixels,mode='local')
            except ValueError as exc:error=str(exc)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not error and not self.busy)
        self.status.setText(error or '选区有效，点击「确认选区」即可保存。')
        return not error
    def manual_changed(self,role):
        self.origins[role]='user';self.selected.add(role);self.show_regions.setChecked(True);self._sync_boxes()
    def preview_box(self,role,box):
        if box is None:self._validate_draft();return
        other='source' if role=='chinese' else 'chinese'
        if other not in self.selected or not self.enable_source.isChecked():return
        b=[s.value() for s in self.controls[other]]
        if max(box[0],b[0])<min(box[2],b[2]) and max(box[1],b[1])<min(box[3],b[3]):
            self.status.setText('中文区域与原文区域不能重叠，请调整范围后再保存。')
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        else:self._validate_draft()
    def drawn(self,role,box):self.set_box(role,box,'user')
    def set_box(self,role,box,origin):
        if role=='source' and 'chinese' not in self.selected:
            self.status.setText('请先框选中文区域，再选择原文区域。');return
        self.selected.add(role);self.show_regions.setChecked(True)
        for control,value in zip(self.controls[role],box):control.blockSignals(True);control.setValue(value);control.blockSignals(False)
        self.origins[role]=origin;self._sync_boxes()
    def highlight(self,row):
        item=self.candidates.item(row);self.canvas.candidate=item.data(Qt.ItemDataRole.UserRole)['box'] if item else None;self.canvas.update()
    def assign_candidate(self,role):
        item=self.candidates.currentItem()
        if not item:self.status.setText('请先在上方列表中选择一个识别选区。');return
        self.set_box(role,item.data(Qt.ItemDataRole.UserRole)['box'],'candidate');self._validate_draft()
    def _busy(self,value):
        self.busy=value;self.detect.setEnabled(not value);self.load.setEnabled(not value);self.timeline.setEnabled(not value);self.seek.setEnabled(not value);self.cancel_job.setEnabled(value);self._validate_draft()
    def cancel_pending(self):
        self.serial+=1
        if self.job:self.bridge.jobs.cancel(self.job)
        self.status.setText('正在取消后台任务，请稍候…')
    def _start(self,launcher):
        if not self.valid():self.reject();return
        self.serial+=1;serial=self.serial;self._busy(True)
        def valid():return self.valid() and serial==self.serial
        def done(value,error):
            if not valid():return
            self.job=None;self._busy(False)
            if error:self.status.setText('已取消。' if isinstance(error,InterruptedError) else '操作失败：'+str(error));return
            return value
        try:return launcher(valid,done)
        except Exception as exc:self._busy(False);self.status.setText(str(exc))
    def propose(self):
        def launch(valid,done):
            self.status.setText('正在抽样画面自动探测字幕位置，请稍候（可随时取消）…')
            def completed(results,error):
                if not valid():return
                done(results,error)
                if error:return
                self.candidates.clear()
                for n,item in enumerate(results):
                    box=item.get('box',[])
                    if len(box)!=4 or any(type(x)!=int for x in box) or not 0<=box[0]<box[2]<=self.width_pixels or not 0<=box[1]<box[3]<=self.height_pixels:continue
                    candidate={'box':list(box),'origin':'candidate','confirmed':False}
                    self.candidates.addItem(f"选区 {n + 1} · 坐标 {box} · 检出 {item.get('hits', 0)} 次 · {item.get('reason', '待人工检查')}")
                    self.candidates.item(self.candidates.count()-1).setData(Qt.ItemDataRole.UserRole,candidate)
                self.status.setText('未探测到明显的字幕区域，可切换至有字幕的画面手动框选，或调整识别语言后重试。' if not self.candidates.count() else '已在画面中探测到备选区域，请在列表中选中并点击「设为中文区域」或「设为原文区域」。')
            self.job=self.bridge.qc_propose_regions(self.language.text(),completed,valid=valid)
        self._start(launch)
    def load_frame(self):
        def launch(valid,done):
            def loaded(image,stamp):
                if not valid():return
                done(None,None);self.canvas.image=image;self.canvas.update();self.actual.setText('画面时间 '+str(round(stamp,6)));self.seek.setValue(stamp)
            self.job=self.bridge.qc_preview_at(self.seek.value(),loaded,valid=valid,error_callback=lambda error:done(None,error))
        self._start(launch)
    def confirm(self):
        if not self._validate_draft() or self.busy:return
        if self.canvas.image is None:self.status.setText('尚未加载视频画面，无法保存选区。');return
        def launch(valid,done):
            zh={'box':[s.value() for s in self.controls['chinese']],'origin':self.origins['chinese'],'confirmed':True}
            src={'box':[s.value() for s in self.controls['source']],'origin':self.origins['source'],'confirmed':True} if self.enable_source.isChecked() else None
            def completed(error):
                if not valid():return
                done(None,error)
                if not error:self.accept()
            self.job=self.bridge.qc_confirm_regions(zh,src,completed,valid=valid)
        self._start(launch)
