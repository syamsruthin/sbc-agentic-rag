"""Generation eval: golden items through the whole agent graph (PLAN.md sections 5.2, 5.3 and 11).

Each item's user turns are sent to the bot named by the item (``both`` runs as the broker), with its UI
selection and, for the public bot, its state. The last reply is scored with deterministic checks only:

| Check | Gate | How |
|---|---|---|
| Numeric exact match | hard, 100% | each ``expected_values[].verbatim`` is in the answer ($1,500 = $1500) |
| Ungrounded numbers | hard, 0 | a digit in the answer body that no expanded placeholder accounts for |
| Plan filter | hard, 100% | the plans whose values the answer shows are all gold plans |
| Confident wrong plan | hard, 0 | an answer about a non-gold plan, or an answer where gold says clarify |
| Correct behavior | 90% | answer, clarify or refuse matches gold; ``partial`` counts as answered |
| Tool selection | 90% | expected tools were called with at least the gold arguments; no forbidden tool |
| Disclaimer, must include, must not include | reported | substring checks |

Faithfulness and citation accuracy for prose need the LLM judge, which joins once it is calibrated (PLAN.md
5.4). Items that need Phase 2 features (``estimate_cost``, ``rank_plans``, multi-turn session references) are
run and reported separately; checkpoint 3 is gated on the rest (PLAN.md section 11).

Usage::

    uv run python -m evals.harness.generation --bundle bundles/facts-2027.1.sqlite \\
        --golden evals/golden/0.2.0 --limit 20

Needs ``OPENROUTER_API_KEY_BROKER`` (and ``_PUBLIC`` for public items) and an embedded bundle. A full run of
the gating set makes about 1,000 model calls (roughly $5-15); ``--max-cost`` stops it early.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import sys
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from evals.harness.golden import GoldenItem, load_golden
from sbc_agent.graph import ABUSE, OUT_OF_SCOPE, TOO_LONG, Agent, Reply

PHASE2_TOOLS = frozenset({"estimate_cost", "rank_plans"})
REFUSALS = frozenset({OUT_OF_SCOPE, ABUSE, TOO_LONG})
DISCLAIMER_MARK = "This summary isn't a guarantee of coverage or cost"
Behavior = Literal["answer", "clarify", "refuse", "no_answer"]
AgentFor = Callable[[str], Agent]
"""Bot name ("broker" or "public") -> an agent; called once per item."""

_CITATION_MARK = re.compile(r"\[\d+\]")
_DIGIT = re.compile(r"\d")


def phase(item: GoldenItem) -> int:
    """2 when the item needs a Phase 2 feature, else 1."""
    if item.category == "multi_turn":
        return 2
    return 2 if any(t.tool in PHASE2_TOOLS for t in item.gold.expected_tools) else 1


def normalize(text: str) -> str:
    """PLAN.md 5.2: only ``$1,500`` = ``$1500`` and whitespace."""
    return " ".join(re.sub(r"(?<=\d),(?=\d{3})", "", text).split())


def behavior_of(reply: Reply) -> Behavior:
    if reply.kind == "clarify":
        return "clarify"
    if reply.kind == "message":
        return "refuse" if reply.markdown in REFUSALS else "no_answer"
    return "answer"


def ungrounded(trace: dict[str, Any]) -> list[str]:
    """Digits in the answer body that no expanded placeholder accounts for."""
    grounding = trace.get("grounding") or {}
    body = _CITATION_MARK.sub(" ", grounding.get("body", ""))
    for text in sorted(grounding.get("grounded", []), key=len, reverse=True):
        body = body.replace(text, " ")
    return [body[max(0, m.start() - 20) : m.end() + 20] for m in _DIGIT.finditer(body)]


def _args_match(expected: dict[str, object], called: dict[str, Any]) -> bool:
    for key, want in expected.items():
        got = called.get(key)
        if isinstance(want, list):
            if not isinstance(got, list) or not set(map(str, want)) <= set(map(str, got)):
                return False
        elif got != want:
            return False
    return True


def tool_selection(item: GoldenItem, tools: Sequence[dict[str, Any]], clarified: bool) -> bool | None:
    """Expected tools called with at least the gold arguments, and no forbidden tool. A clarifying question
    the graph asked in code (an ambiguous plan) counts as ``ask_clarifying_question``."""
    gold = item.gold
    if not gold.expected_tools and not gold.forbidden_tools:
        return None
    ok_calls = [t for t in tools if t.get("ok")]
    if clarified:
        ok_calls.append({"name": "ask_clarifying_question", "arguments": {}, "ok": True})
    for expected in gold.expected_tools:
        if not any(
            t["name"] == expected.tool and _args_match(expected.args_subset, t["arguments"]) for t in ok_calls
        ):
            return False
    return not any(t["name"] in gold.forbidden_tools for t in tools)


@dataclass
class ItemResult:
    id: str
    category: str
    bot: str
    phase: int
    gating: bool
    expected: str
    behavior: Behavior
    checks: dict[str, bool | None] = field(default_factory=dict)
    ungrounded: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    plan_keys: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    path: str | None = None
    fallback: str | None = None
    rejected_drafts: int = 0
    rejections: list[str] = field(default_factory=list)
    """The verifier's reasons for the rejected drafts (at most four)."""
    classification: dict[str, Any] = field(default_factory=dict)
    llm_calls: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    answer: str = ""
    error: bool = False


def score(item: GoldenItem, reply: Reply, seconds: float) -> ItemResult:
    gold = item.gold
    trace = reply.trace
    usage = [*trace.get("usage", []), *([trace["classify_usage"]] if trace.get("classify_usage") else [])]
    result = ItemResult(
        id=item.id, category=item.category, bot=item.bot, phase=phase(item), gating=item.gating,
        expected=gold.expected_behavior, behavior=behavior_of(reply), plan_keys=list(reply.plan_keys),
        tools=[t["name"] + ("" if t.get("ok") else "!") for t in trace.get("tools", [])],
        path=trace.get("path"),
        fallback=trace.get("fallback_reason"), rejected_drafts=len(trace.get("verifier", [])),
        rejections=[v for draft in trace.get("verifier", []) for v in draft][:4],
        classification=trace.get("classification") or {},
        llm_calls=len(usage), cost_usd=sum(u.get("cost_usd") or 0 for u in usage), seconds=seconds,
        answer=reply.markdown,
    )  # fmt: skip
    answered = result.behavior == "answer"
    expects_answer = gold.expected_behavior in ("answer", "partial")
    text = normalize(reply.markdown)
    checks = result.checks
    checks["behavior"] = result.behavior == gold.expected_behavior or (
        gold.expected_behavior == "partial" and answered
    )
    if expects_answer and gold.expected_values:
        missing = [v.verbatim for v in gold.expected_values if normalize(v.verbatim) not in text]
        checks["numeric_exact_match"] = answered and not missing
        result.problems += [f"missing value: {v}" for v in missing[:3]]
    if answered:
        result.ungrounded = ungrounded(trace)
        checks["no_ungrounded_numbers"] = not result.ungrounded
        checks["disclaimer"] = DISCLAIMER_MARK in reply.markdown
        if gold.plan_keys:
            checks["plan_filter"] = set(reply.plan_keys) <= set(gold.plan_keys)
    # A wrong plan: values shown for a plan gold doesn't name, or an answer about some plan when gold says the
    # plan itself is unclear (clarify with no gold plan). Clarifying something else (an amount) isn't one.
    checks["confident_wrong_plan"] = (
        answered
        and bool(reply.plan_keys)
        and (
            (gold.expected_behavior == "clarify" and not gold.plan_keys)
            or (bool(gold.plan_keys) and not set(reply.plan_keys) <= set(gold.plan_keys))
        )
    )
    if expects_answer and gold.must_include:
        absent = [m for m in gold.must_include if normalize(m) not in text]
        checks["must_include"] = answered and not absent
        result.problems += [f"missing text: {m}" for m in absent[:3]]
    if gold.must_not_include:
        present = [m for m in gold.must_not_include if m.lower() in reply.markdown.lower()]
        checks["must_not_include"] = not present
        result.problems += [f"forbidden text: {m}" for m in present]
    if (
        gold.expected_behavior == "clarify"
        and result.behavior == "clarify"
        and gold.clarify_options_must_include
    ):
        options = " ".join(o.label for o in (reply.clarification.options if reply.clarification else ()))
        checks["clarify_options"] = all(code in options for code in gold.clarify_options_must_include)
    checks["tool_selection"] = tool_selection(item, trace.get("tools", []), result.behavior == "clarify")
    if not checks["behavior"]:
        result.problems.insert(0, f"behavior {result.behavior}, gold {gold.expected_behavior}")
    return result


def run_item(item: GoldenItem, agent_for: AgentFor) -> ItemResult:
    bot = "public" if item.bot == "public" else "broker"
    agent = agent_for(bot)
    thread = f"{item.id}-{uuid.uuid4().hex[:8]}"
    selected = item.context.get("ui_selected_plan_keys") or []
    ui = tuple(str(k) for k in selected) if isinstance(selected, list) else ()
    state = str(item.context["state"]) if bot == "public" and item.context.get("state") else None
    start = time.monotonic()
    reply: Reply | None = None
    for turn in item.turns:
        if turn.role == "user":
            reply = agent.ask(thread, turn.content, ui_selected=ui, user_state=state)
    assert reply is not None  # noqa: S101 - the schema requires a final user turn
    return score(item, reply, time.monotonic() - start)


# ------------------------------------------------------------------ summary and report


def _rate(results: Iterable[ItemResult], check: str) -> tuple[float | None, int]:
    values = [r.checks.get(check) for r in results]
    scored = [v for v in values if v is not None]
    return (sum(scored) / len(scored) if scored else None), len(scored)


def summarize(results: Sequence[ItemResult]) -> dict[str, Any]:
    out: dict[str, Any] = {"items": len(results)}
    for check in ("numeric_exact_match", "plan_filter", "behavior", "tool_selection", "disclaimer",
                  "must_include", "must_not_include", "clarify_options"):  # fmt: skip
        rate, n = _rate(results, check)
        out[check] = {"rate": rate, "scored": n}
    out["ungrounded_numbers"] = sum(len(r.ungrounded) for r in results)
    out["confident_wrong_plan"] = sum(bool(r.checks.get("confident_wrong_plan")) for r in results)
    out["errors"] = sum(r.error for r in results)
    calls = sorted(r.llm_calls for r in results) or [0]
    out["llm_calls_p50"] = statistics.median(calls)
    out["llm_calls_p95"] = calls[min(len(calls) - 1, int(0.95 * len(calls)))]
    out["cost_usd"] = round(sum(r.cost_usd for r in results), 4)
    out["cost_per_item_usd"] = round(out["cost_usd"] / len(results), 5) if results else 0
    out["fallback_rate"] = sum(r.fallback is not None for r in results) / len(results) if results else 0
    out["fast_path_rate"] = sum(r.path == "fast" for r in results) / len(results) if results else 0
    secs = sorted(r.seconds for r in results) or [0.0]
    out["seconds_p50"] = round(statistics.median(secs), 1)
    out["seconds_p95"] = round(secs[min(len(secs) - 1, int(0.95 * len(secs)))], 1)
    return out


@dataclass(frozen=True)
class Gate:
    name: str
    key: str
    threshold: float
    hard: bool


GATES = (
    Gate("numeric exact match 100%", "numeric_exact_match.rate", 1.0, True),
    Gate("0 ungrounded numbers", "ungrounded_numbers", 0, True),
    Gate("plan filter 100%", "plan_filter.rate", 1.0, True),
    Gate("0 confident wrong-plan answers", "confident_wrong_plan", 0, True),
    Gate("correct behavior >= 90%", "behavior.rate", 0.90, False),
    Gate("tool selection >= 90%", "tool_selection.rate", 0.90, False),
    Gate("disclaimer 100%", "disclaimer.rate", 1.0, False),
    Gate("0 errors", "errors", 0, True),
)


def check_gates(summary: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for gate in GATES:
        value: Any = summary
        for part in gate.key.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if value is None:
            passed = None
        elif gate.key in ("ungrounded_numbers", "confident_wrong_plan", "errors"):
            passed = value <= gate.threshold
        else:
            passed = value >= gate.threshold
        out.append({"gate": gate.name, "hard": gate.hard, "value": value, "passed": passed})
    return out


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return "–" if value is None else str(value)


def to_markdown(report: dict[str, Any]) -> str:
    lines = [f"# Generation eval: bundle {report['bundle_version']}, golden {report['golden_version']}", ""]
    groups = report["groups"]
    lines += ["| Metric | " + " | ".join(groups) + " |", "|---|" + "---|" * len(groups)]
    rows = [
        ("Items", "items"), ("Numeric exact match", "numeric_exact_match.rate"),
        ("Ungrounded numbers", "ungrounded_numbers"), ("Plan filter", "plan_filter.rate"),
        ("Confident wrong plan", "confident_wrong_plan"), ("Errors", "errors"),
        ("Correct behavior", "behavior.rate"),
        ("Tool selection", "tool_selection.rate"), ("Disclaimer", "disclaimer.rate"),
        ("Must include", "must_include.rate"), ("Must not include", "must_not_include.rate"),
        ("Clarify options", "clarify_options.rate"), ("Fast path", "fast_path_rate"),
        ("Template fallback", "fallback_rate"), ("LLM calls p50 / p95", None),
        ("Seconds p50 / p95", None), ("Cost per item ($)", "cost_per_item_usd"), ("Cost ($)", "cost_usd"),
    ]  # fmt: skip
    for label, key in rows:
        cells = []
        for summary in groups.values():
            if key is None:
                pair = (
                    ("llm_calls_p50", "llm_calls_p95")
                    if label.startswith("LLM")
                    else ("seconds_p50", "seconds_p95")
                )
                cells.append(f"{_fmt(summary[pair[0]])} / {_fmt(summary[pair[1]])}")
                continue
            value: Any = summary
            for part in key.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            cells.append(_fmt(value))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += ["", "## Gates (Phase 1 gating items)", "", "| Gate | Value | Result |", "|---|---|---|"]
    for g in report["gates"]:
        result = "–" if g["passed"] is None else "pass" if g["passed"] else "**FAIL**"
        lines.append(f"| {g['gate']}{' (hard)' if g['hard'] else ''} | {_fmt(g['value'])} | {result} |")
    lines += ["", "## By category (Phase 1 gating items)", ""]
    lines += [
        "| Category | Items | Behavior | Exact match | Tool selection | Must include |",
        "|---|---|---|---|---|---|",
    ]
    for category, s in report["by_category"].items():
        lines.append(
            f"| {category} | {s['items']} | {_fmt(s['behavior']['rate'])} | "
            f"{_fmt(s['numeric_exact_match']['rate'])} | "
            f"{_fmt(s['tool_selection']['rate'])} | {_fmt(s['must_include']['rate'])} |"
        )
    failures = [r for r in report["items"] if r["phase"] == 1 and r["problems"]]
    if failures:
        lines += ["", "## Failing Phase 1 items", ""]
        for r in failures:
            lines.append(
                f"- `{r['id']}` ({r['category']}, {r['path'] or r['behavior']}): " + "; ".join(r["problems"])
            )
    return "\n".join(lines) + "\n"


def build_report(results: Sequence[ItemResult], bundle_version: str, golden_version: str) -> dict[str, Any]:
    gating = [r for r in results if r.phase == 1 and r.gating]
    phase2 = [r for r in results if r.phase == 2]
    by_category: dict[str, list[ItemResult]] = defaultdict(list)
    for r in gating:
        by_category[r.category].append(r)
    groups = {"phase1_gating": summarize(gating)}
    if phase2:
        groups["phase2_reported"] = summarize(phase2)
    return {
        "run_id": f"generation-{bundle_version}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}",
        "bundle_version": bundle_version,
        "golden_version": golden_version,
        "groups": groups,
        "gates": check_gates(groups["phase1_gating"]),
        "by_category": {c: summarize(rs) for c, rs in sorted(by_category.items())},
        "items": [asdict(r) for r in results],
    }


# ------------------------------------------------------------------ running


def run(
    items: Sequence[GoldenItem], agent_for: AgentFor, *, workers: int = 1, max_cost: float | None = None,
    log: Callable[[str], None] = lambda _: None,
) -> list[ItemResult]:  # fmt: skip
    results: list[ItemResult] = []
    spent = 0.0
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_item, item, agent_for): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # one broken item must not stop the run; it counts as failed
                result = ItemResult(
                    item.id, item.category, item.bot, phase(item), item.gating, item.gold.expected_behavior,
                    "no_answer", checks={"behavior": False}, problems=[f"error: {exc!r}"], error=True,
                )  # fmt: skip
            with lock:
                results.append(result)
                spent += result.cost_usd
                log(f"{len(results)}/{len(items)} {result.id} {result.behavior} ${spent:.2f}")
                if max_cost is not None and spent > max_cost:
                    log(f"stopping: spent ${spent:.2f} > --max-cost {max_cost}")
                    for f in futures:
                        f.cancel()
                    break
    order = {item.id: n for n, item in enumerate(items)}
    return sorted(results, key=lambda r: order[r.id])


def live_agent_factory(bundle: Path) -> AgentFor:
    """Agents on the real bundle and OpenRouter, one set per worker thread."""
    from langgraph.checkpoint.memory import InMemorySaver

    from sbc_agent.respond import Deps
    from sbc_core.config import EnvSettings, load_config
    from sbc_core.facts import SqliteFactsReader
    from sbc_core.llm import OpenRouterClient
    from sbc_core.search import SqliteSearchIndex
    from sbc_core.taxonomy import load_taxonomy
    from sbc_retrieval.text_search import TextSearcher

    env = EnvSettings()
    local = threading.local()

    def agent_for(bot: str) -> Agent:
        agents: dict[str, Agent] = local.__dict__.setdefault("agents", {})
        if bot not in agents:
            config = load_config(bot, env.app_env)
            # Each bot has its own key in production. For the eval, the public bot may borrow the broker key;
            # only the credit limit differs, not the models or the data policy.
            key = env.openrouter_key_for(config.bot.name) or env.openrouter_api_key_broker
            if key is None:
                raise SystemExit("set OPENROUTER_API_KEY_BROKER")
            gateway = OpenRouterClient(key, base_url=env.openrouter_base_url)
            role = config.models.embeddings
            index = SqliteSearchIndex(
                bundle, prefetch_limit=config.retrieval.prefetch_limit, rrf_k=config.retrieval.rrf_k,
                expected_embedding=(role.model, role.dimensions or 0),
            )  # fmt: skip
            deps = Deps(
                reader=SqliteFactsReader(bundle), chat=gateway, config=config, taxonomy=load_taxonomy(),
                search=TextSearcher.with_gateway(index, config.retrieval, config.models, gateway),
                plan_year=2027,
            )  # fmt: skip
            agents[bot] = Agent(deps, InMemorySaver())
        return agents[bot]

    return agent_for


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generation eval through the agent graph (PLAN.md 5.2-5.3).")
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--golden", type=Path, required=True)
    ap.add_argument(
        "--phase", choices=["1", "2", "all"], default="1", help="default: the Phase 1 gating items"
    )
    ap.add_argument("--category", action="append", help="repeatable")
    ap.add_argument("--id", action="append", help="repeatable")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sample", type=int, help="a seeded random sample of this many items, across categories")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument(
        "--max-cost", type=float, default=20.0, help="stop when model calls exceed this many dollars"
    )
    ap.add_argument("--out", type=Path, default=Path("evals/reports"))
    ap.add_argument("--fail-on-hard-gates", action="store_true")
    args = ap.parse_args(argv)

    items = load_golden(args.golden)
    if args.phase != "all":
        items = [i for i in items if phase(i) == int(args.phase)]
    if args.category:
        items = [i for i in items if i.category in args.category]
    if args.id:
        items = [i for i in items if i.id in args.id]
    if args.sample:
        items = random.Random(0).sample(items, min(args.sample, len(items)))  # noqa: S311 - not security
    items = items[: args.limit] if args.limit else items
    from sbc_core.facts import SqliteFactsReader

    with SqliteFactsReader(args.bundle) as reader:
        bundle_version = reader.bundle_version
    print(f"running {len(items)} items with {args.workers} workers", file=sys.stderr)
    results = run(items, live_agent_factory(args.bundle), workers=args.workers, max_cost=args.max_cost,
                  log=lambda m: print(m, file=sys.stderr))  # fmt: skip
    report = build_report(results, bundle_version, args.golden.name)
    args.out.mkdir(parents=True, exist_ok=True)
    json_path = args.out / f"{report['run_id']}.json"
    md_path = json_path.with_suffix(".md")
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(to_markdown(report), encoding="utf-8")
    print(to_markdown(report))
    print(f"wrote {json_path} and {md_path}", file=sys.stderr)
    hard_failed = [g["gate"] for g in report["gates"] if g["hard"] and g["passed"] is False]
    return 1 if hard_failed and args.fail_on_hard_gates else 0


if __name__ == "__main__":
    raise SystemExit(main())
