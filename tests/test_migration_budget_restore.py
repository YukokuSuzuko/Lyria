import tempfile
import unittest
from pathlib import Path
from lyricsync.storage import Cache
from unittest.mock import patch


class BudgetRestoreTests(unittest.TestCase):
    def test_snapshot_without_cache_keeps_budget_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            a=Cache(Path(folder)/'a.sqlite');b=Cache(Path(folder)/'b.sqlite')
            try:
                call=a.reserve_call('first',2);a.finish_call(call,'ok')
                a.reserve_call('second',2)
                records=a.records()
                self.assertEqual(b.restore_records(records),2)
                self.assertEqual(b.restore_records(records),0)
                self.assertEqual(len(b.records()),2)
                with self.assertRaises(RuntimeError):b.reserve_call('third',2)
                self.assertIsNone(b.get('first'), 'usage ledger is not a response cache')
            finally:a.close();b.close()

    def test_conflicting_cache_lineage_and_bad_rows_are_atomic(self):
        with tempfile.TemporaryDirectory() as folder:
            c=Cache(Path(folder)/'x.sqlite')
            try:
                c.reserve_call('existing',50)
                with self.assertRaises(ValueError):
                    c.restore_records([dict(id=2,time=1,request_hash='new',status='ok',usage='{}'),
                                       dict(id=1,time=1,request_hash='conflict',status='ok',usage='{}')])
                self.assertEqual(len(c.records()),1)
                with self.assertRaises(ValueError):c.restore_records([dict(id=True)])
                same_request_other_call=c.records()[0].copy()
                same_request_other_call['time']+=1
                with self.assertRaises(ValueError):c.restore_records([same_request_other_call])
                self.assertEqual(len(c.records()),1)
            finally:c.close()

    def test_local_ocr_separate_cache_retains_document_budget(self):
        from lyricsync.pipeline import run
        from lyricsync.model import Project, Visual
        from lyricsync.storage import load_project
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);video=root/'video.mp4';video.write_bytes(b'fixture')
            cache=Cache(root/'desktop.sqlite')
            cache.reserve_call('already-spent',50);records=cache.records();cache.close()
            project=Project(inputs={'video':str(video)},api_records=records)
            project.settings['api_enabled']=False
            with patch('lyricsync.pipeline.scan',return_value=([Visual('v','text',1,2,[0,0,10,10],.9,1)],{'duration':3})):
                run(project,root/'ocr.json')
            self.assertEqual(project.api_records,records)
            self.assertEqual(load_project(root/'ocr.json').api_records,records)

    def test_cancelled_local_ocr_checkpoint_retains_budget(self):
        from lyricsync.pipeline import run
        from lyricsync.model import Project
        from lyricsync.storage import load_project
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);video=root/'video.mp4';video.write_bytes(b'fixture')
            cache=Cache(root/'desktop.sqlite');cache.reserve_call('already-spent',50)
            records=cache.records();cache.close()
            project=Project(inputs={'video':str(video)},api_records=records)
            with self.assertRaises(InterruptedError):run(project,root/'ocr.json',cancel=lambda:True)
            self.assertEqual(load_project(root/'ocr.json').api_records,records)
            self.assertEqual(project.stage,'cancelled')

    def test_conflicting_pipeline_cache_refuses_without_erasing_document_ledger(self):
        from lyricsync.pipeline import run
        from lyricsync.model import Project
        from lyricsync.storage import load_project
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);video=root/'video.mp4';video.write_bytes(b'fixture')
            cache=Cache(root/'desktop.sqlite');cache.reserve_call('document-lineage',50)
            records=cache.records();cache.close()
            cache=Cache(root/'ocr.cache.sqlite');cache.reserve_call('different-lineage',50);cache.close()
            project=Project(inputs={'video':str(video)},api_records=records)
            with self.assertRaisesRegex(ValueError,'API 调用记录与工程不一致'):run(project,root/'ocr.json')
            self.assertEqual(project.api_records,records)
            self.assertEqual(load_project(root/'ocr.json').api_records,records)

    def test_worker_failure_keeps_new_pending_call_in_checkpoint(self):
        from lyricsync.pipeline import run
        from lyricsync.model import Project
        from lyricsync.storage import load_project
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);video=root/'video.mp4';video.write_bytes(b'fixture')
            project=Project(inputs={'video':str(video)})
            def fail_after_recording(*args,**kwargs):
                args[2].reserve_call('worker-interrupted-after-reservation',50)
                raise RuntimeError('simulated interruption')
            with patch('lyricsync.pipeline.scan',side_effect=fail_after_recording):
                with self.assertRaisesRegex(RuntimeError,'simulated'):run(project,root/'ocr.json')
            saved=load_project(root/'ocr.json')
            self.assertEqual(saved.stage,'failed')
            self.assertEqual(len(saved.api_records),1)
            self.assertEqual(saved.api_records[0]['status'],'pending')


if __name__=='__main__':unittest.main()
