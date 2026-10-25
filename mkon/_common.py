"""Общие помощники mkon: числа, даты, комментарии, JS-совместимые строки.

Реализация намеренно повторяет `mkon-js/src/common.js` один в один:
JS-версия — эталон, Python-версия должна давать тот же текст и те же данные.

Пара мест, где Python ведёт себя как JS, а не «как принято в Python»:

* Строки JS — это UTF-16. Длины (порог `condense`, отступы в `'''`)
  считаются в UTF-16 code units, см. `utf16_len`, `to_units`, `from_units`.
* Пробельные символы — набор JS `\\s`, а не `str.isspace()`.
* Числа пишутся по алгоритму `Number.prototype.toString` (`1e+21`, `1e-7`, `100`).
"""

from __future__ import annotations

import math
import re
import sys
from array import array
from datetime import date, datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# JS-совместимые строки
# ---------------------------------------------------------------------------

#: Символы класса `\s` в регулярных выражениях JS (и для `String.prototype.trim`).
JS_WS = "\t\n\x0b\x0c\r \xa0                　﻿"
#: То же, но в виде содержимого `[...]` для `re`.
JS_WS_CLASS = "\\t\\n\\x0b\\x0c\\r \\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff"
#: `.` в JS не матчит переводы строк.
JS_DOT = "[^\\n\\r\\u2028\\u2029]"

_ASTRAL_RE = re.compile("[\U00010000-\U0010ffff]")
_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def js_trim(s: str) -> str:
    return s.strip(JS_WS)


def js_rstrip(s: str) -> str:
    return s.rstrip(JS_WS)


def js_lstrip(s: str) -> str:
    return s.lstrip(JS_WS)


def utf16_len(s: str) -> int:
    """Длина строки, как её видит JS (`str.length`)."""
    return len(s) + len(_ASTRAL_RE.findall(s))


def to_units(s: str) -> str:
    """Строка → строка из UTF-16 code units (символы вне BMP становятся парами суррогатов).

    Парсер работает поверх неё, чтобы позиции и отступы считались как в JS.
    """
    if not _ASTRAL_RE.search(s):
        return s
    units = array("H")
    units.frombytes(s.encode("utf-16-le", "surrogatepass"))
    if sys.byteorder == "big":
        units.byteswap()
    return "".join(map(chr, units))


def from_units(s: str) -> str:
    """Обратно: пары суррогатов → нормальные символы. Одиночные суррогаты остаются как есть."""
    if not _SURROGATE_RE.search(s):
        return s
    return s.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")


def utf16_sort_key(s: str) -> bytes:
    """Ключ сортировки как у `Array.prototype.sort()` по умолчанию (по UTF-16 code units)."""
    return s.encode("utf-16-be", "surrogatepass")


_ARRAY_INDEX_RE = re.compile(r"(?:0|[1-9][0-9]*)\Z")


def js_key_order(keys):
    """Порядок ключей объекта в JS (`for...in`): сначала индексы массива по возрастанию,
    затем остальные ключи в порядке вставки."""
    index_keys = []
    other = []
    for k in keys:
        if _ARRAY_INDEX_RE.match(k) and int(k) < 4294967295:
            index_keys.append(k)
        else:
            other.append(k)
    index_keys.sort(key=int)
    return index_keys + other


# ---------------------------------------------------------------------------
# Числа
# ---------------------------------------------------------------------------

#: 2**53 - 1, как `Number.MAX_SAFE_INTEGER` в JS
MAX_SAFE_INTEGER = 9007199254740991


def try_parse_number(text: str, stop_at_next: bool = False):
    """Порт `tryParseNumber` из JS.

    Возвращает `int` (целый литерал в пределах ±(2**53 - 1)), `float` или `None`,
    если текст — не число.
    Как и в JS, бесконечные значения (`1e400`) числом не считаются.
    `-0` возвращается как `-0.0`, потому что у `int` нет отрицательного нуля.
    """
    n = len(text)
    at = 0

    def nxt():
        nonlocal at
        c = text[at] if at < n else ""
        at += 1
        return c

    string = ""
    leading_zeros = 0
    test_leading = True

    ch = nxt()
    if ch == "-":
        string = "-"
        ch = nxt()

    while "0" <= ch <= "9" and ch != "":
        if test_leading:
            if ch == "0":
                leading_zeros += 1
            else:
                test_leading = False
        string += ch
        ch = nxt()

    if test_leading:
        leading_zeros -= 1

    is_int = True
    if ch == ".":
        is_int = False
        string += "."
        ch = nxt()
        while "0" <= ch <= "9" and ch != "":
            string += ch
            ch = nxt()

    if ch in ("e", "E"):
        is_int = False
        string += ch
        ch = nxt()
        if ch in ("-", "+"):
            string += ch
            ch = nxt()
        while "0" <= ch <= "9" and ch != "":
            string += ch
            ch = nxt()

    while ch and ch <= " ":
        ch = nxt()

    if stop_at_next:
        nxt_ch = text[at] if at < n else ""
        if ch in (",", "}", "]") or (ch == "/" and nxt_ch in ("/", "*")):
            ch = ""

    if ch or leading_zeros:
        return None

    try:
        f = float(string)  # как Number(string) в JS
    except ValueError:
        return None
    if not math.isfinite(f):
        return None

    if is_int:
        if string == "-0":
            return -0.0
        i = int(string)
        # numbers in mkon are IEEE-754 doubles (as in JS / JSON): only "safe" integers stay int,
        # otherwise one file would hold different numbers in Python and JS
        return i if -MAX_SAFE_INTEGER <= i <= MAX_SAFE_INTEGER else f
    return f


#: Специальные значения float, которые mkon пишет литералами. Регистр важен.
SPECIAL_NUMBERS = {
    "NaN": math.nan,
    "Infinity": math.inf,
    "+Infinity": math.inf,
    "-Infinity": -math.inf,
}


def parse_special_number(text: str):
    return SPECIAL_NUMBERS.get(text)


def _js_float_to_string(x: float) -> str:
    """`Number.prototype.toString()` для конечного ненулевого float."""
    sign = "-" if x < 0 else ""
    r = repr(abs(x))  # кратчайшее представление, как и в JS
    if "e" in r:
        mant, exp_s = r.split("e")
        exp = int(exp_s)
    else:
        mant, exp = r, 0
    ip, _, fp = mant.partition(".")
    all_digits = ip + fp
    lz = len(all_digits) - len(all_digits.lstrip("0"))
    digits = all_digits[lz:].rstrip("0")
    k = len(digits)
    n = len(ip) - lz + exp  # x = 0.digits × 10^n

    if k <= n <= 21:
        s = digits + "0" * (n - k)
    elif 0 < n <= 21:
        s = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        s = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        es = ("+" if e >= 0 else "-") + str(abs(e))
        s = digits + "e" + es if k == 1 else digits[0] + "." + digits[1:] + "e" + es
    return sign + s


def number_to_string(value) -> str:
    """Число → текст так, чтобы parse вернул его же (и чтобы совпадало с JS)."""
    if isinstance(value, int):
        if -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            return str(value)
        # numbers in mkon are doubles: write a big int the way JS would write that double
        try:
            value = float(value)
        except OverflowError:
            raise ValueError("Integer is too large for an mkon number (IEEE-754 double): " + str(value)) from None
    if math.isnan(value):
        return "NaN"
    if value == math.inf:
        return "Infinity"
    if value == -math.inf:
        return "-Infinity"
    if value == 0:
        return "-0" if math.copysign(1.0, value) < 0 else "0"
    return _js_float_to_string(value)


# ---------------------------------------------------------------------------
# ISO 8601 даты
# ---------------------------------------------------------------------------

_ISO_DATE_SRC = r"([0-9]{4})-([0-9]{2})-([0-9]{2})"
_ISO_TIME_SRC = r"T([0-9]{2}):([0-9]{2})(?::([0-9]{2})(?:\.([0-9]+))?)?(Z|[+-][0-9]{2}:[0-9]{2})"
_ISO_RE = re.compile("^" + _ISO_DATE_SRC + "(?:" + _ISO_TIME_SRC + r")?\Z")
_ISO_LIKE_RE = re.compile(
    "^" + _ISO_DATE_SRC + "(?:" + _ISO_TIME_SRC + ")?[" + JS_WS_CLASS + "]*((,|\\]|\\}|//|/\\*)" + JS_DOT + r"*)?\Z"
)
_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


class MkonDatetime(datetime):
    """`datetime`, к которому можно прикрепить корневые комментарии (`keep_comments=True`)."""


def parse_iso_date(text: str):
    """Порт `parseIsoDate`: `datetime` (UTC, точность — миллисекунды) или `None`.

    Бросает `ValueError` для невалидной даты, похожей на ISO (`2024-02-30`, `25:00`).
    """
    m = _ISO_RE.match(text)
    if not m:
        return None
    ys, mos, ds, hs, mis, ss, frac, tz = m.groups()
    y, mo, d = int(ys), int(mos), int(ds)
    h = 0 if hs is None else int(hs)
    mi = 0 if mis is None else int(mis)
    s = 0 if ss is None else int(ss)
    ms = 0 if frac is None else int((frac + "00")[:3])

    leap = (y % 4 == 0 and y % 100 != 0) or y % 400 == 0
    dim = 29 if (mo == 2 and leap) else (_DAYS[mo - 1] if 1 <= mo <= 12 else 0)
    if mo < 1 or mo > 12 or d < 1 or d > dim or h > 23 or mi > 59 or s > 59:
        raise ValueError("Invalid date '" + text + "'")

    offset = 0
    if tz and tz != "Z":
        oh, om = int(tz[1:3]), int(tz[4:6])
        if oh > 23 or om > 59:
            raise ValueError("Invalid time zone offset in '" + text + "'")
        offset = (-1 if tz[0] == "-" else 1) * (oh * 60 + om)

    if y < 1:
        # JS умеет год 0000, Python datetime — нет (MINYEAR = 1)
        raise ValueError("Year 0000 is not supported by Python datetime in '" + text + "'")
    try:
        dt = datetime(y, mo, d, h, mi, s, ms * 1000, tzinfo=timezone.utc)
        return dt - timedelta(minutes=offset) if offset else dt
    except OverflowError:
        raise ValueError("Date out of range in '" + text + "'") from None


def looks_like_iso_date(text: str) -> bool:
    return bool(_ISO_LIKE_RE.match(text))


def is_date(value) -> bool:
    return isinstance(value, (datetime, date))


def date_to_string(value) -> str:
    """Порт `dateToString`.

    * `datetime` с часовым поясом → UTC; `YYYY-MM-DD` для полуночи, иначе
      `YYYY-MM-DDTHH:MM:SS[.mmm]Z` (микросекунды обрезаются до миллисекунд, как в JS).
    * `datetime` без пояса — ошибка: непонятно, какой это момент.
    * `date` → `YYYY-MM-DD` (полночь UTC, как его прочитает JS).
    """
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise TypeError("Cannot stringify naive datetime (no tzinfo): " + value.isoformat())
        u = value.astimezone(timezone.utc)
        y, mo, d = u.year, u.month, u.day
        h, mi, s, ms = u.hour, u.minute, u.second, u.microsecond // 1000
    else:
        y, mo, d = value.year, value.month, value.day
        h = mi = s = ms = 0
    head = f"{y:04d}-{mo:02d}-{d:02d}"
    if h == 0 and mi == 0 and s == 0 and ms == 0:
        return head
    t = f"{head}T{h:02d}:{mi:02d}:{s:02d}"
    return t + (f".{ms:03d}Z" if ms else "Z")


# ---------------------------------------------------------------------------
# Комментарии
# ---------------------------------------------------------------------------

class MkonDict(dict):
    """`dict`, который помнит комментарии (`loads(..., keep_comments=True)`)."""

    __slots__ = ("mkon_comments",)


class MkonList(list):
    """`list`, который помнит комментарии (`loads(..., keep_comments=True)`)."""

    __slots__ = ("mkon_comments",)


def create_comment(value, comment=None):
    """Прикрепляет структуру комментариев к значению и возвращает её."""
    value.mkon_comments = comment if comment is not None else {}
    return value.mkon_comments


def get_comment(value):
    """Комментарии, связанные со значением (или `None`).

    Структура — та же, что у `__COMMENTS__` в mkon-js:
    объект — `{"c": {ключ: [до, после]}, "o": [порядок ключей], "e": [внутри пустого]}`,
    массив — `{"a": [[до, после], ...], "e": [...]}`, корень — дополнительно `"r": [до, после]`.
    """
    return getattr(value, "mkon_comments", None)


def force_comment(text: str) -> str:
    """Порт `forceComment`: строкам, которые ещё не комментарии, добавляет `// `.

    Текст комментариев внутри всегда хранится с `\n` (как его собирает парсер).
    """
    if not text:
        return ""
    lines = text.split("\n")
    j = 0
    while j < len(lines):
        line = lines[j]
        for i, char in enumerate(line):
            if char == "/" and i + 1 < len(line) and line[i + 1] in ("/", "*"):
                if line[i + 1] == "*":
                    # считаем, что блочный комментарий занимает весь остаток текста
                    j = len(lines)
                break
            if char > " ":
                lines[j] = "// " + line
                break
        j += 1
    return "\n".join(lines)
