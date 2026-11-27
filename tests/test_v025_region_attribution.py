"""Region ownership is attached to evidence and survives the editable and read-only projections."""
from copy import deepcopy

from lyricsync.candidate_tracks import capture_source, candidate_from_project, prepare_apply
from lyricsync.frames import FrameIndex
from lyricsync.model import Mapping, Project, Visual
from lyricsync.multi_roi import export_top, project_regions, rescan_settings
from lyricsync.roi_attribution import KEY, inherit, protect, sync
from lyricsync.styles import default_style
from lyricsync.project_document import ProjectDocument, load_document, write_document
from lyricsync.vision import region_suggestions
from test_qt_entry_initialization_dev5 import bridge as bridge
from test_qt_startup_phase5 import application as application


def example():
    p = Project()
    actors = ['speaker-'+c*32 for c in 'abc']
    p.settings['speaker_profiles'] = {k:{'name': name} for k,name in zip(actors,['角色甲','角色乙','合唱'])}
    p.settings['subtitle_styles'] = {k:{'name':k,'style':default_style(p)} for k in ('default','note')}
    regions = [dict(id='top',box=[10,5,90,35],enabled=True,speaker_id=actors[0],style_id='note'),
               dict(id='bottom',box=[10,65,90,95],enabled=True,speaker_id=actors[1])]
    p.video = {'width':100,'height':100,'duration':4}
    p.settings.update(ocr_regions=deepcopy(regions),ocr_multi_region=True)
    for i, r in enumerate(regions):
        p.visuals.append(Visual(r['id'],r['id'],i,i+1,r['box'],.95,4,
                         evidence={'roi_ids':[r['id']], 'roi_owner':r['id'], 'roi_configuration':deepcopy(regions)}))
    return p,regions,actors


def test_legacy_projection_export_and_source_precise_rescan():
    legacy={'roi':[5,7,80,26]}
    before=deepcopy(legacy)
    assert project_regions(legacy,100,100)[0]['id']=='legacy-roi-1' and legacy==before
    p,rs,actors=example()
    assert export_top(p.settings)==65
    assert export_top(dict(p.settings,ocr_regions=[dict(r,style_id='note') for r in rs]))==65
    current=deepcopy(p.settings)
    current['ocr_regions'][0]['box']=[0,0,100,40]
    local=rescan_settings(p,Mapping('x',[],visual_ids=['top']),deepcopy(current))
    assert local['ocr_regions']==[rs[0]] and local['ocr_multi_region']
    assert rescan_settings(p,Mapping('manual',[]),deepcopy(current))==current


def test_same_region_split_cross_role_merge_and_manual_sync():
    p,rs,actors=example()
    rows=[Mapping(k,[],visual_ids=visuals) for k,visuals in
          [('a',['top']),('b',['top']),('both',['top','bottom']),('other',['bottom']),('manual',['top'])]]
    p.replace_timeline(rows)
    inherit(p,p.mappings)
    assert [(r.speaker_name,r.style_id) for r in p.mappings]==[
        ('角色甲','note'),('角色甲','note'),('',''),('角色乙',''),('角色甲','note')]
    protect(p,['manual'],'speaker_id')
    p.mappings[-1].human_edited=True
    p.mappings[1].locked=True
    changed=[dict(rs[0],speaker_id=actors[2],style_id=''),rs[1]]
    assert sync(p,changed,{'top':{'speaker_id'}})==1
    assert [r.speaker_name for r in p.mappings]==['合唱','角色甲','','角色乙','角色甲']
    assert [r.style_id for r in p.mappings]==['note','note','','','note']
    assert sync(p,changed,{'top':{'style_id'}})==2
    assert [r.style_id for r in p.mappings]==['','note','','','']


def test_format3_region_sync_after_save_as_keeps_manual_empty(bridge, tmp_path, monkeypatch):
    import json
    from desktop_qt import roi_controls
    from lyricsync.speakers import speaker_name, style_for

    project, _, actors = example()
    project.replace_timeline([
        Mapping('inherited', [], visual_ids=['top'], start=.25, end=.75,
                speaker_id=actors[0], style_id='note'),
        Mapping('cleared', [], visual_ids=['top'], start=1.25, end=1.75,
                human_edited=True, style_id='note'),
    ])
    # v0.27 removed only the speaker key from the inheritance record on manual clear.
    project.settings[KEY] = {
        'inherited': {'regions':['top'], 'speaker_id':actors[0], 'style_id':'note'},
        'cleared': {'regions':['top'], 'style_id':'note'},
    }
    old = ProjectDocument(project).snapshot(explicit_save=True)
    old['format_version'] = 3
    body = old['project']
    for row in body['mappings'] + body['alignment_graph']['cue_specs']:
        row.pop('speaker_name', None)
    legacy = tmp_path / 'legacy.lyria'
    legacy.write_text(json.dumps(old), encoding='utf8')
    expected = [(r.id, r.start, r.end, r.style_id, style_for(project,r)) for r in project.mappings]
    migrated = tmp_path / 'saved-as.lyria'
    write_document(load_document(legacy).snapshot(explicit_save=True), migrated)
    assert json.loads(migrated.read_text('utf8'))['format_version'] == 4
    bridge.document = load_document(migrated)
    reopened = bridge.document.project
    assert [speaker_name(reopened,r) for r in reopened.mappings] == ['角色甲','']
    before = deepcopy(reopened.to_dict())
    def sync_button(dialog):
        return next(b for b in dialog.buttons() if b.text() == '同步未手改归属')
    monkeypatch.setattr(roi_controls.QMessageBox, 'exec', lambda d: 0)
    monkeypatch.setattr(roi_controls.QMessageBox, 'clickedButton', sync_button)
    assert roi_controls.change(bridge, {'action':'speaker_name','id':'top','value':'合唱'})
    assert [speaker_name(reopened,r) for r in reopened.mappings] == ['合唱','']
    assert [(r.id,r.start,r.end,r.style_id,style_for(reopened,r)) for r in reopened.mappings] == expected
    assert next(r for r in reopened.settings['ocr_regions'] if r['id']=='top')['speaker_name']=='合唱'
    assert 'speaker_name' not in reopened.settings[KEY]['cleared']
    assert bridge.command('history.undo', {})
    assert bridge.document.project.to_dict() == before


def test_candidate_colors_apply_undo_and_reopen(bridge,tmp_path):
    generated,rs,actors=example()
    project=Project.from_dict(generated.to_dict())
    project.visuals=[]
    bridge.document=ProjectDocument(project)
    index=FrameIndex([i/30 for i in range(120)],4.)
    bridge.media_controller.index=index
    capture=capture_source(bridge.document,'ocr',index)
    generated.replace_timeline([Mapping('row-'+v.id,[],visual_ids=[v.id],start=v.start,end=v.end,text_override=v.text)
                                for v in generated.visuals])
    record=candidate_from_project(capture,generated)
    bridge._candidate_install(record)
    before=deepcopy(bridge.document.project.to_dict())
    bridge.switchCandidateSource('ocr')
    assert [(r.speaker_name,r.style_id) for r in bridge._candidate_project.mappings]==[('角色甲','note'),('角色乙','')]
    assert bridge.document.project.to_dict()==before
    bridge.switchCandidateSource('main')
    plan=prepare_apply(bridge.document,record['id'],[r['id'] for r in record['rows']],frame_index=index,add_unmapped=True)
    assert len(bridge.candidate_apply(plan))==2
    assert [(r.speaker_name,r.style_id) for r in bridge.document.main_timeline]==[('角色甲','note'),('角色乙','')]
    final=deepcopy(bridge.document.project.to_dict())
    assert len(final['settings'][KEY])==2
    path=tmp_path/'regions.lyria'
    write_document(bridge.document.snapshot(explicit_save=True),path)
    assert load_document(path).project.to_dict()==final
    assert bridge.command('history.undo',{}) and bridge.document.project.to_dict()==before


def test_multiband_watermark_and_numerals_remain_user_selectable():
    samples=[(i,b,text) for i in range(7) for b,text in
             [([1,1,10,6],'TV'),([43,48,56,53],'31'),([9,77,38,85],f'left{i}'),([68,78,94,86],f'right{i}')]]
    bands=region_suggestions(samples,100,100)
    assert len(bands)==4 and all(b['hits']==7 for b in bands)
    assert [b['examples'][0] for b in bands[:2]]==['left0','right0']
    assert next(r for r in bands if r['examples']==['TV'])['watermark']
    assert next(r for r in bands if r['examples']==['31'])['static_number']


def test_static_shape_keeps_small_glyph_change_and_exact_region_cache():
    from lyricsync.multi_roi import RegionProvider
    import numpy as np
    from test_v024_multi_roi import Provider
    provider=Provider()
    wrapped=RegionProvider(provider,[{'id':'a','box':[0,0,16,16],'enabled':True}])
    pixels=np.full((16,16,3),18,dtype=np.uint8)
    pixels[5:11,3:12]=185
    wrapped.recognize(pixels)
    wrapped.recognize(pixels+1)
    assert len(provider.calls)==1 and wrapped.metrics['a']['static_shape_hits']==1
    changed=pixels.copy()
    changed[11,13]=186
    wrapped.recognize(changed)
    assert len(provider.calls)==2


def test_region_card_and_canvas_commit_geometry_with_one_undo(application,tmp_path):
    from types import SimpleNamespace
    from PySide6.QtCore import QPoint
    from test_qt_timeline_input import timeline_session, find, click, point, drag
    from test_qt_preview_visibility import install_cues, present
    from tools.qt_event_loop_wait import delay
    with timeline_session(application,tmp_path/'profile') as (b,w):
        p=b.document.project
        panel=find(w,'mainPreviewPanel')
        ui=SimpleNamespace(bridge=b,window=w,panel=panel)
        install_cues(ui,resolution=(960,540),layout=False)
        b.media_controller._state.update(mediaKind='video')
        present(ui,3)
        b._startup_visible=False
        b.stage='import'
        b._refresh()
        delay(80)
        add=find(w,'addOcrRegion')
        assert add and add.isVisible()
        def draw(n):
            click(w,add)
            delay(45)
            assert panel.property('roiSelecting')
            video=find(w,'videoPreview')
            rect=video.property('contentRect')
            y=.10 + .20*n
            start=point(video,rect.x()+rect.width()*.15,rect.y()+rect.height()*y)
            end=point(video,rect.x()+rect.width()*.82,rect.y()+rect.height()*(y+.15))
            drag(w,start,end)
            delay(80)
        draw(0)
        first=p.settings['ocr_regions'][0]['id']
        assert find(w,'roiBinding_'+first+'_speaker_name').isVisible()
        box=find(w,'ocrRegion_'+first)
        assert box and box.isVisible() and box.width()>35 and box.height()>10
        before=deepcopy(p.settings['ocr_regions'])
        depth=len(b.history.undo_stack)
        where=point(box)
        drag(w,where,where+QPoint(12,-12))
        assert p.settings['ocr_regions'][0]['box']!=before[0]['box']
        assert len(b.history.undo_stack)==depth+1
        assert b.command('history.undo',{})
        assert b.document.project.settings['ocr_regions']==before
        delay(160)
        for count in (2,3,4):
            draw(count-1)
            assert len(b.document.project.settings['ocr_regions']) == count, (count,b.stage,point(find(w,'addOcrRegion')))
        assert len(b.document.project.settings['ocr_regions'])==4
        assert not find(w,'addOcrRegion').isEnabled()
