"""v0.27 regressions at the import, protection, candidate and visual-clock boundaries."""
from copy import deepcopy
import pytest

from lyricsync.frames import FrameIndex
from lyricsync.model import Project, Mapping, Translation, Lyric, Visual
from lyricsync.pairing_session import PairingSession
from lyricsync.project_document import ProjectDocument, _validate_snapshot
from lyricsync.translation_pool import import_translation_pool, update_translation_pool, pairing_label
from desktop_qt.entry_initialization import bind_imported_ocr_rows


@pytest.mark.parametrize('extension,body', [
    ('txt','词一\n词二'),
    ('lrc','[00:00.50]词一\n[00:01.50]词二'),
    ('srt','1\n00:00:00,500 --> 00:00:01,100\n词一\n\n2\n00:00:01,500 --> 00:00:02,300\n词二'),
    ('ass','[Script Info]\nScriptType: v4.00+\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\nDialogue: 0,0:00:00.50,0:00:01.00,Style,Actor,0,0,0,,{\\i1}词一\nDialogue: 0,0:00:01.20,0:00:02.10,Style,Actor,0,0,0,,词二'),
    ('ssa','[Script Info]\nScriptType: v4.00\n[Events]\nFormat: Marked, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\nDialogue: Marked=0,0:00:00.50,0:00:01.00,Style,Actor,0,0,0,,词一\nDialogue: Marked=0,0:00:01.20,0:00:02.10,Style,Actor,0,0,0,,词二')])
def test_pairing_right_only_and_file_identity(tmp_path,extension,body):
    p=tmp_path/('translated.'+extension)
    p.write_text(body,'utf8')
    rows=import_translation_pool(p)
    assert [t.text for t in rows]==['词一','词二']
    assert all((t.start,t.end,t.reference,t.subtitle_metadata)==(None,None,None,{}) for t in rows)
    project=Project(lyrics=[Lyric('l','original',None,None)],
                    translations=[Translation('paired','旧一'),Translation('unused','旧二')],
                    mappings=[Mapping('s',['paired'],lyric_ids=['l'],start=.1,end=.8,confirmed=True,locked=True)])
    before=deepcopy(project.to_dict())
    added=update_translation_pool(project,rows,replace=True)
    assert added.to_dict()['mappings']==before['mappings'] and added.lyrics==project.lyrics
    assert [t.id for t in added.translations]==['paired',*[t.id for t in rows]]
    assert update_translation_pool(added,rows,replace=True) is added
    assert project.to_dict()==before
    _validate_snapshot(ProjectDocument(added).snapshot())


def test_middle_slots_split_and_local_import_history():
    p=Project(visuals=[Visual('v','語',.1,.8,[2,2,20,8],.99,3)],
              translations=[Translation('a','已用'),Translation('b','剩余')])
    session=PairingSession(p)
    original=deepcopy(p.to_dict())
    session.operate('source',[session.anchors[0].id])
    session.operate('bind',[session.data['pieces'][0]['id']],target=session.data['groups'][0]['id'])
    groups=deepcopy(session.data['groups'])
    session.import_pool([Translation('n','新增')],replace=True)
    assert session.data['groups']==groups and set(session.texts)=={'a','n'}
    session.history()
    assert set(session.texts)=={'a','b'} and session.data['groups']==groups
    session.history(True)
    assert set(session.texts)=={'a','n'} and session.data['groups']==groups and p.to_dict()==original


def test_incidental_old_ids_never_bind_a_different_observation():
    orig=Project(translations=[Translation('a','A'),Translation('b','B')],mappings=[Mapping('same',['a'])])
    gen=Project(translations=deepcopy(orig.translations),mappings=[Mapping('same',['b']),Mapping('fresh',['a'])])
    bind_imported_ocr_rows(orig,gen)
    assert gen.mappings[0].id!='same' and gen.mappings[1].id=='same'


def test_project_modes_survive_legacy_and_use_real_pts():
    p=Project(lyrics=[Lyric('l','唱词',None,None)],inputs={'audio':'example.wav'})
    d=ProjectDocument(p,entry_mode='separate')
    snap=d.snapshot(explicit_save=True)
    assert _validate_snapshot(snap).entry_mode=='separate'
    del snap['entry_mode']
    assert _validate_snapshot(snap).entry_mode=='audio'
    idx=FrameIndex([0,.02,.12,.18],.3)
    assert idx.display_coordinate(.07)==pytest.approx(2.5)
    assert idx.current(.07)==2 and idx.display_coordinate(.27)==4
    assert pairing_label(Project(translations=[Translation('a','译文')]))=='与原文配对'
    assert pairing_label(p)=='与翻译配对'
