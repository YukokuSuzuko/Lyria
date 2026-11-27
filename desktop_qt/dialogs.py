"""Small native adjunct dialogs; the application itself remains Qt Quick.

Credentials never enter a QVariant snapshot, project or report. The text box is
transient and Windows credential access is always an explicit button action.
"""
from __future__ import annotations
import os
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox,QComboBox,QDialog,QDialogButtonBox,QDoubleSpinBox,
                               QFormLayout,QFontComboBox,QGridLayout,QGroupBox,QHBoxLayout,QLabel,QLineEdit,
                               QMessageBox,QPushButton,QSpinBox,QTextEdit,QVBoxLayout,QScrollArea,QWidget,QLayout,QFrame)
from .native_theme import DIALOG_STYLE as STYLE, apply_dialog_theme

class PlainLabel(QLabel):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.setTextFormat(Qt.TextFormat.PlainText)

# Every dynamic caption (OCR/API/path/error) is literal, never AutoText.
QLabel=PlainLabel

class ApiSettingsDialog(QDialog):
    def __init__(self,bridge,preset=None):
        super().__init__();self.bridge=bridge;self.setWindowTitle('AI 接口设置');self.resize(800,740)
        from lyricsync import ai_config
        config=dict(bridge.api_settings)
        if preset:config['api_provider']=preset
        outer=QVBoxLayout(self);scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QScrollArea.Shape.NoFrame);outer.addWidget(scroll)
        content=QWidget();root=QVBoxLayout(content)
        # Keep form rows at their natural height under DPI/font changes. The
        # scroll viewport, not the inner form, absorbs a small dialog height.
        root.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setObjectName('apiSettingsScroll');content.setObjectName('apiSettingsContent')
        scroll.setWidget(content)
        notice=QLabel(ai_config.PRIVACY_NOTICE);notice.setWordWrap(True);root.addWidget(notice)
        sections=QGridLayout();root.addLayout(sections)
        connection=QWidget();connection.setObjectName('apiConnectionSection')
        connection_layout=QVBoxLayout(connection);connection_layout.setContentsMargins(0,0,0,0)
        connection_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        advanced_section=QWidget();advanced_section.setObjectName('apiAdvancedSection')
        advanced_layout=QVBoxLayout(advanced_section);advanced_layout.setContentsMargins(0,0,0,0)
        advanced_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        sections.addWidget(connection,0,0);sections.addWidget(advanced_section,1,0)
        self._api_sections=sections;self._api_connection=connection;self._api_advanced_section=advanced_section
        self._api_section_mode=None;self._api_action_columns=None
        form=QFormLayout();form.setVerticalSpacing(7);form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows);connection_layout.addLayout(form)
        self.provider=QComboBox();self.provider.addItem('DeepSeek 官方','deepseek');self.provider.addItem('自定义服务商','custom');self.provider.setCurrentIndex(0 if config['api_provider']=='deepseek' else 1)
        self.name=QLineEdit(config['api_provider_name']);self.url=QLineEdit(config['api_base_url'])
        self.model=QComboBox();self.model.setEditable(True);self.model.addItem(config['api_model'])
        self.key=QLineEdit();self.key.setEchoMode(QLineEdit.EchoMode.Password);self.key.setPlaceholderText('输入 API Key（不会写入工程文件或日志）')
        form.addRow('服务商',self.provider);form.addRow('自定义名称',self.name);form.addRow('Base URL',self.url);form.addRow('模型名称',self.model);form.addRow('API Key',self.key)
        self.enabled=QCheckBox('允许在本次会话中向该接口发送字幕文本');self.enabled.setChecked(bridge.api_text_consent(config));connection_layout.addWidget(self.enabled)
        scope_label=QLabel('仅发送核对所需的歌词、字幕文本与时间信息，不会上传音视频或画面截图。');scope_label.setWordWrap(True);connection_layout.addWidget(scope_label)
        self._consent_scope=ai_config.credential_scope(config)
        manual_notice=QLabel('AI 核对入口暂时隐藏；保存配置不会启用请求，OCR 完成后也不会自动发送。');manual_notice.setObjectName('ocrManualAiNotice');manual_notice.setWordWrap(True);connection_layout.addWidget(manual_notice)
        self.url.textChanged.connect(self.reset_consent)
        self.persist=QCheckBox('保存新密钥到 Windows 凭据管理器');self.persist.setEnabled(os.name=='nt');self.persist.setToolTip('若系统不支持凭据管理器，密钥仅在本次运行期间有效。');connection_layout.addWidget(self.persist)
        # The checkbox is a separate layout row, never a QGroupBox title on a
        # border. This remains non-overlapping under native font/DPI changes.
        self.advanced=QCheckBox('高级设置');self.advanced.setObjectName('apiAdvancedToggle');advanced_layout.addWidget(self.advanced)
        advanced=QFrame();advanced.setObjectName('apiAdvancedBody');advanced_layout.addWidget(advanced)
        advanced_grid=QGridLayout(advanced);advanced_grid.setContentsMargins(10,10,10,10)
        left=QFormLayout();right=QFormLayout();left.setVerticalSpacing(6);right.setVerticalSpacing(6)
        advanced_grid.addLayout(left,0,0);advanced_grid.addLayout(right,0,1)
        self._api_advanced_grid=advanced_grid;self._api_advanced_right=right
        self._api_forms=(form,left,right);self._api_compact_mode=None
        self.max_calls=QSpinBox();self.max_calls.setRange(1,100);self.max_calls.setValue(config.get('api_max_calls',50))
        self.retries=QSpinBox();self.retries.setRange(0,10);self.retries.setValue(config.get('api_retries',3))
        self.transport=QComboBox()
        for title,value in [('自动','auto'),('标准 JSON','json'),('流式传输（SSE）','sse')]:self.transport.addItem(title,value)
        self.thinking=QComboBox()
        for title,value in [('默认','provider_default'),('开启','enabled'),('关闭','disabled')]:self.thinking.addItem(title,value)
        self.reasoning=QComboBox()
        for title,value in [('默认','provider_default'),('低','low'),('高','high'),('最大','max')]:self.reasoning.addItem(title,value)
        for control,key,default in [(self.transport,'api_transport','auto'),(self.thinking,'api_thinking','provider_default'),(self.reasoning,'api_reasoning_effort','provider_default')]:control.setCurrentIndex(max(0,control.findData(config.get(key,default))))
        self.idle_timeout=QSpinBox();self.idle_timeout.setRange(1,120);self.idle_timeout.setValue(config.get('api_idle_timeout_seconds',40));self.idle_timeout.setSuffix(' 秒')
        self.total_timeout=QSpinBox();self.total_timeout.setRange(1,240);self.total_timeout.setValue(config.get('api_total_timeout_seconds',180));self.total_timeout.setSuffix(' 秒')
        for title,control in [('单工程请求次数上限',self.max_calls),('失败重试次数',self.retries),('响应模式',self.transport),('空闲超时',self.idle_timeout)]:left.addRow(title,control)
        for title,control in [('深度思考（DeepSeek）',self.thinking),('思考强度',self.reasoning),('单次总超时',self.total_timeout)]:right.addRow(title,control)
        advanced.setEnabled(False);self.advanced.toggled.connect(advanced.setEnabled)
        self._api_scroll=scroll;self._api_content=content
        for combo in (self.provider,self.model,self.transport,self.thinking,self.reasoning):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
        hint=QLabel('为防重复扣费，连接中断或读取超时不会自动重发；失败和重试也会计入次数上限。');hint.setWordWrap(True);root.addWidget(hint)
        buttons=QGridLayout();outer.addLayout(buttons);self._api_actions=buttons;self._api_action_buttons=[]
        for number,(label,slot) in enumerate([('获取模型列表',self.models),('测试连接',self.test),('载入 Windows 凭据',self.load_key),('清除配置与密钥',self.delete)]):
            button=QPushButton(label);button.clicked.connect(slot);buttons.addWidget(button,number//2,number%2);self._api_action_buttons.append(button)
            if 'Windows' in label:button.setEnabled(os.name=='nt')
        self.status=QLabel('就绪');self.status.setWordWrap(True);outer.addWidget(self.status)
        box=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.save);box.rejected.connect(self.reject);outer.addWidget(box)
        self.provider.currentIndexChanged.connect(self.provider_changed);self.provider_changed()
        bridge.stateChanged.connect(self.refresh_status)
        self._status_connected = True
        self.finished.connect(self.closed)
        apply_dialog_theme(self)
        self._api_buttons=box
        self.setMinimumSize(320,300)
        self._size_to_content()

    def _arrange_advanced(self,width,height=None):
        if not hasattr(self,'_api_forms'):return
        height=self.height() if height is None else height
        side_by_side=width>=940 and height<740
        if side_by_side!=self._api_section_mode:
            self._api_section_mode=side_by_side
            self._api_sections.removeWidget(self._api_advanced_section)
            self._api_sections.addWidget(self._api_advanced_section,0 if side_by_side else 1,1 if side_by_side else 0)
            self._api_sections.setColumnStretch(0,1)
            self._api_sections.setColumnStretch(1,1 if side_by_side else 0)
        mode=1 if side_by_side else 2 if width<500 else 1 if width<700 else 0
        if mode!=self._api_compact_mode:
            self._api_compact_mode=mode
            self._api_advanced_grid.removeItem(self._api_advanced_right)
            self._api_advanced_grid.addLayout(self._api_advanced_right,1 if mode else 0,0 if mode else 1)
            for form in self._api_forms:
                form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows if mode==2 else QFormLayout.RowWrapPolicy.WrapLongRows)
        if hasattr(self,'_api_action_buttons') and self._api_action_buttons:
            required=sum(b.sizeHint().width() for b in self._api_action_buttons)+3*self._api_actions.horizontalSpacing()+24
            columns=4 if width>=required else 2
            if columns!=self._api_action_columns:
                self._api_action_columns=columns
                for number,button in enumerate(self._api_action_buttons):
                    self._api_actions.removeWidget(button)
                    self._api_actions.addWidget(button,number//columns,number%columns)
        self._api_content.layout().invalidate()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self._arrange_advanced(event.size().width(),event.size().height())

    def _size_to_content(self):
        # Short, wide screens use two existing sections side by side. Widgets,
        # readable type size, signals and all consent/credential controls remain.
        self.ensurePolished();self._api_content.ensurePolished()
        screen=self.screen();available=screen.availableGeometry() if screen else None
        maximum_height=max(300,available.height()-60) if available else 900
        width=min(840,max(760,self.sizeHint().width()))
        if available:
            if available.height()<780 and available.width()>=980:
                width=min(1100,available.width()-40)
            width=min(width,max(320,available.width()-40))
        self._arrange_advanced(width,maximum_height)
        self.resize(width,maximum_height);self.layout().activate()
        self._api_content.layout().activate()
        height=self._api_content.layout().heightForWidth(max(1,width-40))
        if height<0:height=self._api_content.sizeHint().height()
        height+=self._api_buttons.sizeHint().height()+self._api_actions.sizeHint().height()+self.status.sizeHint().height()+66
        # Native font and fractional-DPI layout rounding can add a few pixels
        # after first show. Leave breathing room when the work area permits it.
        self.resize(width,min(height+16,maximum_height))

    def reset_consent(self,*args):
        from lyricsync import ai_config
        try:scope=ai_config.credential_scope(self.values())
        except ValueError:scope=None
        if scope!=self._consent_scope:
            self.enabled.setChecked(False);self._consent_scope=scope

    def closed(self,*args):
        self.key.clear()
        if self._status_connected:
            self._status_connected = False
            try:self.bridge.stateChanged.disconnect(self.refresh_status)
            except RuntimeError:pass

    def provider_changed(self):
        custom=self.provider.currentData()=='custom';self.name.setEnabled(True);self.url.setEnabled(custom)
        if not custom:self.url.setText('https://api.deepseek.com')
        self.reset_consent()

    def values(self):
        from lyricsync.ai_config import normalized
        return normalized(dict(api_provider=self.provider.currentData(),api_provider_name=self.name.text(),api_base_url=self.url.text(),api_model=self.model.currentText(),api_max_calls=self.max_calls.value(),api_retries=self.retries.value(),api_transport=self.transport.currentData(),api_thinking=self.thinking.currentData(),api_reasoning_effort=self.reasoning.currentData(),api_idle_timeout_seconds=self.idle_timeout.value(),api_total_timeout_seconds=self.total_timeout.value()))

    def apply(self,persist=False):
        try:
            self.bridge.configure_api(self.values(),self.key.text(),self.enabled.isChecked(),persist and self.persist.isChecked())
            self.key.clear();return True
        except Exception as exc:self.status.setText(str(exc));return False

    def save(self):
        if self.apply(True):self.accept()
    def models(self):
        if self.apply():self.bridge.command('api.models',{})
    def test(self):
        if self.apply():self.bridge.command('api.test',{})
    def load_key(self):
        try:self.bridge.load_api_credential(self.values());self.status.setText('已读取保存的密钥（出于安全考虑不显示明文）。')
        except Exception as exc:self.status.setText(str(exc))
    def delete(self):
        if QMessageBox.question(self,'清除配置与密钥','确定清除已保存的密钥并恢复默认接口设置吗？',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes:
            try:self.bridge.delete_api_provider();self.key.clear();self.reject()
            except Exception as exc:self.status.setText(str(exc))
    def refresh_status(self):
        self.status.setText(self.bridge.api_state.get('status',''))
        models=self.bridge.api_state.get('models',[])
        if models:
            current=self.model.currentText();self.model.clear();self.model.addItems(models);self.model.setCurrentText(current)


def confirm_text_api_consent(settings):
    """Default-No consent; saving keys or enabling auto-review is not consent."""
    from lyricsync import ai_config
    box=QMessageBox();box.setObjectName('textApiConsentDialog')
    box.setTextFormat(Qt.TextFormat.PlainText);box.setWindowTitle('发送字幕文本进行 AI 核对？')
    box.setText('目标接口：'+settings.get('api_provider_name','AI 接口')+'\n'+ai_config.base_url(settings))
    box.setInformativeText('即将向上述接口发送所选范围的歌词、字幕与时间轴信息（不上传音视频和截图），可能消耗接口额度。\n\n授权仅在本次运行期间对当前接口有效。')
    box.setStandardButtons(QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
    box.button(QMessageBox.StandardButton.Yes).setText('允许发送')
    box.button(QMessageBox.StandardButton.No).setText('取消')
    box.setDefaultButton(QMessageBox.StandardButton.No);box.setEscapeButton(QMessageBox.StandardButton.No)
    apply_dialog_theme(box)
    return box.exec()==QMessageBox.StandardButton.Yes


def show_alignment(bridge,plan):
    from .api_review_dialogs import show_alignment_plan
    return show_alignment_plan(bridge,plan)


def json_safe(value):
    import json
    return json.dumps(value,ensure_ascii=False)


def confirm_regions(bridge):
    from .roi_dialog import RegionDialog
    return RegionDialog(bridge).exec()


def show_text(title,body,*,owner=None,modeless=False):
    from . import carbon_widgets as c
    dialog=QDialog();dialog.setWindowTitle(title);dialog.resize(720,560)
    root=c.layout(dialog)
    body_wrap=QWidget();body_wrap.setObjectName('carbonBody');stack=c.layout(body_wrap,margins=(18,14,18,14),space=10);root.addWidget(body_wrap,1)
    card=c.card(stack);card_box=c.layout(card_host:=QWidget(),margins=(12,12,12,12));card.addWidget(card_host)
    text=QTextEdit();text.setReadOnly(True);text.setPlainText(body);card_box.addWidget(text)
    footer=c.footer(root);footer.addStretch(1)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.rejected.connect(dialog.reject);footer.addWidget(buttons)
    if buttons.button(QDialogButtonBox.StandardButton.Close):
        buttons.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
    c.theme(dialog)
    if modeless:
        from .api_review_dialogs import show_nonmodal
        return show_nonmodal(owner,dialog)
    dialog.exec()


def edit_pairing(bridge):
    from . import carbon_widgets as c
    from PySide6.QtWidgets import QListWidget,QAbstractItemView
    row=bridge._row();project=bridge.document.project
    dialog=QDialog();dialog.setWindowTitle('手动关联参考原文与 OCR 结果');dialog.resize(840,580)
    root=c.layout(dialog)
    body=QWidget();body.setObjectName('carbonBody');stack=c.layout(body,margins=(18,14,18,14),space=12);root.addWidget(body,1)
    notice_card=c.card(stack);nb=c.band(notice_card,(14,10,14,10));nb.addWidget(QLabel('调整当前句关联的参考原文与 OCR 识别结果，不会修改已编辑的字幕文本和时间轴。'),1)
    layout=QHBoxLayout();layout.setSpacing(12);stack.addLayout(layout,1);lists={}
    for name,title,records,selected in [('lyricIds','参考原文',project.lyrics,row.lyric_ids),('visualIds','OCR 识别结果',project.visuals,row.visual_ids)]:
        card_frame=QFrame();card_frame.setObjectName('carbonCard');col=c.layout(card_frame,margins=(12,12,12,12),space=8)
        heading=QLabel(title);f=heading.font();f.setWeight(f.Weight.DemiBold);heading.setFont(f);col.addWidget(heading)
        widget=QListWidget();widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection);col.addWidget(widget,1);layout.addWidget(card_frame,1)
        for record in records:
            widget.addItem(record.text);item=widget.item(widget.count()-1);item.setData(Qt.ItemDataRole.UserRole,record.id);item.setSelected(record.id in selected)
        lists[name]=widget
    footer=c.footer(root);footer.addStretch(1)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);footer.addWidget(buttons)
    save_btn=buttons.button(QDialogButtonBox.StandardButton.Save);cancel_btn=buttons.button(QDialogButtonBox.StandardButton.Cancel)
    if save_btn: save_btn.setText('保存'); save_btn.setProperty('styleRole','primary')
    if cancel_btn: cancel_btn.setText('取消')
    buttons.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    c.theme(dialog)
    if dialog.exec()==QDialog.DialogCode.Accepted:
        bridge.command('row.pairing',{'id':row.id,**{key:[i.data(Qt.ItemDataRole.UserRole) for i in widget.selectedItems()] for key,widget in lists.items()}})


def choose_rescan(bridge,identity,result):
    from . import carbon_widgets as c
    from PySide6.QtWidgets import QListWidget,QAbstractItemView
    dialog=QDialog();dialog.setWindowTitle('单句重扫结果');dialog.resize(750,510)
    root=c.layout(dialog)
    body=QWidget();body.setObjectName('carbonBody');stack=c.layout(body,margins=(18,14,18,14),space=12);root.addWidget(body,1)
    notice_card=c.card(stack);nb=c.band(notice_card,(14,10,14,10))
    notice=QLabel(result.get('reason') or '请选择要采用的识别结果，应用后建议结合画面微调起止帧。');notice.setWordWrap(True);nb.addWidget(notice,1)
    list_card=c.card(stack);lb=c.layout(w_list:=QWidget(),margins=(10,10,10,10));list_card.addWidget(w_list)
    listing=QListWidget();listing.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection);lb.addWidget(listing)
    for v in result.get('visuals',[]):
        index=bridge.media_controller.index
        first=index.first(v['start']) if index else '?';last=index.last(v['end']) if index else '?'
        listing.addItem(f"F{first}–F{last} · {v['text']} · OCR {v['confidence']:.2f}")
        listing.item(listing.count()-1).setData(Qt.ItemDataRole.UserRole,v['id'])
    footer=c.footer(root);footer.addStretch(1)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Cancel);footer.addWidget(buttons)
    apply_btn=buttons.button(QDialogButtonBox.StandardButton.Apply);cancel_btn=buttons.button(QDialogButtonBox.StandardButton.Cancel)
    if apply_btn:
        apply_btn.setText('应用所选'); apply_btn.setProperty('styleRole','primary')
        apply_btn.clicked.connect(lambda:(bridge.command('row.adoptRescan',{'id':identity,'visualIds':[i.data(Qt.ItemDataRole.UserRole) for i in listing.selectedItems()]}),dialog.accept()))
    if cancel_btn: cancel_btn.setText('取消')
    buttons.rejected.connect(dialog.reject);buttons.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    c.theme(dialog);dialog.exec()


def ocr_settings(bridge,providers,*,start_after=False):
    document_id=bridge.document.id
    dialog=QDialog();dialog.setWindowTitle('OCR 识别设置');dialog.setObjectName('ocrSettingsDialog');dialog.resize(628,672)
    from . import carbon_widgets as c
    from PySide6.QtWidgets import QButtonGroup
    root=c.layout(dialog); scroll=QScrollArea();scroll.setWidgetResizable(True);root.addWidget(scroll,1)
    body=QWidget();body.setObjectName('carbonBody');stack=c.layout(body,margins=(17,12,17,12),space=10);scroll.setWidget(body)
    settings=bridge.document.project.settings
    engine=QComboBox();engine.setObjectName('ocrEngine');engine.addItem('RapidOCR（推荐）','rapidocr');engine.addItem('Tesseract · CPU','tesseract');engine.setCurrentIndex(max(0,engine.findData(settings.get('engine','rapidocr'))))
    device=QComboBox();device.setObjectName('ocrComputeDevice');device.addItem('CPU','cpu')
    import sys
    from lyricsync.split_profiles import ACTIVE
    for key,name,label in [('directml','DmlExecutionProvider','显卡加速（DirectML）'),('cuda','CUDAExecutionProvider','显卡加速（CUDA）')]:
        allowed = (not getattr(sys,'frozen',False)) or ACTIVE in {'cuda':('nvidia',),'directml':('directml','amd')}[key]
        if name in providers and allowed:device.addItem(label,key)
    default_device = 'auto' if getattr(sys,'frozen',False) else 'cpu'
    device.addItem('自动（任务开始时选已就绪设备）','auto')
    previous_device = settings.get('ocr_device',default_device)
    if engine.currentData() == 'tesseract':
        previous_device = settings.get('ocr_rapid_device', previous_device)
    missing_device=device.findData(previous_device)<0 and previous_device!='auto'
    device.setCurrentIndex(max(0,device.findData(previous_device)))
    model=QComboBox();model.setObjectName('ocrRecognitionModel')
    model.addItem('增强多语模型','ppocrv6');model.addItem('兼容模型','ppocrv4')
    model.setCurrentIndex(max(0,model.findData(settings.get('ocr_model','ppocrv6'))))
    model.setToolTip('仅 RapidOCR：增强多语模型 PP-OCRv6 的各语言共用一套权重，无需为每种语言另行下载。兼容模型使用 PP-OCRv4。缺少所选权重会明确报错，不自动联网下载或切换。')
    preprocess=QComboBox();preprocess.setObjectName('ocrPreprocessMode')
    preprocess.addItem('复杂背景字幕','subtitle');preprocess.addItem('原图','raw')
    preprocess.setCurrentIndex(max(0,preprocess.findData(settings.get('ocr_preprocess','subtitle'))))
    preprocess.setToolTip('仅 RapidOCR：针对白色/浅色字幕保守增强，保留原图回退，不做硬二值化。彩色或特殊字体可切原图复核；原素材不会改变。')
    rapid_device = device.currentData()
    last_engine = 'rapidocr'
    def update_rapid_controls():
        nonlocal rapid_device, last_engine
        enabled=engine.currentData()=='rapidocr'
        if not enabled:
            if last_engine == 'rapidocr':
                rapid_device = device.currentData()
            device.setCurrentIndex(device.findData('cpu'))
        elif last_engine != 'rapidocr':
            device.setCurrentIndex(max(0, device.findData(rapid_device)))
        device.setEnabled(enabled)
        model.setEnabled(enabled);preprocess.setEnabled(enabled)
        last_engine = engine.currentData()
    engine.currentIndexChanged.connect(update_rapid_controls);update_rapid_controls()
    threads=QSpinBox();threads.setRange(1,32);threads.setValue(settings.get('ocr_threads',2));language=QComboBox();language.setObjectName('ocrLanguage');language.setToolTip('仅列出本机已部署权重支持的选项；模型是否能成功加载仍以运行检查为准。')
    from .ocr_languages import language_options
    from lyricsync.vision import resources
    def update_languages():
        old=language.currentData() or settings.get('language','jpn+eng')
        language.clear()
        for label,value in language_options(engine.currentData(),model.currentData(),resources()):language.addItem(label,value)
        language.setCurrentIndex(max(0,language.findData(old)))
        language.setEnabled(language.count()>0)
    engine.currentIndexChanged.connect(update_languages);model.currentIndexChanged.connect(update_languages);update_languages()
    from lyricsync.vision import OCR_MIN_SAMPLE_FPS, OCR_MAX_SAMPLE_FPS
    fps=QDoubleSpinBox();fps.setObjectName('ocrSampleFps');fps.setRange(OCR_MIN_SAMPLE_FPS,OCR_MAX_SAMPLE_FPS);fps.setValue(settings.get('sample_fps',3));fps.setSuffix(' 帧/秒');fps.setToolTip('每秒抽取几帧：数值越高，采样越密，耗时通常越长；不代表文字识别一定更准。')
    def section(title,note):
        card=c.card(stack)
        row,status=c.header(card,title)
        status.hide()
        status=c.label(note,'muted');row.addWidget(status)
        fields=QWidget();card.addWidget(fields);grid=QGridLayout(fields)
        grid.setContentsMargins(14,12,14,13);grid.setHorizontalSpacing(13);grid.setVerticalSpacing(7)
        for col in range(2):grid.setColumnStretch(col,1)
        return grid,status
    def field(grid,row,col,title,control):
        caption=c.label(title,'muted');caption.setBuddy(control)
        grid.addWidget(caption,row,col);grid.addWidget(control,row+1,col)
    content,_=section('识别内容与抽样','按当前视频画面调整')
    field(content,0,0,'识别语言',language);field(content,0,1,'画面字幕类型',preprocess)
    content.addWidget(c.label('抽样频率','muted'),2,0)
    sampling=QWidget();line=c.layout(sampling,False,space=6);content.addWidget(sampling,3,0,1,2)
    segment=QFrame();segment.setObjectName('ocrSamplingPreset');segments=c.layout(segment,False,(2,2,2,2),0);line.addWidget(segment,1)
    group=QButtonGroup(segment);group.setExclusive(False)
    for title,rate in [('标准 3 帧/秒',3),('快歌 6 帧/秒',6),('精细 10 帧/秒',10)]:
        choice=c.button(title,name=f'ocrPreset{rate}');choice.setCheckable(True)
        choice.setProperty('styleRole','segment')
        group.addButton(choice,rate);segments.addWidget(choice,1)
    fps.setAccessibleName('自定义抽样频率');fps.setMinimumWidth(108);line.addWidget(fps)
    def sync_preset():
        for button in group.buttons():button.setChecked(group.id(button)==fps.value())
    def select_preset(rate):
        fps.setValue(rate);sync_preset()
    fps.valueChanged.connect(sync_preset);group.idClicked.connect(select_preset);sync_preset()
    sampling_hint=c.label('更密抽样有助于定位字幕起止时间，处理会更久，单帧识字准确率不变。','muted',True)
    sampling_hint.setObjectName('ocrSamplingHint');content.addWidget(sampling_hint,4,0,1,2)
    hardware,available=section('识别引擎与硬件','')
    field(hardware,0,0,'识别引擎',engine);field(hardware,0,1,'识字模型',model)
    device_heading=QWidget();device_line=c.layout(device_heading,False,space=4)
    device_line.addWidget(c.label('计算设备','muted'));device_line.addStretch()
    swap=c.button('一键切到 CUDA',lambda:device.setCurrentIndex(device.findData('cuda')),'link','ocrChooseCuda')
    device_line.addWidget(swap);hardware.addWidget(device_heading,2,0)
    hardware.addWidget(device,3,0);field(hardware,2,1,'CPU 线程数',threads)
    hint=c.label('','muted',True);hint.setObjectName('ocrDeviceAvailability')
    from lyricsync.split_profiles import REASON
    available.setToolTip('ORT 枚举：'+', '.join(providers)+ ('\\n'+REASON if REASON else ''))
    hint.setToolTip(available.toolTip());hardware.addWidget(hint,4,0,1,2)
    def show_device():
        rapid=engine.currentData()=='rapidocr';selected=device.currentData()
        threads.setEnabled(selected=='cpu')
        swap.setVisible(rapid and selected=='cpu' and device.findData('cuda')>=0)
        choices=['CPU']+[device.itemText(i).replace('显卡加速（','').rstrip('）') for i in range(1,device.count()) if device.itemData(i) != 'auto']
        available.setText(('✓ 已检测到  ' if len(choices)>1 else '可用设备  ')+' · '.join(choices))
        available.setProperty('styleRole','verified' if len(choices)>1 else 'muted')
        available.style().unpolish(available);available.style().polish(available)
        if not rapid:message='Tesseract 使用 CPU。切回 RapidOCR 可恢复本窗口的模型和设备选择。'
        elif selected=='auto':message='开始识别时选择已通过校验的加速设备，未就绪则使用 CPU。运行中的任务保留原设备。'
        elif selected!='cpu':message='已选择 '+device.currentText()+'。CPU 线程设置已保留，实际执行设备以任务结果为准。'
        elif missing_device or REASON:message='已选择 CPU。原加速配置当前不可用。'+(REASON or '请在模型与加速中检查扩展。')
        else:message='已选择 CPU。设置在启动识别时生效。'
        hint.setText(message)
    engine.currentIndexChanged.connect(show_device);device.currentIndexChanged.connect(show_device);show_device()
    stack.addStretch();footer=c.footer(root);footer.addWidget(c.label('修改仅对本次及后续 OCR 生效，不影响已有字幕。','muted',True),1)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
    footer.addWidget(buttons);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject)
    buttons.button(QDialogButtonBox.Save).setText('开始 OCR' if start_after else '保存')
    buttons.button(QDialogButtonBox.Cancel).setText('取消');buttons.setLayoutDirection(Qt.RightToLeft)
    buttons.button(QDialogButtonBox.Save).setProperty('styleRole','primary')
    c.theme(dialog)
    dialog.setStyleSheet(dialog.styleSheet()+"""
        QFrame#ocrSamplingPreset {background:#efebf7;border:1px solid #e2daef;border-radius:8px;min-height:30px;}
        QPushButton[styleRole="segment"] {border:1px solid transparent;background:transparent;color:#736985;padding:4px 4px;border-radius:6px;}
        QPushButton[styleRole="segment"]:checked {background:#ffffff;border:1px solid #dfd6ee;color:#5e3b9e;font-weight:600;}
        QPushButton[styleRole="segment"]:focus {border:1px solid #9e85b8;}
        QLabel#ocrDeviceAvailability {background:#f6f3fb;border:1px solid #eae4f4;border-radius:7px;padding:9px 11px;color:#6e6582;}
        """)
    from PySide6.QtGui import QPalette, QColor
    _hint_pal = sampling_hint.palette()
    for _grp in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
        _hint_pal.setColor(_grp, QPalette.ColorRole.WindowText, QColor('#736985'))
    sampling_hint.setPalette(_hint_pal)
    if dialog.exec()==QDialog.DialogCode.Accepted:
        if bridge.document.id!=document_id or bridge.document.project.settings is not settings:
            return False
        if language.currentData() is None:raise ValueError('没有可用的已部署语言模型，请使用完整安装包。')
        bridge.history.push(bridge.document.project,'修改 OCR 设置');settings.update(engine=engine.currentData(),ocr_model=model.currentData(),ocr_preprocess=preprocess.currentData(),ocr_device=device.currentData(),ocr_rapid_device=rapid_device if engine.currentData()=='tesseract' else device.currentData(),ocr_threads=threads.value(),language=language.currentData(),sample_fps=fps.value());bridge._changed()
        return True
    return False


def subtitle_style(bridge, parent=None):
    from . import carbon_widgets as c
    project = bridge.document.project
    revision = bridge.document.revision
    settings = project.settings
    dialog = QDialog(parent)
    dialog.setWindowTitle('字幕位置与排版（全工程）')
    dialog.resize(480, 340)
    root = c.layout(dialog)
    body = QWidget(); body.setObjectName('carbonBody')
    stack = c.layout(body, margins=(18, 14, 18, 14), space=12)
    root.addWidget(body, 1)
    card = c.card(stack)
    c.header(card, '位置与对齐基准', '应用于画布预览及 ASS 导出')
    form_host = QWidget(); form = QFormLayout(form_host)
    form.setContentsMargins(14, 12, 14, 14); form.setVerticalSpacing(10); form.setHorizontalSpacing(14)
    card.addWidget(form_host)
    def choices(key, options, default):
        box = QComboBox()
        box.setObjectName(key)
        for label,value in options:
            box.addItem(label,value)
        box.setCurrentIndex(max(0,box.findData(settings.get(key,default))))
        return box
    def percentage(key, default):
        spin = QDoubleSpinBox()
        spin.setObjectName(key)
        spin.setRange(5,95)
        spin.setValue(settings.get(key,default))
        spin.setSuffix(' %')
        return spin
    position = choices('subtitle_position',[('原文上方','above_original'),('画面底部','bottom'),('画面顶部','top'),('自定义位置','custom')],'above_original')
    height = percentage('subtitle_height_percent',80)
    horizontal = percentage('subtitle_horizontal_percent',50)
    alignment = choices('subtitle_custom_alignment',[('字幕底部居中',2),('字幕顶部居中',8)],2)
    height.valueChanged.connect(lambda _:position.setCurrentIndex(position.findData('custom')))
    alignment.activated.connect(lambda _:position.setCurrentIndex(position.findData('custom')))
    vertical = QCheckBox('竖排中文字幕（ASS）')
    vertical.setChecked(settings.get('subtitle_vertical',False))
    for label,widget in [('显示位置',position),('距顶部高度',height),('水平位置',horizontal),('自定义对齐基准',alignment),('排版方向',vertical)]:
        form.addRow(label,widget)
    stack.addStretch(1)
    footer = c.footer(root)
    footer.addWidget(QLabel('全工程预览与 ASS 导出均遵循此基准。'), 1)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
    footer.addWidget(buttons)
    save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
    cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
    if save_btn: save_btn.setText('保存'); save_btn.setProperty('styleRole', 'primary')
    if cancel_btn: cancel_btn.setText('取消')
    buttons.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    c.theme(dialog)
    if dialog.exec()!=QDialog.DialogCode.Accepted:
        return False
    if bridge.document.project is not project or bridge.document.revision!=revision:
        raise ValueError('工程已改变，请重新打开字幕位置与排版。')
    values=dict(subtitle_position=position.currentData(),subtitle_height_percent=height.value(),
                subtitle_horizontal_percent=horizontal.value(),subtitle_custom_alignment=alignment.currentData(),
                subtitle_vertical=vertical.isChecked())
    bridge.history.push(project,'修改全工程字幕位置与排版')
    settings.update(values)
    bridge._changed()
    return True


def inspect_qc_cue(bridge,cue,image=None):
    from . import carbon_widgets as c
    from PySide6.QtGui import QPixmap
    dialog=QDialog();dialog.setWindowTitle('成片质检详情');dialog.resize(680,740)
    root=c.layout(dialog)
    body=QWidget();body.setObjectName('carbonBody');stack=c.layout(body,margins=(18,14,18,14),space=10);root.addWidget(body,1)
    info_card=c.card(stack);info_box=c.layout(w_info:=QWidget(),margins=(14,12,14,12),space=6);info_card.addWidget(w_info)
    caption=QLabel('画面识别文字：'+(cue.get('text') or '（未识别到文字）'));caption.setWordWrap(True);info_box.addWidget(caption)
    if cue.get('expected_text'):
        reference=QLabel('工程参考字幕：'+str(cue['expected_text']));reference.setWordWrap(True);info_box.addWidget(reference)
    first=cue.get('first_frame');last=cue.get('last_frame')
    time=QLabel('起始帧：'+(str(first) if first is not None else '待核对')+'    结束帧：'+(str(last) if last is not None else '待核对'));info_box.addWidget(time)
    status=QLabel('时间边界：'+cue.get('boundary_status','needs_review')+' · 文字核对：'+cue.get('content_status','ocr_completeness_unverified')+' · 复核状态：'+cue.get('review_status','needs_review'));status.setWordWrap(True);info_box.addWidget(status)
    if image is not None:
        img_card=c.card(stack);img_box=c.layout(w_img:=QWidget(),margins=(12,12,12,12),space=6);img_card.addWidget(w_img)
        view=QLabel();view.setPixmap(QPixmap.fromImage(image));view.setAlignment(Qt.AlignmentFlag.AlignCenter);img_box.addWidget(view)
        evidence=QLabel('以上为快速抽检截取的代表帧；如需精确核对出入点可使用局部重查。');evidence.setWordWrap(True);img_box.addWidget(evidence)
    note_card=c.card(stack);note_box=c.layout(w_note:=QWidget(),margins=(14,12,14,12),space=8);note_card.addWidget(w_note)
    notes=QTextEdit();notes.setPlaceholderText('复核备注（选填）');notes.setMaximumHeight(82);note_box.addWidget(notes)
    row=QHBoxLayout();row.setSpacing(8);note_box.addLayout(row)
    for label,state,role in [('已人工复核','reviewed','primary'),('跳过','skipped',''),('保持待核对','needs_review','')]:
        button=c.button(label,None,role);row.addWidget(button)
        button.clicked.connect(lambda checked=False,state=state:(bridge.command('qc.review',{'cueId':cue['id'],'status':state,'note':notes.toPlainText()}),dialog.accept()))
    row.addStretch(1)
    rescan=c.button('调整识别区域并局部重查…',None,'soft');row.addWidget(rescan)
    rescan.clicked.connect(lambda:(dialog.accept(),bridge.command('qc.rescan',{'cueId':cue['id']})))
    footer=c.footer(root);footer.addStretch(1)
    close=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);close.rejected.connect(dialog.reject);footer.addWidget(close)
    if close.button(QDialogButtonBox.StandardButton.Close):
        close.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
    c.theme(dialog);dialog.exec()


def qc_rescan_dialog(bridge,cue):
    from . import carbon_widgets as c
    dialog=QDialog();dialog.setWindowTitle('单句局部重查');dialog.resize(570,380)
    root=c.layout(dialog)
    body=QWidget();body.setObjectName('carbonBody');stack=c.layout(body,margins=(18,14,18,14),space=12);root.addWidget(body,1)
    card=c.card(stack);c.header(card,'重查范围与 OCR 语言','生成补充记录，不改动工程字幕')
    form_host=QWidget();form=QFormLayout(form_host);form.setContentsMargins(14,12,14,12);form.setVerticalSpacing(9);card.addWidget(form_host)
    sample=cue.get('representative_frame') or cue.get('first_frame') or 1
    first=QSpinBox();first.setRange(1,2147483647);first.setValue(cue.get('first_frame') or max(1,sample-5))
    last=QSpinBox();last.setRange(1,2147483647);last.setValue(cue.get('last_frame') or sample+5)
    language=QLineEdit(bridge.document.project.settings.get('source_language','jpn+eng'));chinese_language=QLineEdit('chi_sim')
    form.addRow('起始帧',first);form.addRow('结束帧',last);form.addRow('原文 OCR 语言',language);form.addRow('中文 OCR 语言',chinese_language)
    roi_row=c.band(card,(14,0,14,12));roi=c.button('查看当前画面并调整识别区域…',lambda:bridge._prepare_qc_regions({}),'soft');roi_row.addWidget(roi);roi_row.addStretch(1)
    stack.addStretch(1)
    footer=c.footer(root)
    notes=QLabel('会自动向前后相邻帧微调探测出入点。');notes.setWordWrap(True);footer.addWidget(notes,1)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);footer.addWidget(buttons)
    ok_btn=buttons.button(QDialogButtonBox.StandardButton.Ok);cancel_btn=buttons.button(QDialogButtonBox.StandardButton.Cancel)
    if ok_btn: ok_btn.setText('开始重查'); ok_btn.setProperty('styleRole','primary')
    if cancel_btn: cancel_btn.setText('取消')
    buttons.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    c.theme(dialog)
    if dialog.exec()==QDialog.DialogCode.Accepted:
        bridge.command('qc.rescan',{'cueId':cue['id'],'firstFrame':first.value(),'lastFrame':last.value(),'sourceLanguage':language.text(),'chineseLanguage':chinese_language.text(),
                                  'chineseRoi':bridge._qc.get('chineseRoi'),'sourceRoi':bridge._qc.get('sourceRoi')})


def review_import_warnings(warnings):
    from . import carbon_widgets as c
    dialog=QDialog();dialog.setWindowTitle('导入提示与警告');dialog.resize(680,480)
    root=c.layout(dialog)
    body=QWidget();body.setObjectName('carbonBody');stack=c.layout(body,margins=(18,14,18,14),space=12);root.addWidget(body,1)
    notice_card=c.card(stack);nb=c.band(notice_card,(14,10,14,10))
    notice=QLabel('导入文件时检测到以下格式或内容提示，请核对是否影响使用：')
    notice.setWordWrap(True);nb.addWidget(notice,1)
    list_card=c.card(stack);lb=c.layout(w_det:=QWidget(),margins=(12,12,12,12));list_card.addWidget(w_det)
    details=QTextEdit();details.setReadOnly(True);details.setPlainText('\n\n'.join(f'{n}. {text}' for n,text in enumerate(warnings,1)));lb.addWidget(details)
    footer=c.footer(root);footer.addStretch(1)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);footer.addWidget(buttons)
    ok_btn=buttons.button(QDialogButtonBox.StandardButton.Ok);cancel_btn=buttons.button(QDialogButtonBox.StandardButton.Cancel)
    if ok_btn: ok_btn.setText('我知道了，继续'); ok_btn.setProperty('styleRole','primary')
    if cancel_btn: cancel_btn.setText('取消'); cancel_btn.setDefault(True)
    buttons.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject)
    c.theme(dialog)
    return dialog.exec()==QDialog.DialogCode.Accepted


def confirm_mode_switch(*, pending_only=False):
    """Require an explicit decision for the data the switch would discard."""
    message = ('切换会舍弃尚未完成的打点或编辑预览。留在当前模式可以继续编辑。' if pending_only else
               '将清空当前时间轴\n\n仅清空字幕时间轴，保留素材、独立标记和识别历史。工程文件不删除，清空后可撤销。')
    box=QMessageBox(QMessageBox.Icon.Warning, '切换工作模式', message)
    box.setObjectName('modeSwitchConfirmation')
    box.setTextFormat(Qt.TextFormat.PlainText)
    stay=box.addButton('留在原模式', QMessageBox.ButtonRole.RejectRole)
    stay.setObjectName('stayInCurrentMode')
    clear=box.addButton('舍弃草稿并切换' if pending_only else '清空时间轴并切换', QMessageBox.ButtonRole.DestructiveRole)
    clear.setObjectName('clearTimelineAndSwitch')
    box.setDefaultButton(stay)
    box.setEscapeButton(stay)
    apply_dialog_theme(box)
    box.exec()
    return box.clickedButton() is clear


def choose_asset_clear():
    box=QMessageBox(QMessageBox.Icon.Warning,'清除素材','当前内容已有编辑或无法证明是未改动的自动种子。请选择保留字幕并解绑转纯编辑，或先保存当前工程再新建。历史证据仍可撤销恢复。')
    box.setObjectName('assetClearConfirmation')
    cancel=box.addButton('取消',QMessageBox.ButtonRole.RejectRole);cancel.setObjectName('cancelAssetClear')
    detach=box.addButton('保留字幕，解绑转纯编辑',QMessageBox.ButtonRole.AcceptRole);detach.setObjectName('detachAssetToEdit')
    new=box.addButton('新建空工程（先处理保存）',QMessageBox.ButtonRole.ActionRole);new.setObjectName('newProjectForAsset')
    box.setDefaultButton(cancel);box.setEscapeButton(cancel);apply_dialog_theme(box);box.exec()
    return 'detach' if box.clickedButton() is detach else 'new' if box.clickedButton() is new else 'cancel'


def choose_ocr_source_reuse():
    """Require specific consent before bringing old original lyrics into OCR."""
    box=QMessageBox(QMessageBox.Icon.Question,'切入 OCR：如何处理已导入的原词',
                   '可以把上一模式的原词保留在工程历史里，不参与这次 OCR；也可以明确沿用，作为本次 OCR 的参考。原文件不会删除，可撤销。')
    box.setObjectName('ocrSourceReuseConfirmation')
    archive=box.addButton('归档原词，不作参考',QMessageBox.ButtonRole.AcceptRole)
    archive.setObjectName('archiveOcrSource')
    reuse=box.addButton('沿用原词作参考',QMessageBox.ButtonRole.ActionRole)
    reuse.setObjectName('reuseOcrSource')
    cancel=box.addButton('取消切换',QMessageBox.ButtonRole.RejectRole)
    cancel.setObjectName('cancelOcrSourceSwitch')
    box.setDefaultButton(archive);box.setEscapeButton(cancel)
    apply_dialog_theme(box);box.exec()
    return 'archive' if box.clickedButton() is archive else 'reuse' if box.clickedButton() is reuse else 'cancel'
