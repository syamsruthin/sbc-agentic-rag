"""Kill switches and plan quarantine (PLAN.md 3.5.6), read from the ``app`` table and cached for 60 s.

``KILL / <bot>`` holds ``llm_enabled``; ``QUARANTINE / <plan_key>`` lists plans to answer about only with
"temporarily unavailable, see the official SBC". A missing switch means "on": a fresh deployment answers
normally. Setters (admin endpoints) write through and refresh this instance's cache at once; other Lambda
instances pick the change up within the cache period.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sbc_aws.ddb import Clock, from_item

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient

CACHE_SECONDS = 60.0


@dataclass(frozen=True)
class Switches:
    llm_disabled: frozenset[str]
    """Bots whose LLM answers are off (deterministic mode only)."""
    quarantined: frozenset[str]


class DynamoKillSwitches:
    def __init__(
        self,
        client: DynamoDBClient,
        table: str,
        clock: Clock = time.monotonic,
        cache_seconds: float = CACHE_SECONDS,
    ) -> None:
        self._client = client
        self._table = table
        self._clock = clock
        self._cache_seconds = cache_seconds
        self._snapshot: Switches | None = None
        self._loaded_at = 0.0

    def _query(self, pk: str) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        kwargs: dict[str, object] = {}
        while True:
            page = self._client.query(
                TableName=self._table,
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": {"S": pk}},
                ConsistentRead=True,
                **kwargs,  # type: ignore[arg-type]
            )
            items += [from_item(i) for i in page.get("Items", [])]
            if "LastEvaluatedKey" not in page:
                return items
            kwargs = {"ExclusiveStartKey": page["LastEvaluatedKey"]}

    def snapshot(self) -> Switches:
        now = self._clock()
        if self._snapshot is None or now - self._loaded_at >= self._cache_seconds:
            kill = self._query("KILL")
            quarantine = self._query("QUARANTINE")
            self._snapshot = Switches(
                llm_disabled=frozenset(str(i["SK"]) for i in kill if i.get("llm_enabled") is False),
                quarantined=frozenset(str(i["SK"]) for i in quarantine),
            )
            self._loaded_at = now
        return self._snapshot

    def llm_enabled(self, bot: str) -> bool:
        return bot not in self.snapshot().llm_disabled

    def quarantined(self) -> frozenset[str]:
        return self.snapshot().quarantined

    def set_llm_enabled(self, bot: str, enabled: bool, *, actor_hash: str) -> None:
        self._client.put_item(
            TableName=self._table,
            Item={
                "PK": {"S": "KILL"},
                "SK": {"S": bot},
                "llm_enabled": {"BOOL": enabled},
                "actor": {"S": actor_hash},
                "at": {"N": str(int(time.time()))},
            },
        )
        self._snapshot = None

    def quarantine(self, plan_keys: Iterable[str], *, actor_hash: str, reason: str) -> None:
        for key in plan_keys:
            self._client.put_item(
                TableName=self._table,
                Item={
                    "PK": {"S": "QUARANTINE"},
                    "SK": {"S": key},
                    "reason": {"S": reason},
                    "actor": {"S": actor_hash},
                    "at": {"N": str(int(time.time()))},
                },
            )
        self._snapshot = None

    def release(self, plan_keys: Iterable[str]) -> None:
        for key in plan_keys:
            self._client.delete_item(TableName=self._table, Key={"PK": {"S": "QUARANTINE"}, "SK": {"S": key}})
        self._snapshot = None
