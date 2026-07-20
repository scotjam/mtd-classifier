"""Tests for import history listing and undo-import."""

from datetime import date
from pathlib import Path

import pytest

from mtd_classifier.services.import_service import import_statement
from mtd_classifier.services.attachments import attach_file
from mtd_classifier.storage.repository import ImportOutOfOrderError

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_get_import_logs_for_account_by_account_label(repo):
    log = repo.create_import_log(
        filename="a.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=1
    )
    repo.create_raw_transaction(
        import_log_id=log.id, bank="firstdirect", txn_date=date(2026, 4, 20),
        description="X", amount=10.0, dedup_hash="hist_1", account="My Account",
    )
    repo.commit()
    logs = repo.get_import_logs_for_account(bank=None, account="My Account")
    assert len(logs) == 1
    assert logs[0].id == log.id

    assert repo.get_import_logs_for_account(bank=None, account="Other Account") == []


def test_get_import_logs_for_account_by_bank_when_no_account_label(repo):
    log = repo.create_import_log(
        filename="a.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=1
    )
    repo.create_raw_transaction(
        import_log_id=log.id, bank="firstdirect", txn_date=date(2026, 4, 20),
        description="X", amount=10.0, dedup_hash="hist_2", account=None,
    )
    repo.commit()
    logs = repo.get_import_logs_for_account(bank="firstdirect", account=None)
    assert len(logs) == 1


def test_import_logs_newest_first(repo):
    log1 = repo.create_import_log(
        filename="a.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=1
    )
    repo.create_raw_transaction(
        import_log_id=log1.id, bank="firstdirect", txn_date=date(2026, 4, 20),
        description="X", amount=10.0, dedup_hash="hist_3", account="Acc",
    )
    log2 = repo.create_import_log(
        filename="b.csv", file_hash="h2", bank="firstdirect", fmt="csv", record_count=1
    )
    repo.create_raw_transaction(
        import_log_id=log2.id, bank="firstdirect", txn_date=date(2026, 4, 21),
        description="Y", amount=20.0, dedup_hash="hist_4", account="Acc",
    )
    repo.commit()
    logs = repo.get_import_logs_for_account(bank=None, account="Acc")
    assert [l.id for l in logs] == [log2.id, log1.id]


class TestUndoImport:
    def test_undo_removes_only_this_imports_transactions(self, repo):
        outcome = import_statement(
            repo, FIXTURES / "first_direct_sample.csv", account="Acc",
        )
        repo.commit()
        assert repo.count_current_transactions_for_import(outcome.import_log.id) == 10

        result = repo.undo_import(outcome.import_log.id)
        repo.commit()

        assert result["transactions_removed"] == 10
        assert repo.get_transactions_for_account(bank=None, account="Acc") == []
        assert repo.get_import_log_by_hash(outcome.import_log.file_hash) is None

    def test_undo_does_not_remove_duplicates_that_predate_this_import(self, repo, tmp_path):
        """A transaction skipped as a duplicate during import B belongs to
        whichever import (A) first added it - undoing B must leave it intact.

        Uses two DIFFERENT files with identical transaction content (so they
        get distinct file_hash values and distinct ImportLog rows) rather than
        re-importing the same file with force=True - that reuses the original
        ImportLog instead of creating a second one, which wouldn't exercise
        this scenario at all.
        """
        content = (FIXTURES / "first_direct_sample.csv").read_text()
        file_a = tmp_path / "copy_a.csv"
        file_a.write_text(content)
        # A trailing blank line changes the file hash without changing the
        # parsed transactions, giving file_b a distinct ImportLog.
        file_b = tmp_path / "copy_b.csv"
        file_b.write_text(content + "\n")

        outcome_a = import_statement(repo, file_a, account="Acc")
        repo.commit()
        outcome_b = import_statement(repo, file_b, account="Acc")
        repo.commit()
        assert outcome_a.import_log.id != outcome_b.import_log.id
        assert outcome_b.new_count == 0
        assert outcome_b.duplicate_count == 10

        before = len(repo.get_transactions_for_account(bank=None, account="Acc"))
        assert before == 10

        result = repo.undo_import(outcome_b.import_log.id)
        repo.commit()

        # Import B added nothing, so undoing it removes nothing
        assert result["transactions_removed"] == 0
        after = repo.get_transactions_for_account(bank=None, account="Acc")
        assert len(after) == 10  # import A's transactions all still present
        # Import A's log must still exist and still be re-import-blocking
        assert repo.get_import_log_by_hash(outcome_a.import_log.file_hash) is not None

    def test_undo_partial_overlap_keeps_the_original_transactions(self, repo, tmp_path):
        """Import A brings in rows 1-2; import B (a superset file) dedupes
        row 1-2 against A and adds only its new row 3. Undoing B must remove
        only row 3, leaving A's rows untouched."""
        file_a = tmp_path / "morning.csv"
        file_a.write_text(
            "Date,Description,Amount,Balance\n"
            "15/04/2026,ACME CORP SALARY,2500.00,5000.00\n"
        )
        file_b = tmp_path / "fullday.csv"
        file_b.write_text(
            "Date,Description,Amount,Balance\n"
            "15/04/2026,ACME CORP SALARY,2500.00,5000.00\n"
            "16/04/2026,TESCO STORES,-45.67,4954.33\n"
        )
        outcome_a = import_statement(repo, file_a, account="Acc")
        repo.commit()
        outcome_b = import_statement(repo, file_b, account="Acc")
        repo.commit()
        assert outcome_b.new_count == 1
        assert outcome_b.duplicate_count == 1

        result = repo.undo_import(outcome_b.import_log.id)
        repo.commit()

        assert result["transactions_removed"] == 1
        remaining = repo.get_transactions_for_account(bank=None, account="Acc")
        assert len(remaining) == 1
        assert remaining[0].description == "ACME CORP SALARY"

    def test_undo_removes_categorisation_and_audit_log(self, repo):
        outcome = import_statement(
            repo, FIXTURES / "first_direct_sample.csv", account="Acc",
        )
        repo.commit()
        txns = repo.get_transactions_for_account(bank=None, account="Acc")
        repo.set_categorisation(
            raw_transaction_id=txns[0].id, category="self_employed_income",
            confidence="manual", changed_by="test",
        )
        repo.commit()
        assert repo.get_categorisation(txns[0].id) is not None

        repo.undo_import(outcome.import_log.id)
        repo.commit()

        assert repo.get_categorisation(txns[0].id) is None
        # No orphaned audit rows referencing a deleted transaction
        from sqlalchemy import select
        from mtd_classifier.models.audit import AuditLog
        remaining_audit = repo.session.execute(select(AuditLog)).scalars().all()
        assert all(a.raw_transaction_id != txns[0].id for a in remaining_audit)

    def test_undo_removes_attachment_row_and_returns_file_path_for_cleanup(self, repo, tmp_path, monkeypatch):
        import mtd_classifier.services.attachments as attachments_module
        import mtd_classifier.config as config_module
        monkeypatch.setattr(config_module, "ATTACHMENTS_DIR", tmp_path / "attachments")

        outcome = import_statement(
            repo, FIXTURES / "first_direct_sample.csv", account="Acc",
        )
        repo.commit()
        txns = repo.get_transactions_for_account(bank=None, account="Acc")
        receipt = tmp_path / "receipt.pdf"
        receipt.write_bytes(b"%PDF-1.4 fake")
        attachment = attach_file(repo, txns[0].id, receipt)
        repo.commit()
        stored_path = attachments_module.attachment_path(attachment)
        assert stored_path.exists()

        result = repo.undo_import(outcome.import_log.id)
        repo.commit()

        assert len(result["attachment_paths"]) == 1
        assert result["attachment_paths"][0] == stored_path
        assert repo.get_attachments_for_transaction(txns[0].id) == []
        # Repository itself doesn't touch the filesystem - caller's job
        assert stored_path.exists()

    def test_undo_nonexistent_import_log_is_a_no_op(self, repo):
        result = repo.undo_import(999999)
        assert result["transactions_removed"] == 0
        assert result["attachment_paths"] == []

    def test_undo_refuses_older_import_while_newer_one_exists(self, repo, tmp_path):
        """Undoing import A while newer import B stands is unsafe: B may
        have skipped rows as duplicates of A's transactions, so deleting A's
        rows would remove data B's file still logically contains. Must undo
        B first, then A becomes undoable."""
        file_a = tmp_path / "morning.csv"
        file_a.write_text(
            "Date,Description,Amount,Balance\n"
            "15/04/2026,ACME CORP SALARY,2500.00,5000.00\n"
        )
        file_b = tmp_path / "fullday.csv"
        file_b.write_text(
            "Date,Description,Amount,Balance\n"
            "15/04/2026,ACME CORP SALARY,2500.00,5000.00\n"
            "16/04/2026,TESCO STORES,-45.67,4954.33\n"
        )
        outcome_a = import_statement(repo, file_a, account="Acc")
        repo.commit()
        outcome_b = import_statement(repo, file_b, account="Acc")
        repo.commit()

        with pytest.raises(ImportOutOfOrderError):
            repo.undo_import(outcome_a.import_log.id)

        # Nothing was actually removed by the refused attempt
        assert repo.count_current_transactions_for_import(outcome_a.import_log.id) == 1
        assert repo.count_current_transactions_for_import(outcome_b.import_log.id) == 1

        # Undo the newer one first, then the older one is fair game
        repo.undo_import(outcome_b.import_log.id)
        repo.commit()
        repo.undo_import(outcome_a.import_log.id)
        repo.commit()
        assert repo.get_transactions_for_account(bank=None, account="Acc") == []

    def test_undo_order_check_is_scoped_per_account(self, repo, tmp_path):
        """A newer import for a DIFFERENT account must not block undoing an
        older import for this one."""
        file_acc1 = tmp_path / "acc1.csv"
        file_acc1.write_text(
            "Date,Description,Amount,Balance\n"
            "15/04/2026,ACME CORP SALARY,2500.00,5000.00\n"
        )
        file_acc2 = tmp_path / "acc2.csv"
        file_acc2.write_text(
            "Date,Description,Amount,Balance\n"
            "16/04/2026,TESCO STORES,-45.67,4954.33\n"
        )
        outcome_1 = import_statement(repo, file_acc1, account="Account One")
        repo.commit()
        import_statement(repo, file_acc2, account="Account Two")
        repo.commit()

        # No ImportOutOfOrderError - the Account Two import is a different scope
        result = repo.undo_import(outcome_1.import_log.id)
        repo.commit()
        assert result["transactions_removed"] == 1


def test_hash_migration_lets_old_rows_dedup_new_imports(tmp_path):
    """Rows stored under the old dedup formula (bank tag, raw description)
    must be re-hashed on init_db so a re-export of the same transactions
    dedups instead of doubling up."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from mtd_classifier.storage.database import init_db
    from mtd_classifier.storage.repository import Repository
    from mtd_classifier.models.base import Base

    db = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    repo = Repository(session)
    log = repo.create_import_log(
        filename="old.xls", file_hash="hold", bank="spreadsheet", fmt="xls",
        record_count=1,
    )
    # Old-formula hash: sha256("spreadsheet|2026-04-07|direct debit payment|-5.50")
    repo.create_raw_transaction(
        import_log_id=log.id, bank="spreadsheet", txn_date=date(2026, 4, 7),
        description="DIRECT DEBIT PAYMENT", amount=-5.50, balance=100.0,
        dedup_hash="0" * 64, account="Santander current",
        source_formats="tabular",
    )
    session.commit()
    session.close()

    init_db(engine)  # runs the hash migration

    session = Session()
    repo = Repository(session)
    f = tmp_path / "new.csv"
    f.write_text(
        "Date,Description,Amount,Balance\n"
        "07/04/2026,DIRECT DEBIT PAYMENT,-5.50,100.00\n"
    )
    outcome = import_statement(repo, f, account="Santander current")
    session.commit()
    assert outcome.new_count == 0
    assert outcome.duplicate_count == 1
    # Migration is stamped - running init_db again must not re-migrate
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA user_version")).scalar() == 1
