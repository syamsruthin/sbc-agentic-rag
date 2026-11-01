"""Classify the verifier's REVIEW reasons against the allowlist (PLAN.md section 3.2.2).

A plan is indexed only if its verification status is PASS, or REVIEW with every reason in an allowlisted
class. Anything else quarantines it. The allowlist itself needs a human sign-off before a release build.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Literal

import yaml

ReviewClass = Literal["line_break_join", "carried_note", "event_inferred"]

_CARRIED_RE = re.compile(
    r"^benefits\[(?P<service>.+)\]: limitations carried over from the previous page's cell$"
)
_EVENT_RE = re.compile(
    r"^benefits\[(?P<service>.+)\]: event label not printed beside this row; "
    r"MD puts it under '(?P<event>.+)'$"
)


@dataclass(frozen=True)
class ReviewItem:
    cls: ReviewClass | None
    """None when the reason matches no allowlisted class."""
    text: str
    service: str | None = None
    event: str | None = None


@dataclass(frozen=True)
class Allowlist:
    classes: frozenset[str]
    signed_off_by: str | None
    signed_off_on: str | None

    @property
    def signed(self) -> bool:
        return bool(self.signed_off_by and self.signed_off_on)


@cache
def load_allowlist() -> Allowlist:
    raw = yaml.safe_load(
        files("sbc_extract").joinpath("data/review_allowlist.yaml").read_text(encoding="utf-8")
    )
    sign_off = raw.get("sign_off") or {}
    return Allowlist(
        classes=frozenset(c["id"] for c in raw["classes"]),
        signed_off_by=sign_off.get("by"),
        signed_off_on=str(sign_off["on"]) if sign_off.get("on") else None,
    )


def classify(needs_review: str) -> list[ReviewItem]:
    items = []
    for reason in (r.strip() for r in needs_review.split(" | ")):
        if not reason:
            continue
        if reason.startswith("joined across a line break: "):
            items.append(ReviewItem("line_break_join", reason))
        elif m := _CARRIED_RE.match(reason):
            items.append(ReviewItem("carried_note", reason, service=m["service"]))
        elif m := _EVENT_RE.match(reason):
            items.append(ReviewItem("event_inferred", reason, service=m["service"], event=m["event"]))
        else:
            items.append(ReviewItem(None, reason))
    return items
