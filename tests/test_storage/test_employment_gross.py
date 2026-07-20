"""Tests for per-deposit employment gross entries (EmploymentGrossEntry)."""

from datetime import date


def _seed_txn(repo, i=0):
    log = repo.create_import_log(
        filename=f"gross_{i}.csv", file_hash=f"gross_hash_{i}", bank="firstdirect",
        fmt="csv", record_count=1,
    )
    txn = repo.create_raw_transaction(
        import_log_id=log.id, bank="firstdirect", txn_date=date(2026, 4, 28),
        description="ACME PAYROLL", amount=2400.0, dedup_hash=f"gross_txn_{i}",
    )
    repo.commit()
    return txn, log


def test_set_and_get_employment_gross_entry(repo):
    txn, _ = _seed_txn(repo)
    repo.set_employment_gross_entry(txn.id, 3100.0, source="manual")
    repo.commit()
    entries = repo.get_employment_gross_entries([txn.id])
    assert entries[txn.id].gross_amount == 3100.0
    assert entries[txn.id].source == "manual"


def test_set_employment_gross_entry_overwrites_not_duplicates(repo):
    txn, _ = _seed_txn(repo)
    repo.set_employment_gross_entry(txn.id, 3100.0, source="manual")
    repo.set_employment_gross_entry(txn.id, 3200.0, source="payslip_pdf", note="april.pdf")
    repo.commit()
    entries = repo.get_employment_gross_entries([txn.id])
    assert len(entries) == 1
    assert entries[txn.id].gross_amount == 3200.0
    assert entries[txn.id].source == "payslip_pdf"
    assert entries[txn.id].note == "april.pdf"


def test_delete_employment_gross_entries(repo):
    txn, _ = _seed_txn(repo)
    repo.set_employment_gross_entry(txn.id, 3100.0, source="manual")
    repo.delete_employment_gross_entries([txn.id])
    repo.commit()
    assert repo.get_employment_gross_entries([txn.id]) == {}


def test_deleting_transaction_removes_its_gross_entry(repo):
    txn, _ = _seed_txn(repo)
    repo.set_employment_gross_entry(txn.id, 3100.0, source="manual")
    repo.commit()
    repo.delete_raw_transactions([txn.id])
    repo.commit()
    assert repo.get_employment_gross_entries([txn.id]) == {}


def test_undo_import_removes_gross_entries(repo):
    txn, log = _seed_txn(repo)
    repo.set_employment_gross_entry(txn.id, 3100.0, source="manual")
    repo.commit()
    repo.undo_import(log.id)
    repo.commit()
    assert repo.get_employment_gross_entries([txn.id]) == {}
