"""Classification rule model."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from mtd_classifier.models.base import Base


class ClassificationRule(Base):
    """A rule for auto-classifying transactions."""

    __tablename__ = "classification_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_type: Mapped[str] = mapped_column(String(20))  # exact, pattern, builtin
    match_field: Mapped[str] = mapped_column(String(20), default="description")
    match_value: Mapped[str] = mapped_column(String(500))
    category: Mapped[str] = mapped_column(String(50))
    priority: Mapped[int] = mapped_column(Integer, default=0)  # higher = checked first
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    def __repr__(self) -> str:
        return (
            f"ClassificationRule(id={self.id}, type={self.rule_type!r}, "
            f"match={self.match_value!r}, category={self.category!r})"
        )
