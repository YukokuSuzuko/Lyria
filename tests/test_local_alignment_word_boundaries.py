"""Model-free regression at the production worker preparation seam."""
import pytest
from lyricsync.local_alignment_worker import prepare_row_inputs


class Processor:
    def __init__(self):
        self.calls = []

    def split_words_for_alignment(self, text, language):
        self.calls.append((text, language))
        # Reproduced actual nagisa behavior: newline is not a word boundary.
        return [] if not text.strip(' !') else [text.replace('\n', '')]

    def apply_chat_template(self, conversation, **kwargs):
        self.conversation = conversation
        self.kwargs = kwargs
        return {'input_ids': 'model prompt'}


def test_production_preparation_never_merges_across_lyric_rows():
    processor = Processor()
    audio = object()
    inputs, word_lists, counts = prepare_row_inputs(
        processor, audio, [{'id': 'a', 'text': 'も'}, {'id': 'b', 'text': 'ほら'}], 'Japanese')
    assert word_lists == [['も', 'ほら']]
    assert counts == [1, 1]
    assert processor.calls == [('も', 'Japanese'), ('ほら', 'Japanese')]
    content = processor.conversation[0][0]['content']
    assert content[0]['audio'] is audio
    assert [c['text'] for c in content[1:]] == ['も', 'ほら']
    assert processor.kwargs['sampling_rate'] == 16000
    assert processor.kwargs['tokenize'] is True


def test_duplicate_lyrics_retain_two_target_occurrences_and_empty_row_count():
    processor = Processor()
    _, words, counts = prepare_row_inputs(processor, object(),
        [{'text': 'repeat'}, {'text': ' ! '}, {'text': 'repeat'}], 'English')
    assert words == [['repeat', 'repeat']]
    assert counts == [1, 0, 1]


def test_empty_words_stop_before_model_prompt():
    with pytest.raises(RuntimeError, match='没有可识别'):
        prepare_row_inputs(Processor(), object(), [{'text': ' ! '}], 'Japanese')


def test_invalid_word_split_does_not_enter_prompt():
    processor = Processor()
    processor.split_words_for_alignment = lambda *_: ['ok', None]
    with pytest.raises(RuntimeError, match='切词结果异常'):
        prepare_row_inputs(processor, object(), [{'text': 'ok'}], 'English')
