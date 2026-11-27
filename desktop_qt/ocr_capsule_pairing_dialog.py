"""OCR capsule draft desk; local commands first, one explicit project commit."""
import json
from copy import deepcopy
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QPushButton, QListWidgetItem, QInputDialog, QHBoxLayout
from .capsule_pairing_dialog import CapsulePairingDialog
from lyricsync.pairing_session import PairingSession
from lyricsync.translation_pairing_draft import apply_draft


class OcrCapsulePairingDialog(CapsulePairingDialog):
    supports_anchor_append = True
    def __init__(self, bridge, parent=None, *, candidate=None):
        self.candidate = candidate
        self.document, self.epoch = bridge.document, bridge.epoch
        self.session = PairingSession(candidate[2] if candidate else bridge.document.project, getattr(getattr(bridge, 'media_controller', None), 'index', None))
        self.identity, self.revision = bridge.document.id, bridge.document.revision
        self._closed = False; self._ai_pending = False
        super().__init__(bridge, parent)
        self.setObjectName('ocrCapsulePairingDialog')
        if candidate:
            self.setWindowTitle('候选译文时间修复')
            self.heading.setText('候选译文时间修复')
        self.column_titles[0].setText('OCR 识别结果（只读）')
        self.finished.connect(lambda _: setattr(self, '_closed', True))
        self._poll = QTimer(self); self._poll.setInterval(250); self._poll.timeout.connect(self.poll_job); self._poll.start()
        top = self.top
        from pathlib import Path
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QIcon
        from . import carbon_widgets as c
        spark_icon = QIcon(str(Path(__file__).parent / 'assets' / 'icons' / 'spark.svg'))
        for text, name, role, callback in [
            ('AI 辅助配对', 'pairingAiRequest', 'soft', self.request_ai),
            ('拆为多行 / 帧切点', 'pairingSplitFrames', '', self.split_frames),
            ('导入 ID 草案', 'pairingImportDraft', 'quiet', self.import_draft)]:
            b = c.button(text, callback, role, name)
            if name == 'pairingAiRequest':
                b.setIcon(spark_icon); b.setIconSize(QSize(14, 14))
            top.insertWidget(top.count()-2, b)
        self.intro.setValue('将左侧画面识别句拖入中间配对槽，再把右侧译文拖进去匹配；支持右键合并或用剃刀切分，确认应用前不影响当前工程。')

        from PySide6.QtWidgets import QFrame
        extra_bar = QFrame(); extra_bar.setObjectName('ocrCapsuleActionRow')
        extra = c.layout(extra_bar, False, (16, 6, 16, 6), 8)
        for label, callback in [('顺序填入剩余（非语义）', self.fill_remaining), ('选中译文建立未打轴槽', self.add_untimed)]:
            extra.addWidget(c.button(label, callback))
        extra.addStretch()
        self.layout().insertWidget(1, extra_bar)

    def fill_remaining(self):
        try: self.guard(); self.session.sequential_fill(); self.refresh()
        except (ValueError, RuntimeError) as exc: self.status.setText(str(exc))

    def add_untimed(self):
        try: self.guard(); self.session.untimed(self.right.identities()); self.refresh()
        except (ValueError, RuntimeError) as exc: self.status.setText(str(exc))

    def guard(self):
        b = self.bridge
        if self.candidate:
            from lyricsync.candidate_tracks import check_pairing_source
            from .timeline_views import current_candidate
            cid, token, material = self.candidate
            record = next((r for r in b.document.source_candidates if r['id'] == cid), None)
            if b.document is not self.document or b.epoch != self.epoch or not record or not current_candidate(b.document,record):
                raise ValueError('识别会话已变更，请重新打开配对。')
            check_pairing_source(b.document, cid, token, b.media_controller.index)
        if b.document.id != self.identity or b.document.revision != self.revision:
            raise ValueError('工程已变化，请关闭并重新打开配对台；草案未应用。')
        if self._ai_pending: raise ValueError('AI 草案请求尚未结束')
        if b._candidate_source != 'main' or b._alignment_preview.active:
            raise ValueError('请先应用结果到可编辑主轴')
        if b._candidate_job or b._task.get('running') or b._editor_active or b._editor_composing:
            raise ValueError('请先结束运行任务或文字输入')

    def refresh(self):
        d = self.session.data
        used_a = {i for g in d['groups'] for i in g['anchor_ids']} | self.session.reserved_anchors
        used_p = {i for g in d['groups'] for i in g['piece_ids']}
        for listing in (self.left, self.middle, self.right): listing.clear()
        def add(listing, text, identity, used=False):
            item = QListWidgetItem(text); item.setData(Qt.ItemDataRole.UserRole, identity)
            item.setToolTip(text)
            if used: item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            listing.addItem(item)
        anchors = {a.id: a for a in self.session.anchors}
        pieces = {p['id']: p for p in d['pieces']}
        for a in self.session.anchors:
            add(self.left, f'{a.start:.3f}–{a.end:.3f} s\n{a.text}'+(' [已使用]' if a.id in used_a else ''), a.id, a.id in used_a)
        for p in d['pieces']:
            if p['id'] not in used_p: add(self.right, self.session.text(p), p['id'])
        for g in d['groups']:
            text = '\n'.join(anchors[i].text for i in g['anchor_ids']) or '无时间锚点'
            text += '\n\n' + ('\n'.join(self.session.text(pieces[i]) for i in g['piece_ids']) or '拖入对应译文')
            if 'split_frames' in g: text += '\n[估计分界帧：' + ', '.join(map(str, g['split_frames'])) + '；请试听微调]'
            add(self.middle, text, g['id'])
        self.sync_columns(any(t.text.strip() for t in self.session.project.translations))
        self.status.setText(('AI 草案 · 待确认。' if d['ai'] else '本地草案 · 未应用。') + '导入原文、译文原件及 OCR 边界保持不变。')

    def load_pool(self,incoming,replace=False):
        self.guard()
        self.session.import_pool(incoming,replace=replace)
        self.refresh()
        self.status.setText('译文已载入本窗口。确认应用后写入工程，关闭可放弃。')

    def change(self, action, **kwargs):
        try:
            self.guard(); self.session.operate(action, **kwargs); self.refresh(); return True
        except (ValueError, RuntimeError) as exc:
            self.status.setText(str(exc)); return False

    def history(self, redo):
        try: self.guard(); self.session.history(redo); self.refresh()
        except (ValueError, RuntimeError) as exc: self.status.setText(str(exc))

    def apply(self):
        try:
            self.guard()
            source=self.session.project
            if self.session.initial_groups==self.session.data['groups'] and source.translations!=self.session.initial_pool:
                # Import alone is a pool transaction, even when launched from a candidate.
                # Never adopt that candidate's alternate timeline merely to add words.
                result=deepcopy(self.bridge.document.project)
                prior_ids={t.id for t in self.session.initial_pool}
                kept={t.id for t in source.translations}
                known={t.id for t in result.translations}
                result.translations=[t for t in result.translations if t.id not in prior_ids or t.id in kept]
                result.translations.extend(deepcopy(t) for t in source.translations if t.id not in known and t.id not in prior_ids)
                from lyricsync.capsule_pairing import KEY
                if KEY in source.settings:result.settings[KEY]=deepcopy(source.settings[KEY])
                result.alignment_graph.sync_unbound(result.lyrics,result.translations,result.visuals)
            else:
                result=apply_draft(source,self.session.draft(),confirmed=True,frame_index=self.bridge.media_controller.index)
            result.api_records = deepcopy(self.bridge.document.project.api_records)
            self.bridge._adopt_project(result, '与翻译配对：确认草案')
            self.bridge._changed(); self.accept()
        except (ValueError, RuntimeError) as exc: self.status.setText(str(exc))

    def import_draft(self):
        try:
            self.guard()
            text, ok = QInputDialog.getMultiLineText(self, '导入 ID / 切点草案', '仅接受 groups JSON；不会直接应用工程：')
            if ok:
                self.session.load_response(json.loads(text)); self.refresh()
        except (ValueError, RuntimeError) as exc: self.status.setText(str(exc))

    def request_ai(self):
        try:
            self.guard(); self.bridge._api_check()
            from lyricsync.pairing_api import request_pairing
            snapshot = deepcopy(self.session.project)
            before = self.bridge.document.project.to_dict(); before.pop('api_records', None)
            self._ai_pending = True; self.status.setText('正在请求 AI 对应草案；不会自动应用。')
            def completed(response):
                self._ai_pending = False
                if self._closed: return
                now = self.bridge.document.project.to_dict(); now.pop('api_records', None)
                if self.bridge.document.id != self.identity or now != before:
                    self.status.setText('工程已变化，AI 草案未载入。请重新打开配对台。'); return
                # The existing worker updates the API ledger, not editable content.
                self.revision = self.bridge.document.revision
                self.session.project.api_records = deepcopy(self.bridge.document.project.api_records)
                if isinstance(response, dict) and response.get('status') in ('failed', 'cancelled', 'budget_exhausted'):
                    self.status.setText(response.get('message') or 'AI 请求未完成；本地草案已保留。'); return
                try: self.session.load_response(response); self.refresh()
                except (ValueError, RuntimeError) as exc: self.status.setText(str(exc))
            completed._receive_terminal_status = True
            self.bridge._api_job('AI 辅助对应（仅 ID / 切点）',
                lambda cache, key, settings, cancel, progress: request_pairing(snapshot, cache, key, settings, cancel, progress), completed)
        except (ValueError, RuntimeError) as exc:
            self._ai_pending = False; self.status.setText(str(exc))

    def poll_job(self):
        if self._ai_pending and not self.bridge._task.get('running'):
            self._ai_pending = False
            self.status.setText('AI 请求已结束但未载入草案（取消或达到预算）。请关闭后重开重试；工程配对未改变。')

    def split_frames(self):
        try:
            self.guard()
            selected = self.middle.identities()
            if len(selected) != 1: raise ValueError('请在中间选择一个已有译文的配对槽')
            group = next(g for g in self.session.data['groups'] if g['id'] == selected[0])
            pieces = {p['id']: p for p in self.session.data['pieces']}
            slices = [s for i in group['piece_ids'] for s in pieces[i]['slices']]
            if len(group['piece_ids']) < 2: raise ValueError('先在右侧剃刀切成至少两片，再绑定到同一配对槽')
            from lyricsync.translation_pairing_draft import estimate_split_frames
            initial = group.get('split_frames')
            if initial is None: initial = estimate_split_frames(self.session.project, group['anchor_ids'], slices, self.session.frame_index,
                piece_sizes=[len(pieces[i]['slices']) for i in group['piece_ids']])
            text, ok = QInputDialog.getText(self, '1:M 估计分界（可微调）',
                '每个可见译文胶囊生成一行（合并胶囊不反拆）。以下为按文字长度估计的绝对帧切点，不是实测边界。\n逗号分隔，确认后仍需应用草案；输入 merge 恢复合一行：', text=','.join(map(str, initial)))
            if ok:
                frames = None if text.strip() == 'merge' else [int(x.strip()) for x in text.split(',')]
                self.session.set_split_frames(group['id'], frames); self.refresh()
        except (ValueError, RuntimeError) as exc: self.status.setText(str(exc))
