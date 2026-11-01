# evals/

Offline evaluation (PLAN.md sections 5 and 6).

| Folder | What |
|---|---|
| `golden/<version>/` | Versioned golden dataset, one JSONL file per category. Gold values are verified against the SBC PDFs by a human. |
| `calibration/` | Human-labeled set used to calibrate the LLM judge. |
| `harness/` | Runner, metrics, judges, and the report and PR comment. |
| `reports/` | Run outputs; not committed. |

The harness and golden dataset v0 are built in Phase 0, then wired into the CI eval gate.
