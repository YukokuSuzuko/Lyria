import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from copy import deepcopy
import pytest
from lyricsync.japanese_readings import scan_reading_inputs, prepare_reading_rows, invalid_reading_rows, pure_symbol_row, temporary_reading_input
from lyricsync.local_transcription import split_singing_text

@pytest.mark.parametrize('text', ['あ〜↑〜↑〜','いえ～','歌(≧▽≦)'])
def test_temporary_cleaning_keeps_source_and_is_nonblocking(text):
    row={'id':'a','text':text};result=scan_reading_inputs([row])
    assert result['rows'][0]['text']==text and row=={'id':'a','text':text}
    assert result['counts']['review']==0
    assert '≧' not in result['rows'][0]['reading_input']

def test_waves_are_long_vowels():
    assert temporary_reading_input('あ〜↑〜↑〜')[0]=='あーーー'

@pytest.mark.parametrize('text',['(≧▽≦)','〜↑〜','♪!!!','♡'])
def test_pure_symbols_preserved_without_reading(text):
    result=prepare_reading_rows([{'id':'a','text':text}],converter=lambda _:pytest.fail('must not convert'))
    assert result[0]['text']==text and pure_symbol_row(result[0])
    assert invalid_reading_rows(result)==[]

@pytest.mark.parametrize('text',['(歌う)','漢字（かな）','123','Ω'])
def test_not_every_bracket_or_unknown_letter_is_silent(text):
    assert not pure_symbol_row({'text':text})

def test_empty_manual_can_be_filled():
    r=prepare_reading_rows([{'id':'a','text':'空','reading':'','reading_origin':'manual'}],converter=lambda _: [{'hepburn':'sora'}])
    assert r[0]['reading']=='sora'

def test_valid_manual_unchanged():
    row={'id':'a','text':'空','reading':'sora','reading_origin':'manual'}
    r=prepare_reading_rows([row],converter=lambda _:pytest.fail('manual'))
    assert r[0]['reading']=='sora' and row['text']=='空'

def test_split_never_drops_or_translates_text():
    text='hello world again! '+('歌う空の下 '*20)
    lines=split_singing_text(text,max_chars=16)
    assert len(lines)>2
    assert ''.join(text.split())==''.join(''.join(lines).split())

def test_pause_uses_existing_space_only_without_timestamps():
    text='alpha beta gamma delta'
    lines=split_singing_text(text,chunks=[{'text':text,'start':0,'end':10}],pauses=[(4,4.5)],max_chars=100)
    assert len(lines)==2 and ''.join(''.join(lines).split())==''.join(text.split())

from test_dev9_readings_dialog import app,dialog
from desktop_qt.local_alignment_dialog import _READING_PROBES,_reading_key,LocalAlignmentDialog
from lyricsync.model import Project,Lyric
from lyricsync.project_document import ProjectDocument
from lyricsync.local_models import NEXTFIRE

def test_session_cache_and_fixed_tool_label(app,tmp_path):
    python=str(tmp_path/'one'/'python.exe')
    _READING_PROBES[_reading_key(python,'sudachi')]={'ready':True,'version':'test'}
    d=LocalAlignmentDialog(ProjectDocument(Project()),initial_options={'runtime_python':python,'cache_root':str(tmp_path),'model_id':NEXTFIRE.id})
    assert d._reading_ready['sudachi']
    assert d.reading_tool.itemText(d.reading_tool.findData('sudachi'))=='Sudachi（日语分词词典）'
    assert d.manage_reading_tool is d.reading_tool
    d.runtime.setText(str(tmp_path/'two'/'python.exe'));assert not d._reading_ready.get('sudachi')
    d.runtime.setText(python);assert d._reading_ready['sudachi']
    assert '已可用' in d.reading_badge.text()
    d.reject()

def test_empty_external_source_falls_back_to_project(app,tmp_path):
    doc=ProjectDocument(Project(lyrics=[Lyric('l','歌',None,None)]))
    d=LocalAlignmentDialog(doc,initial_options={'text_source':'external','source_rows':[],'cache_root':str(tmp_path)})
    assert d.text_source.currentData()=='reference'
    assert d._source_rows()[0]['text']=='歌'
    d.reject()

def test_both_empty_show_entry_guidance(app,tmp_path):
    d=LocalAlignmentDialog(ProjectDocument(Project()),initial_options={'cache_root':str(tmp_path)})
    assert '共 0 句' not in d.source_role.text()
    assert d.source_preview.isHidden()
    d.reject()

def test_invalid_reading_check_uses_same_errors(app,tmp_path):
    d=dialog(tmp_path);d._scan_reading_inputs()
    assert '读音为空' in d.reading_table.item(0,2).text()
    d.reject()

def test_autofill_does_not_repeat_same_failed_request(app,tmp_path,monkeypatch):
    d=dialog(tmp_path);d._reading_ready['sudachi']=True
    calls=[];monkeypatch.setattr(d,'_generate_readings',lambda:calls.append(1))
    d._maybe_generate_readings();d._maybe_generate_readings();assert calls==[1]
    d.reject()

def test_subscript_kaomoji_original_bug_is_removed_only_from_input():
    text='むーんらいと₍ ᐢ. ̫ .ᐢ ₎'
    row=scan_reading_inputs([dict(id='one',text=text)])['rows'][0]
    assert row['text']==text and row['reading_input']=='むーんらいと'
    assert not invalid_reading_rows([dict(row,reading='muunraito')])

def test_symbols_candidate_keeps_original_without_fake_times():
    from lyricsync.nextfire_candidate import candidate_from_nextfire
    from lyricsync.candidate_tracks import capture_source
    from lyricsync.model import Mapping
    doc=ProjectDocument(Project(mappings=[Mapping('a',[],text_override='歌'),Mapping('b',[],text_override='♪')]))
    before=deepcopy(doc.snapshot());rows=[dict(id='a',text='歌',reading='uta'),dict(id='b',text='♪')]
    response=dict(model_id=NEXTFIRE.id,revision=NEXTFIRE.revision,duration=2.,rows=[dict(rows[0],start_time=.2,end_time=1.,path_score=.8),dict(rows[1],reading='',status='nonvocal_symbols',start_time=None,end_time=None,token_spans=[])])
    result=candidate_from_nextfire(capture_source(doc,'vocal'),rows,response)
    assert len(result['rows'])==2 and result['rows'][1]['text']=='♪'
    assert result['rows'][1]['start'] is None and result['rows'][1]['end'] is None
    assert doc.snapshot()==before
    response['rows'][1]['start_time']=1.1
    with pytest.raises(ValueError):candidate_from_nextfire(capture_source(doc,'vocal'),rows,response)

def test_badge_and_header_are_not_clipped(app,tmp_path):
    d=dialog(tmp_path);d.show()
    for _ in range(4):app.processEvents()
    badge=d.reading_badge;parent=badge.parentWidget()
    assert badge.geometry().right() < parent.width()
    assert d.reading_table.horizontalHeader().height()>=d.reading_table.fontMetrics().height()+20
    d.reject()

def test_real_task_retains_independent_probe_after_dialog_closed(app,tmp_path,monkeypatch):
    from desktop_qt.local_model_tasks import model_tasks
    from test_dev9_readings_dialog import wait
    import lyricsync.local_runtime_setup as runtime
    doc=ProjectDocument(Project());options={'runtime_python':'model-python','reading_runtime_python':str(tmp_path/'reading-python'),'cache_root':str(tmp_path),'model_id':NEXTFIRE.id}
    monkeypatch.setattr(runtime,'probe_reading_tool',lambda *a,**k:{'reading_tool':{'tool':'sudachi','ready':True,'version':'fake'}})
    d=LocalAlignmentDialog(doc,initial_options=options);d._probe_readings();d.reject()
    wait(app,lambda:model_tasks().task is None)
    d2=LocalAlignmentDialog(doc,initial_options=options)
    assert model_tasks().context['reading_runtime_python']==options['reading_runtime_python']
    assert d2._reading_ready.get('sudachi') is True
    d2.reject()

def test_queued_fill_does_not_start_after_close(app,tmp_path,monkeypatch):
    d=dialog(tmp_path);d._reading_ready['sudachi']=True
    monkeypatch.setattr(d,'_generate_readings',lambda:pytest.fail('closed dialog must not start new job'))
    d.reject();d._maybe_generate_readings();app.processEvents()

def test_old_wave_warning_is_retired_but_bracket_review_retained():
    base=dict(id='a',text='歌〜',reading='utaa',generated_reading='utaa',reading_origin='automatic_suggestion')
    assert not invalid_reading_rows([dict(base,reading_issues=[dict(kind='elongation',message='old wave warning',requires_review=True)])])
    base['text']='漢字（かな）';base['reading']='kana'
    assert invalid_reading_rows([dict(base,reading_issues=[dict(kind='annotation_review',message='confirm bracket',requires_review=True)])])


def test_qwen_symbols_keep_positions_without_consuming_acoustic_text():
    from lyricsync.local_alignment import qwen_acoustic_rows, candidate_from_alignment
    from lyricsync.local_models import QWEN
    from lyricsync.candidate_tracks import capture_source
    from lyricsync.model import Mapping
    rows=[dict(id='a',text='歌'),dict(id='b',text='♪'),dict(id='c',text='声')]
    original=deepcopy(rows)
    assert [r['id'] for r in qwen_acoustic_rows(rows)]==['a','c']
    doc=ProjectDocument(Project(mappings=[Mapping(r['id'],[],text_override=r['text']) for r in rows]))
    before=deepcopy(doc.snapshot())
    response=dict(model_id=QWEN.id,revision=QWEN.revision,duration=3.,words=[
        dict(text='歌',start_time=.2,end_time=.8),dict(text='声',start_time=1.2,end_time=1.8)])
    result=candidate_from_alignment(capture_source(doc,'vocal'),rows,response)
    assert [r['sentence_id'] for r in result['rows']]==['a','b','c']
    assert [r['start'] for r in result['rows']]==[.2,None,1.2]
    assert result['rows'][1]['text']=='♪' and result['rows'][1]['end'] is None
    assert rows==original and doc.snapshot()==before


def test_qwen_unknown_reading_failure_is_not_pure_symbol():
    from lyricsync.local_alignment import qwen_acoustic_rows
    rows=[dict(id='x',text='漢字',reading='',reading_issues=[dict(kind='conversion_error',hard_error=True)])]
    assert qwen_acoustic_rows(rows)==rows


def test_bundled_default_preserves_explicit_tool_and_custom_path(app,tmp_path,monkeypatch):
    import lyricsync.bundled_reading as bundled
    monkeypatch.setattr(bundled,'bundled_available',lambda:True)
    d=dialog(tmp_path)
    assert d._reading_python()==bundled.BUNDLED_RUNTIME
    assert not d.reading_advanced.isChecked()
    d.reading_tool.setCurrentIndex(d.reading_tool.findData('pykakasi'))
    assert d._reading_python()!=bundled.BUNDLED_RUNTIME
    d.reading_tool.setCurrentIndex(d.reading_tool.findData('sudachi'))
    d.reading_runtime.setText(str(tmp_path/'custom-python'))
    assert d._reading_python()==str(tmp_path/'custom-python')
    d.reject()


def test_bundled_probe_is_real_once_and_not_assumed_ready(app,tmp_path,monkeypatch):
    import lyricsync.bundled_reading as bundled
    monkeypatch.setattr(bundled,'bundled_available',lambda:True)
    d=dialog(tmp_path);d._reading_ready={}
    calls=[];monkeypatch.setattr(d,'_probe_readings',lambda:calls.append(1))
    d._maybe_generate_readings();d._maybe_generate_readings()
    assert calls==[1] and not d._reading_ready.get('sudachi')
    d.reject()


@pytest.mark.parametrize('text,words', [
    ('(Hello_world)','Helloworld'),('(I love you ^)','Iloveyou'),
    ('hello (world_^)!','helloworld'),('(歌_声)','歌声'),('(123_456)','123456'),
    ('(α_ω)','αω')])
def test_words_inside_face_like_brackets_are_never_discarded(text,words):
    from lyricsync.local_alignment import qwen_acoustic_rows
    clean,owners=temporary_reading_input(text)
    assert ''.join(c for c in clean if c.isalnum())==words
    assert all(text[i]==c for i,c in zip(owners,clean))
    row=dict(id='a',text=text)
    assert not pure_symbol_row(row)
    assert qwen_acoustic_rows([row])[0]['id']=='a'
    assert scan_reading_inputs([row])['rows'][0].get('alignment_skip') is None
    assert row['text']==text



def test_qwen_standalone_reading_selector_and_badge_are_disabled(app,tmp_path):
    from lyricsync.local_models import QWEN
    d=dialog(tmp_path)
    d.alignment_model.setCurrentIndex(d.alignment_model.findData(QWEN.id));d._busy_state()
    assert not d.reading_tool.isEnabled() and not d.reading_badge.isEnabled()
    assert not d.reading_group.isEnabled()
    d.alignment_model.setCurrentIndex(d.alignment_model.findData(NEXTFIRE.id));d._busy_state()
    assert d.reading_tool.isEnabled() and d.reading_badge.isEnabled()
    d.reject()



def test_default_shared_runtime_discovery_keeps_explicit_external_choice(app,tmp_path,monkeypatch):
    import lyricsync.shared_runtime as shared
    expected=tmp_path/'shared-runtime'/'python.exe'
    monkeypatch.setattr(shared,'discover_shared_runtime',lambda:expected)
    monkeypatch.delenv('LYRIA_ALIGNMENT_PYTHON',raising=False)
    d=LocalAlignmentDialog(ProjectDocument(Project()),initial_options={'cache_root':str(tmp_path)})
    assert d.runtime.text()==str(expected)
    d.reject()
    d=LocalAlignmentDialog(ProjectDocument(Project()),initial_options={'runtime_python':'explicit-venv','cache_root':str(tmp_path)})
    assert d.runtime.text()=='explicit-venv'
    d.reject()
