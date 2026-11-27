import json
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
os.environ['QT_QUICK_BACKEND']='software'
import unittest
from copy import deepcopy
from types import SimpleNamespace
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint, QPointF, QMimeData
from PySide6.QtGui import QDropEvent, QFontDatabase, QFont
from PySide6.QtTest import QTest
from desktop_qt.capsule_pairing_dialog import CapsulePairingDialog, MIME
from tests.test_v022_capsule_pairing import sample
from lyricsync.model import Project

APP=QApplication.instance() or QApplication([])
if os.path.isfile("C:/Windows/Fonts/msyh.ttc"):
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    APP.setFont(QFont("Microsoft YaHei UI",10))

class Bridge:
    def __init__(self):
        self.document=SimpleNamespace(id='test-doc',revision=0,project=sample())
        self.epoch=0
        self.media_controller=SimpleNamespace(index=None)
        self._candidate_source='main'
        self._alignment_preview=SimpleNamespace(active=False)
        self._candidate_job=None;self._task={};self._editor_active=False;self._editor_composing=False
        self.past=[];self.future=[]
    def _adopt_project(self,draft,label):
        self.past.append(deepcopy(self.document.project.to_dict()));self.future=[]
        self.document.project=draft
    def _changed(self):self.document.revision+=1
    def _history(self,redo):
        source,dest=(self.future,self.past) if redo else (self.past,self.future)
        if not source:return
        dest.append(deepcopy(self.document.project.to_dict()))
        self.document.project=Project.from_dict(source.pop());self._changed()

class CapsuleUiTests(unittest.TestCase):
    def setUp(self):
        self.b=Bridge();self.d=CapsulePairingDialog(self.b);self.d.show();APP.processEvents()
    def tearDown(self):
        self.d.close();self.d.deleteLater();APP.processEvents()
    def test_factory_routes_ocr_to_evidence_capsules_and_keeps_non_ocr(self):
        from unittest.mock import patch
        from desktop_qt.pairing_workbench import pairing_dialog
        self.b._entry_mode='ocr'
        with patch('desktop_qt.ocr_source_pairing_dialog.OcrSourcePairingDialog') as old:
            self.assertIs(pairing_dialog(self.b),old.return_value)
            old.assert_called_once_with(self.b,None)
        self.b._entry_mode='vocal'
        other=pairing_dialog(self.b)
        self.assertIsInstance(other,CapsulePairingDialog)
        other.close();other.deleteLater()

    def test_initial_empty_middle_and_three_columns(self):
        self.assertEqual([self.d.left.count(),self.d.middle.count(),self.d.right.count()],[2,0,2])
        self.assertLess(self.d.left.mapToGlobal(QPoint()).x(),self.d.middle.mapToGlobal(QPoint()).x())
        self.assertLess(self.d.middle.mapToGlobal(QPoint()).x(),self.d.right.mapToGlobal(QPoint()).x())
        image=self.d.grab().toImage();self.assertFalse(image.isNull())
    def test_pair_remove_undo_redo(self):
        self.assertTrue(self.d.change('source',ids=['m1']))
        gid=self.d.middle.item(0).data(Qt.ItemDataRole.UserRole)
        self.assertTrue(self.d.change('bind',ids=['t1'],target=gid))
        self.assertEqual(self.d.right.count(),1)
        self.d.history(False);self.assertEqual(self.d.right.count(),2)
        self.d.history(True);self.assertEqual(self.d.right.count(),1)
        self.assertTrue(self.d.change('remove',ids=[gid]))
        self.assertEqual(self.d.middle.count(),0);self.assertEqual(self.d.left.count(),2)
    def test_stale_and_busy_do_not_change_project(self):
        self.b.document.revision+=1
        self.assertFalse(self.d.change('source',ids=['m1']))
        self.assertEqual(self.d.middle.count(),0)
        self.b._task={'running':True}
        self.assertFalse(self.d.change('source',ids=['m1']))
    def test_razor_click_direct_and_no_dialog(self):
        self.d.razor.setChecked(True);APP.processEvents()
        rect=self.d.right.visualItemRect(self.d.right.item(0))
        metrics=self.d.right.fontMetrics()
        point=QPoint(rect.left()+14+metrics.horizontalAdvance('甲'),rect.top()+19)
        QTest.mouseClick(self.d.right.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point)
        APP.processEvents()
        self.assertEqual(self.d.right.count(),3,self.d.status.text())
        self.assertIsNone(APP.activeModalWidget())
    def test_drop_events_use_revision_and_keep_all_text(self):
        def drop(kind, ids, revision=None):
            data={'document':self.d.identity, 'revision':self.d.revision if revision is None else revision,
                  'kind':kind, 'ids':ids}
            mime=QMimeData();mime.setData(MIME,json.dumps(data).encode('utf8'))
            pos=self.d.middle.visualItemRect(self.d.middle.item(0)).center() if self.d.middle.count() else QPoint(20,20)
            event=QDropEvent(QPointF(pos),Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
            self.d.middle.dropEvent(event);APP.processEvents();return event.isAccepted()
        self.assertTrue(drop('source',['m1']))
        self.assertFalse(drop('translation',['t1'],revision=-1))
        self.assertTrue(drop('translation',['t1']))
        self.assertEqual(self.d.right.count(),1)
        self.assertEqual(self.b.document.project.mappings[0].start,1.0)

    def test_ctrl_selection_preserved(self):
        for index,mods in [(0,Qt.KeyboardModifier.NoModifier),(1,Qt.KeyboardModifier.ControlModifier)]:
            p=self.d.right.visualItemRect(self.d.right.item(index)).center()
            QTest.mouseClick(self.d.right.viewport(),Qt.MouseButton.LeftButton,mods,p)
        self.assertEqual(len(self.d.right.selectedItems()),2)
        self.assertTrue(self.d.change('merge',ids=self.d.right.identities()))
        self.assertEqual(self.d.right.count(),1)

if __name__=='__main__':unittest.main()
