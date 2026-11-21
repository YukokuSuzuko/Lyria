"""Dev8 real Qt/offscreen entry contracts; not recognition quality acceptance."""
from copy import deepcopy
import pytest
from PySide6.QtWidgets import QDialog,QDialogButtonBox
from test_dev5_feedback_navigation import app,bridge
from test_dev5_feedback_ocr_flow import setup_ocr
from test_qt_entry_initialization_dev5 import settled
from desktop_qt.dialogs import ocr_settings

def test_edit_entry_stays_at_import_without_starting_model(bridge):
    assert bridge.command('editor.start',{'mode':'edit'})
    assert bridge.stage=='import'
    assert bridge.media_controller.index is None
    assert not bridge._candidate_job
    assert bridge.command('editor.continue',{})
    assert bridge.stage=='review'
    assert bridge.media['mediaKind']=='virtual'
    assert not bridge._candidate_job

def test_continue_rejects_other_modes(bridge):
    assert not bridge.command('editor.continue',{})
    assert bridge.stage=='import'

def test_continue_waits_for_selected_media(bridge):
    assert bridge.command('editor.start',{'mode':'edit'})
    bridge.document.project.inputs['video']='selected.mp4'
    before=bridge.document.snapshot()
    assert not bridge.command('editor.continue',{})
    assert bridge.stage=='import'
    assert bridge.document.snapshot()==before

@pytest.mark.parametrize('accept',[False,True])
def test_start_ocr_settings_before_worker(bridge,tmp_path,monkeypatch,accept):
    setup_ocr(bridge,tmp_path)
    order=[]
    def configure(b,providers,**kw):
        assert kw['start_after']
        assert not b._candidate_job
        order.append('settings')
        return accept
    monkeypatch.setattr('desktop_qt.dialogs.ocr_settings',configure)
    monkeypatch.setattr(bridge,'_run_ocr',lambda *a,**k:order.append('ocr'))
    assert bridge.command('ocr.start',{})
    assert not bridge.command('ocr.start',{})
    settled(bridge)
    assert order==(['settings','ocr'] if accept else ['settings'])
    assert not bridge._ocr_settings_pending

def test_changed_media_during_settings_does_not_run(bridge,tmp_path,monkeypatch):
    setup_ocr(bridge,tmp_path)
    calls=[]
    def configure(b,providers,**kw):
        b.document.project.inputs['video']='different.mp4'
        return True
    monkeypatch.setattr('desktop_qt.dialogs.ocr_settings',configure)
    monkeypatch.setattr(bridge,'_run_ocr',lambda *a,**k:calls.append('ocr'))
    assert bridge.command('ocr.start',{})
    settled(bridge)
    assert calls==[]
    assert not bridge._ocr_settings_pending
    assert '未启动 OCR' in bridge.state['error']

def test_missing_material_does_not_open_settings(bridge,monkeypatch):
    calls=[]
    monkeypatch.setattr(bridge,'_start_job',lambda *a,**k:calls.append(a))
    assert not bridge.command('ocr.start',{})
    assert calls==[]

@pytest.mark.parametrize('accept',[False,True])
def test_dialog_returns_explicit_decision(bridge,monkeypatch,accept):
    before=deepcopy(bridge.document.project.settings)
    def inspect(dialog):
        box=dialog.findChild(QDialogButtonBox)
        assert box.button(QDialogButtonBox.StandardButton.Save).text()=='开始 OCR'
        return QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog,'exec',inspect)
    assert ocr_settings(bridge,['CPUExecutionProvider'],start_after=True) is accept
    if not accept:assert bridge.document.project.settings==before


def test_new_cue_uses_current_style_in_one_undo_step(bridge):
    from lyricsync.styles import project_profiles
    p=bridge.document.project
    profiles=project_profiles(p)
    profiles['purple']={'name':'紫色','style':{**profiles['default']['style'],'color':'#AA55FF'}}
    p.settings['subtitle_styles']=profiles
    assert bridge.command('style.select',{'id':'purple'})
    before=len(bridge.history.undo_stack)
    assert bridge.command('row.add',{})
    row=bridge.document.project.mappings[-1]
    assert row.speaker_style['color']=='#AA55FF'
    assert row.speaker_id==''
    assert len(bridge.history.undo_stack)==before+1
    assert bridge.command('history.undo',{})
    assert bridge.document.project.mappings==[]
    assert bridge.command('history.redo',{})
    assert bridge.document.project.mappings[-1].speaker_style['color']=='#AA55FF'


def test_style_commands_respect_candidate_readonly(bridge):
    bridge.switchCandidateSource('ocr')
    before=bridge.document.snapshot()
    assert not bridge.command('style.select',{'id':'default'})
    assert not bridge.command('style.apply',{})
    assert bridge.document.snapshot()==before
