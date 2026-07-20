"""Transaction models - raw imported data and categorised assignments."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, String, Date, Float, DateTime, ForeignKey, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mtd_classifier.models.base import Base


class RawTransaction(Base):
    """Immutable imported transaction data."""

    __tablename__ = "raw_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    import_log_id: Mapped[int] = mapped_column(ForeignKey("import_logs.id"))
    bank: Mapped[str] = mapped_column(String(50))
    account: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    date: Mapped[date] = mapped_column(Date)
    description: Mapped[str] = mapped_column(String(500))
    amount: Mapped[float] = mapped_column(Float)
    balance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    raw_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dedup_hash: Mapped[str] = mapped_column(String(64), unique=True)  # SHA-256
    # Comma-separated source families ("pdf", "tabular") that have evidenced
    # this transaction. Dedup keeps only one row per real transaction, so
    # when a second format's file confirms an existing row, its family is
    # appended here rather than storing a duplicate - this is what makes
    # cross-format verification (PDF total vs CSV/XLS total) possible later.
    source_formats: Mapped[str] = mapped_column(String(50), default="")
    imported_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )

    def has_source_family(self, family: str) -> bool:
        return family in (self.source_formats or "").split(",")

    def add_source_family(self, family: str) -> None:
        families = [f for f in (self.source_formats or "").split(",") if f]
        if family not in families:
            families.append(family)
            self.source_formats = ",".join(families)

    categorisation: Mapped[Optional["CategorisedTransaction"]] = relationship(
        back_populates="raw_transaction", uselist=False
    )
    import_log: Mapped["ImportLog"] = relationship()

    def __repr__(self) -> str:
        return (
            f"RawTransaction(id={self.id}, date={self.date}, "
            f"desc={self.description!r}, amount={self.amount})"
        )


class CategorisedTransaction(Base):
    """Category assignment for a raw transaction."""

    __tablename__ = "categorised_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_transaction_id: Mapped[int] = mapped_column(
        ForeignKey("raw_transactions.id"), unique=True
    )
    category: Mapped[str] = mapped_column(String(50))
    confidence: Mapped[str] = mapped_column(String(20))  # exact, pattern, default, manual
    # Whole transaction is disallowable for tax (e.g. a private-use share
    # paid separately). HMRC still wants it in the expense box total, with
    # the same amount in the matching disallowable box, so the net claim
    # excludes it. Categories in ALWAYS_DISALLOWABLE_CATEGORIES are treated
    # as disallowable regardless of this flag.
    disallowable: Mapped[bool] = mapped_column(Boolean, default=False)
    matched_rule_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("classification_rules.id"), nullable=True
    )
    categorised_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )

    raw_transaction: Mapped["RawTransaction"] = relationship(
        back_populates="categorisation"
    )

    def __repr__(self) -> str:
        return (
            f"CategorisedTransaction(id={self.id}, "
            f"raw_tx={self.raw_transaction_id}, category={self.category!r})"
        )
