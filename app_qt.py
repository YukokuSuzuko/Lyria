#!/usr/bin/env python3
"""Lyria Qt Quick desktop entry point (no Tk, no QQuickWidget)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if os.environ.get('LYRIA_QA_READING_OFFLINE') == '1':
        from desktop_qt.reading_dialog_probe import install_offline_guard
        install_offline_guard()
    if arguments and arguments[0] == '--reading-worker':
        from lyricsync.bundled_reading import main as reading_main
        return reading_main(arguments[1:])
    from lyricsync.ocr_font_setup import configure_ocr_font
    configure_ocr_font()
    from lyricsync import __display_version__ as __version__
    parser=argparse.ArgumentParser(description='Lyria · Moonlit Lilac Qt Quick')
    parser.add_argument('--version', action='version', version='Lyria ' + __version__)
    parser.add_argument('--self-check',help='写入无秘密 Qt 运行诊断 JSON；不是完整 Windows 验收')
    parser.add_argument('--project',help='打开新版 .lyria 工程')
    parser.add_argument('--video',help='导入视频（不会自动运行 OCR 或 API）')
    parser.add_argument('--storage-dir',help='工程自动恢复目录；默认系统应用数据目录')
    parser.add_argument('--native-window-frame',action='store_true',help='保留系统标题栏（窗口环境兼容性回退）')
    parser.add_argument('--exit-after',type=int,default=0,metavar='MS',help='自动退出，仅用于启动/截图测试')
    parser.add_argument('--screenshot',help='启动测试截图的输出路径（只用于测试）')
    parser.add_argument('--qc-self-check',help='仅测试：在独立目录核验本地 OCR 与缓存并写 JSON')
    parser.add_argument('--qc-reference',help='仅测试：与 --qc-self-check 配合的合成中文参考文件')
    parser.add_argument('--foundation-self-check',help='仅测试：独立合成工程编辑、撤销、保存重开及导出探针 JSON')
    parser.add_argument('--editing-self-check',help='仅测试：独立纯编辑、音频及时间轴导入探针 JSON')
    parser.add_argument('--reading-dialog-self-check',help='仅内测：隔离桌面上的真实内嵌注音对话框闭环报告')
    parser.add_argument('--ocr-workflow-self-check',help='仅内测：在隔离桌面核验合成素材的识别、核对、应用与撤销')
    args=parser.parse_args(argv)
    if args.ocr_workflow_self_check:
        if any((args.project,args.video,args.storage_dir,args.self_check,args.qc_self_check,args.qc_reference,args.foundation_self_check,args.editing_self_check,args.reading_dialog_self_check,args.exit_after,args.screenshot)):
            parser.error('识别流程自检须独立运行，仅使用生成的合成素材。')
        from desktop_qt.reading_dialog_probe import prepare_report
        try: report = prepare_report(args.ocr_workflow_self_check)
        except (ValueError, RuntimeError, OSError) as exc: parser.error(str(exc))
        args.ocr_workflow_self_check = str(report)
        args.storage_dir = str(report.parent/'storage')
    if args.reading_dialog_self_check:
        if any((args.project,args.video,args.storage_dir,args.self_check,args.qc_self_check,args.qc_reference,args.foundation_self_check,args.editing_self_check,args.exit_after,args.screenshot)):
            parser.error('注音对话框自检必须独立运行，不接受用户工程、素材或存储目录')
        from desktop_qt.reading_dialog_probe import prepare_report
        try:report=prepare_report(args.reading_dialog_self_check)
        except Exception as exc:parser.error(str(exc))
        args.reading_dialog_self_check=str(report)
        args.storage_dir=str(report.parent/'storage')
    if args.foundation_self_check or args.editing_self_check:
        if args.foundation_self_check and args.editing_self_check:
            parser.error('两种工程自检必须分别运行')
        if any((args.project,args.video,args.storage_dir,args.self_check,args.qc_self_check,args.exit_after,args.screenshot)):
            parser.error('基础工程自检必须独立运行，不接受用户工程、素材或存储目录')
        report=Path(args.foundation_self_check or args.editing_self_check).expanduser().resolve()
        report.parent.mkdir(parents=True,exist_ok=False)
        args.storage_dir=str(report.parent/'storage')
    if args.qc_self_check:
        if not args.video or not args.qc_reference:parser.error('QC 自检需要 --video 和 --qc-reference')
        from lyricsync.qc_runtime_probe import run
        return run(args.video,args.qc_reference,args.qc_self_check)
    from lyricsync.portable_profile import default_portable_storage
    if not args.storage_dir:
        args.storage_dir = default_portable_storage()
    from desktop_qt.diagnostics import DiagnosticStore, RuntimeHooks, show_startup_failure
    diagnostics = DiagnosticStore(args.storage_dir)
    hooks = RuntimeHooks(diagnostics)
    hooks.install()
    try:
        return _run_application(args, diagnostics, hooks)
    except Exception as exc:
        diagnostics.record('startup-failed', exc)
        show_startup_failure(exc, diagnostics)
        return 2
    finally:
        diagnostics.record('session-end')
        hooks.restore()


def _run_application(args, diagnostics, hooks):
    reading_dialog_self_check = getattr(args, "reading_dialog_self_check", None)
    ocr_workflow_self_check = getattr(args, 'ocr_workflow_self_check', None)
    from PySide6.QtCore import QCoreApplication,QTimer,QUrl
    hooks.install_qt()
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication
    from desktop_qt.controller import Bridge
    from desktop_qt.window_chrome import WindowChromeController

    QCoreApplication.setOrganizationName('Lyria')
    from lyricsync.portable_profile import application_name
    QCoreApplication.setApplicationName(application_name())
    from lyricsync import __display_version__ as __version__
    QCoreApplication.setApplicationVersion(__version__)
    QQuickStyle.setStyle('Basic')
    app=QApplication(sys.argv[:1])
    from desktop_qt.native_theme import AppDialogTheme
    dialog_theme=AppDialogTheme(app)
    app.installEventFilter(dialog_theme)
    from desktop_qt.app_icon import setup_application_icon
    setup_application_icon(app)
    bridge=Bridge(storage_dir=args.storage_dir,diagnostics=diagnostics,startup=not bool(args.video or args.project or args.foundation_self_check or args.editing_self_check or reading_dialog_self_check or ocr_workflow_self_check))
    # Only a local release-channel gate in unsigned candidates; no network or installer.
    from lyricsync.software_update import startup_status
    bridge._software_update_status = startup_status()
    try:
        engine=QQmlApplicationEngine()
        engine.rootContext().setContextProperty('bridge',bridge)
        window_chrome=WindowChromeController(native_frame=args.native_window_frame)
        engine.rootContext().setContextProperty('windowChrome',window_chrome)
        qml=Path(__file__).resolve().parent/'desktop_qt'/'qml'/'Main.qml'
        def qml_warnings(errors):
            for error in errors:
                diagnostics.record('qml-warning',source=error.url().toLocalFile(),line=error.line())
        engine.warnings.connect(qml_warnings)
        engine.load(QUrl.fromLocalFile(str(qml)))
        if not engine.rootObjects():
            from desktop_qt.diagnostics import StartupFailure
            bridge.shutdown()
            diagnostics.record('qml-load-failed')
            raise StartupFailure()
        window=engine.rootObjects()[0]
        bridge.quitRequested.connect(app.quit)
        app.aboutToQuit.connect(bridge.shutdown)
        if ocr_workflow_self_check:
            from desktop_qt.ocr_workflow_probe import OcrWorkflowProbe
            bridge._ocr_workflow_probe = OcrWorkflowProbe(app,bridge,window,ocr_workflow_self_check)
            bridge._ocr_workflow_probe.start()
        if reading_dialog_self_check:
            from desktop_qt.reading_dialog_probe import ReadingDialogProbe
            bridge._reading_dialog_probe=ReadingDialogProbe(app,bridge,window,reading_dialog_self_check)
            QTimer.singleShot(0,bridge._reading_dialog_probe.start)
        if args.foundation_self_check:
            from desktop_qt.foundation_probe import FoundationProbe
            bridge._foundation_probe=FoundationProbe(app,bridge,window,args.foundation_self_check)
            bridge._foundation_probe.start()
        if args.editing_self_check:
            from desktop_qt.editing_probe import EditingProbe
            bridge._editing_probe=EditingProbe(app,bridge,window,args.editing_self_check)
            bridge._editing_probe.start()
        if args.project:QTimer.singleShot(0,lambda:bridge.command('project.open',{'path':args.project}))
        elif args.video:QTimer.singleShot(0,lambda:bridge.command('media.open',{'path':args.video}))
        if args.screenshot:
            def capture():
                target=Path(args.screenshot).expanduser().resolve();target.parent.mkdir(parents=True,exist_ok=True)
                image=window.grabWindow()
                if image.isNull() or not image.save(str(target)):
                    print('Screenshot failed',file=sys.stderr)
                else:print('Screenshot:',target)
            QTimer.singleShot(max(500,args.exit_after-150) if args.exit_after else 2000,capture)
        if args.self_check:
            import json,platform
            from PySide6 import __version__ as pyside_version
            from PySide6.QtCore import qVersion
            def write_self_check_report():
                media=bridge.media
                report={'kind':'qt-runtime-smoke','app_version':__version__,'python':platform.python_version(),'platform':platform.platform(),
                        'pyside':pyside_version,'qt':qVersion(),'qml_loaded':bool(engine.rootObjects()),
                        'tk_imported':any(name=='tkinter' or name.startswith('tkinter.') for name in sys.modules),
                        'render_path':'cpu-painted-fallback' if bridge.softwareRendering else 'VideoOutput/QVideoSink',
                        'media_requested':bool(args.video or bridge.document.project.inputs.get('video')),'media':media,
                        'sink_frame_valid':bool(bridge.media_controller.sink and bridge.media_controller.sink.videoFrame().isValid()),
                        'raster_paint_count':bridge._surface.paintCount if bridge._surface else None,
                        'gpu_zero_copy_claim':False,'windows_acceptance_complete':False}
                report['window_chrome']={'integrated':window_chrome.enabled,
                                         'platform':window_chrome.platformName,
                                         'fallback_reason':window_chrome.fallbackReason}
                # Sample actual window pixels against the decoded frame, away from
                # captions/ROI borders. A valid sink alone previously hid blank video.
                report['visible_frame_samples_match']=None
                if report['media_requested'] and bridge.media_controller._last_image is not None:
                    from PySide6.QtCore import QObject,QPointF
                    from PySide6.QtQuick import QQuickItem
                    video=window.findChild(QObject,'videoPreview')
                    screen=window.grabWindow();source=bridge.media_controller._last_image
                    if video and not screen.isNull():
                        rect=video.property('contentRect');differences=[]
                        for x in (.25,.5,.75):
                            for y in (.2,.35,.5):
                                location=video.mapToScene(QPointF(rect.x()+rect.width()*x,rect.y()+rect.height()*y))
                                sx=max(0,min(screen.width()-1,round(location.x()*screen.width()/window.width())))
                                sy=max(0,min(screen.height()-1,round(location.y()*screen.height()/window.height())))
                                actual=screen.pixelColor(sx,sy);expected=source.pixelColor(min(source.width()-1,round(x*source.width())),min(source.height()-1,round(y*source.height())))
                                differences.append(max(abs(actual.red()-expected.red()),abs(actual.green()-expected.green()),abs(actual.blue()-expected.blue())))
                        report['visible_frame_samples_match']=sum(d<50 for d in differences)>=5
                        report['sample_pixel_errors']=differences
                media_ok=(not report['media_requested'] or (media['ready'] and media['displayedFrame']>0 and report['sink_frame_valid'] and report['visible_frame_samples_match'] is True))
                report['ok']=report['qml_loaded'] and not report['tk_imported'] and media_ok
                report['scope']='启动和实际帧像素冒烟检查；不代表 Windows 交互/性能/音频验收'
                target=Path(args.self_check).expanduser().resolve();target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print('Qt runtime report:',target)
                app.quit()
            QTimer.singleShot(args.exit_after or 3000,write_self_check_report)
        elif args.exit_after:QTimer.singleShot(args.exit_after,app.quit)
        exit_code=app.exec()
    finally:
        # Retain bridge, QML engine and media QObjects until every owned Python
        # worker has returned, even after direct quit/exit or QML load failure.
        bridge.wait_for_shutdown(app)
    return exit_code or (1 if bridge._shutdown_errors or bridge._shutdown_failed_saves else 0)



if __name__=='__main__':
    raise SystemExit(main())
