"""Audit log, kill switches, the thread index and feedback, and the S3 PDF cache."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from sbc_aws.audit import AuditConflictError, AuditRecord, DynamoAuditSink, hash_actor
from sbc_aws.ddb import from_item
from sbc_aws.killswitch import DynamoKillSwitches
from sbc_aws.pdfcache import PdfHashMismatchError, S3PdfCache
from sbc_aws.sessions import DynamoSessionStore
from tests.unit.aws.conftest import BUCKET, TABLES, FakeClock

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient
    from mypy_boto3_s3.client import S3Client


# ------------------------------------------------------------------ audit


def _record(request_id: str = "req-1") -> AuditRecord:
    return AuditRecord(
        request_id=request_id, actor_hash=hash_actor("cognito-sub-123"), bot="broker",
        plan_keys=("2027:1",), tools=("get_plan_facts",), verifier="pass", answer_sha256="ab" * 32,
        bundle_version="2027.2", models=("anthropic/claude-sonnet-5.5",), prompt_versions={"compose": "v3"},
        config_hash="93d354cfb7a3", cost_usd=0.0031,
    )  # fmt: skip


def test_audit_records_are_written_by_day_with_retention(ddb: DynamoDBClient, clock: FakeClock) -> None:
    DynamoAuditSink(ddb, TABLES.audit, clock).write(_record())
    (item,) = (from_item(i) for i in ddb.scan(TableName=TABLES.audit)["Items"])
    assert item["PK"] == "DAY#2026-11-04"
    assert str(item["SK"]).endswith("#req-1")
    assert item["plan_keys"] == ["2027:1"]
    assert item["cost_usd"] == pytest.approx(0.0031)
    assert item["expires_at"] == int(clock.now) + 730 * 86400
    assert "detail" not in item  # empty values aren't stored


def test_audit_records_are_never_overwritten(ddb: DynamoDBClient, clock: FakeClock) -> None:
    sink = DynamoAuditSink(ddb, TABLES.audit, clock)
    sink.write(_record())
    with pytest.raises(AuditConflictError):
        sink.write(_record())  # same request at the same instant
    sink.write(_record("req-2"))
    assert ddb.scan(TableName=TABLES.audit)["Count"] == 2


def test_actor_hashes_are_stable_and_opaque() -> None:
    assert hash_actor("abc") == hash_actor("abc") != hash_actor("abd")
    assert len(hash_actor("abc")) == 32
    assert "abc" not in hash_actor("abc")


# ------------------------------------------------------------------ kill switches


def test_switches_default_to_on_and_are_cached(ddb: DynamoDBClient, clock: FakeClock) -> None:
    admin = DynamoKillSwitches(ddb, TABLES.app, clock)
    other = DynamoKillSwitches(ddb, TABLES.app, clock)  # another Lambda instance
    assert admin.llm_enabled("public")
    assert other.llm_enabled("public")
    admin.set_llm_enabled("public", False, actor_hash="admin")
    assert not admin.llm_enabled("public")  # the writer sees it at once
    assert admin.llm_enabled("broker")
    assert other.llm_enabled("public")  # others within their 60 s cache...
    clock.advance(60)
    assert not other.llm_enabled("public")  # ...then pick it up
    admin.set_llm_enabled("public", True, actor_hash="admin")
    assert admin.llm_enabled("public")


def test_plans_can_be_quarantined_and_released(ddb: DynamoDBClient, clock: FakeClock) -> None:
    switches = DynamoKillSwitches(ddb, TABLES.app, clock)
    switches.quarantine(["2027:1", "2027:2"], actor_hash="admin", reason="wrong number report")
    assert switches.quarantined() == {"2027:1", "2027:2"}
    switches.release(["2027:1"])
    assert switches.quarantined() == {"2027:2"}


# ------------------------------------------------------------------ threads and feedback


def test_threads_are_listed_newest_first_for_their_owner_only(ddb: DynamoDBClient, clock: FakeClock) -> None:
    store = DynamoSessionStore(ddb, TABLES.app, clock)
    store.touch_thread("u1", "t1", bot="broker", title="Deductible on BTCU8", ttl_seconds=3600)
    clock.advance(10)
    store.touch_thread("u1", "t2", bot="broker", title="Compare two plans", ttl_seconds=3600)
    clock.advance(10)
    store.touch_thread("u1", "t1", bot="broker", title="A later message", ttl_seconds=3600)
    threads = store.list_threads("u1")
    assert [t.thread_id for t in threads] == ["t1", "t2"]
    assert threads[0].title == "Deductible on BTCU8"  # the first message's title is kept
    assert store.list_threads("u2") == []
    assert store.owns("u1", "t2")
    assert not store.owns("u2", "t2")


def test_expired_threads_disappear(ddb: DynamoDBClient, clock: FakeClock) -> None:
    store = DynamoSessionStore(ddb, TABLES.app, clock)
    store.touch_thread("demo", "t1", bot="broker", title="x" * 200, ttl_seconds=24 * 3600)
    assert len(store.list_threads("demo")[0].title) == 80
    clock.advance(24 * 3600 + 1)
    assert store.list_threads("demo") == []
    assert not store.owns("demo", "t1")


def test_feedback_accepts_only_known_ratings_and_reasons(ddb: DynamoDBClient, clock: FakeClock) -> None:
    store = DynamoSessionStore(ddb, TABLES.app, clock)
    store.add_feedback("t1", "req-1", rating="down", reasons=("wrong_number",), ttl_seconds=3600)
    item = from_item(
        ddb.get_item(TableName=TABLES.app, Key={"PK": {"S": "FEEDBACK#t1"}, "SK": {"S": "req-1"}})["Item"]
    )
    assert (item["rating"], item["reasons"]) == ("down", ["wrong_number"])
    with pytest.raises(ValueError, match="rating"):
        store.add_feedback("t1", "req-2", rating="meh", ttl_seconds=3600)
    with pytest.raises(ValueError, match="reasons"):
        store.add_feedback("t1", "req-3", rating="up", reasons=("free text here",), ttl_seconds=3600)


# ------------------------------------------------------------------ PDF cache

PDF = b"%PDF-1.7\n% a tiny stand-in, not a real SBC\n%%EOF\n"
SHA = hashlib.sha256(PDF).hexdigest()


def test_pdfs_round_trip_under_a_hash_named_key(s3: S3Client) -> None:
    cache = S3PdfCache(s3, BUCKET)
    assert cache.get("2027:100000000001", SHA) is None
    cache.put("2027:100000000001", SHA, PDF)
    assert cache.get("2027:100000000001", SHA) == PDF
    assert cache.key("2027:100000000001", SHA) == f"pdf-cache/2027_100000000001-{SHA[:16]}.pdf"


def test_a_pdf_with_the_wrong_hash_is_never_stored_or_served(s3: S3Client) -> None:
    cache = S3PdfCache(s3, BUCKET)
    with pytest.raises(PdfHashMismatchError):
        cache.put("2027:1", SHA, PDF + b"tampered")
    s3.put_object(Bucket=BUCKET, Key=cache.key("2027:1", SHA), Body=PDF + b"tampered")
    assert cache.get("2027:1", SHA) is None


def test_the_hash_must_look_like_sha256(s3: S3Client) -> None:
    with pytest.raises(ValueError, match="sha256"):
        S3PdfCache(s3, BUCKET).key("2027:1", "not-a-hash")
