from copy import deepcopy
import pytest
from lyricsync.editing import History
from lyricsync.model import Project, Mapping
from lyricsync.speakers import assign_speaker, clear_speaker, speaker_name, speaker_names
from lyricsync.project_document import ProjectDocument, write_document, load_document


def test_names_follow_live_references_and_history(tmp_path):
    project = Project(mappings=[Mapping('a', []), Mapping('b', [])])
    project.settings['ocr_regions'] = [dict(id='roi', box=[0, 0, 8, 8], enabled=False, speaker_name='合唱')]
    history = History()
    assign_speaker(project, history, ['a','b'], '主唱')
    assert [r.speaker_name for r in project.mappings] == ['主唱','主唱']
    assert all(not r.speaker_id for r in project.mappings)
    assert 'speaker_profiles' not in project.settings
    assert speaker_names(project) == ['主唱','合唱']
    clear_speaker(project, history, ['a','b'])
    assert speaker_names(project) == ['合唱']
    restored, _ = history.restore(project)
    assert speaker_names(restored) == ['主唱','合唱']
    path = tmp_path/'actors.lyria'
    write_document(ProjectDocument(restored).snapshot(), path)
    assert speaker_names(load_document(path).project) == ['主唱','合唱']


def test_migrate_legacy_names_and_reject_mirror_disagreement():
    old = 'speaker-'+'a'*32
    project = Project(mappings=[Mapping('a', [], speaker_id=old)])
    project.settings.update(speaker_profiles={old:{'name':'旧名'}}, ocr_regions=[
        dict(id='roi',box=[0,0,8,8],enabled=True,speaker_id=old)])
    raw = project.to_dict()
    for r in raw['mappings'] + raw['alignment_graph']['cue_specs']:
        r.pop('speaker_name', None)
    loaded = Project.from_dict(raw).validate_timeline()
    assert loaded.mappings[0].speaker_name == '旧名'
    assert speaker_name(loaded,loaded.mappings[0]) == '旧名'
    assert loaded.settings['ocr_regions'][0]['speaker_name'] == '旧名'
    assert speaker_names(loaded) == ['旧名']
    bad = deepcopy(raw)
    bad['mappings'][0]['speaker_id'] = ''
    with pytest.raises(ValueError):
        Project.from_dict(bad)
    bad = deepcopy(raw)
    bad['settings']['speaker_profiles'].clear()
    with pytest.raises(ValueError):
        Project.from_dict(bad).validate_timeline()
