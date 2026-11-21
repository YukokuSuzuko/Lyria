import time
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication,QLineEdit,QDialogButtonBox,QCheckBox
from desktop_qt.speaker_dialog import show_speaker_dialog
from lyricsync.model import Project,Mapping
from lyricsync.editing import History
from lyricsync.project_document import ProjectDocument
from lyricsync.speakers import assign_speaker,default_style


@pytest.fixture(scope='module')
def application():
    return QApplication.instance() or QApplication([])


class Bridge:
    def __init__(self):
        self.document=ProjectDocument(Project(mappings=[Mapping('a',[],start=0,end=2,text_override='hello',text_role='source_import')]))
        self.history=History();self.selected='a';self._multi_selected=set();self._editor_composing=False
    def _changed(self):self.document.touch()


def test_dialog_cancel_has_no_project_or_history_effect(application):
    bridge=Bridge();before=bridge.document.snapshot()
    QTimer.singleShot(20,lambda:QApplication.activeModalWidget().reject())
    show_speaker_dialog(bridge)
    assert bridge.document.snapshot()==before and not bridge.history.undo_stack


def test_dialog_explicit_apply_and_default_style_protection(application):
    bridge=Bridge()
    def apply():
        dialog=QApplication.activeModalWidget()
        name=next(x for x in dialog.findChildren(QLineEdit) if x.placeholderText().startswith('例如'))
        name.setText('Singer A')
        assert dialog.windowTitle() == '所选句的说话人'
        assert not dialog.findChildren(QCheckBox)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
    QTimer.singleShot(20,apply);show_speaker_dialog(bridge)
    assert bridge.document.project.mappings[0].speaker_name=='Singer A'
    assert not bridge.document.project.mappings[0].speaker_style
    assert len(bridge.history.undo_stack)==1 and bridge.document.revision==1


def test_real_bridge_layout_carries_style(application,tmp_path):
    from desktop_qt.controller import Bridge as RealBridge
    bridge=RealBridge(storage_dir=tmp_path/'storage')
    try:
        bridge.document.project=Project(mappings=[Mapping('a',[],start=0,end=2,text_override='hello',text_role='source_import')])
        bridge.editor.project=bridge.document.project
        style=default_style(bridge.document.project,'lilac');style['bold']=True
        assign_speaker(bridge.document.project,bridge.history,['a'],'Singer A',style)
        bridge.document.touch();bridge._rebuild_preview();deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            application.processEvents();time.sleep(.01)
            if bridge._layout_revision==bridge.document.revision and bridge._layout_rows:break
        item=bridge._layout_rows[0][0]
        assert item['color']=='#D6B5FF' and item['bold'] is True
        assert item['speakerName']=='Singer A' and item['fontFamily']==style['font']
    finally:bridge.shutdown()
