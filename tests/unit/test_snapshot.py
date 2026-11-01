"""Corpus count snapshot."""

from __future__ import annotations

import hashlib

from sbc_ingest.snapshot import snapshot


def test_snapshot_counts_and_fingerprint() -> None:
    rows = [
        {"sbc_id": "2", "portal": "anthem", "state": "OH", "market_unit": "Small Group"},
        {"sbc_id": "1", "portal": "anthem", "state": "OH", "market_unit": "Individual"},
        {"sbc_id": "3", "portal": "wellpoint", "state": "FL", "market_unit": "Individual"},
    ]
    text = snapshot(rows, "2027")
    assert "- SBCs: **3**" in text
    assert "- Portals: anthem 2, wellpoint 1" in text
    assert "- Markets: Individual 2, Small Group 1" in text
    assert hashlib.sha256(b"1\n2\n3").hexdigest() in text
    assert "| FL | 1 |  | 1 |" in text
    assert "| OH | 1 | 1 | 2 |" in text
