"""Deployment-only language choices; never changes recognition algorithms."""
from pathlib import Path
import os
import shutil


def language_options(engine, model, root):
    root=Path(root)
    if engine=='tesseract':
        candidates=[root/'tesseract'/'tesseract.exe',Path(os.environ.get('LYRICSYNC_TESSERACT','')),Path(r'C:\Program Files\Tesseract-OCR\tesseract.exe')]
        executable=next((p for p in candidates if p.is_file()),None)
        if executable is None:
            found=shutil.which('tesseract');executable=Path(found) if found else None
        if executable is None:return []
        data=executable.parent/'tessdata'
        if not data.is_dir():
            try:
                from lyricsync.tesseract_process import installed_tessdata
                data=installed_tessdata(str(executable))
            except (RuntimeError,OSError):return []
        supported={p.stem for p in data.glob('*.traineddata')}
        choices=[('日语与英语','jpn+eng'),('日语','jpn'),('英语','eng'),('简体中文','chi_sim'),('简体中文与英语','chi_sim+eng'),('繁体中文','chi_tra'),('日语竖排','jpn_vert')]
        return [(label,value) for label,value in choices if set(value.split('+'))<=supported]
    models=root/'models'
    if not models.is_dir():
        try:
            import rapidocr
            models=Path(rapidocr.__file__).parent/'models'
        except ImportError:return []
    if not (models/'ch_ppocr_mobile_v2.0_cls_infer.onnx').is_file():return []
    if model=='ppocrv6':
        if all((models/name).is_file() for name in ('PP-OCRv6_det_small.onnx','PP-OCRv6_rec_medium.onnx')):
            return [('日语 + 英语','jpn+eng'),('日语','jpn'),('简体中文','chi_sim'),('英语','eng')]
        return []
    if not (models/'ch_PP-OCRv4_det_infer.onnx').is_file():return []
    japanese=(models/'japan_PP-OCRv4_rec_infer.onnx').is_file()
    chinese=(models/'ch_PP-OCRv4_rec_infer.onnx').is_file()
    return ([('日语与英语','jpn+eng')] if japanese and chinese else [])+([('日语','jpn')] if japanese else [])+([('简体中文','chi_sim'),('英语','eng')] if chinese else [])
