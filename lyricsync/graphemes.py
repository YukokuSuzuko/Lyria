"""Unicode grapheme-safe text gaps with explicit Qt/JS UTF-16 conversion.

Python and stored edits use code-point offsets. Qt/JavaScript cursor positions
use UTF-16 code units; no caller may substitute one for the other.
"""
from bisect import bisect_left


def _text(value):
    if not isinstance(value, str):
        raise ValueError('文本格式无效')
    # Isolated surrogate values are not valid Unicode scalar text.
    if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError('文本包含损坏的字符编码')
    return value


def character_gaps(text):
    """Full extended grapheme clusters (regex's Unicode UAX #29 support)."""
    import regex
    text = _text(text)
    return [0] + [match.end() for match in regex.finditer(r"\X", text)]


def validate_character_gap(text, offset):
    if type(offset) is not int or offset not in character_gaps(text):
        raise ValueError('请在完整字符之间切分，不能拆开表情或组合字符')
    return offset


def nearest_character_gap(text, offset):
    if type(offset) is not int:
        raise ValueError('字符位置无效')
    gaps = character_gaps(text)
    pos = bisect_left(gaps, offset)
    if pos == 0: return gaps[0]
    if pos == len(gaps): return gaps[-1]
    return min(gaps[pos-1:pos+1], key=lambda gap: (abs(gap-offset), -gap))


def codepoint_to_utf16(text, offset):
    text = _text(text)
    if type(offset) is not int or not 0 <= offset <= len(text):
        raise ValueError('字符位置超出文本长度')
    return len(text[:offset].encode("utf-16-le")) // 2


def utf16_to_codepoint(text, offset, *, snap=False):
    """Strict by default; snap=True is only for visual pointer hit-testing.

    A hit inside a surrogate pair snaps right on ties. Logical edits must validate
    the resulting grapheme boundary separately (or pass snap=True).
    """
    text = _text(text)
    if type(offset) is not int or offset < 0:
        raise ValueError('光标位置无效')
    units = [0]
    for char in text:
        units.append(units[-1] + (2 if ord(char) > 0xFFFF else 1))
    if offset > units[-1]:
        raise ValueError('光标位置超出文本范围')
    pos = bisect_left(units, offset)
    if units[pos] != offset:
        if not snap:
            raise ValueError('请在完整字符之间切分，不能拆开表情或特殊字符')
        pos = min((pos-1, pos), key=lambda i: (abs(units[i]-offset), -i))
    return nearest_character_gap(text, pos) if snap else pos


def safe_utf16_gap(text, offset):
    """Return both representations for QML caret geometry + backend split."""
    point = utf16_to_codepoint(text, offset, snap=True)
    return {"codepoint": point, "utf16": codepoint_to_utf16(text, point)}
