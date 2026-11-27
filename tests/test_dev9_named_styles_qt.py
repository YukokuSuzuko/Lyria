"""Offscreen native dialogs and real import command; not foreground acceptance."""
from copy import deepcopy
import pytest
from PySide6.QtWidgets import QMessageBox
from PySide6.QtCore import Qt
from test_qt_entry_initialization_dev5 import bridge, settled
from lyricsync.model import Project, Mapping
from lyricsync.editing import History
from lyricsync.speakers import default_style, assign_speaker, style_for, speaker_name
from lyricsync import styles
from lyricsync.export import render_project_ass
from desktop_qt.style_dialog import StyleManagerDialog


def ass_file(tmp_path):
    p=Project(mappings=[Mapping('a',[],start=1,end=2,text_role='source_import',text_override='原词')])
    h=History(); styles.save_project_profiles(p,h,{'verse':{'name':'Verse','style':default_style(p,'cyan')}},'verse')
    styles.apply_style(p,h,['a'],'verse');assign_speaker(p,h,['a'],'Alice')
    path=tmp_path/'actor-style.ass';path.write_text(render_project_ass(p),encoding='utf-8')
    return path


@pytest.mark.parametrize('role', ['translation', 'source'])
def test_import_command_preserves_actor_style_and_undo(bridge,tmp_path,role):
    assert bridge.command('project.newEntry',{'mode':'audio'})
    before=deepcopy(bridge.document.project.to_dict());depth=len(bridge.history.undo_stack)
    assert bridge.command('input.import',{'role':role,'path':str(ass_file(tmp_path))})
    settled(bridge)
    p=bridge.document.project
    assert len(p.mappings)==1, bridge.state.get('error')
    row=p.mappings[0]
    assert speaker_name(p,row)=='Alice'
    assert styles.style_name(p,row)=='Verse'
    assert style_for(p,row)['color']=='#9BE7FF'
    assert len(bridge.history.undo_stack)==depth+1
    assert bridge.command('history.undo',{})
    assert bridge.document.project.to_dict()==before
    assert bridge.command('history.redo',{})
    row=bridge.document.project.mappings[0]
    assert speaker_name(bridge.document.project,row)=='Alice'
    assert styles.style_name(bridge.document.project,row)=='Verse'


@pytest.mark.parametrize('keep', [False, True])
def test_manager_explicit_override_policy_one_undo(bridge,tmp_path,monkeypatch,keep):
    assert bridge.command('input.import',{'role':'translation','path':str(ass_file(tmp_path))})
    settled(bridge)
    p=bridge.document.project;row=p.mappings[0];bridge.selected=row.id
    styles.set_style_overrides(p,bridge.history,[row.id],{'color':'#112233'})
    before=deepcopy(p.to_dict());depth=len(bridge.history.undo_stack)
    d=StyleManagerDialog(bridge)
    d.keep_overrides.setChecked(keep)
    chosen=d.project_list.findItems('Verse',Qt.MatchFlag.MatchExactly)[0]
    d.project_list.setCurrentItem(chosen)
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.Yes)
    d.apply()
    assert len(bridge.history.undo_stack)==depth+1
    assert style_for(p,p.mappings[0])['color']==('#112233' if keep else '#9BE7FF')
    assert speaker_name(p,p.mappings[0])=='Alice'
    assert bridge.command('history.undo',{})
    assert bridge.document.project.to_dict()==before


def test_dev8_257_style_gui_import_preserves_all_rows_and_one_undo(bridge,tmp_path):
    from test_dev9_named_styles import dev8_cue_style_ass
    before=deepcopy(bridge.document.project.to_dict());depth=len(bridge.history.undo_stack)
    assert bridge.command('input.import',{'role':'translation','path':str(dev8_cue_style_ass(tmp_path))})
    settled(bridge)
    p=bridge.document.project
    assert len(p.mappings)==257,bridge.state.get('error')
    assert len(p.settings['subtitle_styles'])==256 and not p.mappings[-1].style_id
    assert style_for(p,p.mappings[-1])['color']=='#112233'
    assert speaker_name(p,p.mappings[-1])=='Alice'
    assert any('256' in warning and '个人样式' in warning for warning in p.warnings)
    assert len(bridge.history.undo_stack)==depth+1
    assert bridge.command('history.undo',{}) and bridge.document.project.to_dict()==before
    assert bridge.command('history.redo',{})
    assert len(bridge.document.project.mappings)==257
