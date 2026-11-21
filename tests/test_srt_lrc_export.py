from pathlib import Path
import pytest
from lyricsync.export import render_srt, render_lrc, export_project
from lyricsync.model import Project, Translation, Mapping


def project():
    p=Project()
    p.video={'duration': 20, 'width': 1920, 'height': 1080}
    p.translations=[Translation('a','第一句'),Translation('b','第二句')]
    p.mappings=[Mapping('a',['a'],start=1.005,end=2.123,confirmed=True),Mapping('b',['b'],start=3.25,end=4.5,confirmed=True)]
    return p


def test_srt_precise_and_multiline():
    text=render_srt([(1.005,2.123,'第一行\n第二行'),(3.25,4.5,'下一句')])
    assert '00:00:01,005 --> 00:00:02,123\n第一行\n第二行' in text
    assert '2\n00:00:03,250 --> 00:00:04,500\n下一句' in text
    with pytest.raises(ValueError):render_srt([(1,1.0001,'a')])


def test_lrc_centiseconds_and_no_false_end_times():
    assert render_lrc([(1.005,2,'第一行\n第二行'),(65.25,66,'歌词')]) == '[00:01.01]第一行 第二行\n[01:05.25]歌词\n'


def test_export_formats_and_protect_existing(tmp_path):
    p=project()
    for ext,expected in [('srt','00:00:01,005 --> 00:00:02,123'),('lrc','[00:01.01]第一句')]:
        target=tmp_path/('output.'+ext)
        export_project(p,target)
        assert expected in target.read_text(encoding='utf-8-sig')
        with pytest.raises(FileExistsError):export_project(p,target)
        assert expected in target.read_text(encoding='utf-8-sig')


def test_reject_unsupported_and_unconfirmed(tmp_path):
    p=project()
    with pytest.raises(ValueError):export_project(p,tmp_path/'output.txt')
    p.mappings[0].confirmed=False
    with pytest.raises(ValueError):export_project(p,tmp_path/'output.srt')
    assert not (tmp_path/'output.srt').exists()


def test_srt_and_lrc_choose_boundary_not_after_source_frame(tmp_path):
    from lyricsync.frames import FrameIndex
    p=Project();p.video={'duration':.2};p.translations=[Translation('a','边界')]
    p.mappings=[Mapping('a',['a'],start=.0338,end=.0681,confirmed=True)]
    idx=FrameIndex([0.,.0338,.0681,.1023,.1362,.17],.2)
    srt=tmp_path/'timing.srt';lrc=tmp_path/'timing.lrc'
    export_project(p,srt,frame_index=idx);export_project(p,lrc,frame_index=idx)
    assert '00:00:00,033 --> 00:00:00,068' in srt.read_text(encoding='utf-8-sig')
    assert '[00:00.03]' in lrc.read_text(encoding='utf-8-sig')


def test_lrc_unrepresentable_high_fps_does_not_silently_shift(tmp_path):
    from lyricsync.frames import FrameIndex
    p=Project();p.video={'duration':.1};p.translations=[Translation('a','高帧率')]
    p.mappings=[Mapping('a',['a'],start=.008,end=.016,confirmed=True)]
    idx=FrameIndex([0.,.008,.016,.024,.032,.040,.048,.056,.064,.072,.080,.088,.096],.1)
    target=tmp_path/'unrepresentable.lrc'
    with pytest.raises(ValueError,match='时间精度'):export_project(p,target,frame_index=idx)
    assert not target.exists()


def test_vertical_ass_keeps_graphemes_and_text_export_horizontal(tmp_path):
    from lyricsync.export import render_ass,layout_entries,placement_options
    p=project();p.settings.update(subtitle_vertical=True,font_size=30,subtitle_position='bottom')
    text='春👨‍👩‍👧‍👦風'
    layout=layout_entries([(1,2,text)],(1920,1080),30,**placement_options(p))
    assert layout[0]['text']=='春\n👨‍👩‍👧‍👦\n風'
    assert r'春\N👨‍👩‍👧‍👦\N風' in render_ass([(1,2,text)],(1920,1080),font_size=30,**placement_options(p))
    assert text in render_srt([(1,2,text)])
    assert text in render_lrc([(1,2,text)])


def test_vertical_overlap_preserves_user_position_and_times():
    from lyricsync.export import layout_entries
    rows=layout_entries([(1,3,'春風'),(2,4,'夏雨')],(1920,1080),30,vertical=True)
    assert rows[1]['x'] == rows[0]['x'] and rows[1]['y']==rows[0]['y']
    assert (rows[1]['start'],rows[1]['end'])==(2,4)
