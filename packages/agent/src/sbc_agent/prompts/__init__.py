"""Versioned prompts (PLAN.md section 10): plain Markdown files, so every prompt change goes through review
and the eval gates. A prompt's version is the hash of its text, recorded on every trace."""

from __future__ import annotations

import hashlib
from functools import cache
from importlib.resources import files


@cache
def load(name: str) -> tuple[str, str]:
    """``(text, version)`` of ``prompts/<name>.md``."""
    text = files("sbc_agent.prompts").joinpath(f"{name}.md").read_text(encoding="utf-8")
    return text, hashlib.sha256(text.encode()).hexdigest()[:12]
