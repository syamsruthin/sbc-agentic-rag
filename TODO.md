# Progress

Status at a glance. Deliverables and acceptance criteria are defined in [PLAN.md](PLAN.md) §10–§11.

## Checkpoints

- [x] 1. Repository created and first push
- [ ] 2. Scaffolding done and CI green
- [ ] 3. Phase 1 acceptance criteria met, with eval results against the golden dataset

## Repository setup

- [x] Public GitHub repo with description and topics
- [x] `.gitignore` (Python, Node, virtualenvs, `.env`, model weights, caches, vector DB data, PDFs and datasets)
- [x] `.env.example` listing every required variable, no real values
- [x] Secret scan of existing files before the first push (gitleaks: no leaks)
- [x] MIT `LICENSE`
- [x] gitleaks pre-commit hook
- [x] `data/README.md`: no SBC data committed; fetch and rebuild instructions

## Scaffolding (PLAN.md §10)

- [x] uv workspace with the `packages/` layout; module boundaries enforced by import-linter
- [x] `config/` (base, bot profiles, environments) loaded with pydantic-settings, with unit tests
- [x] ruff, mypy, pytest and pre-commit hooks
- [x] GitHub Actions CI: lint, type check, unit tests, secret scan, eval-gate placeholder
- [x] README rewrite: overview, architecture diagram, setup, status
- [x] PLAN.md §10 fixes approved: `.github/workflows/`, `prd` environment name

## Phase 0: Foundations (PLAN.md §11)

- [ ] Doppler, Qdrant Cloud, Supabase and OpenRouter keys provisioned
- [ ] Existing scripts moved into `sbc_ingest` (regenerated Markdown byte-identical)
- [ ] REVIEW allowlist signed off
- [ ] `sbc_extract`: schema, cost grammar, limit parsers, validators
- [ ] PUF cross-check (0 unexplained mismatches)
- [ ] Facts bundle builder v0
- [ ] Qdrant collection loader (embeddings via OpenRouter)
- [ ] Service taxonomy v1
- [ ] Golden dataset v0 (150 hand-authored, PDF-verified items)
- [ ] Retrieval eval in three configurations

## Phase 1: Broker MVP (PLAN.md §11)

- [ ] FastAPI + LangGraph graph with fast path
- [ ] Tools: `find_plans`, `get_plan_facts`, `search_sbc_text`, `compare_plans`, `ask_clarifying_question`
- [ ] Fact placeholders, verifier and renderer
- [ ] Broker web app with plan picker
- [ ] Supabase auth
- [ ] Langfuse tracing
- [ ] Model bake-off report
- [ ] Deployed to `api-dev`
- [ ] MVP gating set (about 410 items) passing all hard gates
