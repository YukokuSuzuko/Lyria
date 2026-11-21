"""Offline preparation contract. No download, install, or user settings."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from lyricsync import local_preparation as prep
from lyricsync.local_models import QWEN, UVR, ASR

@pytest.fixture
def backend(monkeypatch):
    events=[]
    class Manager:
        def __init__(self,root): self.root=Path(root)
        def status(self,identity): return {'present':False,'wrong_size':[], 'path':str(self.root)}
        def download(self,identity,**kwargs): events.append(('download',identity,kwargs['source']))
        def ensure_verified(self,identity,**kwargs): events.append(('verify',identity))
    monkeypatch.setattr(prep,'ModelManager',Manager)
    monkeypatch.setattr(prep.shutil,'disk_usage',lambda p:SimpleNamespace(free=100*1024**3))
    monkeypatch.setattr(prep,'probe_runtime',lambda *a,**k: events.append(('probe',a[0])) or {'runtime':{'device':'cpu'}})
    return events, Manager

def test_existing_environment_and_explicit_models(tmp_path,backend):
    events,_=backend
    result=prep.prepare_models(cache_root=tmp_path,runtime_python='selected',device='cpu',download_source='modelscope')
    assert events==[('probe','selected'),('download',QWEN.id,'modelscope'),('verify',QWEN.id),('download',UVR.id,'uvr'),('verify',UVR.id)]
    assert result['runtime_python']=='selected'
    assert ASR.id not in result['prepared_models']

def test_asr_opt_in_and_source_fixed(tmp_path,backend):
    events,_=backend
    prep.prepare_models(cache_root=tmp_path,runtime_python='selected',include_asr=True,download_source='modelscope')
    assert ('download',ASR.id,'huggingface') in events

def test_probe_failure_never_downloads_or_creates_replacement(tmp_path,backend,monkeypatch):
    events,_=backend
    monkeypatch.setattr(prep,'probe_runtime',lambda *a,**k: (_ for _ in ()).throw(ValueError('bad environment')))
    with pytest.raises(ValueError,match='bad environment'):
        prep.prepare_models(cache_root=tmp_path,runtime_python='explicit')
    assert not events

def test_new_environment_then_model_failure_preserves_path(tmp_path,backend,monkeypatch):
    events,manager=backend
    monkeypatch.setattr(prep,'discover_python',lambda **k:'base')
    monkeypatch.setattr(prep.setup,'create_runtime',lambda *a,**k:{'runtime_python':str(a[1]/'Scripts/python.exe'),'runtime':{}})
    monkeypatch.setattr(manager,'download',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('network test failure')))
    with pytest.raises(prep.PreparationError) as error:
        prep.prepare_models(cache_root=tmp_path/'cache')
    assert str(tmp_path/'runtimes') in error.value.partial_result['runtime_python']
    assert 'network test failure' in str(error.value)

def test_missing_python_does_not_create_anything(tmp_path,backend,monkeypatch):
    monkeypatch.setattr(prep,'python_candidates',lambda:[])
    with pytest.raises(ValueError,match='不自动下载 Python'):
        prep.prepare_models(cache_root=tmp_path/'cache')
    assert list(tmp_path.iterdir())==[]

def test_cancel_before_work(tmp_path,backend):
    with pytest.raises(InterruptedError):
        prep.prepare_models(cache_root=tmp_path,cancel=lambda:True)
    assert not backend[0]

def test_low_disk_before_probe(tmp_path,backend,monkeypatch):
    monkeypatch.setattr(prep.shutil,'disk_usage',lambda p:SimpleNamespace(free=1))
    with pytest.raises(ValueError,match='空间不足'):
        prep.prepare_models(cache_root=tmp_path,runtime_python='selected')
    assert not backend[0]

def test_existing_wrong_size_not_silently_replaced(tmp_path,backend,monkeypatch):
    events,manager=backend
    monkeypatch.setattr(manager,'status',lambda *a:{'present':False,'wrong_size':['broken'],'path':str(tmp_path)})
    with pytest.raises(prep.PreparationError,match='大小不符'):
        prep.prepare_models(cache_root=tmp_path,runtime_python='selected')
    assert events==[('probe','selected')]

def test_presence_reports_wrong_size_and_missing(tmp_path):
    from lyricsync.local_models import ModelManager
    manager=ModelManager(tmp_path)
    state=manager.status(UVR.id)
    assert state['missing']==[UVR.files[0].name]
    path=manager.path(UVR.id);path.mkdir(parents=True)
    (path/UVR.files[0].name).write_bytes(b'wrong')
    state=manager.status(UVR.id)
    assert state['wrong_size']==[UVR.files[0].name]
    assert not state['present'] and not state['verified']

def test_only_selected_backup_separator_is_prepared(tmp_path, backend):
    from lyricsync.local_models import UVR_HQ1
    events,_=backend
    result=prep.prepare_models(cache_root=tmp_path,runtime_python='selected',separation_model_id=UVR_HQ1.id)
    assert ('download',UVR_HQ1.id,'uvr') in events
    assert not any(event[1]==UVR.id for event in events)
    assert result['prepared_models']==[QWEN.id,UVR_HQ1.id]
