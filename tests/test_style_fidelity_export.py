from copy import deepcopy
import pytest
from lyricsync.export import layout_entries,render_ass,export_project
from test_v04 import fixture


@pytest.mark.parametrize('entries',[
 [(0,2,'单句长字幕'*100)],
 [(0,2,'第一句'),(1,3,'第二句'),(1.5,4,'第三句')],
 [(0,1,'第一句'),(1,2,'第二句'),(2,3,'第三句')],
])
def test_positions_fonts_times_stay_exact_even_outside_safe_area(entries):
    options=dict(position='custom',height_percent=5,horizontal_percent=65)
    rows=layout_entries(entries,(1920,1080),60,**options)
    assert all((r['x'],r['y'],r['font_size'])==(1248,54,60) for r in rows)
    assert [(r['start'],r['end'],r['text']) for r in rows]==entries
    body=render_ass(entries,(1920,1080),'Microsoft YaHei',60,**options)
    assert body.count(r'\pos(1248,54)')==len(entries)
    assert 'Microsoft YaHei,60,' in body


def test_single_cue_overflow_exports_and_never_overwrites_existing_output(tmp_path):
    p=fixture();p.settings.update(subtitle_position='custom',subtitle_height_percent=5,font_size=100)
    before=deepcopy(p.to_dict());path=tmp_path/'faithful.ass'
    export_project(p,path)
    body=path.read_bytes();assert body and p.to_dict()==before
    with pytest.raises(FileExistsError):export_project(p,path)
    assert path.read_bytes()==body
