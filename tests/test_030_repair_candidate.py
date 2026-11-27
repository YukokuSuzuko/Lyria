from types import SimpleNamespace
import numpy as np
from lyricsync.model import Project, Lyric, Translation, Visual, Mapping
from lyricsync.ocr_source_pairing import edit_source_pair, paired_source_ids, source_links
from lyricsync.spectrogram import RATE, stft_columns, waveform_rgb


def test_ordered_group_retains_each_source():
    p = Project(lyrics=[Lyric('l3','abc',None,None), Lyric('l4','def',None,None)],
                translations=[Translation('t','翻译')], mappings=[Mapping('m',['t'])])
    p.settings['ocr_imported_source_links'] = {'t':'l3'}
    p = edit_source_pair(p, 'pair', translation_id='t', source_ids=['l4'])
    assert paired_source_ids(p.mappings[0],source_links(p)) == ['l3','l4']
    p = edit_source_pair(p,'edit',source_id='l4',text='DEF')
    assert p.lyrics[1].text == 'def'
    p = edit_source_pair(p,'unpair',translation_id='t',source_id='l4')
    assert paired_source_ids(p.mappings[0],source_links(p)) == ['l3']


def test_uncovered_audio_is_blank():
    src = SimpleNamespace(samples=np.ones(RATE,dtype=np.float32),duration=1.)
    times = np.array([-.001, .5, 1.001])
    assert not stft_columns(src,times)[[0,2]].any()
    out = waveform_rgb(src,np.array([-.03,-.02,-.01]),32)
    assert not (out == [201,166,250]).all(axis=2).any()
