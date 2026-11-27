"""Verified source publication must precede legacy full-song overview."""
import threading
import pytest
from test_qt_audio_source_switch import source_ui, add_stem, wait

@pytest.mark.parametrize('source',['original','vocal'])
def test_verified_source_precedes_overview_and_keeps_revision(source_ui,monkeypatch,source):
    import lyricsync.audio_analysis as algorithm
    b,root,path=source_ui
    expected=path
    if source=='vocal':_,expected=add_stem(b,root,path,offset=.5,duration=1.)
    original=algorithm.analyze_audio;entered=threading.Event();release=threading.Event()
    def slow(*args,**kwargs):
        entered.set();release.wait(8);return original(*args,**kwargs)
    monkeypatch.setattr(algorithm,'analyze_audio',slow)
    try:
        b.selectAudioAnalysisSource(source)
        wait(lambda:b.audioAnalysis.get('spectrogramSourceReady',False),timeout=2)
        state=b.audioAnalysis
        assert state['status']=='loading' and state['waveform']==[]
        assert state['sourcePath']==str(expected.resolve())
        assert state['sourceOffsetSeconds']==(.5 if source=='vocal' else 0.)
        revision=state['revision']
        assert not entered.is_set(), 'Full overview started before visible viewport priority signal'
        b.spectrumViewportReady(revision)
        wait(entered.is_set)
        release.set();wait(lambda:b.audioAnalysis['status']=='ready')
        assert b.audioAnalysis['revision']==revision
    finally:
        release.set();wait(lambda:not b.jobs.live_threads())


def test_stale_viewport_signal_cannot_start_new_source_overview(source_ui,monkeypatch):
    import lyricsync.audio_analysis as algorithm
    b,root,path=source_ui
    add_stem(b,root,path,offset=.5,duration=1.)
    calls=[];original=algorithm.analyze_audio
    def tracked(path,*args,**kwargs):
        calls.append(str(path));return original(path,*args,**kwargs)
    monkeypatch.setattr(algorithm,'analyze_audio',tracked)
    b.selectAudioAnalysisSource('vocal')
    wait(lambda:b.audioAnalysis.get('spectrogramSourceReady',False))
    stale=b.audioAnalysis['revision']
    b.selectAudioAnalysisSource('original')
    wait(lambda:b.audioAnalysis.get('spectrogramSourceReady',False))
    fresh=b.audioAnalysis['revision']
    assert fresh!=stale
    b.spectrumViewportReady(stale)
    assert calls==[]
    b.spectrumViewportReady(fresh)
    wait(lambda:b.audioAnalysis['status']=='ready')
    assert calls==[str(path)]
    assert b.audioAnalysis['source']=='original'


@pytest.mark.parametrize('role',['source','translation'])
def test_text_detach_does_not_orphan_running_overview(source_ui,monkeypatch,role):
    import lyricsync.audio_analysis as algorithm
    from desktop_qt import dialogs
    b,root,path=source_ui
    text=root/(role+'.txt');text.write_text('synthetic text',encoding='utf-8')
    b.document.project.inputs[role]=str(text);b._changed()
    monkeypatch.setattr(dialogs,'choose_asset_clear',lambda *a,**k:'detach')
    entered=threading.Event();release=threading.Event();original=algorithm.analyze_audio
    def slow(*args,**kwargs):
        entered.set();release.wait(8);return original(*args,**kwargs)
    monkeypatch.setattr(algorithm,'analyze_audio',slow)
    try:
        b.selectAudioAnalysisSource('original')
        wait(lambda:b.audioAnalysis.get('spectrogramSourceReady',False))
        revision=b.audioAnalysis['revision'];b.spectrumViewportReady(revision);wait(entered.is_set)
        assert b.command('input.clear',{'role':role})
        release.set();wait(lambda:b.audioAnalysis['status']=='ready')
        assert b._audio_job is None
        assert b.audioAnalysis['sourcePath']==str(path)
        assert b.audioAnalysis['revision']==revision
    finally:
        release.set();wait(lambda:not b.jobs.live_threads())


def test_display_source_omits_bulk_arrays_without_copying_them(source_ui):
    b,root,path=source_ui
    class DoNotCopy(list):
        def __deepcopy__(self,memo):raise AssertionError('Bulk array copied to display property')
    old=b._audio_analysis
    try:
        for status in ('loading','ready','error'):
            b._audio_analysis=dict(status=status,error='test error' if status=='error' else '',revision=31,
                sourcePath=str(path),durationSeconds=2.,sourceOffsetSeconds=.5,
                spectrogramSourceReady=status!='error',waveform=DoNotCopy([1]),spectrum=DoNotCopy([2]))
            state=b.audioDisplaySource
            assert state['status']==status and state['revision']==31
            assert state['sourceOffsetSeconds']==.5 and state['sourcePath']==str(path)
            assert state['error']==b._audio_analysis['error']
            assert 'waveform' not in state and 'spectrum' not in state
    finally:b._audio_analysis=old
    full=b.audioAnalysis
    assert 'waveform' in full and 'spectrum' in full
    assert full['waveform']==old['waveform'] and full['spectrum']==old['spectrum']


@pytest.mark.parametrize('release_by',['tile','hide'])
def test_visible_slow_viewport_owns_priority_beyond_timeout(source_ui,monkeypatch,release_by):
    import time
    import lyricsync.audio_analysis as algorithm
    b,root,path=source_ui;calls=[];original=algorithm.analyze_audio
    def tracked(*a,**kw):calls.append(True);return original(*a,**kw)
    monkeypatch.setattr(algorithm,'analyze_audio',tracked)
    b.selectAudioAnalysisSource('original')
    wait(lambda:b.audioAnalysis.get('spectrogramSourceReady',False))
    revision=b.audioAnalysis['revision'];b.spectrumViewportActivity(revision,True)
    start=time.monotonic();wait(lambda:time.monotonic()-start>1.75,timeout=3)
    assert not calls and b.audioAnalysis['status']=='loading'
    if release_by=='tile':b.spectrumViewportReady(revision)
    else:b.spectrumViewportActivity(revision,False)
    wait(lambda:b.audioAnalysis['status']=='ready')
    assert len(calls)==1
