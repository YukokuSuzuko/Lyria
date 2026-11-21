"""Offline visualization-font fallback; does not modify OCR inference."""
from pathlib import Path
import hashlib
import os
import sys

FONT_SHA256 = '68a3fc98800b2a27b371f2fb79991daf3633bd89309d4ffaa6946fd587f375b5'
FONT_BYTES = 16467736

def configure_ocr_font(*, root=None, environ=None, platform=None):
    env = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    # Explicit user configuration wins, even when invalid: do not silently repair it.
    if env.get('LYRIA_OCR_FONT'):
        return None
    system = ([Path(env.get('WINDIR', r'C:\Windows'))/'Fonts'/name
               for name in ('msyh.ttc', 'arial.ttf')] if platform == 'win32' else
              [Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'),
               Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')])
    if any(path.is_file() for path in system):
        return None
    base = Path(root) if root is not None else Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    font = base/'resources/fonts/NotoSansCJKjp-Regular.otf'
    if font.stat().st_size != FONT_BYTES:
        raise RuntimeError('Bundled OCR visualization font size mismatch')
    with font.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != FONT_SHA256:
        raise RuntimeError('Bundled OCR visualization font hash mismatch')
    env['LYRIA_OCR_FONT'] = str(font.resolve())
    return font
