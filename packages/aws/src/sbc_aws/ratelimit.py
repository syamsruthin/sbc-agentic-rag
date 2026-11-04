"""Fixed-window rate limits and the global daily cap, as conditional counters in the ``app`` table.

Each window is one item, ``PK = RATE#<key>``, ``SK = <window>#<bucket>``, incremented with ``ADD`` only while
it is below the limit, so concurrent requests can never push it past the limit. Items expire an hour after
their window ends. PLAN.md asked for sliding windows (Upstash); fixed windows are within a factor of two of a
sliding window at a window boundary, which is close enough for these limits (MIGRATION_PLAN.md section 11).

Errors from DynamoDB are raised, not swallowed: a limiter that silently allows everything would remove the
cost protection the public demo relies on.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from botocore.exceptions import ClientError

from sbc_aws.ddb import APP_TTL_ATTRIBUTE, Clock
from sbc_core.config import RateLimits

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient

MINUTE = 60
DAY = 86400
_EXPIRY_GRACE = 3600


@dataclass(frozen=True)
class Window:
    name: str
    seconds: int
    limit: int


@dataclass(frozen=True)
class Decision:
    allowed: bool
    window: str | None = None
    """The window that refused the request, when it was refused."""
    retry_after_s: int = 0
    remaining: int | None = None
    """Requests left in the tightest window, when allowed."""


def windows_for(limits: RateLimits) -> list[Window]:
    """The per-minute and per-day windows of a bot profile's (or the demo account's) limits."""
    return [Window("min", MINUTE, limits.per_minute), Window("day", DAY, limits.per_day)]


def ip_windows(limits: RateLimits) -> list[Window]:
    """The per-IP daily window, if the profile has one (the public bot)."""
    return [Window("ipday", DAY, limits.per_ip_per_day)] if limits.per_ip_per_day else []


def _is_conditional_failure(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


class _Counters:
    def __init__(self, client: DynamoDBClient, table: str, clock: Clock = time.time) -> None:
        self._client = client
        self._table = table
        self._clock = clock

    def _increment(self, pk: str, sk: str, limit: int, expires_at: int) -> int | None:
        """Add one if the counter is below ``limit``; return the new count, or None at the limit."""
        try:
            out = self._client.update_item(
                TableName=self._table,
                Key={"PK": {"S": pk}, "SK": {"S": sk}},
                UpdateExpression="ADD #n :one SET #exp = if_not_exists(#exp, :exp)",
                ConditionExpression="attribute_not_exists(#n) OR #n < :limit",
                ExpressionAttributeNames={"#n": "n", "#exp": APP_TTL_ATTRIBUTE},
                ExpressionAttributeValues={
                    ":one": {"N": "1"},
                    ":limit": {"N": str(limit)},
                    ":exp": {"N": str(expires_at)},
                },
                ReturnValues="UPDATED_NEW",
            )
        except ClientError as exc:
            if _is_conditional_failure(exc):
                return None
            raise
        return int(out["Attributes"]["n"]["N"])

    def _count(self, pk: str, sk: str) -> int:
        item = self._client.get_item(
            TableName=self._table, Key={"PK": {"S": pk}, "SK": {"S": sk}}, ConsistentRead=True
        ).get("Item")
        return int(item["n"]["N"]) if item and "n" in item else 0


class DynamoRateLimiter(_Counters):
    def hit(self, key: str, windows: Sequence[Window]) -> Decision:
        """Count one request for ``key`` (e.g. ``broker:user:<sub>``) in every window, shortest first.

        A request refused by a later window has already been counted in the earlier ones; with the shortest
        window first, that only ever over-counts a minute window, which resets quickly."""
        now = self._clock()
        remaining: int | None = None
        for window in sorted(windows, key=lambda w: w.seconds):
            bucket = int(now // window.seconds)
            end = (bucket + 1) * window.seconds
            count = self._increment(
                f"RATE#{key}", f"{window.name}#{bucket}", window.limit, end + _EXPIRY_GRACE
            )
            if count is None:
                return Decision(False, window=window.name, retry_after_s=max(1, int(end - now)))
            left = window.limit - count
            remaining = left if remaining is None else min(remaining, left)
        return Decision(True, remaining=remaining)


class GlobalCap(_Counters):
    """A daily cap on model-calling turns across both bots (``portfolio.global_daily_llm_turns``)."""

    PK = "GLOBAL"

    def _sk(self) -> str:
        return "DAY#" + time.strftime("%Y%m%d", time.gmtime(self._clock()))

    def consume(self, cap: int) -> bool:
        """Count one turn if today's count is below ``cap``; False when the cap is reached."""
        expires = int(self._clock()) + 2 * DAY
        return self._increment(self.PK, self._sk(), cap, expires) is not None

    def used_today(self) -> int:
        return self._count(self.PK, self._sk())
