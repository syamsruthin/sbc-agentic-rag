# Agent tools

The tools the agent loop can call (PLAN.md section 3.5.4), in `packages/tools` (`sbc_tools`). Phase 1 has five:

| Tool | What it returns |
|---|---|
| `find_plans` | Plans by name, contract code, HIOS id or SBC id, or a listing by state, market, plan type and metal tier |
| `get_plan_facts` | Exact SBC values: Important Questions, benefit rows (by service ID or drug tag) with their limitations, coverage examples, the Excluded and Other Covered Services lists |
| `compare_plans` | One table of the same aspects across 2 or more plans; aspects are question IDs, service IDs, drug tags or presets (`costs_overview`, `everyday_care`, `hospital`) |
| `search_sbc_text` | Plan-filtered hybrid search over the SBC text, for anything outside the template fields |
| `ask_clarifying_question` | A question for the user; the graph pauses the turn on it |

`rank_plans`, `estimate_cost` and `glossary_lookup` come in Phase 2. `Toolbox` offers a bot only the tools that
are both implemented and listed in its profile (`config/bots/*.yaml`); any other name is refused.

## Rules enforced in code

- **Plans.** A tool accepts only plans the turn may use: those the resolver resolved before the agent ran, plus
  plans a `find_plans` call listed without ambiguity. A name that matches several plans is listed for a
  clarifying question but can't be used. Every plan must also be inside the bot's scope (`PlanScope.allows`),
  and in the user's state for a bot without cross-state search.
- **Values.** Facts come from the facts bundle verbatim. A benefit fact always carries its row's limitations
  and the `carried_note` and `event_inferred` flags. Something the SBC has no row for comes back under
  `not_in_sbc`, worded so it is never read as "not covered".
- **Placeholders.** Facts get per-turn IDs (`F1`…), text chunks `S1`…, plans `P1`…; the same value keeps its ID
  within a turn. Compose writes answers with these IDs, and code substitutes the SBC wording (PLAN.md 3.7).
- **Listing data.** State, market, network, exchange, status and metal tier are grouped under `listing` with a
  source label, because they are not SBC text.
- **Search.** Search results that include a plan outside the request fail the call closed. Excerpts are marked
  as data, never instructions.
- **Limits.** One clarifying question per turn; after two unanswered in a row the plan picker opens instead.
  The per-turn tool-call and identical-call limits come from `budget` in `config/base.yaml`.

`sbc_tools` depends only on `sbc_core` (import-linter). The agent passes in the text searcher
(`sbc_retrieval.text_search.TextSearcher`) through `ToolContext`.
