"""Regressions through the existing desktop adapter and persistence boundary."""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch
import threading

from lyricsync.gui import Application
from lyricsync.model import Project, Mapping, Translation
from lyricsync.storage import Cache, save_project, load_project


class DesktopRegressionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = Application(self.root, Path(self.folder.name) / 'app')

    def tearDown(self):
        saver = getattr(self.app, 'project_saver', None)
        if saver:
            saver.close()
        self.app.pause_playback()
        from lyricsync.tk_lifecycle import destroy_root
        destroy_root(self.root)
        self.folder.cleanup()

    def test_open_preserves_wal_cache_and_call_budget_without_modifying_original(self):
        path = Path(self.folder.name) / 'original.lyricsync.json'
        p = Project(translations=[Translation('t', '原中文')], mappings=[Mapping('m', ['t'])])
        save_project(p, path)
        before = path.read_bytes()
        source = Cache(path.with_suffix('.cache.sqlite'))
        source.db.execute('PRAGMA wal_autocheckpoint=0')
        source.put('ocr:test', {'text': '原文'})
        source.put('api:test', {'checked': True})
        call = source.reserve_call('request:test', 1)
        source.finish_call(call, 'ok')
        try:
            with patch.object(self.app, 'load_video'), patch.object(self.app, 'error') as error:
                self.app.open(str(path))
            error.assert_not_called()
            copied = Cache(self.app.project_path.with_suffix('.cache.sqlite'))
            try:
                self.assertEqual(copied.get('ocr:test'), {'text': '原文'})
                self.assertEqual(copied.get('api:test'), {'checked': True})
                self.assertEqual(len(copied.records()), 1)
                with self.assertRaises(RuntimeError):
                    copied.reserve_call('again', 1)
            finally:
                copied.close()
            self.assertEqual(path.read_bytes(), before)
        finally:
            source.close()

    def test_zero_filter_matches_visible_rounded_percentage(self):
        self.app.project.translations = [Translation('t', '歌词')]
        self.app.project.mappings = [Mapping(str(n), ['t'], confidence=c)
                                     for n, c in enumerate((0, .003, .0049, .006, .5))]
        self.app.refresh()
        shown_zero = {i for i in self.app.results.get_children()
                      if self.app.results.item(i, 'values')[-2] == '0%'}
        self.app.only_zero.set(True)
        self.app.refresh()
        self.assertEqual(set(self.app.results.get_children()), shown_zero)

    def test_composition_is_not_applied_or_saved_until_committed(self):
        from lyricsync.editing import mapping_text
        self.app.project.translations = [Translation('t', '原中文')]
        self.app.project.mappings = [Mapping('m', ['t'])]
        self.app.refresh()
        self.app.results.selection_set('m')
        self.app.select_mapping(auto=True)
        box = self.app.review_cards['chinese']
        with patch('lyricsync.live_edit.composition_active', return_value=True):
            box.delete('1.0', 'end')
            box.insert('1.0', 'zhongwen')
            self.app.commit_chinese()
            self.app.autosave()
            self.app.project_saver.flush()
            self.assertEqual(mapping_text(self.app.project, self.app.project.mappings[0]), '原中文')
            saved = load_project(self.app.project_path)
            self.assertEqual(mapping_text(saved, saved.mappings[0]), '原中文')
        with patch('lyricsync.live_edit.composition_active', return_value=False):
            box.delete('1.0', 'end')
            box.insert('1.0', '中文')
            self.app.commit_chinese()
            self.assertEqual(mapping_text(self.app.project, self.app.project.mappings[0]), '中文')


class SaveQueueTests(unittest.TestCase):
    def test_slow_disk_does_not_block_submit_and_latest_snapshot_wins(self):
        from lyricsync.storage import ProjectSaver
        started, release = threading.Event(), threading.Event()
        writes = []
        def writer(project, path):
            if not writes:
                started.set()
                release.wait(3)
            writes.append(project.stage)
        saver = ProjectSaver(writer=writer)
        try:
            p = Project(stage='first')
            saver.submit(p, 'unused.json')
            self.assertTrue(started.wait(1))
            p.stage = 'second'; saver.submit(p, 'unused.json')
            p.stage = 'last'; saver.submit(p, 'unused.json')
            p.stage = 'mutated after submit'
            release.set(); saver.flush()
            self.assertEqual(writes, ['first', 'last'])
        finally:
            release.set(); saver.close()

    def test_save_failure_is_observable_and_retry_recovers(self):
        from lyricsync.storage import ProjectSaver
        events = []
        with patch('lyricsync.storage.save_project'):
            def fail(*args):raise PermissionError('test')
            saver = ProjectSaver(lambda *args: events.append(args), writer=fail)
            saver.submit(Project(), 'unused.json')
            with self.assertRaises(OSError):saver.flush()
            self.assertEqual(events[0][-1], 'PermissionError')
            saver.writer = lambda *args: None
            saver.submit(Project(), 'unused.json');saver.close()


class RescanTests(unittest.TestCase):
    def test_missing_times_use_explicit_window_and_failures_are_traceable(self):
        from lyricsync.rescan import rescan_cue, diagnose_candidates
        from lyricsync.model import Visual
        p=Project(inputs={'video':'fixture.mp4'},video={'duration':10},mappings=[Mapping('m',[])])
        before=p.mappings[0].__dict__.copy()
        with self.assertRaises(ValueError):rescan_cue(p,'m',None,(-1,3))
        def empty(*args,**kwargs):return [],{'ocr_metrics':{'sampled_frames':8}}
        result=rescan_cue(p,'m',None,(2,4),scanner=empty)
        self.assertEqual(result['diagnostics'][0]['code'],'no_reliable_text')
        self.assertEqual(result['window'],[2,4])
        self.assertEqual(p.mappings[0].__dict__,before)
        visual=Visual('v','HELLO',2,3,[0,0,20,20],.8,3,warnings=['boundary uncertain'])
        stages={r['stage'] for r in diagnose_candidates([visual],['GOOD NIGHT'])}
        self.assertEqual(stages,{'source_matching','boundary_verification'})


class ConnectionTests(unittest.TestCase):
    def test_connection_has_room_for_reasoning_and_distinguishes_truncation(self):
        from lyricsync.api import test_connection,IncompleteResponse,error_status
        self.assertEqual(error_status(IncompleteResponse()),'response_truncated')
        with tempfile.TemporaryDirectory() as d:
            cache=Cache(Path(d)/'call.sqlite')
            def reasoning_model(body,key):
                # Reproduced live: 28 reasoning tokens + JSON consumed 35 tokens.
                if body['max_tokens']<35:raise IncompleteResponse()
                return '{"ok":true}',{'completion_tokens':35}
            try:self.assertTrue(test_connection(cache,'fake',transport=reasoning_model)['ok'])
            finally:cache.close()

    def test_test_connection_is_text_only_budgeted_and_does_not_leak_response(self):
        from lyricsync.api import test_connection
        from urllib.error import HTTPError
        with tempfile.TemporaryDirectory() as d:
            cache=Cache(Path(d)/'test.sqlite')
            try:
                bodies=[]
                def transport(body,key):bodies.append(body);return '{"ok":true}',{'total_tokens':5}
                self.assertTrue(test_connection(cache,'SECRET',maximum=2,transport=transport)['ok'])
                self.assertEqual(bodies[0]['messages'][1]['content'],'Connection test.')
                def failed(*args):raise HTTPError('https://example.invalid',401,'SECRET',{},None)
                result=test_connection(cache,'SECRET',maximum=2,transport=failed)
                self.assertFalse(result['ok']);self.assertEqual(result['code'],'authentication_failed')
                self.assertNotIn('SECRET',str(result)+str(cache.records()))
                with self.assertRaises(RuntimeError):test_connection(cache,'SECRET',maximum=2,transport=transport)
                self.assertEqual(len(cache.records()),2)
            finally:cache.close()


if __name__ == '__main__':
    unittest.main()
