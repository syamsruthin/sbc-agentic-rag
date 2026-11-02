"""Live smoke test of the OpenRouter gateway (PLAN.md section 11, Phase 0: confirm the endpoints under ZDR).

One small call to each endpoint with the configured models, checking what the unit tests can only assume:

- **embeddings**: one vector per input with the configured dimensions, for ``input_type`` "document" and
  "query" (if a call with ``input_type`` fails, it is retried without it to tell whether the parameter is
  the problem), and that a query lands closer to the matching document;
- **rerank**: the response shape, and that the relevant document ranks first;
- **chat**: a tiny completion from the classify model.

Every request carries the ZDR provider policy (``sbc_core.llm``), so a passing call means OpenRouter accepted
it. Prints a table, the served models and the reported cost; exits 1 on any failure and 2 without a key.

Usage: ``sbc-llm-smoke`` (needs ``OPENROUTER_API_KEY_BROKER``).
"""

from __future__ import annotations

import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from sbc_core.config import BotName, EnvSettings, Models, load_config
from sbc_core.llm import GatewayError, OpenRouterClient, Usage

DOCUMENTS = (
    "If you visit a health care provider's office or clinic > Specialist visit\n"
    "In-Network Provider: $40/visit deductible does not apply",
    "If you need immediate medical attention > Emergency room care\n"
    "In-Network Provider: $250/visit deductible does not apply\nLimitations: Copayment waived if admitted.",
    "If you need help recovering or have other special health needs > Hospice services\n"
    "In-Network Provider: No charge",
)
QUERY = "How much is the emergency room copay?"
RELEVANT = 1


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) or 1.0)


def _usage(usages: Sequence[Usage]) -> str:
    models = sorted({u.model for u in usages})
    costs = [u.cost_usd for u in usages if u.cost_usd is not None]
    cost = f"${sum(costs):.6f}" if costs else "cost not reported"
    return f"served by {', '.join(models)}; {sum(u.total_tokens for u in usages)} tokens; {cost}"


def check_embeddings(client: OpenRouterClient, models: Models) -> list[Check]:
    role = models.embeddings
    dims = role.dimensions
    out = []
    try:
        docs = client.embed(role.model, DOCUMENTS, dimensions=dims, input_type="document")
        query = client.embed(role.model, [QUERY], dimensions=dims, input_type="query")
    except GatewayError as exc:
        detail = str(exc)
        try:
            client.embed(role.model, [QUERY], dimensions=dims)
            detail += " | the same call WITHOUT input_type succeeds: drop input_type for this model"
        except GatewayError as plain:
            detail += f" | without input_type too: {plain}"
        return [Check("embeddings", False, detail)]
    sizes = {len(v) for v in (*docs.vectors, *query.vectors)}
    shape_ok = len(docs.vectors) == len(DOCUMENTS) and sizes == {dims or min(sizes)}
    shape = f"{len(docs.vectors)} document vectors, dimensions {sorted(sizes)}"
    out.append(Check("embeddings shape", shape_ok, f"{shape}; {_usage([*docs.usage, *query.usage])}"))
    sims = [_cosine(query.vectors[0], d) for d in docs.vectors]
    best = max(range(len(sims)), key=sims.__getitem__)
    cosines = ", ".join(f"{s:.3f}" for s in sims)
    out.append(Check("embeddings relevance", best == RELEVANT, f"cosine {cosines}; want document {RELEVANT}"))
    return out


def check_rerank(client: OpenRouterClient, models: Models) -> list[Check]:
    try:
        result = client.rerank(models.rerank.model, QUERY, DOCUMENTS)
    except GatewayError as exc:
        return [Check("rerank", False, str(exc))]
    order = [r.index for r in result.results]
    ok = bool(order) and order[0] == RELEVANT and sorted(order) == sorted(set(order))
    scores = ", ".join(f"{r.index}:{r.score:.3f}" for r in result.results)
    return [Check("rerank", ok, f"order {order} (scores {scores}); {_usage([result.usage])}")]


def check_chat(client: OpenRouterClient, models: Models) -> list[Check]:
    role = models.classify
    try:
        result = client.chat(role.model, [{"role": "user", "content": "Reply with the single word OK."}],
                             max_tokens=16, effort=role.effort)  # fmt: skip
    except GatewayError as exc:
        return [Check("chat", False, str(exc))]
    text = (result.content or "").strip()
    return [
        Check("chat", bool(text), f"{text[:40]!r}, finish {result.finish_reason}; {_usage([result.usage])}")
    ]


def run(client: OpenRouterClient, models: Models) -> list[Check]:
    return [*check_embeddings(client, models), *check_rerank(client, models), *check_chat(client, models)]


def main(argv: list[str] | None = None) -> int:
    env = EnvSettings()
    models = load_config(BotName.BROKER, env.app_env).models
    try:
        client = OpenRouterClient.for_bot(env, BotName.BROKER)
    except GatewayError as exc:
        print(f"skipped: {exc}", file=sys.stderr)
        return 2
    with client:
        checks = run(client, models)
    for c in checks:
        print(f"{'PASS' if c.ok else 'FAIL'}  {c.name:22} {c.detail}")
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
