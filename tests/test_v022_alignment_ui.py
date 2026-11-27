from copy import deepcopy
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit,QStyleOptionViewItem
from lyricsync.model import Project,Lyric,Translation,Mapping
from lyricsync.project_document import ProjectDocument
from lyricsync.local_models import NEXTFIRE
from desktop_qt.local_alignment_dialog import LocalAlignmentDialog
from desktop_qt.local_alignment_settings import clean_source_state,save_source_state
from desktop_qt.alignment_input import partition,commit_partition,suspected_translation
from lyricsync.editing import History
from test_dev9_readings_dialog import app

def dialog(tmp_path):
 return LocalAlignmentDialog(ProjectDocument(Project(lyrics=[Lyric('a','歌～',None,None)])),initial_options={'cache_root':str(tmp_path),'model_id':NEXTFIRE.id})

def test_confirmation_without_text_change_and_reopen(app,tmp_path):
 d=dialog(tmp_path);d.reading_table.item(0,1).setText('uta')
 # Simulate unchanged generated value, not a manual workaround.
 d._reading_rows[0].update(reading='uta',generated_reading='uta',reading_origin='automatic_suggestion',reading_issues=[dict(message='wave',requires_review=True,kind='wave')])
 d._confirm_all_readings();row=d._current_readings()[0]
 from lyricsync.japanese_readings import invalid_reading_rows
 assert invalid_reading_rows([row])==[]
 assert row['reading']=='uta' and row['reading_confirmed_for']==['歌～','uta']
 state=d.source_state();assert state['reading_rows'][0]['reading_confirmed_for']==['歌～','uta']
 doc=deepcopy(d.document);save_source_state(doc.project,state)
 reopened=LocalAlignmentDialog(doc,initial_options={'cache_root':str(tmp_path),'model_id':NEXTFIRE.id})
 assert reopened._current_readings()[0]['reading_confirmed_for']==['歌～','uta']

def test_manual_edit_confirmed_but_invalid_cannot_confirm(app,tmp_path):
 d=dialog(tmp_path);d.reading_table.item(0,1).setText('uta')
 assert d._current_readings()[0]['reading_origin']=='manual'
 d.reading_table.item(0,1).setText('歌');d._confirm_all_readings()
 assert 'reading_confirmed_for' not in d._current_readings()[0]

def test_changed_original_drops_confirmation():
 state=dict(version=1,text_source='reference',reading_rows=[dict(id='a',text='new',reading='uta',reading_confirmed_for=['old','uta'])])
 assert 'reading_confirmed_for' not in clean_source_state(state)['reading_rows'][0]

def test_translation_never_default_and_main_needs_explicit_confirmation(app,tmp_path):
 p=Project(translations=[Translation('t','我的歌')],mappings=[Mapping('m',['t'])])
 d=LocalAlignmentDialog(ProjectDocument(p),initial_options={'cache_root':str(tmp_path)})
 assert d.text_source.currentData()=='external'
 d.text_source.setCurrentIndex(d.text_source.findData('main'))
 with pytest.raises(ValueError):d._source_rows()
 d.main_source_confirm.setChecked(True)
 assert d._source_rows()[0]['text']=='我的歌'
 assert d._raw_options()['main_source_confirmed']

def test_editor_font_fits_and_buttons_stay(app,tmp_path):
 d=dialog(tmp_path);d.show();app.processEvents()
 index=d.reading_table.model().index(0,1);delegate=d.reading_table.itemDelegateForColumn(1)
 option=QStyleOptionViewItem();option.rect=d.reading_table.visualRect(index)
 editor=delegate.createEditor(d.reading_table.viewport(),option,index);delegate.updateEditorGeometry(editor,option,index)
 assert editor.height()>=editor.fontMetrics().height()+2
 buttons=[d.reading_button,d.reading_scan,d.reading_confirm_all,d.reading_reset]
 before=[(b.isHidden(),b.geometry()) for b in buttons]
 d._confirm_all_readings();d._scan_reading_inputs();app.processEvents()
 assert before==[(b.isHidden(),b.geometry()) for b in buttons]

def test_split_preserves_exact_strings_and_pairs_undo():
 rows,pairs=partition(['歌だ\t我的歌','君へ\t走向你'],'tab')
 p=Project(translations=[Translation('old','保留')]);doc=ProjectDocument(p);before=p.to_dict();history=History()
 assert commit_partition(doc,history,pairs)
 assert [x.text for x in doc.project.lyrics]==['歌だ','君へ']
 assert [x.text for x in doc.project.translations]==['保留','我的歌','走向你']
 assert len(doc.project.alignment_graph.unbound_edges)>=2
 assert len(history.undo_stack)==1
 assert not commit_partition(doc,history,pairs)
 doc.project,_=history.restore(doc.project);assert doc.project.to_dict()==before

def test_split_odd_lines_rejected_not_dropped():
 with pytest.raises(ValueError):partition(['歌','译文','余'],'alternating')
 assert suspected_translation('我的歌') and not suspected_translation('私の歌')

@pytest.mark.parametrize('points',[10,16,22])
def test_font_change_keeps_editor_legible(app,tmp_path,points):
 from PySide6.QtGui import QFont
 d=dialog(tmp_path);d.show();d.reading_table.setFont(QFont('Segoe UI',points));app.processEvents()
 index=d.reading_table.model().index(0,1);option=QStyleOptionViewItem();option.rect=d.reading_table.visualRect(index)
 delegate=d.reading_table.itemDelegateForColumn(1);editor=delegate.createEditor(d.reading_table.viewport(),option,index)
 delegate.updateEditorGeometry(editor,option,index);editor.setText('utau natsuno sorani');editor.show()
 assert editor.height()>=editor.fontMetrics().height()+2
 assert editor.fontMetrics().height()>=d.reading_table.fontMetrics().height()
 if points==16:
  from pathlib import Path
  folder=Path('../evidence/alignment-ui');folder.mkdir(parents=True,exist_ok=True)
  d.settings_scroll.ensureWidgetVisible(d.reading_table);app.processEvents();d.grab().save(str(folder/'reading-editor.png'))

def test_no_model_generation_before_real_probe(app,tmp_path,monkeypatch):
 from lyricsync import local_alignment_runtime as runtime
 d=dialog(tmp_path)
 monkeypatch.setattr(runtime,'run_worker',lambda *a,**k:pytest.fail('unprobed tool must not run'))
 d._generate_readings();assert '检测' in d.status.text()

def test_confirmed_reading_survives_repeated_automatic_fill(app,tmp_path):
 d=dialog(tmp_path);d.reading_table.item(0,1).setText('uta')
 d._reading_rows[0].update(reading='uta',generated_reading='uta',reading_origin='automatic_suggestion')
 d._confirm_all_readings();requested=d._current_readings()
 d._service.action='readings'
 d._outcome(dict(document_id=d.document.id,requested_rows=requested,rows=[dict(requested[0],reading='changed')]),'')
 assert d._current_readings()[0]['reading']=='uta'
 assert d._current_readings()[0]['reading_confirmed_for']==['歌～','uta']


def test_independent_reading_python_never_changes_model_runtime(app,tmp_path,monkeypatch):
 from desktop_qt.local_alignment_settings import clean
 import lyricsync.local_runtime_setup as setup
 d=dialog(tmp_path);d.runtime.setText('model-python');d.reading_runtime.setText('reading-python')
 calls=[]
 monkeypatch.setattr(setup,'probe_reading_tool',lambda python,**kw:calls.append((python,kw['tool'])))
 monkeypatch.setattr(d,'_start_task',lambda work,**kw:work(lambda:False,lambda *a:None))
 d._probe_readings()
 assert calls[0][0]=='reading-python' and d.runtime.text()=='model-python'
 assert clean(d._raw_options())['reading_runtime_python']=='reading-python'
 d._service.action='reading_probe';d._service.context={'runtime_python':'model-python','reading_runtime_python':'old-reading-python'}
 d._outcome({'reading_tool':{'ready':True,'tool':'sudachi','version':'test'}},None)
 assert not d._reading_ready.get('sudachi')
 assert '旧探测' in d.status.text()


def test_suspected_translation_is_highlighted_not_deleted(app,tmp_path):
 d=dialog(tmp_path)
 d._sync_readings([{'id':'cn','text':'我的爱'},{'id':'jp','text':'夢幻'}])
 assert '疑似译文' in d.reading_table.item(0,2).text()
 assert d.reading_table.item(0,0).background().color().name()=='#fff0ad'
 assert '疑似译文' not in d.reading_table.item(1,2).text()
 assert [r['text'] for r in d._current_readings()]==['我的爱','夢幻']


def test_default_sudachi_intent_requires_ready_and_respects_saved_choice(app,tmp_path):
 d=dialog(tmp_path)
 assert d.reading_tool.currentData()=='sudachi'
 assert not d.reading_button.isEnabled()
 d._generate_readings();assert '检测' in d.status.text()
 old=LocalAlignmentDialog(d.document,initial_options={'cache_root':str(tmp_path),'reading_tool':'pykakasi','model_id':NEXTFIRE.id})
 assert old.reading_tool.currentData()=='pykakasi' and not old.reading_button.isEnabled()
 manual=LocalAlignmentDialog(d.document,initial_options={'cache_root':str(tmp_path),'reading_tool':'manual'})
 assert manual.reading_tool.currentData()=='manual'


def test_saved_hard_error_cannot_be_cleared_by_confirmation(app,tmp_path):
 from lyricsync.japanese_readings import invalid_reading_rows
 d=dialog(tmp_path)
 d._reading_rows[0].update(reading='uta',generated_reading='uta',reading_origin='automatic_suggestion',reading_issues=[{'kind':'unmapped','hard_error':True,'message':'unsupported remainder','requires_review':True}])
 d.reading_table.item(0,1).setText('uta');d._confirm_all_readings()
 doc=deepcopy(d.document);save_source_state(doc.project,d.source_state())
 reopened=LocalAlignmentDialog(doc,initial_options={'cache_root':str(tmp_path),'model_id':NEXTFIRE.id})
 row=reopened._current_readings()[0]
 assert row['reading_issues'][0]['hard_error'] is True
 assert invalid_reading_rows([row])


def test_valid_existing_reading_starts_without_sudachi_dependency(app,tmp_path,monkeypatch):
 import desktop_qt.local_alignment_dialog as module
 from PySide6.QtWidgets import QDialog
 d=dialog(tmp_path);d.reading_table.item(0,1).setText('uta');d._confirm_all_readings()
 assert d.reading_tool.currentData()=='sudachi' and not d._reading_ready
 seen=[]
 monkeypatch.setattr(module,'validate_options',lambda document,value:seen.append(value) or value)
 monkeypatch.setattr(module.ModelManager,'status',lambda self,identity:{'present':True})
 monkeypatch.setattr(d,'_generate_readings',lambda:pytest.fail('valid existing reading must not generate'))
 d._accept()
 assert d.result()==QDialog.Accepted and seen[0]['reading_rows'][0]['reading']=='uta'
