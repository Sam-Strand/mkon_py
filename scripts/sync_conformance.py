"""Копирует общий набор тестов из mkon-js (эталон) в tests/conformance/.

    python scripts/sync_conformance.py [/path/to/mkon-js]

Без аргумента берёт $MKON_JS или ищет mkon-js рядом (как тесты).
"""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from _support import CONFORMANCE, find_mkon_js  # noqa: E402


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else find_mkon_js()
    if not src or not (src / "test" / "assets").is_dir():
        sys.exit("mkon-js not found; pass its path or set MKON_JS")
    dst_assets = CONFORMANCE / "assets"
    if dst_assets.exists():
        shutil.rmtree(dst_assets)
    shutil.copytree(src / "test" / "assets", dst_assets)
    shutil.copy2(src / "test" / "conformance" / "cases.json", CONFORMANCE / "cases.json")
    print(f"synced from {src}")


if __name__ == "__main__":
    main()
