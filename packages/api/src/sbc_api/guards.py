"""The checks around every chat turn (PLAN.md 3.5.6, 8 and 9.5; docs/aws/DEPLOYMENT_ANALYSIS.md 17.4).

Before a turn, in this order (none of them calls a model):

1. **Rate limits** per caller: the bot profile's limits, or the demo account's tighter ones; the public bot
   also per IP. A refusal is a ``limit`` event.
2. **Thread ownership**: a ``thread_id`` must belong to the caller (404 otherwise, so threads can't be
   probed). New threads are always created by the server.
3. **Kill switch**: with LLM answers off for the bot, a fixed "temporarily unavailable" message.
4. **Exact-answer cache** for a thread's first message: a hit is answered at once, costs no model call and
   doesn't count toward the daily cap.
5. **Global daily cap** on model-calling turns (portfolio mode): then a ``limit`` event.

After a turn: plans under quarantine are replaced by a "temporarily unavailable" message, the answer is
cached (first messages only), and an audit record is written (no user text).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from sbc_agent.graph import Reply
from sbc_agent.prompts import load
from sbc_api.auth.principal import Principal
from sbc_api.deps import ApiState
from sbc_aws.audit import AuditRecord, hash_actor
from sbc_aws.cache import answer_cache_key
from sbc_aws.checkpoint import checkpoint_ttl_seconds
from sbc_aws.ratelimit import ip_windows, windows_for
from sbc_core.config import AppConfig, BotName

log = logging.getLogger(__name__)

PROMPTS = ("classify", "agent", "compose")
LLM_OFF = (
    "The assistant is temporarily unavailable. Please check the plan's official Summary of Benefits and "
    "Coverage, or try again later."
)
QUARANTINED = (
    "Information for this plan is temporarily unavailable here. Please check the plan's official Summary of "
    "Benefits and Coverage."
)
GLOBAL_CAP = "The demo has reached its limit for today. Please try again tomorrow."
TOO_MANY = "You're sending messages faster than the demo allows. Please wait a moment and try again."


def config_of(api: ApiState, bot: BotName) -> AppConfig:
    return api.bots[bot].settings.config


def thread_ttl_seconds(api: ApiState, principal: Principal) -> int:
    return checkpoint_ttl_seconds(config_of(api, principal.bot), demo=principal.is_demo)


def rate_limited(api: ApiState, principal: Principal, ip: str | None) -> dict[str, Any] | None:
    """The ``limit`` event body when the caller is over a limit, else None (and the request is counted)."""
    config = config_of(api, principal.bot)
    limits = config.portfolio.demo_rate_limits if principal.is_demo else config.bot.rate_limits
    scope = "session" if principal.bot is BotName.PUBLIC else "user"
    decision = api.stores.limiter.hit(
        f"{principal.bot.value}:{scope}:{principal.subject}", windows_for(limits)
    )
    if decision.allowed and principal.bot is BotName.PUBLIC and ip:
        scope = "ip"
        decision = api.stores.limiter.hit(f"public:ip:{ip}", ip_windows(config.bot.rate_limits))
    if decision.allowed:
        return None
    # The shared demo account's refusals get their own event: the DemoAccountLimited alarm watches it (WP12).
    event = "demo_user_limit_reached" if principal.is_demo else "rate_limited"
    log.info("rate limited", extra={"event": event, "bot": principal.bot.value, "scope": scope,
                                    "window": decision.window, "demo": principal.is_demo})  # fmt: skip
    return {"scope": scope, "retry_after_s": decision.retry_after_s, "message": TOO_MANY}


def global_cap_reached(api: ApiState) -> dict[str, Any] | None:
    """Counts one model-calling turn; the ``limit`` body when today's cap is used up."""
    cap = config_of(api, BotName.BROKER).portfolio.global_daily_llm_turns
    if cap is None or api.stores.daily_cap.consume(cap):
        return None
    log.warning("global daily cap reached", extra={"event": "global_cap_reached", "cap": cap})
    return {"scope": "global", "retry_after_s": 0, "message": GLOBAL_CAP}


def prompt_versions() -> dict[str, str]:
    return {name: load(name)[1] for name in PROMPTS}


def first_turn_cache_key(
    api: ApiState, principal: Principal, message: str, ui_selected: list[str], user_state: str | None
) -> str:
    """The exact-answer key for a thread's first message. Later messages depend on the thread's history, so
    they are never cached. The config hash covers the models and every behavior setting."""
    settings = api.bots[principal.bot].settings
    return answer_cache_key(
        bot=principal.bot.value, bundle_version=api.reader.bundle_version, prompt_versions=prompt_versions(),
        model=settings.config_hash, intent="first_turn", plan_keys=ui_selected, fields=[user_state or ""],
        question=message,
    )  # fmt: skip


def apply_quarantine(api: ApiState, reply: Reply) -> Reply:
    if reply.kind != "answer" or not set(reply.plan_keys) & api.stores.switches.quarantined():
        return reply
    log.warning(
        "quarantined plan in an answer", extra={"event": "quarantine_hit", "plans": list(reply.plan_keys)}
    )
    return Reply("message", QUARANTINED, plan_keys=reply.plan_keys, trace=reply.trace)


def _verifier_outcome(reply: Reply) -> str | None:
    if reply.kind != "answer":
        return None
    if reply.trace.get("fallback_reason"):
        return "template"
    return "retried" if reply.trace.get("verifier") else "pass"


def audit_turn(
    api: ApiState, principal: Principal, request_id: str, reply: Reply, *, cached: bool = False
) -> None:
    """One append-only record per turn; a failure is logged (and alarmed on), never shown to the user."""
    trace = reply.trace
    usage = list(trace.get("usage", [])) + ([trace["classify_usage"]] if trace.get("classify_usage") else [])
    settings = api.bots[principal.bot].settings
    record = AuditRecord(
        request_id=request_id, actor_hash=hash_actor(principal.subject), bot=principal.bot.value,
        action="clarify" if reply.kind == "clarify" else "turn", plan_keys=tuple(reply.plan_keys),
        tools=tuple(str(t.get("name")) for t in trace.get("tools", [])), verifier=_verifier_outcome(reply),
        answer_sha256=hashlib.sha256(reply.markdown.encode()).hexdigest(),
        bundle_version=api.reader.bundle_version, models=tuple(sorted({str(u.get("model")) for u in usage})),
        prompt_versions=dict(trace.get("prompt_versions", {})), config_hash=settings.config_hash,
        cost_usd=round(sum(u.get("cost_usd") or 0 for u in usage), 6),
        detail={"cached": "true"} if cached else {},
    )  # fmt: skip
    try:
        api.stores.audit.write(record)
    except Exception:
        log.exception("audit write failed", extra={"event": "audit_failed", "request_id": request_id})
