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
- [x] Full-corpus run: strict verifier passes on all 9,690 files (0 FAIL), converter-fix diff report
      (`docs/reviews/converter-fix-2027.md`), regenerated Markdown installed
- [x] Corpus refetched 2026-11-01: 9,692 SBCs (2 new Anthem Small Group); strict verification 9,692 REVIEW,
      0 FAIL, 0 problems; count snapshot committed (`docs/reviews/corpus-snapshot-2027.md`, `sbc-snapshot`)
- [ ] Converter-fix spot check: 20 random diff rows against the PDFs (`docs/reviews/converter-fix-2027.md`)
- [ ] REVIEW allowlist signed off
- [x] `sbc_extract`: schema, cost grammar, limit parsers, validators (PR #2)
- [x] Cost grammar: ≥ 99.5% of cells `exact` on the full corpus (99.99%: 707,005 of 707,080 cells;
      41 unparsed and 34 blank, all flagged)
- [ ] Cost grammar: all patterns reviewed (263 patterns in `docs/reviews/cost-grammar-review.md`;
      sign-off pending)
- [x] PUF cross-check tool (`sbc-puf-check`)
- [x] PUF cross-check result: 0 unexplained mismatches on the full corpus (448 linked SBCs, 0 mismatches)
- [x] Facts bundle builder v0 (`sbc-build-bundle`)
- [x] Facts bundle ≤ 300 MB on the full corpus (139 MB; 9,690 plans; A3655 and A3754 quarantined;
      30,498 unique chunks)
- [x] Domain types, `FactsReader` / `SearchIndex` interfaces and the SQLite facts reader
- [x] OpenRouter gateway (chat, embeddings, rerank; ZDR routing, retries, usage)
- [x] Qdrant collection loader with embedding cache and BM25 vectors (`sbc-load-qdrant`)
- [x] Plan-filtered hybrid search (`QdrantSearchIndex`, `TextSearcher`)
- [ ] First collection loaded on the Qdrant free cluster; embedding and rerank confirmed under ZDR
      (needs secrets in a new session: run `sbc-llm-smoke`, then `sbc-load-qdrant`)
- [x] Service taxonomy v1
- [x] Golden dataset schema and bundle consistency check
- [x] Plan resolver in code (IDs, UI selection, name search; never guesses) and BM25 synonym expansion
- [x] Retrieval eval harness (`evals/harness/retrieval.py`), PDF gold check (`pdf_check.py`), CI eval gate
- [x] Golden dataset v0 drafted: 150 items in `evals/golden/0.1.0`, every value confirmed in its PDF cell by
      the poppler check, all `pending`
- [x] MVP gating set drafted: golden 0.2.0, 410 items at the PLAN.md 6.2 MVP targets (0.1.0 plus 260 new);
      553 of 557 values confirmed in their PDF cell by the poppler check, the other 4 by reading the page;
      all `pending`
- [ ] Golden dataset verified against the PDFs by a person (then items gate merges; 0.2.0 is the set to review)
- [x] Retrieval eval, BM25-only configuration on golden 0.2.0: plan filter 100%, 0 confident wrong-plan,
      service mapping 98.0%, recall@5 0.847, recall@10 0.952, MRR@10 0.713
- [ ] Retrieval eval, full and no-rerank configurations (needs the live collection); recall@5 ≥ 0.90 gate
- [ ] Taxonomy v2 synonyms from the eval: "delivering (a baby)" is unmapped, "pharmacy" maps to every drug
      tier, and "hospital" (an ambiguous term) adds inpatient rows to a delivery question

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
