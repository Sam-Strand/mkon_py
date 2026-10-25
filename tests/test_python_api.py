"""Python API (в стиле модуля json) и то, чего нет в JS."""

import io
import json
import math
import subprocess
import sys
import tempfile
import unittest
from collections import OrderedDict, namedtuple
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import mkon

ROOT = Path(__file__).resolve().parent.parent


class LoadsTests(unittest.TestCase):
    def test_types(self):
        v = mkon.loads("{ i: 1, f: 1.5, e: 1e3, big: 9007199254740993, z: -0, n: NaN, d: 2024-01-15, b: true, x: null }")
        self.assertIs(type(v["i"]), int)
        self.assertIs(type(v["f"]), float)
        self.assertIs(type(v["e"]), float)
        self.assertIs(type(v["big"]), float)  # за пределами ±2**53 — double, как в JS
        self.assertEqual(math.copysign(1, v["z"]), -1)
        self.assertTrue(math.isnan(v["n"]))
        self.assertEqual(v["d"], datetime(2024, 1, 15, tzinfo=timezone.utc))
        self.assertIs(v["b"], True)
        self.assertIsNone(v["x"])

    def test_bytes_and_bom(self):
        self.assertEqual(mkon.loads(b"{a: 1}"), {"a": 1})
        self.assertEqual(mkon.loads(bytearray("\ufeff{a: é}".encode("utf-8"))), {"a": "é"})
        with self.assertRaises(TypeError):
            mkon.loads(123)

    def test_load_from_file(self):
        self.assertEqual(mkon.load(io.StringIO("a: [1, 2]")), {"a": [1, 2]})

    def test_parse_int_keeps_big_integers(self):
        v = mkon.loads("[9007199254740993, 1, -0, 1.5]", parse_int=int)
        self.assertEqual(v[0], 9007199254740993)
        self.assertIs(type(v[0]), int)
        self.assertEqual(v[1:3], [1, 0])
        self.assertEqual(v[3], 1.5)

    def test_parse_float_decimal(self):
        v = mkon.loads("{ price: 19.90, rate: 1e-3, n: 2 }", parse_float=Decimal)
        self.assertEqual(v, {"price": Decimal("19.90"), "rate": Decimal("1e-3"), "n": 2})

    def test_parse_constant(self):
        seen = []
        v = mkon.loads("[NaN, Infinity, -Infinity, +Infinity]", parse_constant=lambda s: seen.append(s) or s)
        self.assertEqual(v, ["NaN", "Infinity", "-Infinity", "+Infinity"])
        self.assertEqual(seen, v)

    def test_object_hooks(self):
        self.assertEqual(mkon.loads("{a: 1, b: {c: 2}}", object_hook=lambda d: sorted(d)), ["a", "b"])
        pairs = mkon.loads("{a: 1, a: 2, b: 3}", object_pairs_hook=list)
        self.assertEqual(pairs, [("a", 1), ("a", 2), ("b", 3)])  # как в json: все пары, включая повторы
        self.assertIsInstance(mkon.loads("{b: 1, a: 2}", object_pairs_hook=OrderedDict), OrderedDict)

    def test_year_zero_is_not_supported(self):
        with self.assertRaises(mkon.MkonDecodeError):
            mkon.loads("0000-01-01")


class DecodeErrorTests(unittest.TestCase):
    def test_like_json_decode_error(self):
        doc = "{\n  a: 1\n  b c\n  d: 2\n}"
        with self.assertRaises(mkon.MkonDecodeError) as cm:
            mkon.loads(doc)
        e = cm.exception
        self.assertIsInstance(e, ValueError)
        self.assertEqual(e.msg, "Found a line break while looking for ':' after the key name (check your syntax)")
        self.assertEqual(e.doc, doc)
        self.assertEqual((e.lineno, e.colno), (3, 6))
        self.assertEqual(doc[e.pos], "\n")
        self.assertIn("line 3 column 6", str(e))

    def test_position_counts_python_characters(self):
        doc = "{ k😀😀: [1, 2 }"
        with self.assertRaises(mkon.MkonDecodeError) as cm:
            mkon.loads(doc)
        self.assertEqual(doc[cm.exception.pos], "}")

    def test_hint(self):
        with self.assertRaises(mkon.MkonDecodeError) as cm:
            mkon.loads("{\n  a: x{y, b: 1}\n")  # незакрытая { «съела» конец строки
        self.assertIn("x{y, b: 1}", cm.exception.hint)


class DumpsTests(unittest.TestCase):
    def test_python_types(self):
        self.assertEqual(mkon.dumps((1, 2)), "[\n  1\n  2\n]\n")
        self.assertEqual(mkon.dumps(date(2024, 1, 15)), "2024-01-15\n")
        msk = timezone(timedelta(hours=3))
        self.assertEqual(mkon.dumps(datetime(2024, 1, 15, 13, 30, tzinfo=msk)), "2024-01-15T10:30:00Z\n")
        self.assertEqual(mkon.dumps(datetime(2024, 1, 15, 10, 30, 0, 123999, tzinfo=timezone.utc)),
                         "2024-01-15T10:30:00.123Z\n")  # миллисекунды, как в JS
        self.assertEqual(mkon.dumps(2 ** 60), "1152921504606847000\n")  # как double в JS
        self.assertEqual(mkon.dumps(namedtuple("P", "x y")(1, 2)), "[\n  1\n  2\n]\n")

    def test_decimal(self):
        out = mkon.dumps([Decimal("19.90"), Decimal("1E+3"), Decimal("-0"), Decimal("NaN"), Decimal("-Infinity")])
        self.assertEqual(out, "[\n  19.90\n  1E+3\n  -0\n  NaN\n  -Infinity\n]\n")
        self.assertEqual(mkon.loads(out, parse_float=Decimal)[0], Decimal("19.90"))
        with self.assertRaises(ValueError):
            mkon.dumps(Decimal("sNaN"))

    def test_default(self):
        @dataclass
        class Point:
            x: int
            y: int

        self.assertEqual(mkon.dumps({"p": Point(1, 2)}, default=asdict), "{\n  p:\n  {\n    x: 1\n    y: 2\n  }\n}\n")
        self.assertEqual(mkon.dumps({1, 2} and [Path("a/b")], default=str), "[\n  a/b\n]\n")
        with self.assertRaises(TypeError):
            mkon.dumps({"a": object()})

    def test_circular(self):
        a = []
        a.append(a)
        with self.assertRaisesRegex(ValueError, "Circular reference"):
            mkon.dumps(a)
        d = {}
        d["self"] = d
        with self.assertRaisesRegex(ValueError, "Circular reference"):
            mkon.dumps(d)
        with self.assertRaises(RecursionError):
            mkon.dumps(a, check_circular=False)
        shared = [1]
        self.assertEqual(mkon.loads(mkon.dumps({"a": shared, "b": shared})), {"a": [1], "b": [1]})

    def test_keys_like_json(self):
        self.assertEqual(mkon.loads(mkon.dumps({2: "a", 1.5: "b", False: "c", None: "d"})),
                         {"2": "a", "1.5": "b", "false": "c", "null": "d"})
        with self.assertRaises(TypeError):
            mkon.dumps({(1, 2): "x"})
        self.assertEqual(mkon.dumps({(1, 2): "x", "a": 1}, skipkeys=True), "{\n  a: 1\n}\n")

    def test_options(self):
        v = {"b": {"x": [1, "y z"]}, "a": 1}
        self.assertEqual(mkon.dumps(v, indent=4, sort_keys=True),
                         "{\n    a: 1\n    b:\n    {\n        x:\n        [\n            1\n            y z\n        ]\n    }\n}\n")
        self.assertEqual(mkon.dumps(v, indent="\t", root_braces=False, braces_same_line=True, condense=40),
                         "b: {\n\tx: [1, y z]\n}\na: 1\n")
        self.assertEqual(mkon.dumps({"a": 1}, eol="\r\n"), "{\r\n  a: 1\r\n}\r\n")
        self.assertEqual(mkon.dumps({"a": "x"}, quotes="all", quote_char="'"), "{\n  'a': 'x'\n}\n")

    def test_bad_options_are_errors(self):
        for kw in ({"quotes": "some"}, {"multiline": "on"}, {"quote_char": "`"}, {"eol": "\r"},
                   {"condense": -1}, {"indent": -1}):
            with self.assertRaises(ValueError, msg=kw):
                mkon.dumps({}, **kw)
        with self.assertRaises(TypeError):
            mkon.dumps({}, indent=True)
        with self.assertRaises(TypeError):
            mkon.dumps({}, sort_props=True)  # опечатка/JS-имя — ошибка

    def test_dump_to_file(self):
        buf = io.StringIO()
        mkon.dump({"a": 1}, buf)
        self.assertEqual(buf.getvalue(), "{\n  a: 1\n}\n")

    def test_rejects_naive_datetime_and_huge_ints(self):
        with self.assertRaises(TypeError):
            mkon.dumps(datetime(2024, 1, 15, 10, 30))
        with self.assertRaises(ValueError):
            mkon.dumps(10 ** 400)


class CommentsTests(unittest.TestCase):
    SRC = "// top\n{\n  // about a\n  a: 1 // after\n  b: [1, 2]\n}\n"
    OUT = "// top\n{\n  // about a\n  a: 1 // after\n  b:\n  [\n    1\n    2\n  ]\n}\n"

    def test_roundtrip(self):
        data = mkon.loads(self.SRC, keep_comments=True)
        self.assertIsInstance(data, mkon.MkonDict)
        self.assertIsInstance(data["b"], mkon.MkonList)
        self.assertEqual(data, {"a": 1, "b": [1, 2]})
        c = mkon.get_comments(data)
        self.assertEqual(c["r"][0].strip(), "// top")
        self.assertEqual(c["c"]["a"][0].strip(), "// about a")
        self.assertEqual(mkon.dumps(data), self.OUT)
        self.assertEqual(mkon.dumps(data, keep_comments=False), "{\n  a: 1\n  b:\n  [\n    1\n    2\n  ]\n}\n")

    def test_plain_loads_has_no_comments(self):
        data = mkon.loads(self.SRC)
        self.assertIs(type(data), dict)
        self.assertIsNone(mkon.get_comments(data))

    def test_edit_after_loads(self):
        data = mkon.loads(self.SRC, keep_comments=True)
        del data["a"]
        data["c"] = "new"
        self.assertEqual(mkon.dumps(data), "// top\n{\n  b:\n  [\n    1\n    2\n  ]\n  c: new\n}\n")


class ToolTests(unittest.TestCase):
    def run_tool(self, *args, stdin=""):
        return subprocess.run([sys.executable, "-m", "mkon", *args], input=stdin, capture_output=True,
                              text=True, encoding="utf-8", cwd=ROOT)

    def test_format_stdin_keeps_comments(self):
        r = self.run_tool(stdin="// c\n{a: 1, b: [x, y]}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "// c\n{\n  a: 1\n  b:\n  [\n    x\n    y\n  ]\n}\n")

    def test_check_reports_error(self):
        r = self.run_tool("--check", stdin="{a: 1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("missing '}'", r.stderr)

    def test_json_conversion(self):
        r = self.run_tool("--to-json", stdin="{a: 2024-01-15, b: [1, x]}")
        self.assertEqual(json.loads(r.stdout), {"a": "2024-01-15", "b": [1, "x"]})
        r = self.run_tool("--from-json", "--indent", "tab", stdin='{"a": [1, "x"]}')
        self.assertEqual(r.stdout, "{\n\ta:\n\t[\n\t\t1\n\t\tx\n\t]\n}\n")

    def test_in_place(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "c.mkon"
            f.write_text("// keep\n{a:1}", encoding="utf-8")
            r = self.run_tool("-i", str(f))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(f.read_text(encoding="utf-8"), "// keep\n{\n  a: 1\n}\n")


if __name__ == "__main__":
    unittest.main()
