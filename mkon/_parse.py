"""Парсер mkon — порт `mkon-js/src/parse.js` (эталон).

Парсер идёт по UTF-16 code units исходника (как JS), поэтому отступы `'''`-строк
и решения парсера совпадают с JS-версией. Строки результата собираются обратно
в обычный `str`, позиции в ошибках — в символах Python.

Горячие циклы (пробелы и комментарии, обычные символы в строках, ключах и
quoteless-значениях) проходятся регулярками «пачкой»; все решения парсера
принимаются на тех же символах и в том же порядке, что и в JS.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Optional

from ._common import (
    MkonDatetime,
    MkonDict,
    MkonList,
    create_comment,
    from_units,
    get_comment,
    js_rstrip,
    js_trim,
    parse_iso_date,
    parse_special_number,
    to_units,
    try_parse_number,
)

__all__ = ["MkonDecodeError", "loads", "load"]


class MkonDecodeError(ValueError):
    """Ошибка разбора mkon (по образцу `json.JSONDecodeError`).

    Атрибуты: `msg`, `doc`, `pos`, `lineno`, `colno` (с 1), `hint` — подсказка
    о вероятной причине (например, quoteless-строка «съела» скобку), может быть пустой.
    """

    def __init__(self, msg: str, doc: str, pos: int, hint: str = ""):
        lineno = doc.count("\n", 0, pos) + 1
        colno = pos - doc.rfind("\n", 0, pos)
        super().__init__(f"{msg}: line {lineno} column {colno} (char {pos})")
        self.msg = msg
        self.doc = doc
        self.pos = pos
        self.lineno = lineno
        self.colno = colno
        self.hint = hint

    def __reduce__(self):
        return self.__class__, (self.msg, self.doc, self.pos, self.hint)


_ESCAPEE = {
    '"': '"',
    "'": "'",
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}

#: пробелы (всё, что <= ' ') и комментарии `//…` / `/*…*/` (незакрытый — до конца текста)
_WHITE_RE = re.compile(r"(?:[\x00-\x20]+|//[^\n]*|/\*[\s\S]*?(?:\*/|\Z))*")
#: обычные символы quoteless-значения: всё, кроме конца строки, разделителей, скобок и '/'
_TFNNS_PLAIN_RE = re.compile(r"[^\r\n,{}\[\]/]+")
#: обычные символы строки в кавычках
_STRING_PLAIN_RE = {'"': re.compile(r'[^"\\\n\r]+'), "'": re.compile(r"[^'\\\n\r]+")}
#: обычные символы внутри '''-строки
_ML_PLAIN_RE = re.compile(r"[^'\n\r]+")
#: обычные символы ключа без кавычек
_KEY_PLAIN_RE = re.compile(r"[^\x00-\x20{}\[\],:]+")
_ROOT_DATETIME_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:")
_PUNCTUATORS = frozenset("{}[],:")


class _Parser:
    def __init__(self, source: str, keep_comments: bool, object_hook, object_pairs_hook,
                 parse_float, parse_int, parse_constant):
        self.source = source
        self.text = to_units(source)
        self.astral = len(self.text) != len(source)
        self.n = len(self.text)
        self.at = 0
        self.ch = " "
        self.keep = keep_comments
        self.object_hook = object_hook
        self.object_pairs_hook = object_pairs_hook
        self.parse_float = parse_float
        self.parse_int = parse_int
        self.parse_constant = parse_constant

    # ------------------------------------------------------------------ basics

    def char_at(self, i: int) -> str:
        """`text.charAt(i)`: пустая строка вне диапазона (в том числе для отрицательных)."""
        return self.text[i] if 0 <= i < self.n else ""

    def idx(self, i: int):
        """`text[i]`: `None` (как `undefined`) вне диапазона."""
        return self.text[i] if 0 <= i < self.n else None

    def error(self, msg: str):
        pos = min(max(self.at - 1, 0), self.n)
        if self.astral:  # позиция в UTF-16 → позиция в символах Python
            pos = len(from_units(self.text[:pos]))
        raise MkonDecodeError(msg, self.source, pos)

    def next(self) -> str:
        at = self.at
        self.ch = ch = self.text[at] if at < self.n else ""
        self.at = at + 1
        return ch

    def peek(self, offs: int) -> str:
        return self.char_at(self.at + offs)

    def jump(self, pos: int):
        """Встать на символ `pos` (как серия `next()` до него)."""
        self.at = pos
        self.next()

    # ----------------------------------------------------------------- strings

    def string(self, allow_ml: bool) -> str:
        text = self.text
        string = []
        exit_ch = self.ch
        plain = _STRING_PLAIN_RE[exit_ch].match
        while True:
            m = plain(text, self.at)
            if m:
                string.append(m.group())
                self.at = m.end()
            if not self.next():
                break
            ch = self.ch
            if ch == exit_ch:
                self.next()
                if allow_ml and exit_ch == "'" and self.ch == "'" and not string:
                    # ''' indicates a multiline string
                    self.next()
                    return self.ml_string()
                return from_units("".join(string))
            if ch == "\\":
                self.next()
                ch = self.ch
                if ch == "u":
                    uffff = 0
                    for _ in range(4):
                        self.next()
                        c = self.ch
                        if "0" <= c <= "9" and c:
                            hx = ord(c) - 48
                        elif "a" <= c <= "f" and c:
                            hx = ord(c) - 97 + 0xA
                        elif "A" <= c <= "F" and c:
                            hx = ord(c) - 65 + 0xA
                        else:
                            self.error("Bad \\u char " + c)
                        uffff = uffff * 16 + hx
                    string.append(chr(uffff))
                elif ch in _ESCAPEE and ch != "":
                    string.append(_ESCAPEE[ch])
                else:
                    break
            elif ch in ("\n", "\r"):
                self.error("Bad string containing newline")
            else:
                string.append(ch)
        self.error("Bad string")

    def ml_string(self) -> str:
        string = []
        triple = 0

        # we are at ''' +1 - get indent
        indent = 0
        while True:
            c = self.peek(-indent - 5)
            if not c or c == "\n":
                break
            indent += 1

        def skip_indent():
            skip = indent
            while self.ch and self.ch <= " " and self.ch != "\n":
                if skip <= 0:
                    break
                skip -= 1
                self.next()

        # skip white/to (newline)
        while self.ch and self.ch <= " " and self.ch != "\n":
            self.next()
        if self.ch == "\n":
            self.next()
            skip_indent()

        while True:
            ch = self.ch
            if not ch:
                self.error("Bad multiline string")
            elif ch == "'":
                triple += 1
                self.next()
                if triple == 3:
                    if string and string[-1] == "\n":
                        string.pop()  # remove last EOL
                    return from_units("".join(string))
                continue
            else:
                while triple > 0:
                    string.append("'")
                    triple -= 1
            if self.ch == "\n":
                string.append("\n")
                self.next()
                skip_indent()
            elif self.ch == "\r":
                self.next()
            else:
                # обычные символы до кавычки / конца строки — пачкой
                m = _ML_PLAIN_RE.match(self.text, self.at - 1)
                string.append(m.group())
                self.jump(m.end())

    def keyname(self) -> str:
        if self.ch in ('"', "'"):
            return self.string(False)

        text = self.text
        name = ""
        start = self.at
        space = -1
        while True:
            ch = self.ch
            if ch == ":":
                if not name:
                    self.error("Found ':' but no key name (for an empty key name use quotes)")
                elif space >= 0 and space != len(name):
                    self.at = start + space
                    self.error("Found whitespace in your key name (use quotes to include)")
                return from_units(name)
            elif ch <= " ":
                if not ch:
                    self.error("Found EOF while looking for a key name (check your syntax)")
                elif space < 0:
                    space = len(name)
            elif ch in _PUNCTUATORS:
                self.error(
                    "Found '" + ch + "' where a key name was expected (check your syntax or use quotes "
                    "if the key name includes {}[],: or whitespace)"
                )
            else:
                m = _KEY_PLAIN_RE.match(text, self.at - 1)
                name += m.group()
                self.jump(m.end())
                continue
            self.next()

    def white(self):
        ch = self.ch
        if not ch or (ch > " " and ch != "/"):  # частый случай: уже стоим на значимом символе
            return
        if self.at == 0:  # стартовое состояние: виртуальный пробел перед текстом
            self.next()
            if not self.ch:
                return
        self.jump(_WHITE_RE.match(self.text, self.at - 1).end())

    def number(self, literal: str, value):
        """Число с учётом хуков: целый литерал → `parse_int`, с точкой/экспонентой → `parse_float`."""
        if self.parse_int is None and self.parse_float is None:
            return value
        if not any(c in literal for c in ".eE"):
            return self.parse_int(literal) if self.parse_int is not None else value
        return self.parse_float(literal) if self.parse_float is not None else value

    def tfnns(self, in_container: bool):
        """Quoteless-строка, число, NaN/±Infinity, дата, true/false/null."""
        text = self.text
        if self.ch in _PUNCTUATORS:
            self.error(
                "Found a punctuator character '" + self.ch + "' when expecting a quoteless string (check your syntax)"
            )

        start = self.at - 1
        depth = 0  # brackets opened inside this quoteless string
        while True:
            m = _TFNNS_PLAIN_RE.match(text, self.at)
            if m:
                self.at = m.end()
            self.next()
            ch = self.ch
            is_eol = ch in ("\r", "\n", "")
            is_sep = ch in (",", "}", "]") and ch != ""
            ends_string = is_eol or (in_container and is_sep and depth == 0)
            if ends_string or is_sep or (ch == "/" and self.peek(0) in ("/", "*") and self.peek(0) != ""):
                v = text[start:self.at - 1]
                chf = v[:1]  # пусто, если значение началось на конце текста (`a:` в конце файла) — как в JS
                t = js_trim(v)
                if chf == "f":
                    if t == "false":
                        return False
                elif chf == "n":
                    if t == "null":
                        return None
                elif chf == "t":
                    if t == "true":
                        return True
                elif chf in ("N", "I", "+"):
                    sp = parse_special_number(t)
                    if sp is not None:
                        return self.parse_constant(t) if self.parse_constant else sp
                elif chf == "-" or "0" <= chf <= "9":
                    num = try_parse_number(v)
                    if num is not None:
                        return self.number(t, num)
                    if chf != "-":
                        try:
                            dt = parse_iso_date(t)
                        except ValueError as e:
                            self.error(str(e))
                        if dt is not None:
                            return dt
                    if chf == "-":
                        sn = parse_special_number(t)
                        if sn is not None:
                            return self.parse_constant(t) if self.parse_constant else sn
                if ends_string:
                    return from_units(t)
            if ch in ("{", "["):
                depth += 1
            elif ch in ("}", "]") and depth > 0:
                depth -= 1

    # ---------------------------------------------------------------- comments

    def read_comment_at(self, c_at: int, first: bool = False):
        text = self.text
        c_at -= 1
        # remove trailing whitespace, but only up to EOL
        i = self.at - 2
        while True:
            t = self.idx(i)
            if not (i > c_at and t is not None and t <= " " and t != "\n"):
                break
            i -= 1
        if self.idx(i) == "\r":
            i -= 1
        length = i - c_at + 1
        res = text[c_at:c_at + length] if length > 0 and c_at >= 0 else ""
        for ch in res:
            if ch > " ":
                j = res.find("\n")
                if j >= 0:
                    c = [js_rstrip(res[:j]), js_rstrip(res[j + 1:])]
                    if first and len(js_trim(c[0])) == 0:
                        c.pop(0)
                    return [from_units(x) for x in c]
                return [from_units(js_rstrip(res))]
        return []

    @staticmethod
    def error_closing_hint(value) -> str:
        def search(v, ch):
            res = None
            if isinstance(v, str):
                if ch in v:
                    res = v
            elif isinstance(v, list):
                for item in v:
                    res = search(item, ch) or res
            elif isinstance(v, dict):
                for item in v.values():
                    res = search(item, ch) or res
            return res

        def report(ch):
            possible = search(value, ch)
            if possible:
                return (
                    "found '" + ch + "' in a string value, your mistake could be with:\n"
                    "  > " + possible + "\n"
                    "  (unquoted strings end at the next line or at a ',' / '}' / ']' outside of brackets!)"
                )
            return ""

        return report("}") or report("]")

    # -------------------------------------------------------------- containers

    def array(self):
        arr = MkonList() if self.keep else []
        comments = None
        next_comment = None
        try:
            if self.keep:
                comments = create_comment(arr, {"a": []})

            self.next()
            c_at = self.at
            self.white()
            if comments is not None:
                next_comment = "\n".join(self.read_comment_at(c_at, True))
            if self.ch == "]":
                self.next()
                if comments is not None:
                    comments["e"] = [next_comment]
                return arr

            while self.ch:
                arr.append(self.value(True))
                c_at = self.at
                self.white()
                # the comma is optional and trailing commas are allowed
                if self.ch == ",":
                    self.next()
                    c_at = self.at
                    self.white()
                if comments is not None:
                    c = self.read_comment_at(c_at)
                    comments["a"].append([next_comment or "", c[0] if c else ""])
                    next_comment = c[1] if len(c) > 1 else None
                if self.ch == "]":
                    self.next()
                    if comments is not None:
                        comments["a"][-1][1] += next_comment or ""
                    return arr
                self.white()

            self.error("End of input while parsing an array (missing ']')")
        except MkonDecodeError as e:
            e.hint = e.hint or self.error_closing_hint(arr)
            raise

    def finish_object(self, obj, pairs):
        if self.object_pairs_hook is not None:
            return self.object_pairs_hook(pairs)
        if self.object_hook is not None:
            return self.object_hook(obj)
        return obj

    def object(self, without_braces: bool = False):
        obj = MkonDict() if self.keep else {}
        pairs = [] if self.object_pairs_hook is not None else None
        comments = None
        next_comment = None
        try:
            if self.keep:
                comments = create_comment(obj, {"c": {}, "o": []})

            if not without_braces:
                self.next()
                c_at = self.at
                self.white()
                if comments is not None:
                    next_comment = "\n".join(self.read_comment_at(c_at, True))
            else:
                # the leading comment of a braceless root is stored as the root comment
                self.white()
                c_at = self.at

            if self.ch == "}" and not without_braces:
                if comments is not None:
                    comments["e"] = [next_comment]
                self.next()
                return self.finish_object(obj, pairs)

            while self.ch:
                key = self.keyname()
                self.white()
                if self.ch != ":":
                    self.error("Expected ':' instead of '" + self.ch + "'")
                self.next()
                # duplicate keys overwrite the previous value (and keep its position)
                value = self.value(True)
                obj[key] = value
                if pairs is not None:
                    pairs.append((key, value))
                c_at = self.at
                self.white()
                if self.ch == ",":
                    self.next()
                    c_at = self.at
                    self.white()
                if comments is not None:
                    c = self.read_comment_at(c_at)
                    if key not in comments["c"]:
                        comments["o"].append(key)
                    comments["c"][key] = [next_comment or "", c[0] if c else ""]
                    next_comment = c[1] if len(c) > 1 else None
                if self.ch == "}" and not without_braces:
                    self.next()
                    if comments is not None:
                        comments["c"][key][1] += next_comment or ""
                    return self.finish_object(obj, pairs)
                self.white()

            if without_braces:
                return self.finish_object(obj, pairs)
            self.error("End of input while parsing an object (missing '}')")
        except MkonDecodeError as e:
            e.hint = e.hint or self.error_closing_hint(obj)
            raise

    def value(self, in_container: bool = False):
        self.white()
        ch = self.ch
        if ch == "{":
            return self.object()
        if ch == "[":
            return self.array()
        if ch in ("'", '"') and ch != "":
            return self.string(True)
        return self.tfnns(in_container)

    # -------------------------------------------------------------------- root

    def check_trailing(self, v, c):
        c_at = self.at
        self.white()
        if self.ch:
            self.error("Syntax error, found trailing characters")
        if self.keep:
            b = "\n".join(c)
            a = "\n".join(self.read_comment_at(c_at))
            # comments can only be attached to containers and dates;
            # around a primitive root they are dropped (same as JS)
            if (a or b) and isinstance(v, (MkonDict, MkonList, datetime)):
                if isinstance(v, datetime) and not isinstance(v, MkonDatetime):
                    v = MkonDatetime(v.year, v.month, v.day, v.hour, v.minute, v.second,
                                     v.microsecond, tzinfo=v.tzinfo)
                comments = get_comment(v)
                if comments is None:
                    comments = create_comment(v, {})
                comments["r"] = [b, a]
        return v

    def root_value(self):
        self.white()
        c = self.read_comment_at(1) if self.keep else None

        if self.ch == "{":
            return self.check_trailing(self.object(), c)
        if self.ch == "[":
            return self.check_trailing(self.array(), c)

        state = (self.at, self.ch)

        # A bare ISO date-time at the root ("2024-01-15T10:30Z") contains ':'
        # and would otherwise be read as a braceless object.
        if self.at >= 1 and _ROOT_DATETIME_RE.match(self.text, self.at - 1):
            try:
                dt = self.value()
                if isinstance(dt, datetime):
                    return self.check_trailing(dt, c)
            except MkonDecodeError:
                pass
            self.at, self.ch = state

        try:
            return self.check_trailing(self.object(True), c)
        except MkonDecodeError:
            self.at, self.ch = state
            return self.check_trailing(self.value(), c)


def loads(
    s,
    *,
    keep_comments: bool = False,
    object_hook: Optional[Callable[[dict], Any]] = None,
    object_pairs_hook: Optional[Callable[[list], Any]] = None,
    parse_float: Optional[Callable[[str], Any]] = None,
    parse_int: Optional[Callable[[str], Any]] = None,
    parse_constant: Optional[Callable[[str], Any]] = None,
):
    """Разбирает документ mkon (`str`, `bytes` или `bytearray`) в объекты Python.

    Аргументы — как у `json.loads`:

    * `object_hook(dict)` / `object_pairs_hook(list_of_pairs)` — построение объектов;
    * `parse_float(str)` — для чисел с точкой или экспонентой (например, `decimal.Decimal`);
    * `parse_int(str)` — для целых литералов (например, `int`, чтобы не терять точность
      целых за пределами ±2**53: по умолчанию они, как в JS, становятся `float`);
    * `parse_constant(str)` — для `NaN`, `Infinity`, `+Infinity`, `-Infinity`.

    `keep_comments=True` сохраняет комментарии: объекты и массивы тогда — `MkonDict` /
    `MkonList` (см. `get_comments`), и `dumps` запишет комментарии обратно. Хуки построения
    объектов с этим режимом совместимы, но комментарии остаются только на `MkonDict`.

    Ошибка разбора — `MkonDecodeError` (наследник `ValueError`).
    """
    if isinstance(s, (bytes, bytearray)):
        s = bytes(s).decode("utf-8-sig")
    elif not isinstance(s, str):
        raise TypeError(f"the mkon object must be str, bytes or bytearray, not {type(s).__name__}")
    return _Parser(s, keep_comments, object_hook, object_pairs_hook, parse_float, parse_int,
                   parse_constant).root_value()


def load(fp, **kw):
    """Как `loads`, но читает из файлового объекта (`fp.read()`)."""
    return loads(fp.read(), **kw)
