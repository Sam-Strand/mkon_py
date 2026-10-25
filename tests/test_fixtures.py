"""Фикстуры из mkon-js/test/assets — тот же прогон, что fixtures.test.js."""

import json
import unittest

import mkon
from mkon._options import from_js_options

from _support import ASSETS, require_suite, same

require_suite()


def _load(name):
    return (ASSETS / name).read_text(encoding="utf-8").replace("\r", "")


def _meta(name):
    f = ASSETS / f"{name}_testmeta.mkon"
    if not f.exists():
        return {}
    return mkon.loads(f.read_text(encoding="utf-8")).get("options") or {}


class FixtureTests(unittest.TestCase):
    pass


def _make(file):
    name, ext = file.split("_test.")

    def test(self):
        text = _load(file)
        if name.split("/")[-1].startswith("fail"):
            with self.assertRaises(mkon.MkonDecodeError):
                mkon.loads(text)
            return

        data = mkon.loads(text)
        expected = json.loads(_load(f"{name}_result.json"))
        self.assertIsNone(same(data, expected, json_zero=True), "parse result differs from _result.json")

        out = mkon.dumps(data, **from_js_options(_meta(name)))
        self.assertEqual(out, _load(f"{name}_result.mkon"))

        if ext == "json":
            self.assertIsNone(same(data, json.loads(text)), "JSON input must parse like JSON")

    return test


for _file in filter(None, (ASSETS / "testlist.txt").read_text(encoding="utf-8").split("\n")):
    if "_test." in _file:
        setattr(FixtureTests, "test_" + _file.replace("/", "__").replace(".", "_"), _make(_file))


if __name__ == "__main__":
    unittest.main()
