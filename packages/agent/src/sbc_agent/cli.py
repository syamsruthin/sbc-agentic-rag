"""Ask the agent from the terminal, against the real facts bundle and OpenRouter (development only).

    sbc-ask "What's the deductible on BTCU8?"
    sbc-ask --bot public --state CA "Does 0JF1 cover acupuncture?"
    sbc-ask                      # interactive; answer a clarifying question with a number or text

Needs ``OPENROUTER_API_KEY_<BOT>`` and an embedded bundle (``FACTS_BUNDLE_PATH`` or ``--bundle``). Prints the
answer, then the path taken, model calls, tools and cost.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from sbc_agent.graph import Agent, Reply
from sbc_agent.respond import Deps
from sbc_core.config import BotName, EnvSettings, load_config
from sbc_core.facts import SqliteFactsReader
from sbc_core.llm import OpenRouterClient
from sbc_core.search import SqliteSearchIndex
from sbc_core.taxonomy import load_taxonomy
from sbc_retrieval.text_search import TextSearcher


def _show(reply: Reply) -> None:
    print(reply.markdown)
    if reply.clarification is not None:
        for i, option in enumerate(reply.clarification.options, 1):
            print(f"  {i}. {option.label}")
        if reply.clarification.open_plan_picker:
            print("  (or name the plan)")
    t = reply.trace
    usage = t.get("usage", []) + ([t["classify_usage"]] if t.get("classify_usage") else [])
    cost = sum(u.get("cost_usd") or 0 for u in usage)
    tools = ", ".join(c["name"] + ("" if c["ok"] else " (refused)") for c in t.get("tools", []))
    print(
        f"\n-- path {t.get('path', '-')}, {len(usage)} model calls, ${cost:.4f}, tools [{tools}]"
        + (f", fallback {t['fallback_reason']}" if t.get("fallback_reason") else "")
        + (f", rejected drafts {len(t['verifier'])}" if t.get("verifier") else ""),
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Ask the SBC agent (development).")
    ap.add_argument("question", nargs="?")
    ap.add_argument("--bot", choices=[b.value for b in BotName], default="broker")
    ap.add_argument("--bundle", type=Path)
    ap.add_argument("--state", help="the public user's state")
    ap.add_argument("--plan", action="append", default=[], help="a plan_key selected in the UI (repeatable)")
    args = ap.parse_args(argv)

    env = EnvSettings()
    config = load_config(args.bot, env.app_env)
    bundle = args.bundle or Path(env.facts_bundle_path)
    role = config.models.embeddings
    gateway = OpenRouterClient.for_bot(env, config.bot.name)
    index = SqliteSearchIndex(
        bundle, prefetch_limit=config.retrieval.prefetch_limit, rrf_k=config.retrieval.rrf_k,
        expected_embedding=(role.model, role.dimensions or 0),
    )  # fmt: skip
    with SqliteFactsReader(bundle) as reader:
        deps = Deps(
            reader=reader, chat=gateway, config=config, taxonomy=load_taxonomy(), plan_year=2027,
            search=TextSearcher.with_gateway(index, config.retrieval, config.models, gateway),
        )  # fmt: skip
        agent = Agent(deps, InMemorySaver())
        thread = uuid.uuid4().hex
        pending: Reply | None = None
        questions = [args.question] if args.question else None
        while True:
            text = questions.pop(0) if questions else None if questions is not None else input("\n> ").strip()
            if not text:
                break
            if pending is not None and pending.clarification is not None:
                options = pending.clarification.options
                pick = options[int(text) - 1] if text.isdigit() and 0 < int(text) <= len(options) else None
                reply = agent.choose(
                    thread, plan_key=pick.plan_key if pick else None, text=None if pick else text
                )
            else:
                reply = agent.ask(thread, text, ui_selected=tuple(args.plan), user_state=args.state)
            _show(reply)
            pending = reply if reply.kind == "clarify" else None
            if questions is not None and pending is None:
                break
            if questions is not None:
                questions = None  # a one-shot question that needs clarification continues interactively
