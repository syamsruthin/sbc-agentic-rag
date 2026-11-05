# sbc-agentic-rag

[![CI](https://github.com/syamsruthin/sbc-agentic-rag/actions/workflows/ci.yml/badge.svg)](https://github.com/syamsruthin/sbc-agentic-rag/actions/workflows/ci.yml)

Agentic RAG chatbots that answer questions about U.S. health plans and compare them, using each plan's
Summary of Benefits and Coverage (SBC). Every number in an answer is the SBC's own wording, cited to the
plan, section and page.

> **Status: early implementation.** The SBC ingestion pipeline works today: 9,690 plan-year-2027 SBCs are
> converted to Markdown and independently verified. The chatbots are being built in phases; see
> [Status and roadmap](#status-and-roadmap). The full design is in [PLAN.md](PLAN.md).

## Two assistants, one core

| | Broker Assistant | Plan Shopper |
|---|---|---|
| Users | Logged-in brokers and HR teams | Anonymous public visitors |
| Plans | All SBCs, every state and market | Active Individual-market plans |
| Style | Dense tables, contract codes, CSV export, up to 6 plans | Plain language, up to 3 plans, never recommends a plan |

Both run the same pipeline on the same data. A bot profile (`config/bots/*.yaml`) sets each one's scope,
tools, tone, limits and retention, and the code enforces it.

## How it works

1. **Edge:** auth (or a bot check for the public app), rate limits, and PII redaction before anything leaves
   the server.
2. **Classify:** a small model works out the intent, plans, services and network tier, and rewrites vague
   questions into a precise request.
3. **Resolve plans in code:** IDs, then UI selection, then earlier turns, then name search. If more than one
   plan matches, it asks; it never guesses.
4. **Route:** simple lookups read exact values from a facts database (fast path). Comparisons, cost
   estimates and coverage checks go through a tool-using agent loop.
5. **Compose:** the model writes prose and refers to values by placeholder. It never types a number.
6. **Verify:** code rejects any number the model typed and checks citations and required SBC notes.
7. **Render:** the exact SBC wording goes in, with citations, a plan header and a disclaimer.

Arithmetic goes through a deterministic calculator, and "not mentioned in the SBC" is never presented as
"not covered".

## Architecture

```mermaid
flowchart LR
  subgraph OFF["Offline build (GitHub Actions, weekly + on demand)"]
    PORTAL["sbc-portal<br/>catalog + download"] --> PDF[("SBC PDFs")]
    PDF --> CONV["sbc-to-markdown<br/>pdfplumber, template-aware"]
    CONV --> VER["sbc-verify<br/>poppler cross-check"]
    VER -->|"PASS or allow-listed REVIEW"| EXT["sbc_extract<br/>structured rows + cost grammar"]
    VER -->|"FAIL or unknown REVIEW"| Q["Quarantine<br/>(not indexed)"]
    PUFD[("CMS PUF")] --> XC["Cross-checks<br/>PUF + round-trip"]
    EXT --> XC
    XC --> FACTS[("Facts bundle vN<br/>SQLite tables")]
    FACTS --> DIFF["Diff report<br/>human sign-off"]
    EXT --> CHK["Chunker<br/>content-addressed, BM25 weights"]
    CHK --> EMB["Embed new chunks via OpenRouter<br/>cached by chunk hash"]
    CHK --> FACTS
    EMB --> FACTS
  end

  subgraph CLIENTS["Frontends (S3 + CloudFront)"]
    WB["Broker web app"]
    WP["Plan Shopper web app"]
  end

  subgraph RUN["API on AWS Lambda (FastAPI, response streaming)"]
    EDGE["Edge layer<br/>auth, Turnstile, rate limit,<br/>PII redaction, bot profile"]
    AGENT["LangGraph agent"]
    TOOLS["Tools<br/>find_plans, get_plan_facts,<br/>search_sbc_text, compare_plans,<br/>estimate_cost, ask_clarifying_question"]
    VERIFY["Verifier<br/>number + citation firewall"]
    IDX[("Facts bundle vN<br/>facts + search index,<br/>baked into image, read-only")]
  end

  OR["OpenRouter<br/>LLMs, embeddings, rerank"]
  SB[("Cognito + DynamoDB<br/>broker auth, checkpoints,<br/>sessions, audit")]
  UP[("DynamoDB<br/>rate limits, caches")]
  LF["Langfuse (optional)<br/>traces, evals, review queues"]
  DP["SSM Parameter Store<br/>secrets, loaded at cold start"]

  WB --> EDGE
  WP --> EDGE
  EDGE --> AGENT
  AGENT --> TOOLS
  TOOLS --> IDX
  TOOLS --> OR
  AGENT --> OR
  AGENT --> VERIFY
  VERIFY --> EDGE
  EDGE --> UP
  EDGE --> SB
  AGENT --> SB
  AGENT -.-> LF
  DIFF ==>|"docker build"| IDX
  DP -.-> EDGE
```

**Stack:** Python 3.13, uv workspace, LangGraph, FastAPI, SQLite (facts and search index in one file), OpenRouter (Claude Haiku 5.5,
Sonnet 5.5 and Opus 5.5; bge-m3 embeddings and Qwen3 rerank). The deployment is on AWS (PLAN.md revision 3): Lambda
behind CloudFront, S3, DynamoDB, Cognito, SSM Parameter Store, CloudWatch, ECS Fargate for the offline build, and AWS
CDK; Cloudflare Turnstile and optional Langfuse are the external services. PLAN.md section 4 explains each choice, and
[docs/aws/](docs/aws/MIGRATION_PLAN.md) has the deployment analysis, the migration plan and its progress.

## Repository layout

```
config/        base.yaml, bots/{broker,public}.yaml, env/{local,dev,prd}.yaml
packages/      Python workspace packages (imported as sbc_<name>)
  sbc_ingest/    catalog, download, convert, verify SBCs (sbc-portal, sbc-to-markdown, sbc-verify)
  sbc_extract/   Markdown -> structured rows, cost grammar
  sbc_index/     facts bundle builder, with its BM25 and embedding search index
  core/          domain types, config loader, facts and search readers, OpenRouter gateway
  retrieval/     plan resolver, query expansion, text-search pipeline
  tools/         agent tools, cost calculator
  agent/         LangGraph graph, prompts, verifier, renderer
  aws/           AWS adapters (sbc_aws): SSM secrets, DynamoDB checkpointer and stores, S3 PDF cache
  api/           FastAPI app: SSE chat, auth, guards, plan picker, PDF proxy, admin
apps/          broker/, shopper/, chat-ui/ (React)
evals/         golden datasets, judge calibration, harness
tests/         unit/, integration/, contract/, load/
infra/         Docker images and compose (docker/), AWS CDK app (cdk/: SbcFoundation + SbcApp stacks)
scripts/aws/   bootstrap, deploy, secrets, images, users, demo password, offline build
spikes/        throwaway AWS experiments and their scripts (docs/aws/SPIKES.md)
docs/          pipeline, agent graph, tools and answer-path notes; review sheets; ADRs; aws/ deployment track
data/          fetched and generated SBC data (not committed)
```

Module boundaries are enforced by import-linter: `api > (agent | aws) > (tools | retrieval) > core`, build-time
packages (`sbc_ingest`, `sbc_extract`, `sbc_index`) are never imported at runtime, and `core` depends on
nothing internal.

## Getting started

Prerequisites: [uv](https://docs.astral.sh/uv/) 0.7 or newer and
[pre-commit](https://pre-commit.com/) (`uv tool install pre-commit`). The SBC verifier also needs poppler
(`brew install poppler` or `apt-get install poppler-utils`).

```bash
git clone https://github.com/syamsruthin/sbc-agentic-rag.git
cd sbc-agentic-rag
uv sync                # .venv with Python 3.13 and every workspace package
pre-commit install     # gitleaks, ruff, mypy and import contracts on each commit
cp .env.example .env   # local values; on AWS the secrets come from SSM Parameter Store
```

Run the same checks as CI:

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run lint-imports
uv run pytest tests/unit
```

To fetch and rebuild the SBC data locally, see [data/README.md](data/README.md). How the pipeline converts
and verifies SBCs is described in [docs/sbc-pipeline.md](docs/sbc-pipeline.md).

### Try the agent

With an embedded facts bundle and `OPENROUTER_API_KEY_BROKER` set, ask a question from the terminal:

```bash
FACTS_BUNDLE_PATH=bundles/facts-2027.1.sqlite uv run sbc-ask "What's the deductible on BTCU8?"
```

It prints the answer with its citations, then the path taken, the model calls and their cost. Run it without
a question for an interactive session. How a turn is handled is in [docs/agent-graph.md](docs/agent-graph.md);
the tools and the answer checks are in [docs/agent-tools.md](docs/agent-tools.md) and
[docs/answer-path.md](docs/answer-path.md).

### Run the API locally

```bash
APP_ENV=local FACTS_BUNDLE_PATH=bundles/facts-2027.2.sqlite \
  uv run uvicorn --factory sbc_api.app:create_app --port 8080
curl -s 'localhost:8080/api/healthz?deep=1'
curl -sN -X POST localhost:8080/api/broker/chat -H 'content-type: application/json' \
  -d '{"message": "What'"'"'s the deductible on BTCU8?"}'
```

Locally every request is a developer principal and the stores are in-process; set the `DDB_TABLE_*` variables
(and `AWS_ENDPOINT_URL_DYNAMODB` for DynamoDB Local) to use DynamoDB instead.

To run the Lambda image itself on DynamoDB Local:

```bash
uv run python infra/docker/fixture_bundle.py bundles/fixture        # or a folder with a real facts.sqlite
SBC_BUNDLE_DIR=../../bundles/fixture docker compose -f infra/docker/compose.yaml up --build
```

[docs/aws/RUNBOOK.md](docs/aws/RUNBOOK.md) covers the images, the smoke test and the offline build; the AWS
deployment track is in [docs/aws/](docs/aws/MIGRATION_PLAN.md).

## Configuration

Behavior settings live in `config/` and are merged in order: `base.yaml`, then `bots/<bot>.yaml`, then
`env/<env>.yaml`, then `SBC_*` environment variables (for example `SBC_RETRIEVAL__RERANK_ENABLED=false`).
`APP_ENV` picks the environment. Secrets and deploy-time values come only from environment variables
(`.env.example` lists them) and are rejected if they appear in YAML. In `dev` and `prd` a bot refuses to start
if any secret it needs is missing.

## Status and roadmap

| Milestone | Status |
|---|---|
| Repository setup and public-repo hygiene | Done |
| Scaffolding: workspace, config, lint, types, tests, CI | Done (PR #1) |
| Phase 0: extraction, facts bundle with search index, golden dataset | Eval bar met; human sign-offs left |
| Phase 1: Broker Assistant MVP (internal alpha) | In progress: tools, answer path, agent graph and the API (SSE chat, auth, guards, plan picker, PDF proxy) done; frontends, deployment and evals left |
| Phase 2: Broker beta (calculator, multi-turn, rankings) | Planned |
| Phase 3: Plan Shopper (public) | Planned |
| Phase 4: production hardening and GA | Planned |
| AWS deployment track (branch `main_AWS`, the long-lived AWS branch; not merged into `main`) | WP0–WP9 done (config, AWS adapters, bundle PDF sources, API, auth, guards, PDF proxy, containers, CDK stacks); first deploy, WP10–WP14 left |

[TODO.md](TODO.md) has the detailed checklist; PLAN.md section 11 has each phase's acceptance criteria and
eval bar. The AWS track's progress is in [docs/aws/TASKS_DONE.md](docs/aws/TASKS_DONE.md) and
[docs/aws/TODO.md](docs/aws/TODO.md).

## Data and disclaimer

This is an independent project. It is not affiliated with or endorsed by any insurer. SBCs are public
documents that insurers post on their websites; this repository does not include them, only the scripts that
fetch them. Answers summarize SBCs and are not a guarantee of coverage or cost, and not medical, legal, tax or
financial advice. Always check the official plan documents.

## License

[MIT](LICENSE)
