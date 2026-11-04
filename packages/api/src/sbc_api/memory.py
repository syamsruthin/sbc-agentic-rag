"""In-process implementations of the ports, for ``APP_ENV=local`` without DynamoDB, and for tests.

They follow the DynamoDB adapters' behavior (limits, TTLs, append-only audit) closely enough that route
tests mean the same thing on both; the adapters themselves are tested against moto and DynamoDB Local.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sbc_aws.audit import AuditConflictError, AuditRecord
from sbc_aws.cache import ANSWER_TTL_SECONDS
from sbc_aws.ddb import Clock
from sbc_aws.pdfcache import PdfHashMismatchError
from sbc_aws.ratelimit import Decision, Window
from sbc_aws.sessions import FEEDBACK_REASONS, TITLE_MAX, ThreadSummary


class MemoryRateLimiter:
    def __init__(self, clock: Clock = time.time) -> None:
        self._counts: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def hit(self, key: str, windows: Sequence[Window]) -> Decision:
        now = self._clock()
        remaining: int | None = None
        with self._lock:
            for w in sorted(windows, key=lambda w: w.seconds):
                bucket = int(now // w.seconds)
                slot = (key, f"{w.name}#{bucket}")
                if self._counts.get(slot, 0) >= w.limit:
                    return Decision(
                        False, window=w.name, retry_after_s=max(1, int((bucket + 1) * w.seconds - now))
                    )
                self._counts[slot] = self._counts.get(slot, 0) + 1
                left = w.limit - self._counts[slot]
                remaining = left if remaining is None else min(remaining, left)
        return Decision(True, remaining=remaining)


class MemoryDailyCap:
    def __init__(self, clock: Clock = time.time) -> None:
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def _day(self) -> str:
        return time.strftime("%Y%m%d", time.gmtime(self._clock()))

    def consume(self, cap: int) -> bool:
        with self._lock:
            day = self._day()
            if self._counts.get(day, 0) >= cap:
                return False
            self._counts[day] = self._counts.get(day, 0) + 1
            return True

    def used_today(self) -> int:
        return self._counts.get(self._day(), 0)


class MemoryAnswerCache:
    def __init__(self, clock: Clock = time.time) -> None:
        self._items: dict[str, tuple[float, dict[str, Any]]] = {}
        self._clock = clock

    def get(self, key: str) -> dict[str, Any] | None:
        found = self._items.get(key)
        if found is None or found[0] <= self._clock():
            return None
        return dict(found[1])

    def put(self, key: str, reply: Mapping[str, Any], ttl_seconds: int = ANSWER_TTL_SECONDS) -> bool:
        self._items[key] = (self._clock() + ttl_seconds, dict(reply))
        return True


class MemoryAuditSink:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def write(self, record: AuditRecord) -> None:
        if any(r.request_id == record.request_id and r.action == record.action for r in self.records):
            raise AuditConflictError(f"audit record {record.request_id} already exists")
        self.records.append(record)


class MemoryKillSwitches:
    def __init__(self) -> None:
        self._disabled: set[str] = set()
        self._quarantined: set[str] = set()

    def llm_enabled(self, bot: str) -> bool:
        return bot not in self._disabled

    def quarantined(self) -> frozenset[str]:
        return frozenset(self._quarantined)

    def set_llm_enabled(self, bot: str, enabled: bool, *, actor_hash: str) -> None:
        (self._disabled.discard if enabled else self._disabled.add)(bot)

    def quarantine(self, plan_keys: Iterable[str], *, actor_hash: str, reason: str) -> None:
        self._quarantined.update(plan_keys)

    def release(self, plan_keys: Iterable[str]) -> None:
        self._quarantined.difference_update(plan_keys)


class MemorySessionStore:
    def __init__(self, clock: Clock = time.time) -> None:
        self._threads: dict[tuple[str, str], tuple[ThreadSummary, float]] = {}
        self.feedback: list[dict[str, Any]] = []
        self._clock = clock

    def touch_thread(self, owner: str, thread_id: str, *, bot: str, title: str, ttl_seconds: int) -> None:
        now = self._clock()
        old = self._threads.get((owner, thread_id))
        kept = old[0].title if old else title[:TITLE_MAX]
        self._threads[(owner, thread_id)] = (ThreadSummary(thread_id, bot, kept, int(now)), now + ttl_seconds)

    def owns(self, owner: str, thread_id: str) -> bool:
        found = self._threads.get((owner, thread_id))
        return found is not None and found[1] > self._clock()

    def list_threads(self, owner: str, *, limit: int = 20) -> list[ThreadSummary]:
        now = self._clock()
        mine = [t for (o, _), (t, exp) in self._threads.items() if o == owner and exp > now]
        return sorted(mine, key=lambda t: t.updated_at, reverse=True)[:limit]

    def add_feedback(
        self, thread_id: str, request_id: str, *, rating: str, reasons: tuple[str, ...] = (), ttl_seconds: int
    ) -> None:
        if rating not in ("up", "down"):
            raise ValueError("rating must be 'up' or 'down'")
        if set(reasons) - FEEDBACK_REASONS:
            raise ValueError(f"unknown feedback reasons: {sorted(set(reasons) - FEEDBACK_REASONS)}")
        self.feedback.append({"thread_id": thread_id, "request_id": request_id, "rating": rating,
                              "reasons": reasons})  # fmt: skip


class MemoryPdfCache:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], bytes] = {}

    def get(self, plan_key: str, sha256: str) -> bytes | None:
        return self._items.get((plan_key, sha256))

    def put(self, plan_key: str, sha256: str, data: bytes) -> None:
        if hashlib.sha256(data).hexdigest() != sha256:
            raise PdfHashMismatchError(f"PDF for {plan_key} doesn't match the bundle's sha256")
        self._items[(plan_key, sha256)] = data
