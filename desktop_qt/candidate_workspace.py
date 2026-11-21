"""Candidate browsing/review without mutating the main editing projection."""
from copy import deepcopy
import time
import uuid
from bisect import bisect_right
from PySide6.QtCore import Slot, Qt
from lyricsync.model import Mapping, Project
from lyricsync.project_document import ProjectDocument
from lyricsync.candidate_tracks import view_rows, install_candidate, prepare_apply, apply_plan, set_ignored, candidate_source_status
from .entry_initialization import EntryInitialization

class CandidateWorkspace(EntryInitialization):
    document: ProjectDocument

    def _candidate_init(self):
        previous=getattr(self,'_candidate_job',None)
        if previous and hasattr(self,'jobs'):self.jobs.cancel(previous)
        self._candidate_task=dict(running=False,label='',progress=0.,id='')
        self._candidate_evidence={}
        self._timeline_choices_key=None;self._timeline_choices=[]
        self._candidate_source='main';self._candidate_project=None
        self._candidate_generation=getattr(self,'_candidate_generation',0)+1
        self._candidate_job=None;self._candidate_saved_selection='';self._candidate_rows=[];self._candidate_pending=None;self._candidate_projection_key=None

    def _candidate_record(self):
        from .timeline_views import current_candidate
        return next((r for r in reversed(self.document.source_candidates) if r['source']==self._candidate_source and current_candidate(self.document,r)),None)

    def _candidate_rebuild(self):
        self._timeline_choices_key=None
        if self._candidate_source=='main':return
        if self._candidate_source.startswith('saved:'):
            from .timeline_views import archived_project
            self._candidate_project=archived_project(self.document.project,self._candidate_source[6:])
            self._candidate_rows=[];self._candidate_evidence={}
            return
        record=self._candidate_record()
        key=(self.document.id,self.document.revision,self._candidate_source,record['id'] if record else None)
        if key==self._candidate_projection_key:return
        self._candidate_projection_key=key
        project=Project.from_dict(self.document.project.to_dict());project.replace_timeline([]);projection=[]
        self._candidate_rows=view_rows(self.document,self._candidate_source,record=record) if record else []
        self._candidate_evidence={}
        evidence=record['evidence'] if record else {}
        lyrics={item['id']:item['text'] for item in evidence.get('lyrics',[])}
        visuals={item['id']:item['text'] for item in evidence.get('visuals',[])}
        main_lyrics={item.id:item.text for item in self.document.project.lyrics}
        main_visuals={item.id:item.text for item in self.document.project.visuals}
        main_rows={item.id:item for item in self.document.project.project_timeline(detached=True)}
        attributed = {}
        if record and self._candidate_source == 'ocr' and any(v.get('evidence', {}).get('roi_configuration') for v in evidence.get('visuals', [])):
            from lyricsync.candidate_tracks import candidate_project
            detached = candidate_project(record, project)
            attributed = {r.id: (r.speaker_name, r.style_id) for r in detached.mappings}
        labels={'missing':'无识别结果','new':'未关联新句','ready':'待应用','orphaned':'原主轴句已删除'}
        for row in self._candidate_rows:
            text=row['text']
            provenance=row.get('provenance',{})
            if row['status']=='missing' and not provenance:
                text='【暂无识别结果】主轴原句：'+text
                main=main_rows.get(row.get('sentence_id'))
                lyric_text='\n'.join(main_lyrics.get(i,'') for i in main.lyric_ids) if main else ''
                visual_text='\n'.join(main_visuals.get(i,'') for i in main.visual_ids) if main else ''
            else:
                lyric_text='\n'.join(lyrics.get(i,'') for i in provenance.get('lyric_ids',[]))
                visual_text='\n'.join(visuals.get(i,'') for i in provenance.get('visual_ids',[]))
            # Evidence is a read-only projection of this candidate's tables.
            # Do not substitute main data with colliding IDs or alter either table.
            self._candidate_evidence[row['id']]={'source':lyric_text,'ocr':visual_text,'sourceFromOcr':bool(provenance.get('lyric_ids')) and all(i.startswith('ocr-source-') for i in provenance.get('lyric_ids',[]))}
            speaker, style = attributed.get(row['id'], ('', ''))
            projection.append(Mapping(row['id'],[],speaker_name=speaker,style_id=style,text_override=text,text_role=row.get('text_role','translation'),start=row['start'],end=row['end'],locked=True,ignored=row.get('ignored',False),reasons=[labels.get(row['status'],row['status'])]+list(row.get('reasons',[]))+(['结果已过期'] if row.get('stale') else [])+(['已忽略'] if row.get('ignored') else [])))
        project.replace_timeline(projection)
        self._candidate_project=project

    def _candidate_state(self):
        from .timeline_views import timeline_choices
        key=(self.document.id,self.document.revision)
        if self._timeline_choices_key!=key:
            self._timeline_choices=timeline_choices(self.document);self._timeline_choices_key=key
            from .timeline_views import current_candidate
            self._can_compare = any(current_candidate(self.document, r) for r in self.document.source_candidates)
        choices=self._timeline_choices
        record=self._candidate_record()
        if self._candidate_source=='main':status='主时间轴'
        elif self._candidate_source.startswith('saved:'):
            status=next((r['label'] for r in choices if r['id']==self._candidate_source),'保留时间轴（只读）')
        elif not record:status='人声打轴 · 暂无结果' if self._candidate_source=='vocal' else 'OCR 结果 · 暂无记录'
        else:
            rows=self._candidate_rows
            stale=sum(bool(r.get('stale')) for r in rows)
            status=f"识别预览（只读）· 共 {len(record['rows'])} 句"+(f' · {stale} 句已过期' if stale else '')
            warning=candidate_source_status(self.document,record,self.media_controller.index)
            if warning:status+=' · '+warning
            if self._candidate_source=='vocal':
                fixture=any('fixture' in reason.lower() for row in record['rows'] for reason in row.get('reasons',[]))
                status+=' · 测试样例数据' if fixture else ' · 识别结果仅供参考，请结合试听核对'
        if self._candidate_job:status+=' · '+self._candidate_task.get('label','正在后台处理…')
        return dict(candidateSource=self._candidate_source,candidateReadOnly=self._candidate_source!='main',candidateAvailable=bool(record),candidateComparisonAvailable=self._can_compare,candidateStatus=status,candidateRunning=bool(self._candidate_job),candidateTask=deepcopy(self._candidate_task),timelineChoices=choices,timelineSwitchAvailable=len(choices)>1)

    def _candidate_guard(self,name):
        if self._candidate_source=='main':return
        allowed={'review.select','row.select','timeline.select','row.multiToggle','row.multiClear','view.filter','view.layout','stage.set','error.clear','project.save','project.saveAs','project.open','project.new','project.newEntry','project.home','project.resume','project.recentForget','project.recover','project.restore','close.resolve','history.show','log.show','diagnostics.export','task.cancel','editor.start','vocal.configure'}
        if name in allowed or name.startswith('media.') and name!='media.open':return
        raise ValueError('当前处于识别结果预览模式（只读），请切回主时间轴后再编辑、撤销或导出。')

    @Slot(str)
    def switchCandidateSource(self,source):
        from .timeline_views import archives
        allowed={'main','ocr','vocal'}|{'saved:'+r['id'] for r in archives(self.document.project)}
        if source not in allowed or source==self._candidate_source:return
        if self._editor_active or self._editor_composing:
            self._error('请先完成当前文字输入。');return
        self.history.end()
        if self._candidate_source=='main':self._candidate_saved_selection=self.selected
        if self._alignment_preview.active:self._alignment_preview.clear()
        self._candidate_source=source;self._candidate_generation+=1
        if source=='main' and self._candidate_saved_selection:self.selected=self._candidate_saved_selection
        self._manual_axis_mode=False;self._manual_start_frame=None
        self._refresh()

    def _candidate_changed(self):
        self.editor.project=self.document.project
        self.autosave_timer.start();self._refresh()

    def _candidate_install(self,record,*,initial_complete=True,on_initialized=None,on_result=None):
        try:
            before_revision=self.document.revision
            if install_candidate(self.document,record):
                self._candidate_changed()
                context=self._entry_completed_context if initial_complete else None
                if not self._entry_try_initialize(record,context,before_revision,on_initialized,on_result):
                    if self._candidate_source!='main':self.switchCandidateSource('main')
                    if on_result is None:
                        self._message('识别结果已保存；为保护已有编辑，未自动覆盖主轴。可继续编辑当前主轴，完整结果保留在工程识别历史中。')
                    self._refresh(False)
            elif on_result:
                on_result(dict(status='retained', reason='duplicate', applied=0, blocked=0, applied_timed=0))
        except ValueError:
            if len(self.document.source_candidates)>=12:self._candidate_pending=record
            raise

    def _complete_vocal_candidate(self, record, cache_root=None):
        """Distinguish model timing from verified writes and navigation."""
        from lyricsync.local_alignment import alignment_result_summary
        summary = alignment_result_summary(record)
        def manifest(status, reason, applied=0, applied_timed=0, blocked=0):
            if not cache_root:return
            from lyricsync.local_alignment import update_alignment_manifest
            application=dict(status=status,reason=reason,valid=summary['valid'],missing=summary['missing'],
                             applied=applied,applied_timed=applied_timed,blocked=blocked)
            navigation=dict(status='review' if self.stage=='review' else 'not_review',reason=reason)
            self._start_job('alignment-manifest',lambda cancel,progress:update_alignment_manifest(
                cache_root,record,application=application,navigation=navigation),
                lambda result,error:self.diagnostics.record('worker-failed',error,stage='alignment') if error else None,
                foreground=False,diagnostic_stage='alignment')

        interrupted = next((reason.split(':',1)[1] for row in record['rows'] for reason in row.get('reasons',[])
                            if reason in ('interrupted-run:cancelled','interrupted-run:failed')), None)
        if interrupted:
            from PySide6.QtWidgets import QMessageBox
            box=QMessageBox();box.setWindowTitle('打轴中断：保留已完成部分？')
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.setText(f"任务中断。已完成分段有 {summary['valid']} 句合法时间，另有 {summary['missing']} 句未定位。\n保留候选不会自动写入或覆盖主轴。")
            keep=box.addButton('保留已完成部分并进入核对',QMessageBox.ButtonRole.AcceptRole)
            box.addButton('完全放弃本次候选',QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(keep);box.exec()
            if box.clickedButton()!=keep:
                self._candidate_task.update(label='已放弃中断任务候选',resultStatus='discarded',valid=summary['valid'],missing=summary['missing'],applied=0,appliedTimed=0,blocked=0,reason='interrupted_discarded')
                manifest('discarded','interrupted_discarded');self._refresh(False)
                self._message('已放弃本次中断任务候选；原主轴与既有识别记录未改变。');return
            self._entry_session['eligible']=False
            try:
                self._candidate_install(record,initial_complete=False)
            except (ValueError,RuntimeError):
                self._candidate_task.update(label='结果保存失败，主轴未自动修改',resultStatus='failed',
                    valid=summary['valid'],missing=summary['missing'],applied=0,appliedTimed=0,blocked=0,reason='install_failed')
                self.diagnostics.record('alignment-result',stage='alignment',alignment={
                    'status':'failed','reason':'install_failed','total':summary['total'],'valid':summary['valid'],
                    'missing':summary['missing'],'applied':0,'applied_timed':0,'blocked':0,'review':self.stage=='review'})
                manifest('failed','install_failed');self._refresh(False)
                raise
            self._candidate_task.update(label='已保留中断任务候选，待核对',resultStatus='retained',valid=summary['valid'],missing=summary['missing'],applied=0,appliedTimed=0,blocked=0,reason='interrupted_'+interrupted)
            self.stage='review';self.switchCandidateSource('main');self._refresh(False);self._offer_vocal_application(record)
            manifest('retained','interrupted_'+interrupted)
            self._message(f"已保留 {summary['valid']} 句有时间的候选，其余 {summary['missing']} 句未定位。主轴未改动；请预览确认后应用所选句。");return

        def completed_application(result):
            applied=result.get('applied',0);timed=result.get('applied_timed',0)
            status='empty' if not summary['valid'] else result['status']
            reason='empty_result' if not summary['valid'] else result['reason']
            self._candidate_task.update(label='已进入可编辑主轴' if applied else '无有效打轴结果' if status=='empty' else '候选待对比应用',
                resultStatus=status,valid=summary['valid'],missing=summary['missing'],applied=applied,
                appliedTimed=timed,blocked=result.get('blocked',0),reason=reason)
            if applied:
                self.switchCandidateSource('main');self.stage='review';self._select_first_candidate_issue(record)
            elif summary['valid']:
                self._message('实际写入主轴 0 句；当前主轴保持可编辑，请对比确认所选句。')
                self.switchCandidateSource('main');self.stage='review'
                self._offer_vocal_application(record)
            else:
                details='；'.join(dict.fromkeys(str(x) for row in record['rows'] for x in row.get('reasons',[]) if not str(x).startswith('diagnostic-run:')))[:600]
                self._message(summary['message']+' 未跳转空白核对页。'+details)
            self._refresh(False)
            self.diagnostics.record('alignment-result',stage='alignment',alignment={
                'status':status,'reason':reason,'total':summary['total'],'valid':summary['valid'],
                'missing':summary['missing'],'applied':applied,'applied_timed':timed,
                'blocked':result.get('blocked',0),'review':self.stage=='review'})
            manifest(status,reason,applied,timed,result.get('blocked',0))
        try:
            self._candidate_install(record,initial_complete=bool(summary['valid']),on_result=completed_application)
        except (ValueError, RuntimeError):
            self._candidate_task.update(label='结果保存失败，主轴未自动修改', resultStatus='failed',
                valid=summary['valid'], missing=summary['missing'], applied=0,
                appliedTimed=0, blocked=0, reason='install_failed')
            self.diagnostics.record('alignment-result', stage='alignment', alignment={
                'status':'failed', 'reason':'install_failed', 'total':summary['total'],
                'valid':summary['valid'], 'missing':summary['missing'], 'applied':0,
                'applied_timed':0, 'blocked':0, 'review':self.stage == 'review'})
            self._refresh(False)
            manifest('failed','install_failed')
            raise

    def _offer_vocal_application(self,record):
        """Reruns stay editable; unpaired source/translation conflict has an exit."""
        if (self.document.project.translations and record['rows'] and
                all(r['sentence_id'] is None and r.get('text_role','translation')!='translation' for r in record['rows'])):
            from PySide6.QtWidgets import QMessageBox
            box=QMessageBox();box.setWindowTitle('演唱原词与译文尚未配对');box.setTextFormat(Qt.TextFormat.PlainText)
            box.setText(f"本次 {len(record['rows'])} 句演唱原词与已有 {len(self.document.project.translations)} 行译文尚未关联。\n建立原词主轴会完整保留旧时间轴与全部原译文，随后进入配对；可一次撤销。")
            build=box.addButton('以已打轴原词建立主轴，保留译文待配对',QMessageBox.ButtonRole.AcceptRole)
            compare=box.addButton('对比现有主轴并应用',QMessageBox.ButtonRole.ActionRole)
            box.addButton('暂不应用',QMessageBox.ButtonRole.RejectRole);box.exec()
            if box.clickedButton() is build:
                self._rebuild_vocal_main(record);return
            if box.clickedButton() is not compare:return
        from .candidate_dialog import CandidateDialog
        dialog=CandidateDialog(self,record);dialog.exec()

    def _rebuild_vocal_main(self,record):
        from .vocal_application import prepare_rebuild,commit_rebuild
        detached=deepcopy(self.document);index=deepcopy(self.media_controller.index)
        def completed(result,error):
            if error:self._error(str(error));return
            try:changed=commit_rebuild(self.document,self.history,result,self.media_controller.index)
            except (ValueError,RuntimeError) as exc:self._error(str(exc));return
            self._entry_session['eligible']=False;self.switchCandidateSource('main');self.stage='review'
            if changed:self.selected=changed[0]
            self._candidate_changed()
            self._message('已建立可编辑演唱原词主轴；原译文完整保留待配对，旧时间轴已保留，可一次撤销。')
            self.command('pairing.workbench',{})
        self._start_job('正在校验原词主轴与译文保留…',lambda cancel,progress:prepare_rebuild(detached,record['id'],index,cancel),completed,foreground=False)

    def _select_first_candidate_issue(self,record):
        rows=record.get('rows',[])
        def issue(row):
            start,end=row.get('start'),row.get('end')
            return start is None or end is None or end-start<.2 or end-start>25 or (row.get('confidence') or 0)<.6
        chosen=next((row for row in rows if row.get('start') is None or row.get('end') is None),None)
        if chosen is None:chosen=next((row for row in rows if issue(row)),rows[0] if rows else None)
        if chosen:
            identity=chosen['id']
            if self._candidate_source=='main':
                note='candidate:'+record['id']+':'+chosen['id']
                target=next((r for r in self.document.main_timeline if note in r.reasons),None)
                if target is None:return
                identity=target.id
            self.selected=identity;self.selectionRequested.emit()

    @Slot()
    def clearOldCandidates(self):
        from PySide6.QtWidgets import QMessageBox
        from lyricsync.candidate_tracks import remove_candidates
        records=self.document.source_candidates
        keep={}
        for record in records:keep[record['source']]=record['id']
        old=[record for record in records if record['id'] not in keep.values()]
        if not old:self._message(f'当前共有 {len(records)} 条识别记录，暂无旧记录可清理。');return
        counts={source:sum(r['source']==source for r in old) for source in ('ocr','vocal')}
        message=f"将清理 {len(old)} 条旧识别记录（OCR {counts['ocr']} 条 / 人声打轴 {counts['vocal']} 条），仅保留各来源最新一次记录。\n已应用到主时间轴的字幕不受影响，清理后无法撤销，是否继续？"
        if self._candidate_pending:message+=' 清理后将自动保存本次新生成的识别结果。'
        if QMessageBox.question(None,'清理旧识别记录',message)!=QMessageBox.StandardButton.Yes:return
        try:
            remove_candidates(self.document,[r['id'] for r in old]);self._candidate_changed()
            if self._candidate_pending:
                pending=self._candidate_pending;self._candidate_install(pending);self._candidate_pending=None
            self._message('已清理旧识别记录。')
        except Exception as exc:self._error(str(exc))

    @Slot(int,result=float)
    def timelineFrameSeconds(self,frame):
        index=self.media_controller.index
        if index is None:return 0.
        if frame>=len(index.stamps)+1:return float(index.duration)
        return float(index.stamp(max(1,frame)))

    @Slot(float,result=float)
    def timelineSecondsFrame(self,seconds):
        index=self.media_controller.index
        if index is None:return 1.
        pos=max(0,min(len(index.stamps)-1,bisect_right(index.stamps,seconds)-1))
        start=index.stamps[pos];end=index.stamps[pos+1] if pos+1<len(index.stamps) else index.duration
        return float(pos+1+max(0.,min(1.,(seconds-start)/(end-start))))

    def _start_candidate_job(self,kind,source,function,callback):
        if self._candidate_job:raise ValueError('当前已有识别任务正在运行，请等待完成或先取消。')
        context=self._entry_capture_job(source)
        def completed(result,error):
            cancelled=self._candidate_task.get('cancelling',False)
            self._candidate_job=None
            self._candidate_task.update(running=False,id='',progress=1.,cancelling=False,cancelRequested=cancelled,
                                        label='已取消' if isinstance(error,InterruptedError) else '失败' if error else '识别完成')
            self._entry_completed_context=context if not error and not cancelled else None
            try:callback(result,error)
            finally:self._entry_completed_context=None
        identity=self._start_job(kind,function,completed,foreground=False,diagnostic_stage='ocr' if source=='ocr' else 'alignment')
        self._candidate_job=identity
        self._candidate_task=dict(running=True,label=kind,progress=0.,id=identity,startedAt=time.time()*1000,source=source,cancelling=False)
        self._refresh(False)
        return identity

    @Slot()
    def cancelCandidateJob(self):
        if self._candidate_job:
            # A late successful result after cancellation is still only a candidate.
            self._entry_session['version']=uuid.uuid4().hex
            self.jobs.cancel(self._candidate_job)
            self._candidate_task.update(cancelling=True,label='正在取消打轴…' if self._candidate_task.get('source') == 'vocal' else '正在取消 OCR…')
            self._refresh(False)

    @Slot()
    def manageTimelines(self):
        from PySide6.QtWidgets import QInputDialog
        from .timeline_views import timeline_choices
        choices = timeline_choices(self.document)
        labels = [f"{index + 1}. {item['label']}" for index, item in enumerate(choices)]
        active = next((i for i, item in enumerate(choices) if item['id'] == self._candidate_source), 0)
        label, accepted = QInputDialog.getItem(None, '切换或管理保留时间轴', '选择要查看的时间轴', labels, active, False)
        if accepted and label in labels:
            self.switchCandidateSource(choices[labels.index(label)]['id'])

    @Slot()
    def reopenOcrResult(self):
        task = self._candidate_task
        if task.get('source') != 'ocr' or task.get('resultDocument') != self.document.id:
            return
        record = next((r for r in self.document.source_candidates if r['id'] == task.get('resultId')), None)
        if record is not None:
            self._open_candidate_result(record)

    @Slot()
    def compareCandidates(self):
        from datetime import datetime
        from PySide6.QtWidgets import QInputDialog
        from .timeline_views import current_candidate
        records = [r for r in reversed(self.document.source_candidates) if current_candidate(self.document, r)]
        if not records:
            self._error('当前工程暂无可对比的识别或打轴结果。请在导入页确认选区并运行识别。')
            return
        labels = []
        for i, result in enumerate(records):
            source = '画面 OCR' if result['source'] == 'ocr' else '人声自动打轴'
            when = datetime.fromtimestamp(result['created_at']).strftime('%Y-%m-%d %H:%M:%S') if 'created_at' in result else '旧记录，生成时间未记录'
            labels.append(f"{len(records)-i}. {source} · {when} · {len(result['rows'])} 句")
        label, accepted = QInputDialog.getItem(None, '对比与应用识别/打轴结果', '选择要对比的结果', labels, 0, False)
        if not accepted or label not in labels:
            return
        record = records[labels.index(label)]
        self._open_candidate_result(record)

    def _open_candidate_result(self, record, *, automatic=False):
        from .timeline_views import current_candidate
        if record.get('document_id') != self.document.id or not current_candidate(self.document, record):
            self._error('这份结果属于先前的素材，请从字幕菜单选择当前工程的结果。')
            return
        from .candidate_dialog import CandidateDialog
        self._entry_session['eligible'] = False
        if automatic:
            previous = getattr(self, '_ocr_result_window', None)
            if previous is not None:
                previous.close()
        try:
            dialog = CandidateDialog(self, record)
            if automatic:
                self._ocr_result_window = dialog
                dialog.finished.connect(lambda: setattr(self, '_ocr_result_window', None)
                                        if self._ocr_result_window is dialog else None)
                dialog.open()
            else:
                dialog.exec()
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            message = '这份结果已保存在工程中，但核对窗口未能打开。可从字幕菜单重新打开。' + str(exc)
            if automatic:
                self._ocr_result_window = None
                self._ocr_result_notice('failed',message)
            else:
                self._error(message)

    @Slot()
    def loadCandidateFixture(self):
        path=self._path({},False,'载入识别测试数据','JSON (*.json)')
        if not path:return
        detached=deepcopy(self.document);index=deepcopy(self.media_controller.index)
        def work(cancel,progress):
            import json
            from pathlib import Path
            from lyricsync.candidate_tracks import capture_source,candidate_from_project
            source=Path(path)
            if source.stat().st_size>8*1024*1024:raise ValueError('测试数据超过 8 MiB。')
            if cancel():raise InterruptedError('已取消测试数据载入')
            data=json.loads(source.read_text(encoding='utf-8'))
            if not isinstance(data,dict):raise ValueError('测试数据必须是 JSON 对象。')
            if data.get('fixture') is not True or data.get('source') not in ('ocr','vocal'):raise ValueError('测试文件需包含 fixture:true 且 source 为 ocr 或 vocal。')
            project=data.get('project')
            if not isinstance(project,dict) or not isinstance(project.get('mappings'),list) or len(project['mappings'])>10000:raise ValueError('测试数据的 project.mappings 不能超过 10000 行。')
            captured=capture_source(detached,data['source'],index,cancel=cancel)
            generated=Project.from_dict(project)
            for row in generated.mappings:row.reasons.append('显式测试 fixture，非真实模型识别')
            result=candidate_from_project(captured,generated)
            if cancel():raise InterruptedError('已取消测试数据载入')
            return result
        def completed(record,error):
            if error:raise error
            self._candidate_install(record);self._message('已载入测试样例数据。')
        self._start_job('candidate fixture',work,completed,foreground=False)

    def candidate_preview(self,record_id,ids,bindings,add_new,callback,apply_text=False,time_overrides=None,explicit_visual_regroup=False):
        # Explicit review owns the next decision. A concurrent initializer must
        # not apply behind its preview/confirmation dialog.
        from .timeline_views import current_candidate
        selected_record=next((r for r in self.document.source_candidates if r['id']==record_id),None)
        if selected_record is not None and not current_candidate(self.document,selected_record):
            callback(None,ValueError('这是先前处理会话的历史结果，已保留证据；请重新运行后再应用到当前会话。'));return
        self._entry_session['eligible']=False
        detached=deepcopy(self.document);index=deepcopy(self.media_controller.index)
        self.history.end()
        record=next((r for r in detached.source_candidates if r['id']==record_id),None)
        explicit_source_draft=bool(add_new and not detached.main_timeline and not detached.project.translations
            and record and record['source']=='vocal' and record['rows']
            and all(r.get('text_role')=='source_asr' for r in record['rows']))
        def work(cancel,progress):return prepare_apply(detached,record_id,ids,frame_index=index,bindings=bindings,add_unmapped=add_new,apply_text=apply_text,cancel=cancel,time_overrides=deepcopy(time_overrides),initial_asr_draft=explicit_source_draft,explicit_visual_regroup=explicit_visual_regroup)
        return self._start_job('candidate verification',work,lambda plan,error:callback(plan,error),foreground=False)

    def candidate_apply(self,plan):
        from .timeline_views import current_candidate
        record=next((r for r in self.document.source_candidates if r['id']==plan.get('candidate_id')),None)
        if record is not None and not current_candidate(self.document,record):raise ValueError('历史会话结果不能应用到当前会话；原始证据仍已保留。')
        changed=apply_plan(self.document,self.history,plan,frame_index=self.media_controller.index)
        if changed:
            self._entry_session['eligible']=False
            if self._candidate_source!='main':self.switchCandidateSource('main')
            self.selected=changed[0];self.stage='review'
        self._candidate_changed()
        return changed

    def candidate_ignore(self,record_id,ids,ignored=True):
        self.history.end()
        if set_ignored(self.document,self.history,record_id,ids,ignored):self._candidate_changed()
