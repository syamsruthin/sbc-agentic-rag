"""Rate limits, the global cap, the answer cache and the query-embedding cache."""

from __future__ import annotations

from typing import TYPE_CHECKING

import boto3
import pytest

from sbc_aws.cache import DynamoAnswerCache, QueryEmbeddingCache, answer_cache_key, normalize_question
from sbc_aws.ratelimit import DAY, DynamoRateLimiter, GlobalCap, Window, ip_windows, windows_for
from sbc_core.config import RateLimits
from tests.unit.aws.conftest import REGION, TABLES, FakeClock

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient


# ------------------------------------------------------------------ rate limits


def test_the_limit_is_reached_exactly(ddb: DynamoDBClient, clock: FakeClock) -> None:
    limiter = DynamoRateLimiter(ddb, TABLES.app, clock)
    windows = [Window("min", 60, 3)]
    results = [limiter.hit("public:session:s1", windows) for _ in range(5)]
    assert [r.allowed for r in results] == [True, True, True, False, False]
    assert [r.remaining for r in results[:3]] == [2, 1, 0]
    assert results[3].window == "min"
    assert 1 <= results[3].retry_after_s <= 60


def test_a_new_window_resets_the_count(ddb: DynamoDBClient, clock: FakeClock) -> None:
    limiter = DynamoRateLimiter(ddb, TABLES.app, clock)
    windows = [Window("min", 60, 1)]
    assert limiter.hit("k", windows).allowed
    assert not limiter.hit("k", windows).allowed
    clock.advance(60)
    assert limiter.hit("k", windows).allowed


def test_the_day_window_holds_across_minutes(ddb: DynamoDBClient, clock: FakeClock) -> None:
    limiter = DynamoRateLimiter(ddb, TABLES.app, clock)
    windows = windows_for(RateLimits(per_minute=10, per_day=3))
    for _ in range(3):
        assert limiter.hit("broker:user:u1", windows).allowed
        clock.advance(61)
    refused = limiter.hit("broker:user:u1", windows)
    assert (refused.allowed, refused.window) == (False, "day")
    assert refused.retry_after_s > 60


def test_subjects_are_counted_separately(ddb: DynamoDBClient, clock: FakeClock) -> None:
    limiter = DynamoRateLimiter(ddb, TABLES.app, clock)
    windows = [Window("min", 60, 1)]
    assert limiter.hit("a", windows).allowed
    assert limiter.hit("b", windows).allowed


def test_counters_expire_after_their_window(ddb: DynamoDBClient, clock: FakeClock) -> None:
    DynamoRateLimiter(ddb, TABLES.app, clock).hit("k", [Window("min", 60, 5)])
    item = ddb.scan(TableName=TABLES.app)["Items"][0]
    assert clock.now < int(item["expires_at"]["N"]) <= clock.now + 60 + 3600


def test_window_helpers_follow_the_profile() -> None:
    public = RateLimits(per_minute=10, per_day=60, per_ip_per_day=200)
    assert [(w.name, w.limit) for w in windows_for(public)] == [("min", 10), ("day", 60)]
    assert [(w.name, w.seconds, w.limit) for w in ip_windows(public)] == [("ipday", DAY, 200)]
    assert ip_windows(RateLimits(per_minute=30, per_day=1000)) == []


def test_dynamodb_errors_are_raised_not_swallowed(clock: FakeClock) -> None:
    client = boto3.Session(region_name=REGION).client("dynamodb")  # no tables created
    with pytest.raises(Exception, match=r"ResourceNotFound|non-existent|not found"):
        DynamoRateLimiter(client, "missing-table", clock).hit("k", [Window("min", 60, 1)])


def test_global_cap_counts_per_utc_day(ddb: DynamoDBClient, clock: FakeClock) -> None:
    cap = GlobalCap(ddb, TABLES.app, clock)
    assert [cap.consume(2) for _ in range(3)] == [True, True, False]
    assert cap.used_today() == 2
    clock.advance(DAY)
    assert cap.used_today() == 0
    assert cap.consume(2)


# ------------------------------------------------------------------ answer cache


def _key(**overrides: object) -> str:
    args: dict[str, object] = {
        "bot": "broker", "bundle_version": "2027.2", "prompt_versions": {"compose": "v3", "agent": "v2"},
        "model": "anthropic/claude-sonnet-5.5", "intent": "lookup", "plan_keys": ["2027:2", "2027:1"],
        "fields": ["overall_deductible"], "question": "What's the deductible on BTCU8?",
    }  # fmt: skip
    args.update(overrides)
    return answer_cache_key(**args)  # type: ignore[arg-type]


def test_answer_key_ignores_case_spacing_and_order_but_not_meaning() -> None:
    assert (
        _key() == _key(question="  what's THE deductible on btcu8 ") == _key(plan_keys=["2027:1", "2027:2"])
    )
    assert _key() != _key(fields=["oop_limit"])
    assert _key() != _key(bundle_version="2027.3")
    assert _key() != _key(bot="public")
    assert normalize_question("What's  the\tdeductible?!") == "what's the deductible"


def test_answers_round_trip_and_expire(ddb: DynamoDBClient, clock: FakeClock) -> None:
    cache = DynamoAnswerCache(ddb, TABLES.app, clock)
    reply = {"kind": "answer", "markdown": "The deductible is $500.", "citations": [{"page": 1}]}
    assert cache.get("k1") is None
    assert cache.put("k1", reply, ttl_seconds=100)
    assert cache.get("k1") == reply
    clock.advance(101)
    assert cache.get("k1") is None  # expired, even before DynamoDB's TTL sweep


def test_oversized_answers_are_not_cached(ddb: DynamoDBClient, clock: FakeClock) -> None:
    cache = DynamoAnswerCache(ddb, TABLES.app, clock)
    assert not cache.put("big", {"markdown": "x" * 400_000})
    assert cache.get("big") is None


def test_cache_errors_are_misses(clock: FakeClock) -> None:
    client = boto3.Session(region_name=REGION).client("dynamodb")
    cache = DynamoAnswerCache(client, "missing-table", clock)
    assert cache.get("k") is None
    assert not cache.put("k", {"a": 1})


# ------------------------------------------------------------------ query-embedding cache


def test_embeddings_come_from_memory_then_dynamodb_then_the_model(
    ddb: DynamoDBClient, clock: FakeClock
) -> None:
    calls: list[str] = []

    def embed(text: str) -> list[float]:
        calls.append(text)
        return [0.25, -0.5, 0.125]

    first = QueryEmbeddingCache(ddb, TABLES.app, "baai/bge-m3", clock=clock)
    cached = first.wrap(embed)
    assert list(cached("MRI cost?")) == [0.25, -0.5, 0.125]
    assert list(cached("mri  cost")) == [0.25, -0.5, 0.125]  # same normalized query: memory hit
    assert first.hits == {"memory": 1, "dynamodb": 0, "model": 1}

    second = QueryEmbeddingCache(ddb, TABLES.app, "baai/bge-m3", clock=clock)  # another Lambda instance
    assert list(second.wrap(embed)("MRI cost?")) == [0.25, -0.5, 0.125]  # exact in float16
    assert second.hits == {"memory": 0, "dynamodb": 1, "model": 0}
    assert calls == ["MRI cost?"]

    other_model = QueryEmbeddingCache(ddb, TABLES.app, "qwen/qwen3-embedding-8b", clock=clock)
    other_model.wrap(embed)("MRI cost?")
    assert other_model.hits["model"] == 1
