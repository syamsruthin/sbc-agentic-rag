"""Optional Langfuse tracing: one trace per chat turn (docs/aws/MIGRATION_PLAN.md WP12, PLAN.md 7.1).

The trace is built after the turn from what the graph already records (:attr:`sbc_agent.graph.Reply.trace`):

- the root observation: the redacted question in, the verified answer out, and the path, intent, verifier
  rejections, fallback reason, prompt versions and plans as metadata;
- one ``generation`` (or ``embedding``) per model call, with the model that served it, token usage, the cost
  OpenRouter reported and the call's latency;
- one ``tool`` per tool call, with its name and whether it succeeded (never its arguments);
- trace attributes: the bot, the thread as the session, a hashed actor ID as the user, the bundle version
  and the config hash.

Nothing reaches Langfuse unredacted: the question is redacted at ingress already, and a mask runs the same
redaction over every string again before export (it also masks dates and phone numbers that an SBC quotes in
an answer; the trace loses those, the user's answer doesn't). The model calls don't go through LangChain, so
a LangChain callback would see none of this; the per-turn trace does.

Tracing is on only when both Langfuse keys are set (``EnvSettings.langfuse_enabled``). The SDK is imported
only then, so it costs nothing at cold start otherwise. A tracing failure is logged and never reaches the
user.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from sbc_agent.graph import Reply
from sbc_agent.redact import redact
from sbc_core.config import EnvSettings

if TYPE_CHECKING:
    from langfuse import Langfuse

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TracedTurn:
    request_id: str
    thread_id: str
    bot: str
    actor_hash: str
    """:func:`sbc_aws.audit.hash_actor` of the caller, as in the audit log; never the raw ID."""
    question: str
    """Already redacted."""
    reply: Reply
    bundle_version: str
    config_hash: str
    demo: bool = False
    cached: bool = False


class TurnTracer(Protocol):
    name: str

    def record(self, turn: TracedTurn) -> None: ...

    def flush(self) -> None:
        """Send what is queued. Called after each response, because Lambda freezes the process after it."""
        ...


class NoTracing:
    name = "off"

    def record(self, turn: TracedTurn) -> None:
        return None

    def flush(self) -> None:
        return None


def mask(*, data: Any, **_: Any) -> Any:
    """Langfuse's mask hook: the ingress redaction, over every string in the payload."""
    if isinstance(data, str):
        return redact(data).text
    if isinstance(data, dict):
        return {k: mask(data=v) for k, v in data.items()}
    if isinstance(data, list | tuple):
        return [mask(data=v) for v in data]
    return data


def _usage(reply: Reply) -> list[dict[str, Any]]:
    trace = reply.trace
    calls = [trace["classify_usage"]] if trace.get("classify_usage") else []
    return calls + list(trace.get("usage", []))


class LangfuseTracer:
    name = "langfuse"

    def __init__(self, client: Langfuse) -> None:
        self._client = client

    @classmethod
    def from_env(cls, env: EnvSettings, **client_options: Any) -> LangfuseTracer:
        from langfuse import Langfuse  # only when tracing is on: the SDK pulls in OpenTelemetry

        secret = env.langfuse_secret_key.get_secret_value() if env.langfuse_secret_key else None
        client = Langfuse(
            public_key=env.langfuse_public_key, secret_key=secret, base_url=env.langfuse_url, mask=mask,
            environment=env.app_env.value, **client_options,
        )  # fmt: skip
        return cls(client)

    def record(self, turn: TracedTurn) -> None:
        try:
            self._record(turn)
        except Exception:
            log.warning("langfuse trace failed", exc_info=True,
                        extra={"event": "trace_failed", "request_id": turn.request_id})  # fmt: skip

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception:
            log.warning("langfuse flush failed", exc_info=True, extra={"event": "trace_failed"})

    def _record(self, turn: TracedTurn) -> None:
        from langfuse import propagate_attributes

        reply, trace = turn.reply, turn.reply.trace
        tags = [turn.bot, reply.kind] + (["cached"] if turn.cached else []) + (["demo"] if turn.demo else [])
        attributes = {
            "request_id": turn.request_id, "bundle_version": turn.bundle_version,
            "config_hash": turn.config_hash,
        }  # fmt: skip
        classification = trace.get("classification") or {}
        metadata = {
            "path": trace.get("path"), "intent": classification.get("intent"),
            "plan_keys": list(reply.plan_keys), "verifier_rejections": len(trace.get("verifier", [])),
            "fallback": trace.get("fallback_reason"), "pii_found": len(trace.get("pii_found", [])),
            "prompt_versions": dict(trace.get("prompt_versions", {})), "cached": turn.cached,
        }  # fmt: skip
        with (
            propagate_attributes(
                user_id=turn.actor_hash, session_id=turn.thread_id, tags=tags, metadata=attributes,
                trace_name=f"{turn.bot} turn",
            ),
            self._client.start_as_current_observation(
                name="turn", as_type="agent", input=turn.question, output=reply.markdown, metadata=metadata
            ) as root,
        ):  # fmt: skip
            for call in _usage(reply):
                cost = call.get("cost_usd")
                details: dict[str, Any] = {
                    "name": str(call.get("kind", "chat")), "model": str(call.get("model", "")),
                    "usage_details": {
                        "input": int(call.get("prompt_tokens") or 0),
                        "output": int(call.get("completion_tokens") or 0),
                        "total": int(call.get("total_tokens") or 0),
                    },
                    "cost_details": {"total": float(cost)} if cost is not None else None,
                    "metadata": {"latency_ms": call.get("latency_ms")},
                }  # fmt: skip
                if call.get("kind") == "embeddings":
                    root.start_observation(as_type="embedding", **details).end()
                else:
                    root.start_observation(as_type="generation", **details).end()
            for tool in trace.get("tools", []):
                root.start_observation(
                    name=str(tool.get("name")), as_type="tool", metadata={"ok": tool.get("ok")}
                ).end()


def build_tracer(env: EnvSettings) -> TurnTracer:
    if not env.langfuse_enabled:
        return NoTracing()
    try:
        return LangfuseTracer.from_env(env)
    except Exception:
        log.warning("langfuse is configured but couldn't start; tracing is off", exc_info=True,
                    extra={"event": "trace_failed"})  # fmt: skip
        return NoTracing()


def flushed_after(events: Iterator[str], tracer: TurnTracer) -> Iterator[str]:
    """Relay a response stream, then flush the tracer: the turn's trace goes out after the user has the answer
    (and before Lambda freezes the process)."""
    try:
        yield from events
    finally:
        try:
            tracer.flush()
        except Exception:
            log.warning("trace flush failed", exc_info=True, extra={"event": "trace_failed"})
