# evals/

Offline evaluation (PLAN.md sections 5 and 6).

| Folder | What |
|---|---|
| `golden/<version>/` | Versioned golden dataset, one JSONL file per category. Gold values are verified against the SBC PDFs by a person; until then an item is `verified_against: "pending"` and doesn't gate merges. |
| `calibration/` | Human-labeled set used to calibrate the LLM judge (Phase 1). |
| `harness/` | Schema, metrics, the PDF gold check and the CI gate (below). |
| `reports/` | Run outputs; not committed. |

## Harness

| Module | What it does |
|---|---|
| `golden.py` | Record schema (PLAN.md 6.3), loader, and `check_against_bundle`: every gold value must equal the facts bundle's, or someone reads the PDF to find out which is wrong. |
| `pdf_check.py` | Independent check of gold values against the PDF with poppler word coordinates (not pdfplumber, which built the bundle). Each value must be found in its own cell: tier column and service row, the Answers column, or the coverage example line. |
| `retrieval.py` | Retrieval and plan-resolution eval (PLAN.md 5.1): plan-filter correctness, resolution accuracy and confident wrong-plan resolutions, service mapping, recall@5/@10, MRR@10, and context precision and recall by chunk ID. Three setups: `full`, `no_rerank`, `bm25_only`. |
| `ci_gate.py` | The deterministic CI gate: validates every committed golden dataset and runs the BM25-only eval on the synthetic fixture bundle; any hard-gate failure fails the `eval-gate` job. |

### Running

The facts bundle comes from `sbc-build-bundle` (see `data/README.md`).

```bash
uv run python -m evals.harness.ci_gate
```

```bash
uv run python -m evals.harness.retrieval --bundle bundles/facts-2027.1.sqlite --golden evals/golden/0.2.0 --setup bm25_only
```

```bash
uv run python -m evals.harness.pdf_check --bundle bundles/facts-2027.1.sqlite --golden evals/golden/0.2.0
```

- `bm25_only` builds an in-memory Qdrant collection from the bundle and needs no keys (about 5 minutes for the full corpus).
- `full` and `no_rerank` need `QDRANT_URL`, `QDRANT_API_KEY` and `OPENROUTER_API_KEY_BROKER`, and a collection loaded with `sbc-load-qdrant`. Pass `--setup` more than once to run several setups into one report; with both `full` and `no_rerank` the report shows the rerank lift.
- Reports go to `evals/reports/retrieval-<bundle>-<time>.{json,md}`. `--fail-on-hard-gates` exits non-zero on a hard-gate failure.

### What the metrics mean

- **Plan resolution** uses the deterministic resolver (`sbc_retrieval.resolver`): IDs in the text, UI-selected plans, and name search on `context.plan_mentions`, which stands in for the classifier's plan mentions until Phase 1. Multi-turn items are skipped (session references come with the graph).
  A **confident wrong-plan** resolution is a `resolved` result that includes a non-gold plan, or any `resolved` result when gold says to clarify.
- **Service mapping** scans the question with the taxonomy (`Taxonomy.scan`, synonym stage only) after masking plan IDs and names, and compares the services with those the gold values and sections name.
- **Text search** runs under the gold plans, so search is measured apart from resolution. Recall and MRR are per (item, plan), over the top 10 hits for that plan. Context precision (rank-aware average precision, as in RAGAS) and context recall cover the `keep_per_plan` chunks an answer would see.
- **Plan-filter correctness**: every hit must belong only to requested plans, and the facts bundle must confirm that each of those plans contains the chunk.

### PDF gold check: calibration

On 300 random plans of the 2027 corpus (about 33,000 values), each true value was checked, and so was a wrong value from the same plan in the same cell:

- **0 of 33,207 wrong values were confirmed.**
- True values confirmed in their cells: 97% of benefit values, 92% of Important Questions answers and 100% of coverage example lines.

Anything not confirmed is listed for a person to read in the PDF. A failure to find a cell always makes a match fail; it never makes one succeed.
