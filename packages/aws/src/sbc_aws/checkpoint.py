"""The LangGraph checkpointer on DynamoDB (``langgraph-checkpoint-aws``), with large checkpoints in S3.

Spike 1 (docs/aws/SPIKES.md) showed a clarifying question asked in one process resuming in another through
this saver, which is what happens when two Lambda instances serve consecutive requests of one thread.

The TTL is fixed per saver instance, so the API builds one saver (and one ``Agent``) per retention class:
the public bot, the broker bot, and the shared demo broker account (docs/aws/DEPLOYMENT_ANALYSIS.md 17.5).
"""

from __future__ import annotations

from typing import Any

import boto3
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph_checkpoint_aws import DynamoDBSaver

from sbc_core.config import AppConfig

DEFAULT_S3_PREFIX = "checkpoints/"


def checkpoint_ttl_seconds(config: AppConfig, *, demo: bool = False) -> int:
    """How long a thread's checkpoints are kept: the bot's retention, or the demo account's history TTL."""
    if demo:
        return config.portfolio.demo_history_ttl_hours * 3600
    return config.bot.retention_days * 86400


def make_checkpointer(
    table: str,
    *,
    session: boto3.Session,
    ttl_seconds: int,
    s3_bucket: str | None = None,
    s3_prefix: str = DEFAULT_S3_PREFIX,
    endpoint_url: str | None = None,
) -> BaseCheckpointSaver[Any]:
    """A ``DynamoDBSaver`` on ``table``. Checkpoints over 350 KB go to ``s3://{s3_bucket}/{s3_prefix}``."""
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    offload = {"bucket_name": s3_bucket, "key_prefix": s3_prefix} if s3_bucket else None
    saver: BaseCheckpointSaver[Any] = DynamoDBSaver(
        table_name=table,
        session=session,
        endpoint_url=endpoint_url,
        ttl_seconds=ttl_seconds,
        s3_offload_config=offload,
    )
    return saver
