"""Analysis-only source switching with current-project provenance verification.

Never changes player media, alignment inputs, or project content. File hashing and
PCM analysis run on workers; GUI acceptance uses generation and stat checks.
"""
from copy import deepcopy
import math
import time
from pathlib import Path
from PySide6.QtCore import QTimer, Slot

from lyricsync.candidate_tracks import verify_candidate_source, candidate_source_status
from lyricsync.local_separation import stem_for_candidate


class AudioSourceAnalysis:
    def _vocal_analysis_record(self):
        from .timeline_views import current_candidate
        return next((r for r in reversed(self.document.source_candidates) if r['source'] == 'vocal' and current_candidate(self.document,r)), None)

    def _audio_source_state(self):
        record = self._vocal_analysis_record()
        files = (record or {}).get('provenance', {}).get('auxiliary_files', {})
        return {'vocalAvailable': all(k in files for k in ('alignment_audio', 'separated_vocals', 'separation_manifest')),
                'source': getattr(self, '_analysis_source', 'original')}

    def _clear_audio_analysis(self):
        if self._audio_job:
            self.jobs.cancel(self._audio_job)
        self._audio_job = None
        self._overview_start = None
        self._spectrum_priority_revision = None
        self._analysis_generation = getattr(self, '_analysis_generation', 0) + 1
        self._analysis_source = 'original'
        self._analysis_record = None
        if getattr(self, '_analysis_guard_timer', None):
            self._analysis_guard_timer.stop()
        self._audio_revision += 1
        self._audio_analysis = dict(revision=self._audio_revision, status='idle', error='',
                                   durationSeconds=0., waveform=[], spectrum=[], maxFrequency=4000)
        self.audioAnalysisChanged.emit()

    def _start_audio_analysis(self, metadata, index):
        self._clear_audio_analysis()
        if not metadata.get('has_audio'):
            self._audio_analysis.update(status='no_audio', durationSeconds=index.duration)
            self.audioAnalysisChanged.emit()
            return
        self._launch_analysis('original', index)

    @Slot(str)
    def selectAudioAnalysisSource(self, source):
        if source not in ('original', 'vocal'):
            self._error('未知音频显示来源。'); self.audioAnalysisChanged.emit(); return
        index = self.media_controller.index
        if index is None or not self.media_controller.snapshot().get('ready'):
            self._error('请先载入当前工程的音频或视频。'); self.audioAnalysisChanged.emit(); return
        record = self._vocal_analysis_record() if source == 'vocal' else None
        if source == 'vocal' and not self._audio_source_state()['vocalAvailable']:
            self._error('当前工程没有已保存的分离人声，请先完成人声分离。'); self.audioAnalysisChanged.emit(); return
        self._clear_audio_analysis()
        self._launch_analysis(source, index, record)

    @Slot(int, bool)
    def spectrumViewportActivity(self, revision, visible):
        if revision != self._audio_revision: return
        self._spectrum_priority_revision = revision if visible else None
        if not visible:
            # Let QML settle source/visibility bindings before deciding that a
            # transient inactive state really permits background overview work.
            QTimer.singleShot(0, lambda: self._overview_if_not_visible(revision))

    def _overview_if_not_visible(self, revision):
        if revision != self._audio_revision: return
        if getattr(self, '_spectrum_priority_revision', None) != revision:
            self.spectrumViewportReady(revision)

    @Slot(int)
    def spectrumViewportReady(self, revision):
        pending = getattr(self, '_overview_start', None)
        if pending and revision == self._audio_revision:
            self._overview_start = None
            pending()

    def _launch_analysis(self, source, index, record=None):
        from lyricsync.audio_analysis import analyze_audio
        generation = self._analysis_generation
        media_generation = self.media_controller.epoch
        path = self.media_controller.path
        document_id = self.document.id
        detached = deepcopy(self.document)
        detached_index = deepcopy(index)
        record = deepcopy(record)
        self._analysis_source = source
        self._audio_analysis.update(status='loading', durationSeconds=index.duration,
                                    sourceOffsetSeconds=0., spectrogramSourceReady=False)
        self.audioAnalysisChanged.emit()
        self._refresh(False)

        def current():
            return (not self._shutdown and generation == self._analysis_generation
                    and media_generation == self.media_controller.epoch
                    and path == self.media_controller.path and document_id == self.document.id)

        def warning():
            if record is None:
                return ''
            latest = self._vocal_analysis_record()
            if not latest or latest['id'] != record['id']:
                return 'Analysis source changed; select it again.'
            return candidate_source_status(self.document, record, self.media_controller.index)

        def fail(error):
            self._audio_job = None
            self._overview_start = None
            self._audio_analysis.update(status='error', error=str(error), sourcePath='',
                                        spectrogramSourceReady=False, waveform=[], spectrum=[])
            self.audioAnalysisChanged.emit()
            self._refresh(False)

        def resolve(cancel, progress):
            analysis_path, duration, offset = path, detached_index.duration, 0.
            if record is not None:
                verify_candidate_source(detached, record, detached_index, cancel=cancel)
                stem = stem_for_candidate(record, cancel=cancel)
                offset, duration = stem.get('offset_seconds'), stem.get('duration_seconds')
                if (type(offset) not in (int, float) or type(duration) not in (int, float)
                        or not math.isfinite(offset) or not math.isfinite(duration)
                        or offset < 0 or duration <= 0
                        or stem.get('latency_samples') != 0 or stem.get('padding_removed') is not True
                        or stem.get('time_mapping') != 'project_seconds = offset_seconds + stem_sample / sample_rate'):
                    raise ValueError('分离人声缺少可信时间映射，不能显示到当前时间轴。')
                # Both PCM and IEEE-float WAV preserve an exact sample time base.
                import av
                with av.open(str(stem['path'])) as container:
                    audio = container.streams.audio[0]
                    rate = audio.codec_context.sample_rate
                    physical = float(audio.duration * audio.time_base) if audio.duration is not None else None
                if (not physical or not rate or abs(physical-duration) > 1.5 / rate
                        or ('sample_rate' in stem and stem['sample_rate'] != rate)
                        or ('samples' in stem and stem['samples'] != round(physical * rate))):
                    raise ValueError('人声文件的采样时长与映射记录不符。')
                duration = min(offset + min(physical,duration), detached_index.duration) - offset
                if duration <= 0:
                    raise ValueError('人声映射与当前视频时间轴没有交集。')
                analysis_path = stem['path']
            if cancel(): raise InterruptedError('Analysis source cancelled')
            return dict(sourcePath=str(analysis_path), durationSeconds=duration, sourceOffsetSeconds=offset)

        def source_ready(resolved, error):
            if not current(): return
            if not error: error = warning()
            if error:
                fail(error); return
            self._audio_job = None
            self._analysis_record = record
            self._audio_analysis.update(resolved, spectrogramSourceReady=True,
                                        status='loading', overviewStatus='waiting_for_viewport')
            if record is not None:
                if not getattr(self, '_analysis_guard_timer', None):
                    self._analysis_guard_timer = QTimer(self)
                    self._analysis_guard_timer.setInterval(1000)
                    self._analysis_guard_timer.timeout.connect(self._check_analysis_source)
                self._analysis_guard_timer.start()
            revision = self._audio_revision

            def work(cancel, progress):
                ticks = 0
                def cooperative_cancel():
                    nonlocal ticks
                    ticks += 1
                    # Bounded decode batches yield to visible spectrum/GUI work.
                    if ticks % 8 == 0: time.sleep(.001)
                    return cancel()
                result = analyze_audio(resolved['sourcePath'], resolved['durationSeconds'], cooperative_cancel)
                if record is not None:
                    verify_candidate_source(detached, record, detached_index, cancel=cancel)
                result.update(sourceOffsetSeconds=resolved['sourceOffsetSeconds'],
                              spectrogramSourceReady=True, overviewStatus='ready')
                return result

            def completed(result, error):
                if not current(): return
                if not error: error = warning()
                if error:
                    fail(error); return
                self._audio_job = None
                # Same verified source, same revision: overview must not invalidate
                # a valid spectrum cache or flash a new source onto the surface.
                result['revision'] = revision
                self._audio_analysis = result
                self.audioAnalysisChanged.emit()
                self._refresh(False)

            def start_overview():
                if not current() or revision != self._audio_revision: return
                self._audio_analysis['overviewStatus'] = 'loading'
                self.audioAnalysisChanged.emit()
                self._audio_job = self._start_job('audio-analysis', work, completed,
                                                foreground=False, diagnostic_stage='media', asset_dependent=False)
            self._overview_start = start_overview
            self.audioAnalysisChanged.emit()
            self._refresh(False)
            # A visible spectrum signals its first published tile. Hidden or
            # waveform-only panels still need their overview, after a grace period.
            QTimer.singleShot(1500, lambda: self._overview_if_not_visible(revision) if current() else None)

        self._audio_job = self._start_job('audio-source', resolve, source_ready,
                                        foreground=False, diagnostic_stage='media', asset_dependent=False)

    def _check_analysis_source(self):
        record = getattr(self, '_analysis_record', None)
        if not record or self._shutdown:
            return
        latest = self._vocal_analysis_record()
        warning = (candidate_source_status(self.document, record, self.media_controller.index)
                   if latest and latest['id'] == record['id'] else '人声结果已更新，请重新选择音频显示来源。')
        if warning:
            self._clear_audio_analysis()
            self._analysis_source = 'vocal'
            self._audio_analysis.update(status='error', error=warning)
            self.audioAnalysisChanged.emit()
            self._refresh(False)
