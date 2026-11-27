"""Opt-in offline synthetic project probe, also collected by PyInstaller.

Uses the live Bridge, its asynchronous jobs and loaded QML window. This is not
physical mouse, audio, GPU, real IME, OCR, online API or user acceptance coverage.
"""
from pathlib import Path
import hashlib
import json
import sys
import time
from PySide6.QtCore import QObject, QTimer


class FoundationProbe(QObject):
    def __init__(self, app, bridge, window, report):
        super().__init__(bridge)
        self.app, self.bridge, self.window = app, bridge, window
        self.report = Path(report)
        self.root = self.report.parent
        self.results = []
        self.timer = QTimer(self)
        self.timer.setInterval(10)
        self.timer.timeout.connect(self.tick)
        self.flow = self.scenario()
        self.waiting = None
        self.deadline = 0.

    def start(self):
        self.timer.start()

    def command(self, name, /, **payload):
        if not self.bridge.command(name, payload):
            raise AssertionError(name + ': ' + self.bridge.state.get('error', 'command rejected'))

    def record(self, name, **details):
        self.results.append(dict(name=name, status='passed', **details))

    def rows(self):
        from lyricsync.editing import mapping_text
        p = self.bridge.document.project
        return [(m.id, mapping_text(p, m), m.start, m.end) for m in p.mappings]

    def tick(self):
        try:
            if self.waiting is not None:
                if not self.waiting():
                    if time.monotonic() > self.deadline:
                        raise TimeoutError('Qt operation did not finish: ' + self.bridge.state.get('error', ''))
                    return
                self.waiting = None
            self.waiting = next(self.flow)
            self.deadline = time.monotonic() + 30
        except StopIteration:
            self.finish(True)
        except Exception as exc:
            self.finish(False, type(exc).__name__ + ': ' + str(exc))

    def finish(self, ok, error=''):
        self.timer.stop()
        from lyricsync import __version__
        screenshot = self.root/'foundation-final.png'
        image = self.window.grabWindow()
        captured = not image.isNull() and image.save(str(screenshot))
        ok = ok and captured
        result = dict(kind='foundation-synthetic-end-to-end', app_version=__version__,
                      ok=ok, status='passed' if ok else 'failed', error=error,
                      qml_window_loaded=self.window is not None,
                      screenshot_saved=bool(captured), checks=self.results,
                      scope='Live Qt/QML app and real Bridge commands, synthetic media only; no network requests',
                      not_verified=['physical mouse interaction', 'audio sync', 'GPU', 'real IME',
                                    'OCR', 'online API', 'user project acceptance'])
        self.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        self.app.exit(0 if ok else 1)

    def scenario(self):
        b = self.bridge
        base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
        video = base/'examples'/'synthetic_mv.mp4'
        if not video.is_file():
            raise FileNotFoundError('Packaged synthetic video is absent')
        video_hash = hashlib.sha256(video.read_bytes()).hexdigest()
        self.command('media.open', path=str(video))
        yield lambda: b.media['ready'] and b.media['displayedFrame'] > 0
        assert b.media['totalFrames'] >= 90
        self.record('real_media_loaded', total_frames=b.media['totalFrames'])
        source = self.root/'repeated.srt'
        source.write_text('1\n00:00:00,200 --> 00:00:00,700\nRepeated chorus\n\n2\n00:00:01,000 --> 00:00:01,500\nRepeated chorus\n', encoding='utf-8')
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        self.command('input.import', role='translation', path=str(source))
        yield lambda: not b.state['task']['running']
        assert not b.state['error'], b.state['error']
        original = self.rows()
        assert len(original) == 2 and original[0][1] == original[1][1]
        identities = [r[0] for r in original]
        assert len(set(identities)) == 2
        self.record('repeated_lyrics_import_distinct_ids', ids=identities)
        self.command('row.edit', id=identities[1], text='Second chorus edited')
        assert self.rows()[0] == original[0]
        assert self.rows()[1][0] == identities[1]
        edited = self.rows()
        self.command('history.undo')
        assert self.rows() == original
        self.command('history.redo')
        assert self.rows() == edited
        self.record('text_edit_undo_redo_preserve_identity')
        self.command('row.setBoundary', id=identities[1], edge='first', frame=20)
        timed = self.rows()
        assert timed[1][0] == identities[1] and timed[1][2] == b.media_controller.index.stamp(20)
        assert timed[0] == original[0]
        self.command('history.undo')
        assert self.rows() == edited
        self.command('history.redo')
        assert self.rows() == timed
        self.record('frame_boundary_edit_undo_redo')
        revision, count = b.document.revision, len(b.history.undo_stack)
        assert not b.command('row.setBoundary', dict(id=identities[1], edge='first', frame=b.media['totalFrames']))
        assert self.rows() == timed and b.document.revision == revision
        assert len(b.history.undo_stack) == count
        self.command('error.clear')
        self.record('invalid_edit_is_atomic')
        self.command('timeline.createRange', first=60, last=75)
        created = [r[0] for r in self.rows() if r[0] not in identities]
        assert len(created) == 1
        self.command('row.edit', id=created[0], text='Repeated chorus')
        expected = self.rows()
        assert len(expected) == 3 and len({r[0] for r in expected}) == 3
        self.record('new_repeated_cue_has_unique_identity', id=created[0])
        self.command('media.seek', frame=70)
        yield lambda: b.media['displayedFrame'] == 70
        assert b.media['requestedFrame'] == 70
        self.record('actual_frame_seek', displayed_frame=70)
        project = self.root/'foundation.lyria'
        self.command('project.save', path=str(project))
        yield lambda: b._save_running is None and project.is_file()
        assert not b.state['project']['dirty'], b.state['error']
        document_id = b.document.id
        self.record('explicit_save', document_id=document_id)
        self.command('project.new')
        if b.state['closeDialog'].get('visible'):
            self.command('close.resolve', choice='discard')
        assert not self.rows()
        self.command('project.open', path=str(project))
        yield lambda: not b.state['task']['running'] and b.document.id == document_id and b.media['ready'] and b.media['displayedFrame'] > 0
        assert self.rows() == expected
        assert b.editor.project is b.document.project
        self.record('save_close_reopen_retains_ids_text_times')
        exported = self.root/'foundation-draft.srt'
        self.command('export.ass', path=str(exported), format='srt', draft=True)
        yield lambda: not b.state['task']['running']
        assert exported.is_file(), b.state['error']
        text = exported.read_text(encoding='utf-8-sig')
        assert text.count('Repeated chorus') == 2 and text.count('Second chorus edited') == 1
        assert len([line for line in text.splitlines() if ' --> ' in line]) == 3
        assert self.rows() == expected and not any(m.confirmed for m in b.document.project.mappings)
        self.record('explicit_draft_export_keeps_all_three_cues_and_confirmation_state')
        assert hashlib.sha256(video.read_bytes()).hexdigest() == video_hash
        assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
        self.record('input_files_unchanged')