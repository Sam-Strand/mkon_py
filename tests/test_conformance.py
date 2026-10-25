"""Общие кейсы из cases.json (тот же файл прогоняет mkon-js/test/conformance.test.js).

Кейс с полем "py": "skip: причина" — известное расхождение реализаций, см. README.
"""

import unittest

import mkon
from mkon._options import from_js_options

from _support import decode, load_cases, same


class ConformanceTests(unittest.TestCase):
    pass


def _make(case):
    def test(self):
        if str(case.get("py", "")).startswith("skip"):
            self.skipTest(case["py"])
        rt = bool(case.get("rt"))
        opts = dict(case.get("options") or {})

        def parse(text):
            return mkon.loads(text, keep_comments=rt)

        def stringify(value, js_opts):
            return mkon.dumps(value, **from_js_options(js_opts))

        if case.get("error"):
            with self.assertRaises((ValueError, TypeError)) as cm:
                if "mkon" in case:
                    parse(case["mkon"])
                else:
                    stringify(decode(case["input"]), opts)
            self.assertIn(case["message"], str(cm.exception))
            return

        expected = decode(case["value"])
        if "mkon" in case:
            v = parse(case["mkon"])
            self.assertIsNone(same(v, expected), "parse")
            self.assertEqual(stringify(v, opts), case["text"])
        else:
            self.assertEqual(stringify(decode(case["input"]), opts), case["text"])
        if not opts.get("colors"):  # ANSI-раскраска не предназначена для обратного чтения
            self.assertIsNone(same(parse(case["text"]), expected), "output must parse back to the same value")

    return test


for _i, _case in enumerate(load_cases()):
    _name = "test_%02d_" % _i + "".join(ch if ch.isalnum() else "_" for ch in _case["name"])[:60]
    setattr(ConformanceTests, _name, _make(_case))


if __name__ == "__main__":
    unittest.main()
