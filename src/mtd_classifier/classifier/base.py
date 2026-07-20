"""Base protocol for transaction classifiers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class ClassificationResult:
    """Result of classifying a transaction."""

    category: str
    confidence: str  # "exact", "pattern", "default"
    rule_id: int | None = None
    rule_description: str | None = None


@runtime_checkable
class TransactionClassifier(Protocol):
    """Protocol for transaction classifiers."""

    def classify(self, description: str, amount: float) -> ClassificationResult:
        """Classify a transaction."""
        ...

    def learn(self, description: str, amount: float, category: str) -> None:
        """Learn from a user correction."""
        ...

    def get_suggestions(
        self, description: str, amount: float, n: int = 3
    ) -> list[ClassificationResult]:
        """Get top-N category suggestions for a transaction."""
        ...
