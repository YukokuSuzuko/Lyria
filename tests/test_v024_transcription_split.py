import regex
import pytest
from lyricsync.local_transcription import split_singing_text
from lyricsync.dev9_long_audio import merge_transcript_chunks

@pytest.mark.parametrize('text',['春天来了我们一起歌唱','桜が咲いて空を見上げる'])
def test_no_space_pause_and_trimmed_chunk(text):
    chunk={'text':'  '+text+' \n','start':0,'end':12}
    merged,_,_=merge_transcript_chunks([chunk])
    lines=split_singing_text(merged,chunks=[chunk],pauses=[(5.5,6.5)],max_chars=100)
    assert len(lines)==2
    assert ''.join(lines)==text

def test_long_intro_excluded_from_draft_position():
    text='一二三四五六七八九十'
    chunk={'text':text,'start':0,'end':112}
    # Two equally long singing spans; the 90-second intro is not text.
    lines=split_singing_text(text,chunks=[chunk],pauses=[(0,90),(100,102)],max_chars=100)
    assert lines==['一二三四五','六七八九十']

def test_validated_words_take_precedence_over_linear_guess():
    text='一二三四五六七八九十'
    chunk={'text':text,'start':0,'end':20,'words':[
        {'text':'一二','start':0,'end':8},
        {'text':'三四五六七八九十','start':10,'end':20}]}
    assert split_singing_text(text,chunks=[chunk],pauses=[(8,10)],max_chars=100)==['一二','三四五六七八九十']

def test_mismatched_words_are_not_trusted():
    text='一二三四五六七八九十'
    chunk={'text':text,'start':0,'end':20,'words':[{'text':'错字','start':0,'end':8}]}
    lines=split_singing_text(text,chunks=[chunk],pauses=[(9,11)],max_chars=100)
    assert lines==['一二三四五','六七八九十']

@pytest.mark.parametrize('atom',['か\u3099','👨‍👩‍👧‍👦','🇯🇵','a\u0301','👍🏽'])
def test_graphemes_never_split(atom):
    text=atom*9
    lines=split_singing_text(text,max_chars=4)
    assert lines==[atom*4,atom*4,atom]
    assert ''.join(lines)==text

def test_cjk_comma_preferred_for_length():
    assert split_singing_text('春天来了，大家一起唱歌',max_chars=8)==['春天来了，','大家一起唱歌']

def test_invalid_times_and_overlapping_pauses_are_safe():
    text='一二三四五六七八九十'
    chunk={'text':text,'start':0,'end':20}
    a=split_singing_text(text,chunks=[chunk],pauses=[(0,5),(2,5),(9,11),(float('nan'),2)],max_chars=100)
    b=split_singing_text(text,chunks=[chunk],pauses=[(0,5),(9,11)],max_chars=100)
    assert a==b and ''.join(a)==text

def test_latin_without_timing_stays_on_whitespace():
    text='alpha beta gamma delta'
    result=split_singing_text(text,chunks=[{'text':text,'start':0,'end':10}],pauses=[(4,4.5)],max_chars=100)
    assert len(result)==2
    assert set(' '.join(result).split())==set(text.split())
