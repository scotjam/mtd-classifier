"""Tests for the repository CRUD layer."""

from datetime import date

import pytest

from mtd_classifier.storage.repository import Repository


def test_create_import_log(repo):
    log = repo.create_import_log(
        filename="test.csv",
        file_hash="abc123",
        bank="firstdirect",
        fmt="csv",
        record_count=10,
        new_record_count=8,
    )
    assert log.id is not None
    assert log.filename == "test.csv"
    assert log.file_hash == "abc123"
    assert log.record_count == 10


def test_get_import_log_by_hash(repo):
    repo.create_import_log(
        filename="test.csv",
        file_hash="hash123",
        bank="firstdirect",
        fmt="csv",
    )
    found = repo.get_import_log_by_hash("hash123")
    assert found is not None
    assert found.filename == "test.csv"

    not_found = repo.get_import_log_by_hash("nonexistent")
    assert not_found is None


def test_create_raw_transaction(repo):
    log = repo.create_import_log(
        filename="test.csv", file_hash="h1", bank="firstdirect", fmt="csv"
    )
    txn = repo.create_raw_transaction(
        import_log_id=log.id,
        bank="firstdirect",
        txn_date=date(2026, 4, 15),
        description="TESCO STORES",
        amount=-45.67,
        dedup_hash="txn_hash_1",
        balance=4954.33,
    )
    assert txn.id is not None
    assert txn.description == "TESCO STORES"
    assert txn.amount == -45.67


def test_dedup_hash_unique(repo):
    log = repo.create_import_log(
        filename="test.csv", file_hash="h2", bank="firstdirect", fmt="csv"
    )
    repo.create_raw_transaction(
        import_log_id=log.id,
        bank="firstdirect",
        txn_date=date(2026, 4, 15),
        description="TEST",
        amount=-10.00,
        dedup_hash="unique_hash",
    )
    found = repo.get_raw_transaction_by_hash("unique_hash")
    assert found is not None
    assert found.description == "TEST"


def test_get_transactions_in_period(repo):
    log = repo.create_import_log(
        filename="test.csv", file_hash="h3", bank="firstdirect", fmt="csv"
    )
    for i, d in enumerate([date(2026, 4, 10), date(2026, 4, 20), date(2026, 5, 10)]):
        repo.create_raw_transaction(
            import_log_id=log.id,
            bank="firstdirect",
            txn_date=d,
            description=f"TXN {i}",
            amount=-10.00,
            dedup_hash=f"hash_{i}",
        )

    txns = repo.get_transactions_in_period(date(2026, 4, 1), date(2026, 4, 30))
    assert len(txns) == 2


def test_set_and_get_categorisation(repo):
    log = repo.create_import_log(
        filename="test.csv", file_hash="h4", bank="firstdirect", fmt="csv"
    )
    txn = repo.create_raw_transaction(
        import_log_id=log.id,
        bank="firstdirect",
        txn_date=date(2026, 4, 15),
        description="MORTGAGE",
        amount=-850.00,
        dedup_hash="cat_hash_1",
    )

    cat = repo.set_categorisation(
        raw_transaction_id=txn.id,
        category="mortgage_interest",
        confidence="pattern",
        changed_by="system",
    )
    assert cat.category == "mortgage_interest"

    found = repo.get_categorisation(txn.id)
    assert found is not None
    assert found.category == "mortgage_interest"


def test_update_categorisation_creates_audit(repo):
    log = repo.create_import_log(
        filename="test.csv", file_hash="h5", bank="firstdirect", fmt="csv"
    )
    txn = repo.create_raw_transaction(
        import_log_id=log.id,
        bank="firstdirect",
        txn_date=date(2026, 4, 15),
        description="TEST",
        amount=-10.00,
        dedup_hash="audit_hash_1",
    )

    # First categorisation
    repo.set_categorisation(
        raw_transaction_id=txn.id,
        category="personal",
        confidence="default",
        changed_by="system",
    )

    # Update categorisation
    repo.set_categorisation(
        raw_transaction_id=txn.id,
        category="se_office_costs",
        confidence="manual",
        changed_by="user",
    )

    # Verify audit trail
    from mtd_classifier.models.audit import AuditLog
    from sqlalchemy import select

    stmt = select(AuditLog).where(AuditLog.raw_transaction_id == txn.id)
    audits = repo.session.execute(stmt).scalars().all()
    assert len(audits) == 2

    # Second audit should record the change
    assert audits[1].old_category == "personal"
    assert audits[1].new_category == "se_office_costs"
    assert audits[1].changed_by == "user"


def test_uncategorised_transactions(repo):
    log = repo.create_import_log(
        filename="test.csv", file_hash="h6", bank="firstdirect", fmt="csv"
    )
    txn1 = repo.create_raw_transaction(
        import_log_id=log.id,
        bank="firstdirect",
        txn_date=date(2026, 4, 15),
        description="TXN1",
        amount=-10.00,
        dedup_hash="uncat_hash_1",
    )
    txn2 = repo.create_raw_transaction(
        import_log_id=log.id,
        bank="firstdirect",
        txn_date=date(2026, 4, 16),
        description="TXN2",
        amount=-20.00,
        dedup_hash="uncat_hash_2",
    )

    # Categorise only txn1
    repo.set_categorisation(
        raw_transaction_id=txn1.id,
        category="personal",
        confidence="default",
        changed_by="system",
    )

    uncategorised = repo.get_uncategorised_transactions()
    assert len(uncategorised) == 1
    assert uncategorised[0].id == txn2.id


def test_create_and_get_rules(repo):
    rule = repo.create_rule(
        rule_type="exact",
        match_value="tesco stores",
        category="personal",
        priority=100,
    )
    assert rule.id is not None

    active_rules = repo.get_active_rules()
    assert len(active_rules) == 1
    assert active_rules[0].category == "personal"


def test_upsert_exact_rule(repo):
    # Create initial rule
    rule1 = repo.upsert_exact_rule("TESCO STORES", "personal")
    assert rule1.category == "personal"

    # Update same description to new category
    rule2 = repo.upsert_exact_rule("TESCO STORES", "se_office_costs")
    assert rule2.id == rule1.id
    assert rule2.category == "se_office_costs"
    assert rule2.hit_count == 1


def test_delete_rule(repo):
    rule = repo.create_rule(
        rule_type="pattern",
        match_value=r"(?i)\btest\b",
        category="personal",
    )
    assert repo.delete_rule(rule.id) is True

    # Should not appear in active rules
    active = repo.get_active_rules()
    assert len(active) == 0


def test_count_methods(repo):
    assert repo.count_transactions() == 0
    assert repo.count_categorised() == 0

    log = repo.create_import_log(
        filename="test.csv", file_hash="h7", bank="firstdirect", fmt="csv"
    )
    txn = repo.create_raw_transaction(
        import_log_id=log.id,
        bank="firstdirect",
        txn_date=date(2026, 4, 15),
        description="TEST",
        amount=-10.00,
        dedup_hash="count_hash",
    )
    assert repo.count_transactions() == 1

    repo.set_categorisation(
        raw_transaction_id=txn.id,
        category="personal",
        confidence="default",
        changed_by="system",
    )
    assert repo.count_categorised() == 1
