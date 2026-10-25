r"""Командная строка: проверка и форматирование mkon (по образцу `python -m json.tool`).

    $ python -m mkon config.mkon              # проверить и вывести отформатированным
    $ python -m mkon config.mkon -i           # переформатировать файл на месте
    $ python -m mkon --check *.mkon           # только проверить (код возврата 1 при ошибке)
    $ python -m mkon --to-json config.mkon    # mkon → JSON
    $ python -m mkon --from-json data.json    # JSON → mkon
    $ echo '{a: 1, b: [x, y]}' | python -m mkon

Комментарии при форматировании сохраняются (кроме --to-json).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime
from decimal import Decimal

from . import MkonDecodeError, dumps, loads


def _to_json_default(o):
    if isinstance(o, (datetime, date)):
        return dumps(o).strip()  # тот же текст, что в mkon: 2024-01-15 / 2024-01-15T10:30:00Z
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def _has_non_finite(v):
    if isinstance(v, float):
        return not math.isfinite(v)
    if isinstance(v, dict):
        return any(_has_non_finite(x) for x in v.values())
    if isinstance(v, (list, tuple)):
        return any(_has_non_finite(x) for x in v)
    return False


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m mkon", description="Validate and pretty-print mkon.")
    p.add_argument("files", nargs="*", help="files to read (default: stdin)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="only validate, print nothing on success")
    g.add_argument("-i", "--in-place", action="store_true", help="rewrite files in place")
    g.add_argument("--to-json", action="store_true", help="convert mkon to JSON")
    p.add_argument("--from-json", action="store_true", help="input is JSON, output is mkon")
    p.add_argument("--indent", default="2", help="number of spaces or 'tab' (default: 2)")
    p.add_argument("--sort-keys", action="store_true", help="sort object keys")
    p.add_argument("--quotes", choices=("min", "keys", "strings", "all"), default="min")
    p.add_argument("--no-root-braces", action="store_true", help="omit braces around the root object")
    p.add_argument("--condense", type=int, default=0, help="fit containers shorter than N on one line")
    args = p.parse_args(argv)

    indent = "\t" if args.indent == "tab" else int(args.indent)
    sources = args.files or ["-"]
    status = 0
    for name in sources:
        try:
            if name == "-":
                text = sys.stdin.read()
            else:
                with open(name, encoding="utf-8-sig") as f:
                    text = f.read()
            data = json.loads(text) if args.from_json else loads(text, keep_comments=not args.to_json)

            if args.check:
                continue
            if args.to_json:
                if _has_non_finite(data):
                    raise ValueError("NaN / Infinity cannot be represented in JSON")
                out = json.dumps(data, ensure_ascii=False, indent=indent, sort_keys=args.sort_keys,
                                 default=_to_json_default, allow_nan=False) + "\n"
            else:
                out = dumps(data, indent=indent, sort_keys=args.sort_keys, quotes=args.quotes,
                            root_braces=not args.no_root_braces, condense=args.condense)
            if args.in_place and name != "-":
                with open(name, "w", encoding="utf-8", newline="") as f:
                    f.write(out)
            else:
                sys.stdout.write(out)
        except (MkonDecodeError, ValueError, TypeError, OSError) as e:
            status = 1
            where = name if name != "-" else "<stdin>"
            print(f"{where}: {e}", file=sys.stderr)
            hint = getattr(e, "hint", "")
            if hint:
                print(hint, file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.exit(main())
