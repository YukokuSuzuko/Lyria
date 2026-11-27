"""Explicit local model management and given-lyrics alignment settings.

The dialog never edits the project. Explicit ASR creates only an independent
draft; its corrected text must be confirmed before the owner starts alignment.
Application-owned management tasks survive closing this dialog.
"""
from __future__ import annotations
from copy import deepcopy
import os
from pathlib import Path
import time
from PySide6.QtCore import Qt, QUrl, QEvent, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QProgressBar, QPushButton, QSpinBox, QTextBrowser, QVBoxLayout,
    QStyledItemDelegate, QTableWidget, QWidget, QTabWidget, QScrollArea, QFrame, QPlainTextEdit, QGroupBox, QToolButton, QSizePolicy)
from lyricsync.local_models import ModelManager, QWEN, UVR, ASR, NEXTFIRE, ALIGNMENT_MODELS, SEPARATION_MODELS, separation_spec, DOWNLOAD_SOURCES, default_cache, readable_bytes
from lyricsync.local_alignment import LANGUAGES, validate_options
from lyricsync.local_alignment_runtime import probe_runtime
from lyricsync.local_runtime_setup import create_runtime
from lyricsync.local_preparation import prepare_models
from . import carbon_widgets as c
from .native_theme import apply_dialog_theme

from .local_model_tasks import model_tasks
from .model_dialog_layout import ReadinessSummary, ResponsiveRow, wrapped_checkbox

# Session only: no persisted readiness claims across process restarts.
_READING_PROBES = {}

def _reading_key(python, tool):
    return (os.path.normcase(str(Path(python).expanduser().resolve())), tool) if python.strip() else ('',tool)

def _label(text):
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setWordWrap(True)
    result.setMinimumWidth(0)
    result.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return result

class _FixedStatusText(QPlainTextEdit):
    """Full selectable error text in a fixed, scrollable layout slot."""
    def __init__(self,text):
        super().__init__();self.setReadOnly(True);self.setPlainText(text)
        self.setFixedHeight(self.fontMetrics().height()*3+12)
        self.setMinimumWidth(0)
    def setText(self,text):self.setPlainText(str(text))
    def text(self):return self.toPlainText()

class _ReadingTable(QTableWidget):
    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange,QEvent.Type.ApplicationFontChange):
            self.verticalHeader().setDefaultSectionSize(max(36,self.fontMetrics().height()+14))

class _ReadingDelegate(QStyledItemDelegate):
    """Keep the table editor readable without inherited form-control padding."""
    def createEditor(self,parent,option,index):
        editor=super().createEditor(parent,option,index)
        if isinstance(editor,QLineEdit):
            editor.setFont(self.parent().font())
            editor.setStyleSheet('QLineEdit { padding: 0px 2px; margin: 0px; border: 1px solid #777; border-radius: 0px; font-size: '+str(self.parent().font().pointSizeF())+'pt; }')
            editor.setTextMargins(0,0,0,0)
        return editor
    def updateEditorGeometry(self,editor,option,index):
        editor.setGeometry(option.rect.adjusted(1,1,-1,-1))

class _StatusArea(QScrollArea):
    def sizeHint(self):
        hint = super().sizeHint()
        # Prefer several readable lines when space permits, but allow the
        # layout to shrink to one line without enlarging a short-screen dialog.
        hint.setHeight(self.maximumHeight())
        return hint

from .audio_preview import AudioPreview


def _format_time(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class _TimeSpinBox(QDoubleSpinBox):
    """Seconds internally, readable minute/second editor without silent truncation."""
    def textFromValue(self, value):
        whole = int(value)
        fraction = value - whole
        suffix = f"{whole % 60:02d}" if fraction < .0001 else f"{whole % 60 + fraction:06.3f}"
        return f"{whole // 60:02d}:{suffix}"

    def valueFromText(self, text):
        try:
            pieces = text.strip().split(":")
            return int(pieces[0]) * 60 + float(pieces[1]) if len(pieces) == 2 else float(text)
        except (ValueError, IndexError):
            return self.value()

    def validate(self, text, position):
        from PySide6.QtGui import QValidator
        import re
        if re.fullmatch(r"\d+:([0-5]\d)(\.\d{0,3})?", text):
            return QValidator.State.Acceptable, text, position
        if re.fullmatch(r"[0-9:.]*", text):
            return QValidator.State.Intermediate, text, position
        return QValidator.State.Invalid, text, position


class LocalAlignmentDialog(QDialog, AudioPreview):
    manage_scroll: QScrollArea

    def __init__(self, document, parent=None, default_audio="", *,
                 setup_only=False, initial_options=None):
        super().__init__(parent)
        self._live_document = document
        self.document = deepcopy(document)
        self.setup_only = setup_only
        self._closed = False
        self._bundled_probe_requests = set()
        self._service = model_tasks()
        self._transcript_draft = None
        self._init_preview()
        self._options = None
        initial = dict(initial_options or {})
        from .local_alignment_settings import load_source_state
        source_state = load_source_state(self.document.project)
        if source_state and not setup_only:
            initial.setdefault('text_source', source_state['text_source'])
        if self._service.task is not None or self._service.stop_reason == "pause":
            initial.update(self._service.context)
        # A completed preparation may finish while the window is closed.
        # Recover its environment for this cache without overriding a saved choice.
        completed = self._service.value
        if (not initial.get("runtime_python") and isinstance(completed, dict)
                and completed.get("runtime_python")
                and self._service.context.get("cache_root") == initial.get("cache_root", str(default_cache()))):
            initial["runtime_python"] = completed["runtime_python"]
        self.setObjectName("localAlignmentDialog")
        self.setWindowTitle("本地模型管理" if setup_only else "AI 自动打轴")
        avail = self.screen().availableGeometry() if self.screen() else None
        target_h = 740 if not avail else min(740, max(540, avail.height() - 36))
        self.resize(704, target_h)
        self._external_rows = deepcopy(initial.get('source_rows', source_state.get('external_rows', [])))
        self._split_pairs=deepcopy(source_state.get('split_pairs',[]))
        self._external_origin = initial.get('source_origin', source_state.get('origin', ''))
        self._reading_cache = {(row['id'], row['text']):deepcopy(row)
                               for row in initial.get('reading_rows', source_state.get('reading_rows', []))}
        self._adopted_draft = None
        self._reading_rows = []
        self._auto_reading_request = None
        self._browse_buttons = []
        root = c.layout(self)
        self.readiness = ReadinessSummary(self)
        self.readiness.hide()
        tabs = self.tabs = QTabWidget()
        tabs.setObjectName("modelWorkflowTabs")
        tabs.setUsesScrollButtons(True)
        tabs.setDocumentMode(False)
        tabs.tabBar().setExpanding(False)
        tabs.tabBar().setDrawBase(False)
        root.addWidget(tabs, 1)
        self.audio = QLineEdit(initial.get("audio_path", default_audio))
        self.cache = QLineEdit(initial.get("cache_root", str(default_cache())))
        runtime_value=initial.get("runtime_python", os.environ.get("LYRIA_ALIGNMENT_PYTHON", ""))
        if not runtime_value:
            from lyricsync.shared_runtime import discover_shared_runtime
            try:runtime_value=str(discover_shared_runtime() or '')
            except (OSError,ValueError):runtime_value=''  # Invalid bundle is not advertised as ready.
        self.runtime = QLineEdit(runtime_value)
        self.reading_runtime = QLineEdit(initial.get("reading_runtime_python", ""))
        self.reading_runtime.setPlaceholderText("留空沿用模型 Python；可单独选择无 Torch 的注音环境")
        self.runtime.setPlaceholderText("Python 3.12 独立环境的 Scripts\\python.exe 路径")
        self.language = QComboBox()
        for title, code in (("日语", "Japanese"), ("英语", "English"), ("中文", "Chinese"), ("粤语", "Cantonese"), ("韩语", "Korean")):
            self.language.addItem(title, code)
        self.language.setCurrentIndex(max(0, self.language.findData(initial.get("language", "Japanese"))))
        self.device = QComboBox()
        self.device.addItem("显卡加速（CUDA / ROCm）", "cuda:0")
        self.device.addItem("CPU", "cpu")
        from lyricsync.split_profiles import ACTIVE
        if ACTIVE == 'amd':
            self.device.setItemText(0, "显卡加速（ROCm + DirectML）")
        elif ACTIVE == 'nvidia':
            self.device.setItemText(0, "显卡加速（CUDA）")
        self.device.setCurrentIndex(1 if initial.get("device") == "cpu" or ACTIVE not in ('nvidia', 'amd') else 0)
        self.alignment_model = QComboBox()
        self.alignment_model.addItem("NextFire 日语卡拉 OK 对时（默认推荐）", NEXTFIRE.id)
        self.alignment_model.addItem("Qwen3 原词对时", QWEN.id)
        self.alignment_model.setCurrentIndex(max(0, self.alignment_model.findData(initial.get("model_id", NEXTFIRE.id))))
        self.download_source = QComboBox()
        for source_id in ("mirror", "official", "modelscope", "huggingface"):
            self.download_source.addItem(DOWNLOAD_SOURCES[source_id]["label"], source_id)
        raw_src = initial.get("download_source", "mirror")
        self.download_source.setCurrentIndex(max(0, self.download_source.findData(raw_src if raw_src in DOWNLOAD_SOURCES else "mirror")))
        self.source_details = _label("")
        self.source_details.setParent(self);self.source_details.hide()
        self.download_source.currentIndexChanged.connect(self._update_source_details)
        self._update_source_details()
        self.separation_model = QComboBox()
        self.separation_model.setObjectName("separationModelSelector")
        for spec in SEPARATION_MODELS.values():
            self.separation_model.addItem(spec.files[0].name.removesuffix('.onnx'), spec.id)
        self.separation_model.setCurrentIndex(max(0, self.separation_model.findData(initial.get('separation_model_id', UVR.id))))
        self.separate = QCheckBox("先分离人声（可选，较慢）")
        self.separate.setObjectName("useVocalSeparation")
        self.separate.setChecked(initial.get("use_separation", False) in (True, "true"))
        check_icon = (Path(__file__).parent / "assets/icons/check-white.svg").as_posix()
        self.separate.setStyleSheet("QCheckBox::indicator { width: 18px; height: 18px; border-radius: 4px; border: 1px solid #6C607B; background: white; }"
                                   f"QCheckBox::indicator:checked {{ background: #62408D; image: url('{check_icon}'); }}")
        from .native_theme import TextSourceCombo
        self.text_source = TextSourceCombo()
        self.text_source.addItem("工程中已导入的演唱原词", "reference")
        self.text_source.addItem("外部原词 / 剪贴板副本（保留复用）", "external")
        self.text_source.addItem("还没有原词？听写初稿", "draft")
        self.text_source.addItem("当前时间轴显示文本（可能是译文）", "main")
        if initial.get("text_source")=="external" and not self._external_rows and self.document.project.lyrics:
            initial["text_source"]="reference"
        default_source = "reference" if self.document.project.lyrics else "external"
        if initial.get("text_source") == "reference" and not self.document.project.lyrics:
            initial["text_source"] = "external"
        self.text_source.setCurrentIndex(max(0, self.text_source.findData(initial.get("text_source", default_source))))
        self.main_source_confirm=QCheckBox("我确认当前时间轴文本就是实际演唱原词")
        self._confirmed_main = None
        self.main_source_confirm.setChecked(False)
        self.main_source_confirm.toggled.connect(self._confirm_main)
        self.source_role = _label("")
        self.source_role.setMinimumHeight(18)
        self.source_preview = c.NumberedLyrics()
        self.source_preview.setReadOnly(True)
        self.source_preview.setFixedHeight(140)
        self.source_preview.setPlaceholderText('原词预览（可滚动查看全部句子）')
        self.language_warning = _label("")
        self.language_warning.setStyleSheet("color: #b58b21;")
        self.external_button = QPushButton("选择歌词文件")
        self.external_button.clicked.connect(self._choose_external)
        self.paste_button = QPushButton("从剪贴板粘贴歌词")
        self.paste_button.clicked.connect(self._paste_lyrics)
        self.edit_lyrics_button=QPushButton("编辑原词与断句建议")
        self.edit_lyrics_button.clicked.connect(self._edit_lyrics)
        count = max(1, len(document.project.mappings))
        self.first, self.last = QSpinBox(), QSpinBox()
        for box in (self.first, self.last): box.setRange(1, count)
        self.first.setValue(1); self.last.setValue(count)
        selected = initial.get("sentence_ids") or []
        positions = [i+1 for i,row in enumerate(document.project.mappings) if row.id in selected]
        if positions: self.first.setValue(min(positions)); self.last.setValue(max(positions))
        self.offset, self.duration = _TimeSpinBox(), _TimeSpinBox()
        self.offset.setRange(0, 86400 * 365); self.duration.setRange(.001, 86400 * 365); self.offset.setDecimals(3); self.duration.setDecimals(3)
        raw_duration = document.project.video.get("duration", 0)
        media_duration = float(raw_duration) if isinstance(raw_duration, (int,float)) and raw_duration > 0 else 0
        self.offset.setValue(initial.get("offset_seconds", 0.0))
        self.duration.setValue(initial.get("duration_seconds") or media_duration or .001)
        self.range_required = False
        self.range_summary = _label('默认处理整首歌曲；内部按模型与资源分段，不要求手动切片。')
        self.custom_range = QCheckBox("自定义打轴片段")
        self.custom_range.setObjectName("customAlignmentRange"); self.custom_range.setChecked(initial.get("duration_seconds") is not None or bool(initial.get("offset_seconds")) or bool(positions and len(positions) < count))
        self.range_body = QWidget()
        range_form = QFormLayout(self.range_body)
        range_form.addRow("开始时间（分:秒）", self.offset)
        range_form.addRow("处理时长（分:秒）", self.duration)
        range_form.addRow("时间轴起始句", self.first); range_form.addRow("时间轴结束句", self.last)
        self.range_body.setVisible(self.custom_range.isChecked())
        self.custom_range.toggled.connect(self.range_body.setVisible)
        self.confirm = QCheckBox(self); self.confirm.hide() # legacy API only; never a launch gate
        from PySide6.QtWidgets import QGridLayout
        settings=QWidget();settings.setObjectName('carbonBody')
        stack=c.layout(settings,margins=(18,12,18,12),space=10)
        media=c.card(stack);head=c.band(media,(14,9,14,9))
        head.addWidget(c.Glyph('media'))
        names=QVBoxLayout();names.setSpacing(2);head.addLayout(names,1)
        self.media_name=c.Elide('','title');self.media_folder=c.Elide('',middle=True)
        names.addWidget(self.media_name);names.addWidget(self.media_folder)
        link=self._with_browse(self.audio)
        select=link.takeAt(1).widget();select.setText('更换…');head.addWidget(select)
        link.takeAt(0);media.addWidget(self.audio);self.audio.hide()
        def display_media():
            raw=self.audio.text();path=Path(raw)
            self.media_name.setValue(path.name if raw else '尚未选择媒体')
            self.media_folder.setValue(str(path.parent) if raw else '')
        self.audio.textChanged.connect(display_media);display_media()
        c.line(media)
        controls=QWidget();media.addWidget(controls);grid=QGridLayout(controls)
        grid.setContentsMargins(14,9,14,12);grid.setSpacing(5)
        grid.setColumnStretch(0,4);grid.setColumnStretch(1,5)
        grid.addWidget(c.label('演唱语言','muted'),0,0)
        def field_header(title,status=None):
            w=QWidget();row=c.layout(w,False,space=3)
            row.addWidget(c.label(title,'muted'));row.addStretch()
            if status is not None:row.addWidget(status)
            return w
        self.alignment_presence=c.label('','verified');self.separation_presence=c.label('','verified')
        grid.addWidget(field_header('对时模型',self.alignment_presence),0,1)
        grid.addWidget(self.language,1,0);grid.addWidget(self.alignment_model,1,1)
        grid.addWidget(field_header('伴奏处理',c.label('可选','muted')),2,1)
        self.separate.setText('先分离人声');self.separate.setToolTip('先分离人声，再对时。此步骤会增加耗时。')
        separate_box=QFrame();separate_box.setObjectName('separateBox');separate_box.setFixedHeight(32)
        sb_layout=c.layout(separate_box,False,(10,0,10,0),6)
        sb_layout.addWidget(self.separate,1)
        sb_layout.addWidget(c.label('复杂配乐推荐','muted'))
        grid.addWidget(separate_box,3,1)
        self.separation_fields=QWidget();sf=c.layout(self.separation_fields,False,space=8)
        sf.addWidget(c.label('人声分离模型','muted'))
        if not setup_only:sf.addWidget(self.separation_model,1)
        sf.addWidget(self.separation_presence);grid.addWidget(self.separation_fields,4,0,1,2)
        self.separation_fields.setVisible(not setup_only and self.separate.isChecked())
        self.manage_link=c.button('模型与环境…',lambda:self.tabs.setCurrentWidget(self.manage_scroll))
        self.manage_link.setParent(self);self.manage_link.hide()
        lyric=c.card(stack);src_head=c.band(lyric,(13,8,13,8),7)
        src_head.addWidget(c.label('演唱原词','title'));src_head.addWidget(self.text_source)
        src_head.addStretch();self.lyric_count=c.label('','muted');src_head.addWidget(self.lyric_count)
        self.review_readings=c.button('核对读音',role='link');self.review_readings.setCheckable(True)
        src_head.addWidget(self.review_readings);c.line(lyric)
        lyric.addWidget(self.main_source_confirm);preview=QWidget();pv=c.layout(preview,margins=(13,9,13,9));lyric.addWidget(preview)
        pv.addWidget(self.source_preview);lyric.addWidget(self.language_warning)
        c.line(lyric);src_foot=c.band(lyric,(13,4,13,4),3)
        self.source_role.setProperty('styleRole','muted');src_foot.addWidget(self.source_role,1)
        self.external_button.setText('另选歌词文件…');self.external_button.setProperty('styleRole','link');src_foot.addWidget(self.external_button)
        more,source_menu=c.menu_button('更多 ▾','quiet','lyricSourceMenu');src_foot.addWidget(more)
        self.split_bilingual=QPushButton('双语原词 / 译文分流…');self.split_bilingual.clicked.connect(self._split_bilingual)
        self.move_translations=QPushButton('将疑似译文移至译文池…');self.move_translations.clicked.connect(self._move_suspected_translations)
        for button in (self.paste_button,self.edit_lyrics_button,self.split_bilingual,self.move_translations):
            button.setParent(self);button.hide()
            action=source_menu.addAction(button.text(),button.click)
            source_menu.aboutToShow.connect(lambda b=button,a=action:a.setEnabled(b.isEnabled()))
        path_toggle=source_menu.addAction('编辑媒体路径');path_toggle.setCheckable(True);path_toggle.toggled.connect(self.audio.setVisible)
        advanced_toggle=source_menu.addAction('片段与高级选项');advanced_toggle.setCheckable(True)


        self._diagnostic_export_path=''
        self.diagnostic_export=QPushButton('本次导出完整诊断…')
        self.diagnostic_export.setToolTip('含完整歌词、媒体路径和模型裸输出；默认不导出、不上传，仅保存到你明确选择的位置。')
        self.diagnostic_export.clicked.connect(self._choose_diagnostic_export)
        self.diagnostic_clear=QPushButton('取消本次诊断导出');self.diagnostic_clear.clicked.connect(self._clear_diagnostic_export)
        diagnostic_row=ResponsiveRow();diagnostic_row.addWidget(self.diagnostic_export);diagnostic_row.addWidget(self.diagnostic_clear)
        # Diagnostic export belongs to task logs, not between lyrics and readings.
        from PySide6.QtWidgets import QTableWidget, QHeaderView
        self.reading_group=QGroupBox("日语读音（原词不变，可逐句修正罗马字）")
        reading_box=QVBoxLayout(self.reading_group)
        reading_box.addWidget(_label("自动读音可能读错人名或歌词特殊发音，影响对齐。已有注音优先保留；请检查下表。"))
        self.reading_tool = QComboBox(); self.reading_tool.setObjectName("readingToolSelector")
        self.reading_tool.addItem("pykakasi（轻量词典转写）", "pykakasi")
        self.reading_tool.addItem("Sudachi（日语分词词典）", "sudachi")
        self.reading_tool.addItem("仅使用已有 / 手填读音", "manual")
        self._reading_ready={}
        self.manage_reading_tool=self.reading_tool
        self.reading_tool.setCurrentIndex(max(0, self.reading_tool.findData(initial.get("reading_tool", "sudachi"))))
        self.reading_badge=c.label('待检测','verified')
        self.reading_badge.setSizePolicy(QSizePolicy.Policy.Fixed,QSizePolicy.Policy.Preferred)
        self.reading_badge.setFixedWidth(self.reading_badge.fontMetrics().horizontalAdvance('✓ 已可用')+24)
        reading_selector=QWidget();selector_layout=QHBoxLayout(reading_selector);selector_layout.setContentsMargins(0,0,0,0)
        selector_layout.addWidget(self.reading_tool,1)
        grid.addWidget(field_header("注音工具",self.reading_badge),2,0);grid.addWidget(reading_selector,3,0)
        self.reading_model_note = _label("")
        reading_box.addWidget(self.reading_model_note)
        self.reading_table=_ReadingTable(0,4); self.reading_table.setHorizontalHeaderLabels(["原词","读音","输入检查 / 处理说明","确认无误"])
        self.reading_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.reading_table.horizontalHeader().setMinimumHeight(self.reading_table.fontMetrics().height()+20)
        self.reading_table.setVerticalScrollMode(QTableWidget.ScrollMode.ScrollPerItem)
        self.reading_table.setMinimumHeight(140);self.reading_table.setMaximumHeight(220)
        self.reading_table.setItemDelegateForColumn(1,_ReadingDelegate(self.reading_table))
        self.reading_table.itemChanged.connect(self._reading_changed)
        self.reading_table.verticalHeader().setDefaultSectionSize(max(36,self.reading_table.fontMetrics().height()+14))
        reading_box.addWidget(self.reading_table)
        self.reading_confirm_all=QPushButton("确认全部待核对读音")
        self.reading_confirm_all.clicked.connect(self._confirm_all_readings)
        self.reading_reset=QPushButton("全部重置为自动读音…")
        self.reading_reset.clicked.connect(self._reset_readings)
        self.reading_reset_undo=QPushButton("撤销上次读音重置")
        self.reading_reset_undo.setEnabled(False)
        self.reading_reset_undo.clicked.connect(self._undo_reading_reset)
        confirmation_row=ResponsiveRow();confirmation_row.addWidget(self.reading_confirm_all);confirmation_row.addWidget(self.reading_reset);confirmation_row.addWidget(self.reading_reset_undo)
        reading_box.addWidget(confirmation_row)
        self.reading_button=QPushButton("自动补齐日语读音")
        self.reading_button.clicked.connect(self._generate_readings)
        self.reading_scan=QPushButton("检查全曲读音输入与符号")
        self.reading_scan.clicked.connect(self._scan_reading_inputs)
        self.reading_repair=QPushButton('读音依赖说明 / 修复')
        self.reading_repair.clicked.connect(self._reading_dependency_help)
        reading_actions=ResponsiveRow()
        for action in (self.reading_button,self.reading_scan,self.reading_repair):reading_actions.addWidget(action)
        reading_box.addWidget(reading_actions)
        stack.addWidget(self.reading_group);self.reading_group.hide()
        def review(checked):
            self.reading_group.setVisible(checked)
            if checked:self.settings_scroll.ensureWidgetVisible(self.reading_group);self.reading_table.setFocus()
        self.review_readings.toggled.connect(review)
        advanced=QWidget();sform=QFormLayout(advanced);stack.addWidget(advanced);advanced.hide()
        advanced_toggle.toggled.connect(advanced.setVisible)
        self.tail_silence_guard=QCheckBox('句尾静音裁短保护（实验，保留原始时间证据）')
        self.tail_silence_guard.setChecked(initial.get('tail_silence_guard') is True)
        sform.addRow(self.tail_silence_guard)
        segment_options = QWidget(); segment_options.setObjectName("alignmentSegmentOptions")
        options_row = QHBoxLayout(segment_options); options_row.setContentsMargins(0, 0, 0, 0)
        options_row.addWidget(self.custom_range); options_row.addStretch(1)
        sform.addRow(self.range_summary); sform.addRow(segment_options); sform.addRow(self.range_body)
        sform.addRow(_label("日语原词建议保留已有注音。缺少读音会影响模型对时；请核对读音与原词。难以匹配的句子保留待人工检查。"))
        stack.addStretch(1)
        self.settings_page = settings
        settings_scroll = QScrollArea(); settings_scroll.setWidgetResizable(True); settings_scroll.setWidget(settings)
        self.settings_scroll = settings_scroll
        draft_page = QWidget(); draft_page.setObjectName('carbonBody')
        draft_layout = QVBoxLayout(draft_page); draft_layout.setContentsMargins(18, 14, 18, 14); draft_layout.setSpacing(10)
        draft_layout.addWidget(_label("按所选音频片段和演唱语言听写原词；结果需要试听校对，不会直接改写工程。"))
        self.asr_missing = _label("")
        draft_layout.addWidget(self.asr_missing)
        self.asr_install_link = QPushButton("立即下载听写模型")
        self.asr_install_link.clicked.connect(self._download_asr)
        draft_layout.addWidget(self.asr_install_link)
        self.asr_generate = QPushButton("开始听写歌词")
        self.asr_generate.clicked.connect(self._generate_asr)
        self.listen_original = QPushButton("播放原曲")
        self.listen_vocals = QPushButton("播放纯人声")
        self.stop_listen = QPushButton("停止")
        self.listen_original.clicked.connect(lambda: self._listen_draft(False))
        self.listen_vocals.clicked.connect(lambda: self._listen_draft(True))
        self.stop_listen.clicked.connect(self._stop_preview)
        listen = ResponsiveRow()
        for button in (self.asr_generate, self.listen_original, self.listen_vocals, self.stop_listen): listen.addWidget(button)
        draft_layout.addWidget(listen)
        self.draft_info = _label("暂无听写初稿。")
        draft_layout.addWidget(self.draft_info)
        self.split_draft=QPushButton('按停顿与空格自动断句')
        self.split_draft.clicked.connect(self._split_draft_text);draft_layout.addWidget(self.split_draft)
        self.draft_text = QPlainTextEdit()
        self.draft_text.setPlaceholderText("每行一句，结合试听修正歌词。")
        draft_layout.addWidget(self.draft_text, 1)
        self.draft_confirm = QCheckBox(self); self.draft_use = QCheckBox(self)
        self.draft_confirm.hide(); self.draft_use.hide()
        self.adopt_button = QPushButton("采用这份歌词并返回打轴")
        self.adopt_button.clicked.connect(self._adopt_draft)
        self.draft_text.textChanged.connect(self._invalidate_adoption)
        draft_layout.addWidget(self.adopt_button)
        self.draft_page = draft_page
        manage = QWidget(); manage.setObjectName('carbonBody')
        manage_layout = QVBoxLayout(manage); manage_layout.setContentsMargins(18, 14, 18, 14); manage_layout.setSpacing(10)
        self.preflight = _label("");self.preflight.setParent(self); self.preflight.hide()
        self.model_labels = {}; self.model_manage_buttons = {}
        self.download_button = QPushButton("下载模型"); self.import_button = QPushButton("本地导入"); self.verify_button = QPushButton("校验")
        self.separation_download = QPushButton("下载模型"); self.separation_import = QPushButton("本地导入"); self.separation_verify = QPushButton("校验")
        self.asr_download = QPushButton("下载模型"); self.asr_import = QPushButton("本地导入"); self.asr_verify = QPushButton("校验")
        self.download_button.clicked.connect(self._download); self.import_button.clicked.connect(self._import); self.verify_button.clicked.connect(self._verify)
        self.separation_download.clicked.connect(self._download_separation); self.separation_import.clicked.connect(self._import_separation)
        self.separation_verify.clicked.connect(lambda: self._model_task("verify", model_id=self.separation_model.currentData()))
        self.asr_download.clicked.connect(self._download_asr); self.asr_import.clicked.connect(self._import_asr)
        self.asr_verify.clicked.connect(lambda: self._model_task("verify", model_id=ASR.id))
        from PySide6.QtWidgets import QMenu
        for key,title,spec,buttons in (("alignment","歌词对时",QWEN,(self.download_button,self.import_button,self.verify_button)),("separation","人声分离",UVR,(self.separation_download,self.separation_import,self.separation_verify)),("asr","语音听写",ASR,(self.asr_download,self.asr_import,self.asr_verify))):
            card = QGroupBox(title); card.setObjectName("modelSetupCard")
            box = QVBoxLayout(card);box.setContentsMargins(5,2,5,3);box.setSpacing(4); label = _label(""); self.model_labels[key]=label; label.setFixedHeight(label.fontMetrics().height() * 3 + 4); box.addWidget(label)
            actions = ResponsiveRow(); actions.addWidget(buttons[0]); actions.addWidget(buttons[1])
            menu_button = QToolButton(); menu_button.setText("管理"); menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            menu = QMenu(menu_button); menu.addAction("校验模型", buttons[2].click); menu.addAction("重新导入", buttons[1].click);menu.addAction("下载源与人声分离模型",lambda:self.download_source_row.setVisible(True))
            menu_button.setMenu(menu); actions.addWidget(menu_button); self.model_manage_buttons[key]=menu_button
            buttons[2].setParent(self);buttons[2].hide(); box.addWidget(actions); manage_layout.addWidget(card)
        self.download_source_row = QWidget(); source_layout = QFormLayout(self.download_source_row);self.source_form=source_layout
        self.manage_alignment_model=QComboBox()
        for i in range(self.alignment_model.count()):self.manage_alignment_model.addItem(self.alignment_model.itemText(i),self.alignment_model.itemData(i))
        self.manage_alignment_model.setCurrentIndex(self.alignment_model.currentIndex())
        self.manage_alignment_model.currentIndexChanged.connect(self.alignment_model.setCurrentIndex)
        self.alignment_model.currentIndexChanged.connect(self.manage_alignment_model.setCurrentIndex)
        if setup_only:source_layout.addRow("对时模型",self.manage_alignment_model)
        else:
            self.manage_alignment_model.setParent(self);self.manage_alignment_model.hide()
            model_link=QPushButton("选择对时模型（自动打轴页）")
            model_link.clicked.connect(lambda:self.tabs.setCurrentIndex(0));source_layout.addRow(model_link)
        source_layout.addRow("对时模型下载源", self.download_source)
        if setup_only:
            source_layout.addRow("人声分离模型", self.separation_model)
        else:
            select_separation = QPushButton("选择人声分离模型（自动打轴页）")
            select_separation.clicked.connect(lambda: self.tabs.setCurrentWidget(self.settings_scroll))
            source_layout.addRow(select_separation)
        manage_layout.addWidget(self.download_source_row)
        runtime_card=QGroupBox("运行环境"); runtime_form=QFormLayout(runtime_card)
        runtime_form.addRow("加速方式",self.device)
        self.runtime_status=_FixedStatusText("尚未检测"); runtime_form.addRow(self.runtime_status)
        self.probe_button=QPushButton("检测环境与显卡"); self.probe_button.clicked.connect(self._probe); runtime_form.addRow(self.probe_button)
        manage_layout.addWidget(runtime_card)
        reading_card = QGroupBox("日语注音工具"); reading_layout = QVBoxLayout(reading_card)
        self.reading_tool_status = _FixedStatusText("SudachiSmall 主方案 · 尚未检测所选 Python 环境；不会自动回退旧工具")
        self.reading_advanced = QCheckBox("高级：自定义注音 Python")
        # QCheckBox text never wraps. Keep the actionable label short and the
        # complete scope note wrapped, including at inherited 10pt UI fonts.
        self.reading_advanced_note = _label("仅用于高级自定义注音环境，不改变模型/CUDA 环境。")
        self.reading_advanced_note.setObjectName("readingAdvancedScopeNote")
        self.reading_advanced.setChecked(bool(self.reading_runtime.text().strip()))
        reading_layout.addWidget(self.reading_advanced)
        reading_layout.addWidget(self.reading_advanced_note)
        reading_layout.addWidget(self.reading_runtime)
        self.reading_runtime.setVisible(self.reading_advanced.isChecked())
        self.reading_advanced.toggled.connect(self.reading_runtime.setVisible)
        self.reading_advanced.toggled.connect(lambda:self._busy_state())
        self.reading_runtime.setPlaceholderText("默认使用随程序内嵌 Sudachi；此处仅供高级自定义环境")
        if setup_only:reading_layout.addWidget(reading_selector)
        else:
            choose_tool=QPushButton('选择注音词典（自动打轴页）')
            choose_tool.clicked.connect(lambda:self.tabs.setCurrentIndex(0));reading_layout.addWidget(choose_tool)
        reading_layout.addWidget(self.reading_tool_status)
        reading_layout.addWidget(_label("只用于补充日语读音，不需要显卡或下载声学模型。已有读音优先保留。"))
        self.reading_probe = QPushButton("检测注音工具"); self.reading_probe.clicked.connect(self._probe_readings)
        self.reading_install = QPushButton("安装 / 修复所选注音工具"); self.reading_install.clicked.connect(self._install_readings)
        reading_actions = ResponsiveRow(); reading_actions.addWidget(self.reading_probe); reading_actions.addWidget(self.reading_install)
        self.reading_create = QPushButton("创建轻量注音环境"); self.reading_create.clicked.connect(self._create_reading_runtime)
        reading_actions.addWidget(self.reading_create)
        reading_layout.addWidget(reading_actions); manage_layout.addWidget(reading_card)
        self._reading_probe_result = None
        self.runtime.textChanged.connect(self._invalidate_reading_probe)
        self.reading_runtime.textChanged.connect(self._invalidate_reading_probe)
        self.reading_tool.currentIndexChanged.connect(self._reading_selection_changed)
        self.advanced_toggle=QToolButton(); self.advanced_toggle.setObjectName("modelAdvancedToggle")
        self.advanced_toggle.setCheckable(True); self.advanced_toggle.setText("高级路径与手动安装")
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        from PySide6.QtCore import QSize
        self.advanced_toggle.setIconSize(QSize(10,10)); self.advanced_toggle.setFixedHeight(30)
        self.advanced_toggle.setStyleSheet("QToolButton { padding: 4px 8px; text-align: left; } QToolButton::menu-indicator { width: 0px; }")
        self.advanced_body=QWidget(); self.advanced_body.setObjectName("modelAdvancedBody")
        advanced_form=QFormLayout(self.advanced_body)
        advanced_form.addRow("模型目录",self._with_browse(self.cache,directory=True))
        advanced_form.addRow("Python 环境",self._with_browse(self.runtime,executable=True))
        self.install_runtime_button=QPushButton("配置独立 Python 运行环境"); self.install_runtime_button.clicked.connect(self._install_runtime)
        self.prepare_button=QPushButton("准备 Qwen 环境与模型"); self.prepare_button.clicked.connect(self._prepare_all)
        self.prepare_asr=QCheckBox("同时准备听写模型")
        advanced_form.addRow(self.install_runtime_button); advanced_form.addRow(self.prepare_asr); advanced_form.addRow(self.prepare_button)
        self.advanced_body.hide()
        self.advanced_toggle.toggled.connect(lambda checked: (self.advanced_body.setVisible(checked),self.advanced_toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)))
        manage_layout.addWidget(self.advanced_toggle); manage_layout.addWidget(self.advanced_body); manage_layout.addStretch(1)
        manage_scroll=QScrollArea(); manage_scroll.setWidgetResizable(True); manage_scroll.setWidget(manage)
        self.manage_scroll=manage_scroll
        if not setup_only:
            tabs.addTab(settings_scroll,"自动打轴"); tabs.addTab(draft_page,"听写原词")
        else:
            settings_scroll.setParent(self);settings_scroll.hide()
            draft_page.setParent(self);draft_page.hide()
        tabs.addTab(manage_scroll,"模型与环境")
        help_page=QTextBrowser(); help_page.setOpenExternalLinks(True)
        help_page.setPlainText("模型与许可\n\n" + "\n\n".join(spec.id + "\n版本：" + spec.revision + "\n" + spec.source_url + "\n" + spec.license_url for spec in (QWEN,NEXTFIRE,UVR,ASR)) + "\n\n下载或导入后校验 SHA-256。模型不包含在编辑器中。UVR 模型遵守 MIT 并署名，感谢 Ultimate Vocal Remover 及模型原作者。韩语分词 soynlp 遵守 GPLv3；自动注音 pykakasi 遵守 GPL-3.0-or-later。NextFire 权重为 CC-BY-NC-SA-4.0，仅限非商业使用，须署名及相同方式共享。其他组件遵守各自许可。\n\n使用 Python 3.12 独立环境安装 requirements.local-alignment.txt；GPU 需匹配的 CUDA 版 PyTorch。既有环境默认仅检测；仅在明确确认后安装 / 修复 pykakasi，不覆盖整套运行环境。")
        help_page.append("\nModelScope 对时模型版本："+DOWNLOAD_SOURCES["modelscope"]["revision"])
        self.license_page=help_page
        tabs.addTab(help_page,"安装与许可")
        self.logs=QPlainTextEdit(); self.logs.setReadOnly(True)
        log_page=QWidget();log_page.setObjectName('carbonBody')
        log_layout=QVBoxLayout(log_page);log_layout.setContentsMargins(18,14,18,14);log_layout.setSpacing(10);log_layout.addWidget(diagnostic_row);log_layout.addWidget(self.logs)
        tabs.addTab(log_page,"任务日志")
        if not setup_only:
            # Keep the internal page indices and existing navigation dispatch.
            aux=QWidget();links=QHBoxLayout(aux);links.setContentsMargins(0,0,18,0);links.setSpacing(6)
            for page,label in ((manage_scroll,"模型与环境"),(log_page,"任务日志")):
                tabs.setTabVisible(tabs.indexOf(page),False)
                button=c.button(label,role="quiet")
                button.clicked.connect(lambda checked=False,p=page:tabs.setCurrentWidget(p));links.addWidget(button)
            tabs.setTabVisible(tabs.indexOf(help_page),False)
            license_button=QPushButton("安装与许可");license_button.clicked.connect(lambda:tabs.setCurrentWidget(help_page))
            manage_layout.insertWidget(manage_layout.count()-1,license_button)
            tabs.setCornerWidget(aux)
        status_wrap=QWidget();status_box=QVBoxLayout(status_wrap);status_box.setContentsMargins(18,4,18,8);status_box.setSpacing(6)
        self.status=c.ActivityText(); self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status.setContentsMargins(10,6,10,6)
        self.status_scroll=_StatusArea(); self.status_scroll.setObjectName("alignmentStatusBanner")
        self.status_scroll.setWidgetResizable(True); self.status_scroll.setWidget(self.status)
        self.status_scroll.setFrameShape(QFrame.Shape.NoFrame); self.status_scroll.setMaximumHeight(58)
        status_box.addWidget(self.status_scroll);self.status_scroll.hide()
        self.status.updated.connect(self.status_scroll.setVisible)
        self.progress=QProgressBar(); status_box.addWidget(self.progress)
        self.cancel_task=QPushButton("取消当前任务"); self.cancel_task.clicked.connect(self._cancel_task)
        self.pause_task=QPushButton("暂停下载"); self.pause_task.clicked.connect(lambda:self._service.stop("pause"))
        self.resume_task=QPushButton("继续下载"); self.resume_task.clicked.connect(self._resume_task)
        task_buttons=ResponsiveRow()
        for button in (self.pause_task,self.cancel_task,self.resume_task):task_buttons.addWidget(button)
        status_box.addWidget(task_buttons)
        root.addWidget(status_wrap)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("关闭" if setup_only else "开始自动打轴")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("关闭")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setToolTip("关闭窗口不取消正在进行的管理任务；如需停止，请先点取消当前任务。")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setVisible(not setup_only)
        self.buttons.setLayoutDirection(Qt.RightToLeft)
        self.buttons.button(QDialogButtonBox.Ok).setProperty('styleRole', 'primary')
        self.buttons.accepted.connect(self._accept); self.buttons.rejected.connect(self.reject); footer=c.footer(root)
        if not setup_only:footer.addWidget(c.label('已有主轴时先比对确认，保留原词与译文。','muted',True),1)
        else:footer.addStretch(1)
        footer.addWidget(self.buttons)
        for combo in self.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8);combo.setMinimumWidth(0)
        self.text_source.setMinimumContentsLength(16 if not setup_only else 8)
        self.text_source.setMinimumWidth(177 if not setup_only else 0)
        for form in self.findChildren(QFormLayout):
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        c.theme(self)
        self.setStyleSheet(self.styleSheet()+"\nQGroupBox#modelSetupCard { padding: 8px 6px 4px; margin-top: 10px; }\nQFrame#separateBox { background: #faf8fd; border: 1px solid #dfd7ee; border-radius: 7px; }\nQTextEdit#sourceLyricsPreview { background: #faf9fd; border: 1px solid #eae5f3; border-radius: 8px; }\nQScrollArea#alignmentStatusBanner { background: #f3eefb; border: 1px solid #e2d8f3; border-radius: 8px; }")
        if not setup_only:
            tabs.tabBar().setDrawBase(False)
            tabs.setStyleSheet("""
                QTabWidget#modelWorkflowTabs::pane { border: 0; border-top: 1px solid #e6e0f2; background: #f7f5fb; }
                QTabWidget#modelWorkflowTabs::tab-bar { left: 18px; }
                QTabBar { background: #eeebf4; border-radius: 8px; margin: 7px 0px; padding: 2px; }
                QTabWidget#modelWorkflowTabs QTabBar::tab { padding: 5px 14px; border: 1px solid transparent; border-radius: 6px; color: #736985; font-size: 12px; outline: none; }
                QTabWidget#modelWorkflowTabs QTabBar::tab:selected { background: #ffffff; color: #5e3b9e; border: 1px solid #e2daf0; font-weight: 600; }
                """)
            tabs.setContentsMargins(0, 0, 0, 0)
        tabs.setCurrentIndex(0)
        self.text_source.currentIndexChanged.connect(self._source_changed)
        self.alignment_model.currentIndexChanged.connect(self._preflight)
        self._service.progress.connect(self._progress)
        self._service.outcome.connect(self._outcome)
        self._service.finished.connect(self._finished)
        self._progress(self._service.percent, self._service.message)
        self._busy_state()
        for field in (self.audio, self.runtime, self.cache):
            field.textChanged.connect(self._preflight)
        self.separation_model.currentIndexChanged.connect(self._preflight)
        self.separate.toggled.connect(self._preflight)
        self.text_source.currentIndexChanged.connect(self._preflight)
        self.device.currentIndexChanged.connect(self._preflight)
        self.language.currentIndexChanged.connect(self._preflight)
        self.first.valueChanged.connect(self._preflight)
        self.last.valueChanged.connect(self._preflight)
        self.custom_range.toggled.connect(self._preflight)
        self._invalidate_reading_probe()
        self._preflight()
        retry_draft = initial.get("transcript_draft")
        if retry_draft and retry_draft.get("document_id") == self.document.id and retry_draft.get("revision") == self.document.revision:
            self._show_draft(retry_draft)
            self.draft_text.setPlainText(retry_draft["corrected_text"])
            self._adopt_draft()
        elif (not self._service.error and isinstance(self._service.value, dict)
                and self._service.value.get("kind") == "transcript_draft"):
            self._show_draft(self._service.value)
        elif (not self._service.error and self._service.action == 'readings'
              and isinstance(self._service.value, dict)
              and self._service.value.get('document_id') == self.document.id):
            self._outcome(self._service.value, '')

    def _with_browse(self, line, directory=False, executable=False):
        layout = QHBoxLayout()
        layout.addWidget(line)
        button = QPushButton("选择")
        def browse():
            if directory:
                value = QFileDialog.getExistingDirectory(self, '选择模型存放目录', line.text())
            else:
                value, _ = QFileDialog.getOpenFileName(self, '选择 Python 3.12 程序（python.exe）' if executable else '选择音频或视频文件',
                    line.text(), "Python (python.exe)" if executable else "媒体文件 (*.wav *.flac *.mp3 *.m4a *.ogg *.mp4 *.mkv *.webm);;所有文件 (*)")
            if value:
                line.setText(value)
        button.clicked.connect(browse)
        layout.addWidget(button)
        self._browse_buttons.append(button)
        return layout

    @property
    def _task(self):
        return self._service.task

    def _start_task(self, work, action="manage"):
        if self._service.start(work, self._raw_options(), action):
            self._busy_state()

    def _busy_state(self):
        busy = self._task is not None
        paused = self._service.stop_reason == "pause" and self._service.resume_work is not None
        # Task state changes availability, never geometry. Explicit tabs,
        # advanced paths and the requested custom-range toggle remain collapsible.
        line_height = max(1, self.status.fontMetrics().height())
        self.status_scroll.setFixedHeight(line_height * (3 if busy else 2) + 6)
        self.progress.setVisible(busy)
        self.cancel_task.setVisible(busy or paused)
        self.pause_task.setVisible(busy and self._service.action=="download")
        self.resume_task.setVisible(paused and not busy)
        self.main_source_confirm.setVisible(self.text_source.currentData()=="main" and not self._known_translation())
        if not self.setup_only:
            self.separation_fields.setVisible(self.separate.isChecked())
            self.separation_presence.setVisible(self.separate.isChecked())
        self.cancel_task.setEnabled(busy or paused)
        self.pause_task.setEnabled(busy and self._service.action == "download")
        self.resume_task.setEnabled(not busy and self._service.stop_reason == "pause"
                                    and self._service.resume_work is not None)
        self.download_source.setEnabled(not busy)
        self.separation_model.setEnabled(not busy and (self.setup_only or self.separate.isChecked()))
        self.prepare_asr.setEnabled(not busy and self.alignment_model.currentData() == QWEN.id)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.setup_only or not busy)
        for button in (self.download_button, self.import_button, self.verify_button, self.probe_button,
                       self.prepare_button, self.install_runtime_button, self.separation_download, self.separation_import,
                       self.separation_verify, self.asr_download, self.asr_import, self.asr_verify):
            button.setEnabled(not busy)
        self.asr_generate.setEnabled(not busy and not self.setup_only)
        nextfire = self.alignment_model.currentData() == NEXTFIRE.id
        self.reading_button.setEnabled(not busy and nextfire and self.reading_tool.currentData() in ('pykakasi','sudachi') and self._reading_ready.get(self.reading_tool.currentData(),False))
        from lyricsync.bundled_reading import BUNDLED_RUNTIME
        bundled = self._reading_python() == BUNDLED_RUNTIME
        self.reading_probe.setEnabled(not busy)
        self.reading_install.setEnabled(not busy and not bundled)
        self.reading_create.setEnabled(not busy and (not bundled or self.reading_advanced.isChecked()))
        self.manage_reading_tool.setEnabled(not busy)
        self.reading_scan.setEnabled(not busy and nextfire)
        self.reading_confirm_all.setEnabled(not busy and nextfire)
        self.reading_reset.setEnabled(not busy and nextfire)
        self.reading_reset_undo.setEnabled(not busy and nextfire and bool(getattr(self,'_reading_reset_snapshot',None)))
        self.reading_table.setEnabled(not busy and nextfire)
        self.main_source_confirm.setEnabled(not busy and self.text_source.currentData()=='main')
        self.reading_tool.setEnabled(not busy and (nextfire or self.setup_only))
        self.reading_badge.setEnabled(nextfire or self.setup_only)
        self.reading_group.setEnabled(nextfire)
        self.reading_group.setVisible(nextfire and self.review_readings.isChecked())
        self.review_readings.setVisible(nextfire)
        self.tail_silence_guard.setEnabled(not busy and nextfire)
        self.reading_repair.setEnabled(not busy and nextfire)
        self.alignment_model.setEnabled(not busy)
        self.manage_alignment_model.setEnabled(not busy)
        for widget in (self.audio, self.runtime, self.reading_runtime, self.cache, self.language, self.device,
                       self.text_source, self.separate, self.custom_range, self.offset, self.duration,
                       self.external_button, self.paste_button, self.edit_lyrics_button, self.split_bilingual, self.move_translations, self.diagnostic_export, self.diagnostic_clear, *self._browse_buttons,
                       *self.model_manage_buttons.values()):
            widget.setEnabled(not busy)
        self.first.setEnabled(not busy and self.text_source.currentData() == 'main')
        self.last.setEnabled(not busy and self.text_source.currentData() == 'main')
        self.prepare_button.setEnabled(not busy and self.alignment_model.currentData() == QWEN.id)
        for button in (self.download_button, self.separation_download, self.asr_download, self.asr_install_link):
            button.setEnabled(not busy and not bool(button.property('modelReady')))
        self.edit_lyrics_button.setEnabled(not busy and self.text_source.currentData() != 'draft')
        self.listen_original.setEnabled(not busy and not self.setup_only and Path(self.audio.text()).is_file())
        self.listen_vocals.setEnabled(not busy and not self.setup_only and bool((self._transcript_draft or {}).get("stem_path")))
        self.draft_text.setReadOnly(self.setup_only or busy)
        self.adopt_button.setEnabled(not self.setup_only and not busy)
        self.draft_use.setEnabled(not self.setup_only and not busy)

    def _source_rows(self):
        from lyricsync.local_alignment import request_rows
        source = self.text_source.currentData()
        if source == "external":
            return deepcopy(self._external_rows)
        if source == "draft":
            return deepcopy((self._adopted_draft or {}).get("rows", []))
        if source=='main' and self._known_translation():
            raise ValueError('所选时间轴包含明确标记的译文，请选择或导入演唱原词。')
        if source=='main' and not self.main_source_confirm.isChecked():
            raise ValueError('请明确确认当前时间轴是演唱原词，或导入 / 粘贴原词。原有译文完整保留。')
        return request_rows(self.document,
            [row.id for row in (self.document.project.mappings[self.first.value()-1:self.last.value()] if self.custom_range.isChecked() else self.document.project.mappings)], source,main_source_confirmed=self.main_source_confirm.isChecked())

    def _sync_readings(self, rows):
        from PySide6.QtWidgets import QTableWidgetItem
        signature=[(row["id"],row["text"]) for row in rows]
        if signature==[(row["id"],row["text"]) for row in self._reading_rows]:return
        for row in self._current_readings():
            self._reading_cache[(row['id'], row['text'])] = row
        self.reading_table.blockSignals(True)
        self._reading_rows=deepcopy(rows)
        self.reading_table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            original=QTableWidgetItem(row["text"]);original.setFlags(original.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.reading_table.setItem(i,0,original)
            remembered=self._reading_cache.get((row['id'], row['text']), row)
            for name in ('source_reading','reading_origin','reading_warning','reading_issues','generated_reading','reading_confirmed_for','alignment_units','reading_tool','reading_conversion'):
                if name in remembered:self._reading_rows[i][name]=deepcopy(remembered[name])
            self._reading_rows[i]['reading']=remembered.get('reading','')
            self.reading_table.setItem(i,1,QTableWidgetItem(remembered.get("reading","")))
            confirmed=QTableWidgetItem('确认无误');confirmed.setFlags((confirmed.flags() & ~Qt.ItemFlag.ItemIsEditable) | Qt.ItemFlag.ItemIsUserCheckable)
            confirmed.setCheckState(Qt.CheckState.Checked if remembered.get('reading_confirmed_for')==[row['text'],remembered.get('reading','')] else Qt.CheckState.Unchecked)
            self.reading_table.setItem(i,3,confirmed)
            note=QTableWidgetItem(remembered.get("reading_warning", "尚未检查")); note.setFlags(note.flags() & ~Qt.ItemFlag.ItemIsEditable)
            from .alignment_input import suspected_translation
            if suspected_translation(row['text']):
                from PySide6.QtGui import QColor
                note.setText('疑似译文：可移至译文池；不自动删除。 '+note.text())
                note.setBackground(QColor('#fff0ad'));original.setBackground(QColor('#fff0ad'))
            self.reading_table.setItem(i,2,note)
        self.reading_table.blockSignals(False)
        self.reading_table.scrollToTop()
        QTimer.singleShot(0,self._maybe_generate_readings)

    def _current_readings(self):
        result=deepcopy(self._reading_rows)
        for i,row in enumerate(result):
            item=self.reading_table.item(i,1)
            if item is not None:row["reading"]=item.text().strip()
            check=self.reading_table.item(i,3)
            changed=row.get('reading','')!=self._reading_rows[i].get('reading','')
            if changed or (check is not None and check.checkState()==Qt.CheckState.Checked):
                from lyricsync.experimental_nextfire_alignment import normalize_reading
                try:normalize_reading(row.get('reading',''))
                except ValueError:
                    row.pop('reading_confirmed_for',None)
                    if not row.get('reading','').strip():row.pop('reading_origin',None)
                else:
                    row['reading_confirmed_for']=[row['text'],row['reading']]
                    if changed:row['reading_origin']='manual'
            else:row.pop('reading_confirmed_for',None)
        return result

    def _reading_changed(self,*args):
        if not hasattr(self,'reading_confirm_all'):return
        rows=self._current_readings()
        count=sum(any(x.get('requires_review') for x in row.get('reading_issues',[])) and
                  row.get('reading_confirmed_for')!=[row['text'],row.get('reading','')] for row in rows)
        self.reading_confirm_all.setText(f'确认全部待核对项（共 {count} 句）')

    def _confirm_all_readings(self):
        from lyricsync.experimental_nextfire_alignment import normalize_reading
        count=0
        for i,row in enumerate(self._current_readings()):
            try:normalize_reading(row.get('reading',''))
            except ValueError:continue
            self.reading_table.item(i,3).setCheckState(Qt.CheckState.Checked);count+=1
        self._reading_changed()
        self.status.setText(f'已确认 {count} 句当前读音；原词和读音未改字。无效字符仍须修正。')

    def _reset_readings(self):
        if self._task:return
        if not self._reading_ready.get(self.reading_tool.currentData(),False):
            self.status.setText('请先检测所选注音工具；已保留全部手填读音。');return
        from lyricsync.bundled_reading import BUNDLED_RUNTIME
        from lyricsync.local_alignment_runtime import validate_runtime_path
        try:
            if self._reading_python()!=BUNDLED_RUNTIME:validate_runtime_path(self._reading_python())
        except ValueError as exc:
            self.status.setText(str(exc));return
        if self._question('重置读音','将清除本次原词的手填读音与确认状态，重新自动生成；原词和译文不变。是否继续？')!=QMessageBox.StandardButton.Yes:return
        self._generate_readings(reset=True)

    def _undo_reading_reset(self):
        if self._task:return
        saved=getattr(self,'_reading_reset_snapshot',None)
        if not saved:return
        if [(r['id'],r['text']) for r in saved]!=[(r['id'],r['text']) for r in self._reading_rows]:
            self.status.setText('原词已变化，不能恢复旧读音。');return
        if self._question('撤销读音重置','恢复上次重置前的全部读音与确认状态，将替换重置后填写的读音。是否继续？')!=QMessageBox.StandardButton.Yes:return
        from PySide6.QtWidgets import QTableWidgetItem
        self._reading_rows=deepcopy(saved)
        for i,row in enumerate(saved):
            self.reading_table.setItem(i,1,QTableWidgetItem(row.get('reading','')))
            self.reading_table.item(i,3).setCheckState(Qt.CheckState.Checked if row.get('reading_confirmed_for')==[row['text'],row.get('reading','')] else Qt.CheckState.Unchecked)
        self._reading_reset_snapshot=None
        self.reading_reset_undo.setEnabled(False)
        self._reading_changed()
        self.status.setText('已恢复重置前的读音与确认状态；原词未修改。')


    def source_state(self):
        from .local_alignment_settings import clean_source_state
        source=self.text_source.currentData()
        if source == 'draft':
            # An ASR adoption has its own media/revision validation and is not
            # converted to an unguarded reusable external lyric snapshot.
            source='external' if self._external_rows else 'reference' if self.document.project.lyrics else 'main'
        return clean_source_state(dict(version=1, text_source=source,
            external_rows=deepcopy(self._external_rows), origin=self._external_origin,split_pairs=deepcopy(self._split_pairs),
            reading_rows=self._current_readings()))

    def _scan_reading_inputs(self):
        from lyricsync.japanese_readings import scan_reading_inputs
        from lyricsync.japanese_readings import invalid_reading_rows
        result=scan_reading_inputs(self._current_readings())
        result['issues'].extend(invalid_reading_rows(result['rows']))
        self._show_reading_issues(result)

    def _show_reading_issues(self, result):
        from PySide6.QtWidgets import QTableWidgetItem
        issues = result.get('issues', [])
        by_id = {}
        for issue in issues:
            by_id.setdefault(issue.get('id'), []).append(issue)
        for i, row in enumerate(self._reading_rows):
            found = by_id.get(row['id'], [])
            text = '\n'.join(issue.get('message', issue.get('kind', '待核对')) for issue in found) or '输入符号正常；读音另需检查'
            item=QTableWidgetItem(text); item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setToolTip('\n\n'.join(str(issue.get('before',''))+' → '+str(issue.get('after','')) for issue in found))
            self.reading_table.setItem(i,2,item)
        self.status.setText(f'已检查全部 {len(self._reading_rows)} 句，共 {len(issues)} 项输入处理或待确认说明。原词和手填读音未修改；悬停说明可查看前后差异。')

    def _generate_readings(self, checked=False, *, reset=False):
        if self._task:return
        if not self._reading_ready.get(self.reading_tool.currentData(),False):
            self.status.setText('请先在模型与环境页检测所选注音工具；只有实际转换检查通过才会生成。');return
        try:
            from lyricsync.local_alignment_runtime import run_worker, validate_runtime_path
            from lyricsync.bundled_reading import BUNDLED_RUNTIME
            selected=self._reading_python()
            python=selected if selected==BUNDLED_RUNTIME else str(validate_runtime_path(selected))
            rows=self._source_rows();self._sync_readings(rows)
            request={"operation":"readings","rows":self._current_readings(),"reading_tool":self.reading_tool.currentData(),"reset_readings":reset}
            document_id=self.document.id
            def work(cancel, progress):
                result=run_worker(request,python,cancel=cancel,progress=progress)
                return dict(result, document_id=document_id, requested_rows=deepcopy(request['rows']), reset_readings=reset)
            self._start_task(work,action="readings")
        except Exception as exc:self.status.setText(str(exc))

    def _reading_dependency_help(self):
        self.tabs.setCurrentWidget(self.manage_scroll)
        self.status.setText('请在“日语注音工具”中检测，或点击安装 / 修复。也可以选用已有 / 手填读音，不会自动安装。')

    def _reading_selection_changed(self):
        tool=self.reading_tool.currentData()
        key=_reading_key(self._reading_python(),tool)
        cached=self._service.reading_probe_cache.get(key,_READING_PROBES.get(key,{}))
        if cached.get('ready') is True:self._reading_ready[tool]=True
        self.reading_badge.setText('✓ 已可用' if self._reading_ready.get(tool) else '手填读音' if tool=='manual' else '待检测')
        self.reading_tool_status.setText(('✓ '+tool+' 词典已可用；特殊人名或造词仍需核对。') if self._reading_ready.get(tool) else '仅使用已有 / 手填读音。' if tool=='manual' else tool+' 尚未检测所选 Python 环境，可检测或安装 / 修复。')
        self._busy_state()
        QTimer.singleShot(0,self._maybe_generate_readings)

    def _maybe_generate_readings(self):
        from shiboken6 import isValid
        if not isValid(self) or self._closed or not isValid(self.alignment_model):return
        if self._task:return
        from lyricsync.bundled_reading import BUNDLED_RUNTIME
        tool=self.reading_tool.currentData()
        if self._reading_python()==BUNDLED_RUNTIME and not self._reading_ready.get(tool):
            key=_reading_key(BUNDLED_RUNTIME,tool)
            if key not in self._bundled_probe_requests:
                self._bundled_probe_requests.add(key);self._probe_readings()
            return
        if self.setup_only or self.alignment_model.currentData()!=NEXTFIRE.id:return
        rows=self._current_readings();tool=self.reading_tool.currentData()
        from lyricsync.japanese_readings import pure_symbol_row
        missing=[r for r in rows if not r.get('reading','').strip() and not pure_symbol_row(r)]
        signature=(_reading_key(self._reading_python(),tool),tuple((r['id'],r['text']) for r in rows))
        if missing and self._reading_ready.get(tool) and signature!=self._auto_reading_request:
            self._auto_reading_request=signature;self._generate_readings()

    def _invalidate_reading_probe(self):
        self._reading_probe_result=None;self._reading_ready={}
        for tool in ('pykakasi','sudachi'):
            key=_reading_key(self._reading_python(),tool)
            cached=self._service.reading_probe_cache.get(key,_READING_PROBES.get(key,{}))
            if cached.get('ready') is True:self._reading_ready[tool]=True
        self.reading_tool_status.setText('词典状态随所选解释器与工具复用；路径改变后须重新检测。')
        self._reading_selection_changed()

    def _reading_python(self):
        custom=self.reading_runtime.text().strip()
        if custom:return custom
        from lyricsync.bundled_reading import BUNDLED_RUNTIME,bundled_available
        if self.reading_tool.currentData()=='sudachi' and bundled_available():
            return BUNDLED_RUNTIME
        return self.runtime.text()

    def _create_reading_runtime(self):
        from lyricsync.local_runtime_setup import create_reading_runtime
        if self._task:return
        target=QFileDialog.getExistingDirectory(self,'选择父目录，将新建 lyria-reading-runtime 子目录')
        if not target:return
        target=str(Path(target)/'lyria-reading-runtime')
        tool=self.manage_reading_tool.currentData();base=self.runtime.text()
        if self._question('创建独立注音环境','将在以下新目录建立 Python 3.12 注音环境，仅从官方 PyPI 安装所选工具，不安装 Torch，不修改现有模型环境。需要约 1 GB 空间。\n'+target+'\n工具：'+tool+'\n是否继续？')!=QMessageBox.StandardButton.Yes:return
        self._start_task(lambda cancel,progress:create_reading_runtime(base,target,tool=tool,cancel=cancel,progress=progress),action='reading_create')

    def _probe_readings(self):
        from lyricsync.local_runtime_setup import probe_reading_tool
        python=self._reading_python();tool=self.manage_reading_tool.currentData()
        self._start_task(lambda cancel,progress:probe_reading_tool(python,tool=tool,cancel=cancel,progress=progress),action='reading_probe')

    def _install_readings(self):
        from lyricsync.local_runtime_setup import install_reading_tool
        from lyricsync.local_alignment_runtime import validate_runtime_path
        try:python=str(validate_runtime_path(self._reading_python()))
        except ValueError as exc:self.status.setText(str(exc));return
        tool=self.manage_reading_tool.currentData()
        description=('SudachiPy 0.6.11 与 SudachiDict-small（Apache-2.0）' if tool=='sudachi' else 'pykakasi 2.3.0（GPL-3.0-or-later）')
        if self._question('安装日语注音工具','将在指定独立Python环境从官方PyPI安装 '+description+'，需要网络；不含声学模型、不改原词。\n'+python+'\n是否开始？')!=QMessageBox.StandardButton.Yes:return
        self._start_task(lambda cancel,progress:install_reading_tool(python,tool=tool,cancel=cancel,progress=progress),action='reading_install')

    def _known_translation(self):
        rows=self.document.project.mappings
        if self.custom_range.isChecked():rows=rows[self.first.value()-1:self.last.value()]
        return any(r.text_role=='translation' and r.translation_ids for r in rows)

    def _main_key(self,document):
        from lyricsync.editing import mapping_text
        rows=document.project.mappings
        if self.custom_range.isChecked():rows=rows[self.first.value()-1:self.last.value()]
        return (document.id,document.revision,tuple((r.id,r.text_role,mapping_text(document.project,r)) for r in rows))

    def _confirm_main(self,checked):
        self._confirmed_main=self._main_key(self.document) if checked else None
        self._preflight()

    def _source_changed(self):
        self.main_source_confirm.setChecked(False)
        if self.text_source.currentData() == "draft" and self._adopted_draft is None and not self.setup_only:
            self.tabs.setCurrentWidget(self.draft_page)
        self._preflight()

    def _choose_external(self):
        filename, _ = QFileDialog.getOpenFileName(self,"选择原词文件","","歌词 (*.txt *.lrc *.srt *.ass *.ssa);;所有文件 (*)")
        if not filename: return
        try:
            from lyricsync.subtitles import import_original
            rows, warnings = import_original(filename)
            self._external_rows = [{"id": "external-" + row.id, "text": row.text, "lyric_ids":[row.id], "reading":getattr(row,"reading",""), "reading_segments":deepcopy(getattr(row,"reading_segments",[]))} for row in rows]
            self._external_origin = str(Path(filename).resolve())
            self.text_source.setCurrentIndex(self.text_source.findData("external"))
            self._preflight()
            self.status.setText("已读取原词文件，仅用于独立打轴草稿。" + ("\n" + "\n".join(warnings) if warnings else ""))
        except Exception as exc: self.status.setText(str(exc))

    def _paste_lyrics(self):
        from PySide6.QtWidgets import QApplication
        import uuid
        text = QApplication.clipboard().text()
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            self.status.setText("剪贴板中没有歌词文本。"); return
        if len(text) > 24000 or len(lines) > 1000:
            self.status.setText("歌词超过本次处理容量，请缩短文本。"); return
        identity = uuid.uuid4().hex
        self._external_rows = [{"id":f"paste-{identity}-{i}","text":line} for i,line in enumerate(lines)]
        self._external_origin = '剪贴板原词副本'
        self.text_source.setCurrentIndex(self.text_source.findData("external"))
        self._preflight()
        self.status.setText("已粘贴原词，仅用于独立打轴草稿。" + ("未检测到换行，请先按句整理歌词。" if len(lines)==1 else ""))

    def _split_bilingual(self):
        from .alignment_input import partition
        try:rows=self._source_rows()
        except ValueError as exc:self.status.setText(str(exc));return
        box=QMessageBox(self);box.setWindowTitle('明确选择双语排列')
        box.setText('分流前请确认原词在前、译文在后。原译文字完整保留；开始打轴时保存入工程。')
        tab=box.addButton('每行 原词<TAB>译文',QMessageBox.ButtonRole.AcceptRole)
        alternating=box.addButton('奇数行原词 / 偶数行译文',QMessageBox.ButtonRole.AcceptRole)
        box.addButton('取消',QMessageBox.ButtonRole.RejectRole);box.exec()
        if box.clickedButton() not in (tab,alternating):return
        try:new_rows,pairs=partition([r['text'] for r in rows],'tab' if box.clickedButton() is tab else 'alternating')
        except ValueError as exc:self.status.setText(str(exc));return
        if self._question('确认原译分流','将保留 '+str(len(pairs))+' 对原词与译文。\n\n'+'\n'.join(p['source']+' → '+p['translation'] for p in pairs) )!=QMessageBox.StandardButton.Yes:return
        self._external_rows=new_rows;self._split_pairs.extend(pairs);self._external_origin='明确确认的双语分流'
        self.text_source.setCurrentIndex(self.text_source.findData('external'));self._preflight()

    def _move_suspected_translations(self):
        from .alignment_input import suspected_translation
        import uuid
        try:rows=self._source_rows()
        except ValueError as exc:self.status.setText(str(exc));return
        suspected=[r for r in rows if suspected_translation(r['text'])]
        if not suspected:self.status.setText('没有检测到疑似译文行；未改动。');return
        if self._question('保留文本并移出打轴输入','这些行将保留至译文池，不参与对齐。\n\n'+'\n'.join(r['text'] for r in suspected))!=QMessageBox.StandardButton.Yes:return
        ids={r['id'] for r in suspected}
        self._external_rows=[r for r in rows if r['id'] not in ids]
        self._split_pairs.extend(dict(translation_id='moved-translation-'+uuid.uuid4().hex,translation=r['text']) for r in suspected)
        self._external_origin='明确移出疑似译文后的原词'
        self.text_source.setCurrentIndex(self.text_source.findData('external'));self._preflight()

    def _edit_lyrics(self):
        try:rows=self._source_rows()
        except ValueError:rows=[]
        editor=QDialog(self);editor.setWindowTitle("原词编辑与断句建议");editor.resize(620,500)
        layout=QVBoxLayout(editor)
        layout.addWidget(_label("只编辑本次打轴的独立原词副本，不覆盖工程原文。断句仅为建议，请检查后采用。"))
        field=QPlainTextEdit();field.setPlainText("\n".join(row["text"] for row in rows));layout.addWidget(field,1)
        suggest=QPushButton("按句末标点建议换行")
        def propose():
            import re
            field.setPlainText(re.sub(r"([。！？!?])(?=[^\n])",r"\1\n",field.toPlainText()))
        suggest.clicked.connect(propose);layout.addWidget(suggest)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("采用这份原词")
        buttons.accepted.connect(editor.accept);buttons.rejected.connect(editor.reject);layout.addWidget(buttons)
        apply_dialog_theme(editor)
        if editor.exec()!=QDialog.DialogCode.Accepted:return
        import uuid
        lines=[line for line in field.toPlainText().splitlines() if line.strip()]
        if not lines or len(lines)>1000 or sum(map(len,lines))>24000:
            self.status.setText("原词为空或超过处理容量，未采用。");return
        identity=uuid.uuid4().hex
        self._external_rows=(deepcopy(rows) if lines == [row['text'] for row in rows] else
                             [{"id":f"edited-{identity}-{i}","text":text} for i,text in enumerate(lines)])
        self._external_origin = '手动编辑的原词副本'
        self.text_source.setCurrentIndex(self.text_source.findData("external"));self._preflight()

    def _invalidate_adoption(self):
        self._adopted_draft = None
        self.draft_use.setChecked(False)
        self.draft_confirm.setChecked(False)

    def _split_draft_text(self):
        from lyricsync.local_transcription import split_singing_text
        draft=self._transcript_draft or {}
        text=self.draft_text.toPlainText()
        parts=split_singing_text(text,chunks=draft.get('chunks',[]) if text==draft.get('text') else [],pauses=draft.get('pause_intervals',[]))
        self.draft_text.setPlainText('\n'.join(parts))
        self.status.setText(f'断句建议共 {len(parts)} 句；仅改听写草稿换行，未推断新时间、未改已有原词或译文。请试听核对。')

    def _adopt_draft(self):
        try:
            from lyricsync.local_transcription import confirm_transcript_draft
            if self._transcript_draft is None: raise ValueError("请先生成听写初稿。")
            self._adopted_draft = confirm_transcript_draft(self._transcript_draft,self.draft_text.toPlainText(),self.document,confirmed=True)
            self.draft_confirm.setChecked(True); self.draft_use.setChecked(True)
            self.text_source.setCurrentIndex(self.text_source.findData("draft"))
            self.tabs.setCurrentWidget(self.settings_scroll)
            self._preflight()
        except Exception as exc:self.status.setText(str(exc))

    def _preflight(self):
        source = self.text_source.currentData()
        if self.main_source_confirm.isChecked() and self._confirmed_main!=self._main_key(self.document):
            self.main_source_confirm.setChecked(False)
        self.external_button.setVisible(True)
        self.first.setEnabled(source == "main"); self.last.setEnabled(source == "main")
        try:
            rows=self._source_rows()
            description={'reference':'工程演唱原词；保持独立原文、读音及已有译文关联。',
                         'external':'外部原词副本：' + (self._external_origin or '尚未选择') + '。随工程保存后可复用。',
                         'main':'当前时间轴显示文本；可能包含译文或手改内容，请确认它是实际演唱文本。',
                         'draft':'已采用的听写原词；须先试听校对。'}[source]
            if source == 'reference' and not rows and self.document.project.translations:
                description += '已导入的字幕属于译文，不会自动作为演唱原词；如它就是演唱文本，可明确选用时间轴显示文本。'
            self.source_role.setText(description if rows else description+' 请选择文件、粘贴原词或前往听写。')
            self.source_preview.setPlainText('\n'.join(row['text'] for row in rows))
        except ValueError as exc:
            rows=[];self.source_role.setText(str(exc));self.source_preview.clear()
            if source == 'reference' and not self.document.project.lyrics and self.document.project.translations:
                self.source_role.setText('当前工程仅有译文，请导入或粘贴演唱原词（原有译文将完整保留）。若时间轴确为演唱文本，请选择它并明确勾选确认。')
        self.lyric_count.setText(f'共 {len(rows)} 句')
        self.source_preview.setVisible(bool(rows))
        self._sync_readings(rows)
        self.reading_model_note.setText('NextFire 使用独立日语读音；原词及已有手填读音不改变。' if self.alignment_model.currentData()==NEXTFIRE.id else
                                       'Qwen 直接使用演唱原词，不需要注音；此区域不参与 Qwen 启动检查。')
        self.prepare_button.setToolTip('仅用于 Qwen 一键准备；NextFire 可用上方下载或本地导入。')
        from .alignment_input import suspected_translation
        suspected=[str(i+1) for i,row in enumerate(rows) if suspected_translation(row['text'])]
        self.language_warning.setText('第 '+ '、'.join(suspected)+' 句疑似中文译文，可移入译文池；检测仅为提醒，不删除或猜测演唱语言。' if suspected else '')
        self.language_warning.setVisible(bool(suspected))
        self.language_warning.setFixedHeight(self.language_warning.fontMetrics().height() * 2 + 4)
        self.reading_model_note.setFixedHeight(self.reading_model_note.fontMetrics().height() * 2 + 4)
        self.asr_missing.setFixedHeight(self.asr_missing.fontMetrics().height() * 2 + 4)
        try:
            manager=ModelManager(self.cache.text())
            entries=(("alignment",ALIGNMENT_MODELS[self.alignment_model.currentData()],self.download_button,self.import_button,"根据原词定位句子时间"),("separation",separation_spec(self.separation_model.currentData()),self.separation_download,self.separation_import,"可选提取人声，辅助试听与对时"),("asr",ASR,self.asr_download,self.asr_import,"从音频听写原词初稿"))
            for key,spec,download,import_button,purpose in entries:
                state=manager.status(spec.id)
                ready=bool(state.get("verification_cached"))
                status="✓ 已校验 SHA-256" if ready else ("文件在位，待校验" if state.get("present") else "文件大小不符" if state.get("wrong_size") else "缺少文件")
                if key in ("alignment", "separation"):
                    label = self.alignment_presence if key == "alignment" else self.separation_presence
                    label.setText(("未启用 · " if key == "separation" and not self.separate.isChecked() else "") + status)
                    label.setToolTip(str(manager.path(spec.id))+' · '+status)
                    label.setProperty('styleRole','verified' if ready else 'muted')
                    label.style().unpolish(label);label.style().polish(label)
                name={QWEN.id:"Qwen3 歌词对时",NEXTFIRE.id:"NextFire 日语对时",ASR.id:"Qwen3 原词听写",UVR.id:"UVR HQ3 人声提取"}.get(spec.id,"UVR HQ1 人声提取")
                self.model_labels[key].setText(f"{name} · {readable_bytes(spec.size)} · {status}\n{purpose}")
                download.setProperty("modelReady", ready)
                download.setToolTip("已校验就绪，无需重复下载。" if ready else "下载并校验此模型。")
                self.model_manage_buttons[key].setVisible(True)
                if key=="asr":
                    self.asr_missing.setText("听写模型尚未校验就绪，请先安装或在模型与环境页校验。" if not ready else "")
                    self.asr_install_link.setProperty("modelReady", ready)
                    self.asr_install_link.setVisible(not ready)
            from lyricsync.local_alignment_runtime import validate_runtime_path
            try:validate_runtime_path(self.runtime.text()); runtime="环境路径有效，尚未检测"
            except Exception:runtime="尚未配置独立运行环境"
            checked=(not self._service.error and isinstance(self._service.value,dict) and bool(self._service.value.get("runtime")) and all(self._service.context.get(key)==value for key,value in (("runtime_python",self.runtime.text()),("device",self.device.currentData()),("language",self.language.currentData()))))
            self.runtime_status.setText("✓ 本次环境检测通过" if checked else runtime)
        except Exception as exc:
            for label in (self.runtime_status, self.alignment_presence, self.separation_presence):
                label.setText("状态检查失败：" + str(exc))
        self._busy_state()

    def _progress(self, percent, message):
        self.progress.setValue(percent)
        self.status.setText(message)
        self._busy_state()
        self.logs.setPlainText("\n".join(self._service.log))
        QTimer.singleShot(0,self._maybe_generate_readings)

    def _outcome(self, value, error):
        if self._service.action in ('reading_probe','reading_install','reading_create'):
            if self._service.context.get('effective_reading_python', self._service.context.get('reading_runtime_python') or self._service.context.get('runtime_python'))!=self._reading_python():
                self.status.setText('Python环境已改变，未采用旧探测。');return
            tool=(value or {}).get('reading_tool',{}).get('tool',self.manage_reading_tool.currentData()) if isinstance(value,dict) else self.manage_reading_tool.currentData()
            result=value.get('reading_tool',{}) if isinstance(value,dict) else {}
            ready=not error and bool(result.get('ready'))
            if ready and self._service.action=='reading_create':self.reading_runtime.setText(value['reading_runtime_python'])
            self._reading_ready[tool]=ready
            n=self.reading_tool.findData(tool)
            if n>=0:
                self.reading_tool.model().item(n).setEnabled(ready)
                _READING_PROBES[_reading_key(self._reading_python(),tool)]=deepcopy(result) if ready else {}
            self._reading_probe_result=value if ready else None
            self.reading_tool_status.setText('✓ '+tool+' 分词/转写词典已可用。特殊人名或造词仍需核对。' if ready else tool+' 不可用：'+str(error or '转换检查未通过，请安装 / 修复。'))
            self.reading_tool_status.setToolTip('\n'.join(
                label+'：'+str(result[key]) for key,label in (
                    ('tool','注音工具'),('version','工具版本'),('dictionary_version','词典版本'),
                    ('license','许可'),('bundled','内嵌词典'),('ready','转换检查通过'))
                if key in result))
            self.reading_badge.setText('✓ 已可用' if ready else '待修复')
            if ready:self.reading_tool.setCurrentIndex(n)
            self._busy_state();QTimer.singleShot(0,self._maybe_generate_readings);return
        if not error and self._service.action=="readings":
            from PySide6.QtWidgets import QTableWidgetItem
            rows=value.get("rows",[]) if isinstance(value,dict) else []
            if not isinstance(value, dict) or value.get('document_id') != self.document.id:
                return
            requested=value.get('requested_rows', [])
            if [(r.get("id"),r.get("text")) for r in rows] != [(r["id"],r["text"]) for r in self._reading_rows]:
                self.status.setText("原词已变动，未采用过时读音。");return
            if [(r.get('id'),r.get('text')) for r in requested] != [(r['id'],r['text']) for r in rows]:
                self.status.setText('读音任务来源校验不符，未回填。');return
            current=self._current_readings()
            resetting=bool(value.get('reset_readings'))
            if resetting:self._reading_reset_snapshot=deepcopy(current)
            retained=0
            for i,(row,before,live) in enumerate(zip(rows,requested,current)):
                if live.get('reading','') != before.get('reading','') or (not resetting and bool(live.get('reading','').strip()) and (live.get('reading_origin')=='manual' or live.get('reading_confirmed_for')==[live['text'],live.get('reading','')])):
                    retained += 1
                    continue
                if not isinstance(row.get('reading'),str):
                    continue
                self.reading_table.setItem(i,1,QTableWidgetItem(row['reading']))
                if resetting:
                    self._reading_rows[i]=deepcopy(row)
                    self.reading_table.item(i,3).setCheckState(Qt.CheckState.Unchecked)
                self._reading_rows[i]['reading']=row['reading']
                for name in ('source_reading','reading_origin','reading_warning','reading_issues','generated_reading','reading_confirmed_for','alignment_units','reading_tool','reading_conversion'):
                    if name in row:self._reading_rows[i][name]=deepcopy(row[name])
            if resetting:self.reading_reset_undo.setEnabled(True)
            from lyricsync.japanese_readings import invalid_reading_rows
            self._show_reading_issues(dict(issues=[dict(issue,id=row['id']) for row in rows for issue in row.get('reading_issues', [])]+invalid_reading_rows(self._current_readings())))
            from lyricsync.japanese_readings import invalid_reading_rows
            invalid=[str(x['index']) for x in invalid_reading_rows(self._current_readings())]
            message = ('读音任务已结束，但第 ' + '、'.join(invalid[:20]) + ' 句仍无有效读音或含不支持字符，请手动核对。' if invalid else "已补齐读音。请核对并修改，再开始自动打轴。")
            self.status.setText(message + (f'保留了任务运行期间手改的 {retained} 句读音。' if retained else ''));return
        if isinstance(value, dict) and value.get("runtime_python"):
            self.runtime.setText(value["runtime_python"])
        if error:
            self.status.setText(error)
            self.reading_repair.setToolTip('可在模型与环境页检测或修复注音依赖。')
        elif isinstance(value, dict):
            if value.get("kind") == "transcript_draft":
                self._show_draft(value)
                return
            if value.get("runtime_python"):
                self.runtime.setText(value["runtime_python"])
            self.status.setText(self._action_result_message(value))
        else:
            self.status.setText(self._action_result_message(value))

    def _action_result_message(self, value):
        names = {'probe':'环境与显卡检测', 'install_runtime':'独立运行环境配置',
                 'prepare':'Qwen 环境与模型准备', 'download':'模型下载与校验',
                 'import_folder':'本地模型导入与校验', 'verify':'模型完整性校验',
                 'reading_probe':'注音工具检测', 'reading_install':'注音工具安装',
                 'readings':'日语读音生成', 'asr':'原词听写', 'manage':'管理操作'}
        label = names.get(self._service.action, '当前操作')
        if self._service.stop_reason == 'pause':
            return label + '已暂停；点击继续下载可恢复，关闭窗口不会丢弃已下载文件。'
        if self._service.stop_reason == 'cancel':
            return label + '已取消；未自动开始对齐。'
        return label + '完成。请查看任务日志；模型就绪与对齐准确是不同的检查。'

    def _finished(self):
        self._busy_state()
        self._preflight()
        self.logs.setPlainText("\n".join(self._service.log))
        QTimer.singleShot(0,self._maybe_generate_readings)

    def _resume_task(self):
        self._service.resume()
        self._busy_state()

    def _cancel_task(self):
        if self._task is None and self._service.stop_reason == 'pause':
            self._service.resume_work=None; self._service.stop_reason='cancel'
            self.status.setText('已取消暂停的下载；已下载文件保留，不再等待继续。')
            self._busy_state();return
        self._service.stop("cancel")
        self.status.setText("正在取消当前任务；窗口保持打开。")
        self._busy_state()

    def _listen_draft(self, vocals):
        if self.setup_only:return
        record=self._transcript_draft
        if record is None:
            options=self._raw_options()
            try:
                from lyricsync.local_alignment import discover_media_duration
                duration=options['duration_seconds']
                if duration is None:duration=discover_media_duration(self.audio.text())
                record={"source_path":self.audio.text(),"offset_seconds":options['offset_seconds'],"duration_seconds":duration}
            except (ValueError,OSError) as exc:
                self.status.setText('无法试听：'+str(exc));return
        if vocals and not record.get("stem_path"):
            self.status.setText("尚无人声分轨，可选择分离人声后听写。");return
        self._listen_audio(record,vocals)

    def _question(self, title, message):
        # Paths and backend notices must never become rich text. No is default.
        box = QMessageBox(QMessageBox.Icon.Question, title, message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        apply_dialog_theme(box)
        return box.exec()

    def _download_asr(self):
        source = self.download_source.currentData()
        if source not in ('mirror', 'official'):
            source = 'mirror' if source == 'modelscope' else 'official'
        src_label = DOWNLOAD_SOURCES[source]['label']
        if self._question( '下载语音转写模型',
                (f'即将从 {src_label} 下载语音转写模型（约 {readable_bytes(ASR.size)}，版本 {ASR.revision}）到本地缓存目录，下载完成后自动校验完整性。\n\n是否开始下载？'
                                                                                                                            )) == QMessageBox.StandardButton.Yes:
            self._model_task("download", source=source, model_id=ASR.id)

    def _import_asr(self):
        folder = QFileDialog.getExistingDirectory(self, '选择语音转写模型（Qwen3-ASR）文件夹')
        if folder:
            self._model_task("import_folder", folder, model_id=ASR.id)

    def _generate_asr(self):
        if self.setup_only:
            self.status.setText('请从自动打轴入口打开此窗口以生成听写初稿。')
            return
        self._stop_preview()
        try:
            from lyricsync.local_transcription import validate_transcription_options, generate_transcript_draft
            options = validate_transcription_options(self._raw_options())
            document = deepcopy(self.document)
            self._start_task(lambda cancel, progress: generate_transcript_draft(
                document, options=options, cancel=cancel, progress=progress), action="asr")
        except Exception as exc:
            self.status.setText(str(exc))

    def _show_draft(self, value):
        if value.get("document_id") != self.document.id or value.get("revision") != self.document.revision:
            self.status.setText('该听写初稿属于其他工程或旧版本，无法在当前工程中使用，请重新生成。')
            return
        self._stop_preview()
        self._transcript_draft = deepcopy(value)
        suggested=value.get("suggested_lines")
        self.draft_text.setPlainText("\n".join(suggested) if isinstance(suggested,list) and all(isinstance(x,str) for x in suggested) else value["text"])
        self.draft_confirm.setChecked(False)
        self.draft_use.setChecked(False)
        self.draft_info.setText((f"待校对初稿 · 音频片段 {value['offset_seconds']:.3f}–{value['offset_seconds'] + value['duration_seconds']:.3f} 秒\n"
                                                                             ) + value["warning"] +
            ("" if value.get("complete") else '\n转写内容过长被截断，请缩短片段时长后重新生成。'))
        self.status.setText('听写初稿已生成，已提供自动断句建议（非精确语义边界）；原始返回保留在听写诊断。试听校对后再采用。' if suggested else '听写初稿已生成。可按停顿与空格自动断句，试听校对后再采用。')
        self.tabs.setCurrentWidget(self.draft_page)
        self._busy_state()

    def _download_separation(self):
        spec = separation_spec(self.separation_model.currentData())
        source = self.download_source.currentData()
        if source not in ('mirror', 'official', 'uvr'):
            source = 'mirror' if source == 'modelscope' else 'official'
        src_label = DOWNLOAD_SOURCES[source]['label']
        if self._question( '下载人声分离模型',
                (f'即将从 {src_label} 下载 {spec.files[0].name}（约 {readable_bytes(spec.size)}）到本地缓存目录，下载后自动校验完整性。\n\n是否开始下载？'

                                                                                                           )) == QMessageBox.StandardButton.Yes:
            self._model_task("download", source=source, model_id=self.separation_model.currentData())

    def _import_separation(self):
        folder = QFileDialog.getExistingDirectory(self, '选择人声分离模型（UVR）文件夹')
        if folder:
            self._model_task("import_folder", folder, model_id=self.separation_model.currentData())

    def _update_source_details(self):
        info = DOWNLOAD_SOURCES[self.download_source.currentData()]
        note = '推荐优先使用「国内镜像源（HF-Mirror / 魔搭 / GitHub 加速）」；也可按需切换为海外官方直连源。'
        if self.download_source.currentData() in ("mirror", "modelscope"):
            note += '所有模型下载后均校验固定 SHA-256 摘要，中断可随时断点续传。'
        self.source_details.setText(note)
        self.source_details.setToolTip("模型版本："+info["revision"])

    def _download(self):
        spec=ALIGNMENT_MODELS[self.alignment_model.currentData()]
        source=self.download_source.currentData()
        if spec.id != QWEN.id and source == 'modelscope':
            source = 'mirror'
        source_info=DOWNLOAD_SOURCES[source]
        notice=f"将下载 {spec.id}（{readable_bytes(spec.size)}）到所选缓存，随后校验。\n来源：{source_info['label']} {source_info['page']}\n版本：{spec.revision}\n许可：{spec.license_url}"
        if spec.id==NEXTFIRE.id:notice+="\nNextFire 权重为 CC-BY-NC-SA-4.0，仅限非商业使用，须署名并遵守相同方式共享条款。权重不随编辑器分发。"
        if self._question("下载对时模型",notice+"\n是否开始？") == QMessageBox.StandardButton.Yes:
            self._model_task("download",source=source,model_id=spec.id)

    def _import(self):
        spec=ALIGNMENT_MODELS[self.alignment_model.currentData()]
        folder=QFileDialog.getExistingDirectory(self,"选择 "+spec.id+" 模型文件夹")
        if folder:self._model_task("import_folder",folder,model_id=spec.id)

    def _verify(self):
        self._model_task("verify",model_id=self.alignment_model.currentData())

    def _model_task(self, action, folder=None, *, source=None, model_id=QWEN.id):
        try:
            manager = ModelManager(self.cache.text())
            args = (folder,) if folder else ()
            kwargs = {"model_id": model_id}
            if action == "download":
                kwargs["source"] = source
            self._start_task(lambda cancel, progress: getattr(manager, action)(*args, cancel=cancel, progress=progress, **kwargs), action=action)
        except Exception as exc:
            self.status.setText(str(exc))

    def _prepare_all(self):
        options = self._raw_options()
        include_asr = self.prepare_asr.isChecked()
        size = QWEN.size + separation_spec(options["separation_model_id"]).size + (ASR.size if include_asr else 0)
        notice = (f"将准备所选模型（{readable_bytes(size)}）及独立运行环境。新环境下载/安装约数 GB，"
                  "需至少 12 GiB 加模型缓存及下载暂存空间。\n"
                  f"缓存：{options['cache_root']}\n"
                  "使用所选官方对齐下载源、UVR 官方 GitHub；文字识别模型使用官方 Hugging Face。"
                  "依赖来自官方 PyPI/PyTorch；各模型和依赖许可见安装与许可页，包括 Apache-2.0、UVR MIT 署名、soynlp GPLv3 及 pykakasi GPL-3.0-or-later。\n"
                  "已有环境只检测，不修改；未配置时自动寻找已安装的 64 位 Python 3.12 并创建新独立环境。"
                  "未找到时停止，此版不自动下载 Python 本体。不上传音频或歌词。现在准备？")
        if self._question("准备环境与模型", notice) != QMessageBox.StandardButton.Yes:
            return
        self._start_task(lambda cancel, progress: prepare_models(
            cache_root=options['cache_root'], runtime_python=options['runtime_python'],
            device=options['device'], language=options['language'],
            download_source=options['download_source'], include_asr=include_asr,
            separation_model_id=options['separation_model_id'],
            cancel=cancel, progress=progress), action="prepare")

    def _install_runtime(self):
        base, _ = QFileDialog.getOpenFileName(self, '选择本机 64 位 Python 3.12（python.exe）', "", "Python (python.exe)")
        if not base:
            return
        parent = QFileDialog.getExistingDirectory(self, '选择运行环境的安装位置')
        if not parent:
            return
        target = Path(parent) / ("lyria-alignment-runtime-" + time.strftime("%Y%m%d-%H%M%S"))
        device, language = self.device.currentData(), self.language.currentData()
        flavor = 'PyTorch CUDA 12.8（GPU 加速版）' if device == "cuda:0" else 'PyTorch（CPU 版）'
        notice = (
            (f'将在以下新目录创建独立的 Python 3.12 运行环境：\n{target}\n\n将从官方源安装 {flavor}、Transformers 及分词/音频处理组件（含 pykakasi，GPL-3.0-or-later；建议预留至少 12 GB 磁盘空间，不会影响系统或其他环境）。\n\n是否立即开始创建并安装？'



                                                                                         ))
        if self._question( '配置 Python 运行环境', notice) != QMessageBox.StandardButton.Yes:
            return
        self._start_task(lambda cancel, progress: create_runtime(
            base, target, device=device, language=language, cancel=cancel, progress=progress), action="install_runtime")

    def _probe(self):
        python, device, language = self.runtime.text(), self.device.currentData(), self.language.currentData()
        self._start_task(lambda cancel, progress: probe_runtime(python, device, language, cancel=cancel, progress=progress), action="probe")

    def _choose_diagnostic_export(self):
        if self._question('导出包含私人内容的完整诊断', '此文件包含完整歌词、实际送入模型的文字、媒体路径及模型裸输出。仅本次运行保存到你选择的本地文件，不会上传；请勿未经检查就分享。是否继续选择保存位置？') != QMessageBox.StandardButton.Yes:return
        path,_=QFileDialog.getSaveFileName(self,'选择本次完整诊断保存位置','','JSON (*.json)')
        if path:
            self._diagnostic_export_path=path
            self.status.setText('本次将导出完整诊断到：'+path+'；包含歌词及媒体路径，不会自动上传。')
    def _clear_diagnostic_export(self):
        self._diagnostic_export_path='';self.status.setText('已取消本次完整诊断导出；仍只保留匿名计数清单。')
    def _raw_options(self):
        value=dict(effective_reading_python=self._reading_python(), runtime_python=self.runtime.text(), reading_runtime_python=self.reading_runtime.text().strip(), cache_root=self.cache.text(),
                    model_id=self.alignment_model.currentData(), separation_model_id=self.separation_model.currentData(), download_source=self.download_source.currentData(), audio_path=self.audio.text(),
                    language=self.language.currentData(), device=self.device.currentData(),
                    offset_seconds=self.offset.value() if self.custom_range.isChecked() else 0.,
                    duration_seconds=self.duration.value() if self.custom_range.isChecked() else None,
                    text_source=self.text_source.currentData(), main_source_confirmed=self.main_source_confirm.isChecked(), source_rows=deepcopy(self._external_rows),
                    use_separation=self.separate.isChecked(), reading_tool=self.reading_tool.currentData(), reading_rows=self._current_readings(),
                    sentence_ids=[row.id for row in (self.document.project.mappings[self.first.value()-1:self.last.value()] if self.custom_range.isChecked() else self.document.project.mappings)],
                    min_timestamp_score=.01,tail_silence_guard=self.tail_silence_guard.isChecked())
        if self._diagnostic_export_path:value['diagnostic_export_path']=self._diagnostic_export_path
        return value

    def _accept(self):
        if self._task and not self.setup_only: return
        try:
            value=self._raw_options()
            if not self.setup_only and value['text_source']=='main':
                if self._known_translation() or self._confirmed_main is None or self._confirmed_main!=self._main_key(self._live_document):
                    self.main_source_confirm.setChecked(False)
                    raise ValueError("时间轴原词尚未确认或已变更，请重新核对。")
            if not self.setup_only:
                if self.custom_range.isChecked() and self.first.value()>self.last.value() and value["text_source"]=="main":
                    raise ValueError("起始句号不能大于结束句号。")
                if value["text_source"]=="draft":
                    if self._adopted_draft is None:raise ValueError("请在听写原词页采用校对后的歌词。")
                    from lyricsync.local_transcription import validate_transcription_options
                    value = validate_transcription_options(value)
                    draft=self._adopted_draft
                    if (str(Path(value["audio_path"]).resolve())!=draft["source_path"] or value["offset_seconds"]!=draft["offset_seconds"] or value["duration_seconds"]!=draft.get("requested_duration_seconds",draft["duration_seconds"]) or value["language"]!=draft["language"]):
                        raise ValueError("音频、语言或片段已变动，请重新生成听写初稿。")
                    value["transcript_draft"]=deepcopy(draft)
                from lyricsync.experimental_nextfire_alignment import normalize_reading
                if value["model_id"] == NEXTFIRE.id:
                    missing = []; invalid = []
                    from lyricsync.japanese_readings import pure_symbol_row
                    for index, row in enumerate(value["reading_rows"], 1):
                        if pure_symbol_row(row):continue
                        reading = row.get('reading', '')
                        if not reading.strip():
                            if row.get('reading_origin') in ('automatic_suggestion', 'provided_ruby_converted'):
                                invalid.append(f'第 {index} 句未能自动生成可用读音，请手动修正，不会重复自动生成。'); continue
                            missing.append(index); continue
                        import re
                        if re.search(r'[ぁ-ヿ一-鿿]', reading) and row.get('reading_origin') not in ('automatic_suggestion', 'provided_ruby_converted'):
                            missing.append(index); continue
                        try: normalize_reading(reading)
                        except ValueError as exc:
                            invalid.append(f'第 {index} 句读音含未转换内容或不支持的符号，请手动修正：{exc}')
                    from lyricsync.japanese_readings import invalid_reading_rows
                    for issue in invalid_reading_rows(value['reading_rows']):
                        if issue['kind']=='review_input' or (issue['kind']=='invalid_labels' and issue['index'] not in missing):invalid.append(f"第 {issue['index']} 句输入需确认：{issue['message']}")
                    if invalid:
                        raise ValueError('全曲读音检查发现 ' + str(len(invalid)) + ' 项问题：\n' + '\n'.join(invalid))
                    if missing:
                        if value['reading_tool'] == 'manual':
                            raise ValueError('部分日语原词缺少有效读音，请手动补齐，或切换到 pykakasi 自动补齐。')
                        self._generate_readings()
                        if self._task:self.status.setText("正在补齐读音；完成后请核对，再点击开始自动打轴。")
                        return
                if value["text_source"]!="draft":
                    value=validate_options(self.document,value)
                manager = ModelManager(value["cache_root"])
                required = [value["model_id"]]
                if value.get("use_separation"):
                    required.append(value["separation_model_id"])
                if value["model_id"] == QWEN.id and value["duration_seconds"] > 300:
                    from lyricsync.local_alignment import discover_media_duration, qwen_acoustic_rows
                    rows = value.get("rows", (self._adopted_draft or {}).get("rows", []))
                    if qwen_acoustic_rows(rows) and discover_media_duration(value["audio_path"]) - value["offset_seconds"] > 300:
                        required.append(ASR.id)
                for identity in required:
                    state = manager.status(identity)
                    if not state["present"]:
                        prefix = "长音频定位歌词片段需要听写模型。" if identity == ASR.id else ""
                        raise ValueError(prefix + manager.file_problem(identity))
            self._options=value;self._closed=True;self._stop_preview();super().accept()
        except Exception as exc:self.status.setText(str(exc))

    def options(self):
        return deepcopy(self._options if self._options is not None else self._raw_options())

    def reject(self):
        self._closed=True
        self._stop_preview()
        # Management belongs to QApplication, not the dialog. Close is not cancel.
        super().reject()

    def closeEvent(self, event):
        self._closed=True
        self._stop_preview()
        super().closeEvent(event)
