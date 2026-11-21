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
    dialog=QDialog();dialog.setWindowTitle(title);dialog.resize(720,560)
    layout=QVBoxLayout(dialog);text=QTextEdit();text.setReadOnly(True);text.setPlainText(body);layout.addWidget(text)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons);apply_dialog_theme(dialog)
    if modeless:
        from .api_review_dialogs import show_nonmodal
        return show_nonmodal(owner,dialog)
    dialog.exec()


def edit_pairing(bridge):
    from PySide6.QtWidgets import QListWidget,QAbstractItemView
    row=bridge._row();project=bridge.document.project
    dialog=QDialog();dialog.setWindowTitle('手动关联参考原文与 OCR 结果');dialog.resize(820,560)
    root=QVBoxLayout(dialog);root.addWidget(QLabel('手动调整当前句关联的参考原文与 OCR 识别结果，不会改动已编辑的字幕文字和时间轴。'))
    layout=QHBoxLayout();root.addLayout(layout);lists={}
    for name,title,records,selected in [('lyricIds','参考原文',project.lyrics,row.lyric_ids),('visualIds','OCR 识别结果',project.visuals,row.visual_ids)]:
        box=QGroupBox(title);col=QVBoxLayout(box);widget=QListWidget();widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection);col.addWidget(widget);layout.addWidget(box)
        for record in records:
            widget.addItem(record.text);item=widget.item(widget.count()-1);item.setData(Qt.ItemDataRole.UserRole,record.id);item.setSelected(record.id in selected)
        lists[name]=widget
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);root.addWidget(buttons)
    apply_dialog_theme(dialog)
    if dialog.exec()==QDialog.DialogCode.Accepted:
        bridge.command('row.pairing',{'id':row.id,**{key:[i.data(Qt.ItemDataRole.UserRole) for i in widget.selectedItems()] for key,widget in lists.items()}})


def choose_rescan(bridge,identity,result):
    from PySide6.QtWidgets import QListWidget,QAbstractItemView
    dialog=QDialog();dialog.setWindowTitle('单句重扫结果');dialog.resize(750,500)
    root=QVBoxLayout(dialog);notice=QLabel(result.get('reason') or '请选择要采用的识别结果，应用后建议结合画面微调起止帧。');notice.setWordWrap(True);root.addWidget(notice)
    listing=QListWidget();listing.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection);root.addWidget(listing)
    for v in result.get('visuals',[]):
        index=bridge.media_controller.index
        first=index.first(v['start']) if index else '?';last=index.last(v['end']) if index else '?'
        listing.addItem(f"F{first}–F{last} · {v['text']} · OCR {v['confidence']:.2f}")
        listing.item(listing.count()-1).setData(Qt.ItemDataRole.UserRole,v['id'])
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Cancel);root.addWidget(buttons)
    buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(lambda:(bridge.command('row.adoptRescan',{'id':identity,'visualIds':[i.data(Qt.ItemDataRole.UserRole) for i in listing.selectedItems()]}),dialog.accept()))
    buttons.rejected.connect(dialog.reject);apply_dialog_theme(dialog);dialog.exec()


def ocr_settings(bridge,providers,*,start_after=False):
    document_id=bridge.document.id
    dialog=QDialog();dialog.setWindowTitle('OCR 识别设置');dialog.resize(570,440)
    root=QVBoxLayout(dialog);form=QFormLayout();root.addLayout(form);settings=bridge.document.project.settings
    engine=QComboBox();engine.setObjectName('ocrEngine');engine.addItem('RapidOCR（推荐）','rapidocr');engine.addItem('Tesseract · CPU','tesseract');engine.setCurrentIndex(max(0,engine.findData(settings.get('engine','rapidocr'))))
    device=QComboBox();device.setObjectName('ocrComputeDevice');device.addItem('CPU','cpu')
    for key,name,label in [('directml','DmlExecutionProvider','显卡加速（DirectML）'),('cuda','CUDAExecutionProvider','显卡加速（CUDA）')]:
        if name in providers:device.addItem(label,key)
    previous_device=settings.get('ocr_device','cpu')
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
    for title,control in [('识别引擎',engine),('识字模型',model),('识别输入',preprocess),('计算设备',device),('线程数',threads),('识别语言',language),('抽样频率',fps)]:form.addRow(title,control)
    sampling_hint=QLabel('一般歌曲用 3 帧/秒，快歌可试 6 帧/秒，处理时间会增加。更密的采样有助于定位出现和消失时间，不提高单帧识字准确率。');sampling_hint.setObjectName('ocrSamplingHint');sampling_hint.setStyleSheet('color: #736985;');sampling_hint.setWordWrap(True);root.addWidget(sampling_hint)
    hint=QLabel(('此前选择的 GPU 后端当前不可用；保存后将改用 CPU。\n' if missing_device else '')+
               '已检测到的计算后端：'+', '.join({'CPUExecutionProvider':'CPU','DmlExecutionProvider':'显卡加速（DirectML）','CUDAExecutionProvider':'显卡加速（CUDA）'}.get(provider,provider) for provider in providers)+'\n仅列出已检测到的后端，实际初始化失败会明确报错，不会假称 GPU 已启用。\nTesseract 仅使用 CPU。修改后下次运行 OCR 生效，不影响已有字幕。')
    hint.setObjectName('ocrDeviceAvailability');hint.setWordWrap(True);root.addWidget(hint)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);root.addWidget(buttons);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject)
    buttons.button(QDialogButtonBox.StandardButton.Save).setText('开始 OCR' if start_after else '保存')
    apply_dialog_theme(dialog)
    if dialog.exec()==QDialog.DialogCode.Accepted:
        if bridge.document.id!=document_id or bridge.document.project.settings is not settings:
            return False
        if language.currentData() is None:raise ValueError('没有可用的已部署语言模型，请使用完整安装包。')
        bridge.history.push(bridge.document.project,'修改 OCR 设置');settings.update(engine=engine.currentData(),ocr_model=model.currentData(),ocr_preprocess=preprocess.currentData(),ocr_device=device.currentData(),ocr_threads=threads.value(),language=language.currentData(),sample_fps=fps.value());bridge._changed()
        return True
    return False


def subtitle_style(bridge, parent=None):
    project = bridge.document.project
    revision = bridge.document.revision
    settings = project.settings
    dialog = QDialog(parent)
    dialog.setWindowTitle('字幕位置与排版（全工程）')
    root = QVBoxLayout(dialog)
    form = QFormLayout()
    root.addLayout(form)
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
    for label,widget in [('位置',position),('距顶部高度',height),('水平位置',horizontal),('自定义对齐基准',alignment),('排版',vertical)]:
        form.addRow(label,widget)
    root.addWidget(QLabel('应用到全工程的字幕预览与 ASS 导出。'))
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
    root.addWidget(buttons)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    apply_dialog_theme(dialog)
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
    from PySide6.QtGui import QPixmap
    dialog=QDialog();dialog.setWindowTitle('成片质检详情');dialog.resize(680,760)
    root=QVBoxLayout(dialog);caption=QLabel('画面识别文字：'+(cue.get('text') or '（未识别到文字）'));caption.setWordWrap(True);root.addWidget(caption)
    if cue.get('expected_text'):
        reference=QLabel('工程参考字幕：'+str(cue['expected_text']));reference.setWordWrap(True);root.addWidget(reference)
    first=cue.get('first_frame');last=cue.get('last_frame')
    time=QLabel('起始帧：'+(str(first) if first is not None else '待核对')+'    结束帧：'+(str(last) if last is not None else '待核对'));root.addWidget(time)
    status=QLabel('时间边界：'+cue.get('boundary_status','needs_review')+'\n文字核对：'+cue.get('content_status','ocr_completeness_unverified')+'\n复核状态：'+cue.get('review_status','needs_review'));status.setWordWrap(True);root.addWidget(status)
    if image is not None:
        view=QLabel();view.setPixmap(QPixmap.fromImage(image));view.setAlignment(Qt.AlignmentFlag.AlignCenter);root.addWidget(view)
    evidence=QLabel('上方为当前句的质检截图；快速抽检仅截取代表帧，如需核对准确出入点可使用局部重查。');evidence.setWordWrap(True);root.addWidget(evidence)
    notes=QTextEdit();notes.setPlaceholderText('复核备注（选填）');notes.setMaximumHeight(90);root.addWidget(notes)
    row=QHBoxLayout();root.addLayout(row)
    for label,state in [('已人工复核','reviewed'),('跳过','skipped'),('保持待核对','needs_review')]:
        button=QPushButton(label);row.addWidget(button)
        button.clicked.connect(lambda checked=False,state=state:(bridge.command('qc.review',{'cueId':cue['id'],'status':state,'note':notes.toPlainText()}),dialog.accept()))
    rescan=QPushButton('调整识别区域并局部重查…');root.addWidget(rescan)
    rescan.clicked.connect(lambda:(dialog.accept(),bridge.command('qc.rescan',{'cueId':cue['id']})))
    close=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);close.rejected.connect(dialog.reject);root.addWidget(close);apply_dialog_theme(dialog);dialog.exec()


def qc_rescan_dialog(bridge,cue):
    dialog=QDialog();dialog.setWindowTitle('单句局部重查');dialog.resize(570,350)
    root=QVBoxLayout(dialog);intro=QLabel('针对当前句指定帧范围重新运行本地 OCR 精查，生成补充质检记录，不会改动工程字幕或时间轴。');intro.setWordWrap(True);root.addWidget(intro)
    form=QFormLayout();root.addLayout(form)
    sample=cue.get('representative_frame') or cue.get('first_frame') or 1
    first=QSpinBox();first.setRange(1,2147483647);first.setValue(cue.get('first_frame') or max(1,sample-5))
    last=QSpinBox();last.setRange(1,2147483647);last.setValue(cue.get('last_frame') or sample+5)
    language=QLineEdit(bridge.document.project.settings.get('source_language','jpn+eng'));chinese_language=QLineEdit('chi_sim')
    form.addRow('起始帧',first);form.addRow('结束帧',last);form.addRow('原文 OCR 语言',language);form.addRow('中文 OCR 语言',chinese_language)
    roi=QPushButton('查看当前画面并调整识别区域…');root.addWidget(roi);roi.clicked.connect(lambda:bridge._prepare_qc_regions({}))
    notes=QLabel('精查时会自动向前后相邻帧微调探测出入点；若画面较复杂仍无法识别，可直接人工复核。');notes.setWordWrap(True);root.addWidget(notes)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);root.addWidget(buttons)
    apply_dialog_theme(dialog)
    if dialog.exec()==QDialog.DialogCode.Accepted:
        bridge.command('qc.rescan',{'cueId':cue['id'],'firstFrame':first.value(),'lastFrame':last.value(),'sourceLanguage':language.text(),'chineseLanguage':chinese_language.text(),
                                  'chineseRoi':bridge._qc.get('chineseRoi'),'sourceRoi':bridge._qc.get('sourceRoi')})


def review_import_warnings(warnings):
    dialog=QDialog();dialog.setWindowTitle('导入提示与警告');dialog.resize(680,480)
    root=QVBoxLayout(dialog)
    notice=QLabel('导入文件时检测到以下格式或内容提示，请核对是否影响使用：')
    notice.setWordWrap(True);root.addWidget(notice)
    details=QTextEdit();details.setReadOnly(True);details.setPlainText('\n\n'.join(f'{n}. {text}' for n,text in enumerate(warnings,1)));root.addWidget(details)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText('我知道了，继续')
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
    buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);root.addWidget(buttons)
    apply_dialog_theme(dialog)
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
