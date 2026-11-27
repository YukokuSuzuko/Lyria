"""Exercise the real dialog submission with a lightweight model-state double."""
import json
import wave
import pytest
from PySide6.QtWidgets import QDialog
from desktop_qt import local_alignment_settings
from desktop_qt.local_alignment_dialog import LocalAlignmentDialog
from lyricsync.local_models import ModelManager, NEXTFIRE, QWEN, ASR, UVR
from lyricsync.model import Project, Lyric
from lyricsync.project_document import ProjectDocument
from test_qt_local_alignment_phase4 import application as application


@pytest.mark.parametrize('old,expected', [(True, True), ('true', True), (False, False), ('false', False)])
def test_boolean_preferences_round_trip(tmp_path, old, expected):
    path=tmp_path/local_alignment_settings.NAME
    path.write_text(json.dumps({'use_separation':old}), 'utf8')
    assert local_alignment_settings.load(tmp_path)['use_separation'] is expected
    local_alignment_settings.save(tmp_path,local_alignment_settings.load(tmp_path))
    assert json.loads(path.read_text('utf8'))['use_separation'] is expected


def test_missing_separator_preserves_dialog_and_readings(application, tmp_path, monkeypatch):
    from lyricsync import local_alignment
    audio=tmp_path/'input.wav'
    with wave.open(str(audio), 'wb') as out:
        out.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        out.writeframes(bytes(64000))
    doc=ProjectDocument(Project(lyrics=[Lyric('a','夢',None,None,reading='yume'), Lyric('b','空',None,None,reading='sora')]))
    monkeypatch.setattr(local_alignment, 'validate_runtime_path', lambda path: tmp_path/'python.exe')
    monkeypatch.setattr(ModelManager, 'status', lambda self, model: {'present':model==NEXTFIRE.id, 'verification_cached':model==NEXTFIRE.id})
    dialog=LocalAlignmentDialog(doc, default_audio=str(audio), initial_options={'model_id':NEXTFIRE.id,'cache_root':str(tmp_path),'reading_tool':'manual','use_separation':'true'})
    try:
        rows = dialog._current_readings()
        assert [r['reading'] for r in rows] == ['yume','sora']
        dialog._accept()
        assert not dialog.result() and 'HQ_3' in dialog.status.text() and str(tmp_path) in dialog.status.text()
        assert dialog._current_readings() == rows
        dialog.separate.setChecked(False)
        dialog._accept()
        assert dialog.result() == QDialog.DialogCode.Accepted, dialog.status.text()
        assert dialog.options()['use_separation'] is False
    finally:
        dialog.close()


def test_missing_model_names_directory_and_recovery(tmp_path):
    manager=ModelManager(tmp_path)
    with pytest.raises(RuntimeError) as error:
        manager.verify(UVR.id)
    assert 'HQ_3' in str(error.value) and str(manager.path(UVR.id)) in str(error.value) and '取消' in str(error.value)



@pytest.mark.parametrize('seconds,word,available,accepted', [
    (300., '声', False, True), (301., '声', False, False),
    (301., '声', True, True), (301., '♪', False, False)])
def test_qwen_long_anchor_depends_on_actual_audio_and_vocal_rows(application,tmp_path,monkeypatch,
                                                               seconds,word,available,accepted):
    from lyricsync import local_alignment
    audio=tmp_path/'sound.wav'
    with wave.open(str(audio), 'wb') as out:
        out.setparams((1,2,16000,0,'NONE','uncompressed'))
        out.writeframes(bytes(64000))
    doc=ProjectDocument(Project(lyrics=[Lyric('line',word,None,None)]))
    monkeypatch.setattr(local_alignment,'validate_runtime_path',lambda path:tmp_path/'python.exe')
    monkeypatch.setattr(local_alignment,'discover_media_duration',lambda path:seconds)
    monkeypatch.setattr(ModelManager,'status',lambda manager,model:{
        'present':model==QWEN.id or (model==ASR.id and available),'verification_cached':False})
    dialog=LocalAlignmentDialog(doc,default_audio=str(audio),initial_options={
        'model_id':QWEN.id,'duration_seconds':seconds,'cache_root':str(tmp_path),'use_separation':False})
    try:
        dialog._accept()
        assert bool(dialog.result()) is accepted, dialog.status.text()
        if word == '♪':
            assert '原词' in dialog.status.text() and '长音频' not in dialog.status.text()
        elif not accepted:
            assert '长音频' in dialog.status.text() and str(tmp_path) in dialog.status.text()
    finally:
        dialog.close()
