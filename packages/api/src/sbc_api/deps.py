"""Builds everything the API serves with, once per process (a Lambda cold start).

Order matters: secrets come from SSM Parameter Store into the environment *before* the settings are read,
so ``sbc_core.config`` keeps reading plain environment variables (WP1/WP2). Then:

- the facts bundle and its search index, shared by both bots (read-only, thread-safe for reads);
- the stores: DynamoDB and S3 when the table names are configured (always in dev and prd), otherwise
  in-process ones (``APP_ENV=local``);
- one agent per bot, each with its own OpenRouter key and a checkpointer whose TTL is the bot's retention.
"""

from __future__ import annotations

import logging
import os
import secrets as stdlib_secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from sbc_agent.classify import ChatModel
from sbc_agent.graph import Agent
from sbc_agent.respond import Deps
from sbc_api.auth.anon import SessionTokens
from sbc_api.auth.cognito import CognitoVerifier
from sbc_api.auth.principal import Authenticator, BearerAuthenticator, LocalAuthenticator
from sbc_api.auth.turnstile import TurnstileVerifier
from sbc_api.memory import (
    MemoryAnswerCache,
    MemoryAuditSink,
    MemoryDailyCap,
    MemoryKillSwitches,
    MemoryPdfCache,
    MemoryRateLimiter,
    MemorySessionStore,
)
from sbc_api.portal_preview import PortalPreviewClient
from sbc_api.ports import AnswerCache, AuditSink, DailyCap, KillSwitches, PdfCache, RateLimiter, SessionStore
from sbc_api.tracing import NoTracing, TurnTracer, build_tracer
from sbc_aws.audit import DynamoAuditSink
from sbc_aws.cache import DynamoAnswerCache, QueryEmbeddingCache
from sbc_aws.checkpoint import checkpoint_ttl_seconds, make_checkpointer
from sbc_aws.ddb import Tables, dynamodb_client, session_for
from sbc_aws.killswitch import DynamoKillSwitches
from sbc_aws.pdfcache import S3PdfCache
from sbc_aws.ratelimit import DynamoRateLimiter, GlobalCap
from sbc_aws.secrets import load_ssm_parameters
from sbc_aws.sessions import DynamoSessionStore
from sbc_core.config import BotName, Env, EnvSettings, Settings, load_settings
from sbc_core.facts import SqliteFactsReader
from sbc_core.llm import OpenRouterClient
from sbc_core.search import SearchIndexError, SqliteSearchIndex
from sbc_core.taxonomy import load_taxonomy
from sbc_retrieval.text_search import TextSearcher

log = logging.getLogger(__name__)

PDF_PROXY_BASE = "/api/sbc/"

ChatFactory = Callable[[BotName, Settings], ChatModel]
SaverFactory = Callable[[BotName, Settings, bool], BaseCheckpointSaver[Any]]
"""``(bot, settings, demo)``: the checkpointer for a bot, or for its shared demo account (shorter TTL)."""


@dataclass
class Stores:
    backend: str
    """``dynamodb`` or ``memory``."""
    limiter: RateLimiter
    daily_cap: DailyCap
    answers: AnswerCache
    audit: AuditSink
    switches: KillSwitches
    sessions: SessionStore
    pdfs: PdfCache


@dataclass
class BotRuntime:
    settings: Settings
    agent: Agent | None
    unavailable: str | None = None
    """Why the bot can't answer (only possible locally, e.g. no OpenRouter key)."""
    demo_agent: Agent | None = None
    """The broker bot's agent for the shared demo account: same everything, but its checkpoints expire after
    ``portfolio.demo_history_ttl_hours`` (the TTL is per checkpointer, so it needs its own)."""

    def agent_for(self, demo: bool) -> Agent | None:
        return self.demo_agent if demo and self.demo_agent is not None else self.agent


@dataclass
class ApiState:
    env: EnvSettings
    reader: SqliteFactsReader
    search_mode: str
    """``hybrid`` (bundle embeddings + BM25) or ``bm25`` (local runs on a bundle without embeddings)."""
    bots: dict[BotName, BotRuntime]
    stores: Stores
    authenticator: Authenticator
    session_tokens: SessionTokens
    turnstile: TurnstileVerifier | None
    """None only in local runs without ``TURNSTILE_SECRET_KEY``: the session route then skips the check."""
    portal: PortalPreviewClient = field(default_factory=PortalPreviewClient)
    init: dict[str, Any] = field(default_factory=dict)
    tracer: TurnTracer = field(default_factory=NoTracing)
    """Langfuse, one trace per turn, when both keys are set (:mod:`sbc_api.tracing`)."""


def memory_stores() -> Stores:
    return Stores(
        backend="memory", limiter=MemoryRateLimiter(), daily_cap=MemoryDailyCap(),
        answers=MemoryAnswerCache(), audit=MemoryAuditSink(), switches=MemoryKillSwitches(),
        sessions=MemorySessionStore(), pdfs=MemoryPdfCache(),
    )  # fmt: skip


def aws_stores(env: EnvSettings) -> Stores:
    tables = Tables.from_env(env)
    session = session_for(env)
    ddb = dynamodb_client(session)
    pdfs: PdfCache = (
        S3PdfCache(session.client("s3"), env.s3_data_bucket) if env.s3_data_bucket else MemoryPdfCache()
    )
    return Stores(
        backend="dynamodb", limiter=DynamoRateLimiter(ddb, tables.app), daily_cap=GlobalCap(ddb, tables.app),
        answers=DynamoAnswerCache(ddb, tables.app), audit=DynamoAuditSink(ddb, tables.audit),
        switches=DynamoKillSwitches(ddb, tables.app), sessions=DynamoSessionStore(ddb, tables.app), pdfs=pdfs,
    )  # fmt: skip


def _tables_configured(env: EnvSettings) -> bool:
    return bool(env.ddb_table_app and env.ddb_table_audit and env.ddb_table_checkpoints)


def load_secrets_into_environment() -> list[str]:
    """Copy SSM parameters under ``SSM_PREFIX`` into the environment. Returns the names it set.

    Runs whenever ``SSM_PREFIX`` is set, locally too: the full local stack (docs/local/LOCAL_PLAN.md) sets
    it and points the SDK at a local Parameter Store with ``AWS_ENDPOINT_URL_SSM``. Plain local runs leave it
    unset."""
    prefix = os.environ.get("SSM_PREFIX")
    if not prefix:
        return []
    import boto3

    region = os.environ.get("AWS_REGION", "us-east-1")
    loaded = load_ssm_parameters(prefix, boto3.Session(region_name=region).client("ssm"))
    return list(loaded.names)


def _open_index(env: EnvSettings, settings: Settings) -> tuple[SqliteSearchIndex, str]:
    role = settings.config.models.embeddings
    retrieval = settings.config.retrieval
    try:
        index = SqliteSearchIndex(
            env.facts_bundle_path, prefetch_limit=retrieval.prefetch_limit, rrf_k=retrieval.rrf_k,
            expected_embedding=(role.model, role.dimensions or 0),
        )  # fmt: skip
        return index, "hybrid"
    except SearchIndexError:
        if env.app_env is not Env.LOCAL:
            raise
        log.warning("bundle has no %s embeddings: local text search is BM25-only", role.model)
        index = SqliteSearchIndex(
            env.facts_bundle_path, prefetch_limit=retrieval.prefetch_limit, rrf_k=retrieval.rrf_k
        )
        return index, "bm25"


def build_state(
    env: EnvSettings | None = None,
    *,
    config_dir: Path | str = "config",
    chat_factory: ChatFactory | None = None,
    saver_factory: SaverFactory | None = None,
    stores: Stores | None = None,
    authenticator: Authenticator | None = None,
) -> ApiState:
    """Everything the app needs. Tests pass scripted models, savers, stores and an authenticator."""
    started = time.perf_counter()
    secrets = load_secrets_into_environment() if env is None else []
    env = env if env is not None else EnvSettings()
    settings = {bot: load_settings(bot, config_dir, env) for bot in BotName}
    reader = SqliteFactsReader(env.facts_bundle_path, pdf_proxy_base=PDF_PROXY_BASE)
    index, search_mode = _open_index(env, settings[BotName.BROKER])
    if stores is None:
        stores = aws_stores(env) if _tables_configured(env) else memory_stores()
    taxonomy = load_taxonomy()
    bots: dict[BotName, BotRuntime] = {}
    for bot, s in settings.items():
        chat: ChatModel
        searcher: TextSearcher
        if chat_factory is not None:
            chat = chat_factory(bot, s)
            searcher = TextSearcher(index, s.config.retrieval)
        elif env.openrouter_key_for(bot) is None:
            bots[bot] = BotRuntime(s, None, f"OPENROUTER_API_KEY_{bot.value.upper()} is not set")
            continue
        else:
            gateway = OpenRouterClient.for_bot(env, bot)
            chat = gateway
            searcher = TextSearcher.with_gateway(index, s.config.retrieval, s.config.models, gateway)
            if search_mode == "bm25":
                searcher = TextSearcher(index, s.config.retrieval, rerank=searcher.rerank)
            elif searcher.embed_query is not None and _tables_configured(env):
                cache = QueryEmbeddingCache(
                    dynamodb_client(session_for(env)),
                    str(env.ddb_table_app),
                    s.config.models.embeddings.model,
                )
                searcher.embed_query = cache.wrap(searcher.embed_query)
        deps = Deps(reader=reader, chat=chat, config=s.config, taxonomy=taxonomy, search=searcher)

        def saver(demo: bool, bot: BotName = bot, s: Settings = s) -> BaseCheckpointSaver[Any]:
            return saver_factory(bot, s, demo) if saver_factory is not None else _saver(env, s, demo)

        demo_agent = Agent(deps, saver(True)) if bot is BotName.BROKER else None
        bots[bot] = BotRuntime(s, Agent(deps, saver(False)), demo_agent=demo_agent)
    session_tokens = _session_tokens(env)
    if authenticator is None:
        authenticator = _authenticator(env, settings, session_tokens)
    turnstile_secret = env.turnstile_secret_key.get_secret_value() if env.turnstile_secret_key else ""
    turnstile = TurnstileVerifier(turnstile_secret) if turnstile_secret else None
    tracer = build_tracer(env)
    init = {
        "ms": round((time.perf_counter() - started) * 1000), "secrets_loaded": len(secrets),
        "bundle_version": reader.bundle_version, "search": search_mode, "stores": stores.backend,
        "tracing": tracer.name,
    }  # fmt: skip
    return ApiState(
        env,
        reader,
        search_mode,
        bots,
        stores,
        authenticator,
        session_tokens,
        turnstile,
        init=init,
        tracer=tracer,
    )


def _session_tokens(env: EnvSettings) -> SessionTokens:
    secret = env.session_jwt_secret.get_secret_value() if env.session_jwt_secret else ""
    if not secret:
        if env.app_env is not Env.LOCAL:
            raise RuntimeError("SESSION_JWT_SECRET is required outside local runs")
        secret = stdlib_secrets.token_urlsafe(32)  # local only: sessions last for this process
    return SessionTokens(secret)


def _authenticator(
    env: EnvSettings, settings: dict[BotName, Settings], sessions: SessionTokens
) -> Authenticator:
    cognito_configured = bool(env.cognito_user_pool_id and env.cognito_app_client_id)
    if env.app_env is Env.LOCAL and not cognito_configured:
        return LocalAuthenticator()  # plain local runs; the full local stack configures a Cognito stand-in
    cognito = None
    if cognito_configured:
        cognito = CognitoVerifier(
            region=env.aws_region,
            user_pool_id=str(env.cognito_user_pool_id),
            app_client_id=str(env.cognito_app_client_id),
            jwks_url=env.cognito_jwks_url,
        )
    demo = frozenset(settings[BotName.BROKER].config.portfolio.demo_user_ids)
    return BearerAuthenticator(cognito=cognito, sessions=sessions, demo_user_ids=demo)


def _saver(env: EnvSettings, settings: Settings, demo: bool) -> BaseCheckpointSaver[Any]:
    if not _tables_configured(env):
        return InMemorySaver()
    return make_checkpointer(
        str(env.ddb_table_checkpoints), session=session_for(env), s3_bucket=env.s3_data_bucket,
        ttl_seconds=checkpoint_ttl_seconds(settings.config, demo=demo),
    )  # fmt: skip
