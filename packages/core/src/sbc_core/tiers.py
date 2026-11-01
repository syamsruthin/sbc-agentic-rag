"""Provider tiers: SBC column labels and limit phrases mapped to canonical tier IDs.

Benefit rows use the column label printed in the SBC (e.g. "Non-IHCP In-Network Provider"); deductible and
out-of-pocket answers name tiers in prose ("for Tier 1 In-Network Providers and Tier 2 In-Network Providers
combined"). Both map to the same IDs so a fact can be joined to its limits and compared across plans.
"""

from __future__ import annotations

from typing import Literal

TierId = Literal["ihcp", "preferred", "in_net", "in_net_t1", "in_net_t2", "out_net", "rx_level1", "unlabeled"]

COLUMN_TIERS: dict[str, TierId] = {
    "Indian Health Care Provider (IHCP)": "ihcp",
    "Preferred Network Provider": "preferred",
    "In-Network Provider": "in_net",
    "Network Provider": "in_net",
    "Non-IHCP In-Network Provider": "in_net",
    "Tier 1 In-Network Provider": "in_net_t1",
    "Tier 2 In-Network Provider": "in_net_t2",
    "Out-of-Network Provider": "out_net",
    "Non-Network Provider": "out_net",
    "Non-IHCP Out-of-Network Provider": "out_net",
    "Non-IHCP Non-Network Provider": "out_net",
    "Level 1 Pharmacy- RX Only": "rx_level1",
    # Two SBCs print a single unlabeled "Provider" column; their rows are kept but flagged for review.
    "Provider": "unlabeled",
}

LIMIT_PHRASE_TIERS: dict[str, tuple[TierId, ...]] = {
    "In-Network Providers": ("in_net",),
    "Network Providers": ("in_net",),
    "Non-IHCP In-Network Providers": ("in_net",),
    "Preferred Network Providers": ("preferred",),
    "Tier 1 In-Network Providers": ("in_net_t1",),
    "Tier 2 In-Network Providers": ("in_net_t2",),
    "Non-IHCP Tier 1 In-Network Providers": ("in_net_t1",),
    "Non-IHCP Tier 2 In-Network Providers": ("in_net_t2",),
    "Tier 1 In-Network Providers and Tier 2 In-Network Providers combined": ("in_net_t1", "in_net_t2"),
    "Preferred Network Providers and In-Network Providers combined": ("preferred", "in_net"),
    "Out-of-Network Providers": ("out_net",),
    "Non-Network Providers": ("out_net",),
    "Non-IHCP Out-of-Network Providers": ("out_net",),
    "Non-IHCP Non-Network Providers": ("out_net",),
}


def column_tier(label: str) -> TierId:
    try:
        return COLUMN_TIERS[label]
    except KeyError:
        raise KeyError(f"unknown SBC tier column label: {label!r}") from None
