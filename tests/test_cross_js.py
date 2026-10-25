"""Кросс-проверка Python ↔ JS: цикл JS → mkon → Python → mkon → JS не должен ничего ломать.

Нужны `node` и исходники mkon-js (путь — $MKON_JS или соседний каталог), иначе тесты пропускаются.

Что проверяется:
1. Корпус текстов (все фикстуры и кейсы): parse даёт одинаковые данные (или обе реализации
   падают), stringify(parse(text)) даёт одинаковый текст — при разных опциях и в режиме rt.
2. Случайные данные (seed фиксирован): Python и JS пишут их одинаково, и каждая реализация
   читает текст другой в те же данные.
"""

import math
import os
import random
import unittest
from datetime import datetime, timedelta, timezone

import mkon
from mkon._options import from_js_options

from _support import ASSETS, encode, decode, js_available, load_cases, run_js, same

OPTION_SETS = [
    {},
    {"quotes": "all"},
    {"quotes": "keys"},
    {"separator": True},
    {"condense": 60},
    {"bracesSameLine": True},
    {"emitRootBraces": False},
    {"multiline": "off"},
    {"multiline": "no-tabs"},
    {"sortProps": True},
    {"space": "\t"},
    {"quoteChar": "'"},
]

SEED = int(os.environ.get("MKON_CROSS_SEED", "20261025"))
COUNT = int(os.environ.get("MKON_CROSS_COUNT", "300"))


def corpus():
    texts = []
    for f in (ASSETS / "testlist.txt").read_text(encoding="utf-8").split("\n"):
        if "_test." in f:
            texts.append((f, (ASSETS / f).read_text(encoding="utf-8").replace("\r", "")))
    for c in load_cases():
        if "mkon" in c and not str(c.get("py", "")).startswith("skip"):
            texts.append(("case: " + c["name"], c["mkon"]))
    return texts


# ------------------------------------------------------------------ random values

_PIECES = [
    "a", "b", "z", "Hello", "world", " ", "  ", ",", ", ", "}", "]", "{", "[", ":", "#", "//", "/*", "*/",
    "'", '"', "'''", "\\", "\\n", "\n", "\t", "\r", "x y", "é", "⊄", "😀", "\u200b", "\ufeff", "\u2028",
    "true", "null", "NaN", "Infinity", "-0", "1", "0.5", "1e5", "2024-01-15", "T10:30Z", "\\frac{a,b}{c}",
]


def rand_str(r):
    return "".join(r.choice(_PIECES) for _ in range(r.randint(0, 4)))


def rand_key(r):
    return r.choice([rand_str(r), str(r.randint(0, 20)), "0" + str(r.randint(0, 9)), "k" + str(r.randint(0, 9))])


_FLOATS = [0.0, -0.0, 0.1, 1.5, -2.5e-7, 1e21, 1e-7, 123.456, 5e-324, 1.7976931348623157e308,
           math.nan, math.inf, -math.inf, 100.0, 1e16, 0.000001]


def rand_scalar(r):
    k = r.randint(0, 9)
    if k == 0:
        return None
    if k == 1:
        return r.choice([True, False])
    if k == 2:
        return r.randint(-(2 ** 53), 2 ** 53)
    if k == 3:
        return r.choice(_FLOATS)
    if k == 4:
        return r.uniform(-1e6, 1e6) * 10 ** r.randint(-30, 30)
    if k == 5:
        base = datetime(1, 1, 1, tzinfo=timezone.utc)
        ms = r.randint(0, int((datetime(9999, 12, 31, tzinfo=timezone.utc) - base).total_seconds() * 1000))
        dt = base + timedelta(milliseconds=ms)
        return dt.replace(hour=0, minute=0, second=0, microsecond=0) if r.random() < 0.3 else dt
    return rand_str(r)


def rand_value(r, depth=0):
    k = r.randint(0, 9)
    if depth < 3 and k < 3:
        return [rand_value(r, depth + 1) for _ in range(r.randint(0, 4))]
    if depth < 3 and k < 6:
        return {rand_key(r): rand_value(r, depth + 1) for _ in range(r.randint(0, 4))}
    return rand_scalar(r)


# ------------------------------------------------------------------ tests

def _py(fn):
    try:
        return True, fn()
    except (ValueError, TypeError) as e:
        return False, str(e)


@unittest.skipUnless(js_available(), "node or mkon-js not found (set MKON_JS)")
class CrossJsTests(unittest.TestCase):
    maxDiff = None

    def test_corpus_parse_and_format(self):
        texts = corpus()
        reqs, plan = [], []
        for name, t in texts:
            for rt in (False, True):
                reqs.append({"op": "parse", "text": t, "rt": rt})
                plan.append((name, "parse", rt, None))
                for o in OPTION_SETS:
                    reqs.append({"op": "format", "text": t, "options": o, "rt": rt})
                    plan.append((name, "format", rt, o))
        res = run_js(reqs)
        text_of = dict(texts)
        problems = []
        for (name, op, rt, o), r in zip(plan, res):
            t = text_of[name]
            ok, py = (_py(lambda: mkon.loads(t, keep_comments=rt)) if op == "parse" else
                      _py(lambda: mkon.dumps(mkon.loads(t, keep_comments=rt), **from_js_options(o))))
            where = f"{name} [{op}{' rt' if rt else ''} {o or ''}]"
            if ok != r["ok"]:
                problems.append(f"{where}: python {'ok' if ok else 'error: ' + py} / js {'ok' if r['ok'] else 'error: ' + r['error']}")
            elif ok and op == "parse":
                d = same(py, decode(r["result"]))
                if d:
                    problems.append(f"{where}: data differs: {d}")
            elif ok and py != r["result"]:
                problems.append(f"{where}: text differs:\n  py: {py!r}\n  js: {r['result']!r}")
        self.assertEqual(problems, [], "\n".join(problems[:20]))

    def test_random_values_cycle(self):
        r = random.Random(SEED)
        values = [rand_value(r) for _ in range(COUNT)]
        reqs, plan = [], []
        for i, v in enumerate(values):
            o = OPTION_SETS[i % len(OPTION_SETS)]
            ok, t_py = _py(lambda: mkon.dumps(v, **from_js_options(o)))
            self.assertTrue(ok, f"python stringify failed for {v!r}: {t_py}")
            reqs.append({"op": "stringify", "value": encode(v), "options": o})
            reqs.append({"op": "parse", "text": t_py})
            plan.append((v, o, t_py))
        res = run_js(reqs)
        problems = []
        for k, (v, o, t_py) in enumerate(plan):
            js_text, js_parsed = res[2 * k], res[2 * k + 1]
            where = f"value #{k} {o or ''}: {v!r}"[:300]
            if not js_text["ok"] or not js_parsed["ok"]:
                problems.append(f"{where}: js error {js_text.get('error') or js_parsed.get('error')}")
                continue
            # 1. одинаковый текст
            if js_text["result"] != t_py:
                problems.append(f"{where}: text differs:\n  py: {t_py!r}\n  js: {js_text['result']!r}")
            # 2. JS читает текст Python в те же данные
            d = same(decode(js_parsed["result"]), v)
            if d:
                problems.append(f"{where}: js.parse(py text) differs: {d}")
            # 3. Python читает текст JS в те же данные
            ok, back = _py(lambda: mkon.loads(js_text["result"]))
            if not ok:
                problems.append(f"{where}: py.parse(js text) failed: {back}")
            else:
                d = same(back, v)
                if d:
                    problems.append(f"{where}: py.parse(js text) differs: {d}")
        self.assertEqual(problems, [], f"{len(problems)} problems:\n" + "\n".join(problems[:15]))

    def test_random_texts(self):
        """Случайный «суп» из токенов (в основном невалидный): одинаковые ошибки и данные."""
        r = random.Random(SEED)
        texts = ["".join(r.choice(_TOKENS) for _ in range(r.randint(0, 25))) for _ in range(COUNT * 10)]
        reqs = [{"op": "parse", "text": t, "rt": i % 3 == 0} for i, t in enumerate(texts)]
        reqs += [{"op": "format", "text": t, "rt": i % 3 == 0} for i, t in enumerate(texts)]
        res = run_js(reqs)
        problems = []
        for i, t in enumerate(texts):
            rt = i % 3 == 0
            js_p, js_f = res[i], res[len(texts) + i]
            try:
                v, ok = mkon.loads(t, keep_comments=rt), True
            except ValueError as e:
                ok, msg = False, getattr(e, "msg", str(e))
            if ok != js_p["ok"]:
                problems.append(f"{t!r}: python {'ok' if ok else msg} / js {js_p}")
            elif not ok:
                if not js_p["error"].startswith(msg):
                    problems.append(f"{t!r}: error differs: {msg!r} / {js_p['error']!r}")
            elif same(v, decode(js_p["result"])):
                problems.append(f"{t!r}: data differs: {same(v, decode(js_p['result']))}")
            else:
                ok2, out = _py(lambda: mkon.dumps(v))
                if ok2 != js_f["ok"] or (ok2 and out != js_f["result"]):
                    problems.append(f"{t!r}: format differs: {out!r} / {js_f}")
        self.assertEqual(problems, [], f"{len(problems)} problems:\n" + "\n".join(problems[:15]))


_TOKENS = ["{", "}", "[", "]", ",", ":", '"', "'", "'''", "/", "//", "/*", "*/", "\n", "\r\n", " ", "  ", "\t",
           "a", "key", "1", "-0", "1.5e3", "#", "\\", "\\n", "\\u0041", "x y", "true", "null", "NaN", "-Infinity",
           "2024-01-15", "T10:30Z", "\xa0", "😀", "\x00", "\ufeff"]


if __name__ == "__main__":
    unittest.main()
