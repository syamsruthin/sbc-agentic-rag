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

- [ ] Doppler, Supabase and OpenRouter keys provisioned (OpenRouter key exists; Doppler and Supabase not yet;
      Qdrant Cloud is no longer needed, see below)
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
      30,498 unique chunks). With the BM25 search tables: 150 MB (rebuilt locally 2026-11-02 from a fresh
      catalog: 9,692 SBCs, 9,692 REVIEW / 0 FAIL, 99.99% exact, PUF 0 mismatches, snapshot unchanged)
- [x] Domain types, `FactsReader` / `SearchIndex` interfaces and the SQLite facts reader
- [x] OpenRouter gateway (chat, embeddings, rerank; ZDR routing, retries, usage)
- [x] Search index moved from Qdrant Cloud into the facts bundle (PLAN.md change approved 2026-11-02): BM25
      tables written by `sbc-build-bundle`, embeddings added by `sbc-embed-bundle` (embedding cache), exact
      plan-filtered hybrid search in `sbc_core.search.SqliteSearchIndex`; `TextSearcher` unchanged. Checked
      against the old Qdrant code on the same bundle (golden 0.2.0, BM25 only): identical recall@5/@10, context
      recall, plan filter and service mapping; 85 ranking positions differ, all exact BM25 score ties (Qdrant
      orders ties arbitrarily, the bundle index by chunk id); the eval runs in 2 s instead of about 5 min
- [x] `sbc-llm-smoke` run (2026-11-02): chat passes; `voyageai/voyage-4-lite` and `voyageai/rerank-3-lite` are
      refused under ZDR (no ZDR endpoint on OpenRouter; `cohere/rerank-4-fast` has none either)
- [x] ZDR replacements approved and in config (PLAN.md 8): `baai/bge-m3` embeddings, `qwen/qwen3-reranker-8b`
      rerank (the only ZDR reranker on OpenRouter); `sbc-llm-smoke` passes all four checks on them
- [x] Embeddings added to bundle `2027.1` (`sbc-embed-bundle`): 30,498 bge-m3 vectors, 20 plan-filter checks
      passed; bundle 217 MB with the full search index (16 KB pages; 4 KB pages wasted half the vector space)
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
      service mapping 98.0%, recall@5 0.847, recall@10 0.952, MRR@10 0.712 (0.713 with Qdrant's tie order)
- [x] Retrieval eval, all three configurations on golden 0.2.0 (2026-11-02, 0 degraded searches). Phase 0 bar
      met: plan filter 100% and 0 confident wrong-plan everywhere, service mapping 98.0%, full recall@5 0.920.

      | Setup | recall@5 | recall@10 | MRR@10 | Context precision | Context recall |
      |---|---|---|---|---|---|
      | full (bge-m3 + BM25 + qwen3 rerank) | 0.920 | 0.962 | 0.730 | 0.724 | 0.934 |
      | no_rerank | 0.900 | 0.945 | 0.783 | 0.776 | 0.916 |
      | bm25_only | 0.847 | 0.952 | 0.712 | 0.701 | 0.915 |

      Rerank lift: recall@5 +0.020, recall@10 +0.017, context recall +0.018, but MRR@10 −0.053 and context
      precision −0.052 (it pulls more gold chunks into the top 5 while often demoting the best one). RRF alone
      is below 0.95, so rerank stays on plan-filtered searches (PLAN.md 3.4.3); revisit in the Phase 1 bake-off.
      Weakest categories: cost scenarios (recall@5 about 0.69) and multi-turn (about 0.60).
- [ ] Taxonomy v2 synonyms from the eval: "delivering (a baby)" is unmapped, "pharmacy" maps to every drug
      tier, and "hospital" (an ambiguous term) adds inpatient rows to a delivery question

- [x] Unit tests no longer read a developer's `.env` (a real key there made the smoke-test tests call
      OpenRouter)

## Phase 1: Broker MVP (PLAN.md §11)

- [x] Phase 1 gate scope decided (2026-11-02): gated on the 353 golden 0.2.0 items that don't need Phase 2
      features; the 57 that do (`estimate_cost` 24, `rank_plans` 8, multi-turn 25) are reported separately

- [ ] FastAPI + LangGraph graph with fast path
- [x] Tools: `find_plans`, `get_plan_facts`, `search_sbc_text`, `compare_plans`, `ask_clarifying_question`
      (`sbc_tools`, `docs/agent-tools.md`): Pydantic I/O, plan gate and scope checks in code, per-turn
      F/S/P placeholder IDs, `Toolbox` with profile filtering, JSON schemas and turn limits
- [x] Fact placeholders, verifier and renderer (`sbc_agent.answer`, `docs/answer-path.md`): number, placeholder,
      link and advice firewall; header, SBC wording, page citations, required limitation notes, carried-note and
      inferred-heading labels, disclaimer; deterministic template fallback through the same checks
- [ ] Decide how to link the 1,383 SBCs that have no public PDF URL (portal preview form only): an API
      endpoint that fetches the PDF, or hosting the PDFs. Until then their citations link to the portal
- [ ] Broker web app with plan picker
- [ ] Supabase auth
- [ ] Langfuse tracing
- [ ] Model bake-off report
- [ ] Deployed to `api-dev`
- [ ] MVP gating set (353 items of golden 0.2.0) passing all hard gates; the other 57 reported
