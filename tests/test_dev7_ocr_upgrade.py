"""Offline adapter/preprocessing contracts; mocked results are not quality claims."""
from unittest.mock import patch
import numpy as np
import pytest
from lyricsync import vision
from lyricsync.model import Detection
from lyricsync.ocr_preprocess import subtitle_image, needs_raw_recovery, recover_from_raw, text_key
from lyricsync.ocr_models import verified_v6_models, V6_MODELS


def detection(text='短い句', confidence=.8, box=None):
    return Detection(text, box or [2, 2, 20, 10], confidence)


def test_min_channel_is_non_destructive_not_binary():
    image = np.arange(120, dtype=np.uint8).reshape(4, 10, 3)
    original = image.copy()
    processed = subtitle_image(image)
    assert np.array_equal(image, original)
    assert processed.shape == image.shape and processed.dtype == np.uint8
    assert not np.shares_memory(image, processed)
    assert np.array_equal(processed[:, :, 0], image.min(axis=2))
    assert np.array_equal(processed[:, :, 0], processed[:, :, 2])
    assert len(np.unique(processed)) > 2


@pytest.mark.parametrize('image', [None, np.zeros((0, 2, 3), np.uint8), np.zeros((2, 2), np.uint8), np.zeros((2, 2, 3), np.float32)])
def test_bad_preprocess_input_is_rejected(image):
    with pytest.raises(ValueError):
        subtitle_image(image)


def test_raw_recovery_does_not_invent_or_inflate_unconfirmed_text():
    raw = detection('あ！', .99)
    result = recover_from_raw([], [raw])
    assert result[0].text == raw.text and result[0].confidence == .7
    assert raw.confidence == .99
    assert result[0].box == raw.box and result[0].box is not raw.box
    assert recover_from_raw([], []) == []


def test_disagreement_preserved_as_uncertain_and_inputs_not_mutated():
    enhanced = detection('その手祭いで', .8)
    raw = detection('その手繋いで', .98)
    result = recover_from_raw([enhanced], [raw])
    assert result[0].text == raw.text and result[0].confidence <= .7
    assert enhanced.text == 'その手祭いで' and enhanced.confidence == .8
    assert raw.confidence == .98


def test_identical_text_can_be_corroborated():
    result = recover_from_raw([detection('短い句', .8)], [detection('短い句', .97)])
    assert result[0].confidence == .97
    assert needs_raw_recovery([]) and needs_raw_recovery([detection(confidence=.8)])
    assert not needs_raw_recovery([detection(confidence=.95)])


def test_punctuation_and_small_kana_are_not_erased_by_consensus():
    assert text_key('Ａ B') == 'AB'
    assert text_key('ゃ') != text_key('や')
    assert text_key('はい！') != text_key('はい')


def test_provider_raw_mode_bypasses_preprocessing():
    provider = vision.RapidOCRProvider.__new__(vision.RapidOCRProvider)
    provider.preprocess = 'raw'
    image = np.zeros((10, 20, 3), np.uint8)
    with patch.object(provider, '_recognize_input', return_value=[detection()]) as call, patch('lyricsync.ocr_preprocess.subtitle_image', side_effect=AssertionError('raw means raw')):
        provider.recognize(image)
    assert call.call_count == 1 and call.call_args.args[0] is image


def test_provider_recovery_is_bounded_to_two_calls():
    provider = vision.RapidOCRProvider.__new__(vision.RapidOCRProvider)
    provider.preprocess = 'subtitle'
    image = np.zeros((10, 20, 3), np.uint8)
    with patch.object(provider, '_recognize_input', side_effect=[[], [detection()]]) as call:
        result = provider.recognize(image)
    assert call.call_count == 2 and result[0].confidence <= .7


def test_offline_model_missing_and_corrupt_fail_closed(tmp_path):
    with pytest.raises(RuntimeError, match='不会自动下载'):
        verified_v6_models(tmp_path)
    for name in V6_MODELS:
        (tmp_path/name).write_bytes(b'not a model')
    with pytest.raises(RuntimeError, match='校验失败'):
        verified_v6_models(tmp_path)


def test_offline_model_hash_contract(tmp_path):
    for name in V6_MODELS:
        (tmp_path/name).write_bytes(b'fixture')
    with patch('lyricsync.ocr_models.file_hash', side_effect=lambda path: V6_MODELS[path.name]):
        assert verified_v6_models(tmp_path) == V6_MODELS


def test_settings_default_and_explicit_v4_raw_are_forwarded():
    with patch.object(vision, 'RapidOCRProvider') as provider:
        vision.make_ocr({})
        assert provider.call_args.kwargs['model'] == 'ppocrv6'
        assert provider.call_args.kwargs['preprocess'] == 'subtitle'
        vision.make_ocr({'ocr_model':'ppocrv4', 'ocr_preprocess':'raw'})
        assert provider.call_args.kwargs['model'] == 'ppocrv4'
        assert provider.call_args.kwargs['preprocess'] == 'raw'
    with patch.object(vision, 'TesseractOCR') as provider:
        vision.make_ocr({'engine':'tesseract'})
        assert provider.called


@pytest.mark.parametrize('options', [{'model':'unknown'}, {'preprocess':'unknown'}])
def test_invalid_explicit_options_are_not_silently_defaulted(options):
    with pytest.raises(ValueError, match='未知'):
        vision.RapidOCRProvider(**options)


@pytest.mark.parametrize('missing', ['ch_PP-OCRv4_det_infer.onnx', 'ch_ppocr_mobile_v2.0_cls_infer.onnx'])
def test_missing_auxiliary_model_does_not_construct_download_capable_engine(tmp_path, missing):
    root = tmp_path/'models'
    root.mkdir()
    for name in ['ch_PP-OCRv4_det_infer.onnx', 'ch_ppocr_mobile_v2.0_cls_infer.onnx']:
        if name != missing:
            (root/name).write_bytes(b'fixture')
    with patch.object(vision, 'resources', return_value=tmp_path), patch('rapidocr.RapidOCR') as engine:
        with pytest.raises(RuntimeError, match='不会自动下载'):
            vision.RapidOCRProvider(model='ppocrv4', preprocess='raw')
    engine.assert_not_called()
