"""Narrow, validated Qt/QML bridge over Lyria's Python domain.

The GUI thread owns the document and history. Workers receive detached snapshots,
never a live project. Row model notifications are independent from video ticks.
No secrets, screenshot bytes or synthetic fixture data are exposed in state.
"""
from __future__ import annotations

from bisect import bisect_right
from copy import deepcopy
from dataclasses import asdict
import json
import math
from pathlib import Path
import tempfile
import threading
import time
import uuid

from PySide6.QtCore import (QAbstractListModel, QModelIndex, QObject, Property,
                            QStandardPaths, Qt, QTimer, QUrl, Signal, Slot)
from PySide6.QtCore import QTextBoundaryFinder
from PySide6.QtWidgets import QFileDialog

from lyricsync.editing import (Editor, History, mapping_text, merge_mappings, split_mapping,
                              pending_text_ids, pending_pair, validate_pending_pair)
from lyricsync.frames import FrameIndex
from lyricsync.model import Mapping, Project, valid_time, text_role_label
from lyricsync.project_document import ProjectDocument, load_document, write_document
from .media import MediaController
from .candidate_workspace import CandidateWorkspace
from .audio_source_analysis import AudioSourceAnalysis


ROLE_NAMES = ("cueId", "id", "number", "chinese", "source", "ocr", "firstFrame", "lastFrame",
              "first", "last", "confirmed", "locked", "protected", "overlap", "needsReview", "aiNote", "reasons", "pendingText", "untimed", "textRole", "textRoleLabel", "sourceFromOcr", "speakerName", "styleName", "styleId", "styleOverrides", "speakerId", "speakerColor", "speakerDefault", "specialStyle", "cps", "cpsWarning", "durationSeconds", "startSeconds", "endSeconds", "timeLabel", "translationText")


class CueModel(QAbstractListModel):
    countChanged=Signal()
    projectionReset=Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent); self.items=[];self._source_key=None
        self.roles={int(Qt.ItemDataRole.UserRole)+n+1:name.encode() for n,name in enumerate(ROLE_NAMES)}

    def roleNames(self):return self.roles
    def rowCount(self, parent=QModelIndex()):return 0 if parent.isValid() else len(self.items)
    @Property(int,notify=countChanged)
    def count(self):return len(self.items)
    @Slot(int,result='QVariantMap')
    def get(self,position):
        return deepcopy(self.items[position]) if 0<=position<len(self.items) else {}
    def data(self,index,role):
        if not index.isValid() or not 0<=index.row()<len(self.items):return None
        name=self.roles.get(role)
        return self.items[index.row()].get(name.decode()) if name else None

    def replace(self, rows, source_key=None):
        # Keep the ListView alive throughout edits. A model reset exposes an
        # empty model for a render turn and resets its scroll origin to zero.
        # Rows are slot projections; cue identity is always an explicit role.
        old_count=len(self.items)
        source_changed=source_key is not None and self._source_key is not None and source_key!=self._source_key
        if source_key is not None:self._source_key=source_key
        # Loading/replacing an entire dataset has no meaningful existing view.
        # Keep a reset for that boundary, never for ordinary edits/reorders/splits.
        shared_ids={r['id'] for r in self.items}&{r['id'] for r in rows}
        if source_changed or old_count>10 and (not shared_ids or len(shared_ids)<old_count/2 and old_count-len(rows)>10):
            self.beginResetModel();self.items=deepcopy(rows);self.endResetModel()
            if old_count!=len(rows):self.countChanged.emit()
            self.projectionReset.emit(not source_changed)
            return
        prefix=0
        while prefix<min(old_count,len(rows)) and self.items[prefix]['id']==rows[prefix]['id']:
            prefix+=1
        suffix=0
        while suffix<min(old_count,len(rows))-prefix and self.items[old_count-1-suffix]['id']==rows[len(rows)-1-suffix]['id']:
            suffix+=1
        shared=min(old_count-prefix-suffix,len(rows)-prefix-suffix)
        position=prefix+shared
        if old_count>len(rows):
            self.beginRemoveRows(QModelIndex(),position,position+old_count-len(rows)-1)
            del self.items[position:position+old_count-len(rows)]
            self.endRemoveRows()
        elif len(rows)>old_count:
            self.beginInsertRows(QModelIndex(),position,position+len(rows)-old_count-1)
            self.items[position:position]=deepcopy(rows[position:position+len(rows)-old_count])
            self.endInsertRows()
        for position,(old,new) in enumerate(zip(self.items,rows)):
            if old!=new:
                changed=[role for role,name in self.roles.items() if old.get(name.decode())!=new.get(name.decode())]
                self.items[position]=deepcopy(new)
                self.dataChanged.emit(self.index(position),self.index(position),changed)
        if old_count!=len(self.items):self.countChanged.emit()


class Jobs(QObject):
    finished=Signal(str,object,object)
    progressed=Signal(str,float,object)

    def __init__(self,parent=None):
        super().__init__(parent);self.active={};self.threads={};self.kinds={};self.outcomes={};self.completed={};self.acknowledged=set();self.closing=False

    def run(self,kind,function, *, during_shutdown=False):
        if self.closing and not during_shutdown:raise ValueError('程序正在关闭，无法启动新任务。')
        self.reap_threads()
        identity=kind+'-'+uuid.uuid4().hex
        cancelled=threading.Event();self.active[identity]=cancelled
        def work():
            result,error=None,None
            try:
                result=function(cancelled.is_set,lambda progress,label:self.progressed.emit(identity,float(progress),label))
            except BaseException as exc:
                error=exc
            self.outcomes[identity]=error
            try:self.finished.emit(identity,result,error)
            except RuntimeError:pass
        thread=threading.Thread(target=work,name='lyria-'+kind,daemon=True)
        self.threads[identity]=thread;self.kinds[identity]=kind
        try:thread.start()
        except BaseException:
            self.threads.pop(identity,None);self.kinds.pop(identity,None);self.active.pop(identity,None)
            raise
        return identity

    def cancel(self,identity):
        if identity in self.active:self.active[identity].set()

    def reap_threads(self):
        """GUI-thread bookkeeping. A queued finished signal is NOT thread exit."""
        ended=[]
        for identity,thread in list(self.threads.items()):
            if thread.is_alive():continue
            thread.join(0)
            error=self.outcomes.pop(identity,None)
            ended.append((identity,error))
            if identity not in self.acknowledged:self.completed[identity]=error
            self.acknowledged.discard(identity)
            self.threads.pop(identity,None);self.kinds.pop(identity,None)
        return ended

    def acknowledge(self,identity):
        self.completed.pop(identity,None)
        if identity in self.threads:self.acknowledged.add(identity)

    def take_completed(self):
        result=list(self.completed.items());self.completed.clear();return result

    def live_threads(self):
        return [thread for thread in self.threads.values() if thread.is_alive()]

    def live_kind(self,kind):
        return any(self.kinds.get(identity)==kind and thread.is_alive()
                   for identity,thread in self.threads.items())

    def begin_shutdown(self):
        self.closing=True
        for identity in tuple(self.active):
            # Already accepted saves run to completion, including atomic replace.
            if self.kinds.get(identity) not in ('save','shutdown-save'):self.cancel(identity)


class Bridge(AudioSourceAnalysis, CandidateWorkspace, QObject):
    stateChanged=Signal()
    selectionRequested=Signal()
    mediaChanged=Signal()
    playbackCursorChanged=Signal()
    audioAnalysisChanged=Signal()
    closeDialogRequested=Signal('QVariantMap')
    quitRequested=Signal()
    shutdownFinished=Signal()
    message=Signal(str)
    notice=Signal(str,str)
    razorSplitCreated=Signal(str,str)
    cueCreated=Signal(str)

    @staticmethod
    def _pairing_note(project, row):
        from lyricsync.api import review_signature
        result=next((r for r in project.settings.get('api_suggestions',[]) if isinstance(r,dict) and r.get('mapping_id')==row.id),None)
        if not result: return ''
        if result.get('status')!='processed' or result.get('context_signature')!=review_signature(project,row): return '内容已改动或尚未完成，请重新核对'
        value=result.get('suggestion',{})
        verdict={'match':'对应','mismatch':'不对应','uncertain':'不确定'}.get(value.get('verdict'))
        return verdict+'：'+value.get('reason','') if verdict else '旧版核对记录，请重新核对'

    def __init__(self,parent=None,storage_dir=None,diagnostics=None,*,startup=False):
        super().__init__(parent)
        try:
            from importlib.util import find_spec
            self._ocr_available = find_spec('onnxruntime') is not None and any(
                find_spec(engine) is not None for engine in ('rapidocr', 'pytesseract'))
            from .diagnostics import DiagnosticStore
            self.diagnostics=diagnostics or DiagnosticStore(storage_dir)
            from .surface import register_types
            register_types()
            from .spectrogram_item import register_types as register_spectrogram_types
            register_spectrogram_types();self._surface=None;self._shutdown=False
            self._shutdown_complete=False;self._quit_after_shutdown=False;self._shutdown_errors=[]
            self._shutdown_external=False;self._shutdown_failure_waiting=False;self._shutdown_failed_saves=[]
            self._accepted_save_intents={};self._shutdown_retry_queue=[]
            self._shutdown_timer=QTimer(self);self._shutdown_timer.setInterval(20)
            self._shutdown_timer.timeout.connect(self._poll_shutdown)
            self.document=ProjectDocument(project=Project());self.document.mark_saved(0);self.document.mark_exported(0)
            self.history=History(context=self._history_context);self.editor=Editor(self.document.project,self.history,self._changed)
            self.model=CueModel(self);self.timeline_model=CueModel(self)
            self.media_controller=MediaController(self)
            self.media_controller.changed.connect(self.mediaChanged)
            self.media_controller.cursorChanged.connect(self.playbackCursorChanged)
            self.media_controller.presented.connect(self._frame_presented)
            self.media_controller.loaded.connect(self._media_loaded)
            self.media_controller.error.connect(self._error)
            self.jobs=Jobs(self);self.jobs.finished.connect(self._job_finished);self.jobs.progressed.connect(self._job_progress)
            self.storage_dir=Path(storage_dir or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
            self.storage_dir.mkdir(parents=True,exist_ok=True)
            from .recent_projects import RecentProjects
            self._recent_projects=RecentProjects(self.storage_dir)
            self._recent_snapshot=self._recent_projects.snapshot()
            self._startup_visible=bool(startup)
            self._has_workspace=not startup
            from .file_dialog_history import FileDialogHistory
            self._file_dialog_history=FileDialogHistory(self.storage_dir)
            from .workspace_layout import DEFAULTS, update_layout
            self._workspace_layout=dict(DEFAULTS)
            try:
                saved=json.loads((self.storage_dir/'workspace-layout.json').read_text(encoding='utf-8'))
                self._workspace_layout=update_layout(self._workspace_layout,saved,loading=True)
            except (OSError,ValueError,AttributeError):pass
            self._candidate_init()
            self._entry_reset(new=True)
            self.path='';self.selected='';self.stage='import';self._entry_mode='ocr';self.rows_revision=0;self.epoch=0
            self._all_rows=[];self._row_positions={};self._visible_positions={};self._confirmed_count=0;self._omissions=[];self._export_compatibility=[];self._visible_ids=set();self._unconfirmed_only=False;self._editor_active=False;self._editor_composing=False;self._editor_id='';self._ime_commit_pending=False;self._hold_cue_selection=False;self._active_ids=[]
            self._timeline_selection=False;self._confirmed_selection='';self._timeline_bounds={'firstFrame':0,'lastFrame':0}
            self._timeline_spans=[];self._timeline_starts=[];self._timeline_prefix_ends=[];self._timeline_prefix_rows=[]
            self._log=[]
            self._error_text='';self._status='请导入音视频或字幕开始制作'
            self._task=dict(running=False,label='',progress=0.,id='')
            self._qc=dict(inputPath='',mode='local',issues=[],summary={},status='尚未运行',available=True);self._qc_preview=None;self._qc_source_generation=0
            self._alignment={};self._close_dialog={};self._continuation=None;self._closing=False
            self._allocation={};self._allocation_context=None
            from .alignment_preview import AlignmentPreview
            self._alignment_preview=AlignmentPreview()
            self._alignment_check_context=None;self._alignment_checked={};self._alignment_selection=None
            self._audio_revision=0;self._audio_analysis=dict(revision=0,status='idle',error='',durationSeconds=0.,waveform=[],spectrum=[],maxFrequency=4000);self._audio_job=None
            self._job_diagnostic_stages={};self._handlers={};self._save_pending=None;self._save_running=None;self._autosave_status='尚无更改'
            from lyricsync import ai_config
            self.api_settings=ai_config.load(self.storage_dir);self._api_key='';self._api_scope='';self._api_text_consents=set();self.api_state={'status':'首次调用前会询问授权','models':[]};self._api_resume=None
            self._last_autosaved_revision=-1
            self._manual_axis_mode=False;self._manual_start_frame=None;self._multi_selected=set()
            self._preview_stamps=[];self._preview_texts=[]
            self._layout_stamps=[];self._layout_rows=[];self._layout_revision=-1;self._layout_running=False;self._layout_pending=False;self._layout_error=''
            self.layout_timer=QTimer(self);self.layout_timer.setSingleShot(True);self.layout_timer.setInterval(120);self.layout_timer.timeout.connect(self._begin_layout)
            self.autosave_timer=QTimer(self);self.autosave_timer.setSingleShot(True);self.autosave_timer.setInterval(450)
            self.autosave_timer.timeout.connect(lambda:self._save(False))
            self._refresh()
        except BaseException:
            self._drain_failed_initialization()
            raise

    def _drain_failed_initialization(self):
        """The constructor may fail after MediaController has started a worker."""
        from PySide6.QtCore import QCoreApplication,QEventLoop
        for name in ('autosave_timer','layout_timer','_shutdown_timer'):
            timer=getattr(self,name,None)
            if timer is not None:timer.stop()
        media=getattr(self,'media_controller',None);jobs=getattr(self,'jobs',None)
        if media is not None:media.close()
        if jobs is not None:jobs.begin_shutdown()
        while ((media is not None and media.worker.thread.is_alive()) or
               (jobs is not None and jobs.live_threads())):
            QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents,20)
            time.sleep(.005)
        if media is not None:media.worker.thread.join(0)
        if jobs is not None:jobs.reap_threads()

    @Property('QVariantMap',notify=stateChanged)
    def state(self):return deepcopy(self._state)
    @Property('QVariantMap',notify=stateChanged)
    def uiState(self):
        # QML consumes the virtualized row model, not a full duplicate on every cue crossing.
        result=deepcopy({k:v for k,v in self._state.items() if k not in ('rows','rowsRevision','audioAnalysis')})
        result['audioAnalysis']={k:v for k,v in self._audio_analysis.items() if k not in ('waveform','spectrum')}
        return result
    @Property('QVariantMap',notify=audioAnalysisChanged)
    def audioAnalysis(self):return {**deepcopy(self._audio_analysis), **self._audio_source_state()}
    @Property('QVariantMap',notify=audioAnalysisChanged)
    def audioDisplaySource(self):
        # Preserve the complete legacy API; the viewport needs only metadata.
        result={k:v for k,v in self._audio_analysis.items()
                if v is None or type(v) in (str,bool,int,float)}
        return {**result, **self._audio_source_state()}
    @Property(float,notify=playbackCursorChanged)
    def playbackCursor(self):
        return self.media_controller.cursor_frame

    @Property('QVariantMap',notify=mediaChanged)
    def media(self):
        result=self.media_controller.snapshot()
        result['sourceEpoch']=self.media_controller.epoch
        pos=bisect_right(self._preview_stamps,result['positionSeconds']+1e-6)-1
        result['subtitleText']=self._preview_texts[pos] if pos>=0 and self.document.project.settings.get('subtitle_preview',True) else ''
        layout_pos=bisect_right(self._layout_stamps,result['positionSeconds']+1e-6)-1
        result['subtitleRows']=self._layout_rows[layout_pos] if layout_pos>=0 and self._layout_revision==self.document.revision and self.document.project.settings.get('subtitle_preview',True) else []
        result['subtitleLayoutPending']=self._layout_running or self.layout_timer.isActive()
        result['subtitleLayoutError']=self._layout_error
        result['subtitleLayoutRevision']=self.document.revision
        result['presentationPath']='cpu-painted-fallback' if self.softwareRendering else 'VideoOutput/QVideoSink'
        active=self._active_ids[pos] if 0<=pos<len(self._active_ids) else []
        result['activeCueIds']=active;result['activeCueCount']=len(active)
        roles={getattr(self,'_text_roles',{}).get(i,'translation') for i in active}
        result['subtitleTextRoleLabel']=(text_role_label(next(iter(roles))) if len(roles)==1
                                        else '含原文歌词' if roles else '')
        result['frameStatus']='gap' if not active else ('overlap' if len(active)>1 else 'single')
        hidden=sum(i not in self._visible_ids for i in active)
        result['hiddenCueCount']=hidden
        result['frameMessage']=f'当前有 {hidden} 句被筛选隐藏' if hidden else ('字幕空档' if not active else f'重叠 {len(active)} 句' if len(active)>1 else '')
        return result
    @Property(QObject,constant=True)
    def rows(self):return self.model
    @Property(QObject,constant=True)
    def timelineRows(self):return self.timeline_model

    @Slot(int,result=int)
    def timelineIndexAtFrame(self,frame):
        """Find a containing or nearest full-model row without crossing all rows into QML.

        Prefix maxima handle overlapping intervals and arbitrary mapping order.
        In gaps, equal-distance ties prefer the preceding interval. Overlap ties
        use the interval with the furthest end, then the lowest original row.
        """
        if type(frame) is not int or not self._timeline_spans:return -1
        position=bisect_right(self._timeline_starts,frame)-1
        if position<0:return self._timeline_spans[0][2]
        left_end=self._timeline_prefix_ends[position];left_row=self._timeline_prefix_rows[position]
        if left_end>=frame or position+1==len(self._timeline_spans):return left_row
        right_start,_,right_row=self._timeline_spans[position+1]
        return left_row if frame-left_end<=right_start-frame else right_row

    @Slot(float,float,int,result='QVariantList')
    def spectrumColumnTimes(self,first,span,columns):
        """Physical pixel centres on the real PTS axis, including tile overscan."""
        index=self.media_controller.index
        if index is None or not index.stamps:return []
        if type(columns) is not int or not 1<=columns<=32768:return []
        if any(type(value) not in (int,float) or not math.isfinite(value) for value in (first,span)) or span<=0:return []
        stamps=index.stamps;n=len(stamps)
        first_step=(stamps[1]-stamps[0]) if n>1 else index.duration-stamps[0]
        last_step=index.duration-stamps[-1]
        result=[]
        for column in range(columns):
            position=first+(column+.5)*span/columns-1.
            if position<0:value=stamps[0]+position*first_step
            elif position>=n-1:value=stamps[-1]+(position-(n-1))*last_step
            else:
                left=int(position);fraction=position-left
                value=stamps[left]+fraction*(stamps[left+1]-stamps[left])
            result.append(float(value))
        return result

    def _rebuild_timeline_index(self,items):
        spans=sorted((r['firstFrame'],r['lastFrame'],n) for n,r in enumerate(items)
                     if 0<r['firstFrame']<=r['lastFrame'])
        starts=[];ends=[];rows=[];furthest=0;furthest_row=-1
        for first,last,row in spans:
            if last>furthest or (last==furthest and row<furthest_row):furthest=last;furthest_row=row
            starts.append(first);ends.append(furthest);rows.append(furthest_row)
        self._timeline_spans=spans;self._timeline_starts=starts
        self._timeline_prefix_ends=ends;self._timeline_prefix_rows=rows
        self._timeline_bounds={'firstFrame':starts[0] if starts else 0,'lastFrame':ends[-1] if ends else 0}

    @Property(bool,constant=True)
    def softwareRendering(self):
        import os
        from PySide6.QtGui import QGuiApplication
        return os.environ.get('QT_QUICK_BACKEND')=='software' or QGuiApplication.platformName() in ('offscreen','minimal')

    @Slot(QObject)
    def attachFrameSurface(self,surface):
        from .surface import FrameSurface
        if not isinstance(surface,FrameSurface):raise ValueError('视频画面组件绑定失败（需要 FrameSurface）')
        self._surface=surface
        if self.softwareRendering:
            self.media_controller.frameReady.connect(surface.setFrame)
            if self.media_controller._last_image is not None:surface.setFrame(self.media_controller._last_image)

    @Slot(QObject)
    def attachVideoSink(self,sink):
        try:self.media_controller.attach_sink(sink)
        except Exception as exc:self._error(str(exc))

    def _display_project(self):
        if self._candidate_source != "main":return self._candidate_project
        return self._alignment_preview.project if self._alignment_preview.active else self.document.project

    def _display_row(self,identity=None):
        identity=identity or self.selected
        row=next((m for m in self._display_project().mappings if m.id==identity),None)
        if row is None:raise ValueError('请先选择字幕。')
        return row

    def _row(self,identity=None):
        identity=identity or self.selected
        row=next((m for m in self.document.project.mappings if m.id==identity),None)
        if row is None:raise ValueError('请先选择字幕。')
        return row

    def _rows(self):
        p=self._display_project(); index=self.media_controller.index
        pending=pending_text_ids(p)
        translations={t.id:t.text for t in p.translations};lyrics={l.id:l.text for l in p.lyrics};visuals={v.id:v.text for v in p.visuals}
        from lyricsync.speakers import speaker_name
        from .review_actions import row_metadata
        from lyricsync.styles import project_profiles
        profiles=project_profiles(p)
        result=[]
        for n,m in enumerate(p.mappings,1):
            first=index.first(m.start) if index and m.start is not None else 0
            last=index.last(m.end) if index and m.end is not None else 0
            if index is not None and valid_time(m.start, m.end):
                first, last = index.frame_span(m.start, m.end)
            result.append(dict(cueId=m.id,id=m.id,number=n,chinese=mapping_text(p,m,translations),
                               **row_metadata(p,m,mapping_text(p,m,translations)),
                               speakerName=speaker_name(p,m),styleName=profiles.get(m.style_id,{}).get('name','个人样式' if m.speaker_style else '默认样式'),styleId=m.style_id,styleOverrides=bool(m.style_overrides),
                               textRole=m.text_role,textRoleLabel=text_role_label(m.text_role),translationText='\n'.join(translations.get(i,'') for i in m.translation_ids),
                               source='\n'.join(lyrics.get(i,'') for i in m.lyric_ids if not i.startswith('ocr-source-')),
                               sourceFromOcr=bool(m.lyric_ids) and all(i.startswith('ocr-source-') for i in m.lyric_ids),
                               ocr='\n'.join(visuals.get(i,'') for i in m.visual_ids),
                               firstFrame=first,lastFrame=last,first=first,last=last,
                               confirmed=m.confirmed,locked=self._alignment_preview.active or m.id in p.settings.get('manual_locked_ids',[]),protected=m.locked,needsReview=not m.confirmed,pendingText=m.id in pending,untimed=m.start is None and m.end is None,
                               aiNote=self._pairing_note(p,m),reasons=list(m.reasons)))
        if self._candidate_source!='main':
            for row in result:row.update(self._candidate_evidence.get(row['id'],{}))
        import heapq
        active=[];overlap=set()
        spans=sorted((r['firstFrame'],r['lastFrame'],r['id']) for r in result if r['firstFrame'] and r['lastFrame']) if index else sorted((m.start,m.end,m.id) for m in p.mappings if valid_time(m.start,m.end))
        for first,last,identity in spans:
            while active and (active[0][0]<first if index else active[0][0]<=first+1e-6):heapq.heappop(active)
            if active:overlap.add(identity);overlap.add(active[0][1])
            heapq.heappush(active,(last,identity))
        for row in result:row['overlap']=row['id'] in overlap
        for number,row in enumerate(self._review_order(result),1):row['number']=number
        return result

    def _review_order(self,rows):
        """Timed rows sort by time; untimed lyrics follow their logical predecessor.

        A run without timing stays with its preceding timed lyric (or at the
        beginning), rather than acquiring unrelated numbers at the end.
        This is presentation only and never rewrites mapping/text/time data.
        """
        starts={m.id:m.start for m in self._display_project().mappings}
        index=self.media_controller.index
        leading=[];groups=[]
        for row in rows:
            start=starts.get(row['id'])
            if isinstance(start,(int,float)) and math.isfinite(start) and start>=0:
                groups.append((index.first(start) if index is not None else start,[row]))
            elif groups:groups[-1][1].append(row)
            else:leading.append(row)
        return leading+[row for _,group in sorted(groups,key=lambda group:group[0]) for row in group]

    def _refresh(self,rows=True):
        if rows:self._candidate_rebuild()
        alignment_state=self._alignment_state()
        if self._alignment_preview.active and not self._alignment_preview.valid(self.document,self.media_controller.index):
            self.selected=self._alignment_preview.clear();self._status='AI 建议已过期，已切回主时间轴，请重新获取。';rows=True
        p=self._display_project()
        if self._allocation and not self._allocation_valid():self._allocation={};self._allocation_context=None
        if rows:
            items=self._rows()
            from .review_actions import choices
            self._review_speakers = choices(p)
            from lyricsync.translation_pool import pairing_label
            self._pairing_label=pairing_label(p)
            self._text_roles={item['id']:item['textRole'] for item in items}
            if items!=self._all_rows:
                self.rows_revision+=1;self._rebuild_timeline_index(items)
            self._all_rows=items;self._row_positions={r['id']:n for n,r in enumerate(items)}
            self._confirmed_count=sum(m.confirmed for m in p.mappings)
            self._export_compatibility=(['字幕中含有半角大括号，在 ASS 格式下可能被识别为特效代码；请在播放器中检查显示效果，或改用 SRT。']
                                        if any('{' in row['chinese'] or '}' in row['chinese'] for row in items) else [])
            if any(row['textRole']!='translation' for row in items):
                self._export_compatibility.append('当前主时间轴包含未翻译的原文歌词，将按现有文本直接导出。')
            pending_count=sum(row['pendingText'] for row in items)
            if pending_count:self._export_compatibility.append(f'仍有 {pending_count} 句待分配歌词文字，导出前请检查词句对应。')
            duration=p.video.get('duration');self._omissions=[]
            for m,row in zip(p.mappings,items):
                if not valid_time(m.start,m.end) or (duration and m.end is not None and m.end>duration+1e-6):
                    self._omissions.append({'id':m.id,'number':row['number'],'chinese':row['chinese'],'reason':'未设置有效起止时间' if not valid_time(m.start,m.end) else '时间超出视频范围'})
            self.model.replace([r for r in self._review_order(items) if not self._unconfirmed_only or not r['confirmed']],self.document.id+':'+self._candidate_source);self._visible_ids={r['id'] for r in self.model.items};self._visible_positions={r['id']:n for n,r in enumerate(self.model.items)}
            self.timeline_model.replace(items,self.document.id+":"+self._candidate_source)
            self._rebuild_preview()
        else:items=self._all_rows
        self._multi_selected.intersection_update(self._row_positions)
        eligible=self.model.items if self._unconfirmed_only else items
        allow_hidden=self._timeline_selection or self.selected==self._confirmed_selection
        selection_ids=self._visible_ids if self._unconfirmed_only and not allow_hidden else self._row_positions
        if self._candidate_source=='main' and self.selected not in selection_ids:self.selected=eligible[0]['id'] if eligible else ''
        position=self._row_positions.get(self.selected,-1)
        visible_position=self._visible_positions.get(self.selected,-1)
        context_first=max(0,min(position-2,len(items)-5)) if position>=0 else 0
        timeline_context=items[context_first:context_first+5] if position>=0 else []
        ordered=sorted(items,key=lambda row:row['number']);display_position=next((n for n,row in enumerate(ordered) if row['id']==self.selected),-1)
        selection=dict(current=items[position] if position>=0 else {},
                       previous=ordered[display_position-1] if display_position>0 else {},
                       previousTimed=next((row for row in reversed(ordered[:max(0,display_position)]) if row['firstFrame']>0 and row['lastFrame']>=row['firstFrame']),{}),
                       next=ordered[display_position+1] if 0<=display_position<len(ordered)-1 else {},
                       previousVisible=self.model.items[visible_position-1] if visible_position>0 else {},
                       nextVisible=self.model.items[visible_position+1] if 0<=visible_position<len(self.model.items)-1 else {})
        settings={'motion':p.settings.get('motion',True),'subtitlePreview':p.settings.get('subtitle_preview',True),
                  'playerRounded':p.settings.get('player_rounded',True),'playerGlass':p.settings.get('player_glass',True),
                  'apiEnabled':p.settings.get('api_auto_review',False),'apiMaxCalls':p.settings.get('api_max_calls',50),
                  'apiRetries':p.settings.get('api_retries',3),'language':p.settings.get('language','jpn+eng'),
                  'ocrEngine':p.settings.get('engine','rapidocr')}
        from lyricsync.export import layout_entries,layout_anchor,placement_options
        try:
            vw,vh=p.video.get('width',1920) or 1920,p.video.get('height',1080) or 1080
            base=layout_anchor((vw,vh),p.settings.get('font_size'),**placement_options(p))
            settings['subtitleFallbackStyle']={'x':base['x']/vw,'y':base['y']/vh,'alignment':base['alignment'],'size':base['font_size']/vh,'font':p.settings.get('font','Microsoft YaHei')}
        except (ValueError,TypeError,ZeroDivisionError):settings['subtitleFallbackStyle']={}
        roi=p.settings.get('roi')
        width,height=p.video.get('width',0),p.video.get('height',0)
        normal_roi=None
        if self._valid_roi(roi,width,height):
            normal_roi=dict(x=roi[0]/width,y=roi[1]/height,width=(roi[2]-roi[0])/width,height=(roi[3]-roi[1])/height)
        from lyricsync.multi_roi import region_cards
        region_items = region_cards(p, width, height)
        normal_regions = [r for r in region_items if r['enabled']] if p.settings.get('ocr_multi_region') else []
        from .review_actions import selection_state
        from lyricsync.styles import state as review_styles
        from .force_retry_dialog import available as alignment_retry_available
        self._state=dict(alignmentRetryAvailable=alignment_retry_available(self),startupVisible=self._startup_visible,recentProjects=self._recent_snapshot,canResumeProject=bool(self._has_workspace or self.path or self.document.revision or self.document.project.inputs),audioAnalysis=self._audio_analysis,export={'omissions':self._omissions,'warnings':list(p.warnings),'warningsAcknowledged':bool(p.settings.get('parser_acknowledged',False)),
                                'compatibilityWarnings':self._export_compatibility},revision=self.document.revision,rowsRevision=self.rows_revision,
                         pendingTextAction=self._pending_text_action(),
                         alignmentPreview={**alignment_state,'active':self._alignment_preview.active},
                         manualTiming={'enabled':self._manual_axis_mode,'startFrame':self._manual_start_frame or 0},textAllocation=deepcopy(self._allocation),
                         multiSelectedIds=sorted(self._multi_selected),reviewSpeakers=self._review_speakers,speakerSelection=selection_state(self,items,position),reviewStyles=review_styles(p),
                         project=dict(id=self.document.id,name=Path(self.path).stem if self.path else '未命名工程',path=self.path,
                                      dirty=self.document.revision!=self.document.saved_revision,
                                      unexported=self.document.revision!=self.document.exported_revision,
                                      autosaveStatus=self._autosave_status),
                         view={'unconfirmedOnly':self._unconfirmed_only},workspaceLayout=dict(self._workspace_layout),visibleCount=len(self.model.items),confirmedCount=self._confirmed_count,rowCount=len(items),rows=items,selectedVisibleIndex=visible_position,selectedId=self.selected,selection=selection,
                         selectedTimelineIndex=position,timelineContext=timeline_context,timelineContextFirstIndex=context_first,timelineBounds=self._timeline_bounds,
                         markers=[dict(id='marker-'+str(f),frame=f) for f in p.settings.get('frame_markers',[])],
                         history=dict(canUndo=bool(self.history.undo_stack) and self._candidate_source=='main' and not self._alignment_preview.active,canRedo=bool(self.history.redo_stack) and self._candidate_source=='main' and not self._alignment_preview.active),
                         hasSourceEvidence=bool(p.lyrics or p.visuals),
                         task=dict(self._task),settings=settings,stage=self.stage,entryMode=self._entry_mode,pairingLabel=self._pairing_label,roi=normal_roi,ocrMultiRegion=p.settings.get('ocr_multi_region') is True,ocrRegionsConfigured=bool(p.settings.get('ocr_regions')),multiRois=normal_regions,regionCards=region_items,inputs={**p.inputs,'videoPath':p.inputs.get('video',''),'translationPath':p.inputs.get('translation',''),'sourcePath':p.inputs.get('ktv','')},api=deepcopy(self.api_state),
                         # Completion marks reflect actual work, not the page the
                         # user visited or manually created rows. A cancelled or
                         # empty OCR run is not presented as successful evidence.
                         stageCompletion={'import':bool(self.media_controller.index),
                                          'ocr':bool(p.stage in ('review','ready') and p.visuals and p.video.get('ocr_metrics')),
                                          'review':bool(items and self._confirmed_count==len(items))},
                         qc={k:v for k,v in self._qc.items() if k not in ('report','lastRescan')},alignment=deepcopy(self._alignment),closeDialog=deepcopy(self._close_dialog),
                         diagnostics={'persistent':self.diagnostics.persistent,'temporary':self.diagnostics.fallback},
                         error=self._error_text,status=self._status,
                         capabilities={'editor.start':True,'separation.open':True,'vocal.start':True,'vocal.configure':True,'ocr':self._ocr_available,'ocr.start':self._ocr_available,'row.merge':True,'row.repeat':True,'row.fillGap':True,'row.rescan':True,'row.pairing':True,'project.rematch':True,'api.review':True,'api.reviewSaved':True,'settings.ocr':True,'settings.subtitleStyle':True,'history.show':True,'log.show':True,'diagnostics.export':True,'qc':True,'qc.open':True,'qc.start':True,
                                       'ktv.convert':True,'row.restoreDetected':True,'project.reviewWarnings':True,
                                       'row.referenceTranslation':True,'row.align':True,'alignment.batch':True,
                                       'apiSettings':True,'api.configure':True,'qc.roi':True,'qc.api':True,'qc.inspect':True,'qc.review':True,'qc.rescan':True,'qc.report':True,'project.recover':True,'project.relocate':True})
        from lyricsync.styles import state as style_state
        self._state['styles']=style_state(p)
        self._state.update(self._candidate_state())
        self.stateChanged.emit()

    def _rebuild_preview(self):
        p=self._display_project();events={};lookup={t.id:t.text for t in p.translations}
        for m in p.mappings:
            if valid_time(m.start,m.end) and not m.ignored:
                text=mapping_text(p,m,lookup)
                events.setdefault(m.start,[]).append((m.id,text))
                events.setdefault(m.end,[]).append((m.id,None))
        active={};self._preview_stamps=[];self._preview_texts=[];self._active_ids=[]
        for stamp,changes in sorted(events.items()):
            for identity,text in sorted(changes,key=lambda pair:pair[1] is not None):
                if text is None:active.pop(identity,None)
                else:active[identity]=text
            self._preview_stamps.append(stamp);self._preview_texts.append('\n'.join(active.values()));self._active_ids.append(list(active))
        self.layout_timer.start();self.mediaChanged.emit()

    def _begin_layout(self):
        if self._layout_running:self._layout_pending=True;return
        self._layout_running=True;self._layout_pending=False
        snapshot=Project.from_dict(self._display_project().to_dict());revision=self.document.revision;generation=(self._alignment_preview.generation,self._candidate_generation)
        def work(cancel,progress):
            from lyricsync.export import layout_entries,layout_anchor,placement_options
            from lyricsync.export import project_layout_rows
            width=snapshot.video.get('width',1920) or 1920;height=snapshot.video.get('height',1080) or 1080
            laid=project_layout_rows(snapshot)
            events={}
            for n,row in enumerate(laid):
                item={'text':row['text'],'xNormalized':row['x']/width,'yNormalized':row['y']/height,'alignment':row['alignment'],'fontSizeNormalized':row['font_size']/height,'fontFamily':row['style']['font'],'color':row['style']['color'],'bold':row['style']['bold'],'italic':row['style']['italic'],'speakerName':row['speaker_name']}
                events.setdefault(row['start'],[]).append((n,item));events.setdefault(row['end'],[]).append((n,None))
            active={};stamps=[];rows=[]
            for stamp,changes in sorted(events.items()):
                for identity,item in sorted(changes,key=lambda pair:pair[1] is not None):
                    if item is None:active.pop(identity,None)
                    else:active[identity]=item
                stamps.append(stamp);rows.append(list(active.values()))
            warning='部分字幕可能超出画面安全区，可按需调整字号或位置（不影响正常导出）。' if any(r.get('overflow') for r in laid) else ''
            return stamps,rows,warning
        def completed(result,error):
            self._layout_running=False
            if revision==self.document.revision and generation==(self._alignment_preview.generation,self._candidate_generation):
                if error:self._layout_error=str(error);self._layout_stamps=[];self._layout_rows=[]
                else:self._layout_stamps,self._layout_rows,self._layout_error=result;self._layout_revision=revision
                self.mediaChanged.emit()
            if self._layout_pending or revision!=self.document.revision or generation!=(self._alignment_preview.generation,self._candidate_generation):self.layout_timer.start()
        self._start_job('subtitle-layout',work,completed,foreground=False,asset_dependent=False)

    def _adopt_project(self, project, label=None):
        """One validated owner for whole-project edits and history restoration.

        Validate before recording history, and always bind the editor to the
        checked document object, never to an unchecked worker/draft object.
        """
        previous=self.document.project
        if not self.document.replace_project(project,touch=False):return False
        if label:self.history.push(previous,label)
        self.editor.project=self.document.project
        return True

    def _changed(self):
        self._entry_observe_main()
        self.document.touch();self._error_text='';self._autosave_status='等待自动备份'
        self.autosave_timer.start();self._refresh()

    def _error(self,message):
        self.diagnostics.record('ui-error')
        self._log.append('错误：'+str(message));self._log=self._log[-200:]
        self._error_text=str(message);self._status=str(message);self._refresh(False);self.message.emit(str(message))

    def _message(self,message):
        self._log.append(str(message));self._log=self._log[-200:]
        self._status=str(message);self._error_text='';self._refresh(False)

    def _path(self,payload,save=False,title='选择文件',filter='所有文件 (*)',suggested='',category=None):
        raw=payload.get('path')
        if raw:
            raw=str(raw)
            if raw.startswith('file:'):raw=QUrl(raw).toLocalFile()
            return str(Path(raw).expanduser().resolve())
        category=category or ('save' if save else 'open')
        initial=self._file_dialog_history.initial(category,suggested if save else '')
        if save:
            value,_=QFileDialog.getSaveFileName(None,title,initial,filter)
        else:value,_=QFileDialog.getOpenFileName(None,title,initial,filter)
        if value:self._file_dialog_history.remember(category,value)
        # Native file dialogs return forward slashes on Windows. Use the same
        # representation as explicit paths and MediaController.open().
        return str(Path(value).expanduser().resolve()) if value else ''

    @Slot(str,'QVariantMap',result=bool)
    def command(self,name,payload):
        if self._shutdown:
            if name=='close.resolve' and isinstance(payload,dict) and self._shutdown_failure_waiting:
                return self._resolve_shutdown_failure(payload.get('choice'))
            return False
        if not isinstance(payload,dict):
            self._error('操作参数格式无效。');return False
        creation_names={'row.add','row.repeat','row.split','timeline.createRange','timeline.createAtPlayhead','timeline.manualMark','history.redo'}
        creation_before={m.id for m in self.document.project.mappings} if name in creation_names else None
        try:
            # Temporarily hidden product features must not run via stale UI state.
            if name in ('row.align','row.referenceTranslation','api.review','alignment.batch','alignment.forceRetry'):
                raise ValueError('AI 核对暂时停用；已有配置和历史记录仍保留。')
            self._candidate_guard(name)
            if name in ('roi.multi', 'roi.set', 'roi.auto') and self.stage == 'review':
                raise ValueError('请返回导入 / 识别页调整识别选区。')
            if self._alignment_preview.active and (name.startswith(('row.','style.','timeline.','marker.','export.','history.','input.','textAllocation.')) or name in ('ocr.start','project.rematch','subtitle.move','settings.subtitleStyle','roi.set','roi.auto','roi.multi')) and name not in ('row.select','timeline.select','history.show'):
                raise ValueError('当前正在预览 AI 建议，请先应用或切回主时间轴后再编辑、导出。')
            if name in ('timeline.move','timeline.razor','row.edit','row.setBoundary','row.setCurrent','row.split','row.merge','row.restoreDetected','row.fillGap','row.rescan','row.adoptRescan','row.pairing'):
                identities=payload.get('ids') or [payload.get('id',self.selected)]
                if any(i in self.document.project.settings.get('manual_locked_ids',[]) for i in identities):raise ValueError('当前句已锁定，请先解锁再修改。')
            if name not in ('timeline.move','row.edit','row.setBoundary','editor.activity','history.begin','history.end') and not name.startswith('media.'):
                self.history.end()
            if name in ('media.seek','media.scrubBegin','media.step','media.play','media.cueFirst','media.cueLast'):
                self._hold_cue_selection=False
            if name=='editor.activity':
                active=self._boolean(payload,'active',False);composing=self._boolean(payload,'composing',False);identity=payload.get('id',self.selected)
                if active or composing:
                    if identity!=self._editor_id or not self._editor_active:self.history.end();self.history.begin('编辑字幕' if ':' not in identity else '编辑时间')
                    if composing and not self._editor_composing:
                        self.history.end();self.history.begin('输入文字');self._ime_commit_pending=False
                    elif self._editor_composing and not composing:self._ime_commit_pending=True
                    self._editor_id=identity;self._editor_active=active;self._editor_composing=composing
                elif not self._editor_id or identity==self._editor_id:
                    self._editor_active=False;self._editor_composing=False;self._editor_id='';self._ime_commit_pending=False;self.history.end()
            elif name=='view.filter':
                self._unconfirmed_only=self._boolean(payload,'unconfirmedOnly',False);self._refresh()
            elif name=='view.layout':self._set_workspace_layout(payload)
            elif name=='error.clear':self._error_text='';self._refresh(False)
            elif name=='stage.set':
                if payload.get('stage') not in ('import','ocr','review','export','qc'):raise ValueError('无效的流程页面。')
                self.stage=payload['stage'];self._refresh(False)
            elif name=='editor.start':return self._start_editor(payload)
            elif name=='editor.continue':return self._continue_editor(payload)
            elif name=='project.newEntry':return self._new_entry(payload)
            elif name=='software.update':
                from .software_update_dialog import SoftwareUpdateDialog
                SoftwareUpdateDialog().exec()
            elif name in ('review.assignSpeaker','review.assignStyle','review.select','review.speakerShortcut','review.selectAll','review.clearSelection','review.selectSameSpeaker'):
                from . import review_actions
                if name=='review.select':review_actions.select(self,payload)
                elif name=='review.selectSameSpeaker':review_actions.select_same(self,payload.get('id') or self.selected)
                elif name=='review.speakerShortcut':review_actions.shortcut(self,payload)
                elif name in ('review.selectAll','review.clearSelection'):review_actions.selection_scope(self,name=='review.clearSelection')
                else:review_actions.assign(self,'speaker' if name=='review.assignSpeaker' else 'style',payload)
            elif name=='row.speaker':
                from .speaker_dialog import show_speaker_dialog
                show_speaker_dialog(self)
            elif name=='vocal.configure':self._configure_vocal(setup_only=True)
            elif name=='vocal.start':self._configure_vocal(setup_only=False)
            elif name=='separation.open':self._open_separation()
            elif name=='settings.set':self._settings(payload)
            elif name=='roi.multi':
                from .roi_controls import change
                if not change(self, payload):return False
            elif name=='roi.set':self._roi(payload)
            elif name=='subtitle.move':self._move_subtitle(payload)
            elif name=='roi.auto':self._auto_roi()
            elif name=='timeline.select':self._select_timeline(payload.get('id'))
            elif name=='timeline.move':
                if self.editor.move_frames(self._row(payload.get('id')).id,self._integer(payload,'delta'),self.media_controller.index):self._hold_playing_interval()
            elif name=='timeline.razor':self._razor(payload)
            elif name=='textAllocation.begin':self._begin_allocation(payload)
            elif name=='textAllocation.cancel':self._allocation={};self._allocation_context=None;self._refresh(False)
            elif name=='textAllocation.apply':self._apply_allocation(payload)
            elif name=='textAllocation.markDone':self._mark_allocation(payload)
            elif name=='textAllocation.keep':self._keep_allocation(payload)
            elif name=='timeline.manualMode':self._manual_mode(payload)
            elif name=='timeline.manualMark':self._manual_mark()
            elif name=='timeline.manualCancel':self._manual_cancel()
            elif name=='timeline.copyManual':self._copy_manual_timeline()
            elif name=='timeline.createRange':self._manual_create_range(self._integer(payload,'first'),self._integer(payload,'last'))
            elif name=='timeline.createAtPlayhead':self._manual_create_at_playhead()
            elif name=='timeline.assignRange':self._assign_untimed_range(payload.get('id'),self._integer(payload,'first'),self._integer(payload,'last'))
            elif name=='timeline.assignAtPlayhead':self._assign_untimed_at_playhead(payload.get('id'))
            elif name=='media.open':self._open_video(payload)
            elif name=='media.seekSeconds':self.media_controller.seek_seconds(payload.get('seconds'))
            elif name=='project.pureEditing':self._pure_editing(payload)
            elif name=='media.seek':self.media_controller.seek(self._integer(payload,'frame'))
            elif name=='media.scrubBegin':self.media_controller.scrub(self._integer(payload,'frame'),begin=True)
            elif name=='media.scrub':self.media_controller.scrub(self._integer(payload,'frame'))
            elif name=='media.scrubEnd':self.media_controller.seek(self._integer(payload,'frame'))
            elif name=='media.step':self.media_controller.step(self._integer(payload,'delta'))
            elif name=='media.play':
                self.media_controller.pause() if (self.media_controller.snapshot()['playing'] or self.media_controller.snapshot()['playPending']) else self.media_controller.play()
            elif name=='media.loopCue':
                if self.media_controller.snapshot()['loopRange']:self.media_controller.pause()
                else:self._cue_media(name)
            elif name=='media.pause':self.media_controller.pause()
            elif name=='media.mute':self.media_controller.mute(self._boolean(payload,'value',True))
            elif name in ('media.cueFirst','media.cueLast','media.playCue'):self._cue_media(name)
            elif name=='row.select':self._select_row(payload.get('id'))
            elif name=='row.multiToggle':self._multi_toggle(payload)
            elif name=='row.multiClear':self._multi_selected.clear();self._refresh(False)
            elif name=='row.edit':
                if self._editor_active and not self.history.group:self.history.begin('编辑字幕')
                if self.editor.set_text(payload.get('id',self.selected),payload.get('text')):self._hold_playing_interval()
                if self._ime_commit_pending:self.history.end();self._ime_commit_pending=False
            elif name in ('row.setBoundary','row.setCurrent'):self._boundary(payload,current=name=='row.setCurrent')
            elif name=='row.confirmNext':self._confirm_next(payload)
            elif name=='row.confirm':self._confirm_row(payload)
            elif name=='row.lock':
                row=self._row(payload.get('id'));value=self._boolean(payload,'value',True)
                locked=set(self.document.project.settings.get('manual_locked_ids',[]))
                if (row.id in locked)!=value:
                    self.history.push(self.document.project,'锁定/解锁字幕')
                    if value:locked.add(row.id)
                    else:locked.discard(row.id)
                    self.document.project.settings['manual_locked_ids']=sorted(locked);self._changed()
            elif name=='row.add':self._add(payload)
            elif name=='row.delete':self._delete_row(payload)
            elif name=='row.deleteSelected':self._delete_selected(payload)
            elif name=='row.split':self._split(payload)
            elif name=='row.merge':self._merge(payload)
            elif name=='row.repeat':self._repeat(payload)
            elif name=='row.restoreDetected':self._restore_detected(payload)
            elif name=='row.fillGap':self.editor.fill_gap(payload.get('id',self.selected))
            elif name=='row.rescan':self._rescan(payload)
            elif name=='row.adoptRescan':
                from lyricsync.rescan import adopt_rescan
                adopt_rescan(self.editor,payload.get('id',self.selected),payload.get('visualIds',[]))
            elif name=='pairing.workbench':
                from .pairing_workbench import pairing_dialog
                pairing_dialog(self).exec()
            elif name=='row.pairing':self._pairing(payload)
            elif name=='project.rematch':self._rematch()
            elif name=='settings.ocr':self._ocr_settings()
            elif name=='settings.subtitleStyle':
                from .dialogs import subtitle_style
                subtitle_style(self)
            elif name=='style.manager':
                from .style_dialog import show_style_manager
                show_style_manager(self,batch=payload.get('batch',False))
            elif name=='style.editCurrent':
                from .style_dialog import edit_current_style
                edit_current_style(self, region_id=payload.get('regionId'))
            elif name=='style.select':
                from lyricsync.styles import select_style
                select_style(self.document.project,self.history,payload.get('id'))
                self._changed()
            elif name=='style.apply':
                from lyricsync.styles import apply_style
                ids=[row.id for row in self.document.project.mappings if row.id in self._multi_selected]
                if not ids and self.selected:ids=[self.selected]
                apply_style(self.document.project,self.history,ids,payload.get('id'))
                self._changed()
            elif name in ('history.show','log.show'):
                from .dialogs import show_text
                show_text('操作历史' if name=='history.show' else '运行日志','\n'.join(label for label,_ in self.history.undo_stack) if name=='history.show' else '\n'.join(self._log))
            elif name=='diagnostics.export':
                path=self._path(payload,True,'导出诊断信息','诊断 JSON (*.json)','Lyria-diagnostics.json')
                if not path:return False
                try:self.diagnostics.export(path)
                except Exception as exc:
                    self.diagnostics.record('export-failed',exc)
                    self._error('诊断文件保存失败。请选择可写入的文件夹后重试。');return False
                self._message('诊断信息已导出保存。')
            elif name=='marker.add':self.editor.add_marker(self._integer(payload,'frame',self.media_controller.snapshot()['displayedFrame']),self.media_controller.index)
            elif name=='marker.remove':self.editor.delete_marker(self._integer(payload,'frame'))
            elif name=='marker.clear':
                if self.document.project.settings.get('frame_markers'):
                    self.history.push(self.document.project,'清空标记点');self.document.project.settings['frame_markers']=[];self._changed()
            elif name in ('history.undo','history.redo'):self._history(name.endswith('redo'))
            elif name=='history.begin':self.history.begin(str(payload.get('label','连续编辑')))
            elif name=='history.end':self.history.end()
            elif name=='project.home':
                self._startup_visible=True;self._recent_snapshot=self._recent_projects.snapshot();self._refresh(False)
            elif name=='project.resume':self._startup_visible=False;self._has_workspace=True;self._refresh(False)
            elif name=='project.recentForget':
                self._recent_projects.forget(payload.get('path',''));self._recent_snapshot=self._recent_projects.snapshot();self._refresh(False)
            elif name=='project.newFromReview':self._new_from_review()
            elif name=='project.new':self._replace_guard(lambda:self._new())
            elif name=='project.open':self._open_project(payload)
            elif name=='project.relocate':self._relocate(payload)
            elif name=='project.reviewWarnings':self._review_warnings()
            elif name in ('project.save','project.saveAs'):
                path=self.path if name=='project.save' else ''
                path=payload.get('path') or path or self._path(payload,True,'保存工程','Lyria 工程 (*.lyria)', '未命名工程.lyria')
                if path:self._save(True,str(path))
                else:return False
            elif name in ('project.restore','project.recover'):self._restore_recovery(payload)
            elif name=='input.import':self._import_text(payload)
            elif name=='input.clear':self._clear_input(payload)
            elif name=='ocr.start':self._ocr_settings(start_after=True)
            elif name=='task.cancel':
                self.jobs.cancel(self._task.get('id',''));self._task['cancelling']=True
                self._task['label']=('正在取消请求，已完成的结果会保留…' if self._task.get('api') else '正在取消任务，已完成的结果会保留…');self._refresh(False)
            elif name=='export.ass':self._export(payload)
            elif name=='ktv.convert':self._convert(payload)
            elif name=='qc.open':self._open_qc(payload)
            elif name=='qc.roi':self._prepare_qc_regions(payload)
            elif name=='api.configure':
                from .dialogs import ApiSettingsDialog
                ApiSettingsDialog(self,payload.get('preset')).exec()
            elif name in ('api.models','api.test'):self._api_simple(name,payload)
            elif name=='alignment.batch':self._api_alignment(payload)
            elif name=='alignment.forceRetry':
                from .force_retry_dialog import request
                request(self)
            elif name=='api.reviewSaved':
                from .api_review_dialogs import show_saved_reviews
                show_saved_reviews(self,payload)
            elif name=='row.align':self._api_review({**payload,'id':self._row(payload.get('id')).id})
            elif name=='api.review':self._api_review(payload)
            elif name=='row.referenceTranslation':self._api_reference(payload)
            elif name=='alignment.preview':self._set_alignment_preview(self._boolean(payload,'enabled',False))
            elif name=='alignment.show':
                from .api_review_dialogs import show_alignment_plan
                if not self._alignment.get('plan'):raise ValueError('暂无可查看的 AI 拆合建议。')
                show_alignment_plan(self,self._alignment['plan'])
            elif name=='alignment.accept':self._accept_alignment()
            elif name=='alignment.reject':self._discard_alignment()
            elif name=='api.resume':self._resume_api()
            elif name=='qc.start':self._run_qc(payload)
            elif name=='qc.jump':self._qc_jump(payload)
            elif name=='qc.inspect':self._qc_inspect(payload)
            elif name=='qc.review':self._qc_review(payload)
            elif name=='qc.rescan':self._qc_rescan(payload)
            elif name in ('qc.report','qc.openRescan'):self._qc_open_report(payload)
            elif name=='close.resolve':self._resolve_close(payload.get('choice'))
            else:raise ValueError('暂不支持此操作：'+str(name))
            if creation_before is not None:
                created={m.id for m in self.document.project.mappings}-creation_before
                if name=='history.redo' and len(created)==1 and self.selected not in created:
                    self._hold_selection(next(iter(created)));self._refresh(False)
                if self.selected in created:self.cueCreated.emit(self.selected)
            return True
        except Exception as exc:
            from .diagnostics import diagnostic_stage
            self.diagnostics.record('command-failed',exc,stage=diagnostic_stage(name))
            self._error(str(exc) or type(exc).__name__);return False

    @staticmethod
    def _integer(payload,key,default=None):
        value=payload.get(key,default)
        if isinstance(value,float) and math.isfinite(value) and value.is_integer():value=int(value)
        if type(value) is not int:raise ValueError(key+' 必须是整数。')
        return value

    @staticmethod
    def _boolean(payload,key,default=False):
        value=payload.get(key,default)
        if type(value) is not bool:raise ValueError(key+' 必须是真/假值。')
        return value

    @Slot(str,int,result=int)
    def graphemeBoundary(self,text,position):
        length=len(text.encode('utf-16-le'))//2
        position=max(0,min(length,position))
        finder=QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Grapheme,text)
        finder.setPosition(position)
        if finder.isAtBoundary():return position
        left=finder.toPreviousBoundary();finder.setPosition(position);right=finder.toNextBoundary()
        return left if position-left<=right-position else right

    def _split(self,payload):
        if self._editor_composing:raise ValueError('请先完成当前输入法选词，再切分字幕。')
        row=self._row(payload.get('id'));text=mapping_text(self.document.project,row)
        offset=self._integer(payload,'offsetUtf16');frame=self._integer(payload,'frame')
        if self.graphemeBoundary(text,offset)!=offset:raise ValueError('请将光标放在两个完整字符之间再切分。')
        try:character=len(text.encode('utf-16-le')[:offset*2].decode('utf-16-le'))
        except UnicodeDecodeError:raise ValueError('切分位置不能落在单个字符或表情符号中间。')
        if not 0<offset<len(text.encode('utf-16-le'))//2:raise ValueError('请将光标放在字幕文字中间（不能在句首或句尾）。')
        index=self.media_controller.index
        if index is None:raise ValueError('视频帧索引正在加载，请稍候。')
        if valid_time(row.start,row.end):
            first,last=index.frame_span(row.start,row.end)
            if first>=last:raise ValueError('当前句时长不足 2 帧，无法继续切分。')
            if not first<frame<=last:raise ValueError(f'标记点 F{frame} 超出可切分范围（F{first + 1}–F{last}），句首 F{first} 处无法切分。')
        # Select the relevant new half before notifying QML. Otherwise the old
        # ID disappears, _refresh falls back to the first cue and the timeline jumps.
        was_selected=self.selected==row.id
        displayed=self.media_controller.snapshot()['displayedFrame']
        def keep_selection(parts):
            if was_selected:
                self._hold_selection(parts[1].id if displayed and displayed>=frame else parts[0].id)
        self.editor.split_at_marker(row.id,character,frame,index,before_notify=keep_selection)

    def _razor(self,payload):
        if self._editor_composing:raise ValueError('请先完成当前输入法选词，再切分字幕。')
        row=self._row(payload.get('id'));frame=self._integer(payload,'frame')
        def keep_selection(parts):
            # Keep the clicked clip's left half selected, without touching the
            # player. C remains active for a continuous series of time cuts.
            self._hold_selection(parts[0].id);self._timeline_selection=True
            self.razorSplitCreated.emit(parts[0].id,parts[1].id)
        self.editor.razor_split(row.id,frame,self.media_controller.index,before_notify=keep_selection)

    def _split_character(self,text,offset):
        if type(offset) is not int or not 0<offset<len(text.encode('utf-16-le'))//2:
            raise ValueError('请在字幕文字中间选择拆分位置。')
        if self.graphemeBoundary(text,offset)!=offset:raise ValueError('拆分位置必须在两个完整字符之间。')
        try:character=len(text.encode('utf-16-le')[:offset*2].decode('utf-16-le'))
        except UnicodeDecodeError:raise ValueError('拆分位置不能落在单个字符或表情符号中间。')
        from lyricsync.graphemes import validate_character_gap
        validate_character_gap(text,character)
        if not text[:character].strip() or not text[character:].strip():raise ValueError('拆分后前后两段都不能为空。')
        return character

    @staticmethod
    def _allocation_display(text):
        """Map QTextDocument's normalized UTF-16 positions to literal source.

        CRLF is one displayed paragraph but two source code units. Other Qt
        paragraph/line separators keep one unit. Surrogate interiors are left
        unmapped so the same strict grapheme validator handles every route.
        """
        display=[];offsets=[0];source=0;position=0
        while position<len(text):
            char=text[position]
            if char=='\r' and text[position:position+2]=='\r\n':
                display.append('\n');source+=2;offsets.append(source);position+=2;continue
            display.append('\n' if char in '\r\u2028\u2029' else char)
            units=len(char.encode('utf-16-le'))//2
            if units==2:offsets.append(None)
            source+=units;offsets.append(source);position+=1
        return ''.join(display),offsets

    def _allocation_character(self,display_offset):
        if not self._allocation_valid():raise ValueError('字幕内容已变动，请重新打开文字分配。')
        offsets=self._allocation_offsets
        if type(display_offset) is not int or not 0<=display_offset<len(offsets) or offsets[display_offset] is None:
            raise ValueError('拆分位置必须在两个完整字符之间。')
        return self._split_character(self._allocation['text'],offsets[display_offset])

    @Slot(int,result='QVariantMap')
    def allocationPreview(self,offset):
        try:
            character=self._allocation_character(offset);text=self._allocation['text']
            return dict(valid=True,left=text[:character],right=text[character:],error='')
        except ValueError as exc:return dict(valid=False,left='',right='',error=str(exc))

    def _allocation_valid(self):
        if not self._allocation_context:return False
        project,revision,epoch,index,selected,rows=self._allocation_context
        return (not self._shutdown and self.document.project is project and self.document.revision==revision
                and self.epoch==epoch and self.media_controller.index is index and self.selected==selected
                and [asdict(m) for m in project.mappings if m.id in (self._allocation.get('leftId'),self._allocation.get('rightId'))]==rows)

    def _pending_text_link(self,identity):
        p=self.document.project
        link=pending_pair(p,identity)
        seen=set()
        while link and link['right']:
            a=self._row(link['left'])
            if mapping_text(p,a).strip() or a.id in seen:break
            seen.add(a.id);incoming=pending_pair(p,a.id)
            if not incoming or incoming['right']!=a.id:break
            link=incoming
        return link

    def _pending_text_action(self):
        # Playback refreshes only view state. Do not traverse mappings on every
        # presented frame, even while a pending pair remains selected.
        position=self._row_positions.get(self.selected,-1)
        if self._alignment_preview.active or position<0 or not self._all_rows[position]['pendingText']:return {}
        key=(self.document.id,id(self.document.project),self.document.revision,self.rows_revision,self.selected)
        cached=getattr(self,'_pending_action_cache',None)
        if cached and cached[0]==key:return deepcopy(cached[1])
        action=self._build_pending_text_action()
        self._pending_action_cache=(key,deepcopy(action))
        return action

    def _build_pending_text_action(self):
        link=self._pending_text_link(self.selected)
        if not link:return {}
        rows={m.id:m for m in self.document.project.mappings}
        a,b=rows.get(link['left']),rows.get(link['right'])
        if a is None:return {}
        numbers={r['id']:r['number'] for r in self._all_rows}
        error=''
        try:validate_pending_pair(self.document.project,link['left'],link['right'],require_timing=False)
        except ValueError as exc:error=str(exc)
        can_allocate=bool(b and mapping_text(self.document.project,a).strip())
        try:validate_pending_pair(self.document.project,link['left'],link['right'])
        except ValueError:can_allocate=False
        return dict(leftId=a.id,rightId=b.id if b else '',leftNumber=numbers.get(a.id,0),rightNumber=numbers.get(b.id,0) if b else 0,
                    leftText=mapping_text(self.document.project,a),rightText=mapping_text(self.document.project,b) if b else '',
                    projectId=self.document.id,revision=self.document.revision,selectedId=self.selected,canKeep=not error,canAllocate=can_allocate,error=error)

    def _check_pending_action(self,payload):
        if payload.get('projectId')!=self.document.id or payload.get('revision')!=self.document.revision or payload.get('selectedId')!=self.selected:
            raise ValueError('字幕状态已更新，请重新选择后再操作。')
        if not isinstance(payload.get('leftId'),str) or not isinstance(payload.get('rightId'),str):
            raise ValueError('请选择待处理的相邻字幕片段。')

    def _keep_allocation(self,payload):
        if self._editor_active or self._editor_composing:raise ValueError('请先完成文字或时间编辑。')
        self._check_pending_action(payload)
        self.editor.keep_pending_text(payload['leftId'],payload['rightId'])

    def _begin_allocation(self,payload):
        if self._editor_composing:raise ValueError('请先完成当前输入法选词。')
        if 'leftId' in payload:
            self._check_pending_action(payload);link={'left':payload['leftId'],'right':payload['rightId']}
        else:link=self._pending_text_link(self._row(payload.get('id')).id)
        if not link:raise ValueError('此句没有待分配文字。')
        a,b=validate_pending_pair(self.document.project,link['left'],link['right'])
        # A later blank piece can be selected after several consecutive cuts.
        # Start at its first unfinished incoming pair that owns text; the user
        # still chooses every character boundary explicitly.
        seen=set()
        while b is not None and not mapping_text(self.document.project,a).strip() and a.id not in seen:
            seen.add(a.id)
            incoming=pending_pair(self.document.project,a.id)
            if not incoming or incoming['right']!=a.id:break
            a,b=validate_pending_pair(self.document.project,incoming['left'],incoming['right'])
        if b is not None and not mapping_text(self.document.project,a).strip():raise ValueError('前一段还没有文字，请先填写内容或从更早的有字片段向后分配。')
        text=mapping_text(self.document.project,a)+(mapping_text(self.document.project,b) if b else '')
        display,self._allocation_offsets=self._allocation_display(text)
        index=self.media_controller.index
        numbers={row['id']:row['number'] for row in self._all_rows}
        self._allocation=dict(leftId=a.id,rightId=b.id if b else '',text=text,displayText=display,
                              leftNumber=numbers[a.id],rightNumber=numbers[b.id] if b else 0)
        self._allocation_context=(self.document.project,self.document.revision,self.epoch,index,self.selected,[asdict(m) for m in (a,b) if m])
        self._refresh(False)

    def _apply_allocation(self,payload):
        if self._editor_composing or not self._allocation_valid():raise ValueError('字幕内容已变动，请重新打开文字分配。')
        current=self._allocation
        if payload.get('leftId')!=current['leftId'] or payload.get('rightId')!=current['rightId']:raise ValueError('待分配的字幕片段已变动，请重试。')
        character=self._allocation_character(self._integer(payload,'offsetUtf16'))
        self.editor.allocate_text(current['leftId'],current['rightId'],character)

    def _mark_allocation(self,payload):
        link=pending_pair(self.document.project,self._row(payload.get('id')).id)
        if not link:raise ValueError('此句没有待分配文字。')
        self.editor.allocate_text(link['left'],link['right'],mark_done=True)

    def _boundary(self,payload,current=False):
        row=self._row(payload.get('id'));index=self.media_controller.index
        if index is None:raise ValueError('视频帧索引正在加载，请稍候。')
        edge=payload.get('edge')
        if edge not in ('first','last'):raise ValueError('请选择入点或出点。')
        frame=self.media_controller.snapshot()['displayedFrame'] if current else self._integer(payload,'frame')
        if not frame:raise ValueError('视频画面尚未加载完成，请稍候。')
        start,end=index.interval(frame,frame)
        if self.editor.set_time(row.id,**({'start':start} if edge=='first' else {'end':end})):self._hold_playing_interval()

    def _cue_media(self,name):
        row=self._display_row();index=self.media_controller.index
        if index is None or not valid_time(row.start,row.end):raise ValueError('当前句尚未设置完整的起止时间。')
        first,last=index.frame_span(row.start,row.end)
        if name=='media.loopCue':
            self._hold_cue_selection=True;self._held_playback_ids=None;self.media_controller.play_range(first,last,loop=True)
        elif name=='media.playCue':self._hold_cue_selection=True;self._held_playback_ids=None;self.media_controller.play_range(max(1,first-5),min(len(index.stamps),last+5))
        else:self.media_controller.seek(first if name=='media.cueFirst' else last)

    def _multi_toggle(self,payload):
        row=self._row(payload.get('id'));selected=self._boolean(payload,'selected',False)
        if selected:self._multi_selected.add(row.id)
        else:self._multi_selected.discard(row.id)
        self._refresh(False)

    def _hold_selection(self,identity):
        self.selected=identity;self._hold_playing_interval();self._confirmed_selection=''

    def _hold_playing_interval(self):
        """Keep an explicit edit/selection stable, then resume ongoing playback.

        Paused edits retain selection. While playing, hold only until the next
        actual subtitle boundary, rather than requiring pause/play to release.
        """
        self._hold_cue_selection=True;self._held_playback_ids=None
        media=self.media_controller.snapshot()
        if media['playing']:
            pos=bisect_right(self._preview_stamps,media['positionSeconds']+1e-6)-1
            self._held_playback_ids=tuple(self._active_ids[pos] if 0<=pos<len(self._active_ids) else [])

    def _set_workspace_layout(self,payload):
        from lyricsync.storage import atomic_json
        from .workspace_layout import update_layout
        updated=update_layout(self._workspace_layout,payload)
        if updated!=self._workspace_layout:
            atomic_json(self.storage_dir/'workspace-layout.json',updated)
            self._workspace_layout=updated;self._refresh(False)

    def _select_timeline(self,identity):
        row=self._display_row(identity);self._hold_selection(row.id);self._timeline_selection=True
        self._refresh(False);self.selectionRequested.emit()

    def _select_row(self,identity):
        row=self._display_row(identity);self._hold_selection(row.id);self._timeline_selection=False
        self.media_controller.pause()
        if self.media_controller.index is not None and row.start is not None:
            index = self.media_controller.index
            first = index.frame_span(row.start,row.end)[0] if valid_time(row.start,row.end) else index.first(row.start)
            self.media_controller.seek(first)
        self._refresh(False);self.selectionRequested.emit()

    def _confirm_row(self,payload):
        row=self._row(payload.get('id'));value=self._boolean(payload,'value',True)
        def keep_selection():
            if self.selected==row.id:
                self._confirmed_selection=row.id
                # Confirming a playing cue must not permanently disable the
                # frame-driven selection of the following cue.
                self._hold_cue_selection=not self.media_controller.snapshot()['playing']
        self.editor.confirm(row.id,value,before_notify=keep_selection)

    def _confirm_next(self,payload):
        row=self._row(payload.get('id'));visible=[r['id'] for r in self.model.items]
        position=visible.index(row.id) if row.id in visible else -1
        next_id=visible[position+1] if 0<=position<len(visible)-1 else None
        self.editor.confirm(row.id,True)
        if next_id and any(m.id==next_id for m in self.document.project.mappings):
            self._select_row(next_id);self._message('已确认，进入下一句。')
        else:self._message('已确认最后一句。')

    def _manual_mode(self,payload):
        enabled=self._boolean(payload,'enabled',False)
        self._manual_axis_mode=enabled
        if not enabled:self._manual_start_frame=None
        self._refresh(False)

    def _manual_cancel(self):
        if self._manual_start_frame is None:raise ValueError('当前没有待取消的入点。')
        self._manual_start_frame=None;self._refresh(False);self._message('已取消当前入点。')

    def _manual_mark(self):
        if not self._manual_axis_mode:raise ValueError('请先开启打轴模式。')
        index=self.media_controller.index;frame=self.media_controller.snapshot()['displayedFrame']
        if index is None or not frame:raise ValueError('请先载入视频并等待画面就绪。')
        if self._manual_start_frame is None:
            self._manual_start_frame=frame;self._refresh(False);return
        first=self._manual_start_frame
        if frame < first:raise ValueError('出点不能早于入点；可取消入点后重新打点。')
        self._manual_create_range(first,frame)
        self._manual_start_frame=None;self._refresh(False)

    def _manual_create_at_playhead(self):
        index=self.media_controller.index;frame=self.media_controller.snapshot()['displayedFrame']
        if index is None or not frame:raise ValueError('请先载入视频并等待画面就绪。')
        desired=min(index.duration,index.stamp(frame)+3.0)
        last=max(frame,index.last(desired))
        self._manual_create_range(frame,last)

    def _assign_untimed_at_playhead(self,identity):
        index=self.media_controller.index;frame=self.media_controller.snapshot()['displayedFrame']
        if index is None or not frame:raise ValueError('请先载入视频并等待画面就绪。')
        last=max(frame,index.last(min(index.duration,index.stamp(frame)+3.0)))
        self._assign_untimed_range(identity,frame,last)

    def _assign_untimed_range(self,identity,first,last):
        # Explicit stable target, captured when the gesture/menu was started.
        # A stale target must fail, never fall back to inserting another row.
        if not isinstance(identity,str) or not identity:raise ValueError('请选择要补时间轴的字幕。')
        row=self._row(identity);index=self.media_controller.index
        if row.start is not None or row.end is not None:raise ValueError('该句已有时间轴，请使用修边调整。')
        if identity in self.document.project.settings.get('manual_locked_ids',[]):raise ValueError('当前句已锁定，请先解锁。')
        if index is None:raise ValueError('视频帧索引正在加载，请稍候。')
        if not 1<=first<=last<=len(index.stamps):raise ValueError('起止时间不能超出视频范围，且出点不能早于入点。')
        start,end=index.interval(first,last)
        self._hold_selection(identity);self._timeline_selection=True
        self.editor.set_time(identity,start=start,end=end)

    def _manual_create_range(self,first,last):
        index=self.media_controller.index
        if index is None:raise ValueError('请先载入视频并等待帧索引就绪。')
        start,end=index.interval(first,last)
        candidate=Project.from_dict(self.document.project.to_dict())
        before=next((m.id for m in candidate.mappings if valid_time(m.start,m.end) and m.start>start),None)
        draft=Editor(candidate,History())
        row=draft.insert_blank(fill_available_gap=False,before=before)
        row.start,row.end=start,end
        row.reasons=['新建空白句，请填写字幕并核对时间']
        self._adopt_project(candidate,'创建空白字幕')
        self._hold_selection(row.id);self._changed()

    def _delete_selected(self,payload):
        if self._editor_active or self._editor_composing:raise ValueError('请先退出文字编辑。')
        identities=payload.get('ids')
        if not isinstance(identities,list) or not identities or any(not isinstance(i,str) for i in identities):raise ValueError('请先选择要删除的字幕。')
        rows=[self._row(i) for i in dict.fromkeys(identities)]
        ids={m.id for m in rows}
        if ids.intersection(self.document.project.settings.get('manual_locked_ids',[])):raise ValueError('选中的字幕包含已锁定句，请先解锁。')
        ordered=self._review_order(self._all_rows)
        origin=next((n for n,r in enumerate(ordered) if r['id']==self.selected),0)
        neighbours=ordered[origin+1:]+list(reversed(ordered[:origin]))
        next_id=next((r['id'] for r in neighbours if r['id'] not in ids and (not self._unconfirmed_only or not r['confirmed'])),'')
        candidate=Project.from_dict(self.document.project.to_dict())
        editor=Editor(candidate,History())
        for row in rows:editor.delete_mapping(row.id)
        self._adopt_project(candidate,'批量删除字幕')
        self._multi_selected.clear();self._hold_selection(next_id);self._changed()
        self._message(f'已删除 {len(rows)} 句，可使用撤销恢复。')

    def _delete_row(self,payload):
        row=self._row(payload.get('id'));rows=self.document.project.mappings
        if row.id in self.document.project.settings.get('manual_locked_ids',[]):
            raise ValueError('当前句已锁定，请先解锁。')
        position=rows.index(row)
        neighbours=rows[position+1:]+list(reversed(rows[:position]))
        next_id=next((m.id for m in neighbours if not self._unconfirmed_only or self._timeline_selection or not m.confirmed),'')
        def keep_selection():
            self._hold_selection(next_id)
        self.editor.delete_mapping(row.id,before_notify=keep_selection)
        self._refresh(False);self._message('已删除当前句，可使用撤销恢复。')

    def _add(self,payload):
        candidate=Project.from_dict(self.document.project.to_dict())
        draft=Editor(candidate,History())
        row=draft.insert_blank(payload.get('after',self.selected or None),before=payload.get('before'))
        try:draft.fill_gap(row.id)
        except ValueError:pass  # Unknown/occupied boundaries remain unknown; never shift neighbours.
        self._adopt_project(candidate,'新增空白字幕')
        self._hold_selection(row.id);self._changed()

    def _merge(self,payload):
        row=self._row(payload.get('id'))
        order=[r['id'] for r in self._review_order(self._all_rows)]
        position=order.index(row.id)
        ids=payload.get('ids') or order[position:position+2]
        if any(i in self.document.project.settings.get('manual_locked_ids',[]) for i in ids):raise ValueError('要合并的字幕包含已锁定句，请先解锁。')
        candidate=Project.from_dict(self.document.project.to_dict());merged=merge_mappings(candidate,ids,order=order)
        self._adopt_project(candidate,'合并相邻字幕')
        self._hold_selection(merged.id);self._multi_selected.clear();self._changed()

    def _repeat(self,payload):
        row=self._row(payload.get('id'));self.history.push(self.document.project,'重复字幕')
        repeated=deepcopy(row);repeated.id='repeat-'+uuid.uuid4().hex;repeated.start=repeated.end=None
        repeated.lineage_ids=list(dict.fromkeys(row.lineage_ids+[row.id]))
        repeated.confirmed=False;repeated.locked=True;repeated.human_edited=True
        self.document.project.mappings.insert(self.document.project.mappings.index(row)+1,repeated)
        self._hold_selection(repeated.id);self._changed()

    def _restore_detected(self,payload):
        row=self._row(payload.get('id'));visuals=[v for v in self.document.project.visuals if v.id in row.visual_ids]
        if not visuals:raise ValueError('当前句没有关联的 OCR 时间记录。')
        self.editor.set_time(row.id,start=min(v.start for v in visuals),end=max(v.end for v in visuals))

    def _history_context(self):
        return {'selected':self.selected,'entry_mode':self._entry_mode,'stage':self.stage}

    def _history(self,redo):
        old_inputs=dict(self.document.project.inputs)
        old_duration=self.document.project.settings.get('editing_duration')
        records=deepcopy(self.document.project.api_records)
        old_rows=self.document.project.mappings
        old_position=next((i for i,m in enumerate(old_rows) if m.id==self.selected),0)
        restored,label=self.history.restore(self.document.project,redo,validator=self.document.validate_project)
        restored.api_records=records
        if label:
            identities={m.id for m in restored.mappings}
            context=self.history.restored_context
            if isinstance(context,dict):
                restored_selection=context.get('selected','')
                self._entry_mode=self.document.entry_mode=context.get('entry_mode',self._entry_mode)
                if label in ('切换模式：清空时间轴','切换工作方式'):
                    self.stage=context.get('stage',self.stage)
                    self._manual_axis_mode=False;self._manual_start_frame=None
                    self._allocation={};self._allocation_context=None
            else:restored_selection=context
            self._invalidate_assets()  # Undo never revives late asset results.
            self._entry_select_mode(self._entry_mode)
            if restored_selection in identities:self.selected=restored_selection
            elif self.selected not in identities:
                self.selected=restored.mappings[min(old_position,len(restored.mappings)-1)].id if restored.mappings else ''
            self._hold_cue_selection=True;self._timeline_selection=True;self._confirmed_selection=''
            project_changed=self._adopt_project(restored)
            old_source=old_inputs.get('video') or old_inputs.get('audio')
            source=restored.inputs.get('video') or restored.inputs.get('audio')
            duration=restored.settings.get('editing_duration')
            if source!=old_source or (not source and duration!=old_duration):
                self._clear_audio_analysis()
                if source and Path(source).is_file():self.media_controller.open(source)
                elif not source and duration:self.media_controller.open_virtual(duration)
                else:self.media_controller.clear()
            if label in ('切换模式：清空时间轴','切换工作方式') and not project_changed and not self.path:self._refresh()
            else:self._changed()
            self._hold_playing_interval();self._message(('重做：' if redo else '撤销：')+label)

    def _settings(self,payload):
        allowed={'motion':'motion','subtitlePreview':'subtitle_preview','playerRounded':'player_rounded','playerGlass':'player_glass','apiEnabled':'api_auto_review','language':'language'}
        key=payload.get('key')
        if key not in allowed:raise ValueError('暂不支持修改此项设置。')
        value=payload.get('value')
        if key in ('motion','subtitlePreview','playerRounded','playerGlass','apiEnabled') and type(value) is not bool:raise ValueError('开关值必须为真/假。')
        if key=='language' and (not isinstance(value,str) or not value or len(value)>80):raise ValueError('OCR 识别语言设置无效。')
        if key=='apiEnabled' and value:self._api_check()
        if self.document.project.settings.get(allowed[key])!=value:
            self.history.push(self.document.project,'修改设置');self.document.project.settings[allowed[key]]=value;self._changed()

    def _move_subtitle(self,payload):
        from lyricsync.export import layout_entries,layout_anchor,placement_options
        dx=payload.get('dx');dy=payload.get('dy')
        if any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>1 for v in (dx,dy)):
            raise ValueError('字幕位置调整超出有效范围。')
        p=self.document.project;width=p.video.get('width');height=p.video.get('height')
        if not width or not height:raise ValueError('请先载入视频。')
        sample=layout_anchor((width,height),p.settings.get('font_size'),**placement_options(p))
        x=max(5.,min(95.,(sample['x']/width+dx)*100))
        y=max(5.,min(95.,(sample['y']/height+dy)*100))
        self.history.push(p,'拖动字幕位置')
        alignment=8 if p.settings.get('subtitle_position')=='top' else (p.settings.get('subtitle_custom_alignment',2) if p.settings.get('subtitle_position')=='custom' else 2)
        p.settings.update(subtitle_horizontal_percent=x,subtitle_position='custom',subtitle_height_percent=y,subtitle_custom_alignment=alignment)
        self._changed()

    def _auto_roi(self):
        project=self.document.project
        video=project.inputs.get('video');width=project.video.get('width');height=project.video.get('height')
        if not video or not width or not height or not self.media_controller.snapshot()['ready']:
            raise ValueError('请先载入视频并等待画面就绪。')
        settings=deepcopy(project.settings);old_roi=deepcopy(settings.get('roi'));epoch=self.epoch
        revision=self.document.revision
        def work(cancel,progress):
            from lyricsync.vision import make_ocr,propose_regions
            progress(.05,'正在自动检测字幕区域…')
            return propose_regions(video,make_ocr(settings),cancel)
        def completed(result,error):
            if error:raise error
            if self.epoch!=epoch or self.document.project.inputs.get('video')!=video or self.document.project.settings.get('roi')!=old_roi:
                raise ValueError('检测期间视频或选区已变动，请重新检测。')
            if not result:raise ValueError('未自动检测到明显的字幕区域，请手动框选。')
            from .roi_controls import region_choices, store
            selected = region_choices(result)
            if not selected:
                return
            if self.epoch != epoch or self.document.revision != revision or self.document.project.inputs.get('video') != video:
                raise ValueError('确认期间工程已改变，请重新检测。')
            from lyricsync.multi_roi import project_regions
            original = project_regions(self.document.project.settings, width, height)
            by_box = {tuple(r['box']):r for r in original}
            regions = [deepcopy(by_box.get(tuple(b['box']))) or
                       dict(id='roi-'+uuid.uuid4().hex[:12],name=f'区域 {i+1}',box=b['box'],enabled=True)
                       for i,b in enumerate(selected)]
            if self.stage == 'review':
                return
            store(self, [dict(r,enabled=True) for r in regions])
            self._message(f'已采用 {len(regions)} 个选区，可在画布或导入页继续微调。')
        self._start_job('自动检测字幕区域',work,completed)

    @staticmethod
    def _valid_roi(roi,width,height):
        return (isinstance(roi,(list,tuple)) and len(roi)==4 and
                all(type(v) in (int,float) and math.isfinite(v) for v in (*roi,width,height)) and
                0<=roi[0]<roi[2]<=width and 0<=roi[1]<roi[3]<=height)

    def _roi(self,payload):
        p=self.document.project;width,height=p.video.get('width'),p.video.get('height')
        if not width or not height:raise ValueError('请先载入视频。')
        values=[payload.get(k) for k in ('x','y','width','height')]
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in values):raise ValueError('字幕选区坐标无效。')
        x,y,w,h=values
        if min(x,y)<0 or min(w,h)<=0 or x+w>1.00001 or y+h>1.00001:raise ValueError('字幕选区不能超出视频画面。')
        pixels=[round(x*width),round(y*height),round((x+w)*width),round((y+h)*height)]
        if not self._valid_roi(pixels,width,height):raise ValueError('识别区域过小，请完整框住字幕区域。')
        self.history.push(p,'调整字幕识别区域');p.settings['roi']=pixels
        # Legacy single-region gestures must not add unrelated settings keys.
        if 'ocr_multi_region' in p.settings:p.settings['ocr_multi_region']=False
        self._changed()

    def _media_loaded(self,metadata,index):
        video_path=self.document.project.inputs.get('video') or self.document.project.inputs.get('audio')
        loaded_path=self.media_controller.path
        # Saved projects can contain Qt-style separators too. Keep the stale
        # media guard, but compare paths rather than their spelling.
        if metadata.get('media_kind')=='virtual':
            self._refresh();return
        if not video_path or not loaded_path:return
        if Path(video_path).expanduser().resolve()!=Path(loaded_path).expanduser().resolve():return
        project=self.document.project
        previous_hash=project.video.get('content_hash');loaded_hash=metadata.get('content_hash')
        if project.mappings and previous_hash and loaded_hash and previous_hash!=loaded_hash:
            self.media_controller.clear()
            self._error('当前视频与工程原本使用的视频不一致（文件可能已被替换）。为免时间轴错位，请重新指定原视频或新建工程。')
            return
        if metadata.get('media_kind')=='audio':
            project.inputs['audio']=loaded_path;project.inputs.pop('video',None)
            if self._entry_mode!='edit':self.stage='review'
        from lyricsync.timebase import Timebase
        project.timebase=Timebase(frame_coordinates='one-based-10ms-editing-grid').to_dict() if metadata.get('media_kind')=='audio' else Timebase().to_dict()
        self._start_audio_analysis(metadata,index)
        summary={k:v for k,v in metadata.items() if k not in ('frame_pts','frame_stamps')}
        changed=any(self.document.project.video.get(k)!=v for k,v in summary.items())
        self.document.project.video.update(summary)
        # Full PTS arrays are media/cache data, never copied into every edit undo.
        from lyricsync.storage import atomic_json
        cache_path=self.storage_dir/('frame-index-'+metadata.get('content_hash',self.document.id)+'.json')
        self._start_job('frame-index-cache',lambda cancel,progress:atomic_json(cache_path,metadata),lambda result,error:self._error('帧索引缓存写入失败：'+type(error).__name__) if error else None,foreground=False)
        if changed:self._changed()
        else:self._refresh()
        self._message('音频已就绪（10 毫秒精度时间轴）。' if metadata.get('media_kind')=='audio' else '视频帧索引已就绪，可逐帧定位。')

    def _open_video(self,payload):
        if self._task.get('running') or self._candidate_job:
            raise ValueError('素材处理中，请先等待完成或取消任务，再更换素材。')
        if self._editor_active or self._editor_composing:
            raise ValueError('请先完成当前文字输入。')
        path=self._path(payload,title='导入视频或音频',category='import',filter='媒体 (*.mp4 *.mkv *.mov *.avi *.webm *.ts *.wav *.flac *.mp3 *.m4a *.aac *.ogg *.opus);;所有文件 (*)')
        if not path:return
        if not Path(path).is_file():raise ValueError('找不到所选的音视频文件。')
        p=self.document.project
        previous=p.inputs.get('video') or p.inputs.get('audio')
        replacing=bool(previous and Path(previous).expanduser().resolve()!=Path(path).resolve())
        choice='keep'
        has_timing=any(row.start is not None or row.end is not None for row in p.mappings)
        if (replacing and has_timing) or p.settings.get('media_detached_with_timing'):
            identity,revision=self.document.id,self.document.revision
            choice=self._confirm_media_timing_reuse()
            if choice is True:choice='keep'  # compatibility for old integrations
            if choice not in ('keep','discard'):return
            if (self.document.id!=identity or self.document.revision!=revision or
                    self._task.get('running') or self._candidate_job):
                raise ValueError('确认期间工程发生变化，请重新选择素材。')
        from .asset_evidence import check_archive_capacity,archive_evidence,detach_ocr_evidence
        if replacing or (not previous and p.visuals):check_archive_capacity(p)
        self._manual_start_frame=None
        self.history.push(p,'更换音视频：放弃原时间轴' if choice=='discard' else '导入视频')
        if replacing or (not previous and p.visuals):
            archive_evidence(p,'更换音视频前的完整证据')
            self._isolate_asset_candidates();self._invalidate_assets()
            detach_ocr_evidence(p)
        if choice=='discard':
            for row in p.mappings:
                row.start=row.end=None
                row.text_segments=[]
                row.confirmed=False
                row.locked=False
                row.visual_ids=[]
                row.confidence=0.
                row.method='media-replacement-untimed'
            p.settings['manual_locked_ids']=[]
            p.settings['pending_text_splits']=[]
            self._allocation={};self._allocation_context=None
            self._alignment_preview.clear()
        p.settings.pop('media_detached_with_timing',None)
        if replacing or not previous:
            # Old content fingerprints and dimensions must not reject the newly
            # selected file when its asynchronous loaded callback arrives.
            p.video.clear()
            p.settings['roi']=None
            for key in ('video','audio'):
                p.settings.get('input_hashes',{}).pop(key,None)
        self._clear_audio_analysis()
        self.media_controller.clear()
        p.inputs.pop('audio',None);p.inputs['video']=path
        self.stage='import' if self._entry_mode=='edit' else 'ocr'
        self.media_controller.open(path);self._changed()
        if choice=='discard':self._message('已更换素材并清除主轴时间，歌词和样式均保留；可撤销。')

    @staticmethod
    def _confirm_media_timing_reuse():
        from PySide6.QtWidgets import QMessageBox
        dialog=QMessageBox(QMessageBox.Icon.Warning,'更换音视频后的时间轴',
            '现有时间属于之前的素材，换片后可能错位。\n保留时间后请重新校对；放弃原时间轴会清除主轴时间和时间锁定，但保留歌词与样式。关闭或取消不会更换素材。')
        keep=dialog.addButton('保留时间，重新校对',QMessageBox.ButtonRole.AcceptRole)
        discard=dialog.addButton('放弃原时间轴',QMessageBox.ButtonRole.DestructiveRole)
        cancel=dialog.addButton('取消',QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel);dialog.setEscapeButton(cancel)
        dialog.exec()
        clicked=dialog.clickedButton()
        return 'keep' if clicked is keep else ('discard' if clicked is discard else None)

    def _virtual_timeline_duration(self,duration=None):
        p=self.document.project
        last=max([0.]+[float(t) for row in p.mappings for t in (row.start,row.end) if t is not None])
        if last>86400:raise ValueError('纯编辑时间轴最长为 24 小时。')
        requested=p.settings.get('editing_duration',300.) if duration is None else duration
        try:
            duration=max(float(requested),min(86400.,last+10.) if last else 0.)
            from lyricsync.virtual_timeline import VirtualFrameIndex
            VirtualFrameIndex(duration)
        except (TypeError,ValueError,OverflowError) as error:
            raise ValueError('无媒体编辑时长必须大于零且不超过 24 小时；未修改素材选择。') from error
        return duration

    def _ensure_virtual_timeline(self,duration=None):
        p=self.document.project
        if p.inputs.get('video') or p.inputs.get('audio'):return
        duration=self._virtual_timeline_duration(duration)
        from lyricsync.timebase import Timebase
        p.timebase=Timebase(frame_coordinates='one-based-10ms-editing-grid').to_dict()
        p.settings['editing_duration']=duration
        p.settings['editing_grid_seconds']=.01
        self._clear_audio_analysis()
        self.media_controller.open_virtual(duration)

    def _pure_editing(self,payload):
        if self.document.project.inputs.get('video') or self.document.project.inputs.get('audio'):
            raise ValueError('当前工程已载入音视频；如需使用纯时间轴模式，请新建工程。')
        duration=payload.get('durationSeconds',300.)
        if type(duration) not in (int,float) or not math.isfinite(duration) or not 0<duration<=86400:
            raise ValueError('时间轴总时长需大于 0 且不超过 24 小时。')
        self.history.push(self.document.project,'启用纯时间轴模式')
        self._ensure_virtual_timeline(duration);self.stage='review';self._changed()

    def _start_editor(self,payload):
        mode=payload.get('mode')
        if mode not in ('ocr','audio','edit','separate'):raise ValueError('请选择画面字幕 OCR、音频自动打轴、仅人声分离或手动打轴编辑。')
        if self._editor_active or self._editor_composing:raise ValueError('请先完成当前文字输入。')
        if getattr(self,'_mode_switch_pending',False):return False
        if mode!=self._entry_mode and (self._task.get('running') or self._candidate_job or self._save_running):
            raise ValueError('请先等待当前任务完成或取消任务，再切换模式。')
        switching=mode!=self._entry_mode and bool(self._has_workspace or self.document.main_timeline or self._entry_session['mode'])
        if switching and self.document.main_timeline:
            self._entry_observe_main()
            if not self._entry_session['eligible'] or any(m.human_edited or m.confirmed or m.locked for m in self.document.main_timeline):
                from .dialogs import choose_asset_clear
                identity,revision=self.document.id,self.document.revision
                self._mode_switch_pending=True
                try:choice=choose_asset_clear()
                finally:self._mode_switch_pending=False
                if (self.document.id!=identity or self.document.revision!=revision or self._task.get('running') or self._candidate_job):raise ValueError('确认期间工程已改变，请重新选择。')
                if choice=='new':
                    def new_mode():
                        self._new();self._start_editor({'mode':mode})
                    self._replace_guard(new_mode);return False
                if choice!='detach':return False
                from .asset_evidence import check_archive_capacity,archive_evidence,detach_ocr_evidence
                check_archive_capacity(self.document.project)
                self.history.push(self.document.project,'解绑模式并转纯编辑')
                archive_evidence(self.document.project,'解绑模式前的完整证据')
                detach_ocr_evidence(self.document.project)
                self._isolate_asset_candidates();self._invalidate_assets()
                self._entry_mode=self.document.entry_mode='edit';self._entry_select_mode('edit')
                self._startup_visible=False;self._has_workspace=True;self.stage='review'
                self._changed();self._message('已保留全部字幕并转纯编辑；旧处理候选已隔离，可撤销。')
                return True
        pending_draft = self._manual_start_frame is not None or bool(self._allocation) or self._alignment_preview.active
        if switching and (self.document.main_timeline or pending_draft):
            from .dialogs import confirm_mode_switch
            self._mode_switch_pending=True
            identity,revision=self.document.id,self.document.revision
            try:
                approved = confirm_mode_switch() if self.document.main_timeline else confirm_mode_switch(pending_only=True)
                if not approved:return False
            finally:self._mode_switch_pending=False
            # A modal loop may receive queued work. Approval cannot clear newer edits.
            if self.document.id!=identity or self.document.revision!=revision or self._task.get('running') or self._candidate_job or self._save_running:
                raise ValueError('工程在确认期间发生变化，未清空时间轴，请重新选择模式。')
        if switching and self.document.main_timeline:
            from .asset_evidence import check_archive_capacity,archive_evidence,detach_ocr_evidence
            check_archive_capacity(self.document.project)
            self.history.push(self.document.project,'切换模式：清空时间轴')
            archive_evidence(self.document.project,'切换模式前的完整证据')
            detach_ocr_evidence(self.document.project)
            self._isolate_asset_candidates();self._invalidate_assets()
            settings=self.document.project.settings
            timeline_changed=bool(self.document.main_timeline or settings.get('pending_text_splits') or settings.get('manual_locked_ids') or settings.get('timeline_archives') or settings.get('historical_asset_candidate_ids'))
            if mode!='ocr':
                from .timeline_views import isolate_vocal_candidates
                timeline_changed=isolate_vocal_candidates(self.document) or timeline_changed
            self.document.project.mappings=[]
            for key in ('pending_text_splits','manual_locked_ids'):
                if settings.get(key):settings[key]=[]
            self.selected='';self._multi_selected.clear()
            self._manual_axis_mode=False;self._manual_start_frame=None
            self._allocation={};self._allocation_context=None
            self._alignment_preview.clear()
            if timeline_changed:self._changed()
            else:self._refresh()  # UI-only mode history does not dirty project content.
            # Explicitly clearing is a new processing intent, not a page visit.
            self._entry_reset(new=True)
        elif switching:
            self.history.push(self.document.project,'切换工作方式')
            self._manual_axis_mode=False
            self._manual_start_frame=None
            self._allocation={};self._allocation_context=None
            if pending_draft:self._alignment_preview.clear()
        if self._candidate_source!='main':self.switchCandidateSource('main')
        self._entry_select_mode(mode)
        self._startup_visible=False
        self._has_workspace=True
        previous_mode = self.document.entry_mode
        self._entry_mode=self.document.entry_mode=mode
        if previous_mode!=mode and switching and (self.path or self.document.revision):
            self.document.touch()
            self.autosave_timer.start()
        self.stage='import';self._refresh(False)
        self._message({'ocr':'请导入视频并框选字幕区域，即可开始 OCR 识别打轴。','audio':'请导入音视频与演唱原文（或使用语音转写），开始自动打轴。','edit':'可先导入视频或音频，歌词与现有字幕可选；准备好后进入歌词编辑，也可无媒体编辑。','separate':'请选择音视频及 HQ3/HQ1，仅分离人声供试听比较，不需要歌词，也不会生成时间轴。'}[mode])
        return True

    def _continue_editor(self,payload):
        if self._entry_mode!='edit':
            raise ValueError('请先选择纯编辑模式。')
        if self._task.get('running') or self._candidate_job or self._editor_composing:
            raise ValueError('请先等待当前任务或文字输入完成。')
        media_path=self.document.project.inputs.get('video') or self.document.project.inputs.get('audio')
        if media_path:
            if self.media_controller.index is None or not self.media_controller.snapshot().get('ready'):
                raise ValueError('音视频仍在载入，请等待就绪后进入歌词编辑。')
            self.stage='review';self._refresh(False)
        else:
            self._pure_editing(payload)
        return True

    def _new_entry(self,payload):
        mode=payload.get('mode')
        if mode not in ('ocr','audio','edit','separate'):raise ValueError('请选择画面字幕 OCR、音频自动打轴、仅人声分离或手动打轴编辑。')
        if self._editor_active or self._editor_composing:raise ValueError('请先完成当前文字输入。')
        def begin():
            self._new()
            self._start_editor({'mode':mode})
        if self._has_close_reasons():
            self._replace_guard(begin)
            return False
        begin()
        return True

    def _configure_vocal(self,setup_only=False,initial_options=None):
        from PySide6.QtWidgets import QDialog
        from .local_alignment_dialog import LocalAlignmentDialog
        if self._candidate_job and not setup_only:raise ValueError('当前已有识别或打轴任务正在运行，请等待完成或先取消。')
        from . import local_alignment_settings
        source_signature=local_alignment_settings.source_signature(self.document)
        source_document,source_revision=self.document.id,self.document.revision
        retry = getattr(self, '_vocal_retry_options', None)
        if not setup_only and not initial_options and retry and retry[:2] == (source_document, source_revision):
            initial_options = retry[2]
        dialog=LocalAlignmentDialog(deepcopy(self.document),default_audio=self.document.project.inputs.get('audio') or self.document.project.inputs.get('video',''),setup_only=setup_only,
                                    initial_options={**local_alignment_settings.load(self.storage_dir), **(initial_options or {})})
        accepted=dialog.exec()==QDialog.DialogCode.Accepted
        options=deepcopy(dialog.options()) if accepted else {}
        if (not setup_only and hasattr(dialog,'source_state') and
                source_signature==local_alignment_settings.source_signature(self.document)):
            before_save=self.document.revision
            if local_alignment_settings.save_source_state(self.document.project,dialog.source_state()):
                self._changed()
                draft=options.get('transcript_draft',{})
                # Only our own source-preference save may advance an unchanged
                # draft. A real edit during the dialog keeps the stale guard.
                if (draft.get('document_id')==source_document==self.document.id and
                        draft.get('revision')==source_revision==before_save):
                    draft['revision']=self.document.revision
        if not accepted:return
        if not setup_only:
            if source_signature!=local_alignment_settings.source_signature(self.document):
                raise ValueError('设置窗口打开期间原词或译文已改变，请重新打开自动打轴。')
            from .alignment_input import commit_partition
            if commit_partition(self.document,self.history,(dialog.source_state() if hasattr(dialog,'source_state') else {}).get('split_pairs',[])):
                self._entry_accept_import();self._candidate_changed()
        local_alignment_settings.save(self.storage_dir,options)
        if setup_only:
            self._message('音频打轴设置已保存。');return
        self._vocal_retry_options = None
        detached=deepcopy(self.document);index=deepcopy(self.media_controller.index)
        from lyricsync.local_alignment import generate_vocal_candidate
        def work(cancel,progress):
            generator=generate_vocal_candidate
            if 'transcript_draft' in options:
                from lyricsync.local_transcription import generate_confirmed_draft_candidate
                generator=generate_confirmed_draft_candidate
            return generator(detached,index,options=options,cancel=cancel,
                             progress=lambda percent,message:progress(percent/100.,message))
        def completed(record,error):
            if error:
                if not isinstance(error, InterruptedError) and (self.document.id, self.document.revision) == (detached.id, detached.revision):
                    self._vocal_retry_options = (detached.id, detached.revision, deepcopy(options))
                if str(error).startswith('媒体范围不匹配：'):
                    from PySide6.QtWidgets import QMessageBox
                    from PySide6.QtCore import QTimer
                    box=QMessageBox(QMessageBox.Icon.Warning,'媒体范围需要确认',str(error),parent=None)
                    box.setTextFormat(Qt.TextFormat.PlainText)
                    choose=box.addButton('重新选择音频 / 范围',QMessageBox.ButtonRole.AcceptRole)
                    keep=box.addButton('保留设置并取消',QMessageBox.ButtonRole.RejectRole)
                    box.setDefaultButton(keep);box.exec()
                    self._message('本次打轴未启动或未完成，原素材、歌词和主轴均未自动裁剪或平移。')
                    if box.clickedButton() is choose:
                        QTimer.singleShot(0,lambda:self._configure_vocal(initial_options=options))
                    return
                raise error
            self._complete_vocal_candidate(record, cache_root=options.get("cache_root"))
        self._start_candidate_job('人声自动打轴','vocal',work,completed)
        self._refresh(False)

    def _open_separation(self):
        from .separation_dialog import SeparationDialog
        from . import local_alignment_settings
        if self._task.get('running') or self._candidate_job:
            raise ValueError('请先等待当前任务完成或取消任务，再打开人声分离。')
        # The dialog owns no document reference and cannot write a timeline.
        dialog=SeparationDialog(default_audio=self.document.project.inputs.get('audio') or self.document.project.inputs.get('video',''),
            initial_options=local_alignment_settings.load(self.storage_dir),
            save_options=lambda options: local_alignment_settings.save(self.storage_dir, options))
        dialog.exec()
        dialog.deleteLater()

    def _reset_transient(self):
        self._review_new_context=None
        self._candidate_init()
        self._vocal_retry_options = None
        self._ocr_settings_pending=False;self._ocr_settings_job=None
        self._entry_reset()
        self._entry_mode='ocr'
        self._clear_audio_analysis()
        self._alignment_preview.clear()
        self._alignment_check_context=None;self._alignment_checked={};self._alignment_selection=None
        self._manual_axis_mode=False;self._manual_start_frame=None;self._multi_selected.clear()
        self._alignment={};self._alignment_retry=None;self._api_resume=None;self._api_resume_context=None;self._qc=dict(inputPath='',mode='local',issues=[],summary={},status='尚未运行',available=True);self._qc_preview=None
        self.api_state={'status':'尚未调用 AI 接口','models':self.api_state.get('models',[])}
        self._unconfirmed_only=False;self._editor_active=False;self._editor_composing=False;self._editor_id='';self._ime_commit_pending=False;self._hold_cue_selection=False;self._close_dialog={}
        self._timeline_selection=False;self._confirmed_selection=''

    def _frame_presented(self,frame,stamp):
        if self._editor_active or self._editor_composing:return
        pos=bisect_right(self._preview_stamps,stamp+1e-6)-1
        active=self._active_ids[pos] if 0<=pos<len(self._active_ids) else []
        if self._hold_cue_selection:
            held=getattr(self,'_held_playback_ids',None)
            # An older, long overlapping cue becoming the only survivor is not
            # a new sentence. Do not pull the user's edit back to that old row.
            entered=[identity for identity in active if held is not None and identity not in held]
            if not self.media_controller.snapshot()['playing'] or not entered:return
            self._hold_cue_selection=False;self._held_playback_ids=None
            active=entered
        if not active:return
        if self._unconfirmed_only:
            active=[i for i in active if i in self._visible_ids]
        if active:
            # A long overlapping cue must not pin selection while newer cues enter.
            # Explicit editing/play-cue holds above still own their full interval.
            latest=max(active,key=lambda identity:self._all_rows[self._row_positions[identity]]['firstFrame'])
            if self.selected in active and self._all_rows[self._row_positions[self.selected]]['firstFrame']==self._all_rows[self._row_positions[latest]]['firstFrame']:return
            if latest==self.selected:return
            self.selected=latest
            self._timeline_selection=False;self._confirmed_selection='';self._refresh(False)

    def _new_from_review(self):
        if self.stage != 'review' or self._startup_visible:
            return
        if self._task.get('running') or self._candidate_job or self._save_running or self._editor_composing:
            raise ValueError('请先结束当前输入、保存或后台任务，再新建工程。')
        if self.document.revision==self.document.saved_revision:
            self._new()
            return
        self._close_dialog=dict(visible=True,newFromReview=True,dirty=True)
        self._review_new_context=(self.document,self.document.revision,self.epoch)
        self._refresh(False)
        self.closeDialogRequested.emit(self._close_dialog)

    def _finish_new_from_review(self, context):
        if not context or self.document is not context[0] or (self.document.revision,self.epoch)!=context[1:] or self._task.get('running') or self._candidate_job:
            raise ValueError('确认期间工程或任务已改变，请重新选择新建工程。')
        self._new()

    def _new(self):
        self._startup_visible=True
        self._has_workspace=False
        self.jobs.cancel(self._task.get('id',''));self.epoch+=1;self.media_controller.clear();getattr(self,'_job_asset_epochs',{}).clear()
        self.autosave_timer.stop();self.layout_timer.stop();self._layout_running=False;self._layout_pending=False;self._layout_revision=-1;self._save_running=None;self._save_pending=None
        self.document=ProjectDocument(project=Project());self.document.mark_saved(0);self.document.mark_exported(0);self.history=History(context=self._history_context);self.editor=Editor(self.document.project,self.history,self._changed)
        self._reset_transient()
        self._entry_reset(new=True)
        self.path='';self.selected='';self.stage='import';self._task=dict(running=False,label='',progress=0.,id='')
        self._error_text='';self._status='新工程';self._close_dialog={};self._refresh()
        return True

    def _start_job(self,kind,function,callback, *, foreground=True,diagnostic_stage='worker',asset_dependent=True):
        if self._shutdown:raise ValueError('应用正在关闭。')
        if foreground and self._task['running']:raise ValueError('当前有任务正在运行，请等待完成或先取消。')
        identity=self.jobs.run(kind,function);self._handlers[identity]=(callback,self.epoch)
        if not hasattr(self,'_job_asset_epochs'):self._job_asset_epochs={}
        if asset_dependent and kind!='save':self._job_asset_epochs[identity]=getattr(self,'_asset_epoch',0)
        self._job_diagnostic_stages[identity]=diagnostic_stage
        if foreground:self._task=dict(running=True,label=kind,progress=0.,id=identity,startedAt=time.time()*1000);self._refresh(False)
        return identity

    def _job_progress(self,identity,progress,label):
        if self._shutdown:return
        generation=getattr(self,'_job_asset_epochs',{}).get(identity)
        if generation is not None and generation!=getattr(self,'_asset_epoch',0):return
        if identity==self._candidate_job and not self._candidate_task.get('cancelling'):
            self._candidate_task.update(progress=max(0.,min(1.,progress)),label=label);self._refresh(False)
        if identity==self._task.get('id') and not self._task.get('cancelling'):
            self._task.update(progress=max(0.,min(1.,progress)),label=getattr(label,'summary',str(label)),details=str(label) if hasattr(label,'summary') else '');self._refresh(False)

    def _job_finished(self,identity,result,error):
        job_stage=self._job_diagnostic_stages.pop(identity,'worker')
        self.jobs.active.pop(identity,None)
        registered=self._handlers.pop(identity,None)
        asset_epoch=getattr(self,'_job_asset_epochs',{}).pop(identity,None)
        if self._shutdown:return
        self._accepted_save_intents.pop(identity,None)
        self.jobs.acknowledge(identity);self.jobs.reap_threads()
        if identity==self._task.get('id'):
            self._task=dict(running=False,label='已取消' if isinstance(error,InterruptedError) else '完成',progress=1.,id='')
        if not registered:return
        callback,epoch=registered
        if epoch!=self.epoch or (asset_epoch is not None and asset_epoch!=getattr(self,'_asset_epoch',0)):
            if identity==getattr(self,'_ocr_settings_job',None):
                self._ocr_settings_pending=False;self._ocr_settings_job=None
            if identity==self._candidate_job:
                self._candidate_job=None
                self._candidate_task.update(running=False,id='',cancelling=False,label='素材已改变，旧结果未应用')
            self._refresh(False);return
        if error:self.diagnostics.record('worker-failed',error,stage=job_stage)
        try:callback(result,error)
        except Exception as exc:
            self.diagnostics.record('callback-failed',exc,stage=job_stage)
            self._error(str(exc))
        self._refresh(False)

    def _replace_guard(self,callback):
        if self._has_close_reasons():
            self._continuation=callback;self._show_close()
        else:callback()

    def _open_project(self,payload):
        path=self._path(payload,title='打开工程',filter='Lyria 工程 (*.lyria);;所有文件 (*)')
        if not path:return
        def begin():
            def completed(document,error):
                if error:raise error
                # Only a successfully read replacement document may retire the old source.
                # Missing/absent video must never leave the previous project's PTS usable.
                self.media_controller.clear()
                self.epoch+=1;self.autosave_timer.stop();self.layout_timer.stop();self._layout_running=False;self._layout_pending=False;self._layout_revision=-1;self._save_running=None;self._save_pending=None
                self.document=document;self._reset_transient();self._entry_mode=document.entry_mode;self._entry_select_mode(document.entry_mode);self.path='' if payload.get('recovery') else path;self.history=History(context=self._history_context)
                if payload.get('recovery'):self.document.saved_revision=-1
                self.editor=Editor(document.project,self.history,self._changed);self.selected='';self.stage='review';self._startup_visible=False;self._has_workspace=True
                if self.path:self._recent_projects.remember(self.path);self._recent_snapshot=self._recent_projects.snapshot()
                self._refresh();video=document.project.inputs.get('video') or document.project.inputs.get('audio')
                if video and Path(video).is_file():self.media_controller.open(video)
                elif video:self._error('找不到原音视频文件；工程已打开，请重新指定素材位置。')
                elif (document.project.settings.get('editing_grid_seconds')==.01 and
                      document.project.timebase.get('frame_coordinates')=='one-based-10ms-editing-grid'):
                    # Rehydrate only explicitly saved pure-editing transport.
                    # Opening a document is not permission to migrate its data or
                    # invalidate candidates by rewriting settings/timebase.
                    self.media_controller.open_virtual(document.project.settings.get('editing_duration',300.))
            self._start_job('打开工程',lambda cancel,progress:load_document(path,recover=bool(payload.get('recover',False))),completed)
        self._replace_guard(begin)

    def _save(self,explicit,path=None, *, new_context=None):
        if explicit and not path:raise ValueError('请指定工程保存路径。')
        revision=self.document.revision
        if explicit:
            from lyricsync.storage import protect_output,canonical
            if not Path(path).suffix:path=str(path)+'.lyria'
            if Path(path).suffix.casefold()!='.lyria':raise ValueError('工程文件请使用 .lyria 扩展名。')
            protected=[p for p in self._protected_paths() if not self.path or canonical(p)!=canonical(self.path)]
            if self.path and canonical(path)!=canonical(self.path):protected.append(self.path)
            protect_output(path,protected)
        target=str(path or self.storage_dir/(self.document.id+'.recovery.lyria'))
        if (explicit and self._save_running and self._save_pending and self._save_pending[2]
                and canonical(target)!=canonical(self._save_pending[1])):
            raise ValueError('已有另一个另存为请求等待写入；本次未接受，请等待保存完成后再试。')
        snapshot=self.document.snapshot(explicit_save=explicit)
        intent=(snapshot,target,explicit,revision,self.epoch,new_context)
        # Serialized writes; pending recovery coalesces without ever replacing an
        # explicit save request. No synchronous flush/join on the GUI thread.
        if self._save_running:
            if explicit:self._save_pending=intent
            elif self._save_pending is None or not self._save_pending[2]:self._save_pending=intent
            return
        self._save_running=intent;self._autosave_status='正在保存工程' if explicit else '正在自动备份…';self._refresh(False)
        def completed(result,error):
            self._save_running=None
            if error:
                from lyricsync.storage import DocumentSizeError
                self._autosave_status='保存失败';self._closing=False
                self._error(str(error) if isinstance(error,DocumentSizeError) else '工程保存失败：'+type(error).__name__)
            else:
                if explicit:
                    self.path=target;self.document.mark_saved(revision);self._autosave_status='工程已保存'
                    self._recent_projects.remember(target);self._recent_snapshot=self._recent_projects.snapshot()
                else:self._last_autosaved_revision=revision;self._autosave_status='已自动备份'
                self._refresh(False)
                if new_context is not None and not (self._save_pending and self._save_pending[2]):
                    self._finish_new_from_review(new_context)
                elif explicit and self._closing:self._resume_close()
            if self._shutdown:return  # Shutdown owns any already accepted pending save.
            pending=self._save_pending;self._save_pending=None
            if pending and pending[4]==self.epoch:
                # Capture newest state for autosave; explicit intent preserves its path.
                self._save(pending[2],pending[1] if pending[2] else None,new_context=pending[5])
        identity=self._start_job('save',lambda cancel,progress:write_document(snapshot,target),completed,foreground=False)
        self._accepted_save_intents[identity]=intent

    def _restore_recovery(self,payload):
        path=payload.get('path')
        if not path:
            files=sorted(self.storage_dir.glob('*.recovery.lyria'),key=lambda p:p.stat().st_mtime,reverse=True)
            initial=str(files[0]) if files else str(self.storage_dir)
            path,_=QFileDialog.getOpenFileName(None,'选择自动备份文件',initial,'Lyria 自动备份 (*.recovery.lyria *.lyria.bak *.pipeline.lyricsync.json)')
            if not path:return
        if str(path).endswith('.pipeline.lyricsync.json'):
            def begin():
                from lyricsync.storage import load_project
                def completed(project,error):
                    if error:raise error
                    self._new();self.document=ProjectDocument(project=project);self.editor=Editor(project,self.history,self._changed)
                    from lyricsync.project_document import infer_entry_mode
                    self._entry_mode=self.document.entry_mode=infer_entry_mode(project)
                    self._entry_reset();self._entry_select_mode(self._entry_mode)
                    self.stage='review';self._changed();self._message('已从备份恢复进度，请及时另存为工程文件。')
                    if project.inputs.get('video') and Path(project.inputs['video']).is_file():self.media_controller.open(project.inputs['video'])
                self._start_job('正在恢复备份',lambda cancel,progress:load_project(path,recover=True),completed)
            self._replace_guard(begin)
        else:self._open_project({'path':path,'recovery':True})

    def _invalidate_assets(self):
        # Separate from document lifetime/save generation and media playback epoch.
        self._asset_epoch=getattr(self,'_asset_epoch',0)+1
        self._entry_reset(new=False)
        self._alignment_preview.clear()
        self._allocation={};self._allocation_context=None
        self._api_resume=None;self._api_resume_context=None

    def _isolate_asset_candidates(self):
        p=self.document.project
        old=p.settings.get('historical_asset_candidate_ids',[])
        ids=list(dict.fromkeys(old+[r['id'] for r in self.document.source_candidates]))
        if ids:p.settings['historical_asset_candidate_ids']=ids
        if self._candidate_source!='main':self.switchCandidateSource('main')

    def _clear_input(self,payload):
        """Clear a proven untouched seed, otherwise explicitly detach or replace."""
        if getattr(self,'_mode_switch_pending',False):return
        role=payload.get('role')
        keys={'media':('video','audio'),'translation':('translation',),'source':('ktv',)}
        if role not in keys:raise ValueError('请选择要清除的素材。')
        if self._task.get('running') or self._candidate_job:
            raise ValueError('素材处理中，请先等待完成或取消任务。')
        if self._editor_active or self._editor_composing:raise ValueError('请先完成当前文字输入。')
        project=self.document.project
        if not any(project.inputs.get(key) for key in keys[role]) and not (role=='source' and project.settings.get('local_alignment_source')):return
        self._entry_observe_main()
        pristine=bool(self._entry_session['eligible'] and not any(
            m.human_edited or m.confirmed or m.locked for m in project.mappings))
        choice='clear' if pristine or not project.mappings else None
        identity,revision=self.document.id,self.document.revision
        if choice is None:
            from .dialogs import choose_asset_clear
            self._mode_switch_pending=True
            try:choice=choose_asset_clear()
            finally:self._mode_switch_pending=False
            if choice=='cancel':return
            if self.document.id!=identity or self.document.revision!=revision or self._task.get('running') or self._candidate_job:
                raise ValueError('确认期间工程已改变，请重新选择。')
            if choice=='new':
                self._replace_guard(self._new);return
            if choice!='detach':return
        virtual_duration=self._virtual_timeline_duration() if role=='media' else None
        # Archive is recoverable in the document, in addition to session Undo.
        from .timeline_views import archive_main,archives
        from .asset_evidence import check_archive_capacity,archive_evidence,detach_ocr_evidence
        check_archive_capacity(project)
        self.history.push(project,'清除素材选择')
        archive_evidence(project,'解绑素材前的完整证据')
        self._isolate_asset_candidates()
        rows=project.project_timeline(detached=True)
        if choice=='clear':
            if role=='media':rows=[]
            elif role=='source':rows=[m for m in rows if m.method!='source_import']
            elif role=='translation':rows=[m for m in rows if not m.translation_ids]
        for row in rows:
            if row.text_override is None and not row.translation_ids:
                if role=='source' and row.text_role=='source_import':
                    lookup={item.id:item.text for item in project.lyrics}
                    row.text_override='\n'.join(lookup[i] for i in row.lyric_ids if i in lookup)
                elif role=='media' and row.text_role=='source_ocr':
                    lookup={item.id:item.text for item in project.visuals}
                    row.text_override='\n'.join(lookup[i] for i in row.visual_ids if i in lookup)
            if role=='translation':
                row.text_override=mapping_text(project,row)
                row.translation_ids=[];row.text_segments=[]
            elif role=='source':row.lyric_ids=[]
            elif role=='media':row.visual_ids=[]
        project.replace_timeline(rows)
        if role=='translation':project.translations=[]
        elif role=='source':
            project.lyrics=[];project.settings.pop('local_alignment_source',None)
        elif role=='media':detach_ocr_evidence(project)
        surviving={r.id for r in rows}
        project.settings['manual_locked_ids']=[i for i in project.settings.get('manual_locked_ids',[]) if i in surviving]
        project.settings['pending_text_splits']=[x for x in project.settings.get('pending_text_splits',[]) if x.get('left') in surviving and (not x.get('right') or x['right'] in surviving)]
        for key in keys[role]:
            project.inputs.pop(key,None);project.settings.get('input_hashes',{}).pop(key,None)
        if role=='media':
            self.media_controller.clear();self._clear_audio_analysis()
            project.settings['roi']=None;project.video.clear()
            project.settings['media_detached_with_timing']=any(valid_time(row.start,row.end) for row in rows)
            self._ensure_virtual_timeline(virtual_duration)
        if choice=='detach':self._entry_mode=self.document.entry_mode='edit';self.stage='review'
        self._invalidate_assets()
        if self.selected not in surviving:self.selected=''
        self._multi_selected.intersection_update(surviving)
        self._changed()
        self._message('素材已解绑；历史证据已隔离，保留内容可继续编辑。本操作可撤销，磁盘原文件未删除。')

    def _copy_manual_timeline(self):
        from .timeline_views import archive_main,archives
        p=self.document.project
        if not any(valid_time(row.start,row.end) for row in p.mappings):
            raise ValueError('当前主轴没有有效时间，请先导入或生成时间轴。')
        if len(archives(p))>=8:raise ValueError('已有 8 套保留时间轴，请新建工程后再添加；现有归档完整保留，另存副本不会释放容量。')
        self.history.push(p,'复制为手动时间轴')
        archive_main(p,'复制前的主时间轴')
        p.settings['main_timeline_kind']='manual-copy'
        self._entry_session['eligible']=False
        self._changed()
        self._message('已复制为可编辑的手动时间轴，原时间轴保留为只读视图；切换查看不会应用或覆盖另一套时间。可撤销此次复制。')

    def _import_text(self,payload):
        self._entry_observe_main()
        role=payload.get('role')
        if role not in ('translation','source'):raise ValueError('请指定导入文本的类型（中文字幕或演唱原文）。')
        audio_pool = role == 'translation' and self.document.entry_mode == 'audio' and payload.get('poolOnly') is True
        path=self._path(payload,title='导入中文' if role=='translation' else '导入原文',category='import',filter='文本/字幕 (*.txt *.srt *.lrc *.ass *.ssa)')
        if not path:return
        revision=self.document.revision
        source_seed_context=self._entry_capture_source_import() if role=='source' else None
        def work(cancel,progress):
            from lyricsync.subtitles import import_translation,import_original
            from lyricsync.storage import file_hash
            return {'parsed':import_translation(path) if role=='translation' else import_original(path),'hash':file_hash(path,cancel)}
        def completed(bundle,error):
            if error:raise error
            if self.document.revision!=revision:raise ValueError('导入期间工程有新改动，请重新导入。')
            result=bundle['parsed'];p=deepcopy(self.document.project)
            source_seed=self._entry_source_import_seed(source_seed_context,result[0]) if role=='source' else []
            timed_import=role=='translation' and not audio_pool and any(valid_time(row.start,row.end) for row in result)
            pool_import = audio_pool or (role == 'translation' and not timed_import and any(
                row.start is not None or row.end is not None or row.text_role != 'translation'
                or row.human_edited or row.confirmed or row.locked or row.ignored
                or row.method != 'import' or row.text_override is not None
                for row in p.mappings
            ))
            if timed_import and p.mappings:
                from .timeline_views import archives
                if len(archives(p))>=8:raise ValueError('已有 8 套保留时间轴，请新建工程后再导入；现有归档完整保留，另存副本不会释放容量。原主轴未改动。')
            if not (p.inputs.get('video') or p.inputs.get('audio')) and (role=='translation' or source_seed):
                if any(t is not None and t>86400 for row in (result if role=='translation' else source_seed) for t in (row.start,row.end)):
                    raise ValueError('纯时间轴模式下导入的字幕时长不能超过 24 小时。')
            if role=='translation':
                if timed_import and p.mappings:
                    from .timeline_views import archive_main
                    archive_main(p,'导入前的主时间轴')
                    # These identities belong to the archived axis. Keeping
                    # them on new rows is invalid; undo restores the full copy.
                    p.settings.pop('pending_text_splits',None)
                    p.settings.pop('manual_locked_ids',None)
                if pool_import:
                    previous = {item.id: item for item in p.translations}
                    if any(item.id in previous and previous[item.id] != item for item in result):
                        raise ValueError('译文标识与已有内容冲突，请检查导入文件。')
                    added = [item for item in result if item.id not in previous]
                    if not added:
                        self._message('这份译文已在译文池中，可打开「与翻译配对」。')
                        return
                    p.translations.extend(added)
                    p.settings['untimed_translation_pool'] = True
                    if audio_pool: p.settings['audio_translation_pool'] = True
                else:
                    p.settings.pop('untimed_translation_pool', None)
                    p.settings.pop('audio_translation_pool', None)
                    p.translations=result;p.mappings=[Mapping('map-'+uuid.uuid4().hex,[t.id],start=t.start,end=t.end,method='import') for t in result]
                p.inputs['translation']=path
                if timed_import:p.settings['main_timeline_kind']='timed-import'
            else:
                lyrics,warnings=result;retained={i for m in p.mappings for i in m.lyric_ids};new_ids={l.id for l in lyrics}
                p.lyrics=lyrics+[l for l in p.lyrics if l.id in retained and l.id not in new_ids];p.warnings=warnings;p.inputs['ktv']=path
                if source_seed:p.mappings=source_seed
                old_hash=p.settings.get('input_hashes',{}).get('ktv')
                if old_hash and old_hash!=bundle['hash']:
                    p.warnings.append('演唱原文文件已更新，请重新核对词句对应关系。')
                    for mapping in p.mappings:mapping.confirmed=False
                    p.settings['parser_acknowledged']=False
            if (role=='translation' and not pool_import) or source_seed:
                from lyricsync.importing import apply_imported_appearance
                apply_imported_appearance(p,[row.id for row in p.mappings])
            p.settings.setdefault('input_hashes',{})['translation' if role=='translation' else 'ktv']=bundle['hash']
            p=self.document.validate_project(p)
            self.history.push(self.document.project,'导入'+('中文' if role=='translation' else '原文'))
            self.document.project=p;self.editor.project=p
            if not (p.inputs.get('video') or p.inputs.get('audio')):self._ensure_virtual_timeline()
            self._entry_accept_import()
            self._changed()
            if pool_import:
                self._message(f'已将 {len(added)} 句加入译文池，主轴保留 {len(p.mappings)} 句。请打开「与翻译配对」核对并应用。')
            else:
                self._message(('原文已放入主时间轴，' if source_seed else '')+'导入完成（若 LRC 末句缺少出点请手动补齐）。')
        self._start_job('导入文本',work,completed,diagnostic_stage='import')

    def _relocate(self,payload):
        current=dict(self.document.project.inputs)
        if not current:raise ValueError('当前工程没有关联的外部素材文件。')
        paths=payload.get('paths')
        if paths is None:
            paths={}
            for role,previous in current.items():
                path,_=QFileDialog.getOpenFileName(None,'重新选择素材文件：'+role,str(Path(previous).parent),'所有文件 (*)')
                if not path:return
                paths[role]=path
        if not isinstance(paths,dict) or any(not isinstance(v,str) for v in paths.values()):raise ValueError('素材路径格式无效。')
        snapshot=Project.from_dict(self.document.project.to_dict());revision=self.document.revision
        def work(cancel,progress):
            from lyricsync.storage import relocate_inputs
            if cancel():raise InterruptedError('已取消重新定位')
            relocate_inputs(snapshot,paths)
            if cancel():raise InterruptedError('已取消重新定位')
            return snapshot.inputs
        def completed(result,error):
            if error:raise error
            if self.document.revision!=revision:raise ValueError('重新关联素材期间工程有改动，请重试。')
            self.history.push(self.document.project,'重新定位素材');self.document.project.inputs=result;self._changed()
            media_path=result.get('video') or result.get('audio')
            if media_path:self.media_controller.open(media_path)
            self._message('素材校验一致，文件路径已更新。')
        self._start_job('校验并关联素材',work,completed)

    def _run_ocr(self,payload=None):
        from lyricsync.pipeline import run
        from lyricsync.candidate_tracks import capture_source,candidate_from_project
        if not self.document.project.inputs.get('video'):raise ValueError('请先导入视频。')
        roi=self.document.project.settings.get('roi')
        width=self.document.project.video.get('width',0);height=self.document.project.video.get('height',0)
        multi_region=self.document.project.settings.get('ocr_multi_region') is True
        if multi_region:
            from lyricsync.multi_roi import validate_regions
            validate_regions(self.document.project.settings.get('ocr_regions'), width, height)
        elif not self._valid_roi(roi, width, height):
            raise ValueError('请先框选画面中的字幕识别区域。')
        if self._candidate_job:
            raise ValueError('OCR 识别任务正在运行中，请稍候。')
        detached=deepcopy(self.document);index=deepcopy(self.media_controller.index)
        checkpoint=self.storage_dir/(self.document.id+'.pipeline.lyricsync.json')
        def work(cancel,progress):
            captured=capture_source(detached,'ocr',index,cancel=cancel)
            project=Project.from_dict(detached.project.to_dict())
            project.replace_timeline([])
            project.visuals = []
            project.video.pop('ocr_metrics', None)
            enabled=project.settings.get('api_enabled',False);project.settings['api_enabled']=False
            cancelled=False
            try:run(project,checkpoint,progress=progress,cancel=cancel,use_imported_pools=True)
            except InterruptedError:
                cancelled=True
                if project.visuals and not project.mappings:
                    from lyricsync.ocr_correspondence import match_visual_project
                    match_visual_project(project)
            project.settings['api_enabled']=enabled
            from .entry_initialization import bind_imported_ocr_rows
            bind_imported_ocr_rows(detached.project,project)
            from lyricsync.ocr_outcome import scan_counts
            record = candidate_from_project(captured,project)
            return record, cancelled, scan_counts(project, record['rows'] if record else [])
        def completed(bundle, error):
            if error:
                self._ocr_result_notice('cancelled' if isinstance(error, InterruptedError) else 'failed',
                                        '已取消识别。' if isinstance(error, InterruptedError) else '本轮识别未能生成可核对结果：' + str(error))
                return
            record, cancelled, counts = bundle or (None, False, {})
            task = self._candidate_task
            self._candidate_task['scanCounts'] = counts
            cancelled = cancelled or self._candidate_task.get('cancelRequested', False)
            rows = record['rows'] if record else ()
            timed = [r for r in rows if valid_time(r['start'], r['end']) and r['provenance']['visual_ids']]
            if not timed:
                self._ocr_result_notice('cancelled' if cancelled else 'empty', '已取消识别，尚无可核对的歌词。' if cancelled else
                                        '选区内没有找到带时间的歌词。请查看画面，调整选区后重试。')
                return
            def report(outcome):
                if self._candidate_task is not task or self.document.id != record['document_id']:
                    return
                self._candidate_task.update(resultId=record['id'], resultDocument=self.document.id)
                counts['applied'] = outcome['applied_timed']
                counts['main_rows'] = len(self.document.main_timeline)
                if outcome['status'] == 'applied':
                    extras = sum(not r['provenance']['translation_ids'] for r in timed) if any(t.text.strip() for t in self.document.project.translations) else 0
                    self._ocr_result_notice('applied', f"已进入逐句核对，{outcome['applied_timed']} 句已落轴。" +
                                            (f'其中 {extras} 句待补译。' if extras else ''))
                    return
                actual = next((r for r in self.document.source_candidates if r['id'] == record['id']), None)
                if actual is None:
                    self._ocr_result_notice('failed', '本轮结果未能载入工程，请返回导入页重试。')
                    return
                self.stage = 'import' if cancelled and not self.document.main_timeline else 'review'
                self.switchCandidateSource('main')
                reason = {'not_pristine': '已有主轴需要保护', 'identity_mismatch': '歌词对应关系需要核对',
                          'verification_failed': '素材校验未通过', 'missing_index': '帧索引尚未就绪',
                          'stale_context': '识别期间工程发生了变化', 'duplicate': '工程中已有这份结果'}.get(outcome['reason'], '请核对识别时间')
                self._ocr_result_notice('retained', ('识别已取消，已保留部分结果' if cancelled else reason) +
                                        f'。本轮有 {len(timed)} 句带时间，可在字幕菜单重新打开。')
                if not cancelled:
                    self._open_candidate_result(actual, automatic=True)
            try:
                self._candidate_install(record, initial_complete=not cancelled, on_result=report)
            except (ValueError, RuntimeError) as exc:
                self._ocr_result_notice('failed', '本轮识别结果未能保存或载入：' + str(exc))
        self._start_candidate_job('画面字幕 OCR 识别','ocr',work,completed)

    def _ocr_result_notice(self, status, message):
        if status in ('empty', 'cancelled', 'failed') and not self._candidate_task.get('resultId'):
            self.stage = 'import'
        self._candidate_task.update(resultStatus=status, label=message, running=False)
        self.diagnostics.record('ocr-result', stage='ocr', ocr=dict(status=status, review=self.stage=='review',
                                records=len(self.document.source_candidates), **self._candidate_task.get('scanCounts', {})))
        if status == 'failed':
            self._error(message)
        else:
            self._message(message)
            self.notice.emit('info', message)

    def _protected_paths(self):
        from lyricsync.storage import protected_project_inputs
        paths=protected_project_inputs(self.document.project)
        if self.path:paths.extend([self.path,self.path+'.bak'])
        paths.extend(str(p) for p in self.storage_dir.glob(self.document.id+'.*'))
        content_hash=self.document.project.video.get('content_hash')
        if content_hash:paths.append(str(self.storage_dir/('frame-index-'+content_hash+'.json')))
        return paths

    def _protect_artifact_output(self,path):
        from lyricsync.storage import protect_output
        target=protect_output(path,self._protected_paths())
        if target.is_relative_to(self.storage_dir.resolve()):raise ValueError('不能将文件导出到软件缓存目录，请选择其他保存文件夹。')
        return str(target)

    def _review_warnings(self):
        from .dialogs import review_import_warnings
        warnings=list(self.document.project.warnings);document_id=self.document.id
        if not warnings:self._message('当前没有需要注意的导入提示。');return
        if not review_import_warnings(warnings):return
        if self.document.id!=document_id or self.document.project.warnings!=warnings:
            raise ValueError('提示内容已更新，请重新查看。')
        if not self.document.project.settings.get('parser_acknowledged',False):
            self.history.push(self.document.project,'确认导入提示')
            self.document.project.settings['parser_acknowledged']=True;self._changed()
        self._message('已标记为知悉导入提示。')

    def _export(self,payload):
        from lyricsync.export import export_project
        fmt=payload.get('format','ass')
        if fmt not in ('ass','srt','lrc'):raise ValueError('不支持的字幕格式。')
        from lyricsync.speakers import has_speaker_styles
        if fmt != 'ass' and has_speaker_styles(self.document.project):
            from PySide6.QtWidgets import QMessageBox
            answer=QMessageBox.question(None,'导出格式不保留样式',
                'SRT/LRC 不保存说话人、字体或颜色。工程中的设置仍保留；要保留这些信息请导出 ASS。继续纯文本导出？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Yes:self._closing=False;return
        source_text=any(row.text_role!='translation' for row in self.document.main_timeline)
        path=self._path(payload,True,'导出原文歌词字幕' if source_text else '导出中文字幕',
                        f'{fmt.upper()} 字幕 (*.{fmt})', f'{"歌词" if source_text else "中文"}.{fmt}')
        if not path:self._closing=False;return
        path=self._protect_artifact_output(path)
        if Path(path).suffix.casefold()!='.'+fmt:raise ValueError('字幕扩展名与所选格式不一致。')
        partial=self._boolean(payload,'skipMissingTimes',False);draft=self._boolean(payload,'draft',False) or partial
        overwrite=self._boolean(payload,'overwrite',False) or (not payload.get('path') and Path(path).exists())
        if partial:
            report=Path(path).with_name(Path(path).stem+'_遗漏清单.json');self._protect_artifact_output(report)
            if report.exists() and not overwrite:raise ValueError('同名遗漏清单文件已存在，请更换导出文件名。')
        snapshot=Project.from_dict(self.document.project.to_dict())
        snapshot.inputs.update({f'_protected_export_{n}':p for n,p in enumerate(self._protected_paths())})
        index=self.media_controller.index;revision=self.document.revision
        def completed(result,error):
            if error:self._closing=False;raise error
            if not draft:self.document.mark_exported(revision)
            self._message(('已跳过 '+str(len(result))+' 句，草稿' if partial else '草稿' if draft else '正式')+'字幕已导出：'+Path(path).name)
            if self._closing:self._resume_close()
        self._start_job('导出字幕',lambda cancel,progress:export_project(snapshot,path,overwrite=overwrite,draft=draft,skip_missing_times=partial,frame_index=index),completed)

    def _convert(self,payload):
        from lyricsync.export import convert_ktv
        source=payload.get('source') or self._path({},title='选择 KTV 字幕文件',filter='ASS (*.ass *.ssa)')
        if not source:return
        target=payload.get('target') or self._path({},True,'保存转换后的 ASS 字幕','ASS (*.ass)','转换.ass')
        if not target:return
        target=self._protect_artifact_output(target)
        if Path(target).suffix.casefold()!='.ass':raise ValueError('转换字幕请使用 .ass 扩展名。')
        self._start_job('转换 KTV 字幕',lambda cancel,progress:convert_ktv(source,target,no_overlap=bool(payload.get('noOverlap',False)),overwrite=bool(payload.get('overwrite',False)) or (not payload.get('target') and Path(target).exists())),lambda result,error:self._error(str(error)) if error else self._message('KTV 字幕转换完成。'))

    def configure_api(self,settings,key,enabled,persist_key=False,auto_review=None):
        from lyricsync import ai_config
        from lyricsync.credentials import KeyStore
        safe=ai_config.normalized(settings);scope=ai_config.credential_scope(safe)
        next_key=self._api_key if scope==self._api_scope else ''
        if key:
            if len(key)>2560:raise ValueError('密钥过长。')
            next_key=key.strip()
            if persist_key:KeyStore().save(scope,next_key)
        ai_config.save(self.storage_dir,safe)
        self._api_key=next_key;self._api_scope=scope;self.api_settings=safe
        self.history.push(self.document.project,'修改 AI 接口设置')
        self.document.project.settings.update(safe)
        # Privacy consent is session-local and endpoint-specific. A project file
        # (including a legacy api_enabled flag) cannot authorize transmission.
        if enabled:self._api_text_consents.add(scope)
        else:self._api_text_consents.discard(scope)
        if auto_review is not None:self.document.project.settings['api_auto_review']=bool(auto_review)
        self.api_state['status']='已授权向当前 AI 服务商发送字幕文本' if enabled else '首次调用前会询问授权'
        self._changed()

    def load_api_credential(self,settings):
        from lyricsync.ai_config import credential_scope
        from lyricsync.credentials import KeyStore
        scope=credential_scope(settings);self._api_key=KeyStore().get(scope);self._api_scope=scope
        self.api_state['status']='已读取已保存的 API 密钥' if self._api_key else '当前服务商没有已保存的 API 密钥'
        self._refresh(False)

    def delete_api_provider(self):
        import os
        from lyricsync import ai_config
        from lyricsync.credentials import KeyStore
        if os.name=='nt':ai_config.delete_provider(self.storage_dir,self.api_settings,KeyStore())
        else:ai_config.save(self.storage_dir,ai_config.DEFAULTS)
        self._api_key='';self._api_scope='';self.api_settings=dict(ai_config.DEFAULTS)
        self._api_text_consents.clear()
        self.document.project.settings.update(self.api_settings,api_enabled=False,api_auto_review=False)
        self.api_state={'status':'已重置服务商并清除保存的密钥','models':[]};self._changed()

    def api_text_consent(self,settings=None):
        from lyricsync import ai_config
        return ai_config.credential_scope(settings or self.api_settings) in self._api_text_consents

    def _api_check(self,enabled=True):
        from lyricsync import ai_config
        ai_config.validate(self.api_settings)
        scope=ai_config.credential_scope(self.api_settings)
        if not self._api_key or self._api_scope!=scope:raise ValueError('请先在 AI 设置中填写或读取当前服务商的 API 密钥。')
        if enabled and not self.api_text_consent():
            from .dialogs import confirm_text_api_consent
            if not confirm_text_api_consent(self.api_settings):
                raise ValueError('已取消授权，本次未发送 AI 请求。')
            self._api_text_consents.add(scope)


    def _record_api_usage(self,records):
        if records==self.document.project.api_records:return
        previous=self.document.revision;self.document.project.api_records=deepcopy(records);self.document.touch()
        if self._layout_revision==previous:self._layout_revision=self.document.revision
        self._autosave_status='已更新调用计数，等待自动备份';self.autosave_timer.start();self._refresh(False)

    def _api_job(self,label,operation,callback,resume=None):
        """Per-document ledger, worker-owned connection; count failures and persist."""
        records=deepcopy(self.document.project.api_records)
        cache_path=self.storage_dir/(self.document.id+'.api.cache.sqlite')
        key=self._api_key;settings=deepcopy(self.api_settings)
        def work(cancel,progress):
            from lyricsync.storage import Cache
            from lyricsync.api_client import BudgetLimit,ApiRequestError
            cache=Cache(cache_path)
            try:
                cache.restore_records(records)
                try:result=operation(cache,key,settings,cancel,progress)
                except BudgetLimit as exc:result=exc.state
                except Exception as exc:
                    result={'status':'failed','message':str(exc) if isinstance(exc,(ApiRequestError,ValueError,InterruptedError)) else 'AI 请求失败：'+type(exc).__name__}
                return {'result':result,'records':cache.records()}
            finally:cache.close()
        def completed(bundle,error):
            if error:raise error
            self._record_api_usage(bundle['records'])
            result=bundle['result']
            if isinstance(result,dict) and result.get('status')=='budget_exhausted':
                self.api_state.update(status=result['message'],budget=result)
                self._set_api_resume(resume)
                self._refresh(False)
                if getattr(callback, '_receive_terminal_status', False):callback(result)
                self._budget_dialog(result)
            else:callback(result)
        self._start_job(label,work,completed,diagnostic_stage='ai')
        self._task['api']=True;self._refresh(False)

    def _budget_dialog(self,result):
        from PySide6.QtWidgets import QMessageBox
        box=QMessageBox();box.setTextFormat(Qt.TextFormat.PlainText);box.setWindowTitle('AI 调用次数已达上限')
        box.setText(result.get('message','已达到单次工程的 AI 调用次数上限。'))
        box.setInformativeText(f"当前已调用 {result.get('used', 0)} 次（含失败与重试）。临时增加额度仅对本次生效，不会改动默认上限。")
        more=box.addButton('临时提高到 100 次并继续',QMessageBox.ButtonRole.AcceptRole) if result.get('limit',100)<100 and result.get('used',100)<100 and self._api_resume else None
        later=box.addButton('暂不继续',QMessageBox.ButtonRole.RejectRole)
        # Enter on a newly opened notice must not silently expand a paid budget.
        box.setDefaultButton(later);box.setEscapeButton(later)
        from .native_theme import apply_dialog_theme
        apply_dialog_theme(box);box.exec()
        if more is not None and box.clickedButton() is more:self._resume_api()

    def _set_api_resume(self,callback):
        self._api_resume=callback;self._api_resume_context=(self.document.id,self.epoch,self.document.revision)

    def _resume_api(self):
        callback=self._api_resume;context=getattr(self,'_api_resume_context',None);self._api_resume=None
        if callback is None:raise ValueError('当前没有可继续的 AI 任务。')
        if context!=(self.document.id,self.epoch,self.document.revision):raise ValueError('字幕内容已变动，请重新发起 AI 核对。')
        callback(100)

    def _api_simple(self,name,payload,maximum=None):
        from lyricsync.api import fetch_models,test_connection
        self._api_check(False)
        maximum=self.api_settings.get('api_max_calls',50) if maximum is None else maximum
        def operation(cache,key,settings,cancel,progress):
            return fetch_models(cache,key,settings=settings,maximum=maximum,cancel=cancel) if name=='api.models' else test_connection(cache,key,settings=settings,maximum=maximum,cancel=cancel)
        def completed(result):
            if name=='api.models':
                if result.get('status')=='success':self.api_state['models']=result.get('models',[])
                self.api_state['status']='已获取模型列表，也可手动输入模型名称' if result.get('status')=='success' else result.get('message','未能获取模型列表，请手动输入模型名称')
            else:self.api_state['status']=result.get('message','连接测试完成')
            self._refresh(False)
        self._api_job('获取模型列表' if name=='api.models' else '连接测试',operation,completed,
                      resume=lambda limit:self._api_simple(name,payload,limit))

    def _api_reference(self,payload,maximum=None):
        from lyricsync.api import translate_reference,review_signature
        self._api_check();row=self._row(payload.get('id'));snapshot=next(r for r in self._all_rows if r['id']==row.id)
        text=snapshot['source'] or snapshot['ocr'];identity=row.id;signature=review_signature(self.document.project,row)
        if not text.strip():raise ValueError('当前句没有参考原文或 OCR 识别文字，无法生成参考译文。')
        def completed(result):
            if not isinstance(result,dict) or 'translation' not in result:
                self._error(result.get('message','参考翻译未完成。'));return
            current=next((r for r in self._all_rows if r['id']==identity),{})
            current_row=next((m for m in self.document.project.mappings if m.id==identity),None)
            if current_row is None or review_signature(self.document.project,current_row)!=signature or (current.get('source') or current.get('ocr'))!=text:
                self._message('该句原文已变动，本次参考译文未保存。');return
            if any(m.id==identity for m in self.document.project.mappings):
                self.document.project.settings.setdefault('reference_translations',{})[identity]={**result,'context_signature':signature,'source_text':text}
                self._changed();self._message('已生成本句参考译文。')
        self._api_job('单句参考翻译',lambda cache,key,settings,cancel,progress:translate_reference(text,identity,cache,key,settings=settings,maximum=settings.get('api_max_calls',50) if maximum is None else maximum,retries=settings.get('api_retries',3),cancel=cancel),completed,
                      resume=lambda limit:self._api_reference(payload,limit))

    def _api_review(self,payload,maximum=None):
        from lyricsync.api import suggest,review_signature
        # api.review without an id means the full song, regardless of selection.
        identity=payload.get('id') or None
        if identity:self._row(identity)
        from lyricsync.api import require_translation_targets
        require_translation_targets(self.document.project,[identity] if identity else None)
        if not self.document.project.mappings:raise ValueError('当前工程没有可核对字幕。')
        self._api_check();snapshot=Project.from_dict(self.document.project.to_dict());index=self.media_controller.index
        def operation(cache,key,settings,cancel,progress):
            try:
                suggest(snapshot,cache,key,settings=settings,index=index,temporary_maximum=maximum,
                        cancel=cancel,mapping_ids=[identity] if identity else None,manual=True,progress=progress)
            except InterruptedError:pass  # Coverage records cancellation; completed evidence is retained.
            return {'status':'review_result','suggestions':snapshot.settings.get('api_suggestions',[]),
                    'coverage':snapshot.settings.get('api_coverage',{}),'resume':snapshot.settings.get('api_resume')}
        def completed(result):
            if result.get('status')!='review_result':self._error(result.get('message','AI 词句核对未完成。'));return
            # Replace only this run's rows. Other rows can have newer saved evidence.
            coverage=result['coverage'];scope=set(coverage.get('scope_mapping_ids',[]))
            suggestions=deepcopy(result['suggestions'])
            proposals=[proposal for proposal in suggestions if proposal.get('mapping_id') in scope]
            for proposal in proposals:
                row=next((m for m in self.document.project.mappings if m.id==proposal['mapping_id']),None)
                if proposal.get('context_signature') and (row is None or review_signature(self.document.project,row)!=proposal['context_signature']):proposal['status']='stale'
            current=self.document.project.settings.get('api_suggestions',[])
            self.document.project.settings['api_suggestions']=[r for r in current if r.get('mapping_id') not in scope]+proposals
            self.document.project.settings['api_coverage']=coverage;self._changed()
            summary=self._review_summary(coverage)
            self._task.update(label=summary,progress=coverage.get('processed',0)/max(1,coverage.get('total',0)))
            self.api_state['status']=summary;self._message(summary)
            from .dialogs import show_text
            from lyricsync.api import format_review_suggestions
            show_text('AI 词句核对结果',summary+'\n\n'+format_review_suggestions(self.document.project,proposals,self.media_controller.index),owner=self,modeless=True)
            if result.get('resume') and coverage.get('status')=='budget_exhausted':
                self._set_api_resume(lambda limit:self._api_review(payload,limit));self._budget_dialog(result['resume'])
        self._api_job('AI 单句核对' if identity else 'AI 全文核对',operation,completed,resume=lambda limit:self._api_review(payload,limit))

    @staticmethod
    def _review_summary(coverage):
        labels={'completed':'核对已完成','cancelled':'核对已取消','failed':'核对已停止',
                'partial':'部分核对完成','budget_exhausted':'已达调用上限，核对暂停'}
        status=coverage.get('status','partial')
        summary=(labels.get(status,'核对未完成')+(f"：已核对 {coverage.get('processed', 0)}/{coverage.get('total', 0)} 句（失败 {coverage.get('failed', 0)} 句，待核对 {coverage.get('pending', 0)} 句）。"
                                                                                                           ))
        if coverage.get('diagnostics'):
            from lyricsync.api_client import diagnostic_message
            summary+=' '+diagnostic_message(coverage['diagnostics'])
        if coverage.get('error'):summary+=' '+str(coverage.get('error_message') or coverage['error'])
        if status!='completed':summary+=' 已完成的结果已保存，可再次点击继续核对。'
        return summary


    def _pairing(self,payload):
        row=self._row(payload.get('id'))
        if 'lyricIds' not in payload and 'visualIds' not in payload:
            from .pairing_workbench import pairing_dialog
            pairing_dialog(self).exec();return
        lyrics=payload.get('lyricIds',row.lyric_ids);visuals=payload.get('visualIds',row.visual_ids)
        for values,allowed in ((lyrics,{l.id for l in self.document.project.lyrics}),(visuals,{v.id for v in self.document.project.visuals})):
            if not isinstance(values,list) or any(not isinstance(v,str) or v not in allowed for v in values):raise ValueError('关联的原文或 OCR 记录无效。')
        if lyrics==row.lyric_ids and visuals==row.visual_ids:return
        self.history.push(self.document.project,'修改原文/OCR 关联');row.lyric_ids=list(dict.fromkeys(lyrics));row.visual_ids=list(dict.fromkeys(visuals));self.editor.changed(row)

    def _rescan(self,payload):
        from lyricsync.rescan import rescan_cue
        from lyricsync.api import review_signature
        row=self._row(payload.get('id'));identity=row.id;signature=review_signature(self.document.project,row)
        snapshot=Project.from_dict(self.document.project.to_dict());cache_path=self.storage_dir/(self.document.id+'.ocr.cache.sqlite')
        window=payload.get('window')
        if window is None and not valid_time(row.start,row.end):
            index=self.media_controller.index;current=self.media_controller.snapshot()['displayedFrame']
            if index is None:raise ValueError('该句尚未打轴，请先将视频停在对应画面，再重新识别。')
            stamp=index.stamp(max(1,current));window=[max(0,stamp-2),min(index.duration,stamp+2)]
        def work(cancel,progress):
            from lyricsync.storage import Cache
            cache=Cache(cache_path)
            try:return rescan_cue(snapshot,identity,cache,window=window,progress=progress,cancel=cancel)
            finally:cache.close()
        def completed(result,error):
            if error:raise error
            live=self._row(identity)
            if review_signature(self.document.project,live)!=signature:raise ValueError('识别期间该句内容已变动，请重试。')
            self.document.project.settings.setdefault('cue_rescans',{})[identity]=result;self._changed()
            from .dialogs import choose_rescan
            choose_rescan(self,identity,result)
        self._start_job('单句重新识别',work,completed)

    def _rematch(self):
        if any(row.text_role != 'translation' for row in self.document.main_timeline):
            raise ValueError('当前主时间轴包含原文歌词，「重新配对」仅适用于中文字幕与原文的对应。')
        from lyricsync.matching import match_project
        from lyricsync.candidate_tracks import capture_source,candidate_from_project,candidate_project,verify_candidate_source
        detached=deepcopy(self.document);index=deepcopy(self.media_controller.index)
        from .timeline_views import current_candidate
        record=next((r for r in reversed(detached.source_candidates) if r['source']=='ocr' and current_candidate(detached,r)),None)
        def work(cancel,progress):
            captured=capture_source(detached,'ocr',index,cancel=cancel)
            if record:verify_candidate_source(detached,record,index,current_provenance=captured['provenance'])
            project=candidate_project(record,detached.project) if record else Project.from_dict(detached.project.to_dict())
            project.translations=deepcopy(detached.project.translations)
            match_project(project)
            if cancel():raise InterruptedError('已取消重新配对')
            return candidate_from_project(captured,project)
        def completed(record,error):
            if error:raise error
            self._candidate_install(record)
            self._message('重新配对完成，结果已保存至「OCR 结果」，可在对比后应用到主轴。')
        self._start_job('OCR rematch candidate',work,completed,foreground=False)

    def _ocr_settings(self,start_after=False):
        # Resolve devices first, then ask. No OCR worker exists before acceptance.
        if getattr(self,'_ocr_settings_pending',False):
            raise ValueError('OCR 设置已打开，请先完成或取消当前设置。')
        if self._candidate_job or self._task.get('running'):
            raise ValueError('当前已有任务运行，请等待完成或取消后再设置 OCR。')
        if start_after:
            p=self.document.project
            if not p.inputs.get('video'):raise ValueError('请先导入视频。')
            if p.settings.get('ocr_multi_region') is True:
                from lyricsync.multi_roi import validate_regions
                validate_regions(p.settings.get('ocr_regions'), p.video.get('width', 0), p.video.get('height', 0))
            elif not self._valid_roi(p.settings.get('roi'),p.video.get('width',0),p.video.get('height',0)):
                raise ValueError('请先框选画面中的字幕识别区域。')
        identity,epoch=self.document.id,self.epoch
        source=deepcopy(self.document.project.inputs)
        self._ocr_settings_pending=True
        def work(cancel,progress):
            import onnxruntime
            if cancel():raise InterruptedError('已取消设备检测')
            return onnxruntime.get_available_providers()
        def completed(providers,error):
            try:
                if error:raise error
                if identity!=self.document.id or epoch!=self.epoch or source!=self.document.project.inputs:
                    return
                from .dialogs import ocr_settings
                accepted=ocr_settings(self,providers,start_after=start_after)
                if accepted and start_after:
                    if identity!=self.document.id or epoch!=self.epoch or source!=self.document.project.inputs:
                        raise ValueError('设置期间工程或素材发生变化，未启动 OCR，请重新确认。')
                    self._run_ocr()
            finally:
                self._ocr_settings_pending=False;self._ocr_settings_job=None
        try:self._ocr_settings_job=self._start_job('检测 OCR 运行设备',work,completed)
        except Exception:
            self._ocr_settings_pending=False
            raise

    def _api_alignment(self,payload,one=False,maximum=None,*,force_retry=None):
        from lyricsync.api_alignment import request_plan,build_payload
        from .api_review_dialogs import request_alignment_scope,show_alignment_plan
        index=self.media_controller.index
        if index is None:raise ValueError('请先载入视频并等待帧索引就绪，再获取 AI 拆合建议。')
        scope_payload=dict(payload)
        if one:
            identity=self._row(payload.get('id')).id
            number=self._all_rows[self._row_positions[identity]]['number']
            scope_payload.update(firstNumber=number,lastNumber=number)
        scope=request_alignment_scope(self.document.project,scope_payload,self.selected,index=index)
        if scope is None:return
        build_payload(self.document.project,index,scope['mapping_ids'])
        from .reference_scope_dialog import request_reference_scope
        reference_ids = request_reference_scope(self.document.project, payload)
        if reference_ids is False:return  # Explicit cancellation, no request.
        self._api_check()
        snapshot=Project.from_dict(self.document.project.to_dict())
        resume_payload={**payload,'firstNumber':scope['firstNumber'],'lastNumber':scope['lastNumber']}
        if reference_ids is not None:resume_payload['referenceIds']=reference_ids
        def operation(cache,key,settings,cancel,progress):
            return request_plan(snapshot,index,cache,key,mapping_ids=scope['mapping_ids'],reference_ids=reference_ids,force_retry=force_retry,
                                settings={**settings,'api_enabled':True},temporary_maximum=maximum,cancel=cancel,progress=progress)
        def completed(result):
            if result.get('plan'):
                from lyricsync.alignment_candidate import candidate_record
                # A late/stale response must not replace a still useful candidate.
                record=candidate_record(self.document.project,self.media_controller.index,result['plan'])
                self._set_alignment_preview(False)
                self._alignment=result;self._alignment_selection=None
                self.document.alignment_candidate=record;self.document.candidate_warning=''
                self._changed()
                show_alignment_plan(self,result['plan'])
                if result.get('coverage'):self._message(result['message'])
                if result.get('resume'):
                    self._set_api_resume(lambda limit:self._api_alignment(resume_payload,False,limit))
                    self._budget_dialog(result['resume'])
            else:self._message(result.get('message','本次没有需要拆分或合并的调整建议。'))
            from .force_retry_dialog import remember
            remember(self,result.get('force_retry_offer'),resume_payload,maximum)
        self._alignment_retry=None
        self._api_job('获取 AI 拆合建议',operation,completed,resume=lambda limit:self._api_alignment(resume_payload,False,limit))

    def _alignment_state(self):
        """Validate once per document revision/index; reopening starts on original."""
        index=self.media_controller.index
        context=(id(self.document),self.document.revision,id(index),id(self._alignment.get('plan')),
                 id(self.document.alignment_candidate),self.document.candidate_warning)
        if context==self._alignment_check_context:return self._alignment_checked
        plan=self._alignment.get('plan');record=self.document.alignment_candidate
        state={'available':False,'hasCandidate':bool(plan or record or self.document.candidate_warning),'reason':'尚未获取 AI 拆合建议。'}
        try:
            if self.document.candidate_warning:
                state['reason']=self.document.candidate_warning
            elif plan or record:
                if index is None:raise ValueError('已有保存的 AI 建议，请先载入原视频并等待帧索引就绪。')
                if plan is None:
                    from lyricsync.alignment_candidate import restore_candidate
                    plan=restore_candidate(self.document.project,index,record);self._alignment={'plan':plan}
                from lyricsync.api_alignment import validate_plan
                validate_plan(self.document.project,plan,index)
                summary=plan['summary'];state['available']=bool(plan['diff'])
                state['reason']=(f"拆分 {summary['split_count']} 项 · 合并 {summary['merge_count']} 项 · 尚未应用"
                                 if state['available'] else '当前字幕无需拆分或合并，可查看 AI 核对说明。')
                if plan.get('coverage'):
                    from lyricsync.api_alignment_batches import coverage_message
                    state['reason']+=' '+coverage_message(plan['coverage'])
        except (ValueError,TypeError,KeyError,AttributeError,IndexError) as exc:
            state['reason']=str(exc)
        self._alignment_check_context=(id(self.document),self.document.revision,id(index),id(self._alignment.get('plan')),
                                       id(self.document.alignment_candidate),self.document.candidate_warning)
        self._alignment_checked=state
        return state

    def _discard_alignment(self):
        self._set_alignment_preview(False)
        had_saved=self.document.alignment_candidate is not None or bool(self.document.candidate_warning)
        self._alignment={};self._alignment_selection=None
        self.document.alignment_candidate=None;self.document.candidate_warning=''
        if had_saved:self._changed()
        else:self._refresh(False)

    def _set_alignment_preview(self,enabled):
        if enabled==self._alignment_preview.active:return
        if enabled:
            if self._editor_active or self._editor_composing:raise ValueError('请先完成当前文字或时间编辑，再预览 AI 建议。')
            state=self._alignment_state()
            if not state['available']:raise ValueError(state['reason'])
            plan=self._alignment.get('plan')
            old_selected=self.selected
            candidate=self._alignment_preview.activate(self.document,self.media_controller.index,plan,old_selected)
            identities={m.id for m in candidate.mappings}
            if old_selected not in identities:
                parts=[part for change in plan['diff'] if old_selected in change['mapping_ids'] for part in change['after']]
                stamp=self.media_controller.snapshot()['positionSeconds']
                self.selected=next((p['id'] for p in parts if p['start']<=stamp<p['end']),parts[0]['id'] if parts else '')
            pair=self._alignment_selection
            if pair and pair[0]==old_selected and pair[1] in identities:self.selected=pair[1]
            self._allocation={};self._allocation_context=None
        else:
            candidate_selected=self.selected;original=self._alignment_preview.clear()
            identities={m.id for m in self.document.project.mappings}
            if candidate_selected in identities:original=candidate_selected
            else:
                plan=self._alignment.get('plan',{})
                change=next((d for d in plan.get('diff',[]) if candidate_selected in {p['id'] for p in d['after']}),None)
                if change and original not in change['mapping_ids']:
                    stamp=self.media_controller.snapshot()['positionSeconds']
                    original=next((p['id'] for p in change['before'] if valid_time(p['start'],p['end']) and p['start']<=stamp<p['end']),change['mapping_ids'][0])
            self.selected=original;self._alignment_selection=(original,candidate_selected)
        self._layout_revision=-1;self._layout_stamps=[];self._layout_rows=[];self._layout_error=''
        self._hold_cue_selection=not self.media_controller.snapshot()['playing']
        self._held_playback_ids=None
        self._refresh()

    def _accept_alignment(self):
        from lyricsync.api_alignment import accept_plan
        state=self._alignment_state()
        if not state['available']:raise ValueError(state['reason'])
        plan=self._alignment.get('plan')
        locked=set(self.document.project.settings.get('manual_locked_ids',[]))
        if any(i in locked for d in plan.get('diff',[]) for i in d['mapping_ids']):raise ValueError('AI 建议涉及已锁定的字幕，请先解锁相关句再应用。')
        selected=self.selected
        parts=[part for change in plan['diff'] if selected in change['mapping_ids'] for part in change['after']]
        if parts:
            stamp=self.media_controller.snapshot()['positionSeconds']
            selected=next((part['id'] for part in parts if part['start']<=stamp<part['end']),parts[0]['id'])
        def changed():
            self._alignment_preview.clear()
            if selected in {m.id for m in self.document.project.mappings}:self.selected=selected
            self._layout_revision=-1;self._layout_stamps=[];self._layout_rows=[]
            self.document.alignment_candidate=None;self.document.candidate_warning=''
            self._alignment={};self._alignment_selection=None
            self._changed()
        result=accept_plan(self.document.project,plan,self.media_controller.index,self.history,changed)
        self._alignment={};self._message(f"已应用 {result.get('changed_count', 0)} 处拆合调整（可按撤销恢复）。")

    def _open_qc(self,payload):
        self.stage='qc'
        path=self._path(payload,title='选择成片视频',filter='视频 (*.mp4 *.mkv *.mov *.avi *.webm);;所有文件 (*)')
        if not path:return
        def completed(result,error):
            if error:raise error
            info,image,stamp=result;self._qc_preview=image
            self._qc_source_generation+=1
            # A new source must not leave clickable cue/rescan actions from an old report.
            # Previous reports remain on disk; only this current view is replaced.
            self._qc=dict(inputKind='hardsubbed',assPath=None,roiPreviewKind='finished_video',inputPath=path,width=info['width'],height=info['height'],duration=info['duration'],previewSeconds=stamp,chineseRoi=None,sourceRoi=None,completed=False,status='已载入视频，请框选字幕识别区域',mode='local',issues=[],cues=[],rescans=[],summary={},available=True)
            self._refresh(False)
        self._start_job('读取成片信息',lambda cancel,progress:self._qc_preview_frame(path,None,cancel),completed)

    @staticmethod
    def _qc_preview_frame(path,stamp,cancel):
        from lyricsync.vision import Video
        from PySide6.QtGui import QImage
        video=Video(path);actual,pixels=video.at(video.info['duration']/2 if stamp is None else stamp,cancel=cancel)
        if pixels is None:raise ValueError('无法读取视频预览画面。')
        image=QImage(pixels.data,pixels.shape[1],pixels.shape[0],pixels.strides[0],QImage.Format.Format_BGR888).copy()
        image=image.scaled(600,338,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
        return video.info,image,actual

    def qc_region_context(self):
        """Cheap main-thread identity; content hashes are verified in the worker."""
        versions=[]
        for key in ('inputPath','assPath'):
            path=self._qc.get(key) if key=='inputPath' or self._qc.get('inputKind')=='raw' else None
            try:
                stat=Path(path).stat() if path else None
                versions.append((str(path or ''),stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns) if stat else ('',))
            except OSError:versions.append((str(path),'missing'))
        return (self.epoch,self.document.id,self._qc_source_generation,self._qc.get('inputKind','hardsubbed'),tuple(versions))

    def _qc_region_job(self,label,operation,callback,*,valid=lambda:True,render=True):
        from lyricsync.audit_service import fingerprint_inputs,rendered_input
        path=self._qc.get('inputPath')
        if not path:raise ValueError('请先选择要质检的视频。')
        context=self.qc_region_context();kind=self._qc.get('inputKind','hardsubbed');ass=self._qc.get('assPath') if kind=='raw' else None
        inputs={'video':path,'ass':ass};expected=deepcopy(self._qc.get('roiInputVersions'));settings=deepcopy(self.document.project.settings)
        def work(cancel,progress):
            before=fingerprint_inputs(inputs,cancel)
            if expected and before!=expected:raise ValueError('视频或外挂字幕文件已发生变动，请重新载入并确认识别区域。')
            if cancel():raise InterruptedError('已取消操作。')
            if render:
                with rendered_input(path,ass,input_kind=kind,cancel=cancel,timeout=settings.get('render_timeout',1800),max_bytes=settings.get('render_max_bytes',8*1024**3)) as (actual,stage):
                    if kind=='raw':
                        from lyricsync.vision import Video
                        original=Video(path).info;rendered=Video(actual).info
                        if (original['width'],original['height'])!=(rendered['width'],rendered['height']):raise ValueError('临时压制预览改变了画面分辨率或方向，无法直接对应区域坐标；请先压制出成片后再质检。')
                    value=operation(str(actual),cancel,progress)
            else:value=operation(path,cancel,progress);stage={}
            after=fingerprint_inputs(inputs,cancel)
            if before!=after:raise ValueError('处理期间视频或外挂字幕文件发生变动，请重试。')
            if cancel():raise InterruptedError('已取消操作。')
            return value,before,stage
        def completed(result,error):
            if self._shutdown or not valid() or context!=self.qc_region_context():return
            if error:callback(None,error);return
            value,versions,stage=result
            self._qc['roiInputVersions']=versions
            callback(value,None)
        return self._start_job(label,work,completed)

    def _prepare_qc_regions(self,payload):
        from .dialogs import confirm_regions
        if not self._qc.get('inputPath'):raise ValueError('请先选择要质检的视频。')
        previous=deepcopy(self._qc);previous_image=self._qc_preview
        kind=('raw' if payload['mode']=='temporary' else 'hardsubbed') if 'mode' in payload else payload.get('inputKind',self._qc.get('inputKind','hardsubbed'))
        ass=(payload.get('assPath') or self._qc.get('assPath')) if kind=='raw' else None
        if kind=='raw' and not ass:
            ass=self._path({},title='选择外挂 ASS 字幕',filter='ASS 字幕 (*.ass *.ssa)')
            if not ass:return
        if self._task['running']:raise ValueError('当前有任务正在运行，请等待完成或先取消。')
        if kind!=self._qc.get('inputKind','hardsubbed') or ass!=self._qc.get('assPath'):
            self._qc_source_generation+=1;self._qc.update(chineseRoi=None,sourceRoi=None,completed=False,issues=[],cues=[],rescans=[],summary={})
            for key in ('roiInputVersions','report','lastRescan','reportDirectory','reportKind','apiStatus','boundaryIssues'):self._qc.pop(key,None)
        self._qc.update(inputKind=kind,assPath=ass)
        self._refresh(False)
        def completed(result,error):
            if error:
                self._qc=previous;self._qc_preview=previous_image;self._qc_source_generation+=1;self._refresh(False)
                raise error
            info,image,stamp=result;self._qc_preview=image
            self._qc.update(width=info['width'],height=info['height'],duration=info['duration'],previewSeconds=stamp,
                            roiPreviewKind='temporary_render_precheck' if kind=='raw' else 'finished_video')
            confirm_regions(self)
        self._qc_region_job('正在生成预览画面…',lambda path,cancel,progress:self._qc_preview_frame(path,None,cancel),completed)

    def qc_preview_at(self,stamp,callback,*,valid=lambda:True,error_callback=None):
        def completed(result,error):
            if error:
                if error_callback:error_callback(error)
                else:self._error(str(error))
                return
            info,image,actual=result;self._qc_preview=image;self._qc['previewSeconds']=actual
            callback(image,actual)
        return self._qc_region_job('读取预览画面',lambda path,cancel,progress:self._qc_preview_frame(path,stamp,cancel),completed,valid=valid)

    def qc_propose_regions(self,language,callback,*,valid=lambda:True):
        from lyricsync.audit_service import propose_audit_regions,_language
        language=_language(language,'区域检测');settings=deepcopy(self.document.project.settings);settings['language']=language
        return self._qc_region_job('自动检测字幕区域',lambda path,cancel,progress:propose_audit_regions(path,settings=settings,cancel=cancel),callback,valid=valid)

    def qc_confirm_regions(self,chinese,source,callback,*,valid=lambda:True):
        from lyricsync.audit_service import validate_regions
        zh,src=validate_regions(chinese,source,self._qc['width'],self._qc['height'],mode='local')
        def completed(result,error):
            if not error:
                self._qc.update(chineseRoi=zh,sourceRoi=src,status='识别区域已确认，可开始质检');self._refresh(False)
            callback(error)
        return self._qc_region_job('校验质检素材',lambda path,cancel,progress:None,completed,valid=valid,render=False)

    def _run_qc(self,payload):
        from lyricsync.audit_service import run_audit
        video=payload.get('videoPath') or self._qc.get('inputPath')
        if not video:raise ValueError('请先选择要质检的视频。')
        use_api=bool(payload.get('api'))
        strategy=payload.get('strategy','sentence')
        if strategy not in ('sentence','frame'):raise ValueError('请选择「快速逐句抽检」或「逐帧边界精查」。')
        if use_api:self._api_check()
        roi=payload.get('chineseRoi') or self._qc.get('chineseRoi')
        source=payload.get('sourceRoi') or self._qc.get('sourceRoi')
        if not roi:raise ValueError('请先框选并确认中文字幕区域。')
        if use_api and not source:raise ValueError('开启双语 AI 核对时，请同时框选原文字幕区域。')
        kind=payload.get('inputKind') or ('raw' if payload.get('mode')=='temporary' else 'hardsubbed')
        if kind!=self._qc.get('inputKind','hardsubbed') and not payload.get('chineseRoi'):
            raise ValueError('质检模式已切换，请重新预览画面并确认字幕区域。')
        ass=payload.get('assPath') or (self._qc.get('assPath') if kind=='raw' else None)
        if kind=='raw' and ass!=self._qc.get('assPath') and not payload.get('chineseRoi'):
            raise ValueError('外挂字幕已更换，请重新预览画面并确认字幕区域。')
        if kind=='raw' and not ass:
            ass=self._path({},title='选择外挂 ASS 字幕',filter='ASS 字幕 (*.ass *.ssa)')
            if not ass:return
        translation=payload.get('translationPath') or self.document.project.inputs.get('translation')
        texts=[mapping_text(self.document.project,m) for m in self.document.project.mappings if not m.ignored]
        protected_inputs=list(self.document.project.inputs.values())
        if not translation and not any(t.strip() for t in texts):
            translation=self._path({},title='选择参考中文字幕或文本',filter='中文文本/字幕 (*.txt *.srt *.ass *.ssa)')
            if not translation:return
        directory=self.storage_dir/('qc-'+uuid.uuid4().hex)
        document_snapshot=self.document.snapshot();reference_project=Project.from_dict(document_snapshot['project']);ktv_path=reference_project.inputs.get('ktv')
        settings=deepcopy(self.document.project.settings);settings['audit_use_current_project']=bool(reference_project.mappings);settings['audit_strategy']=strategy;api_settings=deepcopy(self.api_settings);key=self._api_key
        for name in ('chinese_engine', 'source_engine'):
            if name in payload:
                if payload[name] not in ('rapidocr', 'tesseract'):raise ValueError('请选择有效 OCR 引擎。')
                settings[name]=payload[name]
        records=deepcopy(self.document.project.api_records);cache_path=self.storage_dir/(self.document.id+'.api.cache.sqlite')
        roi_versions=deepcopy(self._qc.get('roiInputVersions')) if not payload.get('chineseRoi') else None
        def work(cancel,progress):
            from lyricsync.storage import Cache,atomic_text
            if roi_versions:
                from lyricsync.audit_service import fingerprint_inputs
                if fingerprint_inputs({'video':video,'ass':ass if kind=='raw' else None},cancel)!=roi_versions:
                    raise ValueError('视频或外挂字幕文件已发生变动，请重新载入并确认识别区域。')
            directory.mkdir(parents=True,exist_ok=True)
            project_reference=directory/'current-project.lyria'
            write_document(document_snapshot,project_reference)
            ref=translation
            if not ref:
                directory.mkdir(parents=True,exist_ok=True);ref=directory/'current-project-chinese.txt'
                atomic_text(ref,'\n'.join(texts),protected_inputs)
            cache=None
            try:
                semantic=None
                if use_api:
                    from lyricsync.audit import text_audit
                    cache=Cache(cache_path);cache.restore_records(records)
                    semantic=lambda report,out,**kwargs:text_audit(report,out,key,api_settings['api_model'],settings=api_settings,cache=cache,**kwargs)
                qc_started=time.perf_counter()
                report=run_audit(video,ref,directory,mode='text' if use_api else 'local',chinese_roi=roi,source_roi=source,
                                 input_kind=kind,ass_path=ass,project_path=project_reference,ktv_path=ktv_path,settings=settings,cancel=cancel,progress=progress,semantic_runner=semantic,cache_dir=self.storage_dir/'qc-local-cache')
                from lyricsync.storage import atomic_json
                report.setdefault('performance',{})['total_seconds']=time.perf_counter()-qc_started
                atomic_json(directory/'report.json',report,report.get('inputs',{}).values())
                return {'report':report,'records':cache.records() if cache else records}
            except Exception as exc:
                saved=directory/'report.json'
                try:partial=json.loads(saved.read_text(encoding='utf-8')) if saved.is_file() else None
                except (OSError,ValueError):partial=None
                return {'report':partial,'records':cache.records() if cache else records,'error':str(exc) if isinstance(exc,(ValueError,InterruptedError)) else type(exc).__name__}
            finally:
                if cache:cache.close()
        def completed(bundle,error):
            if error:self._qc.update(status='质检未完成：'+type(error).__name__,completed=False);raise error
            self._record_api_usage(bundle['records'])
            if bundle.get('report'):
                self._display_qc_report(bundle['report'],video,directory,bundle.get('error'))
            else:
                self._qc.update(status='质检未完成：'+bundle.get('error','未能生成质检报告'),completed=False)
                raise ValueError(bundle.get('error','未能生成质检报告'))
        self._qc.update(inputPath=video,mode='text' if use_api else 'local',status='运行中',completed=False)
        self._start_job('快速逐句抽检' if strategy=='sentence' else '逐帧边界精查',work,completed)

    def _display_qc_report(self,report,video,directory,error=None):
        cues={c['id']:c for c in report.get('cues',[])+report.get('source_cues',[])+report.get('unobserved_cues',[])+report.get('unmatched_cues',[])};issues=[]
        for n,issue in enumerate(report.get('issues',[])):
            evidence=next((cues[i] for i in issue.get('cue_ids',[]) if i in cues),{})
            issues.append({**issue,'id':'qc-'+str(n),'message':issue.get('reason','需要人工复核'),'detail':issue.get('status','needs_review'),
                           'frame':evidence.get('first_frame',0),'first_frame':evidence.get('first_frame'),
                           'evidenceFrames':evidence.get('evidence_frames',[])})
        api=report.get('api',{}).get('text',{});status=api.get('status','not_run')
        labels={'completed':'AI 语义核对已完成','partial':'AI 语义核对部分完成',
                'failed':'AI 语义核对失败','cancelled':'AI 语义核对已取消','budget_exhausted':'AI 调用达上限，语义核对暂停',
                'disabled':'未启用 AI 语义核对','not_run':'未启用 AI 语义核对','running':'AI 语义核对未完成'}
        label=labels.get(status,'AI 语义核对状态未知')
        normal_cues=[{'id':c['id'],'cueId':c['id'],'text':c.get('text',''),'expected_text':c.get('expected_text',''),'track':c.get('track','chinese'),'firstFrame':c.get('first_frame'),'lastFrame':c.get('last_frame'),'boundaryStatus':c.get('boundary_status','needs_review'),'contentStatus':c.get('content_status','ocr_completeness_unverified'),'reviewStatus':c.get('review_status','needs_review'),'representativeFrame':c.get('representative_frame'),'evidenceFrames':[{**f,'url':QUrl.fromLocalFile(f['path']).toString()} for f in c.get('evidence_frames',[]) if f.get('path')],
                      'screenshotUrl':QUrl.fromLocalFile(c['screenshot']).toString() if c.get('screenshot') else '',
                      'source_text':'\n'.join(c.get('reference_lyrics',[])),'warnings':c.get('warnings',[])} for c in report.get('cues',[])+report.get('source_cues',[])+report.get('unobserved_cues',[])+report.get('unmatched_cues',[])]
        sentence_mode=report.get('strategy')=='sentence'
        if sentence_mode:
            def sentence_row(c):
                if c.get('latest_recheck'):
                    c={**c,**c['latest_recheck']['observation'],'id':c['id'],'number':c.get('number'),
                       'expected_ids':c.get('expected_ids',[]),'review_status':c.get('review_status','needs_review'),
                       'recheckStatus':c['latest_recheck']['meaning']}
                return {**c,'cueId':c['id'],'firstFrame':c.get('first_frame'),'lastFrame':c.get('last_frame'),
                        'boundaryStatus':c.get('boundary_status'),'contentStatus':c.get('overall_status',c.get('content_status')),
                        'chineseContentStatus':c.get('content_status'),'sourceContentStatus':c.get('source_content_status'),
                        'reviewStatus':c.get('review_status','needs_review'),'representativeFrame':c.get('representative_frame'),
                        'screenshotUrl':QUrl.fromLocalFile(c['screenshot']).toString() if c.get('screenshot') else '',
                        'evidenceFrames':[{**f,'url':QUrl.fromLocalFile(f['path']).toString()} for f in c.get('evidence_frames',[]) if f.get('path')]}
            normal_cues=[sentence_row(c) for c in report.get('cues',[])]
            unmatched=[sentence_row(c) for c in report.get('unmatched_cues',[])]
        else: unmatched=[]
        self._qc.update(sentenceMode=sentence_mode,unmatched=unmatched,scopeDetail=report.get('scope_detail',{}),
                        performance=report.get('performance',{}),localStatus=report.get('local_status'),
                        apiReason=api.get('reason',''),semanticUnchecked=api.get('unchecked_ids',[]),
                        referenceMedia=report.get('reference_media',{}),warnings=report.get('warnings',[]),ocrSettings=report.get('ocr_settings',{}))
        if api.get('reason') and api['reason']!=label: label+='：'+api['reason']
        if sentence_mode: label+='；未抽检 '+str(report.get('coverage',{}).get('sentences_unchecked',0))+' 句'
        regions=report.get('regions',{})
        report_kind='raw' if report.get('stage',{}).get('kind')=='temporary_render_precheck' else 'hardsubbed'
        self._qc.update(inputKind=report_kind,assPath=report.get('inputs',{}).get('ass') if report_kind=='raw' else None)
        report_versions=report.get('input_versions',{})
        if report_versions.get('video'):
            self._qc['roiInputVersions']={k:deepcopy(v) for k,v in report_versions.items() if k=='video' or (k=='ass' and report_kind=='raw')}
        else:self._qc.pop('roiInputVersions',None)
        self._qc.update(width=report.get('video',{}).get('width',self._qc.get('width',0)),height=report.get('video',{}).get('height',self._qc.get('height',0)),duration=report.get('video',{}).get('duration',self._qc.get('duration',0)),cues=normal_cues,rescans=report.get('rescans',[]),chineseRoi=regions.get('chinese',self._qc.get('chineseRoi')),sourceRoi=regions.get('source',self._qc.get('sourceRoi')),inputPath=video,status=(('已取消，保留已查结果；' if report.get('local_status')=='cancelled' else '质检中断，已存部分结果；') if error else '画面检查完成；')+label,
                        completed=True,apiStatus=status,reportKind=report.get('stage',{}).get('label',''),summary=report.get('coverage',{}),
                        reportDirectory=str(directory),boundaryIssues=sum(i.get('kind') in ('timing','display') for i in issues),issues=issues,report=report)
        self._message('质检报告已生成：'+label+'。')
        if status=='budget_exhausted' and api.get('budget'):
            self._set_api_resume(lambda limit:self._resume_qc_semantics(report,directory,limit))
            self.api_state['budget']=api['budget'];self._budget_dialog(api['budget'])

    def _resume_qc_semantics(self,report,directory,maximum):
        self._api_check();snapshot=deepcopy(report)
        def operation(cache,key,settings,cancel,progress):
            from lyricsync.audit import text_audit
            try:
                text_audit(snapshot,directory,key,settings['api_model'],maximum=maximum,retries=settings.get('api_retries',3),settings=settings,cache=cache,cancel=cancel,progress=progress)
                error=None
            except Exception as exc:error=type(exc).__name__
            return {'status':'qc_semantics','report':snapshot,'error':error}
        def completed(result):
            self._display_qc_report(result['report'],report['inputs']['video'],directory,result.get('error'))
        self._api_job('继续 AI 语义核对',operation,completed)

    def _qc_cue(self,identity):
        report=self._qc.get('report',{})
        cue=next((c for c in report.get('cues',[])+report.get('source_cues',[])+report.get('unobserved_cues',[])+report.get('unmatched_cues',[]) if c.get('id')==identity),None)
        if cue is None:raise ValueError('找不到该条质检记录，请从当前质检列表中重新选择。')
        if cue.get('latest_recheck'):
            return {**cue,**cue['latest_recheck']['observation'],'id':cue['id'],'number':cue.get('number'),
                    'expected_ids':cue.get('expected_ids',[]),'review_status':cue.get('review_status','needs_review')}
        return cue

    def _qc_inspect(self,payload):
        cue=deepcopy(self._qc_cue(payload.get('cueId')));evidence=cue.get('evidence_frames',[])
        path=evidence[0].get('path') if evidence else cue.get('screenshot')
        def work(cancel,progress):
            from PySide6.QtGui import QImage
            if not path:return None
            image=QImage(path)
            if image.isNull():raise ValueError('无法读取质检截图文件，仍可在报告中查看识别文字与画面时间。')
            return image.scaled(620,350,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
        def completed(image,error):
            if error:raise error
            if image is not None:self._qc_preview=image
            from .dialogs import inspect_qc_cue
            inspect_qc_cue(self,cue,image)
        self._start_job('加载质检详情',work,completed)

    def _qc_review(self,payload):
        from lyricsync.audit_service import record_audit_review
        cue=self._qc_cue(payload.get('cueId'));status=payload.get('status');note=payload.get('note','')
        if status not in ('needs_review','reviewed','skipped') or not isinstance(note,str) or len(note)>4000:raise ValueError('复核状态或备注内容无效。')
        report=deepcopy(self._qc['report']);directory=Path(self._qc['reportDirectory'])
        def completed(result,error):
            if error:raise error
            self._display_qc_report(result,result['inputs']['video'],directory)
            self._message('复核记录已保存。')
        self._start_job('保存复核记录',lambda cancel,progress:record_audit_review(report,directory,[cue['id']],status,note),completed)

    def _qc_rescan(self,payload):
        cue=self._qc_cue(payload.get('cueId'))
        if 'firstFrame' not in payload or 'lastFrame' not in payload:
            from .dialogs import qc_rescan_dialog
            qc_rescan_dialog(self,cue);return
        first=self._integer(payload,'firstFrame');last=self._integer(payload,'lastFrame')
        if not 1<=first<=last:raise ValueError('重查帧范围无效（起始帧需大于等于 1，且不能晚于结束帧）。')
        language=payload.get('sourceLanguage','jpn+eng');chinese_language=payload.get('chineseLanguage','chi_sim')
        if any(not isinstance(v,str) or not v.strip() or len(v)>80 for v in (language,chinese_language)):raise ValueError('OCR 识别语言设置无效。')
        report=deepcopy(self._qc['report']);directory=Path(self._qc['reportDirectory']);settings=deepcopy(self.document.project.settings)
        zh=deepcopy(payload.get('chineseRoi') or self._qc.get('chineseRoi'));source=deepcopy(payload.get('sourceRoi') or self._qc.get('sourceRoi'))
        def work(cancel,progress):
            from lyricsync.audit_service import rescan_audit
            try:
                return rescan_audit(report,directory,first_frame=first,last_frame=last,cue_ids=[cue['id']],
                                    chinese_roi=zh,source_roi=source,source_language=language.strip(),chinese_language=chinese_language.strip(),settings=settings,cancel=cancel,progress=progress,cache_dir=self.storage_dir/'qc-local-cache')
            except Exception as exc:
                try:saved=json.loads((directory/'report.json').read_text(encoding='utf-8'))
                except (OSError,ValueError):saved=report
                return {'parent_report':saved,'report':None,'error':str(exc) if isinstance(exc,(ValueError,InterruptedError)) else type(exc).__name__}
        def completed(result,error):
            if error:
                # Service persists failed/cancelled attempts in parent report.
                self._qc['status']='局部重查未完成：'+type(error).__name__;raise error
            parent=result['parent_report'];child=result['report']
            self._display_qc_report(parent,parent['inputs']['video'],directory)
            if result.get('error'):
                self._qc['status']='局部重查未完成：'+result['error'];self._error(self._qc['status']);return
            self._qc['lastRescan']=child;self._refresh(False)
            from .dialogs import show_text
            lines=['以下为指定帧范围的重新识别结果（原质检报告保持不变）：']
            for c in child.get('cues',[])+child.get('source_cues',[])+child.get('unobserved_cues',[]):
                lines.append(f"{c.get('track','')} · {c.get('text','')}\n帧 {c.get('first_frame') if c.get('first_frame') is not None else '待核对'}–{c.get('last_frame') if c.get('last_frame') is not None else '待核对'} · {c.get('boundary_status','needs_review')} · {c.get('content_status','ocr_completeness_unverified')}")
            if len(lines)==1:lines.append('该帧范围内未识别到字幕文字，请结合画面核对。')
            show_text('局部重查结果','\n\n'.join(lines))
        self._start_job('局部重新识别',work,completed)

    def _qc_open_report(self,payload):
        from PySide6.QtGui import QDesktopServices
        directory=Path(self._qc.get('reportDirectory',''))
        if payload.get('id'):
            entry=next((r for r in self._qc.get('rescans',[]) if r.get('id')==payload['id']),None)
            if not entry:raise ValueError('局部重查记录不存在。')
            target=Path(entry['report_path']).resolve()
            if not target.is_relative_to(directory.resolve()):raise ValueError('重查报告路径异常，无法打开。')
            target=target.with_suffix('.html')
        else:target=directory/'report.html'
        if not target.is_file():raise ValueError('报告尚未生成。')
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.resolve()))):raise ValueError('无法自动用浏览器打开报告，请前往报告文件夹手动打开。')

    def _qc_jump(self,payload):
        if payload.get('cueId'):
            self._qc_inspect(payload);return
        issue=next((i for i in self._qc.get('issues',[]) if str(i.get('id'))==str(payload.get('id'))),None)
        if not issue:raise ValueError('找不到对应的质检记录。')
        evidence=issue.get('evidenceFrames',[])
        if evidence:
            from PySide6.QtGui import QDesktopServices
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(evidence[0]['path'])):raise ValueError('无法打开质检截图，请前往报告文件夹查看。')
        elif self._qc.get('inputPath')==self.media_controller.path and issue.get('frame'):
            self.media_controller.seek(int(issue['frame']));self.stage='review';self._refresh(False)
        else:raise ValueError('该条记录没有保存画面截图，请在报告中查看识别文字与时间信息。')

    def _has_close_reasons(self):
        return bool(self.document.revision!=self.document.saved_revision or
                    (self.document.project.mappings and self.document.revision!=self.document.exported_revision) or self._task['running'] or bool(self._candidate_job))

    def _show_close(self):
        self.media_controller.pause()
        self._close_dialog=dict(visible=True,switching=bool(self._continuation),exportTarget='主时间轴',dirty=self.document.revision!=self.document.saved_revision,
                               unexported=bool(self.document.project.mappings and self.document.revision!=self.document.exported_revision),
                               taskRunning=self._task['running'] or bool(self._candidate_job),saving=bool(self._save_running))
        self._refresh(False);self.closeDialogRequested.emit(self._close_dialog)

    @Slot(result=bool)
    def requestClose(self):
        if self._shutdown:
            if self._shutdown_failure_waiting:self._show_shutdown_failure()
            return self._shutdown_complete
        if not self._has_close_reasons() and not self._save_running:
            self._begin_quit();return False
        self._continuation=None;self._show_close();return False

    def _resolve_close(self,choice):
        if self._close_dialog.get('newFromReview'):
            self._close_dialog={}
            context=self._review_new_context
            self._review_new_context=None
            if choice=='cancel':
                self._refresh(False)
            elif choice=='discard':
                self._finish_new_from_review(context)
            elif choice=='save':
                path=self.path or self._path({},True,'保存工程','Lyria 工程 (*.lyria)','未命名工程.lyria')
                if not path:
                    self._message('已取消另存为，继续核对当前工程。')
                elif not context or self.document is not context[0] or (self.document.revision,self.epoch)!=context[1:]:
                    raise ValueError('选择保存路径期间工程有变动，请重新选择新建工程。')
                else:
                    self._save(True,path,new_context=context)
            return
        if choice=='cancel':self._closing=False;self._continuation=None;self._close_dialog={};self._refresh(False);return
        if choice=='discard':
            self._close_dialog={};callback=self._continuation;self._continuation=None
            if callback:callback()
            else:self._begin_quit()
            return
        if choice=='save':
            path=self.path or self._path({},True,'保存工程','Lyria 工程 (*.lyria)','未命名工程.lyria')
            if not path:return
            self._closing=True;self._save(True,path)
        elif choice=='export':self._closing=True;self._export({})
        else:raise ValueError('请选择保存、导出、放弃或取消。')

    def _resume_close(self):
        self._closing=False
        if self._has_close_reasons():self._show_close();return
        callback=self._continuation;self._continuation=None;self._close_dialog={}
        if callback:callback()
        else:self._begin_quit()

    def _begin_quit(self):
        self._quit_after_shutdown=True
        self.shutdown()

    def shutdown(self):
        """Request cooperative shutdown without blocking the GUI or destroying owners."""
        if self._shutdown:return
        self._shutdown=True;self.epoch+=1;self._handlers.clear();getattr(self,'_job_asset_epochs',{}).clear()
        self._shutdown_started=time.monotonic();self._shutdown_warning=self._shutdown_started
        for dialog in list(getattr(self,'_auxiliary_dialogs',[])):dialog.close()
        self.autosave_timer.stop();self.layout_timer.stop()
        self.jobs.begin_shutdown()
        from .spectrogram_item import shutdown_workers
        shutdown_workers(timeout=0.,controller=self)
        self.media_controller.close()
        self._status='正在等待文件保存与后台任务结束…'
        self._refresh(False)
        self._shutdown_timer.start()

    @property
    def shutdown_complete(self):
        return self._shutdown_complete

    def _poll_shutdown(self):
        if not self._shutdown or self._shutdown_complete:return
        self.jobs.reap_threads()
        for identity,error in self.jobs.take_completed():
            intent=self._accepted_save_intents.pop(identity,None)
            if error is not None and not isinstance(error,InterruptedError):
                if intent:self._shutdown_failed_saves.append(intent)
                else:self._shutdown_errors.append(type(error).__name__)
                self.diagnostics.record('shutdown-worker-failed',error)
            elif intent and error is None:
                from lyricsync.storage import canonical
                # A newer successful save to the same document/target supersedes
                # an older failed write. Never retry old data over that success.
                self._shutdown_failed_saves=[old for old in self._shutdown_failed_saves
                    if not (canonical(old[1])==canonical(intent[1]) and
                            old[0].get('document_id')==intent[0].get('document_id') and old[3]<=intent[3])]
        # Keep serialization with the already-running save. Replay the accepted
        # immutable pending intent, not a new snapshot or a different target.
        if (self._save_pending or self._shutdown_retry_queue) and not self.jobs.live_kind('save') and not self.jobs.live_kind('shutdown-save'):
            intent=self._save_pending or self._shutdown_retry_queue.pop(0)
            snapshot,target=intent[:2]
            self._save_pending=None
            identity=self.jobs.run('shutdown-save',lambda cancel,progress:write_document(snapshot,target),during_shutdown=True)
            self._accepted_save_intents[identity]=intent
            return  # Reap this write's actual thread and outcome on the next tick.
        from .spectrogram_item import shutdown_workers
        spectrum_stopped=shutdown_workers(timeout=0.,controller=self)
        if self.jobs.live_threads() or self.media_controller.worker.thread.is_alive() or not spectrum_stopped:
            now=time.monotonic()
            if now-self._shutdown_warning>=10:
                self._shutdown_warning=now
                self.diagnostics.record('shutdown-still-waiting')
                self._status='正在安全保存数据并结束后台任务，请稍候…'
                self._refresh(False)
            return
        # A thread may have returned since the initial reap. Consume its result
        # on the next poll, rather than declaring completion with unseen errors.
        if self.jobs.threads:return
        if self._shutdown_failed_saves and self._quit_after_shutdown and not self._shutdown_external:
            if not self._shutdown_failure_waiting:
                self._shutdown_failure_waiting=True;self._shutdown_timer.stop()
                self._show_shutdown_failure()
            return
        self.media_controller.worker.thread.join(0)
        error=getattr(self.media_controller.worker,'exit_error',None)
        if error is not None:
            self._shutdown_errors.append(type(error).__name__)
            self.diagnostics.record('shutdown-decoder-failed',error)
        self._save_running=None;self.jobs.active.clear();self._shutdown_timer.stop()
        self._shutdown_complete=True
        self.shutdownFinished.emit()
        if self._quit_after_shutdown:self.quitRequested.emit()

    def _show_shutdown_failure(self):
        self._error_text='工程保存失败。请选择重试保存，或不保存并退出。'
        self._status=self._error_text
        self._close_dialog=dict(visible=True,shutdownFailure=True,message=self._error_text,dirty=True,unexported=False,taskRunning=False,saving=False)
        self._refresh(False);self.closeDialogRequested.emit(self._close_dialog)

    def _resolve_shutdown_failure(self,choice):
        if choice not in ('save','discard'):
            self._show_shutdown_failure();return False
        if choice=='save':
            self._shutdown_retry_queue.extend(self._shutdown_failed_saves)
        self._shutdown_failed_saves=[];self._shutdown_failure_waiting=False
        self._close_dialog={};self._shutdown_timer.start();self._refresh(False)
        return True

    def wait_for_shutdown(self,app):
        """Final exit barrier only: retain Qt owners after exec()/startup failure.

        Normal window close drains asynchronously above. Direct app.quit/exit and
        QML load failures also pass here. No timeout abandons live native work.
        """
        from PySide6.QtCore import QEventLoop
        self._shutdown_external=True
        self.shutdown()
        while not self._shutdown_complete:
            app.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents,20)
            self._poll_shutdown()
            if not self._shutdown_complete:time.sleep(.005)
        # Deliver already queued completion signals while their receivers live.
        app.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents,20)
