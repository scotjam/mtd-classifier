"""Confirmed employment income (P60/P45 gross figure) per tax year.

Bank deposits for a salary are NET (after-tax) pay, not the gross figure
HMRC's SA102 employment page needs - so employment income transactions are
tracked but never used to compute a reportable total on their own. This
model stores the actual confirmed gross figure once the user has supplied
it from their P60/P45 or payslips, so quarterly summaries and exports can
show that instead of (or a clear "unconfirmed" flag instead of) the
misleading bank-deposit sum.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Float, Integer, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from mtd_classifier.models.base import Base


# Sources whose confirmed figure is a running SUM OF DEPOSITS TO DATE (the
# per-deposit breakdown flows), not a full-year figure. Full-year estimates
# must scale these up; annual sources ("manual", "payslip_pdf" - a typed
# figure or a P60) are used as-is.
YTD_SOURCES = frozenset({"statement_breakdown", "paid_gross"})


class EmploymentIncomeRecord(Base):
    """One confirmed gross employment income figure per tax year."""

    __tablename__ = "employment_income_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    tax_year: Mapped[int] = mapped_column(Integer, unique=True)
    gross_amount: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20))  # "manual", "payslip_pdf", "statement_breakdown" or "paid_gross"
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )

    @property
    def is_year_to_date(self) -> bool:
        """Whether gross_amount is a to-date deposit sum rather than annual."""
        return self.source in YTD_SOURCES

    def __repr__(self) -> str:
        return (
            f"EmploymentIncomeRecord(tax_year={self.tax_year}, "
            f"gross={self.gross_amount}, source={self.source!r})"
        )


class EmploymentGrossEntry(Base):
    """Per-deposit gross (pre-tax) amount for one salary transaction.

    The bank statement records the NET deposit; the user amends each one
    with the gross figure from the matching payslip (or confirms the pay
    is not taxed at source, in which case gross == deposit). The per-year
    confirmed total in EmploymentIncomeRecord is the sum of these.
    """

    __tablename__ = "employment_gross_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_transaction_id: Mapped[int] = mapped_column(Integer, unique=True)
    gross_amount: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20))  # "manual", "payslip_pdf" or "paid_gross"
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    entered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        return (
            f"EmploymentGrossEntry(raw_transaction_id={self.raw_transaction_id}, "
            f"gross={self.gross_amount}, source={self.source!r})"
        )
