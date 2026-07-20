"""Tests for the retrospective category refinement flow."""

from datetime import date

from mtd_classifier.classifier.rule_engine import RuleBasedClassifier


def _seed(repo):
    log = repo.create_import_log(
        filename="r.csv", file_hash="refine_h1", bank="firstdirect", fmt="csv",
        record_count=3,
    )
    ids = {}
    for i, (desc, amount, category) in enumerate([
        ("COUNCIL TAX BRISTOL", -150.0, "property_other_expense"),
        ("MISC BUSINESS COST", -60.0, "se_other_expense"),
        ("PLUMBER J SMITH", -180.0, "property_repairs"),  # already refined
    ]):
        txn = repo.create_raw_transaction(
            import_log_id=log.id, bank="firstdirect", txn_date=date(2026, 4, 20 + i),
            description=desc, amount=amount, dedup_hash=f"refine_t{i}",
        )
        repo.set_categorisation(
            raw_transaction_id=txn.id, category=category,
            confidence="manual", changed_by="user",
        )
        repo.upsert_exact_rule(desc, category)
        ids[desc] = txn.id
    repo.commit()
    return ids


def test_uncategorise_by_categories_clears_and_audits(repo):
    ids = _seed(repo)
    result = repo.uncategorise_by_categories(
        ["property_other_expense", "se_other_expense"], changed_by="refine",
    )
    repo.commit()
    assert result["transactions"] == 2
    assert repo.get_categorisation(ids["COUNCIL TAX BRISTOL"]) is None
    assert repo.get_categorisation(ids["MISC BUSINESS COST"]) is None
    # Unrelated categories untouched
    assert repo.get_categorisation(ids["PLUMBER J SMITH"]).category == "property_repairs"


def test_uncategorise_deactivates_learned_rules(repo):
    _seed(repo)
    result = repo.uncategorise_by_categories(
        ["property_other_expense", "se_other_expense"],
    )
    repo.commit()
    assert result["rules_deactivated"] == 2
    active = {r.match_value for r in repo.get_active_rules() if r.rule_type == "exact"}
    assert "council tax bristol" not in active
    assert "misc business cost" not in active
    assert "plumber j smith" in active  # refined category's rule survives


def test_count_categorised_by_categories(repo):
    _seed(repo)
    counts = repo.count_categorised_by_categories(
        ["property_other_expense", "se_other_expense", "se_travel"],
    )
    assert counts == {"property_other_expense": 1, "se_other_expense": 1}


def test_builtin_rules_now_place_box_24_items_correctly():
    classifier = RuleBasedClassifier()
    for desc in ("COUNCIL TAX EXAMPLETOWN", "GROUND RENT 1 EXAMPLE RD",
                 "SERVICE CHARGE Q2", "WATER RATES EXAMPLE WATER CO"):
        result = classifier.classify(desc, -100.0)
        assert result.category == "property_insurance", desc


def test_reclassify_after_refinement_uses_builtins(repo):
    """The full loop: uncategorise the catch-alls, then auto-classification
    places council tax into box 24 instead of the old catch-all."""
    ids = _seed(repo)
    repo.uncategorise_by_categories(["property_other_expense", "se_other_expense"])
    repo.commit()
    classifier = RuleBasedClassifier(db_rules=repo.get_active_rules())
    result = classifier.classify("COUNCIL TAX BRISTOL", -150.0)
    assert result.category == "property_insurance"
