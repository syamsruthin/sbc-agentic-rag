"""Retrieval and plan-resolution eval (PLAN.md section 5.1; the Phase 0 eval bar in section 11).

For every golden item it measures, independently of any LLM:

- **Plan resolution** with the deterministic resolver (IDs, UI selection, name search on the plan mentions in
  ``context.plan_mentions``, which stand in for the classifier until Phase 1). A *confident wrong-plan*
  resolution is a ``resolved`` result that includes a plan outside the gold plans, or any ``resolved`` result
  when gold says to clarify (a guess between look-alike plans).
- **Service mapping**: taxonomy terms found in the question against the services named by the gold values
  and sections. This is the synonym stage only; embedding and LLM disambiguation come with Phase 1. Multi-turn
  items are skipped here too, since a follow-up's service is often named in an earlier turn.
- **Text search** under the gold plans (oracle resolution, so search quality is measured on its own):
  recall@5 and @10 and MRR@10 per (item, plan), and context precision and recall by chunk ID over the
  ``keep_per_plan`` chunks an answer would see. Context precision is rank-aware (average precision, as in
  RAGAS); context recall is the share of gold chunks among them.
- **Plan-filter correctness**: every hit must belong only to requested gold plans, and the facts bundle must
  confirm that each of those plans contains the chunk. Anything else is a violation (hard gate: 100%).

Three setups (PLAN.md 5.1): ``full`` (dense + BM25 + rerank), ``no_rerank`` (dense + BM25) and
``bm25_only`` (the fallback when embeddings fail). All three search the bundle's own index. ``bm25_only``
needs no API keys; the other two need a bundle embedded with ``sbc-embed-bundle`` and OpenRouter for the
query embedding and rerank.

Usage::

    uv run python -m evals.harness.retrieval --bundle bundles/facts-2027.1.sqlite \\
        --golden evals/golden/0.2.0 --setup bm25_only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, get_args

from evals.harness.golden import GoldenItem, load_golden
from sbc_core.config import BotName, PlanScope, Retrieval, load_config
from sbc_core.facts import SqliteFactsReader
from sbc_core.interfaces import FactsReader
from sbc_core.search import SqliteSearchIndex
from sbc_core.taxonomy import Taxonomy, TaxonomyError, load_taxonomy
from sbc_retrieval.resolver import PlanResolver, Resolution, id_tokens
from sbc_retrieval.text_search import TextSearcher

Setup = Literal["full", "no_rerank", "bm25_only"]
SETUPS: tuple[Setup, ...] = get_args(Setup)
EVAL_DEPTH = 10
"""Hits kept per plan during the eval, for recall@10 and MRR@10. Answers see only ``keep_per_plan``."""
_NO_RESOLUTION = {
    "multi_turn": "needs session references (Phase 1)",
    "discovery_ranking": "no plan to resolve",
}

# --------------------------------------------------------------------------------------------- results


@dataclass
class PlanRanking:
    plan_key: str
    gold: list[str]
    """Gold chunk hashes that this plan contains."""
    ranked: list[str]
    """Chunk hashes returned for this plan, best first, at most EVAL_DEPTH."""
    keep: int

    def recall_at(self, k: int) -> float:
        return len(set(self.gold) & set(self.ranked[:k])) / len(self.gold)

    @property
    def reciprocal_rank(self) -> float:
        for rank, chunk in enumerate(self.ranked[:EVAL_DEPTH], 1):
            if chunk in self.gold:
                return 1.0 / rank
        return 0.0

    @property
    def context(self) -> list[str]:
        return self.ranked[: self.keep]

    @property
    def context_precision(self) -> float:
        """Average precision over the context: 1.0 when every gold chunk found ranks above every other."""
        found, total = 0, 0.0
        for rank, chunk in enumerate(self.context, 1):
            if chunk in self.gold:
                found += 1
                total += found / rank
        return total / found if found else 0.0

    @property
    def context_recall(self) -> float:
        return self.recall_at(self.keep)


@dataclass
class ResolutionResult:
    status: str
    plan_keys: list[str]
    expected: Literal["plans", "clarify"]
    correct: bool
    confident_wrong: bool


@dataclass
class ServiceResult:
    gold: list[str]
    predicted: list[str]
    ambiguous: bool
    correct: bool


@dataclass
class ItemResult:
    id: str
    category: str
    bot: str
    gating: bool
    query: str
    resolution: ResolutionResult | None = None
    resolution_skipped: str | None = None
    services: ServiceResult | None = None
    rankings: list[PlanRanking] = field(default_factory=list)
    hits: int = 0
    plan_filter_violations: list[str] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    """Gold that can't be scored as written (e.g. a gold chunk none of the gold plans contains)."""


# --------------------------------------------------------------------------------------------- scoring


def gold_services(item: GoldenItem, taxonomy: Taxonomy) -> set[str]:
    """Services the gold values and sections name: ``benefit:<service>:<tier>`` and ``Event > Service``."""
    services = {v.fact.split(":")[1] for v in item.gold.expected_values if v.fact.startswith("benefit:")}
    for section in item.gold.sections:
        event, sep, service = section.partition(" > ")
        if not sep:
            continue
        try:
            services.add(taxonomy.classify_row(event, service).service_id)
        except TaxonomyError:
            continue  # "Important Questions > ...", "Coverage Examples > ..." and the like
    return services


def _mask(text: str, phrases: Iterable[str]) -> str:
    """Blank out plan IDs and names before scanning: names contain words like "Rx" and "Dental"."""
    for phrase in sorted({p for p in phrases if p}, key=len, reverse=True):
        text = re.sub(re.escape(phrase), lambda m: " " * len(m[0]), text, flags=re.IGNORECASE)
    return text


def predicted_services(
    text: str, plan_keys: Sequence[str], reader: FactsReader, taxonomy: Taxonomy
) -> tuple[set[str], bool]:
    """Service IDs the taxonomy finds in ``text``. Drug wording maps to the matching drug tiers of the plans,
    because what a tier holds differs by plan. Returns (services, any ambiguous term)."""
    services: set[str] = set()
    tags: set[str] = set()
    ambiguous = False
    for hit in taxonomy.scan(text):
        services.update(hit.match.service_ids)
        tags.update(hit.match.drug_tags)
        ambiguous |= hit.match.ambiguous
    if tags:
        for plan_key in plan_keys:
            for fact in reader.benefit_facts(plan_key, service_ids=taxonomy.drug_service_ids):
                label = taxonomy.drug_labels.get(fact.label)
                if label and ("any_drug" in tags or tags & set(label.tags)):
                    services.add(label.service_id)
    return services, ambiguous


def score_resolution(item: GoldenItem, resolution: Resolution) -> ResolutionResult:
    gold = set(item.gold.plan_keys)
    got = set(resolution.plan_keys)
    if gold:
        correct = resolution.resolved and got == gold
        wrong = resolution.resolved and bool(got - gold)
        expected: Literal["plans", "clarify"] = "plans"
    else:
        correct = not resolution.resolved
        wrong = resolution.resolved
        expected = "clarify"
    if correct and item.gold.clarify_options_must_include:
        offered = {p.contract_code for a in resolution.ambiguities for p in a.candidates}
        offered |= {p.plan_key for a in resolution.ambiguities for p in a.candidates}
        correct = set(item.gold.clarify_options_must_include) <= offered
    return ResolutionResult(resolution.status, sorted(got), expected, correct, wrong)


# --------------------------------------------------------------------------------------------- runner


@dataclass
class Scopes:
    broker: PlanScope
    public: PlanScope

    @classmethod
    def from_config(cls, config_dir: Path | str = "config") -> Scopes:
        return cls(
            broker=load_config(BotName.BROKER, "local", config_dir).bot.plan_scope,
            public=load_config(BotName.PUBLIC, "local", config_dir).bot.plan_scope,
        )


def evaluate_item(
    item: GoldenItem,
    reader: FactsReader,
    searcher: TextSearcher,
    *,
    keep: int,
    scopes: Scopes,
    taxonomy: Taxonomy,
) -> ItemResult:
    query = item.turns[-1].content
    result = ItemResult(item.id, item.category, item.bot, item.gating, query)
    ctx = item.context
    mentions = [str(m) for m in _as_list(ctx.get("plan_mentions"))]
    ui_selected = [str(k) for k in _as_list(ctx.get("ui_selected_plan_keys"))]

    # Plan resolution
    resolution: Resolution | None = None
    skip = _NO_RESOLUTION.get(item.category)
    if skip is None and not item.gold.plan_keys and item.gold.expected_behavior != "clarify":
        skip = "no plan expected"
    if skip is None:
        scope = scopes.public if item.bot == "public" else scopes.broker
        state = ctx.get("state")
        resolution = PlanResolver(reader, scope).resolve(
            query,
            ui_selected=ui_selected,
            mentions=mentions,
            plan_year=item.gold.plan_year,
            states=[str(state)] if item.bot == "public" and state else [],
        )
        result.resolution = score_resolution(item, resolution)
    else:
        result.resolution_skipped = skip

    # Service mapping
    # A follow-up turn often names its service in an earlier turn ("and urgent care?" then "the second one"),
    # which needs the conversation; like resolution, that waits for the Phase 1 graph.
    gold_svc = gold_services(item, taxonomy) if item.category != "multi_turn" else set()
    if gold_svc:
        plans = list(item.gold.plan_keys)
        names = [
            p.plan_name
            for p in reader.plans(plans + list(resolution.plan_keys if resolution else ())).values()
        ]
        masked = _mask(query, [*id_tokens(query), *mentions, *names])
        predicted, ambiguous = predicted_services(masked, plans, reader, taxonomy)
        result.services = ServiceResult(sorted(gold_svc), sorted(predicted), ambiguous, predicted == gold_svc)

    # Text search under the gold plans
    if item.gold.chunk_hashes and item.gold.plan_keys:
        _search(item, result, reader, searcher, keep)
    return result


def _search(
    item: GoldenItem, result: ItemResult, reader: FactsReader, searcher: TextSearcher, keep: int
) -> None:
    plans = list(dict.fromkeys(item.gold.plan_keys))
    found = searcher.search(plans, result.query)
    result.degraded = found.degraded
    result.hits = len(found.hits)
    wanted = set(plans)
    by_plan: dict[str, list[str]] = defaultdict(list)
    for hit in found.hits:
        foreign = set(hit.plan_keys) - wanted
        if foreign:
            result.plan_filter_violations.append(f"{hit.chunk_hash}: foreign plans {sorted(foreign)}")
        for plan_key in hit.plan_keys:
            by_plan[plan_key].append(hit.chunk_hash)
    gold = list(dict.fromkeys(item.gold.chunk_hashes))
    unowned = set(gold)
    for plan_key in plans:
        ranked = by_plan.get(plan_key, [])
        context = reader.chunk_context(plan_key, list(dict.fromkeys(ranked + gold)))
        for chunk in ranked:
            if chunk not in context:
                result.plan_filter_violations.append(f"{chunk}: bundle says {plan_key} does not contain it")
        plan_gold = [h for h in gold if h in context]
        unowned -= set(plan_gold)
        if plan_gold:
            result.rankings.append(PlanRanking(plan_key, plan_gold, ranked[:EVAL_DEPTH], keep))
    if unowned:
        result.problems.append(f"gold chunks in none of the gold plans: {sorted(unowned)}")


def _as_list(value: object) -> list[object]:
    if value is None:
        return []
    return list(value) if isinstance(value, list | tuple) else [value]


# --------------------------------------------------------------------------------------------- summary


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def summarize(results: Sequence[ItemResult]) -> dict[str, Any]:
    rankings = [r for item in results for r in item.rankings]
    resolved = [i.resolution for i in results if i.resolution is not None]
    services = [i.services for i in results if i.services is not None]
    hits = sum(i.hits for i in results)
    violations = sum(len(i.plan_filter_violations) for i in results)
    return {
        "items": len(results),
        "plan_filter": {
            "searches": sum(1 for i in results if i.rankings or i.hits),
            "hits": hits,
            "violations": violations,
            "correctness": round(1 - violations / hits, 6) if hits else None,
        },
        "resolution": {
            "items": len(resolved),
            "accuracy": _mean([float(r.correct) for r in resolved]),
            "confident_wrong_plan": sum(r.confident_wrong for r in resolved),
            "skipped": sum(1 for i in results if i.resolution_skipped),
        },
        "service_mapping": {"items": len(services), "accuracy": _mean([float(s.correct) for s in services])},
        "retrieval": {
            "plan_rankings": len(rankings),
            "recall_at_5": _mean([r.recall_at(5) for r in rankings]),
            "recall_at_10": _mean([r.recall_at(10) for r in rankings]),
            "mrr_at_10": _mean([r.reciprocal_rank for r in rankings]),
            "context_precision": _mean([r.context_precision for r in rankings]),
            "context_recall": _mean([r.context_recall for r in rankings]),
        },
        "degraded_searches": sum(1 for i in results if i.degraded and i.degraded != ["embedding_disabled"]),
        "gold_problems": sum(len(i.problems) for i in results),
    }


@dataclass(frozen=True)
class Gate:
    name: str
    metric: str
    """Dotted path into the summary, e.g. "retrieval.recall_at_5"."""
    threshold: float
    op: Literal[">=", "=="]
    setups: tuple[Setup, ...] = SETUPS
    hard: bool = False


# PLAN.md section 11, Phase 0 eval bar, plus the MVP hard gate on confident wrong-plan resolutions (5.1).
PHASE0_GATES = (
    Gate("plan filter 100%", "plan_filter.correctness", 1.0, "==", hard=True),
    Gate("0 confident wrong-plan resolutions", "resolution.confident_wrong_plan", 0, "==", hard=True),
    Gate("service mapping >= 95%", "service_mapping.accuracy", 0.95, ">="),
    Gate("recall@5 >= 0.90", "retrieval.recall_at_5", 0.90, ">=", setups=("full",)),
)


def _get(summary: dict[str, Any], dotted: str) -> Any:
    value: Any = summary
    for part in dotted.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def check_gates(
    summary: dict[str, Any], setup: Setup, gates: Sequence[Gate] = PHASE0_GATES
) -> list[dict[str, Any]]:
    out = []
    for gate in gates:
        if setup not in gate.setups:
            continue
        value = _get(summary, gate.metric)
        if value is None:
            passed = None  # nothing to measure, e.g. no searches ran
        elif gate.op == "==":
            passed = value == gate.threshold
        else:
            passed = value >= gate.threshold
        out.append(
            {
                "gate": gate.name,
                "metric": gate.metric,
                "value": value,
                "threshold": gate.threshold,
                "passed": passed,
                "hard": gate.hard,
            }
        )
    return out


@dataclass
class RunReport:
    run_id: str
    setup: Setup
    bundle_version: str
    golden_version: str
    retrieval_settings: dict[str, Any]
    summary: dict[str, Any]
    gating_summary: dict[str, Any]
    by_category: dict[str, dict[str, Any]]
    gates: list[dict[str, Any]]
    items: list[ItemResult]
    notes: list[str] = field(default_factory=list)

    @property
    def hard_gates_failed(self) -> list[str]:
        return [g["gate"] for g in self.gates if g["hard"] and g["passed"] is False]


def run(
    items: Sequence[GoldenItem],
    reader: FactsReader,
    searcher_for: Callable[[Retrieval], TextSearcher],
    settings: Retrieval,
    *,
    setup: Setup,
    scopes: Scopes,
    taxonomy: Taxonomy | None = None,
    run_id: str | None = None,
) -> RunReport:
    """Evaluate ``items``. ``searcher_for`` builds the setup's searcher from eval-depth settings."""
    taxonomy = taxonomy or load_taxonomy()
    keep = settings.keep_per_plan
    deep = settings.model_copy(
        update={
            "keep_per_plan": max(EVAL_DEPTH, keep),
            "rerank_enabled": settings.rerank_enabled and setup == "full",
        }
    )
    searcher = searcher_for(deep)
    results = [
        evaluate_item(item, reader, searcher, keep=keep, scopes=scopes, taxonomy=taxonomy) for item in items
    ]
    by_category: dict[str, list[ItemResult]] = defaultdict(list)
    for r in results:
        by_category[r.category].append(r)
    versions = sorted({i.dataset_version for i in items})
    summary = summarize(results)
    gating = [r for r in results if r.gating]
    notes = []
    if not gating:
        notes.append(
            "No gating items: every golden item is still pending human PDF verification. Gates are "
            "evaluated on all items and are indicative only until items are verified."
        )
    return RunReport(
        run_id=run_id or f"retrieval-{setup}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}",
        setup=setup,
        bundle_version=reader.bundle_version,
        golden_version=",".join(versions),
        retrieval_settings=settings.model_dump() | {"eval_depth": EVAL_DEPTH},
        summary=summary,
        gating_summary=summarize(gating),
        by_category={c: summarize(rs) for c, rs in sorted(by_category.items())},
        gates=check_gates(summarize(gating) if gating else summary, setup),
        items=results,
        notes=notes,
    )


# --------------------------------------------------------------------------------------------- output


def _fmt(value: Any) -> str:
    if value is None:
        return "–"
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def to_markdown(reports: Sequence[RunReport]) -> str:
    lines = [f"# Retrieval eval: bundle {reports[0].bundle_version}, golden {reports[0].golden_version}", ""]
    metrics = [
        ("Plan-filter correctness", "plan_filter.correctness"),
        ("Plan-filter violations", "plan_filter.violations"),
        ("Resolution accuracy", "resolution.accuracy"),
        ("Confident wrong-plan", "resolution.confident_wrong_plan"),
        ("Service mapping accuracy", "service_mapping.accuracy"),
        ("recall@5", "retrieval.recall_at_5"),
        ("recall@10", "retrieval.recall_at_10"),
        ("MRR@10", "retrieval.mrr_at_10"),
        ("Context precision", "retrieval.context_precision"),
        ("Context recall", "retrieval.context_recall"),
        ("Plan rankings scored", "retrieval.plan_rankings"),
        ("Degraded searches", "degraded_searches"),
    ]
    lines.append("| Metric | " + " | ".join(r.setup for r in reports) + " |")
    lines.append("|---|" + "---|" * len(reports))
    for label, path in metrics:
        lines.append(f"| {label} | " + " | ".join(_fmt(_get(r.summary, path)) for r in reports) + " |")
    lines += ["", "## Gates", "", "| Setup | Gate | Value | Result |", "|---|---|---|---|"]
    for r in reports:
        for g in r.gates:
            result = {True: "pass", False: "FAIL", None: "not measured"}[g["passed"]]
            lines.append(
                f"| {r.setup} | {g['gate']}{' (hard)' if g['hard'] else ''} | {_fmt(g['value'])} | {result} |"
            )
    by_setup = {r.setup: r for r in reports}
    if "full" in by_setup and "no_rerank" in by_setup:
        full, plain = by_setup["full"].summary["retrieval"], by_setup["no_rerank"].summary["retrieval"]
        lines += ["", "## Rerank lift (full minus no_rerank)", ""]
        for key in ("recall_at_5", "mrr_at_10", "context_precision"):
            if full[key] is not None and plain[key] is not None:
                lines.append(f"- {key}: {full[key] - plain[key]:+.3f}")
    lines += ["", "## By category", ""]
    for r in reports:
        lines += [
            f"### {r.setup}",
            "",
            "| Category | Items | Resolution | Service mapping | recall@5 | MRR@10 |",
            "|---|---|---|---|---|---|",
        ]
        for cat, s in r.by_category.items():
            lines.append(
                f"| {cat} | {s['items']} | {_fmt(s['resolution']['accuracy'])} | "
                f"{_fmt(s['service_mapping']['accuracy'])} | {_fmt(s['retrieval']['recall_at_5'])} | "
                f"{_fmt(s['retrieval']['mrr_at_10'])} |"
            )
        lines.append("")
    failures = _failures(reports)
    if failures:
        lines += ["## Failing items", "", *failures, ""]
    for note in dict.fromkeys(n for r in reports for n in r.notes):
        lines.append(f"> {note}")
    return "\n".join(lines).rstrip() + "\n"


def _failures(reports: Sequence[RunReport]) -> list[str]:
    out = []
    for r in reports:
        for i in r.items:
            reasons = []
            if i.plan_filter_violations:
                reasons.append(f"plan filter: {i.plan_filter_violations[:2]}")
            if i.resolution and not i.resolution.correct:
                reasons.append(
                    f"resolution {i.resolution.status} {i.resolution.plan_keys} "
                    f"(expected {i.resolution.expected})"
                )
            if i.services and not i.services.correct:
                reasons.append(f"services {i.services.predicted} vs gold {i.services.gold}")
            missed = [x.plan_key for x in i.rankings if x.recall_at(5) < 1]
            if missed:
                reasons.append(f"gold chunk not in top 5 for {missed}")
            reasons += i.problems
            if reasons:
                out.append(f"- `{r.setup}` {i.id} ({i.category}): " + "; ".join(reasons))
    return out


def write_reports(reports: Sequence[RunReport], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = reports[0].run_id.rsplit("-", 1)[-1]
    # Not Path.with_suffix: a bundle version like "2027.1" would be taken for a suffix.
    base = f"retrieval-{reports[0].bundle_version}-{stamp}"
    data = [asdict(r) | {"hard_gates_failed": r.hard_gates_failed} for r in reports]
    json_path, md_path = out_dir / f"{base}.json", out_dir / f"{base}.md"
    json_path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")
    md_path.write_text(to_markdown(reports), encoding="utf-8")
    return json_path, md_path


# --------------------------------------------------------------------------------------------- CLI


def bm25_searcher(bundle: Path) -> Callable[[Retrieval], TextSearcher]:
    """BM25 only, from the bundle's own index (no API keys)."""

    def build(settings: Retrieval) -> TextSearcher:
        index = SqliteSearchIndex(bundle, prefetch_limit=settings.prefetch_limit, rrf_k=settings.rrf_k)
        return TextSearcher(index, settings)

    return build


def live_searcher(bundle: Path) -> Callable[[Retrieval], TextSearcher]:
    """The bundle's dense + BM25 index with OpenRouter query embeddings and rerank (needs a bundle embedded
    with the configured model, and OPENROUTER_API_KEY_BROKER)."""
    from sbc_core.config import EnvSettings
    from sbc_core.llm import OpenRouterClient

    env = EnvSettings()
    config = load_config(BotName.BROKER, env.app_env)
    role = config.models.embeddings
    dims = role.dimensions
    if dims is None:
        raise SystemExit("models.embeddings.dimensions must be set in config/base.yaml")
    gateway = OpenRouterClient.for_bot(env, BotName.BROKER)

    def build(settings: Retrieval) -> TextSearcher:
        index = SqliteSearchIndex(
            bundle, prefetch_limit=settings.prefetch_limit, rrf_k=settings.rrf_k,
            expected_embedding=(role.model, dims),
        )  # fmt: skip
        return TextSearcher.with_gateway(index, settings, config.models, gateway)

    return build


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Retrieval and plan-resolution eval (PLAN.md 5.1).")
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--golden", type=Path, required=True, help="a golden dataset directory")
    ap.add_argument("--setup", action="append", choices=SETUPS, help="repeatable; default bm25_only")
    ap.add_argument("--out", type=Path, default=Path("evals/reports"))
    ap.add_argument("--fail-on-hard-gates", action="store_true")
    args = ap.parse_args(argv)
    items = load_golden(args.golden)
    settings = load_config(BotName.BROKER, "local").retrieval
    scopes = Scopes.from_config()
    reports = []
    with SqliteFactsReader(args.bundle) as reader:
        for setup in args.setup or ["bm25_only"]:
            print(f"running {setup} on {len(items)} items", file=sys.stderr)
            factory = bm25_searcher(args.bundle) if setup == "bm25_only" else live_searcher(args.bundle)
            reports.append(run(items, reader, factory, settings, setup=setup, scopes=scopes))
    json_path, md_path = write_reports(reports, args.out)
    print(to_markdown(reports))
    print(f"wrote {json_path} and {md_path}", file=sys.stderr)
    failed = [f"{r.setup}: {g}" for r in reports for g in r.hard_gates_failed]
    if failed and args.fail_on_hard_gates:
        print("hard gates failed: " + "; ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
