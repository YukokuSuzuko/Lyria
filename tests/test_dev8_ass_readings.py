from lyricsync.subtitles import ass_plain, parse_ass, recover_lyrics
from lyricsync.model import Project
from lyricsync.japanese_readings import prepare_reading_rows


def test_ass_ruby_separate_from_display_and_roundtrip(tmp_path):
    p=tmp_path/'ruby.ass'
    p.write_text(r"[Events]"+'\n'+r"Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text"+'\n'+r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\k20}漢|か{\k20}#|ん{\k30}字|じ"+'\n'+r"Dialogue: 0,0:00:05.00,0:00:07.00,Default,,0,0,0,,{\k20}漢|か{\k20}#|ん{\k30}字|じ",encoding='utf-8')
    rows=recover_lyrics(parse_ass(p))
    assert [r.text for r in rows]==['漢字','漢字']
    assert [r.reading for r in rows]==['かんじ','かんじ']
    assert rows[0].id!=rows[1].id
    assert rows[0].reading_segments[0]==dict(text='漢',reading='かん',start=0,end=1)
    result=Project.from_dict(Project(lyrics=rows).to_dict())
    assert result.lyrics[0].reading=='かんじ'
    assert ass_plain(r'{\k20}漢|か{\k20}#|ん{\k30}字|じ',karaoke_readings=True)[0]=='漢字'


def test_existing_kana_reading_is_converted_not_original_kanji():
    seen=[]
    def converter(text):seen.append(text);return [dict(orig=text,hepburn='kanji')]
    row=dict(id='r',text='漢字',reading='かんじ')
    result=prepare_reading_rows([row],converter)[0]
    assert seen==['かんじ'] and row['reading']=='かんじ'
    assert result['reading']=='kanji' and result['source_reading']=='かんじ'
    assert result['text']=='漢字'
