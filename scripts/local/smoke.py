"""End-to-end smoke test of the local stack: every route and guard, through the edge (LOCAL_PLAN.md L9).

Run it with scripts/local/smoke.sh (which exports the stack's settings). The API must be up, natively
(scripts/local/api.sh) or as the image (up.sh --image), and in replay mode, so a run costs nothing:

    LLM_MODE=record scripts/local/api.sh   ->  scripts/local/smoke.sh --record   (once: about $0.06)
    LLM_MODE=replay scripts/local/api.sh   ->  scripts/local/smoke.sh            (every run after: $0)

It refuses a live API unless given --allow-live. It clears the answer cache first, so the questions go through
the model path (replayed) rather than the cache, and it puts back the kill switches and quarantine it changes.
Checks print as PASS or FAIL; the exit code is the number of failures.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import boto3
import httpx
from dotenv import dotenv_values

REPO = Path(__file__).resolve().parents[2]
EDGE = f"http://localhost:{os.environ.get('SBC_PORT_EDGE', '8088')}"
API = f"http://localhost:{os.environ.get('SBC_PORT_API', '8080')}"

BTCU8 = "2027:166713189246"  # Blue Access PPO Option 52 with Rx Option 7 (OH Small Group); preview-only SBC
SHOPPER_PLAN = "2027:166711989912"  # 01M7, Anthem Bronze Pathway 11700 HSA (OH Individual)
PREVIEW_ONLY = "2027:166713509987"  # A3682 (MO): a preview-only SBC, fetched through the portal and cached
QUESTION = "What is the emergency room copay on BTCU8?"
FOLLOW_UP = "And for urgent care?"
AMBIGUOUS = "What is the deductible on Blue Access PPO Option 52?"
COMPARISON = "Compare the emergency room copays of BTCU8 and 0JF1."
SHOPPER_QUESTION = "What does a primary care visit cost?"


@dataclass
class Results:
    passed: int = 0
    failed: list[str] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        print(f"{'PASS' if ok else 'FAIL'}  {name}{f'  ({detail})' if detail and not ok else ''}", flush=True)
        if ok:
            self.passed += 1
        else:
            self.failed.append(name)
        return ok

    def attempt(self, name: str, fn: Callable[[], tuple[bool, str]]) -> None:
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        self.check(name, ok, detail)


# ------------------------------------------------------------------ helpers


def token(user: str) -> str:
    password = dotenv_values(REPO / ".local" / "users.env").get(user)
    if not password:
        raise SystemExit(f"no password for {user} in .local/users.env (run scripts/local/up.sh)")
    idp = boto3.Session(region_name=os.environ["AWS_REGION"]).client("cognito-idp")
    result = idp.admin_initiate_auth(
        UserPoolId=os.environ["COGNITO_USER_POOL_ID"],
        ClientId=os.environ["COGNITO_APP_CLIENT_ID"],
        AuthFlow="ADMIN_USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": user, "PASSWORD": password},
    )
    return str(result["AuthenticationResult"]["AccessToken"])


def auth(tok: str) -> dict[str, str]:
    return {"X-Sbc-Authorization": f"Bearer {tok}"}


def events(response: httpx.Response) -> Iterator[tuple[str, dict[str, Any]]]:
    name = ""
    for line in response.text.splitlines():
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: ") and name:
            yield name, json.loads(line[6:])
            name = ""


def chat(
    client: httpx.Client, tok: str, bot: str, body: dict[str, Any], path: str = "chat"
) -> dict[str, Any]:
    """One turn: ``{"events": [...], "thread_id", "request_id", "final": (name, data)}``."""
    r = client.post(f"/api/{bot}/{path}", json=body, headers=auth(tok), timeout=120)
    r.raise_for_status()
    evs = list(events(r))
    first = next((d for n, d in evs if n == "thread"), {})
    final: tuple[str, dict[str, Any]] = next(((n, d) for n, d in reversed(evs) if n != "done"), ("", {}))
    return {
        "events": evs,
        "thread_id": first.get("thread_id") or final[1].get("thread_id"),
        "request_id": first.get("request_id"),
        "final": final,
    }


def answered(turn: dict[str, Any]) -> tuple[bool, str]:
    name, data = turn["final"]
    ok = name == "answer" and data.get("kind") == "answer" and bool(data.get("citations"))
    detail = data.get("markdown", "")[:120] if name == "answer" else f"{name}: {json.dumps(data)[:160]}"
    if ok and "replay" in os.environ.get("SMOKE_EXPECT", "") and (data.get("details") or {}).get("cost_usd"):
        return False, f"cost {data['details']['cost_usd']} in replay mode"
    return ok, detail


def clear_answer_cache() -> int:
    ddb = boto3.Session(region_name=os.environ["AWS_REGION"]).client("dynamodb")
    table, n = os.environ["DDB_TABLE_APP"], 0
    pages = ddb.get_paginator("scan").paginate(
        TableName=table,
        FilterExpression="begins_with(PK, :p)",
        ExpressionAttributeValues={":p": {"S": "CACHE#"}},
    )
    for page in pages:
        for item in page["Items"]:
            ddb.delete_item(TableName=table, Key={"PK": item["PK"], "SK": item["SK"]})
            n += 1
    return n


# ------------------------------------------------------------------ the checks


def run(mode: str, allow_live: bool) -> Results:
    res = Results()
    client = httpx.Client(base_url=EDGE, timeout=30)

    # --- health and mode
    deep = client.get("/api/healthz", params={"deep": 1}).json()["deep"]
    llm = deep.get("llm", "live")
    version = client.get("/api/healthz").json()["bundle_version"]
    print(f"API: bundle {version}, llm {llm}, stores {deep['stores']}")
    if llm == "live" and not allow_live:
        raise SystemExit(
            "the API is in live mode: start it with LLM_MODE=replay (or pass --allow-live; it costs)"
        )
    if mode == "record" and llm != "record":
        raise SystemExit("--record needs the API in record mode: LLM_MODE=record scripts/local/api.sh")
    if mode == "replay" and llm != "replay":
        raise SystemExit(
            "the API isn't in replay mode: LLM_MODE=replay scripts/local/api.sh (or pass --record)"
        )
    os.environ["SMOKE_EXPECT"] = llm
    res.check("health: stores on DynamoDB", deep["stores"] == "dynamodb", str(deep["stores"]))
    res.check("health: both bots ready", set(deep["bots"].values()) == {"ready"}, str(deep["bots"]))
    res.check("health: bundle has PDF sources", (deep.get("plan_source") or {}).get("missing") == 0)
    res.check("health: hybrid search", deep["search"] == "hybrid", deep["search"])

    # --- the edge (CloudFront stand-in)
    for path in ("/", "/broker/", "/shopper/", "/broker/a/client/route", "/landing/demo.json"):
        r = client.get(path)
        res.check(f"edge: GET {path} -> 200", r.status_code == 200, str(r.status_code))
    r = client.get("/broker", follow_redirects=False)
    res.check(
        "edge: /broker -> 301 /broker/", r.status_code == 301 and r.headers.get("location") == "/broker/"
    )
    r = client.get("/")
    res.check(
        "edge: security headers",
        "frame-ancestors 'none'" in r.headers.get("content-security-policy", "")
        and r.headers.get("x-frame-options") == "DENY",
    )
    r = client.get("/api/healthz")
    res.check(
        "edge: /api uncached, with a request ID",
        r.headers.get("cache-control") == "no-store" and len(r.headers.get("x-request-id", "")) >= 16,
    )
    res.check(
        "demo login: published", client.get("/landing/demo.json").json().get("username") == "demo-broker"
    )
    r = httpx.post(f"{API}/events", json={"source": "aws.scheduler"})
    res.check("warmer: POST /events on the API -> 204", r.status_code == 204, str(r.status_code))

    # --- sign-in and scopes
    broker, admin, demo = token("broker"), token("admin"), token("demo-broker")
    res.check("auth: no token -> 401", client.get("/api/broker/threads").status_code == 401)
    res.check(
        "auth: tampered token -> 401",
        client.get("/api/broker/threads", headers=auth(broker[:-2] + "xx")).status_code == 401,
    )
    res.check(
        "auth: token in Authorization (dropped by the edge) -> 401",
        client.get("/api/broker/threads", headers={"Authorization": f"Bearer {broker}"}).status_code == 401,
    )
    res.check(
        "auth: broker token on the public bot -> 401",
        client.get("/api/public/plans/search", params={"q": "gold"}, headers=auth(broker)).status_code == 401,
    )
    for who, tok, want in (("broker", broker, 403), ("demo-broker", demo, 403), ("admin", admin, 204)):
        r = client.post(
            "/api/admin/kill-switch", json={"bot": "public", "llm_enabled": True}, headers=auth(tok)
        )
        res.check(f"admin route as {who} -> {want}", r.status_code == want, str(r.status_code))

    # --- plans
    r = client.get("/api/broker/plans/search", params={"q": "BTCU8", "limit": 3}, headers=auth(broker))
    plans = r.json().get("plans", [])
    res.check("plans: broker search finds BTCU8", any(p["plan_key"] == BTCU8 for p in plans), str(len(plans)))
    r = client.get(f"/api/broker/plans/{BTCU8}", headers=auth(broker))
    res.check(
        "plans: details link the preview-only SBC to the proxy",
        r.status_code == 200 and "/api/sbc/" in json.dumps(r.json()),
    )

    # --- the public session (Turnstile test secret)
    r = client.post("/api/public/session", json={"turnstile_token": "XXXX.DUMMY.TOKEN.XXXX", "state": "OH"})
    session = r.json().get("token", "") if r.status_code == 200 else ""
    res.check("public: Turnstile session opens", bool(session), f"{r.status_code} {r.text[:120]}")
    r = client.get("/api/public/plans/search", params={"q": "bronze", "limit": 20}, headers=auth(session))
    found = r.json().get("plans", []) if r.status_code == 200 else []
    res.check(
        "public: only Ohio Individual plans",
        bool(found) and all(p["state"] == "OH" and p["market"] == "Individual" for p in found),
        f"{len(found)} plans",
    )
    res.check(
        "public: session token on the broker bot -> 401",
        client.get("/api/broker/threads", headers=auth(session)).status_code == 401,
    )

    # --- answers (replayed from the recordings in replay mode)
    print(f"answer cache cleared: {clear_answer_cache()} items")
    turn = chat(client, broker, "broker", {"message": QUESTION})
    res.attempt("chat: broker fast-path answer with citations", lambda: answered(turn))
    thread = turn["thread_id"]
    follow = chat(client, broker, "broker", {"thread_id": thread, "message": FOLLOW_UP})
    res.attempt("chat: follow-up in the same thread", lambda: answered(follow))
    res.attempt(
        "chat: comparison through the agent loop",
        lambda: answered(chat(client, broker, "broker", {"message": COMPARISON})),
    )
    clarify = chat(client, broker, "broker", {"message": AMBIGUOUS})
    name, data = clarify["final"]
    options = [o for o in data.get("options", []) if o.get("plan_key")]
    if res.check(
        "chat: an ambiguous plan gets a clarifying question",
        name == "clarify" and bool(options),
        f"{name}: {json.dumps(data)[:160]}",
    ):
        choice = next((o for o in options if o["plan_key"] == BTCU8), options[0])
        chosen = chat(
            client,
            broker,
            "broker",
            {"thread_id": clarify["thread_id"], "plan_key": choice["plan_key"]},
            path="chat/choose",
        )
        res.attempt("chat: choosing a plan answers", lambda: answered(chosen))
    session_turn = chat(
        client, session, "public", {"message": SHOPPER_QUESTION, "ui_selected": [SHOPPER_PLAN]}
    )
    res.attempt("chat: Plan Shopper answer", lambda: answered(session_turn))
    again = chat(client, broker, "broker", {"message": QUESTION})
    res.check(
        "cache: a repeated first question is served from the cache", again["final"][1].get("cached") is True
    )

    # --- threads, feedback, ownership
    listed = client.get("/api/broker/threads", headers=auth(broker)).json().get("threads", [])
    res.check("threads: the conversation is listed", any(t["thread_id"] == thread for t in listed))
    r = client.post(
        "/api/broker/feedback",
        headers=auth(broker),
        json={"thread_id": thread, "request_id": turn["request_id"], "rating": "up"},
    )
    res.check("feedback: stored", r.status_code == 204, f"{r.status_code} {r.text[:120]}")
    # Another broker (admin: the demo account's rate limit would answer before the ownership check).
    r = client.post("/api/broker/chat", headers=auth(admin), json={"thread_id": thread, "message": "hi"})
    res.check("threads: another user can't continue it -> 404", r.status_code == 404, str(r.status_code))
    if turn["request_id"]:
        found_log = subprocess.run(  # noqa: S603 - a fixed local script and a request ID from our own API
            [str(REPO / "scripts/local/find-request.sh"), turn["request_id"]],
            capture_output=True,
            text=True,
            check=False,
        )
        res.check(
            "logs: the turn is found by its request ID",
            '"event": "turn"' in found_log.stdout or '"turn"' in found_log.stdout,
            found_log.stderr[:120],
        )

    # --- guards: quarantine, kill switch, the demo limit
    q = {"plan_keys": [BTCU8], "reason": "smoke test"}
    client.post("/api/admin/quarantine", json={**q, "action": "add"}, headers=auth(admin)).raise_for_status()
    try:
        held = chat(client, broker, "broker", {"message": QUESTION})
        res.check(
            "quarantine: answers about the plan are withheld (cached ones too)",
            held["final"][1].get("kind") == "message" and "cached" not in held["final"][1],
            held["final"][1].get("markdown", "")[:100],
        )
    finally:
        client.post(
            "/api/admin/quarantine", json={**q, "action": "remove"}, headers=auth(admin)
        ).raise_for_status()
    switch = {"bot": "broker"}
    client.post(
        "/api/admin/kill-switch", json={**switch, "llm_enabled": False}, headers=auth(admin)
    ).raise_for_status()
    try:
        off = chat(client, broker, "broker", {"message": "kill switch check"})
        res.check(
            "kill switch: the bot stops answering",
            off["final"][1].get("kind") == "message"
            and "temporarily unavailable" in off["final"][1].get("markdown", ""),
        )
        limited = False
        for _ in range(7):  # the demo account: 5 turns a minute; refused turns still count
            if chat(client, demo, "broker", {"message": "demo limit check"})["final"][0] == "limit":
                limited = True
                break
        res.check("demo account: its rate limit applies", limited)
    finally:
        client.post(
            "/api/admin/kill-switch", json={**switch, "llm_enabled": True}, headers=auth(admin)
        ).raise_for_status()

    # --- the SBC PDF proxy
    r = client.get(f"/api/sbc/{PREVIEW_ONLY}.pdf", timeout=60)
    res.check(
        "pdf: preview-only SBC served", r.status_code == 200 and r.content[:5] == b"%PDF-", str(r.status_code)
    )
    r = client.get("/api/sbc/2027:166712401476.pdf", follow_redirects=False)
    res.check("pdf: a direct-link SBC redirects", r.status_code == 302, str(r.status_code))
    res.check("pdf: unknown plan -> 404", client.get("/api/sbc/2027:999999999999.pdf").status_code == 404)
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="End-to-end smoke test of the local stack (docs/local/RUNBOOK.md)."
    )
    ap.add_argument("--record", action="store_true", help="the API is in record mode: save the model calls")
    ap.add_argument("--allow-live", action="store_true", help="run against a live API (costs about $0.06)")
    args = ap.parse_args(argv)
    started = time.monotonic()
    res = run("record" if args.record else ("live" if args.allow_live else "replay"), args.allow_live)
    print(f"\n{res.passed} passed, {len(res.failed)} failed in {time.monotonic() - started:.0f} s")
    for name in res.failed:
        print(f"  FAILED: {name}")
    return len(res.failed)


if __name__ == "__main__":
    sys.exit(main())
