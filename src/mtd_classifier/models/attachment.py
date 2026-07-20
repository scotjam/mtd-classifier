"""Receipts/invoices attached to a transaction (PDF or common image formats)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from mtd_classifier.models.base import Base


class Attachment(Base):
    """One file (receipt/invoice) attached to a raw transaction."""

    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_transaction_id: Mapped[int] = mapped_column(
        ForeignKey("raw_transactions.id")
    )
    original_filename: Mapped[str] = mapped_column(String(255))
    # Filename actually on disk under ATTACHMENTS_DIR/<raw_transaction_id>/ -
    # may differ from original_filename if that name was already taken.
    stored_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(20))  # "pdf" or "image"
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        return (
            f"Attachment(id={self.id}, tx={self.raw_transaction_id}, "
            f"file={self.original_filename!r})"
        )
