from __future__ import annotations

import os
from pathlib import Path

import pytest

from sbc_core.config import EnvSettings

REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_SETTINGS_NAMES = {name.upper() for name in EnvSettings.model_fields}


@pytest.fixture
def config_dir() -> Path:
    return REPO_ROOT / "config"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep variables from a developer shell or CI runner out of the tests."""
    for name in list(os.environ):
        if name.startswith("SBC_") or name in _ENV_SETTINGS_NAMES:
            monkeypatch.delenv(name)
