"""Rule-based transaction classifier with three-tier priority."""

from __future__ import annotations

import re
from typing import Sequence

from mtd_classifier.classifier.base import ClassificationResult
from mtd_classifier.classifier.rules import BUILTIN_PATTERNS
from mtd_classifier.models.rule import ClassificationRule


class RuleBasedClassifier:
    """Classifies transactions using a three-tier rule system.

    Priority order:
    1. Exact match rules (from user corrections / learned rules)
    2. Pattern match rules (regex on description)
    3. Default fallback (personal)
    """

    def __init__(self, db_rules: Sequence[ClassificationRule] | None = None) -> None:
        self._exact_rules: dict[str, tuple[str, int]] = {}  # normalised_desc -> (category, rule_id)
        self._pattern_rules: list[tuple[re.Pattern, str, int | None]] = []

        # Load database rules
        if db_rules:
            for rule in db_rules:
                if not rule.is_active:
                    continue
                if rule.rule_type == "exact":
                    self._exact_rules[rule.match_value.lower()] = (
                        rule.category,
                        rule.id,
                    )
                elif rule.rule_type in ("pattern", "builtin"):
                    try:
                        compiled = re.compile(rule.match_value)
                        self._pattern_rules.append(
                            (compiled, rule.category, rule.id)
                        )
                    except re.error:
                        pass  # Skip invalid regex

        # Append built-in patterns (lower priority than DB patterns)
        for pattern, category in BUILTIN_PATTERNS:
            try:
                compiled = re.compile(pattern)
                self._pattern_rules.append((compiled, category, None))
            except re.error:
                pass

    def classify(self, description: str, amount: float) -> ClassificationResult:
        """Classify a transaction using the three-tier system."""
        # Tier 1: Exact match
        normalised = description.lower().strip()
        if normalised in self._exact_rules:
            category, rule_id = self._exact_rules[normalised]
            return ClassificationResult(
                category=category,
                confidence="exact",
                rule_id=rule_id,
                rule_description=f"Exact match: {normalised!r}",
            )

        # Tier 2: Pattern match
        for compiled_pattern, category, rule_id in self._pattern_rules:
            if compiled_pattern.search(description):
                return ClassificationResult(
                    category=category,
                    confidence="pattern",
                    rule_id=rule_id,
                    rule_description=f"Pattern: {compiled_pattern.pattern}",
                )

        # Tier 3: Default
        return ClassificationResult(
            category="personal",
            confidence="default",
            rule_description="No matching rule - defaulted to personal",
        )

    def learn(self, description: str, amount: float, category: str) -> None:
        """Learn from a user correction (in-memory only; DB persistence done elsewhere)."""
        normalised = description.lower().strip()
        self._exact_rules[normalised] = (category, -1)  # -1 = not yet persisted

    def get_suggestions(
        self, description: str, amount: float, n: int = 3
    ) -> list[ClassificationResult]:
        """Get top-N category suggestions.

        Returns the primary classification plus alternative pattern matches.
        """
        results: list[ClassificationResult] = []
        seen_categories: set[str] = set()

        # Primary result
        primary = self.classify(description, amount)
        results.append(primary)
        seen_categories.add(primary.category)

        # Additional pattern matches
        for compiled_pattern, category, rule_id in self._pattern_rules:
            if category in seen_categories:
                continue
            if compiled_pattern.search(description):
                results.append(
                    ClassificationResult(
                        category=category,
                        confidence="pattern",
                        rule_id=rule_id,
                        rule_description=f"Pattern: {compiled_pattern.pattern}",
                    )
                )
                seen_categories.add(category)
                if len(results) >= n:
                    break

        # Pad with default if needed
        if len(results) < n and "personal" not in seen_categories:
            results.append(
                ClassificationResult(
                    category="personal",
                    confidence="default",
                    rule_description="Default",
                )
            )

        return results[:n]
