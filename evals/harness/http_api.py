"""The generation eval through a running API over HTTP (docs/aws/MIGRATION_PLAN.md WP13; LOCAL_PLAN.md L9).

Golden items go to ``POST /api/{bot}/chat`` as SSE turns, exactly as the apps send them (bearer token in
``X-Sbc-Authorization``), and the final event is scored with :func:`evals.harness.generation.score`. It reuses
that module's runner, summary, gates and report.

What HTTP can't show: the turn's internal trace. So ``no_ungrounded_numbers`` and ``tool_selection`` (which
need the drafts and the tool arguments) are reported as not scored (None), never as passed. Behavior, exact
values, the disclaimer, the plan filter and confident wrong-plan answers are scored as in the in-process eval.

    # local stack, API in replay mode (no cost once recorded; record once with LLM_MODE=record)
    scripts/local/eval-http.sh --sample 30
    # any API: a broker access token, and a Turnstile token the API accepts (local: any, with the test secret)
    SBC_EVAL_BROKER_TOKEN=... python -m evals.harness.http_api --base-url https://<site> \
        --golden evals/golden/0.2.0
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx

from evals.harness.generation import ItemResult, build_report, phase, run, to_markdown
from evals.harness.golden import GoldenItem, load_golden
from sbc_agent.graph import Reply
from sbc_tools.context import Clarification, ClarifyOption

NOT_SCORED_OVER_HTTP = ("no_ungrounded_numbers", "tool_selection")
MAX_LIMIT_RETRIES = 3


def sse_events(text: str) -> Iterator[tuple[str, dict[str, Any]]]:
    name = ""
    for line in text.splitlines():
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: ") and name:
            yield name, json.loads(line[6:])
            name = ""


def reply_from_events(events: list[tuple[str, dict[str, Any]]]) -> tuple[Reply, str | None]:
    """The turn's :class:`Reply` from its SSE events, with the trace rebuilt from the answer's details, and
    the server's thread ID."""
    thread_id = next((d.get("thread_id") for n, d in events if n == "thread"), None)
    fallback: tuple[str, dict[str, Any]] = ("error", {})
    name, data = next(((n, d) for n, d in reversed(events) if n != "done"), fallback)
    if name == "clarify":
        options = tuple(
            ClarifyOption(label=o["label"], plan_key=o.get("plan_key")) for o in data.get("options", [])
        )
        clarification = Clarification(
            question=data.get("question", ""),
            options=options,
            open_plan_picker=bool(data.get("open_plan_picker")),
        )
        return Reply("clarify", data.get("question", ""), clarification=clarification), thread_id
    if name != "answer":
        raise RuntimeError(f"turn ended with {name}: {json.dumps(data)[:200]}")
    details = data.get("details") or {}
    calls = int(details.get("llm_calls") or 0)
    usage = [{"cost_usd": details.get("cost_usd") or 0.0}] + [{"cost_usd": 0.0}] * max(calls - 1, 0)
    trace = {
        "path": details.get("path"),
        # Names only: HTTP doesn't show a tool call's arguments (tool_selection is reported as not scored).
        "tools": [{"name": t, "ok": True, "arguments": {}} for t in details.get("tools", [])],
        "usage": usage if calls else [],
    }
    reply = Reply(
        data.get("kind", "answer"), data.get("markdown", ""), tuple(data.get("citations", [])),
        tuple(data.get("plan_keys", [])), trace=trace,
    )  # fmt: skip
    return reply, thread_id or data.get("thread_id")


class HttpAgent:
    """Stands in for :class:`sbc_agent.graph.Agent` in the eval's runner: ``ask`` is one HTTP turn."""

    def __init__(
        self,
        client: httpx.Client,
        bot: str,
        token_for: Callable[[str | None], str],
        sleep: Callable[[float], None],
    ) -> None:
        self._client = client
        self._bot = bot
        self._token_for = token_for
        self._sleep = sleep
        self._threads: dict[str, str] = {}
        self._lock = threading.Lock()

    def ask(
        self,
        thread_id: str,
        message: str,
        *,
        ui_selected: tuple[str, ...] = (),
        user_state: str | None = None,
    ) -> Reply:
        thread = thread_id  # the eval's own label for the conversation; the server picks the real ID
        body: dict[str, Any] = {"message": message, "ui_selected": list(ui_selected)}
        with self._lock:
            server_thread = self._threads.get(thread)
        if server_thread:
            body["thread_id"] = server_thread
        headers = {"X-Sbc-Authorization": f"Bearer {self._token_for(user_state)}"}
        for _ in range(MAX_LIMIT_RETRIES + 1):
            r = self._client.post(f"/api/{self._bot}/chat", json=body, headers=headers, timeout=180)
            r.raise_for_status()
            events = list(sse_events(r.text))
            limit = next((d for n, d in events if n == "limit"), None)
            if limit is None:
                break
            self._sleep(float(limit.get("retry_after_s") or 30) + 1)  # a rate limit: wait it out, then retry
        else:
            raise RuntimeError("still rate-limited after retries")
        reply, server_thread = reply_from_events(events)
        if server_thread:
            with self._lock:
                self._threads[thread] = server_thread
        return reply


def http_agents(
    base_url: str, broker_token: str, turnstile_token: str, sleep: Callable[[float], None] = time.sleep
) -> Callable[[str], HttpAgent]:
    client = httpx.Client(base_url=base_url, timeout=60)
    sessions: dict[str, str] = {}
    lock = threading.Lock()

    def public_session(state: str | None) -> str:
        state = state or "OH"
        with lock:
            if state not in sessions:
                r = client.post(
                    "/api/public/session", json={"turnstile_token": turnstile_token, "state": state}
                )
                r.raise_for_status()
                sessions[state] = str(r.json()["token"])
            return sessions[state]

    agents = {
        "broker": HttpAgent(client, "broker", lambda _state: broker_token, sleep),
        "public": HttpAgent(client, "public", public_session, sleep),
    }
    return lambda bot: agents[bot]


def unscored_over_http(results: list[ItemResult]) -> list[ItemResult]:
    for result in results:
        for check in NOT_SCORED_OVER_HTTP:
            if check in result.checks:
                result.checks[check] = None
        result.ungrounded = []
    return results


def unscored_in_report(report: dict[str, Any]) -> dict[str, Any]:
    """Show the ungrounded-number count and its hard gate as not scored ("–"), never as a pass, over HTTP."""
    for group in report.get("groups", {}).values():
        if isinstance(group, dict) and "ungrounded_numbers" in group:
            group["ungrounded_numbers"] = None
    for gate in report.get("gates", []):
        if "ungrounded" in gate["gate"]:
            gate["value"], gate["passed"] = None, None
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="The generation eval through a running API, over HTTP (WP13).")
    ap.add_argument("--base-url", default=os.environ.get("SBC_EVAL_BASE_URL", "http://localhost:8088"))
    ap.add_argument("--golden", type=Path, required=True)
    ap.add_argument("--phase", choices=["1", "2", "all"], default="1")
    ap.add_argument("--category", action="append")
    ap.add_argument("--id", action="append")
    ap.add_argument("--sample", type=int, help="a seeded random sample of this many items")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--max-cost", type=float, default=2.0, help="stop when reported model cost exceeds this")
    ap.add_argument("--turnstile-token", default="XXXX.DUMMY.TOKEN.XXXX", help="what the session route gets")
    ap.add_argument("--out", type=Path, default=Path("evals/reports"))
    ap.add_argument("--fail-on-hard-gates", action="store_true")
    args = ap.parse_args(argv)
    token = os.environ.get("SBC_EVAL_BROKER_TOKEN", "")
    if not token:
        raise SystemExit(
            "set SBC_EVAL_BROKER_TOKEN to a broker access token (locally: scripts/local/eval-http.sh)"
        )

    items: list[GoldenItem] = load_golden(args.golden)
    if args.phase != "all":
        items = [i for i in items if phase(i) == int(args.phase)]
    if args.category:
        items = [i for i in items if i.category in args.category]
    if args.id:
        items = [i for i in items if i.id in args.id]
    if args.sample:
        items = random.Random(0).sample(items, min(args.sample, len(items)))  # noqa: S311 - not security
    bundle_version = httpx.get(f"{args.base_url}/api/healthz", timeout=30).json()["bundle_version"]
    print(f"running {len(items)} items against {args.base_url} (bundle {bundle_version})", file=sys.stderr)
    results = run(
        items, http_agents(args.base_url, token, args.turnstile_token), workers=args.workers,
        max_cost=args.max_cost, log=lambda m: print(m, file=sys.stderr),
    )  # fmt: skip
    report = unscored_in_report(build_report(unscored_over_http(results), bundle_version, args.golden.name))
    report["run_id"] = f"http-{report['run_id']}-{uuid.uuid4().hex[:4]}"
    report["over_http"] = {"base_url": args.base_url, "not_scored": list(NOT_SCORED_OVER_HTTP)}
    args.out.mkdir(parents=True, exist_ok=True)
    json_path = args.out / f"{report['run_id']}.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    json_path.with_suffix(".md").write_text(to_markdown(report), encoding="utf-8")
    print(to_markdown(report))
    print(f"wrote {json_path}", file=sys.stderr)
    hard_failed = [g["gate"] for g in report["gates"] if g["hard"] and g["passed"] is False]
    return 1 if hard_failed and args.fail_on_hard_gates else 0


if __name__ == "__main__":
    raise SystemExit(main())
