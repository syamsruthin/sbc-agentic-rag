# Golden dataset 0.2.0 (draft, pending human verification)

The MVP gating set: 410 items across the PLAN.md section 6.2 categories, each at its MVP target. Items
gs-000001 to gs-000150 are golden 0.1.0 unchanged (only `dataset_version` differs); gs-000151 to gs-000410
are new, drafted the same way with new wording and fresh plans. Supersedes 0.1.0. Schema:
`evals/harness/golden.py` (PLAN.md 6.3), one JSONL file per category.

**Status: every item is `verified_against: "pending"`, so none gates a merge yet.** An item becomes gating
when a person has checked it against the SBC PDF and filled in `reviewed_by` and `reviewed_at`. Checkpoint 3
needs the hard gates to pass on this set once it is verified.

## Contents

| Category | Items | File |
|---|---:|---|
| Single-field lookup | 60 | `single_field.jsonl` |
| Multi-field | 30 | `multi_field.jsonl` |
| Cross-plan comparison (2 to 6 plans) | 40 | `comparison.jsonl` |
| Cost scenario (calculator, missing amount, unparsed cost) | 40 | `cost_scenario.jsonl` |
| Exclusions and limitations (listed, excluded, limited, not mentioned) | 40 | `exclusions_limitations.jsonl` |
| In- vs out-of-network (incl. Tier 1/Tier 2, IHCP, Level 1 Pharmacy, preferred) | 30 | `network_tiers.jsonl` |
| Coverage examples (Peg, Joe, Mia) | 25 | `coverage_examples.jsonl` |
| Discovery and ranking | 20 | `discovery_ranking.jsonl` |
| Ambiguous (duplicate names, no plan, unclear tier or service) | 30 | `ambiguous.jsonl` |
| Out of scope | 30 | `out_of_scope.jsonl` |
| Adversarial (injection, PII, wrong-plan and number bait) | 30 | `adversarial.jsonl` |
| Multi-turn (3 to 5 turns) | 25 | `multi_turn.jsonl` |
| REVIEW flags (5 carried notes, 5 inferred events) | 10 | `review_flags.jsonl` |
| **Total** | **410** | |

Expected behavior: 321 answer, 42 clarify, 38 refuse, 9 partial. Bots: 237 broker, 92 public, 81 both.
Difficulty: 138 easy, 176 medium, 96 hard. 557 expected values; 327 items have gold chunk hashes for the
retrieval eval.

Spread: 487 plans in all 18 states and all 5 markets (Individual 278, Small Group 118, Large Group 37, ABF 33,
MEWA 21), both portals (Anthem 365, Wellpoint 122), all 11 column layouts in the bundle, and 11 plan types
(EPO, EPO + HSA, HMO, HMO + HSA, POS, POS + HRA, POS + HSA, PPO, PPO + HRA, PPO+HRA, PPO + HSA).

## How the items were drafted

1. **Plans** were chosen with a seeded random draw, spreading items across states, markets, column layouts,
   portals and plan types, and preferring plans not used yet.
2. **Wording** comes from templates written for each category, using everyday terms ("PCP visit", "MRI",
   "out-of-pocket max"). Plans are named by contract code, by UI selection
   (`context.ui_selected_plan_keys`) or by name (`context.plan_mentions`, which stands in for the classifier's
   plan mentions until Phase 1). Every item that names a plan was run through the deterministic resolver, so
   it resolves to its gold plans, or stays unresolved when gold says to clarify.
3. **Values** were copied from facts bundle `2027.1`: SBC wording, page numbers and chunk hashes. Benefit
   values carry their row's limitations in `must_include`, as PLAN.md 3.3 rule 2 requires.
4. **Independent PDF check.** Gold copied from the bundle would pass even when the extractor is wrong, so
   every expected value was then checked with `evals/harness/pdf_check.py`. That check reads the PDF with
   poppler's word coordinates (not pdfplumber, which built the bundle) and finds the value in its own cell.
   Result: 553 of the 557 expected values were found in their own cell (503 exactly, 31 the same apart from
   a line break inside a word, 19 differing only by the converter's `Label: ` markup). The other 4 were
   confirmed by reading the rendered PDF page: an answer that continues onto the next page (gs-000162), the
   last answer on a page (gs-000164), a label printed twice across two pages (gs-000191) and a short last row
   (gs-000188). `check_against_bundle` reports no disagreements.

Every `must_include` string that is SBC text was also found in the plan's PDF: list items in order on their
page, and limitations in the Limitations column of the service's row. Some limitations sit in a box spanning
several rows, and some differ from the PDF only by the converter's markup; those are listed below. Three
limitations the check couldn't separate from the next column (gs-000194 twice, gs-000246) were confirmed by
reading the page. The six "not mentioned" terms (LASIK, teeth whitening, laser hair removal, massage therapy,
a gym membership, tattoo removal) appear nowhere in their plan's PDF.

This is still drafted gold (`provenance.method: "synthetic"`), not a substitute for reading the PDF.

## Where to look hardest

- **Values confirmed by reading the page, not by the cell check:** gs-000162, gs-000164, gs-000188,
  gs-000191; and limitations in gs-000194 and gs-000246.
- **Values matched modulo a line break inside a word:** gs-000006, gs-000036, gs-000051, gs-000052,
  gs-000053, gs-000055, gs-000080, gs-000151, gs-000152, gs-000158, gs-000161, gs-000202, gs-000208,
  gs-000210, gs-000233, gs-000239, gs-000242, gs-000245, gs-000248, gs-000286, gs-000370, gs-000371,
  gs-000375, gs-000376, gs-000378, gs-000403.
- **Values matched modulo the converter's `Label: ` markup:** gs-000016, gs-000030, gs-000045, gs-000061,
  gs-000081, gs-000176, gs-000198, gs-000219, gs-000242, gs-000255, gs-000277, gs-000279, gs-000287,
  gs-000350.
- **Limitations printed in a box spanning several rows** (the converter repeats such a note on each row it
  covers; confirm the box covers this row): gs-000017, gs-000021, gs-000025, gs-000031, gs-000032,
  gs-000041, gs-000044, gs-000046, gs-000054, gs-000059, gs-000074, gs-000086, gs-000147, gs-000148,
  gs-000179, gs-000180, gs-000182, gs-000183, gs-000187, gs-000193, gs-000197, gs-000204, gs-000215,
  gs-000218, gs-000220, gs-000229, gs-000238, gs-000252, gs-000253, gs-000299, gs-000397, gs-000405,
  gs-000406, gs-000407, and the markup items above.
- **Expected behavior is a judgment call:** the unclear-tier and ambiguous-service items in
  `ambiguous.jsonl` (gs-000113 to gs-000115 and gs-000345 to gs-000350) say "clarify"; showing every value
  clearly labeled may be equally acceptable.

## What a reviewer checks

For each item, open each plan's PDF (its `source_url`, or the file under `data/raw/`) at
`provenance.pdf_pages` and confirm:

- each `gold.expected_values[].verbatim` is printed exactly so, in that plan's row and column (or answer, or
  coverage example line);
- each `gold.must_include` string that is SBC text is printed in that plan's SBC;
- the expected behavior (`answer`, `clarify`, `refuse`, `partial`) is right for the question;
- the wording is a fair question.

Then set `provenance.verified_against` to `"pdf"` and fill in `reviewed_by` and `reviewed_at`. Corrections to
gold need two reviewers and a changelog entry (PLAN.md 6.4); bump the patch version.

## Conventions and known limits

- `must_include` mixes SBC text (list items, limitations) with text the renderer must add by design:
  "Premiums aren't included" for rankings (PLAN.md 3.4.4) and the carried-note label "In the SBC this note
  appears in a box continuing from the previous page." (PLAN.md 3.2.2). The PDF check covers
  `expected_values` only.
- Cost scenarios list the SBC facts an estimate must use; they don't state totals, because the calculator
  (`estimate_cost`) is Phase 2 and its totals are calculator facts, never typed by a model.
- Discovery and ranking gold sets were computed from the bundle: plans whose Other Covered Services list
  names the item, or plans tied at the lowest exactly parsed in-network individual deductible among active
  Individual plans of a metal tier and state. Groups with any unparsed deductible were left out, so the gold
  set is complete. Metal tier is plan-listing data, not SBC text.
- "Not mentioned" items were chosen only where the term appears in none of the plan's SBC chunks.
- The seven unparsed-cost items cover lab work, outpatient mental health and PCP visits, the only medical
  services with unparsed cost strings in the corpus.
- Multi-turn items include short assistant turns in the renderer's style. Gold applies to the last user turn.
- Phrasing is templated and not yet paraphrased (PLAN.md 6.1 step 2 adds paraphrases with human review).
