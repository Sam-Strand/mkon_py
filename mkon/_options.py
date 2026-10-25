"""Опции `dumps` — Python-аналог `mkon-js/src/options.js`.

Значения проверяются строго: неверное значение — `ValueError`/`TypeError`
(JS в таких случаях молча берёт значение по умолчанию).
"""

from __future__ import annotations

from dataclasses import dataclass

_QUOTES = ("min", "keys", "strings", "all")
_MULTILINE = {"std": 1, "no-tabs": 2, "off": 0}


@dataclass(frozen=True)
class StringifyOptions:
    eol: str
    indent: str
    keep_comments: bool
    braces_same_line: bool
    emit_root_braces: bool
    quote_keys: bool
    quote_strings: bool
    quote_char: str
    condense: int
    multiline: int  # 0 = off, 1 = std, 2 = no-tabs
    separator: str  # '' | ','
    sort_props: bool
    colors: bool


def make_options(
    *,
    indent=2,
    sort_keys=False,
    quotes="min",
    quote_char='"',
    separator=False,
    braces_same_line=False,
    root_braces=True,
    condense=0,
    multiline="std",
    eol="\n",
    keep_comments=True,
    colors=False,
) -> StringifyOptions:
    if isinstance(indent, bool) or not isinstance(indent, (int, str)):
        raise TypeError("indent must be int or str, not " + type(indent).__name__)
    if isinstance(indent, int):
        if indent < 0:
            raise ValueError("indent must be >= 0")
        indent = " " * indent
    if quotes not in _QUOTES:
        raise ValueError(f"quotes must be one of {_QUOTES}, not {quotes!r}")
    if quote_char not in ('"', "'"):
        raise ValueError(f"quote_char must be '\"' or \"'\", not {quote_char!r}")
    if multiline not in _MULTILINE:
        raise ValueError(f"multiline must be one of {tuple(_MULTILINE)}, not {multiline!r}")
    if eol not in ("\n", "\r\n"):
        raise ValueError(f"eol must be '\\n' or '\\r\\n', not {eol!r}")
    if isinstance(condense, bool) or not isinstance(condense, int) or condense < 0:
        raise ValueError(f"condense must be an int >= 0, not {condense!r}")

    separator = bool(separator)
    quote_strings = quotes in ("all", "strings") or separator
    ml = _MULTILINE[multiline]
    if quote_strings:
        ml = 0
    return StringifyOptions(
        eol=eol,
        indent=indent,
        keep_comments=bool(keep_comments),
        braces_same_line=bool(braces_same_line),
        emit_root_braces=bool(root_braces),
        quote_keys=quotes in ("all", "keys"),
        quote_strings=quote_strings,
        quote_char=quote_char,
        condense=condense,
        multiline=ml,
        separator="," if separator else "",
        sort_props=bool(sort_keys),
        colors=bool(colors),
    )


#: JS-имя опции → имя аргумента `dumps` (для общих тестов и переноса настроек из JS)
JS_OPTION_NAMES = {
    "space": "indent",
    "sortProps": "sort_keys",
    "quotes": "quotes",
    "quoteChar": "quote_char",
    "separator": "separator",
    "bracesSameLine": "braces_same_line",
    "emitRootBraces": "root_braces",
    "condense": "condense",
    "multiline": "multiline",
    "eol": "eol",
    "keepWsc": "keep_comments",
    "colors": "colors",
}


def from_js_options(options: dict) -> dict:
    """Опции в формате mkon-js → аргументы `dumps` (неизвестные ключи игнорируются, как в JS)."""
    return {JS_OPTION_NAMES[k]: v for k, v in (options or {}).items() if k in JS_OPTION_NAMES}
