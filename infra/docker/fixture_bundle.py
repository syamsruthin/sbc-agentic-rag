"""Write the two-plan synthetic test bundle to ``<dir>/facts.sqlite``, to build the image without SBC data.

uv run python infra/docker/fixture_bundle.py bundles/fixture
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tests.unit.test_bundle import make_bundle  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: fixture_bundle.py <output dir>")
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy(make_bundle(Path(tmp)), out / "facts.sqlite")
    print(out / "facts.sqlite")


if __name__ == "__main__":
    main()
