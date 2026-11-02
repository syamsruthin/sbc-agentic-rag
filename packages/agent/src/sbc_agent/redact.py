"""PII redaction at ingress, before any text reaches a model, a log or a trace (PLAN.md 3.8, L1).

Pattern recognizers for the identifiers people type into a plan chat: SSNs, member IDs, phone numbers, email
addresses and dates (birth dates). Plan identifiers survive on purpose, because plan resolution needs them:
contract codes (4-5 characters), HIOS plan IDs and 12-digit SBC IDs never match these patterns. Names and
addresses need the NER recognizer (Presidio with a small spaCy model), which joins before the public launch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b|\b(?:ssn|social security(?: number)?)\W{0,3}\d{9}\b", re.I)),
    ("DATE", re.compile(r"\b(?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01])[/-](?:19|20)\d{2}\b")),
    ("PHONE", re.compile(r"(?<![\w$])(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b")),
    # Member IDs: letters then 6+ digits ("XYZ123456789"). Contract codes are 4-5 characters, and HIOS IDs
    # start with five digits, so neither matches.
    ("MEMBER_ID", re.compile(r"\b[A-Z]{2,4}\d{6,12}\b")),
)


@dataclass(frozen=True)
class Redaction:
    text: str
    found: tuple[str, ...]
    """The kinds of PII replaced, e.g. ("SSN", "MEMBER_ID"); never the values."""


def redact(text: str) -> Redaction:
    found: list[str] = []
    for kind, pattern in _PATTERNS:
        text, n = pattern.subn(f"[{kind}]", text)
        found += [kind] * n
    return Redaction(text, tuple(found))
