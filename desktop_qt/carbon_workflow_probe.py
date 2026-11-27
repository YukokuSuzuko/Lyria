from copy import deepcopy
import json,sys
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog,QLabel,QPushButton,QComboBox,QDoubleSpinBox,QSpinBox,QFileDialog,QProgressBar
from tools.qt_event_loop_wait import wait_until,delay
from lyricsync.model import Project,Lyric,Mapping,Translation
from lyricsync.local_models import ModelManager,NEXTFIRE,UVR
from lyricsync.split_profiles import ACTIVE
from lyricsync.capsule_pairing import state
from . import dialogs,split_dialog
from .local_alignment_settings import save
from .local_model_tasks import model_tasks
from .pairing_workbench import pairing_dialog
import onnxruntime

def remaining(app,bridge,window,tag,out,capture,modal,report):
    lab=Path(sys.executable).resolve().parents[2];cache=lab/'models';folder=cache/UVR.id
    media=bridge.media_controller;checks=report['checks']=[];ok=checks.append
    def snap(widget,name):
        key='alignment' if name.startswith('alignment') else 'pair' if name.startswith('pair-') else 'ocr' if name.startswith('ocr') else 'guide' if name=='download-progress' else 'other'
        wide={'alignment':(680,622),'pair':(1040,632),'ocr':(600,519),'guide':(616,581),'other':(950,620)}
        narrow={'alignment':(550,568),'pair':(760,580),'ocr':(490,495),'guide':(500,550),'other':(690,560)}
        w,h=(narrow if 'narrow' in tag else wide)[key];capture(widget,name,w,h)
    manager=ModelManager(cache)
    if not manager.status(NEXTFIRE.id)['verification_cached']:manager.verify(NEXTFIRE.id)
    save(bridge.storage_dir,{'cache_root':str(cache),'reading_tool':'manual','model_id':NEXTFIRE.id})
    work=model_tasks()
    import time
    # Real model-manager task with controlled progress/cancel, without network.
    work=model_tasks()
    def verify(cancel,progress):
        progress(12,'离线校验任务开始')
        for _ in range(180):
            if cancel():raise InterruptedError('已取消')
            time.sleep(.025)
        return ModelManager(cache).verify(UVR.id,cancel=cancel,progress=progress)
    assert work.start(verify,{},'verify')
    def check_busy(d):
        assert d.findChild(QProgressBar).isVisible()
        next(b for b in d.findChildren(QPushButton) if b.text()=='取消任务').click()
    modal(lambda:split_dialog.guide(bridge,'settings'),check_busy)
    for _ in range(120):
        if work.task is None:break
        delay(25)
    assert work.task is None and work.stop_reason=='cancel'
    ok('实际运行中的受控离线任务显示进度并取消')
    # Feed the original HQ3 bytes through the application's real pinned download
    # procedure, with an in-process transport. No network or extra model is fetched.
    transfer_root=out/'transfer';manager=ModelManager(transfer_root);asset=UVR.files[0]
    class Transport:
        def open(self,request,timeout):
            self.file=(folder/UVR.revision/asset.name).open('rb');self.url=request.full_url
            self.start=int(request.get_header('Range','bytes=0-')[6:-1]);self.file.seek(self.start)
            self.headers={'Content-Range':f'bytes {self.start}-{asset.size-1}/{asset.size}'}
            return self
        def geturl(self):return self.url
        def getcode(self):return 206 if self.start else 200
        def read(self,n):time.sleep(.015);return self.file.read(min(n,262144))
        def __enter__(self):return self
        def __exit__(self,*args):self.file.close()
    def transfer(cancel,progress):return manager.download(UVR.id,source='uvr',cancel=cancel,progress=progress,opener=Transport())
    def wait_job():
        wait_until(lambda:work.task is None,timeout_ms=45000)
    assert work.start(transfer,{},'download')
    def pause_download(d):
        assert d.findChild(QProgressBar).isVisible(),(work.error,work.action)
        snap(d,'download-progress')
        next(b for b in d.findChildren(QPushButton) if b.text()=='暂停下载').click()
    modal(lambda:split_dialog.guide(bridge,'settings'),pause_download);wait_job()
    assert work.stop_reason=='pause'
    modal(lambda:split_dialog.guide(bridge,'settings'),lambda d:next(b for b in d.findChildren(QPushButton) if b.text()=='继续下载').click())
    wait_job();assert manager.status(UVR.id)['verification_cached'] and not work.error
    ok('受控本机传输：原下载器、真实 HQ3 字节、暂停/重开/继续及固定 SHA-256，未联网')

    song=json.loads((lab/'fixtures/carbon-lyrics.json').read_bytes()); demo=Project(lyrics=[Lyric('l'+str(i),text,t,t+.8) for i,(t,text) in enumerate(song)])
    demo.mappings=[Mapping('m'+str(i),[],['l'+str(i)],start=t,end=t+.8,text_role='source_import',text_override=text) for i,(t,text) in enumerate(song)];demo.inputs['audio']=str(lab/'fixtures/alignment.wav')
    bridge._adopt_project(demo);bridge._entry_mode='audio'
    axis=lambda:deepcopy(bridge.document.project.to_dict()['mappings'])
    before=axis()
    def alignment(d):
        assert d.objectName()=='localAlignmentDialog'
        snap(d,'alignment')
        assert d.buttons.mapTo(d,d.buttons.rect().topRight()).x()>d.width()-35
        from PySide6.QtWidgets import QToolButton
        next(b for b in d.tabs.cornerWidget().findChildren(QPushButton) if b.text()=='模型与环境').click()
        assert d.tabs.currentWidget() is d.manage_scroll,'环境页未打开'
        next(b for b in d.manage_scroll.findChildren(QPushButton) if b.text()=='安装与许可').click()
        assert d.tabs.currentWidget() is d.license_page
        next(b for b in d.tabs.cornerWidget().findChildren(QPushButton) if b.text()=='任务日志').click()
        assert d.logs.isVisible()
        d.tabs.setCurrentWidget(d.settings_scroll)
        menu=d.findChild(QPushButton,'lyricSourceMenu').menu()
        toggle=next(a for a in menu.actions() if a.text()=='编辑媒体路径');toggle.trigger();assert d.audio.isVisible();toggle.trigger()
        assert d.source_preview.toPlainText().splitlines()==[x[1] for x in song]
        d.review_readings.click();assert d.reading_table.isVisible();d.review_readings.click()
        assert not d.progress.isVisible() and not d.main_source_confirm.isVisible()
        assert not d.separation_model.isVisible()
        d.separate.setChecked(True);assert d.separation_model.isVisible();snap(d,'alignment-separation')
        path_picker=QFileDialog.getOpenFileName;prior=d.source_preview.toPlainText()
        try:
            QFileDialog.getOpenFileName=staticmethod(lambda *args:('', ''))
            d._choose_external()
        finally:QFileDialog.getOpenFileName=path_picker
        assert d.text_source.currentData()=='reference' and d.source_preview.toPlainText()==prior
        d.text_source.setCurrentIndex(d.text_source.findData('main'))
        assert d.main_source_confirm.isVisible();snap(d,'alignment-confirm')
        d.main_source_confirm.setChecked(True);assert d._confirmed_main
        d.text_source.setCurrentIndex(d.text_source.findData('reference'))
        d.text_source.setCurrentIndex(d.text_source.findData('main'));assert not d.main_source_confirm.isChecked()
        d.text_source.setCurrentIndex(d.text_source.findData('reference'))
    modal(lambda:bridge._configure_vocal(),alignment);assert axis()==before
    ok('原入口、原型同源歌词、可选分离、核对读音、取消选文件、来源确认失效')
    def pair(d):
        snap(d,'pair-empty')
        assert d.empty_pool.width()>=d.empty_pool.sizeHint().width()
        assert d.paste_pool.width()>=d.paste_pool.sizeHint().width()
        report['emptyImport']={'allocated':d.empty_pool.width(),'requested':d.empty_pool.sizeHint().width(),'panel':d.pool_hint.width()}
        assert d.empty_pool.isVisible() and d.paste_pool.isVisible() and not d.import_pool.isVisible()
        d.load_pool([Translation('w1','译文甲'),Translation('w2','译文乙'),Translation('w3','未配译文')])
        assert not d.empty_pool.isVisible() and d.import_pool.isVisible()
        d.add_all()
        assert d.change('merge',ids=['w1','w2'])
        pool=state(d._draft);piece=next(x['id'] for x in pool['pieces'] if x['origins']==['w1','w2'])
        assert d.change('bind',target=pool['groups'][0]['id'],ids=[piece])
        assert axis()==before
        d.history(False);d.history(True);snap(d,'pair-merged')
        d.apply();assert axis()!=before
    d=pairing_dialog(bridge);d.show();app.processEvents();pair(d);d.deleteLater()
    assert any(t.id=='w3' for t in bridge.document.project.translations)
    bridge._history(False);assert axis()==before
    tr=Project(translations=[Translation('only','只有译文')]);bridge._adopt_project(tr);bridge._entry_mode='ocr'
    d=pairing_dialog(bridge);assert d.windowTitle()=='与原文配对' and d.table.horizontalHeaderItem(0).text()=='OCR 识别结果（只读）'
    d.show();app.processEvents();snap(d,'translation-only');d.reject();d.deleteLater()
    ok('配对等待导入、合并、仅译文；独立草案、应用一次撤销，保留未配译文')
    expected=deepcopy(bridge.document.project.to_dict())
    def ocr(d):
        engine=d.findChild(QComboBox,'ocrEngine');device=d.findChild(QComboBox,'ocrComputeDevice');preset=d.findChild(QComboBox,'ocrSamplingPreset');fps=d.findChild(QDoubleSpinBox,'ocrSampleFps');threads=d.findChild(QSpinBox)
        assert fps.value()==3 and d.findChild(QPushButton,'ocrPreset3').isChecked()
        assert 'TensorrtExecutionProvider' not in d.findChild(QLabel,'ocrDeviceAvailability').text()
        snap(d,'ocr')
        assert (device.findData('cuda')>=0)==(ACTIVE=='nvidia')
        if ACTIVE=='nvidia':
            swap=d.findChild(QPushButton,'ocrChooseCuda');assert swap.isVisible();swap.click()
            assert device.currentData()=='cuda' and not threads.isEnabled()
        for index,value in [(1,6),(2,10)]:
            d.findChild(QPushButton,'ocrPreset'+str(value)).click();assert fps.value()==value
            if value==6:snap(d,'ocr-six')
        fps.setValue(4.25);assert not any(d.findChild(QPushButton,'ocrPreset'+str(r)).isChecked() for r in (3,6,10))
        n=threads.value();engine.setCurrentIndex(engine.findData('tesseract'))
        assert device.currentData()=='cpu' and not device.isEnabled() and threads.isEnabled();snap(d,'ocr-tesseract')
        engine.setCurrentIndex(engine.findData('rapidocr'))
        assert threads.value()==n and device.currentData()==('cuda' if ACTIVE=='nvidia' else 'cpu')
    modal(lambda:dialogs.ocr_settings(bridge,onnxruntime.get_available_providers(),start_after=True),ocr)
    assert expected==bridge.document.project.to_dict()
    from PySide6.QtWidgets import QDialogButtonBox
    from lyricsync.storage import atomic_json
    from .local_alignment_settings import load
    def dismiss(d,route,button=None):
        if route=='escape':QTest.keyClick(d,Qt.Key_Escape)
        elif route=='close':assert d.close()
        else:button.click()
        assert not d.isVisible()
    for route in ('button','escape','close'):
        prefpath,_=split_dialog.preferences(bridge);atomic_json(prefpath,{})
        def guide_exit(d):
            dismiss(d,route,d.findChild(QPushButton,'splitLater'))
        modal(lambda:split_dialog.guide(bridge,'startup'),guide_exit)
        assert split_dialog.preferences(bridge)[1]['seen']
        def ocr_exit(d):
            d.findChild(QDoubleSpinBox,'ocrSampleFps').setValue(7.5)
            dismiss(d,route,d.findChild(QDialogButtonBox).button(QDialogButtonBox.Cancel))
        modal(lambda:dialogs.ocr_settings(bridge,onnxruntime.get_available_providers(),start_after=True),ocr_exit)
        assert expected==bridge.document.project.to_dict() and not bridge._task.get('running')
        bridge._entry_mode='audio';prior=bridge.document.snapshot()
        had_undo=(len(bridge.history.undo_stack),len(bridge.history.redo_stack))
        d=pairing_dialog(bridge);d.show();app.processEvents()
        d.load_pool([Translation('cancel-demo','待撤回译文')])
        dismiss(d,route,next(b for b in d.findChildren(QPushButton) if b.text()=='关闭'))
        assert bridge.document.snapshot()==prior and (len(bridge.history.undo_stack),len(bridge.history.redo_stack))==had_undo
        d.deleteLater()
        split_dialog_path,preferences=split_dialog.preferences(bridge)
        atomic_json(split_dialog_path,dict(preferences,never=True))
        def align_exit(d):
            d.language.setCurrentIndex(d.language.findData('English'));d._external_rows=[{'id':'cancel-only','text':'取消的草稿'}];d._external_origin='cancel';d.text_source.setCurrentIndex(d.text_source.findData('external'))
            dismiss(d,route,d.buttons.button(QDialogButtonBox.Cancel))
        saved_axis=axis();before_document=bridge.document.snapshot();history=(len(bridge.history.undo_stack),len(bridge.history.redo_stack));modal(lambda:bridge._configure_vocal(),align_exit)
        assert bridge.document.snapshot()==before_document and history==(len(bridge.history.undo_stack),len(bridge.history.redo_stack))
        assert saved_axis==axis() and load(bridge.storage_dir)['language']=='English'
        expected=deepcopy(bridge.document.project.to_dict())
    ok('四个窗口逐项测试按钮、Escape、系统关闭事件；改动后不扫描、不应用、保留打轴配置')
    # The application owns a running management task. Window dismissal never calls stop.
    from threading import Event
    release=Event()
    def pending(cancel,progress):
        while not release.wait(.02):
            if cancel():raise InterruptedError('unexpected cancellation')
    assert work.start(pending,{},'verify')
    try:
        for route in ('button','escape','close'):
            modal(lambda:bridge._configure_vocal(),lambda d:dismiss(d,route,d.buttons.button(QDialogButtonBox.Cancel)))
            assert work.task is not None and not work.stop_reason
    finally:
        release.set()
        for _ in range(100):
            if work.task is None:break
            delay(15)
    ok('已有后台任务关闭与重开：不重复取消，任务由原所有者正常完成')
    ok('OCR 3/6/10、自定义、CPU/GPU 选择、Tesseract 来回切换、取消保留工程')
    assert not bridge._task.get('running') and media.player.audioOutput() is None
    from .toolbar_acceptance_030 import check
    check(bridge,window,out);ok('整机 EXE 主窗频谱菜单、缩放、换行、键盘焦点、项目保护与静音')
