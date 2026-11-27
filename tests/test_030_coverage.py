import pytest
from test_qt_audio_source_switch import source_ui,add_stem,wait

@pytest.mark.parametrize('offset,duration,expected',[(0,2.047578,2.),(0,.08,.08),(.5,.3,.3),(2.1,.2,None)])
def test_real_pcm_duration_and_intersection(source_ui,offset,duration,expected):
    bridge,root,media=source_ui
    add_stem(bridge,root,media,offset,duration)
    original=bridge.document.project.to_dict()
    bridge.selectAudioAnalysisSource('vocal')
    wait(lambda:bridge.audioAnalysis['status']!='loading')
    if expected is None:
        assert bridge.audioAnalysis['status']=='error' and not bridge.audioAnalysis.get('sourcePath')
    else:
        assert bridge.audioAnalysis['status']=='ready'
        assert bridge.audioAnalysis['sourceOffsetSeconds']==offset
        assert bridge.audioAnalysis['durationSeconds']==pytest.approx(expected,abs=.0001)
    assert bridge.document.project.to_dict()==original
