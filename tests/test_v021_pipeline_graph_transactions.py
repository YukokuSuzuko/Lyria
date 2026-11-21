"""Import/scan checkpoint graph transactions retain evidence without rematch pollution."""
from copy import deepcopy
import pytest
from lyricsync.model import Project,Mapping,Translation,Lyric,Visual
from lyricsync.pipeline import run
from lyricsync.storage import save_project as real_save


def make(tmp_path,with_original=True):
 video=tmp_path/'video.mp4';video.write_bytes(b'controlled scan')
 target=tmp_path/'target.txt';target.write_text('new words',encoding='utf-8')
 source=tmp_path/'source.txt';source.write_text('new words',encoding='utf-8')
 return Project(inputs=dict(video=str(video),translation=str(target),**({'ktv':str(source)} if with_original else {})),
  lyrics=[Lyric('old-l','obsolete source',None,None)],translations=[Translation('old-t','obsolete target')],
  visuals=[Visual('old-v','obsolete source',1.,2.,[],.9,4)],mappings=[Mapping('old',['old-t'],['old-l'],['old-v'],1.,2.)])


@pytest.mark.parametrize('with_original',[True,False])
def test_every_checkpoint_valid_and_old_automatic_evidence_not_rematched(tmp_path,monkeypatch,with_original):
 p=make(tmp_path,with_original);checkpoints=[];vocab=[]
 def save(project,path):
  snapshot=deepcopy(project.to_dict());Project.from_dict(snapshot);checkpoints.append(snapshot);real_save(project,path)
 def scan(*args,**kwargs):
  vocab.extend(args[3]);return [Visual('new-v','new words',3.,4.,[],.99,6)],{'duration':5.}
 monkeypatch.setattr('lyricsync.pipeline.save_project',save);monkeypatch.setattr('lyricsync.pipeline.scan',scan)
 run(p,tmp_path/'result.json')
 assert len(checkpoints)>=3
 assert any(v['id']=='old-v' for v in checkpoints[1]['visuals'])
 assert 'obsolete source' not in vocab
 assert not any(r.id=='old' for r in p.mappings)
 assert not any(v.id=='old-v' for v in p.visuals)
 assert not any(l.id=='old-l' for l in p.lyrics)
 assert not any(t.id=='old-t' for t in p.translations)
 if not with_original:assert p.lyrics==[] and all(not r.lyric_ids for r in p.mappings)


def test_scan_failure_checkpoint_keeps_old_graph_references(tmp_path,monkeypatch):
 p=make(tmp_path);before=deepcopy(p.mappings[0]);checkpoints=[]
 def save(project,path):
  snapshot=project.to_dict();Project.from_dict(snapshot);checkpoints.append(snapshot);real_save(project,path)
 def scan(*args,**kwargs):raise InterruptedError('controlled cancellation')
 monkeypatch.setattr('lyricsync.pipeline.save_project',save);monkeypatch.setattr('lyricsync.pipeline.scan',scan)
 with pytest.raises(InterruptedError):run(p,tmp_path/'cancelled.json')
 assert checkpoints and p.mappings[0]==before
 assert any(l.id=='old-l' for l in p.lyrics) and any(v.id=='old-v' for v in p.visuals)
 Project.from_dict(p.to_dict())
