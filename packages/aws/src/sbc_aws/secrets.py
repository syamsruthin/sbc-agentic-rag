"""Secrets from SSM Parameter Store, copied into the process environment at cold start.

The API calls :func:`load_ssm_parameters` once, before ``EnvSettings()`` is read, so ``sbc_core.config`` keeps
reading plain environment variables and stays cloud-neutral. Parameter names map to variables by their last
path segment: ``/sbc/dev/OPENROUTER_API_KEY_BROKER`` sets ``OPENROUTER_API_KEY_BROKER``.

Values are never logged or returned; only the variable names are.
"""

from __future__ import annotations

import os
import re
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mypy_boto3_ssm.client import SSMClient

_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")


@dataclass(frozen=True)
class LoadedSecrets:
    names: tuple[str, ...] = ()
    """Variables set from Parameter Store."""
    kept: tuple[str, ...] = field(default=())
    """Variables already set in the environment and left alone (``overwrite=False``)."""
    skipped: tuple[str, ...] = field(default=())
    """Parameters whose last path segment isn't a valid variable name."""


def load_ssm_parameters(
    prefix: str,
    client: SSMClient,
    *,
    environ: MutableMapping[str, str] | None = None,
    overwrite: bool = False,
) -> LoadedSecrets:
    """Copy every parameter under ``prefix`` (recursive, decrypted) into ``environ`` (default os.environ)."""
    if not prefix.startswith("/"):
        raise ValueError("the SSM prefix must be an absolute path, e.g. /sbc/dev/")
    target = os.environ if environ is None else environ
    names: list[str] = []
    kept: list[str] = []
    skipped: list[str] = []
    paginator = client.get_paginator("get_parameters_by_path")
    for page in paginator.paginate(Path=prefix.rstrip("/") or "/", Recursive=True, WithDecryption=True):
        for param in page["Parameters"]:
            full_name, value = param.get("Name", ""), param.get("Value")
            name = full_name.rsplit("/", 1)[-1]
            if not _ENV_NAME.match(name) or value is None:
                skipped.append(full_name)
            elif name in target and not overwrite:
                kept.append(name)
            else:
                target[name] = value
                names.append(name)
    return LoadedSecrets(tuple(sorted(names)), tuple(sorted(kept)), tuple(sorted(skipped)))
