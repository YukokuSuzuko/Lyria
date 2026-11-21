"""Session-scoped authority for the first result of an explicitly new project.

Opening/recovering a document never restores this authority. Completed main
content and its provenance use the ordinary persisted project model. A new
session/version and an unchanged revision are required at both worker handoffs.
"""
from copy import deepcopy
import uuid

from lyricsync.candidate_tracks import _digest, prepare_apply, can_regroup_imported_seed
from lyricsync.model import Mapping, Project, stable_id


def bind_imported_ocr_rows(original, generated):
    """Retain a unique imported sentence identity across pipeline rematching.

    An exact nonempty translation-ID set is a stable explicit association.
    Splits, merges, repeated references and unmatched rows stay unbound.
    """
    from collections import Counter
    def key(row):
        for attr in ('translation_ids', 'lyric_ids', 'visual_ids'):
            ids = getattr(row, attr)
            if ids:
                return (attr, *sorted(ids))
        return ()
    original_rows = original.project_timeline(detached=True)
    generated_rows = generated.project_timeline(detached=True)
    before = Counter(key(row) for row in original_rows if key(row))
    after = Counter(key(row) for row in generated_rows if key(row))
    targets = {key(row): row.id for row in original_rows if key(row)}
    original_ids={row.id for row in original_rows}
    for row in generated_rows:
        identity=key(row)
        if identity and before[identity]==after[identity]==1:
            row.id=targets[identity]
        elif row.id in original_ids:
            # An incidental generated ID is not evidence of correspondence.
            row.id=stable_id('unmatched-ocr',row.id,identity,row.start,row.end)

    generated.replace_timeline(generated_rows)


def main_signature(project):
    data = project.to_dict()
    return _digest({key: data[key] for key in ('alignment_graph', 'translations', 'lyrics', 'visuals')} |
                   {'manual_locked_ids': data['settings'].get('manual_locked_ids', []),
                    'markers': data['settings'].get('markers', []),
                    'pending_text_splits': data['settings'].get('pending_text_splits', [])})


class EntryInitialization:
    def _entry_reset(self, *, new=False):
        self._entry_session = {'document': self.document.id, 'version': uuid.uuid4().hex,
                               'mode': None, 'eligible': new,
                               'baseline': main_signature(self.document.project)}
        self._entry_completed_context = None

    def _entry_observe_main(self):
        session = getattr(self, '_entry_session', None)
        if session and session['eligible'] and (session['document'] != self.document.id or
                session['baseline'] != main_signature(self.document.project)):
            # Latched: deleting all rows or undoing an edit cannot make it new.
            session['eligible'] = False
            session['version'] = uuid.uuid4().hex

    def _entry_accept_import(self):
        """Only the guarded import completion may update the pristine seed."""
        session = getattr(self, '_entry_session', None)
        if session and session['eligible'] and session['document'] == self.document.id:
            session['baseline'] = main_signature(self.document.project)
            session['version'] = uuid.uuid4().hex

    def _entry_select_mode(self, mode):
        self._entry_observe_main()
        session = self._entry_session
        if session['mode'] != mode:
            session['mode'] = mode
            session['version'] = uuid.uuid4().hex

    def _entry_capture_source_import(self):
        """Authority for an explicit original-lyrics import in a new audio entry.

        Captured before parsing; mode/session changes cannot create main rows
        when a late import finishes. Never infer this authority from emptiness.
        """
        self._entry_observe_main()
        session = self._entry_session
        if (not session['eligible'] or session['mode'] != 'audio' or
                self.document.main_timeline or (self.document.project.translations
                and not self.document.project.settings.get('audio_translation_pool'))):
            return None
        return dict(session, revision=self.document.revision)

    def _entry_source_import_seed(self, context, lyrics):
        """Build original-text seed rows, without changing the live document."""
        if (not context or not self._entry_matches(context, context['revision']) or
                context['mode'] != 'audio' or self.document.main_timeline or
                (self.document.project.translations and not self.document.project.settings.get('audio_translation_pool'))):
            return []
        rows = [Mapping(stable_id('source-seed', self.document.id, lyric.id), [],
                        lyric_ids=[lyric.id], start=lyric.start, end=lyric.end,
                        text_override=lyric.text, text_role='source_import', method='source_import')
                for lyric in lyrics]
        Project(mappings=rows).validate_timeline()
        return rows

    def _vocal_pristine_main(self):
        """Explicit new run may initialize only empty or untouched source seeds."""
        rows=self.document.main_timeline
        if not rows:return True
        if self.document.project.settings.get('manual_locked_ids'):return False
        lyrics={x.id for x in self.document.project.lyrics}
        seen=[]
        for row in rows:
            if (row.start is not None or row.end is not None or row.human_edited or row.locked or row.confirmed or row.ignored
                    or row.text_role not in ('source_import','source_original') or row.method not in ('import','source_import')
                    or not row.lyric_ids or any(i not in lyrics for i in row.lyric_ids)):
                return False
            seen.extend(row.lyric_ids)
        return len(seen)==len(set(seen)) and set(seen)==lyrics

    def _entry_capture_job(self, source):
        self._entry_observe_main()
        session = self._entry_session
        if source=='vocal' and (session['mode'] not in (None,'audio') or not self._vocal_pristine_main()):return None
        if source=='vocal' and self._vocal_pristine_main():
            # v0.22: safe empty/reopened original seeds are a supported first-run
            # route. Capture at launch only; later cancellation/edit still expires it.
            session.update(mode='audio',eligible=True,baseline=main_signature(self.document.project))
        expected = {'ocr': 'ocr', 'audio': 'vocal'}.get(session['mode'])
        if not session['eligible'] or expected != source:
            return None
        session['version'] = uuid.uuid4().hex
        return dict(session, revision=self.document.revision, source=source)

    def _entry_matches(self, context, revision):
        self._entry_observe_main()
        session = self._entry_session
        return bool(context and session['eligible'] and
                    context['document'] == self.document.id == session['document'] and
                    context['version'] == session['version'] and
                    context['mode'] == session['mode'] and
                    context['baseline'] == session['baseline'] and
                    self.document.revision == revision)

    def _entry_try_initialize(self, record, context, before_revision, on_initialized=None, on_result=None):
        def finish(status, reason, applied=0, blocked=0, applied_timed=0):
            if on_result is not None:
                on_result(dict(status=status, reason=reason, applied=applied, blocked=blocked, applied_timed=applied_timed))
        if not context or before_revision != context['revision'] or record['revision'] != context['revision']:
            finish('retained', 'not_pristine')
            return False
        installed_revision = self.document.revision
        if not self._entry_matches(context, installed_revision) or record['source'] != context['source']:
            finish('retained', 'stale_context')
            return False
        if not record['rows'] or self.media_controller.index is None:
            finish('retained', 'missing_index')
            return False
        # Imported seed rows may receive their own times, but a split/merge or
        # unmatched row never silently replaces/doubles the imported seed.
        main_ids = {row.id for row in self.document.main_timeline}
        targets = [row['sentence_id'] for row in record['rows']]
        unmatched = bool(main_ids and (None in targets or set(targets) != main_ids))
        ocr_seed = context['mode'] == 'ocr' and bool(main_ids)
        safe_seed = ocr_seed and can_regroup_imported_seed(self.document, record, allow_visual_extras=True)
        regroup = unmatched and safe_seed
        if (ocr_seed and not safe_seed) or (unmatched and not regroup):
            finish('retained', 'identity_mismatch')
            return False
        detached = deepcopy(self.document)
        index = deepcopy(self.media_controller.index)

        def work(cancel, progress):
            plan = prepare_apply(detached, record['id'], [row['id'] for row in record['rows']],
                                 frame_index=index, add_unmapped=not main_ids, cancel=cancel, initial_missing_time=True,
                                 initial_seed_regroup=regroup,
                                 explicit_visual_regroup=regroup,
                                 initial_ocr_text=(context['mode'] == 'ocr' and not main_ids
                                                   and not detached.project.translations),
                                 initial_asr_draft=(context['mode'] == 'audio' and not main_ids
                                                    and not detached.project.translations
                                                    and all(row.get('text_role') == 'source_asr' for row in record['rows'])))
            # Keep valid independent rows; unsafe rows remain in immutable history.
            # Re-sign only the worker-built subset, retaining every provenance guard.
            skipped = sum(bool(entry['blocked']) for entry in plan['entries'])
            plan['entries'] = ([] if regroup and skipped else
                               [entry for entry in plan['entries'] if not entry['blocked']])
            plan.pop('signature', None)
            plan['signature'] = _digest(plan)
            return plan, skipped

        def completed(result, error):
            if self._candidate_job == identity:
                self._candidate_job = None
                self._candidate_task.update(running=False,id='',cancelling=False,
                                            label='已完成时间轴生成校验',progress=1.)
            if error:
                self._message('自动建轴未完成，完整识别结果已保留在工程历史中，原主轴未改动。')
                finish('retained', 'cancelled' if isinstance(error, InterruptedError) else 'verification_failed')
                return
            if not self._entry_matches(context, installed_revision):
                self._message('处理期间工程内容已有变动，识别结果已保留为待选记录，未直接覆盖主时间轴。')
                finish('retained', 'stale_context')
                return
            plan, skipped = result
            if not plan['entries']:
                self._message('没有可安全接入的句子，完整识别结果已保留在工程历史中，原主轴未改动。')
                finish('retained', 'blocked', blocked=skipped)
                return
            # Consume before apply. Undo or another job cannot auto-run it again.
            self._entry_session['eligible'] = False
            try:
                changed = self.candidate_apply(plan)
            except (ValueError, RuntimeError) as exc:
                self._message('未能直接写入主时间轴（识别结果已保留）：' + str(exc))
                finish('retained', 'apply_failed', blocked=skipped)
                return
            self.stage = 'review'
            self._refresh(False)
            self._message('识别结果已进入可编辑主轴；缺少时间的句子保留为未打轴（可撤销）。' + (f'另有 {skipped} 句无法安全接入，已保留在工程识别历史中。' if skipped else ''))
            timed = sum(row.id in changed and row.start is not None and row.end is not None and row.end > row.start
                        for row in self.document.main_timeline)
            finish('applied' if changed else 'retained', 'applied' if changed else 'no_change',
                   applied=len(changed), blocked=skipped, applied_timed=timed)
            if on_initialized is not None:
                on_initialized()

        identity = self._start_job('正在生成初始时间轴…', work, completed, foreground=False)
        self._candidate_job = identity
        self._candidate_task.update(running=True,id=identity,cancelling=False,
                                    label='正在生成初始时间轴…',progress=0.)
        self._refresh(False)
        return True
