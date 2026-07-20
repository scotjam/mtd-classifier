"""UK tax year quarter calculations for Making Tax Digital."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# MTD quarterly periods within a UK tax year (6 Apr - 5 Apr):
# Q1: 6 Apr - 5 Jul
# Q2: 6 Jul - 5 Oct
# Q3: 6 Oct - 5 Jan (next calendar year)
# Q4: 6 Jan - 5 Apr

_QUARTER_BOUNDARIES = [
    # (start_month, start_day, end_month, end_day, quarter_number)
    (4, 6, 7, 5, 1),
    (7, 6, 10, 5, 2),
    (10, 6, 1, 5, 3),
    (1, 6, 4, 5, 4),
]


@dataclass(frozen=True)
class TaxQuarter:
    """Represents an MTD tax quarter."""

    tax_year: int  # The tax year starting April (e.g. 2026 means 2026-27)
    quarter: int   # 1-4

    @property
    def start_date(self) -> date:
        if self.quarter == 1:
            return date(self.tax_year, 4, 6)
        elif self.quarter == 2:
            return date(self.tax_year, 7, 6)
        elif self.quarter == 3:
            return date(self.tax_year, 10, 6)
        else:  # Q4
            return date(self.tax_year + 1, 1, 6)

    @property
    def end_date(self) -> date:
        if self.quarter == 1:
            return date(self.tax_year, 7, 5)
        elif self.quarter == 2:
            return date(self.tax_year, 10, 5)
        elif self.quarter == 3:
            return date(self.tax_year + 1, 1, 5)
        else:  # Q4
            return date(self.tax_year + 1, 4, 5)

    @property
    def label(self) -> str:
        return f"Q{self.quarter}-{self.tax_year}"

    def contains(self, d: date) -> bool:
        return self.start_date <= d <= self.end_date

    def __str__(self) -> str:
        return self.label


def tax_year_for_date(d: date) -> int:
    """Return the tax year a date falls in. Tax year starts 6 April."""
    if d >= date(d.year, 4, 6):
        return d.year
    return d.year - 1


def quarter_for_date(d: date) -> TaxQuarter:
    """Return the TaxQuarter a given date falls within."""
    ty = tax_year_for_date(d)
    for q in range(1, 5):
        tq = TaxQuarter(tax_year=ty, quarter=q)
        if tq.contains(d):
            return tq
    raise ValueError(f"Date {d} does not fall in any quarter (should not happen)")


def parse_period(period_str: str) -> TaxQuarter:
    """Parse a period string like 'Q1-2026' into a TaxQuarter."""
    period_str = period_str.strip().upper()
    if not period_str.startswith("Q") or "-" not in period_str:
        raise ValueError(f"Invalid period format: {period_str!r}. Expected 'Q1-2026'.")
    parts = period_str.split("-", 1)
    try:
        quarter = int(parts[0][1:])
        year = int(parts[1])
    except ValueError:
        raise ValueError(f"Invalid period format: {period_str!r}. Expected 'Q1-2026'.")
    if quarter < 1 or quarter > 4:
        raise ValueError(f"Quarter must be 1-4, got {quarter}")
    return TaxQuarter(tax_year=year, quarter=quarter)


def quarters_for_tax_year(tax_year: int) -> list[TaxQuarter]:
    """Return all four quarters for a given tax year."""
    return [TaxQuarter(tax_year=tax_year, quarter=q) for q in range(1, 5)]
