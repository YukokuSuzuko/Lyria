"""Partial export must not report success with a stale or mismatched sidecar."""
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from lyricsync.export import export_project
from lyricsync.model import Project, Mapping, Translation


class PartialExportTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.project=Project(video={'width':1920,'height':1080,'duration':2})
        self.project.translations=[Translation('a','新字幕'),Translation('b','未定时')]
        self.project.mappings=[Mapping('a',['a'],start=0,end=1),Mapping('b',['b'])]
        self.output=self.root/'partial.ass';self.report=self.root/'partial_遗漏清单.json'

    def export(self):
        return export_project(self.project,self.output,skip_missing_times=True,overwrite=True)

    def seed(self):
        self.output.write_bytes(b'old subtitle');self.report.write_bytes(b'old report')

    def assert_old(self):
        self.assertEqual(self.output.read_bytes(),b'old subtitle')
        self.assertEqual(self.report.read_bytes(),b'old report')
        self.assertFalse(list(self.root.glob('.lyria-export-*')))

    def test_success_replaces_both_and_cleans_temporary_files(self):
        self.seed();omitted=self.export()
        self.assertIn('新字幕',self.output.read_text(encoding='utf-8-sig'))
        self.assertIn('未定时',self.report.read_text(encoding='utf-8'))
        self.assertEqual(len(omitted),1)
        self.assertFalse(list(self.root.glob('.lyria-export-*')))

    def test_internal_backup_names_cannot_collide_with_user_names(self):
        for suffix in ('ass','srt','lrc'):
            with self.subTest(suffix=suffix):
                self.output=self.root/('original-0.'+suffix)
                self.report=self.root/'original-0_遗漏清单.json'
                self.seed();self.export()
                self.assertIn('新字幕',self.output.read_text(encoding='utf-8-sig'))
                self.assertIn('未定时',self.report.read_text(encoding='utf-8'))

    def test_subtitle_stage_failure_changes_neither_existing_target(self):
        self.seed()
        with patch('lyricsync.export.write_ass',side_effect=PermissionError('stage denied')):
            with self.assertRaises(PermissionError):self.export()
        self.assert_old()

    def test_report_stage_failure_changes_neither_existing_target(self):
        self.seed()
        with patch('lyricsync.storage.atomic_json',side_effect=PermissionError('report denied')):
            with self.assertRaises(PermissionError):self.export()
        self.assert_old()

    def test_stage_failure_leaves_no_new_report(self):
        with patch('lyricsync.export.write_ass',side_effect=PermissionError('stage denied')):
            with self.assertRaises(PermissionError):self.export()
        self.assertFalse(self.output.exists());self.assertFalse(self.report.exists())
        self.assertFalse(list(self.root.glob('.lyria-export-*')))

    def test_second_install_failure_restores_both_originals(self):
        self.seed();replace=os.replace
        def fail_report(src,dst):
            if Path(dst)==self.report:raise PermissionError('report locked')
            return replace(src,dst)
        with patch('lyricsync.export.os.replace',side_effect=fail_report):
            with self.assertRaises(PermissionError):self.export()
        self.assert_old()

    def test_second_install_failure_removes_new_subtitle(self):
        replace=os.replace
        def fail_report(src,dst):
            if Path(dst)==self.report:raise PermissionError('report locked')
            return replace(src,dst)
        with patch('lyricsync.export.os.replace',side_effect=fail_report):
            with self.assertRaises(PermissionError):self.export()
        self.assertFalse(self.output.exists());self.assertFalse(self.report.exists())
        self.assertFalse(list(self.root.glob('.lyria-export-*')))

    def test_rollback_failure_preserves_original_snapshots_and_reports_directory(self):
        self.seed();replace=os.replace
        def fail_install_and_restore(src,dst):
            if Path(dst)==self.report or Path(src).parent.name=='restore':
                raise PermissionError('locked')
            return replace(src,dst)
        with patch('lyricsync.export.os.replace',side_effect=fail_install_and_restore):
            with self.assertRaisesRegex(OSError,'原文件备份保存在') as raised:self.export()
        recovery=list(self.root.glob('.lyria-export-*'))
        self.assertEqual(len(recovery),1)
        self.assertIn(str(recovery[0]),str(raised.exception))
        self.assertEqual((recovery[0]/'originals/0.ass').read_bytes(),b'old subtitle')
        self.assertEqual((recovery[0]/'originals/1.json').read_bytes(),b'old report')
        self.assertEqual(self.report.read_bytes(),b'old report')

    def test_report_input_alias_is_rejected_before_any_write(self):
        self.seed();self.project.inputs={'translation':str(self.report)}
        with self.assertRaisesRegex(ValueError,'不能直接覆盖原导入素材'):self.export()
        self.assert_old()

    def test_backup_failure_preserves_original_pair(self):
        self.seed()
        with patch('lyricsync.export.shutil.copyfile',side_effect=PermissionError('backup denied')):
            with self.assertRaises(PermissionError):self.export()
        self.assert_old()


if __name__=='__main__':unittest.main()
