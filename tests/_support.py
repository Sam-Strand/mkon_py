"""Общие помощники тестов: tagged JSON, строгое сравнение, вызов JS-моста."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
#: локальная копия общего набора (создаётся scripts/sync_conformance.py — для CI без mkon-js)
CONFORMANCE = HERE / "conformance"


# --------------------------------------------------------------------------- tagged JSON

def encode(v):
    """Python-значение → tagged JSON (как `encode` в js_bridge.mjs)."""
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if math.isnan(v):
            return {"$number": "NaN"}
        if v == math.inf:
            return {"$number": "Infinity"}
        if v == -math.inf:
            return {"$number": "-Infinity"}
        if v == 0 and math.copysign(1.0, v) < 0:
            return {"$number": "-0"}
        return v
    if isinstance(v, datetime):
        u = v.astimezone(timezone.utc)
        return {"$date": f"{u.year:04d}-{u.month:02d}-{u.day:02d}T{u.hour:02d}:{u.minute:02d}:{u.second:02d}"
                         f".{u.microsecond // 1000:03d}Z"}
    if isinstance(v, date):
        return {"$date": f"{v.year:04d}-{v.month:02d}-{v.day:02d}T00:00:00.000Z"}
    if isinstance(v, (list, tuple)):
        return [encode(x) for x in v]
    if isinstance(v, dict):
        return {k: encode(x) for k, x in v.items()}
    raise TypeError(type(v))


def decode(v):
    """tagged JSON → Python-значение."""
    if isinstance(v, list):
        return [decode(x) for x in v]
    if isinstance(v, dict):
        if list(v) == ["$number"]:
            return {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf, "-0": -0.0}[v["$number"]]
        if list(v) == ["$date"]:
            return datetime.fromisoformat(v["$date"].replace("Z", "+00:00"))
        return {k: decode(x) for k, x in v.items()}
    return v


# --------------------------------------------------------------------------- comparison

def same(a, b, path="$", json_zero=False):
    """Строгое сравнение данных. Возвращает None или строку с описанием различия.

    * bool не равен числу (в Python True == 1);
    * int и float равны, если равны значения (в JS это один тип);
    * NaN == NaN, а 0 и -0 различаются (кроме `json_zero=True`: JSON не умеет -0);
    * datetime сравнивается как момент времени.
    """
    if isinstance(a, bool) or isinstance(b, bool):
        return None if (type(a) is type(b) and a == b) else f"{path}: {a!r} != {b!r}"
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if isinstance(a, float) and math.isnan(a) or isinstance(b, float) and math.isnan(b):
            ok = isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b)
            return None if ok else f"{path}: {a!r} != {b!r}"
        if a != b:
            return f"{path}: {a!r} != {b!r}"
        if a == 0 and not json_zero and math.copysign(1.0, a) != math.copysign(1.0, b):
            return f"{path}: {a!r} != {b!r} (sign of zero)"
        return None
    if isinstance(a, datetime) and isinstance(b, datetime):
        return None if a == b else f"{path}: {a.isoformat()} != {b.isoformat()}"
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return f"{path}: keys {sorted(a)} != {sorted(b)}"
        for k in a:
            d = same(a[k], b[k], f"{path}.{k}", json_zero)
            if d:
                return d
        return None
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            return f"{path}: length {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = same(x, y, f"{path}[{i}]", json_zero)
            if d:
                return d
        return None
    if type(a) is not type(b) and not (isinstance(a, str) and isinstance(b, str)):
        return f"{path}: type {type(a).__name__} != {type(b).__name__} ({a!r} vs {b!r})"
    return None if a == b else f"{path}: {a!r} != {b!r}"


# --------------------------------------------------------------------------- JS bridge

def find_mkon_js():
    """Путь к mkon-js: $MKON_JS, иначе соседние каталоги."""
    candidates = []
    if os.environ.get("MKON_JS"):
        candidates.append(Path(os.environ["MKON_JS"]))
    repo = HERE.parent
    candidates += [
        repo.parent / "mkon-js",
        repo.parent.parent / "graph-flow" / "frontend" / "git" / "mkon-js",
    ]
    for c in candidates:
        if (c / "src" / "index.js").is_file():
            return c.resolve()
    return None


def _suite_paths():
    """Общий набор тестов: из mkon-js (эталон), если он рядом, иначе локальная копия."""
    js = find_mkon_js()
    if js and (js / "test" / "conformance" / "cases.json").is_file():
        return js / "test" / "assets", js / "test" / "conformance" / "cases.json"
    if (CONFORMANCE / "cases.json").is_file():
        return CONFORMANCE / "assets", CONFORMANCE / "cases.json"
    raise RuntimeError(
        "shared conformance suite not found: put mkon-js next to mkon_py, set MKON_JS, "
        "or run scripts/sync_conformance.py"
    )


ASSETS, CASES = None, None


def js_available():
    return shutil.which("node") is not None and find_mkon_js() is not None


def run_js(requests):
    """Выполняет пачку операций в mkon-js. См. описание формата в js_bridge.mjs."""
    root = find_mkon_js()
    proc = subprocess.run(
        ["node", str(HERE / "js_bridge.mjs"), str(root)],
        input=json.dumps(requests, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("js_bridge failed:\n" + proc.stderr.decode("utf-8", "replace"))
    # JSON-числа из JS — double: большие целые читаем как float, как это сделал бы mkon
    return json.loads(proc.stdout.decode("utf-8"),
                      parse_int=lambda s: int(s) if abs(int(s)) <= 2 ** 53 - 1 else float(s))


def load_cases():
    require_suite()
    with open(CASES, encoding="utf-8") as f:
        return json.load(f)


try:
    ASSETS, CASES = _suite_paths()
except RuntimeError as _e:  # sync_conformance.py импортирует модуль до того, как набор скопирован
    ASSETS = CASES = None
    _SUITE_ERROR = str(_e)


def require_suite():
    if ASSETS is None:
        raise RuntimeError(_SUITE_ERROR)
