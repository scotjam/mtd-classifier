"""Import log model for file deduplication and audit."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import String, Integer, Date, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from mtd_classifier.models.base import Base


class ImportLog(Base):
    __tablename__ = "import_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(500))
    file_hash: Mapped[str] = mapped_column(String(64), unique=True)  # SHA-256
    bank: Mapped[str] = mapped_column(String(50))
    format: Mapped[str] = mapped_column(String(20))  # csv, txt, ofx
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    new_record_count: Mapped[int] = mapped_column(Integer, default=0)
    # The date range this file covers - a PDF statement's own printed
    # period, or the min/max transaction dates otherwise. What "the data
    # for this period has actually been imported" is judged against.
    period_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    period_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # Which account tab the file was imported into (None for bank-only CLI
    # imports) - lets coverage be assessed per account even for files whose
    # every transaction deduped away.
    account: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Where the file was when (last) imported, so a prompt can open the
    # source statement. None for imports made before this was recorded.
    source_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )

    def __repr__(self) -> str:
        return (
            f"ImportLog(id={self.id}, filename={self.filename!r}, "
            f"bank={self.bank!r}, records={self.record_count})"
        )
