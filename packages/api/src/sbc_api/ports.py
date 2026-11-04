"""What the API needs from its stores, as protocols (docs/aws/MIGRATION_PLAN.md section 3).

``sbc_aws`` implements them on DynamoDB and S3 and ``sbc_api.memory`` in process (local runs and tests).
Both satisfy these structurally; mypy checks the match where ``sbc_api.deps`` assigns them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Protocol

from sbc_aws.audit import AuditRecord
from sbc_aws.ratelimit import Decision, Window
from sbc_aws.sessions import ThreadSummary


class RateLimiter(Protocol):
    def hit(self, key: str, windows: Sequence[Window]) -> Decision: ...


class DailyCap(Protocol):
    def consume(self, cap: int) -> bool: ...

    def used_today(self) -> int: ...


class AnswerCache(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...

    def put(self, key: str, reply: Mapping[str, Any], ttl_seconds: int = ...) -> bool: ...


class AuditSink(Protocol):
    def write(self, record: AuditRecord) -> None: ...


class KillSwitches(Protocol):
    def llm_enabled(self, bot: str) -> bool: ...

    def quarantined(self) -> frozenset[str]: ...

    def set_llm_enabled(self, bot: str, enabled: bool, *, actor_hash: str) -> None: ...

    def quarantine(self, plan_keys: Iterable[str], *, actor_hash: str, reason: str) -> None: ...

    def release(self, plan_keys: Iterable[str]) -> None: ...


class SessionStore(Protocol):
    def touch_thread(self, owner: str, thread_id: str, *, bot: str, title: str, ttl_seconds: int) -> None: ...

    def owns(self, owner: str, thread_id: str) -> bool: ...

    def list_threads(self, owner: str, *, limit: int = ...) -> list[ThreadSummary]: ...

    def add_feedback(
        self,
        thread_id: str,
        request_id: str,
        *,
        rating: str,
        reasons: tuple[str, ...] = ...,
        ttl_seconds: int,
    ) -> None: ...


class PdfCache(Protocol):
    def get(self, plan_key: str, sha256: str) -> bytes | None: ...

    def put(self, plan_key: str, sha256: str, data: bytes) -> None: ...
