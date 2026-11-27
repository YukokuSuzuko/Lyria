"""Reconstructed dev5 catalog contracts. Synthetic transport only; no inference."""
from dataclasses import replace
import hashlib
import io
import re
import urllib.error

import pytest
from lyricsync import local_models as lm


def test_catalog_fixed_manifest_and_complete_asr_tokenizer():
    assert set(lm.CATALOG) == {lm.QWEN.id, lm.UVR.id, lm.UVR_HQ1.id, lm.ASR.id, lm.NEXTFIRE.id}
    assert (lm.UVR.repo,lm.UVR.revision,lm.UVR.size) == ('TRvlvr/model_repo','asset-112310332-sha317554b0',66759214)
    assert lm.UVR.files[0].sha256 == '317554b07fe1ea5279a77f2b1520a41ea4b93432560c4ffd08792c30fddf9adc'
    assert lm.ASR.repo == 'Qwen/Qwen3-ASR-0.6B-hf'
    assert lm.ASR.revision == '7f1569a48a89f3e3f4dc3a5c9d28bddd903bc76c'
    assert lm.ASR.size == 1576379761 and len(lm.ASR.files) == 8
    tokenizer = next(f for f in lm.ASR.files if f.name == 'tokenizer.json')
    assert tokenizer.size == 11429653
    assert tokenizer.sha256 == 'fe1fad59be22a41ee293363fcf95fdedbc7c93f3b49270b1d2e18bd1399a7a05'
    assert tokenizer.sha256 != next(f for f in lm.QWEN.files if f.name == 'tokenizer.json').sha256
    for spec in lm.CATALOG.values():
        assert len({f.name for f in spec.files}) == len(spec.files)
        assert all(f.size > 0 and re.fullmatch('[0-9a-f]{64}',f.sha256) for f in spec.files)
        assert not any(f.name.endswith(('.bin','.py','.pkl')) for f in spec.files)
        assert spec.source_url.startswith('https://') and spec.license_url.startswith('https://')
    assert '#license' in lm.UVR.license_url
    assert next(row for row in lm.EVALUATED_MODELS if row['repo']==lm.NEXTFIRE.repo)['enabled'] is True


def test_source_selection_has_pinned_distinct_origins_and_no_implicit_fallback():
    hf = lm.download_url(lm.QWEN,lm.QWEN.files[0],'huggingface')
    ms = lm.download_url(lm.QWEN,lm.QWEN.files[0],'modelscope')
    assert hf.startswith('https://huggingface.co/') and lm.QWEN.revision in hf
    assert ms.startswith('https://modelscope.cn/') and lm.MODEL_SCOPE_REVISION in ms
    assert lm.QWEN.revision not in ms
    assert lm.download_url(lm.UVR,lm.UVR.files[0],'uvr').startswith('https://github.com/TRvlvr/model_repo/releases/download/')
    assert lm.ASR.revision in lm.download_url(lm.ASR,lm.ASR.files[0],'huggingface')


@pytest.mark.parametrize('model,source',[(lm.UVR,'huggingface'),(lm.UVR,'modelscope'),(lm.ASR,'modelscope'),(lm.ASR,'uvr'),(lm.QWEN,'uvr')])
def test_unsupported_source_is_rejected_before_any_cache_or_network(tmp_path,model,source):
    class Opener:
        def open(self,*_,**__): pytest.fail('Unsupported pair cannot connect')
    manager=lm.ModelManager(tmp_path/'cache')
    with pytest.raises(lm.ModelError):manager.download(model.id,source=source,opener=Opener())
    assert not manager.root.exists()


@pytest.mark.parametrize('url,accepted',[
    ('https://github.com/TRvlvr/model_repo/file',True),
    ('https://release-assets.githubusercontent.com/file',True),
    ('https://objects.githubusercontent.com/file',True),
    ('https://github.com.evil.invalid/file',False),
    ('https://arbitrary.githubusercontent.com/file',False),
    ('https://user:password@github.com/file',False),
    ('http://github.com/file',False),
    ('https://github.com:444/file',False),
    ('https://huggingface.co/file',False),
    ('https://tenant.oss-cn-beijing.aliyuncs.com/file',False)])
def test_uvr_redirects_stay_within_reviewed_official_hosts(url,accepted):
    assert lm._trusted_url(url,'uvr') is accepted


def test_download_meter_restored_bytes_not_counted_as_speed():
    clock=[0.0];meter=lm.DownloadMeter(2_000_000_000,clock=lambda:clock[0])
    meter.mark(1_000_000_000);clock[0]=2
    text=meter.update(1_002_000_000)
    assert '1.00 GB / 2.00 GB' in text and '1.00 MB/s' in text
    assert '16 \u5206 38 \u79d2' in text
    meter.mark(1_002_000_000);clock[0]=2.6
    text=meter.update(1_002_001_200)
    assert '2.0 KB/s' in text and text.endswith('\u672a\u77e5')


def test_download_meter_starts_unknown_and_resets_on_restart():
    clock=[0.0];meter=lm.DownloadMeter(10_000,clock=lambda:clock[0])
    meter.mark(5_000);clock[0]=.1
    assert 'B/s' not in meter.update(5_001)
    clock[0]=4;assert 'B/s' in meter.update(9_000)
    clock[0]=5;assert 'B/s' not in meter.update(1_000)


class Reply(io.BytesIO):
    def __init__(self,data,url,status=200,headers=None):
        super().__init__(data);self.url=url;self.status=status;self.headers=headers or {}
    def geturl(self):return self.url
    def getcode(self):return self.status


@pytest.mark.parametrize('original,source',[(lm.QWEN,'huggingface'),(lm.QWEN,'modelscope'),(lm.UVR,'uvr'),(lm.ASR,'huggingface')])
def test_every_supported_source_download_and_manual_import_uses_same_hash_gate(tmp_path,monkeypatch,original,source):
    data=b'synthetic official-manifest fixture'
    name=original.files[0].name
    spec=replace(original,files=(lm.ModelFile(name,len(data),hashlib.sha256(data).hexdigest()),))
    monkeypatch.setitem(lm.CATALOG,spec.id,spec)
    calls=[]
    class Opener:
        def open(self,request,timeout):
            calls.append(request.full_url)
            assert timeout==30
            return Reply(data,request.full_url)
    manager=lm.ModelManager(tmp_path/'download')
    path=manager.download(spec.id,source=source,opener=Opener())
    assert calls==[lm.download_url(spec,spec.files[0],source)]
    assert manager.verify(spec.id)==path
    imported=lm.ModelManager(tmp_path/'imported')
    assert imported.import_folder(path,spec.id)==imported.verify(spec.id)
    assert (path/name).read_bytes()==data


@pytest.mark.parametrize('source,original',[('uvr',lm.UVR),('huggingface',lm.ASR)])
def test_uvr_asr_failure_never_switches_source_or_publishes(tmp_path,monkeypatch,source,original):
    data=b'synthetic';spec=replace(original,files=(lm.ModelFile(original.files[0].name,len(data),hashlib.sha256(data).hexdigest()),))
    monkeypatch.setitem(lm.CATALOG,spec.id,spec);calls=[]
    class Offline:
        def open(self,request,timeout):calls.append(request.full_url);raise urllib.error.URLError('synthetic offline')
    manager=lm.ModelManager(tmp_path/'cache')
    with pytest.raises(lm.ModelError,match='下载中断'):
        manager.download(spec.id,source=source,opener=Offline())
    assert len(calls)==1 and calls[0]==lm.download_url(spec,spec.files[0],source)
    assert not manager.path(spec.id).exists() and not list(manager.root.glob('*.lock'))


@pytest.mark.parametrize('content_range',['bytes 4-2/12','bytes 3-11/12','bytes 4-11/13','garbage'])
def test_invalid_206_does_not_append_or_publish(tmp_path,monkeypatch,content_range):
    data=b'0123456789ab';spec=lm.ModelSpec('synthetic','Official/Test','a'*40,'Test',
        (lm.ModelFile('test.dat',len(data),hashlib.sha256(data).hexdigest()),))
    monkeypatch.setitem(lm.CATALOG,spec.id,spec)
    manager=lm.ModelManager(tmp_path/'cache')
    staging=manager.root/('.download-'+spec.id+'-'+spec.revision);staging.mkdir(parents=True)
    part=staging/'test.dat.part';part.write_bytes(data[:4])
    class BadRange:
        def open(self,request,timeout):return Reply(data[4:],request.full_url,206,{'Content-Range':content_range})
    with pytest.raises(lm.ModelError):manager.download(spec.id,opener=BadRange())
    assert part.read_bytes()==data[:4] and not manager.path(spec.id).exists()
