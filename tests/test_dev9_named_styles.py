"""Actor/style independence, reference propagation and ASS roundtrip regression."""
from copy import deepcopy
import pytest
from lyricsync.model import Project, Mapping
from lyricsync.editing import History
from lyricsync import styles
from lyricsync.speakers import default_style, style_for, speaker_name
from lyricsync.subtitles import import_translation
from lyricsync.speakers import assign_speaker, clear_speaker
from lyricsync.importing import apply_imported_appearance
from lyricsync.export import render_project_ass, project_layout_rows
from lyricsync.editing import split_mapping, merge_mappings


def project():
    return Project(mappings=[Mapping(key, [], start=n, end=n+1,
        text_override=key, text_role='source_import', confirmed=True)
        for n, key in enumerate(('a', 'b', 'c'))])


def test_edit_named_style_updates_reference_and_undo():
    p = project(); h = History()
    profiles = {'main': {'name': 'Main', 'style': default_style(p)}}
    styles.save_project_profiles(p, h, profiles, 'main')
    styles.apply_style(p, h, ['a', 'b'], 'main')
    untouched = deepcopy(style_for(p, p.mappings[2]))
    profiles['main']['style']['color'] = '#123456'
    styles.save_project_profiles(p, h, profiles, 'main')
    assert style_for(p, p.mappings[0])['color'] == '#123456'
    assert style_for(p, p.mappings[1])['color'] == '#123456'
    assert style_for(p, p.mappings[2]) == untouched
    p, _ = h.restore(p)
    assert style_for(p, p.mappings[0])['color'] == '#FFFFFF'


def test_ass_import_retains_actor_and_named_style(tmp_path):
    path = tmp_path/'source.ass'
    path.write_text('[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, Bold, Italic\n'
        'Style: Verse,Arial,32,&H00332211,-1,0\n'
        '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
        'Dialogue: 0,0:00:01.00,0:00:02.00,Verse,Alice,0,0,0,,Hello\n', encoding='utf-8')
    row = import_translation(path)[0]
    assert getattr(row, 'subtitle_metadata', {}).get('actor') == 'Alice'
    assert row.subtitle_metadata['style_name'] == 'Verse'


def configure(p, h):
    profiles = {'verse': {'name': 'Verse', 'style': default_style(p)},
                'chorus': {'name': 'Chorus', 'style': default_style(p, 'cyan')}}
    styles.save_project_profiles(p, h, profiles, 'verse')
    styles.apply_style(p, h, ['a', 'c'], 'verse')
    styles.apply_style(p, h, ['b'], 'chorus')
    return profiles


def test_actor_and_style_independent_many_to_many():
    p = project(); h = History(); configure(p, h)
    before = [style_for(p, r) for r in p.mappings]
    alice = assign_speaker(p, h, ['a', 'b'], 'Alice')
    bob = assign_speaker(p, h, ['c'], 'Bob')
    assert alice != bob
    assert p.mappings[0].style_id != p.mappings[1].style_id
    assert p.mappings[0].style_id == p.mappings[2].style_id
    assert [style_for(p, r) for r in p.mappings] == before
    styles.apply_style(p, h, ['a', 'b', 'c'], 'chorus')
    assert [speaker_name(p, r) for r in p.mappings] == ['Alice', 'Alice', 'Bob']
    clear_speaker(p, h, ['a', 'b'])
    assert all(r.style_id == 'chorus' for r in p.mappings)


def test_overrides_preserve_only_explicit_properties_and_can_clear():
    p = project(); h = History(); profiles = configure(p, h)
    styles.set_style_overrides(p, h, ['a'], {'color': '#112233'})
    profiles['verse']['style'].update(color='#778899', size=36)
    styles.save_project_profiles(p, h, profiles, 'verse')
    assert style_for(p, p.mappings[0])['color'] == '#112233'
    assert style_for(p, p.mappings[0])['size'] == 36
    assert style_for(p, p.mappings[2])['color'] == '#778899'
    styles.apply_style(p, h, ['a'], 'chorus', clear_overrides=False)
    assert style_for(p, p.mappings[0])['color'] == '#112233'
    styles.apply_style(p, h, ['a'], 'chorus')
    assert style_for(p, p.mappings[0])['color'] == '#9BE7FF'
    p, _ = h.restore(p)
    assert p.mappings[0].style_overrides == {'color': '#112233'}


def test_deleted_style_detaches_effective_appearance_and_undo():
    p = project(); h = History(); profiles = configure(p, h)
    styles.set_style_overrides(p, h, ['a'], {'bold': True})
    before = [style_for(p, r) for r in p.mappings]
    del profiles['verse']
    styles.save_project_profiles(p, h, profiles, 'chorus')
    assert not p.mappings[0].style_id and not p.mappings[2].style_id
    assert [style_for(p, r) for r in p.mappings] == before
    p, _ = h.restore(p)
    assert p.mappings[0].style_id == 'verse'
    assert p.mappings[0].style_overrides == {'bold': True}


def test_legacy_project_preserves_snapshot_until_explicit_conversion():
    p = project(); h = History()
    assign_speaker(p, h, ['a'], 'Legacy actor', default_style(p, 'lilac'))
    raw = p.to_dict(); raw.pop('alignment_graph')  # actual pre-graph legacy file
    for row in raw['mappings']:
        row.pop('style_id'); row.pop('style_overrides')
    restored = Project.from_dict(raw).validate_timeline()
    before = style_for(restored, restored.mappings[0])
    assert speaker_name(restored, restored.mappings[0]) == 'Legacy actor'
    styles.apply_style(restored, h, ['a'], clear_overrides=False)
    assert restored.mappings[0].style_id == 'default'
    assert style_for(restored, restored.mappings[0]) == before
    profiles = styles.project_profiles(restored)
    profiles['default']['style']['color'] = '#111111'
    styles.save_project_profiles(restored, h, profiles, 'default')
    assert style_for(restored, restored.mappings[0]) == before


def test_split_inherits_reference_and_merge_rejects_different_reference():
    p = project(); h = History(); profiles = configure(p, h)
    p.mappings[0].text_override = 'hello world'
    a, b = split_mapping(p, 'a', 5, .5)
    assert a.style_id == b.style_id == 'verse'
    profiles['verse']['style']['bold'] = True
    styles.save_project_profiles(p, h, profiles, 'verse')
    assert all(style_for(p, r)['bold'] for r in p.mappings[:2])
    styles.apply_style(p, h, [b.id], 'chorus')
    with pytest.raises(ValueError, match='样式'): merge_mappings(p, [a.id, b.id])


def seed_translations(rows):
    return Project(translations=rows, mappings=[Mapping('mapping-'+str(n), [row.id],
        start=row.start, end=row.end, confirmed=True) for n, row in enumerate(rows)])


def test_ass_roundtrip_preserves_named_links_actor_and_overrides(tmp_path):
    p = project(); h = History(); configure(p, h)
    assign_speaker(p, h, ['a', 'b'], 'Alice'); assign_speaker(p, h, ['c'], 'Bob')
    styles.set_style_overrides(p, h, ['a'], dict(size=32, color='#123456', bold=True))
    p.mappings[0].text_override = 'Same'; p.mappings[2].text_override = 'Same'
    path = tmp_path/'roundtrip.ass'; path.write_text(render_project_ass(p), encoding='utf-8')
    text = path.read_text(encoding='utf-8')
    assert text.count('Style: Verse,') == 1
    assert text.count('Style: Chorus,') == 1
    imported = seed_translations(import_translation(path))
    assert len(apply_imported_appearance(imported)) == 3
    assert [speaker_name(imported, r) for r in imported.mappings] == ['Alice', 'Alice', 'Bob']
    assert imported.mappings[0].style_id == imported.mappings[2].style_id
    assert imported.mappings[0].style_id != imported.mappings[1].style_id
    assert [style_for(imported, r) for r in imported.mappings] == [style_for(p, r) for r in p.mappings]
    assert imported.mappings[0].style_overrides == {'size':32, 'color':'#123456', 'bold':True}
    profiles = styles.project_profiles(imported)
    profiles[imported.mappings[0].style_id]['style']['italic'] = True
    styles.save_project_profiles(imported, History(), profiles, imported.mappings[0].style_id)
    assert style_for(imported, imported.mappings[0])['italic']
    assert style_for(imported, imported.mappings[2])['italic']


def test_original_ass_keeps_overlapping_different_actors(tmp_path):
    from lyricsync.subtitles import import_original
    p = project(); h = History(); configure(p, h)
    assign_speaker(p, h, ['a'], 'Alice'); assign_speaker(p, h, ['c'], 'Bob')
    p.mappings = [p.mappings[0], p.mappings[2]]
    for row in p.mappings: row.start=0; row.end=2; row.text_override='Same'
    path=tmp_path/'actors.ass';path.write_text(render_project_ass(p),encoding='utf-8')
    rows, _ = import_original(path)
    assert len(rows) == 2
    assert {r.subtitle_metadata['actor'] for r in rows} == {'Alice','Bob'}


def test_import_appearance_uses_provenance_and_never_overwrites(tmp_path):
    p = project(); h = History(); configure(p, h); assign_speaker(p,h,['a'],'Alice')
    path=tmp_path/'appearance.ass';path.write_text(render_project_ass(p),encoding='utf-8')
    imported=seed_translations(import_translation(path))
    imported.mappings.reverse()
    assign_speaker(imported, History(), [imported.mappings[-1].id], 'Kept')
    apply_imported_appearance(imported, [r.id for r in imported.mappings])
    assert speaker_name(imported, imported.mappings[-1]) == 'Kept'
    assert not imported.mappings[-1].style_id
    assert styles.style_name(imported, imported.mappings[0]) == 'Verse'


def test_import_invalid_metadata_is_atomic():
    from lyricsync.model import Translation
    rows = [Translation('x', 'X', 0, 1, subtitle_metadata={'actor':'Alice','style_name':'Main'}),
            Translation('y', 'Y', 1, 2, subtitle_metadata={'actor':'Bad\nname','style_name':'Main'})]
    p = seed_translations(rows); before=deepcopy(p.to_dict())
    with pytest.raises(ValueError): apply_imported_appearance(p)
    assert p.to_dict() == before


def test_invalid_style_reference_and_locked_batch_rejected():
    p=project(); h=History(); configure(p,h)
    p.settings['manual_locked_ids']=['b']; before=deepcopy(p.to_dict())
    with pytest.raises(ValueError): styles.apply_style(p,h,['a','b'],'verse')
    assert p.to_dict() == before
    p.mappings[0].style_id='missing'
    with pytest.raises(ValueError,match='命名样式'): p.validate_timeline()


def test_preview_uses_profile_change_without_row_identity_change():
    p=project(); h=History(); profiles=configure(p,h)
    ids=[r.id for r in p.mappings]
    profiles['verse']['style']['font']='Arial';profiles['verse']['style']['italic']=True
    styles.save_project_profiles(p,h,profiles,'verse')
    rows=project_layout_rows(p)
    assert rows[0]['style']['font']=='Arial' and rows[0]['style']['italic']
    assert [r.id for r in p.mappings]==ids


def test_same_name_different_profile_import_never_overwrites_existing(tmp_path):
    p=project();h=History();configure(p,h)
    path=tmp_path/'same-name.ass';path.write_text(render_project_ass(p),encoding='utf-8')
    imported=seed_translations(import_translation(path))
    previous={'name':'Verse','style':dict(default_style(imported), color='#010203')}
    imported.settings['subtitle_styles']={'existing':deepcopy(previous)}
    apply_imported_appearance(imported)
    assert imported.settings['subtitle_styles']['existing']==previous
    assert imported.mappings[0].style_id!='existing'
    rendered=render_project_ass(imported)
    definitions=[line.split(',',1)[0] for line in rendered.splitlines() if line.startswith('Style:')]
    assert len(definitions)==len(set(definitions))
    assert 'Style: Verse' in definitions and 'Style: Verse (2)' in definitions


def test_ass_leading_reset_preserves_effective_supported_attributes(tmp_path):
    source=tmp_path/'reset.ass'
    source.write_text('[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, Bold, Italic\n'
        'Style: Base,Arial,32,&H00FFFFFF,0,0\nStyle: Other,Arial,40,&H00332211,-1,0\n'
        '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
        'Dialogue: 0,0:00:01.00,0:00:02.00,Base,Alice,0,0,0,,{\\c&H0000FF&\\r}Reset\n'
        'Dialogue: 0,0:00:03.00,0:00:04.00,Base,Alice,0,0,0,,{\\rOther\\i1}Other\n',encoding='utf-8')
    p=seed_translations(import_translation(source));apply_imported_appearance(p)
    assert style_for(p,p.mappings[0])['color']=='#FFFFFF'
    assert style_for(p,p.mappings[1])==dict(font='Arial',size=40,color='#112233',bold=True,italic=True)
    assert p.mappings[0].style_id==p.mappings[1].style_id


@pytest.mark.parametrize('names', [('A,B','A，B'),('Same','Same'),('A'*80,'A'*80)])
def test_actor_export_alias_collision_retains_original_names(tmp_path,names):
    p=project();h=History();configure(p,h)
    ids=[assign_speaker(p,h,[row],name) for row,name in zip(('a','b'),names)]
    path=tmp_path/'actor-aliases.ass';path.write_text(render_project_ass(p),encoding='utf-8')
    restored=seed_translations(import_translation(path));apply_imported_appearance(restored)
    assert [speaker_name(restored,r) for r in restored.mappings[:2]]==list(names)
    assert [r.speaker_name for r in restored.mappings[:2]]==ids
    assert all(not r.speaker_id for r in restored.mappings)


def test_eighty_character_style_aliases_roundtrip_separately(tmp_path):
    p=project();h=History();name='S'*80
    profile={'name':name,'style':default_style(p)}
    styles.save_project_profiles(p,h,{'one':deepcopy(profile),'two':deepcopy(profile)},'one')
    styles.apply_style(p,h,['a'],'one');styles.apply_style(p,h,['b'],'two')
    path=tmp_path/'long-style-aliases.ass';path.write_text(render_project_ass(p),encoding='utf-8')
    from lyricsync.subtitles import parse_ass
    assert all(len(value)<=80 for value in parse_ass(path).styles)
    restored=seed_translations(import_translation(path));apply_imported_appearance(restored)
    assert [styles.style_name(restored,r) for r in restored.mappings[:2]]==[name,name]
    assert [r.style_id for r in restored.mappings[:2]]==['one','two']


def test_aliases_keep_distinct_actor_names_without_custom_metadata(tmp_path):
    p=project();h=History();configure(p,h)
    assign_speaker(p,h,['a'],'A,B');assign_speaker(p,h,['b'],'A，B')
    body=render_project_ass(p)
    body='\n'.join(line for line in body.splitlines() if not line.startswith('LyriaIdentityAliases:'))
    path=tmp_path/'third-party.ass';path.write_text(body,encoding='utf-8')
    restored=seed_translations(import_translation(path));apply_imported_appearance(restored)
    assert len({r.speaker_name for r in restored.mappings[:2]})==2


def test_invalid_alias_metadata_rejected_without_guessing(tmp_path):
    p=project();h=History();configure(p,h)
    body=render_project_ass(p)
    body='\n'.join('LyriaIdentityAliases: {"format":"unknown"}' if line.startswith('LyriaIdentityAliases:') else line
                   for line in body.splitlines())
    path=tmp_path/'invalid-aliases.ass';path.write_text(body,encoding='utf-8')
    with pytest.raises(ValueError,match='身份名称映射无效'): import_translation(path)


def test_generated_snapshot_style_id_cannot_collide_with_named_style(tmp_path):
    p=project();h=History()
    styles.save_project_profiles(p,h,{'detached-0':{'name':'Named','style':default_style(p)}},'detached-0')
    styles.apply_style(p,h,['a'],'detached-0')
    path=tmp_path/'snapshot-ids.ass';path.write_text(render_project_ass(p),encoding='utf-8')
    restored=seed_translations(import_translation(path));apply_imported_appearance(restored)
    assert restored.mappings[0].style_id!=restored.mappings[1].style_id


def test_layout_validates_named_profiles_once_per_batch(monkeypatch):
    p=project();h=History();configure(p,h)
    resolver=styles.project_profiles;calls=[]
    def count(project):
        calls.append(project)
        return resolver(project)
    monkeypatch.setattr(styles,'project_profiles',count)
    rows=project_layout_rows(p)
    assert len(rows)==3 and len(calls)==1
    p.settings['subtitle_styles']['verse']['style']['italic']=True
    assert project_layout_rows(p)[0]['style']['italic'] is True
    assert len(calls)==2  # No persistent stale cache between preview generations.


def dev8_cue_style_ass(tmp_path, count=257):
    """dev8 declared one independent SpeakerCueN style per exported cue."""
    lines=['[V4+ Styles]','Format: Name, Fontname, Fontsize, PrimaryColour, Bold, Italic']
    lines += [f'Style: SpeakerCue{n},Arial,32,&H00332211,-1,0' for n in range(count)]
    lines += ['[Events]','Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text']
    from lyricsync.subtitles import ass_time
    lines += [f'Dialogue: 0,{ass_time(n)},{ass_time(n+1)},SpeakerCue{n},Alice,0,0,0,,line {n}'
              for n in range(count)]
    path=tmp_path/'dev8-per-cue-styles.ass';path.write_text('\n'.join(lines),encoding='utf-8')
    return path


def test_dev8_per_cue_style_capacity_keeps_all_rows_and_overflow_appearance(tmp_path):
    rows=import_translation(dev8_cue_style_ass(tmp_path))
    p=seed_translations(rows)
    assert len(apply_imported_appearance(p))==257
    assert len(p.settings['subtitle_styles'])==256
    assert len({row.style_id for row in p.mappings[:256]})==256
    assert not p.mappings[-1].style_id
    assert p.translations[-1].subtitle_metadata['style_name']=='SpeakerCue256'
    assert p.translations[-1].subtitle_metadata['actor']=='Alice'
    assert all(style_for(p,row)==dict(font='Arial',size=32,color='#112233',bold=True,italic=False)
               for row in p.mappings)
    assert all(speaker_name(p,row)=='Alice' for row in p.mappings)
    assert any('256' in warning and '个人样式' in warning for warning in p.warnings)
    assert len(Project.from_dict(p.to_dict()).validate_timeline().mappings)==257


def test_full_profile_table_keeps_overflow_overrides_and_existing_definitions(tmp_path):
    p=seed_translations(import_translation(dev8_cue_style_ass(tmp_path,count=1)))
    p.settings['subtitle_styles']={f'existing-{n}':{'name':f'Existing{n}','style':default_style(p)}
                                   for n in range(256)}
    p.translations[0].subtitle_metadata['overrides']={'italic':True,'color':'#FF0000'}
    before=deepcopy(p.settings['subtitle_styles']);evidence=deepcopy(p.translations[0].subtitle_metadata)
    apply_imported_appearance(p)
    assert p.settings['subtitle_styles']==before
    assert p.translations[0].subtitle_metadata==evidence
    assert not p.mappings[0].style_id and speaker_name(p,p.mappings[0])=='Alice'
    assert style_for(p,p.mappings[0])==dict(font='Arial',size=32,color='#FF0000',bold=True,italic=True)
    assert p.mappings[0].style_overrides=={'italic':True,'color':'#FF0000'}


def test_overflow_style_still_validates_appearance_atomically(tmp_path):
    p=seed_translations(import_translation(dev8_cue_style_ass(tmp_path,count=1)))
    p.settings['subtitle_styles']={f'existing-{n}':{'name':f'Existing{n}','style':default_style(p)}
                                   for n in range(256)}
    p.translations[0].subtitle_metadata['style']['font']='invalid,font'
    before=deepcopy(p.to_dict())
    with pytest.raises(ValueError):apply_imported_appearance(p)
    assert p.to_dict()==before


def test_source_recovery_restores_decorative_layers_but_edit_import_keeps_events():
    from pathlib import Path
    from lyricsync.subtitles import import_original
    source=Path(__file__).resolve().parents[1]/'examples'/'synthetic_ktv.ass'
    before=source.read_bytes()
    lyrics,_=import_original(source)
    assert len(lyrics)==6
    first=next(row for row in lyrics if row.text=='HELLO WORLD' and row.start<2)
    later=next(row for row in lyrics if row.text=='HELLO WORLD' and row.start>12)
    assert len(first.event_ids)==2 and first.id!=later.id
    assert first.subtitle_metadata['style_name']=='Default'
    assert len(import_translation(source))==7
    assert source.read_bytes()==before


@pytest.mark.parametrize('difference',['actor','layer','time','name-only','position','karaoke','style-placement',
                                       'style-bold','style-unknown','effect'])
def test_source_cross_style_union_requires_decorative_evidence(tmp_path,difference):
    from lyricsync.subtitles import import_original
    actor='Bob' if difference=='actor' else 'Alice'
    layer=0 if difference=='layer' else 1
    start='0:00:01.10' if difference=='time' else '0:00:01.00'
    decoration='' if difference=='name-only' else r'{\bord5}'
    position=r'{\pos(100,200)}' if difference=='position' else ''
    karaoke=r'{\k30}' if difference=='karaoke' else r'{\k20}'
    alignment=8 if difference=='style-placement' else 2
    bold=-1 if difference=='style-bold' else 0
    unknown='different' if difference=='style-unknown' else 'same'
    effect='Scroll up;0;100;10' if difference=='effect' else ''
    text=('[V4+ Styles]\nFormat: Name, Fontname, Fontsize, Alignment, Bold, UnknownLayout\n'
        f'Style: Base,Arial,32,2,0,same\nStyle: Other,Arial,32,{alignment},{bold},{unknown}\n'
        '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
        f'Dialogue: 0,0:00:01.00,0:00:02.00,Base,Alice,0,0,0,{effect},{{\\k20}}HELLO\n'
        f'Dialogue: {layer},{start},0:00:02.00,Other,{actor},0,0,0,{effect},{decoration}{position}{karaoke}HELLO\n')
    path=tmp_path/'distinct.ass';path.write_text(text,encoding='utf-8')
    assert len(import_original(path)[0])==2
    assert len(import_translation(path))==2


def test_source_recovery_protects_explicit_independent_named_style_ids(tmp_path):
    from lyricsync.subtitles import parse_ass,recover_lyrics
    p=project();h=History();configure(p,h)
    assign_speaker(p,h,['a','b'],'Alice')
    p.mappings=p.mappings[:2]
    for row in p.mappings:row.start=1;row.end=2;row.text_override='Same'
    path=tmp_path/'named-instances.ass';path.write_text(render_project_ass(p),encoding='utf-8')
    doc=parse_ass(path)
    doc.events[1].layer='1';doc.events[1].raw_text=r'{\bord5}'+doc.events[1].raw_text
    assert len(recover_lyrics(doc))==2


@pytest.mark.parametrize('margins',['100,0,0','0,100,0','0,0,100'])
def test_source_recovery_preserves_different_dialogue_event_margins(tmp_path,margins):
    from pathlib import Path
    from lyricsync.subtitles import import_original
    fixture=Path(__file__).resolve().parents[1]/'examples'/'synthetic_ktv.ass'
    body=fixture.read_text(encoding='utf-8').replace('Glow,,0,0,0,,',f'Glow,,{margins},,')
    path=tmp_path/'event-margins.ass';path.write_text(body,encoding='utf-8')
    assert len(import_original(path)[0])==7
    assert len(import_translation(path))==7


def test_source_recovery_keeps_unknown_event_layout_fields(tmp_path):
    from pathlib import Path
    from lyricsync.subtitles import import_original,parse_ass
    fixture=Path(__file__).resolve().parents[1]/'examples'/'synthetic_ktv.ass'
    body=fixture.read_text(encoding='utf-8').replace('Effect, Text','Effect, PositionRule, Text')
    body=body.replace(',,{',',,custom,{')
    path=tmp_path/'unknown-event-layout.ass';path.write_text(body,encoding='utf-8')
    assert parse_ass(path).events[0].extra_fields=={'positionrule':'custom'}
    assert len(import_original(path)[0])==7
    assert len(import_translation(path))==7
