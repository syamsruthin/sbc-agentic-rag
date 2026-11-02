# Golden dataset 0.1.0 (draft, pending human verification)

150 items across the PLAN.md section 6.2 categories, scaled from the MVP targets (about 410) to 150. Schema:
`evals/harness/golden.py` (PLAN.md 6.3), one JSONL file per category.

**Status: every item is `verified_against: "pending"`, so none gates a merge yet.** An item becomes gating
when a person has checked it against the SBC PDF and filled in `reviewed_by` and `reviewed_at`.

## Contents

| Category | Items | File |
|---|---:|---|
| Single-field lookup | 22 | `single_field.jsonl` |
| Multi-field | 11 | `multi_field.jsonl` |
| Cross-plan comparison (2 to 6 plans) | 15 | `comparison.jsonl` |
| Cost scenario (calculator, missing amount, unparsed cost) | 14 | `cost_scenario.jsonl` |
| Exclusions and limitations (listed, excluded, limited, not mentioned) | 15 | `exclusions_limitations.jsonl` |
| In- vs out-of-network (incl. Tier 1/Tier 2, IHCP, Level 1 Pharmacy, preferred) | 11 | `network_tiers.jsonl` |
| Coverage examples (Peg, Joe, Mia) | 9 | `coverage_examples.jsonl` |
| Discovery and ranking | 7 | `discovery_ranking.jsonl` |
| Ambiguous (duplicate names, no plan, unclear tier or service) | 11 | `ambiguous.jsonl` |
| Out of scope | 11 | `out_of_scope.jsonl` |
| Adversarial (injection, PII, wrong-plan and number bait) | 11 | `adversarial.jsonl` |
| Multi-turn (3 to 5 turns) | 9 | `multi_turn.jsonl` |
| REVIEW flags (2 carried notes, 2 inferred events) | 4 | `review_flags.jsonl` |
| **Total** | **150** | |

Expected behavior: 117 answer, 15 clarify, 14 refuse, 4 partial. Bots: 86 broker, 34 public, 30 both.
Difficulty: 49 easy, 69 medium, 32 hard. 206 expected values; 119 items have gold chunk hashes for the
retrieval eval.

Spread: 181 plans in all 18 states and all 5 markets (Individual 111, Small Group 33, MEWA 14, ABF 12,
Large Group 11), both portals (Anthem 136, Wellpoint 45), all 11 column layouts in the bundle, and the plan
types EPO, HMO, HMO + HSA, POS, POS + HRA, POS + HSA, PPO, PPO + HRA and PPO + HSA.

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
   Result: all 206 expected values were found in their own cell (190 exactly, 9 the same apart from a line
   break inside a word, 7 differing only by the converter's `Label: ` markup). `check_against_bundle` reports
   no disagreements.

Every `must_include` string that is SBC text was also found in the plan's PDF: list items in order on their
page, and limitations in the Limitations column of the service's row. Some limitations sit in a box spanning
several rows, and some differ from the PDF only by the converter's markup; those are listed below. The three
"not mentioned" terms (LASIK, teeth whitening, laser hair removal) appear nowhere in their plan's PDF.

This is still drafted gold (`provenance.method: "synthetic"`), not a substitute for reading the PDF.

## Where to look hardest

- **Values matched modulo wrapping or markup:** a line break inside a word: gs-000006, gs-000036,
  gs-000051, gs-000052, gs-000053, gs-000055, gs-000080. Converter `Label: ` markup: gs-000016, gs-000030,
  gs-000045, gs-000061, gs-000081.
- **Limitations printed in a box spanning several rows** (the converter repeats such a note on each row it
  covers; confirm the box covers this row): gs-000017, gs-000021, gs-000025, gs-000031, gs-000032,
  gs-000041, gs-000044, gs-000046, gs-000054, gs-000059, gs-000074, gs-000086, gs-000147, gs-000148, and
  the markup items above.
- **Expected behavior is a judgment call:** the unclear-tier and ambiguous-service items in
  `ambiguous.jsonl` (gs-000113 to gs-000115) say "clarify"; showing both values clearly labeled may be
  equally acceptable.

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
- Multi-turn items include short assistant turns in the renderer's style. Gold applies to the last user turn.
- Phrasing is templated and not yet paraphrased (PLAN.md 6.1 step 2 adds paraphrases with human review).
