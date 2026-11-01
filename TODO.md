# Progress

Status at a glance. Deliverables and acceptance criteria are defined in [PLAN.md](PLAN.md) §10–§11.

## Checkpoints

- [x] 1. Repository created and first push
- [x] 2. Scaffolding done and CI green (PR #1)
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

- [ ] Doppler, Qdrant Cloud, Supabase and OpenRouter keys provisioned (OpenRouter key and Qdrant free cluster
      exist; Doppler and Supabase not yet)
- [x] Existing scripts moved into `sbc_ingest` (PR #2; byte-identical regression test in place)
- [ ] Full-corpus run: strict verifier on all 9,690 files, converter-fix diff report, regenerated Markdown
- [ ] REVIEW allowlist signed off
- [x] `sbc_extract`: schema, cost grammar, limit parsers, validators (PR #2)
- [ ] Cost grammar: ≥ 99.5% of cells `exact` on the full corpus, all patterns reviewed (`sbc-grammar-review`)
- [x] PUF cross-check tool (`sbc-puf-check`)
- [ ] PUF cross-check result: 0 unexplained mismatches on the full corpus
- [x] Facts bundle builder v0 (`sbc-build-bundle`)
- [ ] Facts bundle ≤ 300 MB on the full corpus
- [x] Domain types, `FactsReader` / `SearchIndex` interfaces and the SQLite facts reader
- [x] OpenRouter gateway (chat, embeddings, rerank; ZDR routing, retries, usage)
- [x] Qdrant collection loader with embedding cache and BM25 vectors (`sbc-load-qdrant`)
- [x] Plan-filtered hybrid search (`QdrantSearchIndex`, `TextSearcher`)
- [ ] First collection loaded on the Qdrant free cluster; embedding and rerank confirmed under ZDR
- [x] Service taxonomy v1
- [x] Golden dataset schema and bundle consistency check
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
