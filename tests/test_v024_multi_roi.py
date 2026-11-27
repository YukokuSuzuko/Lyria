import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path
import numpy as np
import pytest
from lyricsync.multi_roi import RegionProvider, validate_regions, bounding_box, scan_regions
from lyricsync.model import Detection


def regions():
    return [dict(id='left', box=[10,10,30,30], enabled=True),
            dict(id='right', box=[20,10,40,30], enabled=True)]


class Provider:
    signature = {'engine': 'test'}
    def __init__(self, results=()):
        self.results = iter(results)
        self.calls = []
    def recognize(self, image):
        self.calls.append(image.copy())
        return next(self.results, [])


def test_region_validation_and_bounds():
    assert bounding_box(validate_regions(regions(), 50, 40)) == [10,10,40,30]
    for bad in ([], regions()*3, [dict(id='x',box=[0,0,0,1])],
                [dict(id='x',box=[0,0,1.0,2])],
                [dict(id='x',box=[0,0,1,2],enabled=False)],
                [dict(id='x',box=[0,0,1,2]),dict(id='x',box=[0,0,1,2])]):
        with pytest.raises(ValueError):validate_regions(bad,50,40)


def test_pixel_coordinates_dedup_and_provider_objects_untouched():
    a = Detection('hello', [10,2,18,10], .8, {'source_channels':['raw']})
    b = Detection('hello', [0,2,8,10], .9, {'source_channels':['raw']})
    before = deepcopy(a)
    provider = Provider([[a],[b]])
    wrapped = RegionProvider(provider,regions())
    image = np.arange(20*30*3,dtype=np.uint8).reshape(20,30,3)
    result = wrapped.recognize(image)
    assert len(result) == 1 and result[0].box == [10,2,18,10]
    assert result[0].evidence['roi_ids'] == ['left','right']
    assert result[0].confidence == .8 and result[0].evidence['roi_owner'] == 'left'  # complete box wins
    assert a == before and 'roi_ids' not in b.evidence
    assert np.array_equal(provider.calls[0],image[:,:20])
    assert np.array_equal(provider.calls[1],image[:,10:30])


def test_same_text_in_distinct_positions_is_not_deduped():
    p = Provider([[Detection('same',[0,1,4,8],.9)], [Detection('same',[10,1,14,8],.9)]])
    assert len(RegionProvider(p,regions()).recognize(np.zeros((20,30,3),np.uint8))) == 2


def test_disabled_region_no_inference_and_cache_signature_isolated():
    two = RegionProvider(Provider(),regions())
    r = regions();r[1]['enabled'] = False
    p = Provider()
    one = RegionProvider(p,r)
    one.recognize(np.zeros((20,20,3),np.uint8))
    assert len(p.calls) == 1 and one.signature != two.signature
    r[0]['box'][0] = 11
    assert RegionProvider(Provider(),r).signature != one.signature


def test_cancellation_between_regions():
    calls = []
    class Cancelling(Provider):
        def recognize(self,image):calls.append(True);return []
    with pytest.raises(InterruptedError):
        RegionProvider(Cancelling(),regions(),lambda:bool(calls)).recognize(np.zeros((20,30,3),np.uint8))
    assert len(calls) == 1


def test_real_scan_decodes_once_and_does_not_change_settings(monkeypatch):
    import lyricsync.vision as vision
    counts = dict(frames=0)
    class Video:
        def __init__(self,path):self.info=dict(width=50,height=40,duration=1.,fps=3.)
        def frames(self,*args,**kwargs):
            counts['frames'] += 1
            for n in range(3):yield n/3,np.full((40,50,3),n,np.uint8)
    class Cache:
        def key(self,*args):return repr(args)
        def get(self,key):return None
        def put(self,key,value):pass
    monkeypatch.setattr(vision,'Video',Video)
    monkeypatch.setattr(vision,'file_hash',lambda *args:'digest')
    settings=dict(ocr_regions=regions(),roi=[0,0,1,1],ocr_multi_region=True)
    before=deepcopy(settings); provider=Provider()
    result,info=scan_regions('dummy',settings,Cache(),provider=provider)
    assert result == [] and counts['frames'] == 1
    assert len(provider.calls) == 6 and settings == before
    assert info['ocr_regions'] == regions()


@pytest.fixture(scope='module')
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_dialog_cancel_and_explicit_save(app):
    from PySide6.QtGui import QImage
    from desktop_qt.multi_roi_dialog import MultiRegionDialog
    image=QImage(50,40,QImage.Format.Format_RGB32)
    image.fill(0)
    project=SimpleNamespace(inputs={'video':'dummy'},video={'width':50,'height':40},settings={'roi':[1,2,30,35]})
    writes=[]
    bridge=SimpleNamespace(document=SimpleNamespace(id='doc',revision=0,project=project),
        media_controller=SimpleNamespace(_last_image=image),
        history=SimpleNamespace(push=lambda *a:writes.append('history')),_changed=lambda:writes.append('changed'))
    dialog=MultiRegionDialog(bridge)
    dialog.add_region();dialog.reject()
    assert project.settings == {'roi':[1,2,30,35]} and not writes
    dialog=MultiRegionDialog(bridge)
    dialog.mode.setChecked(True);dialog.add_region();dialog.commit()
    assert project.settings['roi'] == [1,2,30,35]
    assert project.settings['ocr_multi_region'] is True
    assert len(project.settings['ocr_regions']) == 2
    assert writes == ['history','changed']


def test_dialog_rejects_stale_document(app):
    from PySide6.QtGui import QImage
    from desktop_qt.multi_roi_dialog import MultiRegionDialog
    image=QImage(50,40,QImage.Format.Format_RGB32);image.fill(0)
    project=SimpleNamespace(inputs={'video':'dummy'},video={'width':50,'height':40},settings={})
    bridge=SimpleNamespace(document=SimpleNamespace(id='doc',revision=0,project=project),
        media_controller=SimpleNamespace(_last_image=image),history=SimpleNamespace(push=lambda *a:pytest.fail('stale write')),
        _changed=lambda:pytest.fail('stale write'))
    dialog=MultiRegionDialog(bridge);bridge.document.revision=1;dialog.commit()
    assert not project.settings


def test_bridge_empty_multi_result_preserves_main_and_surfaces_notice(app,tmp_path,monkeypatch):
    from desktop_qt.controller import Bridge
    bridge=Bridge(storage_dir=tmp_path/'storage')
    try:
        bridge.document.project.inputs['video']='dummy'
        bridge.document.project.video.update(width=50,height=40)
        bridge.document.project.settings.update(ocr_multi_region=True,ocr_regions=regions(),roi=None)
        jobs=[];installed=[]
        monkeypatch.setattr(bridge,'_start_candidate_job',lambda *args:jobs.append(args))
        monkeypatch.setattr(bridge,'_candidate_install',lambda *args,**kwargs:installed.append(kwargs))
        monkeypatch.setattr(bridge,'_message',lambda *args:None)
        bridge._run_ocr()
        jobs[0][3]((None,False,{}),None)
        assert not installed and not bridge.document.main_timeline
        assert bridge._candidate_task['resultStatus']=='empty' and bridge._candidate_task['label']
        bridge._roi(dict(x=0.,y=0.,width=.5,height=.5))
        assert bridge.document.project.settings['ocr_multi_region'] is False
    finally:
        bridge.shutdown();bridge.wait_for_shutdown(app)


def test_scan_track_keeps_global_pixels_and_region_provenance(monkeypatch):
    import lyricsync.vision as vision
    class Video:
        def __init__(self,path):self.info=dict(width=50,height=40,duration=1.,fps=3.)
        def frames(self,*args,**kwargs):
            for n in range(3):yield n/3,np.full((40,50,3),n,np.uint8)
    class Cache:
        def key(self,*args):return repr(args)
        def get(self,key):return None
        def put(self,key,value):pass
    class TextProvider:
        signature={'engine':'test'}
        def recognize(self,image):return [Detection('hello',[2,2,8,10],.95)]
    monkeypatch.setattr(vision,'Video',Video)
    monkeypatch.setattr(vision,'file_hash',lambda *args:'digest')
    monkeypatch.setattr(vision,'refine_track',lambda *args:(0.,1.,[[0.,0.],[1.,1.]],[]))
    settings=dict(ocr_regions=[dict(id='test',box=[10,10,30,30],enabled=True)])
    visuals,info=scan_regions('dummy',settings,Cache(),provider=TextProvider())
    assert len(visuals) == 1
    assert visuals[0].box == [12,12,18,20]
    assert visuals[0].evidence['roi_ids'] == ['test']
    assert visuals[0].evidence['roi_configuration'] == settings['ocr_regions']


def test_rescan_uses_saved_source_region_not_new_live_settings():
    from lyricsync.multi_roi import rescan_settings
    from lyricsync.model import Visual
    original=regions()
    visual=Visual('v','text',0,1,[12,12,18,20],.9,3,evidence={'roi_ids':['left'],'roi_configuration':original})
    project=SimpleNamespace(visuals=[visual])
    settings=dict(ocr_multi_region=False,ocr_regions=[dict(id='new',box=[0,0,1,1],enabled=True)])
    result=rescan_settings(project,SimpleNamespace(visual_ids=['v']),deepcopy(settings))
    assert result['ocr_multi_region'] is True
    assert result['ocr_regions'] == [original[0]]
    assert settings['ocr_multi_region'] is False


def test_source_provenance_survives_visual_serialization():
    from dataclasses import asdict
    from lyricsync.model import Visual
    v=Visual('v','text',0,1,[12,12,18,20],.9,3,evidence={'roi_ids':['left'],'roi_configuration':regions()})
    assert Visual(**asdict(v)).evidence == v.evidence
