"""Opt-in synthetic phase-two probe through the real application and Bridge.

The generated files are isolated from all user projects and credentials. The
same entry point runs in the installed EXE, without a Python test runner.
"""
from pathlib import Path
import hashlib
import json
import math
import struct
import wave

from .foundation_probe import FoundationProbe


class EditingProbe(FoundationProbe):
    def finish(self, ok, error=''):
        self.timer.stop()
        from lyricsync import __version__
        screenshot = self.root/'editing-final.png'
        image = self.window.grabWindow()
        captured = not image.isNull() and image.save(str(screenshot))
        ok = ok and captured
        self.report.write_text(json.dumps(dict(
            kind='phase-three-synthetic-editing-and-candidates', app_version=__version__, ok=ok,
            status='passed' if ok else 'failed', error=error, checks=self.results,
            qml_window_loaded=self.window is not None, screenshot_saved=bool(captured),
            scope='Real Qt/QML application and Bridge, generated SRT/LRC/WAV only; no network requests',
            not_verified=['foreground physical interaction', 'audible output and sync', 'GPU',
                          'real IME', 'online API', 'user media/project acceptance']),
            ensure_ascii=False, indent=2), encoding='utf8')
        self.app.exit(0 if ok else 1)

    def new_document(self):
        self.command('project.new')
        if self.bridge.state['closeDialog'].get('visible'):
            from PySide6.QtCore import QObject, QMetaObject
            popup = self.window.findChild(QObject, 'closeDialog')
            if popup is not None:
                QMetaObject.invokeMethod(popup, 'close')
            self.command('close.resolve', choice='discard')

    def scenario(self):
        b = self.bridge
        self.command('project.pureEditing', durationSeconds=60)
        assert b.media['mediaKind'] == 'virtual'
        self.record('no_media_editing_has_explicit_virtual_timebase')
        source = self.root/'millisecond-precision.srt'
        source.write_text('1\n00:00:00,123 --> 00:00:00,987\n重复歌词\n\n2\n00:00:01,234 --> 00:00:02,345\n重复歌词\n', encoding='utf8')
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        self.command('input.import', role='translation', path=str(source))
        yield lambda: not b.state['task']['running']
        rows = self.rows()
        assert len(rows) == 2 and rows[0][1] == rows[1][1]
        assert rows[0][2:] == (.123, .987) and rows[1][2:] == (1.234, 2.345), rows
        assert rows[0][0] != rows[1][0]
        self.record('srt_exact_milliseconds_and_repeated_cue_identity')
        self.command('row.edit', id=rows[1][0], text='第二句已修改')
        changed = self.rows()
        assert changed[0] == rows[0]
        self.command('history.undo')
        assert self.rows() == rows
        self.command('history.redo')
        assert self.rows() == changed
        self.record('pure_editing_text_undo_redo')
        self.command('view.layout', audioHeight=180, audioMode='spectrum')
        assert b.state['workspaceLayout']['audioMode'] == 'spectrum'
        self.command('view.layout', reset=True)
        assert b.state['workspaceLayout']['audioMode'] == 'waveform'
        self.record('four_panel_layout_mode_and_reset')
        project = self.root/'no-media.lyria'
        self.command('project.save', path=str(project))
        yield lambda: b._save_running is None and project.is_file()
        identifier = b.document.id
        self.new_document()
        self.command('project.open', path=str(project))
        yield lambda: not b.state['task']['running'] and b.document.id == identifier
        assert b.media['mediaKind'] == 'virtual' and self.rows() == changed
        self.record('no_media_save_reopen_retains_identity_and_exact_times')
        exported = self.root/'no-media-draft.srt'
        self.command('export.ass', path=str(exported), format='srt', draft=True)
        yield lambda: not b.state['task']['running']
        assert exported.is_file(), b.state.get('error')
        text = exported.read_text(encoding='utf-8-sig')
        assert '00:00:00,123 --> 00:00:00,987' in text
        assert '第二句已修改' in text
        self.record('no_media_srt_export_keeps_millisecond_boundaries')
        self.new_document()
        self.command('project.pureEditing', durationSeconds=60)
        lrc = self.root/'repeated-tags.lrc'
        lrc.write_text('[ar:Synthetic fixture]\n[00:00.25][00:01.50]副歌\n[00:02.50]尾句\n', encoding='utf8')
        self.command('input.import', role='translation', path=str(lrc))
        yield lambda: not b.state['task']['running']
        lrc_rows = self.rows()
        assert len(lrc_rows) == 3, lrc_rows
        assert [round(r[2], 3) for r in lrc_rows] == [.25, 1.5, 2.5]
        self.record('lrc_repeated_tags_imported')
        audio = self.root/'tone.wav'
        rate, duration = 16000, 4
        with wave.open(str(audio), 'wb') as writer:
            writer.setparams((1, 2, rate, rate*duration, 'NONE', 'not compressed'))
            writer.writeframes(b''.join(struct.pack('<h', int(11000*math.sin(2*math.pi*440*i/rate)))
                                       for i in range(rate*duration)))
        audio_hash = hashlib.sha256(audio.read_bytes()).hexdigest()
        self.command('media.open', path=str(audio))
        yield lambda: b.media.get('ready') and b.media.get('mediaKind') == 'audio'
        yield lambda: b.state.get('audioAnalysis', {}).get('status') in ('ready', 'error', 'no_audio')
        analysis = b.state['audioAnalysis']
        assert analysis['status'] == 'ready', analysis.get('error')
        assert len(analysis['waveform']) > 100 and len(analysis['spectrum']) > 10
        assert max(max(pair) for pair in analysis['waveform']) > .2
        assert max(max(column) for column in analysis['spectrum']) > .2
        self.record('actual_audio_decode_and_nonempty_waveform_spectrum',
                    peaks=len(analysis['waveform']), columns=len(analysis['spectrum']),
                    max_frequency=analysis['maxFrequency'])
        self.command('media.seekSeconds', seconds=1.25)
        yield lambda: b.media.get('requestedFrame') == 126
        self.record('audio_only_seek_uses_10ms_grid')
        self.command('stage.set', stage='review')
        audio_project = self.root/'audio.lyria'
        expected = self.rows()
        self.command('project.save', path=str(audio_project))
        yield lambda: b._save_running is None and audio_project.is_file()
        audio_id = b.document.id
        self.new_document()
        self.command('project.open', path=str(audio_project))
        yield lambda: not b.state['task']['running'] and b.document.id == audio_id and b.media.get('ready')
        yield lambda: b.state.get('audioAnalysis', {}).get('status') in ('ready', 'error', 'no_audio')
        assert b.media['mediaKind'] == 'audio' and self.rows() == expected
        assert b.state['audioAnalysis']['status'] == 'ready'
        self.command('stage.set', stage='review')
        self.record('audio_only_save_reopen_and_analysis_refresh')
        assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
        assert hashlib.sha256(audio.read_bytes()).hexdigest() == audio_hash
        self.record('synthetic_input_files_unchanged')
        yield from self.candidate_scenario()
        yield lambda: True

    def candidate_scenario(self):
        """Real bundled candidate code/Bridge; generated evidence, no model/API."""
        from copy import deepcopy
        from lyricsync.model import Project
        from lyricsync.candidate_tracks import capture_source, candidate_from_project, prepare_apply
        b = self.bridge
        baseline = self.rows()
        index = b.media_controller.index
        generated = Project.from_dict(deepcopy(b.document.project.to_dict()))
        target = generated.mappings[0]
        target.start += .05
        target.end += .05
        record = candidate_from_project(capture_source(b.document, 'ocr', index), generated)
        b._candidate_install(record)
        assert self.rows() == baseline
        self.record('candidate_generation_preserves_main_timeline')
        b.media_controller.pause()
        before_position = b.media.get('positionSeconds')
        selected = b.selected
        b.switchCandidateSource('ocr')
        assert b.state['candidateReadOnly'] and b.state['candidateAvailable']
        assert not b.command('row.edit', {'id': target.id, 'text': 'must not be written'})
        assert self.rows() == baseline
        assert b.media.get('positionSeconds') == before_position
        b.switchCandidateSource('vocal')
        assert b.state['candidateReadOnly'] and not b.state['candidateAvailable']
        b.switchCandidateSource('main')
        assert b.selected == selected and not b.state['candidateReadOnly']
        self.record('source_switching_is_read_only_and_preserves_transport_selection')
        plan = prepare_apply(b.document, record['id'], [target.id], frame_index=index)
        assert not any(entry['blocked'] for entry in plan['entries'])
        assert self.rows() == baseline
        b.candidate_apply(plan)
        applied = self.rows()
        assert applied[0][0:2] == baseline[0][0:2] and applied[0][2:] != baseline[0][2:]
        assert applied[1:] == baseline[1:]
        self.command('history.undo')
        assert self.rows() == baseline
        self.command('history.redo')
        assert self.rows() == applied
        self.record('explicit_candidate_apply_is_one_reversible_command')
        b.candidate_ignore(record['id'], [generated.mappings[1].id], True)
        assert b.document.project.settings.get('candidate_review_decisions')
        self.command('history.undo')
        assert not b.document.project.settings.get('candidate_review_decisions')
        self.command('history.redo')
        assert b.document.project.settings.get('candidate_review_decisions')
        self.record('candidate_ignore_undo_redo')
        path = self.root/'candidate-roundtrip.lyria'
        self.command('project.save', path=str(path))
        yield lambda: b._save_running is None and path.is_file()
        identity = b.document.id
        self.new_document()
        self.command('project.open', path=str(path))
        yield lambda: not b.state['task']['running'] and b.document.id == identity and b.media.get('ready')
        assert b.state['candidateSource'] == 'main'
        assert self.rows() == applied
        assert any(row['id'] == record['id'] for row in b.document.source_candidates)
        assert b.document.project.settings.get('candidate_review_decisions')
        self.record('candidate_persistence_reopens_on_main_without_recomputation')
