"""Audit log for tracking categorisation changes."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from mtd_classifier.models.base import Base


class AuditLog(Base):
    """Records every categorisation change for audit purposes."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_transaction_id: Mapped[int] = mapped_column(
        ForeignKey("raw_transactions.id")
    )
    old_category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    new_category: Mapped[str] = mapped_column(String(50))
    changed_by: Mapped[str] = mapped_column(String(50))  # "system", "user", "rule"
    changed_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )

    def __repr__(self) -> str:
        return (
            f"AuditLog(id={self.id}, tx={self.raw_transaction_id}, "
            f"{self.old_category!r} -> {self.new_category!r})"
        )
