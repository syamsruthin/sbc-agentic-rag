"""Server-sent events for a chat turn (docs/aws/MIGRATION_PLAN.md section 6.2).

The agent runs in a worker thread; this generator relays its progress as ``status`` events, sends a keepalive
comment whenever ten seconds pass with nothing to say (so CloudFront and the browser keep the stream open),
and ends with exactly one of ``answer``, ``clarify`` or ``error``, then ``done``. Only node names and the
verified, rendered reply cross the thread boundary: nothing the model typed is streamed before the verifier
has passed it.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

from sbc_agent.graph import Progress, Reply

log = logging.getLogger(__name__)

KEEPALIVE_SECONDS = 10.0
TURN_TIMEOUT_SECONDS = 55.0
"""Under the Lambda timeout (60 s); the graph's own wall-clock budget is 25 s."""

NEXT_STAGE = {
    "ingress": ("classify", "Understanding your question…"),
    "classify": ("resolve", "Finding the plan…"),
    "resolve": ("answer", "Reading the SBC and checking every number…"),
}
"""After a node finishes, what happens next (and what the user is told)."""

_END = object()


def event(name: str, data: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


def reply_payload(reply: Reply) -> tuple[str, dict[str, Any]]:
    """The SSE event name and body for a finished turn."""
    if reply.kind == "clarify" and reply.clarification is not None:
        c = reply.clarification
        return "clarify", {
            "question": c.question, "open_plan_picker": c.open_plan_picker,
            "options": [{"label": o.label, "plan_key": o.plan_key} for o in c.options],
        }  # fmt: skip
    return "answer", {
        "kind": reply.kind, "markdown": reply.markdown, "plan_keys": list(reply.plan_keys),
        "citations": [dict(c) for c in reply.citations],
    }  # fmt: skip


def stream_turn(
    run: Callable[[], Iterator[Progress | Reply]],
    *,
    thread_id: str,
    request_id: str,
    on_reply: Callable[[Reply], None] | None = None,
    keepalive_seconds: float = KEEPALIVE_SECONDS,
    timeout_seconds: float = TURN_TIMEOUT_SECONDS,
) -> Iterator[str]:
    """Run ``run()`` in a worker thread and yield the SSE stream for it."""
    items: queue.Queue[object] = queue.Queue()

    def worker() -> None:
        try:
            for item in run():
                items.put(item)
        except Exception as exc:  # reported to the client as a generic error, logged in full
            items.put(exc)
        finally:
            items.put(_END)

    threading.Thread(target=worker, name=f"turn-{request_id}", daemon=True).start()
    started = time.monotonic()
    yield event("thread", {"thread_id": thread_id, "request_id": request_id})
    try:
        while True:
            try:
                item = items.get(timeout=keepalive_seconds)
            except queue.Empty:
                if time.monotonic() - started > timeout_seconds:
                    log.error("turn timed out", extra={"request_id": request_id})
                    yield event(
                        "error", {"code": "timeout", "message": "That took too long. Please try again."}
                    )
                    return
                yield ": keepalive\n\n"
                continue
            if item is _END:
                return
            if isinstance(item, Exception):
                log.error("turn failed", exc_info=item, extra={"request_id": request_id})
                yield event(
                    "error", {"code": "internal", "message": "Something went wrong. Please try again."}
                )
                return
            if isinstance(item, Progress) and item.node in NEXT_STAGE:
                stage, text = NEXT_STAGE[item.node]
                yield event("status", {"stage": stage, "text": text})
            elif isinstance(item, Reply):
                if on_reply is not None:
                    on_reply(item)
                name, body = reply_payload(item)
                yield event(name, body)
    finally:
        yield event("done", {})
