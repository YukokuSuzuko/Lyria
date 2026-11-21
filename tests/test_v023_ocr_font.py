from pathlib import Path
import pytest
from lyricsync.ocr_font_setup import configure_ocr_font, FONT_SHA256

ROOT=Path(__file__).resolve().parents[1]

def test_explicit_font_is_not_overridden(tmp_path):
    env={'LYRIA_OCR_FONT':'chosen-missing.ttf','WINDIR':str(tmp_path)}
    assert configure_ocr_font(root=tmp_path,environ=env,platform='win32') is None
    assert env['LYRIA_OCR_FONT']=='chosen-missing.ttf'

def test_existing_system_font_keeps_old_path(tmp_path):
    fonts=tmp_path/'Fonts';fonts.mkdir();(fonts/'arial.ttf').write_bytes(b'placeholder')
    env={'WINDIR':str(tmp_path)}
    assert configure_ocr_font(root=tmp_path,environ=env,platform='win32') is None
    assert 'LYRIA_OCR_FONT' not in env

def test_missing_bundle_fails_closed(tmp_path):
    with pytest.raises(FileNotFoundError):configure_ocr_font(root=tmp_path,environ={'WINDIR':str(tmp_path)},platform='win32')

def test_corrupt_bundle_fails_closed(tmp_path):
    p=tmp_path/'resources/fonts/NotoSansCJKjp-Regular.otf';p.parent.mkdir(parents=True);p.write_bytes(b'bad')
    with pytest.raises(RuntimeError,match='size mismatch'):
        configure_ocr_font(root=tmp_path,environ={'WINDIR':str(tmp_path)},platform='win32')

def test_real_font_offline_missing_system_and_same_ocr(tmp_path,monkeypatch):
    from PIL import Image,ImageDraw,ImageFont
    import numpy as np
    from rapidocr.utils.download_file import DownloadFile
    from lyricsync.vision import RapidOCRProvider
    def forbidden(*a,**k):raise AssertionError('Unexpected remote font/model download')
    monkeypatch.setattr(DownloadFile,'run',forbidden)
    font=ROOT/'resources/fonts/NotoSansCJKjp-Regular.otf'
    if not font.is_file():pytest.skip('Offline font resource bundle not present in code-only checkout')
    # One fixed synthetic image, not rendered differently for the two OCR configurations.
    image=Image.new('RGB',(640,100),'white');ImageDraw.Draw(image).text((12,12),'日本語 中文 歌詞 ABC',font=ImageFont.truetype(str(font),42),fill='black')
    image=np.asarray(image)
    def outputs():
        return {profile:[(x.text,x.box,x.confidence) for x in RapidOCRProvider(language='jpn',model=profile,device='cpu').recognize(image)] for profile in ('ppocrv4','ppocrv6')}
    monkeypatch.delenv('LYRIA_OCR_FONT',raising=False)
    baseline=outputs()
    monkeypatch.setenv('WINDIR',str(tmp_path))
    assert configure_ocr_font(root=ROOT)==font
    result=outputs()
    assert result==baseline
    assert all(result.values())
