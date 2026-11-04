"""The append-only audit log (PLAN.md section 8), in its own table.

Append-only is enforced twice: every write is conditional on the key not existing, and the API's IAM role
may only ``PutItem`` on this table (the CDK stack and its template test, WP9). There is no update or delete
path in code. Records hold no user text: plan keys, tools, versions and hashes only.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

from botocore.exceptions import ClientError

from sbc_aws.ddb import APP_TTL_ATTRIBUTE, Clock, to_item

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient

AUDIT_RETENTION_DAYS = 730


class AuditConflictError(RuntimeError):
    """A record with this key already exists; audit records are never overwritten."""


def hash_actor(actor: str) -> str:
    """A stable, non-reversible actor ID (Cognito ``sub`` or anonymous session ID)."""
    return hashlib.sha256(actor.encode()).hexdigest()[:32]


@dataclass(frozen=True)
class AuditRecord:
    request_id: str
    actor_hash: str
    bot: str
    action: str = "turn"
    """``turn``, ``clarify``, ``feedback`` or an admin action (``kill_switch``, ``quarantine``)."""
    plan_keys: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    verifier: str | None = None
    """``pass``, ``retried``, ``template`` or None when the turn didn't reach the verifier."""
    answer_sha256: str | None = None
    bundle_version: str | None = None
    models: tuple[str, ...] = ()
    prompt_versions: dict[str, str] = field(default_factory=dict)
    config_hash: str | None = None
    cost_usd: float | None = None
    detail: dict[str, str] = field(default_factory=dict)
    """Small structured extras, e.g. ``{"switch": "llm_off"}`` for admin actions."""


class DynamoAuditSink:
    def __init__(
        self,
        client: DynamoDBClient,
        table: str,
        clock: Clock = time.time,
        retention_days: int = AUDIT_RETENTION_DAYS,
    ) -> None:
        self._client = client
        self._table = table
        self._clock = clock
        self._retention = retention_days * 86400

    def write(self, record: AuditRecord) -> None:
        now = self._clock()
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int(now * 1000) % 1000:03d}Z"
        values = {k: (list(v) if isinstance(v, tuple) else v) for k, v in asdict(record).items()}
        values = {k: v for k, v in values.items() if v not in ([], {})}  # DynamoDB rejects empty sets only
        item = to_item({"PK": f"DAY#{day}", "SK": f"{ts}#{record.request_id}", "ts": ts, **values})
        item[APP_TTL_ATTRIBUTE] = {"N": str(int(now + self._retention))}
        try:
            self._client.put_item(
                TableName=self._table, Item=item, ConditionExpression="attribute_not_exists(PK)"
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise AuditConflictError(f"audit record {record.request_id} already exists") from None
            raise
