# Agent graph

One user turn, wired with LangGraph in `packages/agent/src/sbc_agent/graph.py` (PLAN.md section 3.5).

```text
ingress -> classify -> refuse | resolve
resolve -> clarify (interrupt) | answer
clarify -> (the user's choice) -> resolve
answer  -> clarify (the agent asked) | end
```

| Node | What it does |
|---|---|
| `ingress` | Caps the message at 2,000 characters and redacts PII (SSNs, member IDs, phone numbers, emails, dates) before any model, log or trace sees it. Plan IDs are kept. |
| `classify` | One Haiku 5.5 call with a JSON schema: intent, plan mentions, Important Questions, service terms, tiers, state, and risk flags. If it fails, the turn goes to the agent loop. |
| `refuse` | Fixed, code-written replies for out-of-scope requests, abuse and greetings. |
| `resolve` | `PlanResolver` in code: UI selections, then IDs, then name search. A follow-up with no plan named reuses the previous turn's plan only when there was exactly one. |
| `clarify` | Pauses the turn with `interrupt()`: several plans match, a plan wasn't found, or the question needs a plan. The user's choice resumes at `resolve`. After two in a row the plan picker opens. |
| `answer` | Fast path or agent loop, then verify and render (`respond.py`, `docs/answer-path.md`). |

## Answer node

- **Fast path:** a lookup, or a follow-up lookup on a known plan, whose every service term maps to the taxonomy.
  Facts are fetched in code with `get_plan_facts`, then one Sonnet 5.5 call composes the answer.
- **Agent loop:** everything else. Sonnet 5.5 calls the tools (their schemas list the valid service IDs and
  aspects), and its final message is the draft. A clarifying question from the agent pauses the turn.
- At most `budget.max_llm_calls` model calls per turn, after classification. A draft that fails the firewall
  is retried once with the violations as feedback; a second failure, running out of calls or a model outage
  gives the template answer.
- Bot profile rules: the public bot never recommends a plan. Medical-advice and plan-recommendation questions
  get a fixed opening sentence from code before the answer.

The checkpointed state holds only serializable values: the redacted message, classification, resolved plan
keys, the reply and a short history. Facts exist only inside the answer node. Tests use `InMemorySaver` and
scripted model responses (`tests/unit/test_graph.py`); the API uses `DynamoDBSaver` from `langgraph-checkpoint-aws`
(`sbc_aws.checkpoint`), so a clarifying question asked on one Lambda instance resumes on another.

## Streaming and remembered turns

- `Agent.stream(thread_id, message | choice=...)` yields a `Progress(node)` as each node finishes and the same
  `Reply` as `ask`/`choose` last. The API relays the node names as status events; nothing the model wrote is
  streamed before the verifier has passed it.
- `Agent.remember(thread_id, message=..., reply=...)` records a turn answered outside the graph (the API's
  exact-answer cache) in a new thread's history, so a follow-up keeps its plan and context.

## Trying it

```bash
FACTS_BUNDLE_PATH=bundles/facts-2027.1.sqlite uv run sbc-ask "What's the deductible on BTCU8?"
```

`sbc-ask` needs `OPENROUTER_API_KEY_BROKER` and an embedded bundle. It prints the answer, the path taken, the
model calls, the tools and the cost. Without a question it runs interactively, and you answer a clarifying
question with an option number or text.
