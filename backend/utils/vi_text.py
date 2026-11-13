"""Vietnamese text normalization: spell out what a TTS engine would misread.

Digits matter most for OmniVoice, which fixes the audio length from the written
text *before* synthesis: "2026" is budgeted as four digits while the spoken
form is seven syllables, so the words get squeezed or dropped.

Only the text sent to the engine changes; history and subtitles keep the
user's original wording.
"""

from __future__ import annotations

import re
import unicodedata

_DIGITS = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]
_SCALES = ["", "nghìn", "triệu", "tỷ"]


def _read_triple(n: int, full: bool) -> str:
    """Read 0-999. *full* spells out "không trăm" for non-leading groups."""
    hundreds, rest = divmod(n, 100)
    tens, units = divmod(rest, 10)
    parts: list[str] = []
    if hundreds or full:
        parts += [_DIGITS[hundreds], "trăm"]
    if tens == 0:
        if units:
            if hundreds or full:
                parts.append("linh")
            parts.append(_DIGITS[units])
    elif tens == 1:
        parts.append("mười")
        if units == 5:
            parts.append("lăm")
        elif units:
            parts.append(_DIGITS[units])
    else:
        parts += [_DIGITS[tens], "mươi"]
        if units == 1:
            parts.append("mốt")
        elif units == 4:
            parts.append("tư")
        elif units == 5:
            parts.append("lăm")
        elif units:
            parts.append(_DIGITS[units])
    return " ".join(parts)


def read_integer(n: int) -> str:
    """Spell out a non-negative integer in Vietnamese."""
    if n == 0:
        return "không"
    if n >= 10**12:
        # Above "nghìn tỷ", digit-by-digit is both clearer and how people read it.
        return read_digits(str(n))

    groups: list[int] = []
    while n:
        n, g = divmod(n, 1000)
        groups.append(g)

    words: list[str] = []
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if g == 0:
            continue
        is_leading = i == len(groups) - 1
        words.append(_read_triple(g, full=not is_leading and g < 100))
        if _SCALES[i]:
            words.append(_SCALES[i])
    return " ".join(words)


def read_digits(s: str) -> str:
    return " ".join(_DIGITS[int(c)] for c in s if c.isdigit())


def _read_decimal(int_part: str, frac: str) -> str:
    whole = read_integer(int(int_part))
    if len(frac) <= 2 and not frac.startswith("0"):
        return f"{whole} phẩy {read_integer(int(frac))}"
    return f"{whole} phẩy {read_digits(frac)}"


_MONTHS = {1: "một", 4: "tư"}
_UNITS = {
    "km/h": "ki lô mét trên giờ",
    "km": "ki lô mét",
    "cm": "xăng ti mét",
    "mm": "mi li mét",
    "kg": "ki lô gam",
    "mg": "mi li gam",
    "ml": "mi li lít",
    "ha": "héc ta",
    "m2": "mét vuông",
    "m²": "mét vuông",
    "m3": "mét khối",
    "m³": "mét khối",
    "°C": "độ xê",
    "kWh": "ki lô oát giờ",
    "GB": "ghi ga bai",
    "MB": "mê ga bai",
    "tr": "triệu",
}
_CURRENCY = {"đ": "đồng", "₫": "đồng", "VND": "đồng", "VNĐ": "đồng", "USD": "đô la", "$": "đô la"}

_D = r"(?<![\w.,])"  # not glued to a preceding word or number
_NUMBER = r"\d{1,3}(?:\.\d{3})+|\d+"

_DATE_RE = re.compile(_D + r"(?:[Nn]gày\s+)?(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?!\d)")
_TIME_RE = re.compile(_D + r"(\d{1,2}):(\d{2})(?::\d{2})?(?![\d:])")
_TIME_H_RE = re.compile(_D + r"(\d{1,2})h(\d{2})?(?![\w])")
_PHONE_RE = re.compile(_D + r"(\+?84|0)\d{8,10}(?!\d)")
_RANGE_RE = re.compile(_D + rf"({_NUMBER})\s*[-–]\s*({_NUMBER})(?![\d])")
_DECIMAL_RE = re.compile(_D + r"(\d+),(\d+)(?!\d)")
_DOLLAR_RE = re.compile(_D + rf"\$\s*({_NUMBER})")
_UNIT_NAMES = "|".join(
    re.escape(u) for u in sorted({**_UNITS, **_CURRENCY}, key=len, reverse=True) if u != "$"
)
_UNIT_RE = re.compile(rf"(?<=\d)\s*({_UNIT_NAMES})(?![\w])")
_PERCENT_RE = re.compile(r"(?<=\d)\s*%")
_INTEGER_RE = re.compile(_D + rf"({_NUMBER})(?!\d)")


def _int(s: str) -> int:
    return int(s.replace(".", ""))


def _date(m: re.Match) -> str:
    day, month, year = int(m[1]), int(m[2]), int(m[3])
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return m[0]
    month_word = _MONTHS.get(month, read_integer(month))
    return f"ngày {read_integer(day)} tháng {month_word} năm {read_integer(year)}"


def _time(m: re.Match) -> str:
    h, mi = int(m[1]), int(m[2])
    if h > 24 or mi > 59:
        return m[0]
    return f"{read_integer(h)} giờ" + (f" {read_integer(mi)} phút" if mi else "")


def _time_h(m: re.Match) -> str:
    h = int(m[1])
    if h > 24:
        return m[0]
    return f"{read_integer(h)} giờ" + (f" {read_integer(int(m[2]))} phút" if m[2] else "")


def normalize_vietnamese(text: str) -> str:
    """Spell out dates, times, numbers and common units."""
    if not text:
        return text
    text = unicodedata.normalize("NFC", text)
    if not any(c.isdigit() for c in text):
        return text
    # Order matters: the specific shapes go first, plain integers last.
    text = _DATE_RE.sub(_date, text)
    text = _TIME_RE.sub(_time, text)
    text = _TIME_H_RE.sub(_time_h, text)
    text = _PHONE_RE.sub(lambda m: read_digits(m[0]), text)
    text = _DOLLAR_RE.sub(lambda m: f"{read_integer(_int(m[1]))} đô la", text)
    text = _PERCENT_RE.sub(" phần trăm", text)
    text = _UNIT_RE.sub(lambda m: " " + {**_UNITS, **_CURRENCY}[m[1]], text)
    text = _RANGE_RE.sub(lambda m: f"{read_integer(_int(m[1]))} đến {read_integer(_int(m[2]))}", text)
    text = _DECIMAL_RE.sub(lambda m: _read_decimal(m[1], m[2]), text)
    text = _INTEGER_RE.sub(lambda m: read_integer(_int(m[1])), text)
    return text
