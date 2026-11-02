# Converter fix: changes to the 2027 corpus

The strict verifier (punctuation-exact cells, split sub-labels) showed that the original converter changed the
text of some cells. This page summarizes what the fixed converter changed when the whole corpus was
regenerated. The full cell-by-cell list is `data/markdown/converter_fix_diff_2027.csv`, which is generated
locally and not committed, like the rest of `data/`.

## Result

- All 9,690 SBCs re-converted. Strict verification: **9,690 REVIEW, 0 FAIL, 0 problems**. Every REVIEW reason
  is in an allowlisted class (`packages/sbc_extract/src/sbc_extract/data/review_allowlist.yaml`).
- **765 files** changed, in **2,285** hunks. Every hunk falls in one of the classes below; none are unexplained.

| Change | Hunks | Before | After |
|---|---|---|---|
| Prescription Drug deductible re-joined | 1,838 | `$20/prescription,; Prescription Drug: deductible does not apply (retail) …` | `$20/prescription, Prescription Drug deductible does not apply (retail) …` |
| Wrapped sub-label re-joined | 202 | `Designated; Specialist: $50/visit; Specialist: $80/visit` | `Designated Specialist: $50/visit; Specialist: $80/visit` |
| Durable Medical Equipment deductible re-joined | 124 | `20% coinsurance, Durable Medical; Equipment: deductible applies` | `20% coinsurance, Durable Medical Equipment deductible applies` |
| Drug note heading back to a paragraph | 55 | `### More information about prescription drug coverage …` (fake event) | note printed under the drugs event |
| Drug section page range after the note moved | 55 | `*Source: SBC page 2*` | `*Source: SBC pages 2-3*` |
| Home Health Care deductible re-joined | 7 | `25% coinsurance,; Home Health Care: deductible applies` | `25% coinsurance, Home Health Care deductible applies` |
| Limitations final period restored | 4 | `Preferred Network 30% coinsurance` | `Preferred Network 30% coinsurance.` |

## Causes and fixes

1. **Wrapped words taken for sub-labels.** When a cost cell wrapped in the PDF, the converter sometimes treated
   the wrapped words as a sub-label and inserted `; ` and `:`. It now splits only on a curated list of SBC
   sub-labels (`sbc_core/sublabels.py`), after re-joining labels the PDF wrapped. (PR #2)
2. **Drug note as a fake event.** On a page break, a cell holding only the drug-coverage note started a new
   event. It now continues the drugs event. (PR #2)
3. **Limitations cut short by a stray rule.** In four Maine Individual SBCs, a stray table rule ended the
   hospice limitations cell just before the final period, so it was dropped. Each row's limitations are now
   read from all of the row's cells out to the table's right edge, counting each character once.

The verifier missed all three because it compared cells by letters and digits only. It now also compares
punctuation exactly, allowing only the converter's own `Label: ` markup around known sub-labels.

## Reviewer spot check

- [x] 20 random rows from `converter_fix_diff_2027.csv` checked against their PDFs (Syamsruthin Kaliki, 2026-11-02)
