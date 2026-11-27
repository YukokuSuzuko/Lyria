"""Manage optional acceleration and existing model caches in the Lyria desktop."""
from pathlib import Path
import ctypes
from ctypes import wintypes as wt
import json
import hashlib
from functools import lru_cache
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget,QScrollArea,QGridLayout,QProgressBar,QToolButton
import os
import subprocess
import sys

from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,
                                QFileDialog,QMessageBox,QGroupBox)
from .native_theme import apply_dialog_theme
from .local_alignment_settings import effective_cache_root, effective_download_source, load, save, update_model_preferences
from .local_model_tasks import model_tasks
from lyricsync.local_models import ModelManager,NEXTFIRE,UVR,UVR_HQ1,QWEN,ASR,default_cache,readable_bytes
from lyricsync.shared_runtime import application_dir
from lyricsync.storage import atomic_json

def adapters():
    class Display(ctypes.Structure):
        _fields_=[('cb',wt.DWORD),('DeviceName',wt.WCHAR*32),('DeviceString',wt.WCHAR*128),
                  ('StateFlags',wt.DWORD),('DeviceID',wt.WCHAR*128),('DeviceKey',wt.WCHAR*128)]
    devices=[]
    for i in range(32):
        d=Display();d.cb=ctypes.sizeof(d)
        if not ctypes.windll.user32.EnumDisplayDevicesW(None,i,ctypes.byref(d),0):break
        if any(v in d.DeviceID.upper() for v in ('VEN_10DE','VEN_1002','VEN_8086')) and d.DeviceString not in devices:
            devices.append(d.DeviceString)
    return devices

def preferences(bridge):
    path=Path(bridge.storage_dir)/'acceleration-guide.json'
    try: prefs=json.loads(path.read_bytes())
    except (OSError,ValueError): prefs={}
    return path,prefs

@lru_cache(maxsize=48)
def _ocr_digest(path,size,modified,changed):
    with open(path,"rb") as stream:
        return hashlib.file_digest(stream,"sha256").hexdigest()

def _ocr_state():
    from .ocr_asset_pins import PINS
    from lyricsync.vision import resources
    total=0
    for name,expected in PINS.items():
        asset=resources()/name
        try:
            st=asset.stat()
            if _ocr_digest(asset,st.st_size,st.st_mtime_ns,st.st_ctime_ns)!=expected:return "校验失败"
            total+=st.st_size
        except OSError:return "缺少文件"
    return "已校验 · "+readable_bytes(total)

def _state(status):
    if status['verification_cached']:return '已校验'
    if status['present']:return '文件在位，待校验'
    if status['wrong_size']:return '大小不符'
    return '缺少文件'

def guide(bridge,stage='settings'):
    path,prefs=preferences(bridge)
    options=load(bridge.storage_dir)
    cache=Path(effective_cache_root(bridge.storage_dir))
    source_mode=effective_download_source(bridge.storage_dir)
    if stage=='startup' and prefs.get('seen'):return
    if stage not in ('startup','settings'):
        if not getattr(sys,'frozen',False) or os.environ.get('QT_QPA_PLATFORM')=='offscreen':return
        if prefs.get('never'):return
        model_id=options.get('separation_model_id',UVR.id) if stage=='separate' else options.get('model_id',NEXTFIRE.id)
        if ModelManager(cache).status(model_id)['present']:return
    from PySide6.QtWidgets import QCheckBox,QFrame,QMenu,QDialogButtonBox,QApplication,QSizePolicy
    from . import carbon_widgets as c
    from lyricsync import split_profiles as profiles
    from lyricsync.split_profiles import activate_now,prefer_cpu,set_preference
    from lyricsync.shared_runtime import extension_root
    from lyricsync.online_nvidia import install as install_nvidia
    from lyricsync.online_amd import install as install_amd
    app=application_dir();base=app.parent if app else Path(bridge.storage_dir)
    hardware=adapters();work=model_tasks()
    def clean(p):
        return os.path.normpath(os.path.abspath(os.path.expanduser(str(p))))
    cache=Path(clean(cache))
    dialog=QDialog();dialog.setObjectName('splitDependencies');dialog.setWindowTitle('模型与加速')
    outer=c.layout(dialog);scroll=QScrollArea();scroll.setWidgetResizable(True);outer.addWidget(scroll,1)
    body=QWidget();body.setObjectName('carbonBody');layout=c.layout(body,margins=(19,16,19,15),space=12);scroll.setWidget(body)
    intro=QWidget();introbox=c.layout(intro,space=3);layout.addWidget(intro)
    introbox.addWidget(c.label('准备本地模型与显卡加速','heading'))
    introbox.addWidget(c.label('编辑、核对与导出随时可用。模型让识别和打轴能运行，GPU 加速包只让它更快。','muted',True))
    specs=[(NEXTFIRE,'NextFire'),(UVR,'HQ3'),(UVR_HQ1,'HQ1'),(QWEN,'Qwen 多语言对时'),(ASR,'ASR 听写')]
    modelcard=c.card(layout);head,summary=c.header(modelcard,'本地模型')
    import_btn,imports=c.menu_button('导入文件夹','link','guideImportModel');head.addWidget(import_btn)
    def notice(text):
        statusbar.setText(str(text));statusbar.setVisible(bool(text))
    def start(job,action):
        if not work.start(job,{'cache_root':str(cache)},action):notice('请等待当前任务完成。')
        update_tasks()
    def model_job(spec,action):
        manager=ModelManager(cache)
        if action=='verify':
            start(lambda cancel,progress:manager.verify(spec.id,cancel=cancel,progress=progress),'verify')
        elif action=='import_folder':
            folder=QFileDialog.getExistingDirectory(dialog,'选择含原始权重与配置的模型文件夹',str(cache))
            if folder:start(lambda cancel,progress:manager.import_folder(folder,spec.id,cancel=cancel,progress=progress),'import_folder')
        else:
            src_label = '国内镜像源（HF-Mirror / 魔搭 / GitHub 加速）' if source_mode == 'mirror' else '官方直连源（Hugging Face / GitHub）'
            if QMessageBox.question(dialog,'下载模型',f'{spec.id} · {readable_bytes(spec.size)}\n下载线路：{src_label}\n来源：{spec.source_url}\n许可：{spec.license} · {spec.license_url}\n将下载到 {cache} 并校验。继续？')!=QMessageBox.Yes:return
            start(lambda cancel,progress:manager.download(spec.id,source=source_mode,cancel=cancel,progress=progress),'download')
    for spec,title in specs:imports.addAction(title,lambda checked=False,s=spec:model_job(s,'import_folder'))
    definitions=[
        ('画面字幕 OCR','识别视频内嵌字幕，已含离线字库。',()),
        ('NextFire · 日语自动打轴','歌词自动对时，按演唱原词与读音对齐。',(NEXTFIRE,)),
        ('人声分离 · HQ3 / HQ1','分离人声与伴奏，提升复杂配乐打轴稳定度。',(UVR,UVR_HQ1)),
        ('多语言打轴与听写','Qwen 多语言打轴与 ASR 长曲粗定位。',(QWEN,ASR))]
    views=[]
    for title,hint,group in definitions:
        row=c.band(modelcard,(14,8,14,8),10);row.parentWidget().setMinimumHeight(54)
        left=QVBoxLayout();left.setSpacing(2);row.addLayout(left,1)
        left.addWidget(c.label(title,'title'));left.addWidget(c.Elide(hint,'muted'))
        size=c.label('','muted');state=c.label('','verified');row.addWidget(size);row.addWidget(state)
        action,menu=c.menu_button('选择…','soft');row.addWidget(action)
        verify=c.button('校验',lambda checked=False,g=group:model_job(g[0],'verify') if g else None)
        row.addWidget(verify)
        for spec in group:
            target=menu.addMenu(next(n for m,n in specs if m==spec)) if len(group)>1 else menu
            for verb,op in [('校验','verify'),('导入文件夹…','import_folder'),('下载模型…','download')]:
                target.addAction(verb,lambda checked=False,s=spec,o=op:model_job(s,o))
        views.append((size,state,action,verify));c.line(modelcard)
    model_path=c.band(modelcard,(14,6,14,6));model_path.addWidget(c.label('目录','muted'))
    current=c.Elide('',middle=True);model_path.addWidget(current,1)
    source_combo=QComboBox()
    source_combo.addItem('国内镜像源（推荐）','mirror')
    source_combo.addItem('官方直连源','official')
    source_combo.setCurrentIndex(0 if source_mode=='mirror' else 1)
    def on_source_changed():
        nonlocal source_mode
        source_mode=source_combo.currentData() or 'mirror'
        update_model_preferences(bridge.storage_dir, download_source=source_mode)
    source_combo.currentIndexChanged.connect(on_source_changed)
    model_path.addWidget(source_combo)
    def choose_cache():
        nonlocal cache
        value=QFileDialog.getExistingDirectory(dialog,'选择模型存放目录',str(cache))
        if value:
            cache=Path(clean(value));update_model_preferences(bridge.storage_dir, cache_root=str(cache));refresh()
            if hasattr(bridge, '_sync_model_watcher'): bridge._sync_model_watcher()
    model_path.addWidget(c.button('更改目录',choose_cache,'quiet'))
    def refresh():
        manager=ModelManager(cache);count=0
        for i,(_,hint,group) in enumerate(definitions):
            size,state,action,verify=views[i];verify.hide();action.hide();size.clear()
            if not group:
                checked=_ocr_state();ready=checked.startswith('已校验');msg='✓ 已内置' if ready else checked
                state.setToolTip(checked)
            else:
                statuses=[manager.status(t.id) for t in group]
                valid=[v for v in statuses if v['verification_cached']]
                ready=bool(valid) if i==2 else len(valid)==len(group)
                detail='\n'.join(f'{spec.id} · {_state(st)} · {readable_bytes(spec.size)}' for spec,st in zip(group,statuses))
                state.setToolTip(detail);action.setToolTip(detail);size.setToolTip(detail)
                if ready:
                    size.setText(readable_bytes(sum(spec.size for spec,st in zip(group,statuses) if st['verification_cached'])))
                    msg='✓ 已就绪';action.setVisible(True)
                    action.setText('管理 ▾')
                elif any(st['present'] for st in statuses):
                    msg='已找到';size.setText(readable_bytes(sum(spec.size for spec,st in zip(group,statuses) if st['present'])))
                    verify.setVisible(len(group)==1);action.setVisible(len(group)>1)
                else:
                    msg='校验异常' if any(st['wrong_size'] for st in statuses) else '按需'
                    action.show();action.setText('选择…')
            count+=int(ready);state.setText(msg)
            state.setProperty('styleRole','verified' if ready else 'found' if msg=='已找到' else 'muted')
            state.style().unpolish(state);state.style().polish(state)
        summary.setText(f'已准备 {count} / 4 项');current.setValue(clean(cache))
    gpu=c.card(layout);gpuhead,_=c.header(gpu,'显卡加速','可选 · 仅提升识别与打轴速度')
    rescan_btn=c.button('重新检测',lambda:(device_state(),refresh()),'quiet','guideGpuRescan')
    gpuhead.addWidget(rescan_btn)
    device_row=c.band(gpu,(14,14,14,14),10);device_row.addWidget(c.Glyph('gpu'));identity=QVBoxLayout();identity.setSpacing(3);device_row.addLayout(identity,1)
    device_line=QHBoxLayout();device_line.setSpacing(8);identity.addLayout(device_line)
    has_nvidia = any('NVIDIA' in d.upper() for d in hardware)
    has_amd = any('AMD' in d.upper() or 'RADEON' in d.upper() for d in hardware)
    chosen=next((d for d in hardware if 'NVIDIA' in d.upper()),'') if profiles.ACTIVE=='nvidia' else (next((d for d in hardware if 'AMD' in d.upper() or 'RADEON' in d.upper()),'') if profiles.ACTIVE=='amd' else '')
    device_name=c.DeviceName(chosen or ' / '.join(hardware) or '本机处理器','title')
    device_line.addWidget(device_name);badge=c.label('','badge');badge.setObjectName('guideHardwareState');device_line.addWidget(badge);device_line.addStretch()
    device_name.setObjectName('guideDeviceName')
    device_name.setSizePolicy(QSizePolicy.Maximum,QSizePolicy.Preferred)
    def show_device_name():
        device_name.updateGeometry();device_name.renderValue()
    device_hint=c.label('','muted',True);identity.addWidget(device_hint)
    install_button=c.button('安装 NVIDIA 加速',lambda:online_install(),'soft','installNvidiaOnline');device_row.addWidget(install_button)
    install_amd_button=c.button('安装 AMD ROCm 全加速',lambda:online_install_amd(),'soft','installAmdOnline');device_row.addWidget(install_amd_button)
    manage,menu=c.menu_button('管理 ▾','','guideGpuManage');device_row.addWidget(manage)
    c.line(gpu);where=c.band(gpu,(13,4,13,4));where.addWidget(c.label('安装目录','muted'))
    location=c.Elide('',middle=True);where.addWidget(location,1)
    licence_toggle=c.button('来源与许可 ▾',role='quiet');licence_toggle.setCheckable(True);where.addWidget(licence_toggle)
    licence=c.label('\n\n'.join(f'{title} · {spec.license}\n{spec.source_url}\n{spec.license_url}' for spec,title in specs)+'\n\nDirectML 基础加速已随包内置，Intel/AMD/未装 CUDA 的 N 卡开箱即可加速 OCR 与人声分离。\nNVIDIA 加速下载约 3.11 GB，AMD ROCm 7.2.1 扩展下载约 1.82 GB，首次建议预留 9 GB。各 wheel 内附对应官方许可文件。','muted',True)
    licence.setMargin(12);licence.setTextInteractionFlags(Qt.TextSelectableByMouse);gpu.addWidget(licence);licence.hide()
    licence_toggle.toggled.connect(licence.setVisible)
    def show_location():
        loc=prefs.get('online_directory') or base/'accelerators'
        if app and profiles.ACTIVE in ('nvidia','amd','directml'):
            try:loc=extension_root(app,profiles.ACTIVE).parent
            except (ValueError,OSError,KeyError):pass
        location.setValue(clean(loc))
    def device_state():
        show_device_name();show_location()
        if profiles.CHECKING:msg,note='校验显卡加速中','可以继续编辑和使用 CPU，新任务将在校验完成后使用可用设备。'
        elif profiles.ACTIVE=='nvidia':msg,note='● CUDA 已启用','画面 OCR、人声分离、NextFire 与 Qwen 均可使用 CUDA 加速。'
        elif profiles.ACTIVE=='amd':msg,note='● ROCm + DirectML 已启用','Qwen/NextFire/ASR 使用 AMD ROCm，OCR 与人声分离使用内置 DirectML，全模块加速就绪。'
        elif profiles.ACTIVE=='directml':msg,note='● DirectML 已启用（内置）','画面 OCR 与人声分离已开箱加速；打轴对时在 CPU（或安装 N/A 卡全加速扩展）。'
        else:
            installed=False
            if app:
                try:installed=any(extension_root(app,n).is_dir() for n in ('nvidia','amd','directml'))
                except (ValueError,OSError,KeyError):pass
            msg='CPU 回退' if profiles.REASON else '待检测' if installed else '当前 CPU'
            note=profiles.REASON or ('已发现本地扩展，可直接点击重新检测立即生效。' if installed else '可继续使用 CPU，或安装兼容的可选加速包。')
        if profiles.OCR_NOTICE:note += '　' + profiles.OCR_NOTICE
        badge.setText(msg)
        badge.setProperty('styleRole','badge' if profiles.ACTIVE!='cpu' else 'pill')
        badge.style().unpolish(badge);badge.style().polish(badge)
        device_hint.setText(note);device_hint.setToolTip(note)
    def cpu():
        prefer_cpu();save(bridge.storage_dir,dict(load(bridge.storage_dir),device='cpu'));dialog.accept()
    def auto():
        set_preference('auto');activate_now('auto');device_state();notice('已切换为自动检测并立即生效。')
    def remove():
        name=profiles.ACTIVE if profiles.ACTIVE!='cpu' else ('nvidia' if has_nvidia else ('amd' if has_amd else 'directml'))
        notice('关闭 Lyria 后运行基础包中的 Uninstall-'+name+'.cmd，模型与工程保留。')

    def import_extension():
        archive=QFileDialog.getOpenFileName(dialog,'选择已下载的加速包','','Lyria 加速包 (*.zip)')[0]
        if not archive:return
        base=application_dir().parent
        custom=''
        if QMessageBox.question(dialog,'加速包存放位置','使用默认位置？选择“否”可指定其他文件夹。')==QMessageBox.StandardButton.No:
            custom=QFileDialog.getExistingDirectory(dialog,'选择加速扩展存放位置')
            if not custom:return
        def unpack(cancel,progress):
            progress(0,'正在校验归档并安装扩展…')
            cmd=[str(base/'shared-runtime/python.exe'),'-I','-B',str(base/'install-extension.py'),archive]
            if custom:cmd.extend(['--directory',custom])
            result=subprocess.run(cmd,cwd=str(base),capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:raise ValueError('加速包未安装。请核对版本、校验值、安装位置及可用空间。')
            activate_now('auto', cancel=cancel)
            return '加速扩展已安装并立即生效。'
        start(unpack,'split_install')
    def select_directory():
        custom=QFileDialog.getExistingDirectory(dialog,'选择加速扩展存放目录')
        if custom:
            prefs['online_directory']=custom
            atomic_json(path,prefs)
            show_location()
    def online_install():
        target=prefs.get('online_directory')
        app=application_dir()
        def job(cancel,progress):
            return install_nvidia(app,target,cancel,progress,source_mode=source_mode)
        start(job,'nvidia_online')
    def online_install_amd():
        target=prefs.get('online_directory')
        app=application_dir()
        def job(cancel,progress):
            return install_amd(app,target,cancel,progress)
        start(job,'amd_online')
    def online_cancel():
        if work.action in ('nvidia_online','amd_online'):work.stop('cancel')
    menu.addAction('离线导入加速包…',import_extension);menu.addAction('更改安装位置…',select_directory)
    menu.addAction('自动检测并立即生效',auto);menu.addAction('使用 CPU',cpu);menu.addSeparator();menu.addAction('卸载说明',remove)
    progressbar=QProgressBar();layout.addWidget(progressbar)
    tasks=QWidget();taskrow=c.layout(tasks,False,space=6);layout.addWidget(tasks);taskrow.addStretch()
    cancel=c.button('取消安装',online_cancel,name='cancelNvidiaOnline');taskrow.addWidget(cancel)
    cancel_model=c.button('取消任务',lambda:work.stop('cancel'));taskrow.addWidget(cancel_model)
    pause=c.button('暂停下载',lambda:work.stop('pause'));taskrow.addWidget(pause)
    resume=c.button('继续下载',lambda:(work.resume(),update_tasks()));taskrow.addWidget(resume)
    statusbar=c.label('','muted',True);layout.addWidget(statusbar);statusbar.hide()
    layout.addStretch()
    buttons=c.footer(outer);never=QCheckBox('下次启动不再提示');never.setObjectName('splitNever');never.setChecked(bool(prefs.get('never')))
    never.setToolTip('关闭首启和缺模型时的自动提示。仍可从设置打开。');buttons.addWidget(never);buttons.addStretch()
    later=c.button('稍后' if stage=='startup' else '关闭',dialog.reject,'quiet','splitLater');buttons.addWidget(later)
    use_cpu=c.button('继续用 CPU',cpu,name='splitCPU');buttons.addWidget(use_cpu);use_cpu.setVisible(profiles.ACTIVE=='cpu')
    proceed=c.button('开始使用',dialog.accept,'primary','splitBegin');buttons.addWidget(proceed)
    from PySide6.QtCore import QTimer
    probe_timer = QTimer(dialog)
    def show_probe_state():
        device_state()
        if not profiles.CHECKING:
            probe_timer.stop()
    probe_timer.timeout.connect(show_probe_state)
    probe_timer.start(900)
    def remember():
        prefs.update(seen=True,never=never.isChecked());atomic_json(path,prefs)
    never.toggled.connect(remember);dialog.finished.connect(remember)
    def update_tasks():
        busy=work.task is not None;action=work.action
        show=busy and action in ('download','verify','import_folder','nvidia_online','amd_online','split_install')
        paused=not busy and work.stop_reason=='pause' and work.resume_work is not None
        progressbar.setVisible(show);tasks.setVisible(show or paused)
        cancel.setVisible(show and action in ('nvidia_online','amd_online'));cancel_model.setVisible(show and action in ('download','verify','import_folder'))
        pause.setVisible(show and action=='download');resume.setVisible(paused)
        install_button.setVisible(profiles.ACTIVE!='nvidia' and has_nvidia and not (show and action=='nvidia_online'))
        install_amd_button.setVisible(profiles.ACTIVE!='amd' and has_amd and not (show and action=='amd_online'))
        install_button.setEnabled(not busy);install_amd_button.setEnabled(not busy);menu.setEnabled(not busy);modelcard.parentWidget().setEnabled(not busy)
    def progress(n,msg):
        update_tasks()
        if not progressbar.isHidden():progressbar.setValue(n);notice(msg)
    def finished():
        if work.action in ('nvidia_online','amd_online','split_install','download','verify','import_folder'):
            notice(work.error or ('加速扩展已安装并立即生效。' if work.action in ('nvidia_online','amd_online','split_install') else '模型任务已完成并已自动就绪。'))
        refresh();device_state();update_tasks()
    refresh();device_state();update_tasks();progress(work.percent,work.message)
    work.progress.connect(progress);work.finished.connect(finished)
    c.theme(dialog)
    dialog.resize(650,min(640,dialog.screen().availableGeometry().height()-38))
    show_device_name()
    try:
        if stage=='startup':atomic_json(path,dict(prefs,seen=True))
        dialog.exec()
    finally:
        work.progress.disconnect(progress);work.finished.disconnect(finished);dialog.deleteLater()

