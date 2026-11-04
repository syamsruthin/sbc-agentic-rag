"""The exact-answer cache and the query-embedding cache, in the ``app`` table (PLAN.md section 9.5).

Caches never fail a request: a DynamoDB error on read is a miss, and on write is ignored (and logged by
name only). Expired items are treated as misses even before DynamoDB's TTL sweep deletes them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import numpy as np
from botocore.exceptions import BotoCoreError, ClientError

from sbc_aws.ddb import APP_TTL_ATTRIBUTE, Clock

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient

log = logging.getLogger(__name__)

ANSWER_TTL_SECONDS = 24 * 3600
EMBEDDING_TTL_SECONDS = 30 * 86400
MAX_ITEM_BYTES = 350 * 1024
"""Replies larger than this aren't cached (DynamoDB items are capped at 400 KB)."""

_SPACE = re.compile(r"\s+")


def normalize_question(text: str) -> str:
    """Lower case, collapsed whitespace, no trailing punctuation ("What's  the deductible?" -> "what's the
    deductible")."""
    return _SPACE.sub(" ", text).strip().lower().rstrip("?!. ")


def answer_cache_key(
    *,
    bot: str,
    bundle_version: str,
    prompt_versions: Mapping[str, str],
    model: str,
    intent: str,
    plan_keys: Sequence[str],
    fields: Sequence[str],
    question: str,
) -> str:
    """The exact-answer key from PLAN.md 9.5: same bot, bundle, prompts, model, intent, plans, fields and
    normalized question. No semantic matching: "deductible" and "out-of-pocket max" must never collide."""
    canonical = json.dumps(
        {
            "bot": bot,
            "bundle": bundle_version,
            "prompts": dict(sorted(prompt_versions.items())),
            "model": model,
            "intent": intent,
            "plans": sorted(plan_keys),
            "fields": sorted(fields),
            "q": normalize_question(question),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class DynamoAnswerCache:
    def __init__(self, client: DynamoDBClient, table: str, clock: Clock = time.time) -> None:
        self._client = client
        self._table = table
        self._clock = clock

    def get(self, key: str) -> dict[str, Any] | None:
        try:
            item = self._client.get_item(
                TableName=self._table, Key={"PK": {"S": f"CACHE#{key}"}, "SK": {"S": "A"}}
            )
        except (ClientError, BotoCoreError) as exc:
            log.warning("answer cache read failed: %s", type(exc).__name__)
            return None
        found = item.get("Item")
        if not found or int(found[APP_TTL_ATTRIBUTE]["N"]) <= self._clock():
            return None
        reply: dict[str, Any] = json.loads(found["reply"]["S"])
        return reply

    def put(self, key: str, reply: Mapping[str, Any], ttl_seconds: int = ANSWER_TTL_SECONDS) -> bool:
        """Store ``reply``; False when it is too large or the write failed."""
        body = json.dumps(reply, separators=(",", ":"))
        if len(body.encode()) > MAX_ITEM_BYTES:
            return False
        try:
            self._client.put_item(
                TableName=self._table,
                Item={
                    "PK": {"S": f"CACHE#{key}"},
                    "SK": {"S": "A"},
                    "reply": {"S": body},
                    APP_TTL_ATTRIBUTE: {"N": str(int(self._clock() + ttl_seconds))},
                },
            )
        except (ClientError, BotoCoreError) as exc:
            log.warning("answer cache write failed: %s", type(exc).__name__)
            return False
        return True


Embedder = Callable[[str], Sequence[float]]


class QueryEmbeddingCache:
    """Wraps a query embedder: an in-process LRU first, then DynamoDB, then the model.

    Vectors are stored as float16 (2 KB for 1024 dimensions), like the bundle's chunk vectors; the search
    normalizes and scores in float32, so the rounding doesn't change rankings in practice."""

    def __init__(
        self,
        client: DynamoDBClient,
        table: str,
        model: str,
        *,
        lru_size: int = 256,
        ttl_seconds: int = EMBEDDING_TTL_SECONDS,
        clock: Clock = time.time,
    ) -> None:
        self._client = client
        self._table = table
        self._model = model
        self._lru: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._lru_size = lru_size
        self._ttl = ttl_seconds
        self._clock = clock
        self.hits = {"memory": 0, "dynamodb": 0, "model": 0}

    def _key(self, text: str) -> str:
        return "QEMB#" + self._model + "#" + hashlib.sha256(normalize_question(text).encode()).hexdigest()

    def _remember(self, key: str, vector: tuple[float, ...]) -> None:
        self._lru[key] = vector
        self._lru.move_to_end(key)
        while len(self._lru) > self._lru_size:
            self._lru.popitem(last=False)

    def _load(self, key: str) -> tuple[float, ...] | None:
        try:
            item = self._client.get_item(TableName=self._table, Key={"PK": {"S": key}, "SK": {"S": "E"}}).get(
                "Item"
            )
        except (ClientError, BotoCoreError) as exc:
            log.warning("embedding cache read failed: %s", type(exc).__name__)
            return None
        if not item or int(item[APP_TTL_ATTRIBUTE]["N"]) <= self._clock():
            return None
        raw: bytes = item["v"]["B"]
        return tuple(float(x) for x in np.frombuffer(raw, dtype="<f2"))

    def _store(self, key: str, vector: Sequence[float]) -> None:
        packed = np.asarray(vector, dtype="<f2").tobytes()
        try:
            self._client.put_item(
                TableName=self._table,
                Item={
                    "PK": {"S": key},
                    "SK": {"S": "E"},
                    "v": {"B": packed},
                    APP_TTL_ATTRIBUTE: {"N": str(int(self._clock() + self._ttl))},
                },
            )
        except (ClientError, BotoCoreError) as exc:
            log.warning("embedding cache write failed: %s", type(exc).__name__)

    def wrap(self, embed: Embedder) -> Embedder:
        def cached(text: str) -> Sequence[float]:
            key = self._key(text)
            if key in self._lru:
                self.hits["memory"] += 1
                self._lru.move_to_end(key)
                return self._lru[key]
            stored = self._load(key)
            if stored is not None:
                self.hits["dynamodb"] += 1
                self._remember(key, stored)
                return stored
            self.hits["model"] += 1
            vector = tuple(embed(text))
            self._store(key, vector)
            self._remember(key, vector)
            return vector

        return cached
