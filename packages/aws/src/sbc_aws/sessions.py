"""Conversation index and feedback in the ``app`` table.

``OWNER#<owner> / THREAD#<thread_id>`` lists a principal's conversations (owner = Cognito ``sub`` for
brokers, the anonymous session ID for the public bot). The checkpoints themselves live in the checkpoints
table; this index only lets a broker reopen their own threads. Ownership is in the key, so a principal can
only ever list their own (this replaces Postgres row-level security, MIGRATION_PLAN.md section 11).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sbc_aws.ddb import APP_TTL_ATTRIBUTE, Clock, from_item, to_item

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient

FEEDBACK_REASONS = frozenset(
    {"wrong_number", "wrong_plan", "missing_info", "unclear", "not_helpful", "other"}
)
TITLE_MAX = 80


@dataclass(frozen=True)
class ThreadSummary:
    thread_id: str
    bot: str
    title: str
    updated_at: int


class DynamoSessionStore:
    def __init__(self, client: DynamoDBClient, table: str, clock: Clock = time.time) -> None:
        self._client = client
        self._table = table
        self._clock = clock

    def touch_thread(self, owner: str, thread_id: str, *, bot: str, title: str, ttl_seconds: int) -> None:
        """Create or refresh a thread entry. The title (already redacted) is kept from the first turn."""
        now = int(self._clock())
        self._client.update_item(
            TableName=self._table,
            Key={"PK": {"S": f"OWNER#{owner}"}, "SK": {"S": f"THREAD#{thread_id}"}},
            UpdateExpression=(
                "SET #b = :bot, updated_at = :now, #exp = :exp, title = if_not_exists(title, :title)"
            ),
            ExpressionAttributeNames={"#b": "bot", "#exp": APP_TTL_ATTRIBUTE},
            ExpressionAttributeValues={
                ":bot": {"S": bot},
                ":now": {"N": str(now)},
                ":exp": {"N": str(now + ttl_seconds)},
                ":title": {"S": title[:TITLE_MAX]},
            },
        )

    def owns(self, owner: str, thread_id: str) -> bool:
        item = self._client.get_item(
            TableName=self._table, Key={"PK": {"S": f"OWNER#{owner}"}, "SK": {"S": f"THREAD#{thread_id}"}}
        ).get("Item")
        if not item:
            return False
        return int(from_item(item)[APP_TTL_ATTRIBUTE]) > self._clock()

    def list_threads(self, owner: str, *, limit: int = 20) -> list[ThreadSummary]:
        """The owner's unexpired threads, newest first."""
        out = self._client.query(
            TableName=self._table,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :t)",
            ExpressionAttributeValues={":pk": {"S": f"OWNER#{owner}"}, ":t": {"S": "THREAD#"}},
        )
        now = self._clock()
        rows = [from_item(i) for i in out.get("Items", [])]
        threads = [
            ThreadSummary(
                str(r["SK"]).removeprefix("THREAD#"),
                str(r["bot"]),
                str(r.get("title", "")),
                int(r["updated_at"]),
            )
            for r in rows
            if int(r[APP_TTL_ATTRIBUTE]) > now
        ]
        return sorted(threads, key=lambda t: t.updated_at, reverse=True)[:limit]

    def add_feedback(
        self,
        thread_id: str,
        request_id: str,
        *,
        rating: str,
        reasons: tuple[str, ...] = (),
        ttl_seconds: int,
    ) -> None:
        """Thumbs up or down with optional reason codes; never free text (portfolio mode)."""
        if rating not in ("up", "down"):
            raise ValueError("rating must be 'up' or 'down'")
        unknown = set(reasons) - FEEDBACK_REASONS
        if unknown:
            raise ValueError(f"unknown feedback reasons: {sorted(unknown)}")
        now = int(self._clock())
        item = to_item(
            {
                "PK": f"FEEDBACK#{thread_id}",
                "SK": request_id,
                "rating": rating,
                "reasons": list(reasons),
                "at": now,
                APP_TTL_ATTRIBUTE: now + ttl_seconds,
            }
        )
        self._client.put_item(TableName=self._table, Item=item)
