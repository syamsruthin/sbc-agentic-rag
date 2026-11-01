"""Fixed parts of the CMS SBC template that both the bundle build and the runtime refer to."""

from __future__ import annotations

from typing import Literal

QuestionId = Literal[
    "overall_deductible",
    "services_before_deductible",
    "other_deductibles",
    "oop_limit",
    "not_in_oop",
    "network_pays_less",
    "specialist_referral",
]

# The seven Important Questions, in template order, as printed in every SBC.
QUESTIONS: dict[QuestionId, str] = {
    "overall_deductible": "What is the overall deductible?",
    "services_before_deductible": "Are there services covered before you meet your deductible?",
    "other_deductibles": "Are there other deductibles for specific services?",
    "oop_limit": "What is the out-of-pocket limit for this plan?",
    "not_in_oop": "What is not included in the out-of-pocket limit?",
    "network_pays_less": "Will you pay less if you use a network provider?",
    "specialist_referral": "Do you need a referral to see a specialist?",
}
QUESTION_IDS: tuple[QuestionId, ...] = tuple(QUESTIONS)

# Questions whose answer is also parsed into a limit (plan_limit.kind).
LIMIT_QUESTIONS: dict[str, QuestionId] = {"deductible": "overall_deductible", "oop": "oop_limit"}

ExampleId = Literal["peg", "joe", "mia"]
EXAMPLE_IDS: tuple[ExampleId, ...] = ("peg", "joe", "mia")

ListId = Literal["excluded", "other_covered"]
LIST_TITLES: dict[ListId, str] = {"excluded": "Excluded Services", "other_covered": "Other Covered Services"}
