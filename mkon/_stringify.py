"""Сериализатор mkon — порт `mkon-js/src/stringify.js`.

Цель — побайтово тот же текст, что и у JS для тех же данных. Отсюда пара
JS-особенностей, которые повторены намеренно:

* порядок ключей обычного объекта — как у `for...in` в JS: ключи-индексы
  (`"0"`, `"1"`, `"10"`) по возрастанию, затем остальные в порядке вставки;
* длина для порога `condense` и сортировка `sortProps` — по UTF-16.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Callable, Optional

from ._common import (
    JS_DOT,
    JS_WS_CLASS,
    date_to_string,
    force_comment,
    get_comment,
    is_date,
    js_key_order,
    js_lstrip,
    js_rstrip,
    looks_like_iso_date,
    number_to_string,
    try_parse_number,
    utf16_len,
    utf16_sort_key,
)
from ._options import make_options

_PLAIN_TOKEN = {
    "obj": ("{", "}"),
    "arr": ("[", "]"),
    "key": ("", ""),
    "qkey": ('"', '"'),
    "col": (":", ""),
    "com": (",", ""),
    "str": ("", ""),
    "qstr": ('"', '"'),
    "mstr": ("'''", "'''"),
    "num": ("", ""),
    "lit": ("", ""),
    "date": ("", ""),
    "esc": ("\\", ""),
    "uni": ("\\u", ""),
    "rem": ("", ""),
}

_COLOR_TOKEN = {
    "obj": ("\x1b[37m{\x1b[0m", "\x1b[37m}\x1b[0m"),
    "arr": ("\x1b[37m[\x1b[0m", "\x1b[37m]\x1b[0m"),
    "key": ("\x1b[33m", "\x1b[0m"),
    "qkey": ('\x1b[33m"', '"\x1b[0m'),
    "col": ("\x1b[37m:\x1b[0m", ""),
    "com": ("\x1b[37m,\x1b[0m", ""),
    "str": ("\x1b[37;1m", "\x1b[0m"),
    "qstr": ('\x1b[37;1m"', '"\x1b[0m'),
    "mstr": ("\x1b[37;1m'''", "'''\x1b[0m"),
    "num": ("\x1b[36;1m", "\x1b[0m"),
    "lit": ("\x1b[36m", "\x1b[0m"),
    "date": ("\x1b[36;1m", "\x1b[0m"),
    "esc": ("\x1b[31m\\", "\x1b[0m"),
    "uni": ("\x1b[31m\\u", "\x1b[0m"),
    "rem": ("\x1b[35m", "\x1b[0m"),
}

_COMMON_RANGE = (
    "\\x7f-\\x9f\\u00ad\\u0600-\\u0604\\u070f\\u17b4\\u17b5\\u200c-\\u200f"
    "\\u2028-\\u202f\\u2060-\\u206f\\ufeff\\ufff0-\\uffff"
)
_WS = "[" + JS_WS_CLASS + "]"

_META = {
    "\b": "b", "\t": "t", "\n": "n", "\f": "f", "\r": "r",
    '"': '"', "'": "'", "\\": "\\",
}

_NEEDS_QUOTES = re.compile(
    "^" + _WS + r"|^\"|^'|^/\*|^//|^\{|^\}|^\[|^\]|^:|^,|" + _WS + r"\Z|[\x00-\x1f" + _COMMON_RANGE + "]"
)
_STARTS_WITH_KEYWORD = re.compile(
    r"^(true|false|null|NaN|[+-]?Infinity)" + _WS + r"*((,|\]|\}|//|/\*)" + JS_DOT + r"*)?\Z"
)
_NEEDS_ESCAPE_NAME = re.compile(r"[,\{\[\}\]" + JS_WS_CLASS + r":\"']|//|/\*")


def _breaks_quoteless(s: str) -> bool:
    """Обрежет ли парсер эту строку, если записать её без кавычек?"""
    depth = 0
    for c in s:
        if c in ("{", "["):
            depth += 1
        elif c in ("}", "]"):
            if depth == 0:
                return True
            depth -= 1
        elif c == "," and depth == 0:
            return True
    # an unclosed bracket would swallow the rest of the line
    return depth > 0


def _starts_with_nl(s: str) -> bool:
    if not s:
        return False
    i = 1 if s[0] == "\r" else 0
    return len(s) > i and s[i] == "\n"


def _comment_on_this_line(s) -> bool:
    return bool(s) and not _starts_with_nl(s)


def _kind(v) -> str:
    """Аналог `typeof` для веток condense: 'string' | 'object' | 'other'."""
    if isinstance(v, str):
        return "string"
    if v is None or isinstance(v, (bool, int, float, Decimal)):
        return "other"
    # dict, list, tuple, дата и всё, что пойдёт через default=
    return "object"


def _decimal_to_string(d: Decimal) -> str:
    if d.is_nan():
        if d.is_snan():
            raise ValueError("Cannot serialize signaling NaN")
        return "NaN"
    if d.is_infinite():
        return "-Infinity" if d < 0 else "Infinity"
    return str(d)


class _Ctx:
    __slots__ = ("mind", "eol_mind", "eol_gap", "prefix", "partial", "cpartial", "cdisabled",
                 "save_multiline", "iseparator", "cwrap_len")


class _Stringifier:
    def __init__(self, opt, default=None, check_circular=True, skipkeys=False):
        self.o = opt
        self.default = default
        self.markers = {} if check_circular else None
        self.skipkeys = skipkeys
        tok = _COLOR_TOKEN if opt.colors else _PLAIN_TOKEN
        if opt.quote_char != '"':
            q = opt.quote_char
            tok = dict(tok)
            tok["qkey"] = (tok["qkey"][0].replace('"', q), tok["qkey"][1].replace('"', q))
            tok["qstr"] = (tok["qstr"][0].replace('"', q), tok["qstr"][1].replace('"', q))
        # each token: (left, right, visibleLeftLen, visibleRightLen)
        self.token = {k: (v[0], v[1], len(_PLAIN_TOKEN[k][0]), len(_PLAIN_TOKEN[k][1])) for k, v in tok.items()}

        quote_esc = '\\"' if opt.quote_char == '"' else "'"
        self.needs_escape = re.compile("[\\\\" + quote_esc + "\\x00-\\x1f" + _COMMON_RANGE + "]")
        # \r is excluded on purpose: the reader drops \r inside ''' (CRLF files)
        self.needs_escape_ml = re.compile(
            "'''|^" + _WS + "+\\Z|[\\x00-" + ("\\x09" if opt.multiline == 2 else "\\x08")
            + "\\x0b-\\x1f" + _COMMON_RANGE + "]"
        )

        self.quote_strings = opt.quote_strings
        self.multiline = opt.multiline
        self.gap = ""
        self.wrap_len = 0

    # ------------------------------------------------------------ helpers

    def wrap(self, tk, v: str) -> str:
        self.wrap_len += len(tk[0]) + len(tk[1]) - tk[2] - tk[3]
        return tk[0] + v + tk[1]

    def quote_replace(self, string: str) -> str:
        token = self.token

        def rep(m):
            a = m.group(0)
            c = _META.get(a)
            if c is not None:
                return self.wrap(token["esc"], c)
            return self.wrap(token["uni"], format(ord(a), "04x"))

        return self.needs_escape.sub(rep, string)

    # ----------------------------------------------------------- comments

    @staticmethod
    def reindent(text: str, indent_level: str) -> str:
        out = []
        for line in text.split("\n"):
            stripped = js_lstrip(line)
            out.append(indent_level + stripped if stripped else "")
        return "\n".join(out)

    def render_comment(self, s, indent_level: str) -> str:
        if not s:
            return ""
        return self.wrap(self.token["rem"], self.reindent(force_comment(s), indent_level))

    def render_inline_comment(self, s) -> str:
        if not s:
            return ""
        return " " + self.wrap(self.token["rem"], js_lstrip(force_comment(s)))

    def before_comment(self, c, indent_level: str) -> str:
        if c and c[0]:
            bc = self.render_comment(c[0], indent_level)
            if bc:
                return self.o.eol + bc + self.o.eol + self.gap
        return self.o.eol + self.gap

    def after_comment(self, c, ca: bool, indent_level: str) -> str:
        if not c or len(c) < 2 or not c[1]:
            return ""
        if ca:
            return self.render_inline_comment(c[1])
        return self.o.eol + self.render_comment(c[1], indent_level)

    # ------------------------------------------------------ strings / keys

    def quote(self, string: str, has_comment, is_root_object, no_indent) -> str:
        token = self.token
        if not string:
            return self.wrap(token["qstr"], "")

        if (
            self.quote_strings or has_comment
            or _NEEDS_QUOTES.search(string)
            or _breaks_quoteless(string)
            or try_parse_number(string, True) is not None
            or _STARTS_WITH_KEYWORD.match(string)
            or looks_like_iso_date(string)
            or (is_root_object and ":" in string)
        ):
            if not self.needs_escape.search(string):
                return self.wrap(token["qstr"], string)
            if not self.needs_escape_ml.search(string) and not is_root_object and self.multiline:
                return self.ml_string(string, self.gap, self.o.braces_same_line or no_indent, no_indent)
            return self.wrap(token["qstr"], self.quote_replace(string))
        return self.wrap(token["str"], string)

    def ml_string(self, string: str, current_gap: str, same_line, shallow) -> str:
        token, eol = self.token, self.o.eol
        a = string.replace("\r", "").split("\n")
        if not shallow:
            current_gap += self.o.indent

        # one-line '''x''' form: the reader skips whitespace right after ''',
        # so a value starting with whitespace needs the block form
        if len(a) == 1 and not a[0].startswith("'") and not a[0].endswith("'") and not a[0][:1] <= " ":
            return self.wrap(token["mstr"], a[0])
        # The reader takes the block indent from the column of the opening '''.
        # For an object value (not shallow) that column must equal current_gap,
        # so ''' always goes on its own line, even with braces_same_line.
        res = ("" if (same_line and shallow) else eol + current_gap) + token["mstr"][0]
        for line in a:
            res += eol
            if line:
                res += current_gap + line
        return res + eol + current_gap + token["mstr"][1]

    def quote_key(self, name: str) -> str:
        token = self.token
        if not name:
            return token["qkey"][0] + token["qkey"][1]
        if self.o.quote_keys or _NEEDS_ESCAPE_NAME.search(name):
            return self.wrap(token["qkey"], self.quote_replace(name) if self.needs_escape.search(name) else name)
        return self.wrap(token["key"], name)

    # ------------------------------------------------------------ dispatch

    def str(self, value, has_comment=None, no_indent=None, is_root_object=None) -> str:
        token = self.token
        if is_date(value):
            return self.wrap(token["date"], date_to_string(value))
        if isinstance(value, str):
            return self.quote(value, has_comment, is_root_object, no_indent)
        if value is True or value is False:
            return self.wrap(token["lit"], "true" if value else "false")
        if isinstance(value, (int, float)):
            return self.wrap(token["num"], number_to_string(value))
        if value is None:
            return self.wrap(token["lit"], "null")
        if isinstance(value, Decimal):
            return self.wrap(token["num"], _decimal_to_string(value))
        if isinstance(value, (dict, list, tuple)):
            return self.container(value, no_indent)
        if self.default is None:
            raise TypeError(f"Object of type {type(value).__name__} is not mkon serializable")
        return self.with_marker(value, lambda: self.str(self.default(value), has_comment, no_indent, is_root_object))

    def with_marker(self, value, fn):
        markers = self.markers
        if markers is None:
            return fn()
        mid = id(value)
        if mid in markers:
            raise ValueError("Circular reference detected")
        markers[mid] = value
        try:
            return fn()
        finally:
            del markers[mid]

    def container(self, value, no_indent):
        comments = get_comment(value) if self.o.keep_comments else None
        if isinstance(value, dict):
            return self.with_marker(value, lambda: self.str_object(self.str_keys(value), comments, no_indent))
        return self.with_marker(value, lambda: self.str_array(value, comments, no_indent))

    def str_keys(self, value: dict) -> dict:
        """Ключи как в json: str как есть; int/float/bool/None → строка; иначе TypeError (или пропуск)."""
        if all(isinstance(k, str) for k in value):
            return value
        out = {}
        for k, v in value.items():
            if isinstance(k, str):
                pass
            elif k is True or k is False:
                k = "true" if k else "false"
            elif k is None:
                k = "null"
            elif isinstance(k, (int, float)):
                k = number_to_string(k)
            elif isinstance(k, Decimal):
                k = _decimal_to_string(k)
            elif self.skipkeys:
                continue
            else:
                raise TypeError(f"keys must be str, int, float, bool or None, not {type(k).__name__}")
            out[k] = v
        return out

    # ------------------------------------------------------------ containers

    def open_container(self, no_indent) -> _Ctx:
        o = self.o
        ctx = _Ctx()
        ctx.mind = self.gap
        self.gap += o.indent
        ctx.eol_mind = o.eol + ctx.mind
        ctx.eol_gap = o.eol + self.gap
        ctx.prefix = "" if (no_indent or o.braces_same_line) else ctx.eol_mind
        ctx.partial = []
        ctx.cpartial = [] if o.condense else None
        ctx.cdisabled = False
        ctx.save_multiline = self.multiline
        ctx.iseparator = "" if o.separator else self.token["com"][0]
        ctx.cwrap_len = 0
        return ctx

    def condensed_string(self, v, ctx) -> str:
        self.wrap_len = 0
        self.multiline = 0
        try:
            return self.str(v, False, True)
        finally:
            self.multiline = ctx.save_multiline

    def finish(self, ctx, tk, count, comments) -> str:
        partial = ctx.partial
        if count == 0:
            if comments and comments.get("e"):
                ec = self.render_comment(comments["e"][0], ctx.mind)
                partial.append(ctx.eol_mind + ec + ctx.eol_mind if ec else ctx.eol_mind)
        else:
            partial.append(ctx.eol_mind)

        if not partial:
            res = self.wrap(tk, "")
        else:
            res = ctx.prefix + self.wrap(tk, "".join(partial))
            if ctx.cpartial is not None and not ctx.cdisabled:
                cres = " ".join(ctx.cpartial)
                if utf16_len(cres) - ctx.cwrap_len <= self.o.condense:
                    res = self.wrap(tk, cres)
        self.gap = ctx.mind
        return res

    def str_array(self, value, comments, no_indent) -> str:
        token, o = self.token, self.o
        ctx = self.open_container(no_indent)
        partial, cpartial = ctx.partial, ctx.cpartial
        length = len(value)

        for i, v in enumerate(value):
            setsep = i < length - 1
            c = None
            if comments:
                a = comments.get("a") or []
                c = a[i] if i < len(a) and a[i] else []
            ca = _comment_on_this_line(c[1]) if c and len(c) > 1 else False

            if comments:
                partial.append(self.before_comment(c, self.gap))
                if cpartial is not None and ((c and (c[0] or (len(c) > 1 and c[1]))) or ca):
                    cpartial.clear()
                    ctx.cdisabled = True
            else:
                partial.append(o.eol + self.gap)

            self.wrap_len = 0
            partial.append(self.str(v, ca if comments else False, True) + (o.separator if setsep else ""))

            if cpartial is not None and not ctx.cdisabled:
                kind = _kind(v)
                if kind == "string":
                    cpartial.append(self.condensed_string(v, ctx) + (token["com"][0] if setsep else ""))
                elif kind == "object":
                    ctx.cdisabled = True
                else:
                    cpartial.append(partial[-1] + (ctx.iseparator if setsep else ""))
                if setsep:
                    self.wrap_len += len(token["com"][0]) - token["com"][2]
                ctx.cwrap_len += self.wrap_len

            ac = self.after_comment(c, ca, self.gap)
            if ac:
                partial.append(ac)

        return self.finish(ctx, token["arr"], length, comments)

    def object_keys(self, value: dict, comments):
        comment_keys = list(comments.get("o", [])) if comments else []
        in_comments = set(comment_keys)
        object_keys = [k for k in js_key_order(list(value.keys())) if k not in in_comments]
        if self.o.sort_props:
            object_keys.sort(key=utf16_sort_key)
        # ключи из комментариев, которых уже нет в объекте, JS тоже печатает (как undefined) —
        # здесь их пропускаем, чтобы не падать
        return [k for k in comment_keys if k in value] + object_keys

    def str_object(self, value: dict, comments, no_indent) -> str:
        token, o = self.token, self.o
        ctx = self.open_container(no_indent)
        partial, cpartial = ctx.partial, ctx.cpartial
        keys = self.object_keys(value, comments)
        length = len(keys)

        for i, k in enumerate(keys):
            setsep = i < length - 1
            c = None
            if comments:
                c = (comments.get("c") or {}).get(k) or []
            ca = _comment_on_this_line(c[1]) if c and len(c) > 1 else False

            if comments:
                partial.append(self.before_comment(c, self.gap))
                if cpartial is not None and ((c and (c[0] or (len(c) > 1 and c[1]))) or ca):
                    ctx.cdisabled = True
            else:
                partial.append(o.eol + self.gap)

            self.wrap_len = 0
            v = value[k]
            vs = self.str(v, bool(comments) and ca)
            partial.append(
                self.quote_key(k) + token["col"][0] + ("" if _starts_with_nl(vs) else " ") + vs
                + (o.separator if setsep else "")
            )

            ac = self.after_comment(c, ca, self.gap)
            if ac:
                partial.append(ac)

            if cpartial is not None and not ctx.cdisabled:
                kind = _kind(v)
                if kind == "string":
                    vs2 = self.condensed_string_obj(v, ctx)
                    cpartial.append(self.quote_key(k) + token["col"][0] + " " + vs2 + (token["com"][0] if setsep else ""))
                elif kind == "object":
                    ctx.cdisabled = True
                else:
                    cpartial.append(partial[-1] + (ctx.iseparator if setsep else ""))
                self.wrap_len += len(token["col"][0]) - token["col"][2]
                if setsep:
                    self.wrap_len += len(token["com"][0]) - token["com"][2]
                ctx.cwrap_len += self.wrap_len

        return self.finish(ctx, token["obj"], length, comments)

    def condensed_string_obj(self, v, ctx) -> str:
        # в объекте JS вызывает str(v, false) — без noIndent
        self.wrap_len = 0
        self.multiline = 0
        try:
            return self.str(v, False)
        finally:
            self.multiline = ctx.save_multiline

    # ------------------------------------------------------------------ root

    def run(self, data) -> str:
        o = self.o
        data_comments = get_comment(data) if o.keep_comments else None
        root_comments = data_comments.get("r") if data_comments else None
        res = self.str(data, None, True, True)

        if isinstance(data, dict) and not o.emit_root_braces and res[:1] == "{" and res[-1:] == "}":
            res = res[1:-1].replace("\n" + o.indent, "\n")
            if res[:1] == "\n":
                res = res[1:]
            if res[-1:] == "\n":
                res = res[:-1]

        if root_comments and root_comments[0]:
            res = js_rstrip(root_comments[0]) + o.eol + res
        if root_comments and len(root_comments) > 1 and root_comments[1]:
            res += js_rstrip(root_comments[1])
        if not res.endswith(o.eol):
            res += o.eol
        return res


def dumps(
    obj,
    *,
    indent=2,
    sort_keys: bool = False,
    default: Optional[Callable[[Any], Any]] = None,
    check_circular: bool = True,
    skipkeys: bool = False,
    quotes: str = "min",
    quote_char: str = '"',
    separator: bool = False,
    braces_same_line: bool = False,
    root_braces: bool = True,
    condense: int = 0,
    multiline: str = "std",
    eol: str = "\n",
    keep_comments: bool = True,
    colors: bool = False,
) -> str:
    """Сериализует `obj` в текст mkon.

    Аргументы, общие с `json.dumps`: `indent` (int — число пробелов, str — строка отступа),
    `sort_keys`, `default(obj)` для неподдерживаемых типов, `check_circular`, `skipkeys`.

    Аргументы mkon (те же, что в mkon-js):

    * `quotes` — 'min' (кавычки только где нужны), 'keys', 'strings' или 'all';
    * `quote_char` — '"' или "'"; `separator` — запятые между элементами;
    * `braces_same_line` — `{` / `[` на строке с ключом; `root_braces` — фигурные скобки у корня;
    * `condense` — уместить объект/массив в одну строку, если он короче (0 — выключено);
    * `multiline` — 'std' (многострочные строки в тройных кавычках), 'no-tabs' (то же,
      но табы экранируются) или 'off';
    * `eol` — '\\n' или '\\r\\n'; `colors` — ANSI-раскраска для терминала;
    * `keep_comments` — записать комментарии значений, прочитанных с `loads(keep_comments=True)`.

    Типы: `dict`, `list`/`tuple`, `str`, `int`, `float` (включая NaN/±Infinity/-0), `bool`, `None`,
    `decimal.Decimal`, `datetime` с часовым поясом и `date`.
    """
    opt = make_options(
        indent=indent, sort_keys=sort_keys, quotes=quotes, quote_char=quote_char, separator=separator,
        braces_same_line=braces_same_line, root_braces=root_braces, condense=condense,
        multiline=multiline, eol=eol, keep_comments=keep_comments, colors=colors,
    )
    return _Stringifier(opt, default, check_circular, skipkeys).run(obj)


def dump(obj, fp, **kw) -> None:
    """Как `dumps`, но пишет результат в файловый объект (`fp.write`)."""
    fp.write(dumps(obj, **kw))
