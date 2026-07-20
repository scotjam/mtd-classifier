"""Tests for the shared import service (date-range filter, bank mismatch)."""

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mtd_classifier.models.base import Base
from mtd_classifier.storage.repository import Repository
from mtd_classifier.services.import_service import (
    BankMismatchError,
    DayConflictResolution,
    import_statement,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def repo():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield Repository(session)
    session.close()


def test_date_range_filters_transactions(repo):
    # Fixture spans 15/04/2026 - 01/05/2026; keep April only
    outcome = import_statement(
        repo, FIXTURES / "first_direct_sample.csv",
        date_range=(date(2026, 4, 6), date(2026, 4, 30)),
    )
    assert outcome.new_count == 9
    assert outcome.out_of_range_count == 1  # the 01/05 plumber line
    assert outcome.parsed_count == 10


def test_date_range_rescan_with_force_picks_up_remaining(repo):
    import_statement(
        repo, FIXTURES / "first_direct_sample.csv",
        date_range=(date(2026, 4, 6), date(2026, 4, 30)),
    )
    # Re-scan with a wider range (force bypasses the file-level dedup);
    # transaction dedup keeps the 9 April rows from importing again
    outcome = import_statement(
        repo, FIXTURES / "first_direct_sample.csv",
        force=True,
        date_range=(date(2026, 4, 6), date(2027, 4, 5)),
    )
    assert outcome.new_count == 1
    assert outcome.duplicate_count == 9


def test_bank_mismatch_raises_without_callback(repo):
    with pytest.raises(BankMismatchError) as exc:
        import_statement(
            repo, FIXTURES / "santander_sample.txt",
            expected_bank="firstdirect",
        )
    assert exc.value.detected_bank == "santander"
    assert exc.value.expected_bank == "firstdirect"


def test_bank_mismatch_callback_can_proceed(repo):
    outcome = import_statement(
        repo, FIXTURES / "santander_sample.txt",
        expected_bank="firstdirect",
        on_bank_mismatch=lambda detected, expected: True,
    )
    assert outcome.new_count > 0
    assert outcome.bank == "santander"  # stored truthfully as the detected bank


def test_bank_mismatch_callback_can_skip(repo):
    with pytest.raises(BankMismatchError):
        import_statement(
            repo, FIXTURES / "santander_sample.txt",
            expected_bank="firstdirect",
            on_bank_mismatch=lambda detected, expected: False,
        )
    assert repo.count_transactions() == 0


def test_generic_formats_skip_bank_mismatch_check(repo, tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Date", "Description", "Amount"])
    ws.append(["15/04/2026", "INVOICE", 100.00])
    path = tmp_path / "export.xlsx"
    wb.save(path)

    # A spreadsheet can't declare its bank, so expected_bank must not trip
    outcome = import_statement(repo, path, expected_bank="firstdirect")
    assert outcome.new_count == 1
    assert outcome.bank == "spreadsheet"


def test_matching_bank_no_callback_needed(repo):
    outcome = import_statement(
        repo, FIXTURES / "first_direct_sample.csv",
        expected_bank="firstdirect",
    )
    assert outcome.new_count == 10


# --- cross-format dedup and day-level reconciliation ---
# See EE LIMITED bug: a real-world transaction printed as "DD EE LIMITED" in
# a PDF statement and "EE LIMITED" in the same bank's CSV export imported
# twice because per-transaction hashing didn't recognise them as the same
# transaction.

def test_type_code_prefix_normalized_for_dedup(repo, tmp_path):
    f1 = tmp_path / "a.csv"
    f1.write_text(
        "Date,Description,Amount,Balance\n17/04/2026,DD EE LIMITED,-13.98,100.00\n"
    )
    f2 = tmp_path / "b.csv"
    f2.write_text(
        "Date,Description,Amount,Balance\n17/04/2026,EE LIMITED,-13.98,100.00\n"
    )
    import_statement(repo, f1, account="Acc")
    repo.commit()
    outcome = import_statement(repo, f2, account="Acc")
    repo.commit()
    assert outcome.new_count == 0
    assert outcome.duplicate_count == 1


def test_account_identity_dedups_across_bank_tags(repo):
    """Without an account, two importers tagged differently ("pdf" vs
    "firstdirect") can never dedup each other - with a shared account, they
    do."""
    log = repo.create_import_log(
        filename="statement.pdf", file_hash="h1", bank="pdf", fmt="pdf", record_count=1,
    )
    repo.create_raw_transaction(
        import_log_id=log.id, bank="pdf", txn_date=date(2026, 4, 17),
        description="DD EE LIMITED", amount=-13.98, dedup_hash="seed1",
        account="Acc", balance=100.00,
    )
    repo.commit()

    csv = "Date,Description,Amount,Balance\n17/04/2026,EE LIMITED,-13.98,100.00\n"
    path = FIXTURES / "_tmp_ee.csv"
    path.write_text(csv)
    try:
        outcome = import_statement(repo, path, account="Acc")
    finally:
        path.unlink()
    repo.commit()
    assert outcome.new_count == 0
    assert outcome.duplicate_count == 1


def test_day_reconciliation_replaces_existing_when_incoming_is_fuller(repo, tmp_path):
    """An earlier partial import (only caught one of the day's two
    transactions) is superseded once a fuller same-day file arrives -
    the earlier row's amount does appear, literally, among the fuller
    file's amounts, so this auto-resolves without asking."""
    log = repo.create_import_log(
        filename="old.csv", file_hash="h1", bank="oldformat", fmt="csv", record_count=1,
    )
    repo.create_raw_transaction(
        import_log_id=log.id, bank="oldformat", txn_date=date(2026, 4, 1),
        description="J SMITH RENT SHARE", amount=-1.80, dedup_hash="seed2",
        account="Acc", balance=None,
    )
    repo.commit()

    f = tmp_path / "fuller.csv"
    f.write_text(
        "Date,Description,Amount,Balance\n"
        "01/04/2026,DD HOMESAFE FIRST PAYMENT,-146.07,2969.63\n"
        "01/04/2026,SO J SMITH RENT SHARE,-1.80,2969.63\n"
    )
    outcome = import_statement(repo, f, account="Acc")
    repo.commit()

    assert outcome.new_count == 2
    assert outcome.day_replaced_count == 1
    assert repo.get_raw_transaction_by_hash("seed2") is None


def test_day_reconciliation_conflicts_when_combined_row_has_no_literal_match(repo, tmp_path):
    """A single combined row (e.g. one format sums same-day transactions
    together) doesn't literally appear among a split breakdown's amounts,
    so this can't be auto-resolved even though it's "the same day" -
    needs a human, per the no-guessing rule."""
    log = repo.create_import_log(
        filename="old.csv", file_hash="h1", bank="oldformat", fmt="csv", record_count=1,
    )
    repo.create_raw_transaction(
        import_log_id=log.id, bank="oldformat", txn_date=date(2026, 4, 1),
        description="COMBINED PAYMENT", amount=-147.87, dedup_hash="seed2b",
        account="Acc", balance=None,
    )
    repo.commit()

    f = tmp_path / "split.csv"
    f.write_text(
        "Date,Description,Amount,Balance\n"
        "01/04/2026,DD HOMESAFE FIRST PAYMENT,-146.07,2969.63\n"
        "01/04/2026,SO J SMITH RENT SHARE,-1.80,2969.63\n"
    )
    outcome = import_statement(repo, f, account="Acc")
    repo.commit()

    assert outcome.new_count == 0
    assert outcome.day_conflicts == 1
    assert repo.get_raw_transaction_by_hash("seed2b") is not None  # untouched


def test_day_conflict_without_callback_skips_and_reports(repo, tmp_path):
    """Amounts that can't be reconciled (neither a subset of the other) are
    left untouched and reported, not guessed at."""
    log = repo.create_import_log(
        filename="old.csv", file_hash="h1", bank="oldformat", fmt="csv", record_count=1,
    )
    repo.create_raw_transaction(
        import_log_id=log.id, bank="oldformat", txn_date=date(2026, 4, 1),
        description="SOMETHING", amount=-50.00, dedup_hash="seed3",
        account="Acc", balance=None,
    )
    repo.commit()

    f = tmp_path / "mismatch.csv"
    f.write_text("Date,Description,Amount,Balance\n01/04/2026,DIFFERENT THING,-75.00,1000.00\n")
    outcome = import_statement(repo, f, account="Acc")
    repo.commit()

    assert outcome.new_count == 0
    assert outcome.day_conflicts == 1
    assert repo.count_transactions() == 1  # existing untouched, incoming not added


def test_day_conflict_callback_resolves_with_explicit_choice(repo, tmp_path):
    log = repo.create_import_log(
        filename="old.csv", file_hash="h1", bank="oldformat", fmt="csv", record_count=1,
    )
    existing_txn = repo.create_raw_transaction(
        import_log_id=log.id, bank="oldformat", txn_date=date(2026, 4, 1),
        description="SOMETHING", amount=-50.00, dedup_hash="seed4",
        account="Acc", balance=None,
    )
    repo.commit()

    f = tmp_path / "mismatch.csv"
    f.write_text("Date,Description,Amount,Balance\n01/04/2026,DIFFERENT THING,-75.00,1000.00\n")

    def on_day_conflict(txn_date, identity, existing, incoming):
        # Keep both: the new one plus the old one
        return DayConflictResolution(keep_incoming=list(incoming), delete_existing_ids=[])

    outcome = import_statement(repo, f, account="Acc", on_day_conflict=on_day_conflict)
    repo.commit()

    assert outcome.new_count == 1
    assert outcome.day_conflicts == 0
    assert repo.count_transactions() == 2


def test_day_reconciliation_preserves_categorised_existing(repo, tmp_path):
    """A superseded-looking existing row is never auto-deleted if the user
    already categorised it - that would silently discard their work."""
    log = repo.create_import_log(
        filename="old.csv", file_hash="h1", bank="oldformat", fmt="csv", record_count=1,
    )
    existing_txn = repo.create_raw_transaction(
        import_log_id=log.id, bank="oldformat", txn_date=date(2026, 4, 1),
        description="COMBINED PAYMENT", amount=-147.87, dedup_hash="seed5",
        account="Acc", balance=None,
    )
    repo.set_categorisation(existing_txn.id, category="business_expense", confidence="high")
    repo.commit()

    f = tmp_path / "split.csv"
    f.write_text(
        "Date,Description,Amount,Balance\n"
        "01/04/2026,DD HOMESAFE FIRST PAYMENT,-146.07,2969.63\n"
        "01/04/2026,SO J SMITH RENT SHARE,-1.80,2969.63\n"
    )
    outcome = import_statement(repo, f, account="Acc")
    repo.commit()

    assert outcome.new_count == 0
    assert outcome.day_conflicts == 1
    assert repo.get_raw_transaction_by_hash("seed5") is not None  # not deleted


# --- statement period from filenames ---

def test_filename_date_range_widens_coverage(repo, tmp_path):
    """A file named with its export range covers quiet edge days that have
    no transactions - min/max transaction dates would understate it."""
    f = tmp_path / "transactions_20260401_20260630.csv"
    f.write_text(
        "Date,Description,Amount,Balance\n"
        "07/04/2026,FIRST TXN,-10.00,990.00\n"
        "20/06/2026,LAST TXN,-5.00,985.00\n"
    )
    outcome = import_statement(repo, f, account="Acc")
    assert outcome.period == (date(2026, 4, 1), date(2026, 6, 30))
    assert outcome.import_log.period_start == date(2026, 4, 1)
    assert outcome.import_log.period_end == date(2026, 6, 30)


def test_filename_range_not_containing_txns_is_ignored(repo, tmp_path):
    # Digits look like a range but a transaction falls outside it - the
    # "range" is spurious, fall back to transaction dates
    f = tmp_path / "ref_20260401_20260430.csv"
    f.write_text(
        "Date,Description,Amount,Balance\n"
        "07/04/2026,IN RANGE,-10.00,990.00\n"
        "20/06/2026,OUTSIDE,-5.00,985.00\n"
    )
    outcome = import_statement(repo, f, account="Acc")
    assert outcome.period == (date(2026, 4, 7), date(2026, 6, 20))


def test_single_filename_date_is_not_a_range(repo, tmp_path):
    # "10072026" is a download date, not a coverage range
    f = tmp_path / "10072026_1234.csv"
    f.write_text(
        "Date,Description,Amount,Balance\n"
        "07/04/2026,A,-10.00,990.00\n"
        "20/06/2026,B,-5.00,985.00\n"
    )
    outcome = import_statement(repo, f, account="Acc")
    assert outcome.period == (date(2026, 4, 7), date(2026, 6, 20))


def test_account_number_digits_not_mistaken_for_dates():
    from mtd_classifier.services.import_service import filename_date_range
    assert filename_date_range("Statements09012312345678 (5).xls") is None
    assert filename_date_range("transactions_20260401_20260630.csv") == (
        date(2026, 4, 1), date(2026, 6, 30))
    assert filename_date_range("01-04-2026 to 30-06-2026.csv") == (
        date(2026, 4, 1), date(2026, 6, 30))
    assert filename_date_range("export.csv") is None


def test_confirm_period_callback_fires_only_for_estimated_coverage(repo, tmp_path):
    """The callback runs when coverage was guessed from transaction dates,
    and its answer is what gets persisted; strong sources (filename range)
    don't ask."""
    calls = []

    def confirm(file, period, transactions):
        calls.append(file.name)
        return (date(2026, 4, 1), date(2026, 6, 30))

    weak = tmp_path / "export.csv"  # no dates in the name
    weak.write_text(
        "Date,Description,Amount,Balance\n"
        "07/04/2026,A,-10.00,990.00\n"
        "20/06/2026,B,-5.00,985.00\n"
    )
    outcome = import_statement(repo, weak, account="Acc", on_confirm_period=confirm)
    assert calls == ["export.csv"]
    assert outcome.period == (date(2026, 4, 1), date(2026, 6, 30))
    assert outcome.import_log.period_start == date(2026, 4, 1)

    strong = tmp_path / "export_20260701_20260710.csv"
    strong.write_text(
        "Date,Description,Amount,Balance\n"
        "05/07/2026,C,-10.00,975.00\n"
    )
    outcome2 = import_statement(repo, strong, account="Acc", on_confirm_period=confirm)
    assert calls == ["export.csv"]  # filename range - no confirmation needed
    assert outcome2.period == (date(2026, 7, 1), date(2026, 7, 10))


def test_confirm_period_declined_keeps_estimate(repo, tmp_path):
    f = tmp_path / "export.csv"
    f.write_text(
        "Date,Description,Amount,Balance\n"
        "07/04/2026,A,-10.00,990.00\n"
    )
    outcome = import_statement(
        repo, f, account="Acc", on_confirm_period=lambda *a: None,
    )
    assert outcome.period == (date(2026, 4, 7), date(2026, 4, 7))
