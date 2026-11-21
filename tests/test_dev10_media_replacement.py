"""Dev10 explicit replacement choices preserve text and cancellation state."""
from copy import deepcopy
from test_dev9_media_replacement import bridge, seed


def test_direct_replace_keep_clears_old_fingerprint(bridge, tmp_path, monkeypatch):
    p=seed(bridge); before=deepcopy(p.mappings)
    path=tmp_path/'replacement.mp4';path.write_bytes(b'fixture')
    calls=[]
    monkeypatch.setattr(bridge,'_confirm_media_timing_reuse',lambda:'keep')
    monkeypatch.setattr(bridge.media_controller,'open',calls.append)
    bridge._open_video({'path':str(path)})
    assert p.mappings==before
    assert p.video=={} and p.settings['input_hashes']=={'translation':'text-hash'}
    assert calls==[str(path)] and p.inputs['video']==str(path)


def test_direct_replace_discard_preserves_text_and_styles(bridge,tmp_path,monkeypatch):
    p=seed(bridge); row=p.mappings[0]
    row.text_override='edited lyric';row.locked=True;row.confirmed=True
    row.speaker_id='actor';row.text_segments=[{'start':2.,'end':4.,'text':'old'}]
    p.settings['manual_locked_ids']=['m']
    path=tmp_path/'replacement.mp4';path.write_bytes(b'fixture')
    calls=[]
    monkeypatch.setattr(bridge,'_confirm_media_timing_reuse',lambda:'discard')
    monkeypatch.setattr(bridge.media_controller,'open',calls.append)
    bridge._open_video({'path':str(path)})
    assert row.id=='m' and row.translation_ids==['t'] and row.text_override=='edited lyric'
    assert row.speaker_id=='actor' and row.human_edited
    assert row.start is None and row.end is None and not row.text_segments
    assert not row.locked and not row.confirmed and not p.settings['manual_locked_ids']
    assert p.translations[0].text=='保留的译文'
    assert calls==[str(path)]


def test_cancel_direct_replacement_changes_nothing(bridge,tmp_path,monkeypatch):
    p=seed(bridge);before=p.to_dict()
    path=tmp_path/'replacement.mp4';path.write_bytes(b'fixture')
    calls=[]
    monkeypatch.setattr(bridge,'_confirm_media_timing_reuse',lambda:None)
    monkeypatch.setattr(bridge.media_controller,'open',calls.append)
    bridge._open_video({'path':str(path)})
    assert p.to_dict()==before and not calls


def test_untimed_existing_lyrics_do_not_block_replacement(bridge,tmp_path,monkeypatch):
    p=seed(bridge);p.mappings[0].start=p.mappings[0].end=None
    path=tmp_path/'replacement.mp4';path.write_bytes(b'fixture')
    calls=[]
    monkeypatch.setattr(bridge,'_confirm_media_timing_reuse',lambda:(_ for _ in ()).throw(AssertionError('no timing')))
    monkeypatch.setattr(bridge.media_controller,'open',calls.append)
    bridge._open_video({'path':str(path)})
    assert calls==[str(path)] and len(p.mappings)==1


def test_discard_can_undo_and_redo_without_new_project(bridge,tmp_path,monkeypatch):
    p=seed(bridge); identity=bridge.document.id
    old=tmp_path/'old.mp4';old.write_bytes(b'old');p.inputs['video']=str(old)
    new=tmp_path/'new.mp4';new.write_bytes(b'new')
    before=p.to_dict(); calls=[]
    monkeypatch.setattr(bridge,'_confirm_media_timing_reuse',lambda:'discard')
    monkeypatch.setattr(bridge.media_controller,'open',calls.append)
    bridge._open_video({'path':str(new)})
    bridge._history(False)
    assert bridge.document.id==identity
    assert bridge.document.project.to_dict()==before
    bridge._history(True)
    assert bridge.document.id==identity and bridge.document.project.inputs['video']==str(new)
    assert bridge.document.project.mappings[0].start is None
    assert calls==[str(new),str(old),str(new)]
