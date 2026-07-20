"""Tests for rule-based classifier."""

import pytest

from mtd_classifier.classifier.rule_engine import RuleBasedClassifier
from mtd_classifier.models.rule import ClassificationRule


@pytest.fixture
def classifier():
    """Classifier with no database rules (built-in only)."""
    return RuleBasedClassifier()


def test_pattern_match_mortgage(classifier):
    result = classifier.classify("MORTGAGE PAYMENT", -850.00)
    assert result.category == "mortgage_interest"
    assert result.confidence == "pattern"


def test_pattern_match_tesco(classifier):
    result = classifier.classify("TESCO STORES", -45.67)
    assert result.category == "personal"
    assert result.confidence == "pattern"


def test_pattern_match_letting_agent(classifier):
    result = classifier.classify("ACME LETTING AGENTS FEE", -150.00)
    assert result.category == "letting_agent_fees"
    assert result.confidence == "pattern"


def test_pattern_match_rent_received(classifier):
    result = classifier.classify("RENT RECEIVED FROM TENANT J SMITH", 950.00)
    assert result.category == "rental_income"
    assert result.confidence == "pattern"


def test_pattern_match_netflix(classifier):
    result = classifier.classify("NETFLIX.COM", -15.99)
    assert result.category == "personal"
    assert result.confidence == "pattern"


def test_pattern_match_transfer(classifier):
    result = classifier.classify("TRANSFER TO SAVINGS", -500.00)
    assert result.category == "transfer"
    assert result.confidence == "pattern"


def test_pattern_match_plumber(classifier):
    result = classifier.classify("PLUMBER J SMITH", -180.00)
    assert result.category == "property_repairs"
    assert result.confidence == "pattern"


def test_pattern_match_national_rail(classifier):
    result = classifier.classify("NATIONAL RAIL", -24.80)
    assert result.category == "se_travel"
    assert result.confidence == "pattern"


def test_default_fallback(classifier):
    result = classifier.classify("OBSCURE UNKNOWN MERCHANT XYZ123", -10.00)
    assert result.category == "personal"
    assert result.confidence == "default"


def test_exact_match_takes_priority():
    """Exact match rules should override pattern matches."""
    # Create a mock exact rule for "TESCO STORES" -> rental_income
    # (deliberately wrong to test priority)
    rule = ClassificationRule(
        id=1,
        rule_type="exact",
        match_field="description",
        match_value="tesco stores",
        category="rental_income",
        priority=100,
        hit_count=0,
        is_active=True,
    )
    classifier = RuleBasedClassifier(db_rules=[rule])

    result = classifier.classify("TESCO STORES", -45.67)
    assert result.category == "rental_income"
    assert result.confidence == "exact"
    assert result.rule_id == 1


def test_db_pattern_rule():
    """Database pattern rules should work alongside built-in rules."""
    rule = ClassificationRule(
        id=2,
        rule_type="pattern",
        match_field="description",
        match_value=r"(?i)CUSTOM\s+VENDOR",
        category="se_other_expense",
        priority=50,
        hit_count=0,
        is_active=True,
    )
    classifier = RuleBasedClassifier(db_rules=[rule])

    result = classifier.classify("CUSTOM VENDOR PAYMENT", -100.00)
    assert result.category == "se_other_expense"
    assert result.confidence == "pattern"
    assert result.rule_id == 2


def test_inactive_rule_ignored():
    """Inactive rules should not be used."""
    rule = ClassificationRule(
        id=3,
        rule_type="exact",
        match_field="description",
        match_value="tesco stores",
        category="rental_income",
        priority=100,
        hit_count=0,
        is_active=False,
    )
    classifier = RuleBasedClassifier(db_rules=[rule])

    result = classifier.classify("TESCO STORES", -45.67)
    # Should fall through to built-in pattern match
    assert result.category == "personal"
    assert result.confidence == "pattern"


def test_learn_creates_exact_match(classifier):
    # Before learning, unknown merchant defaults
    result = classifier.classify("MY SPECIAL SHOP", -20.00)
    assert result.confidence == "default"

    # Learn
    classifier.learn("MY SPECIAL SHOP", -20.00, "se_office_costs")

    # After learning, should be exact match
    result = classifier.classify("MY SPECIAL SHOP", -20.00)
    assert result.category == "se_office_costs"
    assert result.confidence == "exact"


def test_get_suggestions(classifier):
    suggestions = classifier.get_suggestions("MORTGAGE PAYMENT", -850.00, n=3)
    assert len(suggestions) <= 3
    assert suggestions[0].category == "mortgage_interest"


def test_case_insensitive_exact_match():
    rule = ClassificationRule(
        id=4,
        rule_type="exact",
        match_field="description",
        match_value="my shop ltd",
        category="se_other_expense",
        priority=100,
        hit_count=0,
        is_active=True,
    )
    classifier = RuleBasedClassifier(db_rules=[rule])

    result = classifier.classify("MY SHOP LTD", -50.00)
    assert result.category == "se_other_expense"
    assert result.confidence == "exact"
