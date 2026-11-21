"""Explicit, draft-only multi-ROI editor for the current decoded video frame."""
from uuid import uuid4
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QPushButton, QSpinBox, QCheckBox, QDialogButtonBox)
from lyricsync.multi_roi import validate_regions, MAX_REGIONS
from .roi_dialog import RoiCanvas
from .native_theme import apply_dialog_theme


class MultiCanvas(RoiCanvas):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#292330'))
        if self.image is None or self.image.isNull():
            painter.setPen(QColor('#ffffff'))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, '请先加载视频画面')
            return
        painter.drawImage(self.content_rect(), self.image)
        boxes = dict(self.boxes)
        if self._start is not None and self._end is not None:
            box = self.pixel_box(self._start, self._end)
            if box:
                boxes[self.role] = box
        for n, (identity, box) in enumerate(boxes.items()):
            painter.setPen(QPen(QColor('#eac5ff' if identity == self.role else '#87d6d0'), 2, Qt.PenStyle.DashLine))
            rect = self.display_box(box)
            painter.drawRect(rect)
            painter.drawText(QPointF(rect.x()+4, rect.y()+16), str(n+1))


class MultiRegionDialog(QDialog):
    def __init__(self, bridge):
        super().__init__()
        self.bridge = bridge
        project = bridge.document.project
        self.document_id = bridge.document.id
        self.revision = bridge.document.revision
        self.video = project.inputs.get('video')
        self.width_pixels = project.video.get('width', 0)
        self.height_pixels = project.video.get('height', 0)
        self.image = bridge.media_controller._last_image
        if not self.video or not self.width_pixels or not self.height_pixels or self.image is None or self.image.isNull():
            raise ValueError('请先载入视频并等待画面就绪。')
        from lyricsync.multi_roi import project_regions
        self.regions = project_regions(project.settings, self.width_pixels, self.height_pixels)
        if not self.regions:
            box = project.settings.get('roi') or [0, round(self.height_pixels*.7), self.width_pixels, self.height_pixels]
            self.regions = [dict(id='roi-'+uuid4().hex[:12], box=list(box), enabled=True)]
        self.setWindowTitle('编辑 OCR 选区')
        self.resize(880, 680)
        root = QVBoxLayout(self)
        self.mode = QCheckBox('启用多选区 OCR；关闭后恢复原单选区模式')
        self.mode.setChecked(project.settings.get('ocr_multi_region') is True)
        root.addWidget(self.mode)
        notice = QLabel('最多 4 区，同一帧逐区识别；仅生成只读候选，不自动覆盖主轴。\n选中列表项后在画面拖动可重画该区，或用像素坐标精确调整。说话人和样式可在导入页的选区卡片设置。')
        notice.setWordWrap(True)
        root.addWidget(notice)
        self.canvas = MultiCanvas(self.width_pixels, self.height_pixels)
        self.canvas.image = self.image.copy()
        root.addWidget(self.canvas, 1)
        self.list = QListWidget()
        self.list.setMaximumHeight(90)
        root.addWidget(self.list)
        actions = QHBoxLayout()
        root.addLayout(actions)
        self.add = QPushButton('添加区域')
        self.remove = QPushButton('删除所选区域')
        self.enabled = QCheckBox('此区域参与识别')
        actions.addWidget(self.add); actions.addWidget(self.remove); actions.addWidget(self.enabled)
        coords = QHBoxLayout()
        root.addLayout(coords)
        self.spins = []
        for n, title in enumerate(('左 X', '上 Y', '右 X', '下 Y')):
            coords.addWidget(QLabel(title))
            spin = QSpinBox()
            spin.setRange(0, self.width_pixels if n%2 == 0 else self.height_pixels)
            coords.addWidget(spin)
            self.spins.append(spin)
            spin.valueChanged.connect(self.changed)
        self.status = QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('保存选区配置')
        root.addWidget(self.buttons)
        self.buttons.accepted.connect(self.commit); self.buttons.rejected.connect(self.reject)
        self.add.clicked.connect(self.add_region); self.remove.clicked.connect(self.remove_region)
        self.list.currentRowChanged.connect(self.selected)
        self.enabled.toggled.connect(self.changed)
        self.canvas.boxDrawn.connect(self.drawn)
        self.mode.toggled.connect(self.validate)
        self.refresh(0)
        apply_dialog_theme(self)

    def refresh(self, selected):
        self.list.blockSignals(True)
        self.list.clear()
        for n, region in enumerate(self.regions):
            self.list.addItem(f"区域 {n+1} · {'启用' if region['enabled'] else '停用'} · {region['box']}")
        self.list.blockSignals(False)
        self.list.setCurrentRow(min(selected, len(self.regions)-1))
        self.selected(self.list.currentRow())
        self.validate()

    def selected(self, row):
        if not 0 <= row < len(self.regions):
            return
        region = self.regions[row]
        self.canvas.role = region['id']
        for spin, value in zip(self.spins, region['box']):
            spin.blockSignals(True); spin.setValue(value); spin.blockSignals(False)
        self.enabled.blockSignals(True); self.enabled.setChecked(region['enabled']); self.enabled.blockSignals(False)
        self.canvas.boxes = {r['id']: r['box'] for r in self.regions}
        self.canvas.update()

    def changed(self, *_):
        row = self.list.currentRow()
        if not 0 <= row < len(self.regions):
            return
        self.regions[row]['box'] = [s.value() for s in self.spins]
        self.regions[row]['enabled'] = self.enabled.isChecked()
        self.refresh(row)

    def drawn(self, identity, box):
        row = self.list.currentRow()
        if row >= 0:
            self.regions[row]['box'] = box
            self.refresh(row)

    def add_region(self):
        if len(self.regions) >= MAX_REGIONS:
            return
        self.regions.append(dict(id='roi-'+uuid4().hex[:12], box=[0, 0, self.width_pixels, self.height_pixels], enabled=True))
        self.refresh(len(self.regions)-1)

    def remove_region(self):
        row = self.list.currentRow()
        if row >= 0 and len(self.regions) > 1:
            self.regions.pop(row)
            self.refresh(max(0, row-1))

    def validate(self, *_):
        error = ''
        try:
            validate_regions(self.regions, self.width_pixels, self.height_pixels)
        except ValueError as exc:
            if self.mode.isChecked():
                error = str(exc)
        self.status.setText(error or '配置仅在点击保存后生效。重叠区域只合并位置近乎相同且文字一致的检测；其他疑似重复请人工核对。')
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not error)
        self.add.setEnabled(len(self.regions) < MAX_REGIONS)
        self.remove.setEnabled(len(self.regions) > 1)
        return not error

    def commit(self):
        if not self.validate():
            return
        bridge = self.bridge
        if bridge.document.id != self.document_id or bridge.document.revision != self.revision or bridge.document.project.inputs.get('video') != self.video:
            self.status.setText('编辑期间工程已改变，未保存选区。请关闭后重新打开。')
            return
        from .roi_controls import store
        if store(bridge, self.regions, enabled=self.mode.isChecked()):
            self.accept()


def open_multi_roi(bridge):
    dialog = MultiRegionDialog(bridge)
    return dialog.exec() == QDialog.DialogCode.Accepted
