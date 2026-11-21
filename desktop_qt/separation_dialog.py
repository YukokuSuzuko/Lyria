"""Independent separation and A/B audition. No editable document is accepted."""
from copy import deepcopy
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QComboBox, QDoubleSpinBox, QPushButton, QProgressBar,
    QFileDialog, QMessageBox, QDialogButtonBox, QScrollArea, QWidget, QCheckBox)
from lyricsync.local_models import ModelManager, UVR, SEPARATION_MODELS, separation_spec, default_cache, readable_bytes
from lyricsync.separation_preview import validate_separation_options, generate_separation_preview, file_stamp
from .audio_preview import AudioPreview
from .local_model_tasks import model_tasks
from .model_dialog_layout import ResponsiveRow
from .native_theme import apply_dialog_theme


def label(text):
    item=QLabel(text);item.setTextFormat(Qt.TextFormat.PlainText);item.setWordWrap(True)
    item.setMinimumWidth(0)
    return item


class SeparationDialog(QDialog, AudioPreview):
    def __init__(self, parent=None, *, default_audio='', initial_options=None, save_options=lambda value:None):
        super().__init__(parent)
        self.setObjectName('separationOnlyDialog');self.setWindowTitle('仅人声分离 · 试听比较')
        self.resize(720,650)
        self._init_preview();self._service=model_tasks();self._save_options=save_options
        self._initial=dict(initial_options or {});self._records=[]
        root=QVBoxLayout(self)
        root.addWidget(label('只运行所选 HQ3/HQ1 人声分离模型。不需要歌词、Qwen 或 ASR 模型，不自动打轴，不修改工程或逐句确认状态。'))
        scroll=QScrollArea();scroll.setWidgetResizable(True);root.addWidget(scroll,1)
        body=QWidget();scroll.setWidget(body);layout=QVBoxLayout(body)
        form=QFormLayout();form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows);layout.addLayout(form)
        self.audio=QLineEdit(default_audio);self.audio.setObjectName('separationAudioPath')
        self.cache=QLineEdit(self._initial.get('cache_root',str(default_cache())))
        self.runtime=QLineEdit(self._initial.get('runtime_python',''))
        self.runtime.setPlaceholderText('已有独立 Python 3.12 环境的 Scripts\\python.exe')
        self._inputs=[self.audio,self.cache,self.runtime];self._browsers=[]
        form.addRow('音频 / 视频',self._browse_row(self.audio,'media'))
        self.separation_model=QComboBox();self.separation_model.setObjectName('separationOnlyModel')
        for spec in SEPARATION_MODELS.values():self.separation_model.addItem(spec.files[0].name.removesuffix('.onnx'),spec.id)
        self.separation_model.setCurrentIndex(max(0,self.separation_model.findData(self._initial.get('separation_model_id',UVR.id))))
        form.addRow('人声分离模型',self.separation_model)
        self.device=QComboBox();self.device.addItem('GPU 加速（CUDA）','cuda:0');self.device.addItem('CPU（速度较慢）','cpu')
        self.device.setCurrentIndex(1 if self._initial.get('device')=='cpu' else 0);form.addRow('计算设备',self.device)
        self.whole_media=QCheckBox('处理完整音频（自动分段衔接）'); self.whole_media.setChecked(True)
        form.addRow(self.whole_media)
        self.offset=QDoubleSpinBox();self.offset.setDecimals(3);self.offset.setRange(0,86400 * 365)
        self.duration=QDoubleSpinBox();self.duration.setDecimals(3);self.duration.setRange(.001,86400 * 365);self.duration.setValue(300)
        self.offset.setEnabled(False); self.duration.setEnabled(False)
        self.whole_media.toggled.connect(lambda full: (self.offset.setEnabled(not full),self.duration.setEnabled(not full)))
        form.addRow('自定义开始时间（秒）',self.offset);form.addRow('自定义处理时长（秒）',self.duration)
        form.addRow('模型缓存目录',self._browse_row(self.cache,'directory'))
        form.addRow('Python 环境',self._browse_row(self.runtime,'python'))
        self.model_status=label('');form.addRow(self.model_status)
        manage=ResponsiveRow();layout.addWidget(manage)
        self.download=QPushButton('下载所选分离模型');self.download.clicked.connect(self._download)
        self.import_model=QPushButton('导入分离模型');self.import_model.clicked.connect(self._import)
        self.verify=QPushButton('完整校验');self.verify.clicked.connect(lambda:self._model_task('verify'))
        self.configure=QPushButton('模型与环境设置');self.configure.clicked.connect(self._configure)
        for button in (self.download,self.import_model,self.verify,self.configure):manage.addWidget(button)
        layout.addWidget(label('默认处理完整音频，内部按模型与资源分段衔接，也可取消勾选后指定片段。运行失败会显示原因，不自动换模型或设备。关闭窗口不取消后台任务，可重新打开查看；需中断请点“取消当前任务”。'))
        self.run=QPushButton('开始仅人声分离');self.run.setObjectName('runSeparationOnly');self.run.clicked.connect(self._generate);layout.addWidget(self.run)
        self.results=QComboBox();self.results.setObjectName('separationAuditionResults');layout.addWidget(self.results)
        self.results.currentIndexChanged.connect(self._select_result)
        self.result_info=label('暂无分离结果。完成后可切换最近 12 次结果试听，缓存文件不会因退出窗口被删除。');layout.addWidget(self.result_info)
        audition=ResponsiveRow();layout.addWidget(audition)
        self.original=QPushButton('试听原音频');self.vocals=QPushButton('试听所选人声');self.stop=QPushButton('停止试听')
        self.original.clicked.connect(lambda:self._listen(False));self.vocals.clicked.connect(lambda:self._listen(True));self.stop.clicked.connect(self._stop_preview)
        for button in (self.original,self.vocals,self.stop):audition.addWidget(button)
        self.status=label('就绪');self.status.setObjectName('separationStatus');layout.addWidget(self.status)
        self.progress=QProgressBar();layout.addWidget(self.progress)
        self.cancel=QPushButton('取消当前任务');self.cancel.clicked.connect(lambda:self._service.stop('cancel'));layout.addWidget(self.cancel)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.rejected.connect(self.reject);root.addWidget(buttons)
        self._service.progress.connect(self._progress);self._service.outcome.connect(self._outcome);self._service.finished.connect(self._finished)
        self.separation_model.currentIndexChanged.connect(self._model_status);self.cache.textChanged.connect(self._model_status)
        self._reload_results();self._model_status();self._busy_state()
        if self._service.task is not None:self._progress(self._service.percent,self._service.message)
        elif self._service.action=='separate':
            # Outcome belongs to the application, not the closed dialog. Do not
            # mislabel an unrelated download/import failure as a separation run.
            if self._service.error:
                self.status.setText('上次人声分离未完成：'+self._service.error)
            elif isinstance(self._service.value,dict) and self._service.value.get('kind')=='separation_preview':
                self.status.setText('上次人声分离已完成，可试听比较；未生成歌词时间轴。')
        apply_dialog_theme(self)

    def _browse_row(self,field,kind):
        row=QHBoxLayout();row.addWidget(field);button=QPushButton('选择');row.addWidget(button);self._browsers.append(button)
        def browse():
            if kind=='directory':value=QFileDialog.getExistingDirectory(self,'选择模型缓存目录',field.text())
            else:value,_=QFileDialog.getOpenFileName(self,'选择 Python 环境' if kind=='python' else '选择音视频',field.text(),
                'Python (python.exe)' if kind=='python' else '媒体 (*.mp4 *.mkv *.mov *.avi *.webm *.ts *.wav *.flac *.mp3 *.m4a *.aac *.ogg *.opus);;所有文件 (*)')
            if value:field.setText(value)
        button.clicked.connect(browse);return row

    def options(self):
        return dict(self._initial,audio_path=self.audio.text(),cache_root=self.cache.text(),runtime_python=self.runtime.text(),
            separation_model_id=self.separation_model.currentData(),device=self.device.currentData(),
            offset_seconds=0. if self.whole_media.isChecked() else self.offset.value(),
            duration_seconds=None if self.whole_media.isChecked() else self.duration.value())

    def _start(self,work,action):
        if self._service.task is not None:return False
        self._stop_preview()
        started=self._service.start(work,self.options(),action)
        self._busy_state();return started

    def _generate(self):
        if self._service.task is not None:return
        try:
            options=validate_separation_options(self.options())
            self._save_options(options)
            self._start(lambda cancel,progress:generate_separation_preview(options=options,cancel=cancel,progress=progress),'separate')
        except Exception as error:self.status.setText(str(error))

    def _model_task(self,action,folder=None):
        if self._service.task is not None:return
        try:
            manager=ModelManager(self.cache.text());model_id=self.separation_model.currentData()
            args=(folder,) if folder else ()
            kwargs={'model_id':model_id}
            if action=='download':kwargs['source']='uvr'
            self._start(lambda cancel,progress:getattr(manager,action)(*args,cancel=cancel,progress=progress,**kwargs),action)
        except Exception as error:self.status.setText(str(error))

    def _download(self):
        spec=separation_spec(self.separation_model.currentData())
        box=QMessageBox(QMessageBox.Icon.Question,'下载人声分离模型',
            f'从 UVR 官方 GitHub 下载 {spec.files[0].name}（约 {readable_bytes(spec.size)}）到所选缓存目录，完成后校验。只下载这一款，不下载歌词对齐或 ASR 模型。是否继续？',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,self)
        box.setTextFormat(Qt.TextFormat.PlainText);box.setDefaultButton(QMessageBox.StandardButton.No);apply_dialog_theme(box)
        if box.exec()==QMessageBox.StandardButton.Yes:self._model_task('download')

    def _import(self):
        folder=QFileDialog.getExistingDirectory(self,'选择所选分离模型文件夹')
        if folder:self._model_task('import_folder',folder)

    def _configure(self):
        from .local_alignment_dialog import LocalAlignmentDialog
        from lyricsync.project_document import ProjectDocument
        dialog=LocalAlignmentDialog(ProjectDocument(),self,setup_only=True,initial_options=self.options())
        if dialog.exec()==QDialog.DialogCode.Accepted:
            values=dialog.options();self._initial.update(values);self._save_options(values)
            self.cache.setText(values['cache_root']);self.runtime.setText(values['runtime_python'])
            self.device.setCurrentIndex(max(0,self.device.findData(values['device'])))
            self.separation_model.setCurrentIndex(max(0,self.separation_model.findData(values['separation_model_id'])))
        dialog.deleteLater();self._busy_state();self._model_status()

    def _model_status(self):
        try:
            value=ModelManager(self.cache.text()).status(self.separation_model.currentData())
            text='模型缺少文件，请先下载或导入' if value['missing'] else '模型文件大小不符，请重新校验' if value['wrong_size'] else '已完整校验 · 文件未变动' if value.get('verification_cached') else '文件在位，运行前将完整校验'
            self.model_status.setText(text)
        except Exception as error:self.model_status.setText(str(error))

    def _progress(self,percent,message):
        self.progress.setValue(percent);self.status.setText(message);self._busy_state()

    def _outcome(self,value,error):
        if error:self.status.setText(error)
        elif isinstance(value,dict) and value.get('kind')=='separation_preview':
            self._reload_results();self.status.setText('人声分离完成，可试听比较。未生成歌词时间轴。')
        else:self.status.setText('本地模型操作完成。')
        self._busy_state()

    def _finished(self):
        self._busy_state();self._model_status()

    def _busy_state(self):
        busy=self._service.task is not None
        self.progress.setVisible(busy);self.cancel.setVisible(busy);self.cancel.setEnabled(busy and not self._service.stop_reason)
        for item in self._inputs+self._browsers+[self.separation_model,self.device,self.whole_media,self.download,self.import_model,self.verify,self.configure,self.run]:item.setEnabled(not busy)
        self.offset.setEnabled(not busy and not self.whole_media.isChecked())
        self.duration.setEnabled(not busy and not self.whole_media.isChecked())
        available=bool(self._records) and not busy
        self.original.setEnabled(available);self.vocals.setEnabled(available);self.results.setEnabled(available)

    def _reload_results(self):
        self._stop_preview();self._records=deepcopy(self._service.separation_results)
        self.results.blockSignals(True);self.results.clear()
        for record in self._records:
            name=separation_spec(record['model_id']).files[0].name.removesuffix('.onnx')
            self.results.addItem(f"{name} · {Path(record['source_path']).name} · {record['offset_seconds']:.3f}–{record['offset_seconds']+record['duration_seconds']:.3f} 秒")
        self.results.setCurrentIndex(len(self._records)-1);self.results.blockSignals(False);self._select_result()

    def _select_result(self,*_):
        self._stop_preview();index=self.results.currentIndex()
        if 0<=index<len(self._records):
            record=self._records[index]
            self.result_info.setText('原素材：'+record['source_path']+'\n人声缓存：'+record['stem_path']+'\n'+record.get('warning','')+'\n仅分离完成，不代表时间轴完成。')

    def _listen(self,vocals):
        if self._service.task is not None:return
        index=self.results.currentIndex()
        if not 0<=index<len(self._records):return
        record=self._records[index]
        try:
            for path_key,stamp_key in (('source_path','source_stamp'),('stem_path','stem_stamp')):
                if file_stamp(record[path_key])!=record[stamp_key]:raise ValueError('原素材或人声缓存已变化，请重新分离后试听。')
            self._listen_audio(record,vocals)
        except (OSError,ValueError) as error:self.status.setText('无法试听：'+str(error))

    def reject(self):
        self._stop_preview();super().reject()

    def closeEvent(self,event):
        self._stop_preview();super().closeEvent(event)
