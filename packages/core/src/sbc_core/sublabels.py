"""Sub-benefit labels that SBC cost cells print on their own line above an amount, e.g. "Office Visit".

The converter renders them as ``Label: amount; Label: amount``. Only labels on this list are split out. Any
other short line is wrapped text and stays inline, so the Markdown never gains punctuation the PDF does not
print (``20% coinsurance, Durable Medical Equipment deductible applies`` must not become
``... Durable Medical; Equipment: deductible applies``). Each entry was checked against the 2027 PDFs.
"""

from __future__ import annotations

KNOWN_SUBLABELS = frozenset(
    {
        "Office Visit",
        "Other Outpatient",
        "Lab – Office",
        "X-Ray – Office",
        "Lab",
        "X-Ray",
        "Level 1",
        "Level 2",
        "Speech",
        "PCP",
        "Specialist",
        "Designated PCP",
        "Designated Specialist",
        "Designated Provider",
        "Preferred PCP",
        "Preferred Specialist",
        "Participating Provider",
        "Participating Specialist",
        "EPHC",
        "PPC",
        "Outpatient",
        "Inpatient",
        "Lower Copay PCP",
        "Standard Copay PCP",
    }
)
_MAX_LABEL_LINES = 3


def merge_label_lines(lines: list[str]) -> list[str]:
    """Re-join a label the PDF wrapped over several lines ("Designated" / "Specialist")."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        for n in range(_MAX_LABEL_LINES, 1, -1):
            joined = " ".join(lines[i : i + n])
            if i + n <= len(lines) and joined in KNOWN_SUBLABELS:
                out.append(joined)
                i += n
                break
        else:
            out.append(lines[i])
            i += 1
    return out
