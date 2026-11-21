"""Opt-in private-desktop release check of the ordinary OCR and comparison UI."""
from copy import deepcopy
import json
from pathlib import Path
import time

from PySide6.QtCore import QEvent, QEventLoop, QMetaObject, QObject, QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QInputDialog

from .candidate_dialog import CandidateDialog
from .foundation_probe import FoundationProbe


def make_captions(path):
    import av
    from PIL import Image, ImageDraw, ImageFont
    root = Path(__file__).resolve().parents[1]
    font = ImageFont.truetype(str(root/'resources/fonts/NotoSansCJKjp-Regular.otf'), 35)
    with av.open(str(path), 'w') as container:
        stream = container.add_stream('ffv1', rate=10)
        stream.width, stream.height = 560, 270
        stream.pix_fmt = 'yuv444p'
        stream.codec_context.thread_count = 1
        for n in range(42):
            frame = Image.new('RGB', (560, 270), (24, 25, 30))
            draw = ImageDraw.Draw(frame)
            if 4 <= n < 19:
                draw.text((65, 180), 'HELLO WORLD', font=font, fill='white')
                draw.text((75, 45), 'BLUE SKY', font=font, fill='white')
            if 23 <= n < 38:
                draw.text((65, 180), 'FINAL LINE', font=font, fill='white')
            for packet in stream.encode(av.VideoFrame.from_image(frame)):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


class OcrWorkflowProbe(FoundationProbe):
    def tick(self):
        waiting = self.waiting
        super().tick()
        if self.waiting is not None and self.waiting is not waiting:
            self.deadline = time.monotonic() + (100 if getattr(self,'step','') in ('cold_single_no_text','warm_single_retained','cold_two_regions_locked_master') else 15)

    def modal(self, actions, trigger):
        """Answer only the expected widgets in the nested Qt event loop."""
        pending = list(actions)
        failure = []
        timer = QTimer(self)
        timer.setInterval(25)
        loop = QEventLoop(self)
        limit = time.monotonic() + 25
        def advance():
            dialog = QApplication.activeModalWidget()
            if not pending and (dialog is None or not dialog.isVisible()):
                timer.stop()
                loop.quit()
                return
            if pending and dialog and pending[0][0](dialog):
                _, action = pending.pop(0)
                try:
                    action(dialog)
                except Exception as exc:
                    failure.append(exc)
                    dialog.reject()
            elif time.monotonic() > limit:
                failure.append(TimeoutError(dialog.windowTitle() if dialog else self.bridge.state.get('error','modal timeout')))
                if dialog:dialog.reject()
                timer.stop()
                loop.quit()
        timer.timeout.connect(advance)
        timer.start()
        try:
            trigger()
            if pending and not failure:
                loop.exec()
        finally:
            timer.stop()
        if failure:
            raise failure[0]
        if pending:
            raise AssertionError('The expected dialog did not open')

    def lookup(self, name):
        item = self.window.findChild(QObject, name)
        if item is None:
            # Repeater delegates belong to the visual tree of the open overlay.
            pending = [self.window.contentItem()]
            while pending:
                child = pending.pop()
                if child.objectName() == name:
                    item = child
                    break
                pending.extend(child.childItems())
        return item

    def invoke(self, name, signal):
        item = self.lookup(name)
        if item is None or not item.property('enabled') or not QMetaObject.invokeMethod(item, signal):
            raise AssertionError('Control unavailable: ' + name)

    def scan(self, name):
        from lyricsync.storage import load_project
        b = self.bridge
        self.step = name
        self.command('stage.set', stage='import')
        yield lambda: b.stage == 'import' and self.window.findChild(QObject, 'startOcrButton').property('enabled')
        self.modal([(lambda d:d.windowTitle() == 'OCR 识别设置',
                     lambda d:d.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click())],
                   lambda:self.invoke('startOcrButton','clicked'))
        yield lambda: not b._candidate_job and not b.jobs.active
        checkpoint = load_project(b.storage_dir / (b.document.id+'.pipeline.lyricsync.json'))
        assert b.stage == 'review' and b.document.source_candidates, b._candidate_task.get('label')
        metrics = checkpoint.video.get('ocr_metrics', {})
        self.record(name, status_from_ui=b._candidate_task.get('resultStatus'), candidates=len(b.document.source_candidates),
                    rows=len(b.document.main_timeline), visuals=len(checkpoint.visuals), metrics=metrics,
                    providers=checkpoint.video.get('ocr_runtime',{}).get('providers',[]),
                    multi=bool(checkpoint.video.get('ocr_multi_region')))
        return checkpoint

    def reopen(self, expected, action):
        def check(dialog):
            assert dialog.record['id'] == expected and dialog.table.columnCount() == 4
            assert dialog.table.rowViewportPosition(0) >= 0 and dialog.table.rowHeight(0) >= 44
            action(dialog)
        self.modal([(lambda d:isinstance(d,QInputDialog),lambda d:d.accept()),
                    (lambda d:isinstance(d,CandidateDialog),check)],
                   lambda:self.invoke('compareRecognitionResults','triggered'))

    def check_workspace(self):
        b, window = self.bridge, self.window
        self.command('stage.set',stage='review')
        start_size = window.size()
        self.step = 'resize review window'
        window.resize(820,650)
        yield lambda:window.width()==820
        def item(name):
            control=window.findChild(QObject,name)
            if control is None:raise ValueError('Missing control: '+name)
            return control
        def inside(control):
            top=control.mapToScene(QPointF())
            return top.x()>=-.5 and top.y()>=-.5 and top.x()+control.width()<=window.width()+1 and top.y()+control.height()<=window.height()+1
        header=item('projectHeader'); stages=item('workflowStageBar')
        assert stages.mapToItem(header,QPointF(0,stages.height())).y()<=header.height()+1
        assert stages.mapToItem(header,QPointF()).y()>=-1
        for name in ('timelineSelectTool','timelineRazorTool','timelineHandTool','timelineZoomOut','timelineZoomIn','audioVisibilityToggle','followCurrentButton','timelineFoldButton'):
            control=item(name)
            assert control.isVisible() and inside(control),name
        # Deliver the same right-button events handled by the QML ruler, while
        # leaving the visible Windows desktop and its actual pointer untouched.
        ruler=item('timelineRulerMouse')
        self.step = 'right-click timeline ruler'
        pos=ruler.mapToScene(QPointF(ruler.width()*.60,8))
        target=window.contentItem()
        hit=[]
        while target is not None:
            hit.append(target.objectName() or target.metaObject().className().split('_QML')[0])
            pt=target.mapFromScene(pos)
            target=target.childAt(pt.x(),pt.y())
        self.record('ruler_pointer_target',point=[pos.x(),pos.y()],hit=hit,
                    ruler_visible=ruler.isVisible(),ready=b.media['ready'],stage=b.stage)
        for typ,buttons in ((QEvent.Type.MouseButtonPress,Qt.MouseButton.RightButton),
                           (QEvent.Type.MouseButtonRelease,Qt.MouseButton.NoButton)):
            self.app.sendEvent(window,QMouseEvent(typ,pos,pos,QPointF(window.mapToGlobal(pos.toPoint())),
                           Qt.MouseButton.RightButton,buttons,Qt.KeyboardModifier.NoModifier))
        timeline=item('timelineContextMenu');audio=item('audioModeMenu')
        yield lambda:timeline.property('visible')
        assert not audio.property('visible') and inside(timeline.property('contentItem'))
        QMetaObject.invokeMethod(timeline,'close')
        yield lambda:not timeline.property('visible')
        self.step = 'open audio display menu'
        assert QMetaObject.invokeMethod(item('audioAnalysisPanel'),'openMenu')
        yield lambda:audio.property('visible')
        assert audio.property('count')==7 and inside(audio.property('contentItem'))
        assert not timeline.property('visible')
        previous_audio = (b.state['workspaceLayout']['audioVisible'], b.audioDisplaySource['source'])
        self.invoke('advancedSpectrumMenuItem','triggered')
        QMetaObject.invokeMethod(audio,'close')
        advanced=item('advancedSpectrumPopup')
        yield lambda:advanced.property('opened')
        assert inside(advanced.property('contentItem'))
        self.invoke('advanced_spectrumWindow_768','clicked')
        yield lambda:b.state['workspaceLayout']['spectrumWindow']==768
        QMetaObject.invokeMethod(advanced,'close')
        self.command('view.layout',audioMode='spectrum')
        self.invoke('spectrumSettings','clicked')
        yield lambda:item('spectrumAdjustments').property('opened')
        self.invoke('openAdvancedSpectrum','clicked')
        yield lambda:advanced.property('opened')
        assert inside(advanced.property('contentItem'))
        self.invoke('advancedSpectrumReset','clicked')
        yield lambda:b.state['workspaceLayout']['spectrumWindow']==384
        assert b.state['workspaceLayout']['audioMode']=='spectrum'
        QMetaObject.invokeMethod(advanced,'close')
        assert previous_audio == (b.state['workspaceLayout']['audioVisible'], b.audioDisplaySource['source'])
        self.record('advanced_display_both_entries',device_pixel_ratio=window.devicePixelRatio(),selected=768,reset=384)
        self.record('narrow_timeline_and_distinct_context_menus',size=[window.width(),window.height()],
                    device_pixel_ratio=window.devicePixelRatio(),audio_menu_rows=audio.property('count'))
        layouts=[]
        for width, height in ((760,600),(1280,800),(1400,940)):
            window.resize(width,height)
            yield lambda: window.width()==width
            cue, mute, marker = (item(n) for n in ('playCurrentCueButton','muteButton','addMarkerButton'))
            left=item('playerToolRow')
            assert all(c.parentItem()==left and c.isVisible() and inside(c) for c in (cue,mute,marker)), [(c.objectName(),c.isVisible(),c.mapToScene(QPointF()),c.size(),window.size()) for c in (cue,mute,marker)]
            assert cue.x()+cue.width()<=mute.x() and mute.x()+mute.width()<=marker.x()
            transport=item('transportButtons')
            relative=left.mapToItem(transport,QPointF())
            assert relative.y()>=transport.height()-1 or relative.x()+left.width()<=1
            layouts.append(dict(width=width,left=cue.mapToScene(QPointF()).x(),cue_x=cue.x(),mute_x=mute.x(),marker_x=marker.x()))
        self.record('left_player_controls',sizes=layouts,dpr=window.devicePixelRatio())
        window.resize(start_size)
        yield lambda:not audio.property('visible') and b.stage=='review'

    def scenario(self):
        b = self.bridge
        video = self.root/'captions.mkv'
        make_captions(video)
        self.command('project.newEntry',mode='ocr')
        self.command('media.open',path=str(video))
        yield lambda:b.media['ready'] and b.media['displayedFrame'] > 0 and b.media_controller.index is not None
        p = b.document.project
        p.settings.update(engine='rapidocr',ocr_model='ppocrv6',ocr_device='directml',ocr_preprocess='subtitle',
                          ocr_threads=1,sample_fps=3.,language='eng',api_enabled=False)
        self.command('roi.set',x=.04,y=.62,width=.90,height=.32)
        cold = yield from self.scan('cold_single_no_text')
        assert cold.video['ocr_metrics']['ocr_calls'] > 0 and cold.video['ocr_runtime']['device'] == 'directml'
        assert len(b.document.main_timeline) >= 2 and b._candidate_task['resultStatus'] == 'applied'
        assert not any(r.human_edited or r.confirmed for r in b.document.main_timeline)
        self.command('history.undo')
        assert not b.document.main_timeline
        warm = yield from self.scan('warm_single_retained')
        assert warm.video['ocr_metrics']['complete_cache_hit'] and warm.video['ocr_metrics']['ocr_calls'] == 0
        automatic = b._ocr_result_window
        assert automatic and automatic.isVisible()
        cid = automatic.record['id']
        before = deepcopy(b.document.project.to_dict())
        automatic.reject()
        assert b.document.project.to_dict() == before
        self.reopen(cid,lambda dialog:dialog.apply.click())
        yield lambda:not b.jobs.active
        assert b.document.main_timeline
        self.command('history.undo')
        assert b.document.project.to_dict() == before
        self.record('reopen_from_subtitle_menu_apply_and_exact_undo')
        # Restore the accepted OCR rows and protect one, then change the crops.
        self.command('history.redo')
        row_id = b.document.main_timeline[0].id
        self.command('row.lock',id=row_id,value=True)
        regions=deepcopy(b.state['regionCards'])
        assert not b.command('roi.multi',{'action':'clear'}) and b.state['regionCards']==regions
        self.command('stage.set',stage='import')
        yield lambda:b.stage=='import' and self.window.findChild(QObject,'mainPreviewPanel').property('cropVisible')
        self.command('roi.multi',action='add',box=[.04,.10,.94,.39])
        prior = deepcopy(b.document.project.mappings)
        multi = yield from self.scan('cold_two_regions_locked_master')
        assert multi.video.get('ocr_multi_region') and len(b.state['multiRois']) == 2
        assert b.document.project.mappings == prior and b._ocr_result_window is not None
        b._ocr_result_window.reject()
        saved = self.root/'checked.lyria'
        self.command('project.saveAs',path=str(saved))
        yield lambda:saved.is_file() and not b.jobs.active
        cid = b.document.source_candidates[-1]['id']
        previous = b.document
        epoch = b.epoch
        self.command('project.open',path=str(saved))
        guard = self.window.findChild(QObject,'closeDialog')
        yield lambda:b.document is not previous or guard.property('visible')
        if b.document is previous:
            # The synthetic project has been saved. Acknowledge the separate
            # switch-without-export prompt before asserting a real reload.
            self.invoke('closeDiscard','clicked')
        yield lambda:b.document is not previous and b.epoch != epoch and not b.jobs.active and not guard.property('visible')
        assert Path(b.path).resolve()==saved.resolve() and b.document.project.mappings == prior
        assert any(c['id']==cid for c in b.document.source_candidates)
        def inspect_latest(dialog):
            assert '未匹配' in dialog.status.text() and dialog.record['id']==cid
            dialog.reject()
        self.reopen(cid,inspect_latest)
        assert b.document.project.mappings == prior
        self.record('locked_rows_and_candidate_survive_saved_project_reopen',document_replaced=True,epoch_advanced=b.epoch!=epoch)
        yield from self.check_workspace()

        original=self.root/'original.txt'
        original.write_text('HELLO WORLD\nFINAL LINE\n',encoding='utf8')
        translation=self.root/'translation.txt'
        translation.write_text('你好世界\n最后一句\n',encoding='utf8')
        for case,with_translation in [('reference_only',False),('original_and_translation',True)]:
            previous=b.document
            b.command('project.newEntry',{'mode':'ocr'})
            if b.document is previous:
                yield lambda:guard.property('visible')
                self.invoke('closeDiscard','clicked')
            yield lambda:b.document is not previous and not guard.property('visible')
            self.command('media.open',path=str(video))
            yield lambda:b.media['ready'] and b.media['displayedFrame']>0 and b.media_controller.index is not None
            self.command('input.import',role='source',path=str(original))
            yield lambda:not b.jobs.active and b.document.project.inputs.get('ktv')==str(original)
            if with_translation:
                self.command('input.import',role='translation',path=str(translation))
                yield lambda:not b.jobs.active and len(b.document.project.translations)==2
            b.document.project.settings.update(engine='rapidocr',ocr_model='ppocrv6',ocr_device='directml',
                ocr_preprocess='subtitle',ocr_threads=1,sample_fps=3.,language='eng',api_enabled=False)
            self.command('roi.set',x=.04,y=.62,width=.90,height=.32)
            before_rows=deepcopy(b.document.project.mappings)
            original_text=[l.text for l in b.document.project.lyrics]
            translated_ids=[t.id for t in b.document.project.translations]
            scanned=yield from self.scan(case)
            assert scanned.visuals and b._candidate_task['resultStatus']=='applied'
            assert [l.text for l in b.document.project.lyrics if l.id in {r.id for r in scanned.lyrics}][:len(original_text)]==original_text
            assigned=[i for row in b.document.main_timeline for i in row.translation_ids]
            if with_translation:
                assert assigned==translated_ids, (assigned,translated_ids)
            else:
                assert not [t for t in b.document.project.translations if t.id in assigned and t.text.strip()]
            assert len(b.document.main_timeline)==2
            self.command('history.undo')
            assert b.document.project.mappings==before_rows
            self.record(case+'_originals_preserved_and_undo',originals=original_text,translations=len(translated_ids))

        # A real blank crop after a valid scan must leave both the editable axis and history intact.
        self.step='blank crop after successful recognition'
        self.command('stage.set',stage='import')
        self.command('roi.set',x=.01,y=.85,width=.12,height=.10)
        previous_candidates=deepcopy(b.document.source_candidates)
        master=deepcopy(b.document.project.mappings)
        self.modal([(lambda d:d.windowTitle()=='OCR 识别设置',
                     lambda d:d.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click())],
                   lambda:self.invoke('startOcrButton','clicked'))
        yield lambda:not b._candidate_job and not b.jobs.active and b._candidate_task.get('resultStatus')=='empty'
        assert b.stage=='import' and b.document.source_candidates==previous_candidates and b.document.main_timeline==master
        assert self.lookup('ocrCompletion').isVisible() and not self.lookup('reopenCurrentOcr').isVisible()
        self.invoke('ocrLayerDetails','clicked')
        assert self.lookup('ocrLayerCounts').isVisible()
        self.record('empty_scan_retains_history_and_shows_counts',counts=b._candidate_task['scanCounts'])

        self.step='audio original and optional translation cards'
        previous=b.document
        b.command('project.newEntry',{'mode':'audio'})
        if b.document is previous:
            yield lambda:guard.property('visible')
            self.invoke('closeDiscard','clicked')
        yield lambda:b.document is not previous and b.state['entryMode']=='audio'
        assert not self.lookup('missingOcrEngine').isVisible()
        from unittest.mock import patch
        for role, path in [('translation',translation),('source',original)]:
            with patch.object(b,'_path',return_value=str(path)):
                self.invoke('chooseMaterial_'+role,'clicked')
            yield lambda:not b.jobs.active
        assert [r.text_override for r in b.document.main_timeline]==['HELLO WORLD','FINAL LINE']
        assert [r.text for r in b.document.project.translations]==['你好世界','最后一句']
        self.record('audio_cards_separate_original_and_translation',source_rows=2,translated_pool=2)
        yield from self.check_audio_review(guard)

    def check_audio_review(self, guard):
        from unittest.mock import patch
        b = self.bridge
        self.step = 'mixed speakers in review and full-width editors'
        self.command('stage.set',stage='review')
        ids=[r.id for r in b.document.main_timeline]
        self.command('review.select',id=ids[0])
        self.command('review.assignSpeaker',name='月')
        from PySide6.QtGui import QInputMethodEvent,QKeyEvent
        for full, name in [(False,'currentSpeakerInput'),(True,'fullSpeakerInput')]:
            self.command('review.select',id=ids[0])
            self.command('review.select',id=ids[1],ctrl=True)
            self.window.setProperty('fullWidthReview',full)
            yield lambda:self.lookup(name).isVisible()
            editor=self.lookup(name)
            assert editor.property('text')=='' and editor.property('placeholderText')=='混合（已选 2 句）'
            editor.forceActiveFocus()
            before=deepcopy(b.document.project.mappings)
            self.app.sendEvent(editor,QInputMethodEvent('hechang',[]))
            assert editor.property('inputMethodComposing') and b.document.main_timeline==before
            commit=QInputMethodEvent();commit.setCommitString('合唱')
            self.app.sendEvent(editor,commit)
            assert b.document.main_timeline==before
            for event in (QEvent.Type.KeyPress,QEvent.Type.KeyRelease):
                self.app.sendEvent(self.window,QKeyEvent(event,Qt.Key.Key_Return,Qt.KeyboardModifier.NoModifier))
            assert [r.speaker_name for r in b.document.main_timeline]==['合唱','合唱']
            self.command('history.undo')
            assert b.document.main_timeline==before
        self.window.setProperty('fullWidthReview',False)
        self.record('mixed_speakers_review_and_full_width',editors=2,ime_events=True)

        self.step='review-new rejected save followed by ordinary save'
        doc=b.document
        self.command('project.newFromReview')
        yield lambda:guard.property('visible')
        with patch.object(b,'_path',return_value=str(self.root/'bad.txt')):
            assert not b.command('close.resolve',{'choice':'save'})
        protected=deepcopy(doc.project.to_dict())
        path=self.root/'ordinary.lyria'
        self.command('project.saveAs',path=str(path))
        yield lambda:path.is_file() and not b.jobs.active
        from lyricsync.project_document import load_document
        assert b.document is doc and b.stage=='review' and b.document.project.to_dict()==load_document(path).project.to_dict()==protected
        self.record('rejected_new_save_cannot_clear_later_save_as')

    def finish(self, ok, error=''):
        self.timer.stop()
        from lyricsync import __display_version__
        report = dict(kind='ocr-workflow', version=__display_version__, ok=ok, error=error, step=getattr(self,'step','startup'), checks=self.results)
        self.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        self.app.exit(0 if ok else 1)
