"""Write the cost-grammar review sheet for human sign-off (PLAN.md section 11, Phase 0).

Groups every distinct cost string in a facts bundle by pattern (amounts replaced by $N, percentages by P%),
with cell counts, one example and how it parsed, so a reviewer can check each pattern once.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path


def _pattern(text: str) -> str:
    text = re.sub(r"\$[\d,]+(?:\.\d+)?", "$N", text)
    text = re.sub(r"\b\d+%", "P%", text)
    return re.sub(r"\b(first|up to) \d+ ", r"\1 N ", text)


def _summary(parse_json: str) -> str:
    parse = json.loads(parse_json)
    bits = []
    for part in parse.get("parts", []):
        label = f"{part['sublabel']}: " if part.get("sublabel") else ""
        for ch in part["channels"]:
            steps = []
            for t in ch["steps"]:
                fields = {k: v for k, v in t.items() if v not in (None, False, "not_stated") and k != "kind"}
                steps.append(
                    t["kind"] + (f"({', '.join(f'{k}={v}' for k, v in fields.items())})" if fields else "")
                )
            chan = "" if ch["channel"] == "all" else f"[{ch['channel']}] "
            bits.append(label + chan + " then ".join(steps))
    return "; ".join(bits)


def write_review(bundle: Path, out: Path) -> tuple[int, int]:
    with closing(sqlite3.connect(bundle)) as db:
        rows = db.execute(
            """SELECT c.verbatim, c.status, c.parse_json, COUNT(b.cost_id)
               FROM cost_string c LEFT JOIN plan_benefit b USING (cost_id) GROUP BY c.cost_id"""
        ).fetchall()
    groups: dict[tuple[str, str], list[tuple[str, str, int]]] = defaultdict(list)
    for verbatim, status, parse_json, cells in rows:
        groups[(_pattern(verbatim), status)].append((verbatim, parse_json, cells))
    ordered = sorted(groups.items(), key=lambda kv: -sum(c for _, _, c in kv[1]))
    total = sum(c for *_, c in rows)
    lines = [
        "# Cost grammar review",
        "",
        f"Bundle: `{bundle.name}`. {len(rows)} distinct cost strings, {total} cells,"
        f" {len(ordered)} patterns.",
        "Tick each pattern once you have checked that the parse matches the SBC wording.",
        "",
        "| OK | Cells | Status | Pattern | Example | Parse |",
        "|---|---|---|---|---|---|",
    ]
    for (pattern, status), items in ordered:
        cells = sum(c for _, _, c in items)
        example, parse_json, _ = max(items, key=lambda i: i[2])
        parse = _summary(parse_json) if status == "exact" else json.loads(parse_json).get("reason") or status
        row = [" ", str(cells), status, pattern, example, parse]
        lines.append("| " + " | ".join(c.replace("|", "\\|") for c in row) + " |")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(ordered), total


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Write the cost-grammar review sheet from a facts bundle.")
    ap.add_argument("bundle", type=Path)
    ap.add_argument("--out", type=Path, default=Path("docs/reviews/cost-grammar-review.md"))
    args = ap.parse_args(argv)
    patterns, cells = write_review(args.bundle, args.out)
    print(f"{args.out}: {patterns} patterns covering {cells} cells")
