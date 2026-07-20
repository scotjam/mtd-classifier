"""Extract candidate gross-pay figures from a payslip or P60 PDF.

A P60 is an annual certificate - any pay figure on it is the FULL YEAR
gross. A payslip only ever covers one pay period (typically a month), so a
figure on it is that period's pay, not the year's - confirming it means
confirming MONTHLY pay, which the caller then projects to a full year
(x12). The two must not be confused, so this module first classifies the
document itself (by looking for "P60" / "End of Year Certificate" markers),
then only extracts the candidates that make sense for that document type:
period-only labels ("Gross Pay", "Total Pay") for payslips, and any pay
figure at all for a P60 (a P60 has no period figures to filter out).

The caller MUST show these candidates to the user and let them pick (or
enter a figure manually) - never treat any candidate as automatically
correct. See models/employment_income.py for why: the confirmed gross figure
directly affects what a tax return declares, so a silent guess is not
acceptable here even though it's a defensible shortcut for parsing bank
statements.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MONEY_RE = re.compile(r"£?\s*(-?\d[\d,]*\.\d{2})")

P60_MARKERS = re.compile(
    r"\bP60\b|END OF YEAR CERTIFICATE|CERTIFICATE OF (PAY|EARNINGS)", re.IGNORECASE
)

# Labels that only ever describe ONE pay period's gross figure - the only
# candidates offered from a payslip, since that's what "confirm monthly pay"
# means. On a P60 these same labels still just mean "the pay figure", since a
# P60 has no period breakdown at all.
PERIOD_LABEL_PATTERNS = (
    (re.compile(r"gross\s*pay", re.IGNORECASE), "Gross pay"),
    (re.compile(r"pay\s*in\s*this\s*employment", re.IGNORECASE), "Pay in this employment"),
    (re.compile(r"total\s*pay\b(?!\s*to\s*date)", re.IGNORECASE), "Total pay"),
    (re.compile(r"\bgross\b", re.IGNORECASE), "Gross"),
)

# Cumulative/year-to-date labels - only meaningful to surface from a P60
# (where they genuinely mean the whole year); deliberately excluded from
# payslip extraction, since a payslip's YTD is not what "confirm monthly
# pay" asks for and is easy to mistake for the full year when it isn't.
ANNUAL_LABEL_PATTERNS = (
    (re.compile(r"total\s*pay\s*to\s*date", re.IGNORECASE), "Total pay to date"),
    (re.compile(r"total\s*for\s*(this\s*)?year", re.IGNORECASE), "Total for year"),
    (re.compile(r"taxable\s*gross\s*(ytd|year\s*to\s*date)", re.IGNORECASE), "Taxable gross YTD"),
    (re.compile(r"gross\s*(pay\s*)?ytd", re.IGNORECASE), "Gross YTD"),
)


@dataclass(frozen=True)
class PayFigureCandidate:
    """One money amount found next to a pay-related label.

    ``is_annual`` is True for a P60 figure (already the full year) and False
    for a payslip figure (one pay period - the caller should multiply by 12
    for a full-year projection).
    """

    label: str
    amount: float
    line: str  # the original line, for the user to sanity-check
    is_annual: bool


def detect_document_type(file_path: Path) -> str:
    """Returns "p60" or "payslip" based on document markers (best effort)."""
    try:
        import pdfplumber

        with pdfplumber.open(file_path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:
        return "payslip"
    return "p60" if P60_MARKERS.search(text) else "payslip"


def extract_candidates(file_path: Path) -> list[PayFigureCandidate]:
    """Scan a payslip/P60 PDF for candidate gross-pay figures.

    Returns candidates in document order; duplicates (same label text and
    amount) are collapsed. Returns an empty list for non-PDF or unreadable
    files rather than raising, since this is a best-effort assist.
    """
    try:
        import pdfplumber
    except ImportError:
        return []

    candidates: list[PayFigureCandidate] = []
    seen: set[tuple[str, float]] = set()

    try:
        with pdfplumber.open(file_path) as pdf:
            pages_text = [page.extract_text() or "" for page in pdf.pages]
    except Exception:
        return []

    is_p60 = bool(P60_MARKERS.search("\n".join(pages_text)))

    for text in pages_text:
        for line in text.splitlines():
            # Annual/YTD-sounding lines are checked first: on a P60 they're
            # exactly what we want, but on a payslip they must be skipped
            # entirely (not fall through to a looser period-label match -
            # e.g. "Gross Pay YTD" must not also be read as "Gross pay").
            match = next(
                ((p, lbl) for p, lbl in ANNUAL_LABEL_PATTERNS if p.search(line)), None
            )
            if match:
                if not is_p60:
                    continue
                _, label = match
            else:
                match = next(
                    ((p, lbl) for p, lbl in PERIOD_LABEL_PATTERNS if p.search(line)), None
                )
                if match is None:
                    continue
                _, label = match

            money_match = MONEY_RE.search(line)
            if not money_match:
                continue
            amount = float(money_match.group(1).replace(",", ""))
            key = (label, amount)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                PayFigureCandidate(
                    label=label, amount=amount, line=line.strip(), is_annual=is_p60,
                )
            )

    return candidates
