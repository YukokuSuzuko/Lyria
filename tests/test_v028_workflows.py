from copy import deepcopy
import threading
import pytest
from PySide6.QtWidgets import QApplication
from lyricsync.model import Mapping, Visual
from test_qt_entry_initialization_dev5 import bridge, ready, settled, wait


def setup(b, tmp_path):
    ready(b)
    path = tmp_path/'local.mp4'
    path.write_bytes(b'test worker uses detached observations')
    b.document.project.inputs['video'] = str(path)
    b.document.project.settings['roi'] = [10,10,180,70]
    b._entry_accept_import()


def fill(project, suffix='a'):
    visual = Visual('v'+suffix, '歌', .4, 1.6, [20,20,120,40], .94, 5)
    project.visuals = [visual]
    project.mappings = [Mapping('m'+suffix, [], visual_ids=[visual.id], start=visual.start,
                                end=visual.end, text_override=visual.text, text_role='source_ocr')]


@pytest.mark.parametrize('end',['empty','failed','cancelled','partial','partial_visuals'])
def test_ocr_unsuccessful_completion(bridge,tmp_path,monkeypatch,end):
    b = bridge
    setup(b,tmp_path)
    entered = threading.Event()
    def run(project,*args,cancel,**kwargs):
        if end=='failed': raise RuntimeError('controlled failure')
        if end.startswith('partial'):
            fill(project)
            if end=='partial_visuals': project.mappings=[]
        if end!='empty':
            entered.set()
            from time import monotonic, sleep
            deadline = monotonic()+3
            while not cancel() and monotonic()<deadline: sleep(.005)
            assert cancel()
            raise InterruptedError()
    monkeypatch.setattr('lyricsync.pipeline.run',run)
    b._run_ocr()
    if end in ('partial','partial_visuals','cancelled'):
        wait(entered.is_set); b.cancelCandidateJob()
    settled(b)
    task=b.state['candidateTask']
    assert b.stage=='import' and not b.document.main_timeline
    assert task['label'] and task['resultStatus']==('retained' if end.startswith('partial') else end)
    if end.startswith('partial'):
        assert len(b.document.source_candidates)==1 and task['resultId']==b.document.source_candidates[0]['id']
        opened=[]
        monkeypatch.setattr(b,'_open_candidate_result',lambda rec:opened.append(rec['id']))
        b.reopenOcrResult()
        assert opened==[task['resultId']]
    else:
        assert not b.document.source_candidates and not b.state['candidateComparisonAvailable']
        assert not task.get('resultId')


def test_success_then_protected_then_empty_keeps_exact_history(bridge,tmp_path,monkeypatch):
    b=bridge
    setup(b,tmp_path)
    monkeypatch.setattr('lyricsync.pipeline.run',lambda project,*a,**kw:fill(project))
    b._run_ocr();settled(b)
    assert b.stage=='review' and b.state['candidateTask']['resultStatus']=='applied'
    assert b.state['candidateTask']['scanCounts']['applied']==1
    assert b.command('row.lock',{'id':b.document.main_timeline[0].id,'value':True})
    before=deepcopy(b.document.project.to_dict())
    monkeypatch.setattr('lyricsync.pipeline.run',lambda project,*a,**kw:fill(project,'b'))
    b._run_ocr();settled(b)
    task=b.state['candidateTask']
    assert task['resultStatus']=='retained' and b._ocr_result_window.record['id']==task['resultId']
    b._ocr_result_window.reject(); QApplication.processEvents()
    opened=[]
    monkeypatch.setattr(b,'_open_candidate_result',lambda rec:opened.append(rec['id']))
    b.reopenOcrResult()
    assert opened==[task['resultId']]
    records=deepcopy(b.document.source_candidates)
    monkeypatch.setattr('lyricsync.pipeline.run',lambda *a,**k:None)
    b._run_ocr();settled(b)
    assert b.stage=='import' and b.state['candidateTask']['resultStatus']=='empty'
    assert not b.state['candidateTask'].get('resultId') and b.state['candidateComparisonAvailable']
    b.reopenOcrResult()
    assert opened==[task['resultId']]
    assert b.document.source_candidates==records and b.document.project.to_dict()==before
    assert b.switchCandidateSource('ocr') is None
    assert b._candidate_record()['id']==task['resultId']


@pytest.mark.parametrize('translation_first',[False,True])
def test_audio_optional_translation_never_becomes_original_axis(bridge,tmp_path,translation_first):
    b=bridge
    assert b.command('project.newEntry',{'mode':'audio'})
    original=tmp_path/'source.txt';original.write_text('歌う\n光る','utf8')
    translation=tmp_path/'translation.srt'
    translation.write_text('1\n00:00:07,000 --> 00:00:08,000\n歌唱\n','utf8')
    def load(role):
        assert b.command('input.import',{'role':role,'poolOnly':role=='translation','path':str(translation if role=='translation' else original)})
        settled(b)
    if translation_first:
        load('translation')
        assert not b.document.main_timeline and not b.document.project.lyrics
    load('source')
    rows=deepcopy(b.document.main_timeline)
    assert [(r.text_role,r.text_override) for r in rows]==[('source_import','歌う'),('source_import','光る')]
    if not translation_first: load('translation')
    assert b.document.main_timeline==rows
    assert [t.text for t in b.document.project.translations]==['歌唱']
    assert [l.text for l in b.document.project.lyrics]==['歌う','光る']
