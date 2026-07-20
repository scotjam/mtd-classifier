"""Tests for cross-format (PDF vs CSV/XLS) verification before HMRC output."""

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mtd_classifier.models.base import Base
from mtd_classifier.storage.repository import Repository
from mtd_classifier.services.import_service import import_statement
from mtd_classifier.services.verification import (
    verification_report,
    verify_cross_format,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def repo():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield Repository(session)
    session.close()


def _seed(repo, bank, fmt, families, amounts, account="Acc", day=1,
          period=(date(2026, 4, 1), date(2026, 4, 30))):
    log = repo.create_import_log(
        filename=f"{fmt}_{bank}_{day}.x", file_hash=f"{bank}{fmt}{day}",
        bank=bank, fmt=fmt, record_count=len(amounts),
        period_start=period[0], period_end=period[1], account=account,
    )
    for i, amount in enumerate(amounts):
        repo.create_raw_transaction(
            import_log_id=log.id, bank=bank, txn_date=date(2026, 4, day + i),
            description=f"TXN {day}-{i}", amount=amount,
            dedup_hash=f"h{bank}{fmt}{day}{i}", account=account,
            source_formats=families,
        )
    repo.commit()


def test_both_sources_matching_totals_ok(repo):
    _seed(repo, "pdf", "pdf", "pdf,tabular", [-10.0, -20.0])
    results = verify_cross_format(repo, date(2026, 4, 1), date(2026, 4, 30))
    assert len(results) == 1
    assert results[0].ok
    assert verification_report(results) == ""


def test_missing_pdf_source_flagged(repo):
    _seed(repo, "firstdirect", "csv", "tabular", [-10.0])
    results = verify_cross_format(repo, date(2026, 4, 1), date(2026, 4, 30))
    assert not results[0].ok
    assert "no PDF statements" in results[0].problem_summary()
    assert "no PDF statement backs this" in verification_report(results)


def test_missing_tabular_source_flagged(repo):
    _seed(repo, "pdf", "pdf", "pdf", [-10.0])
    results = verify_cross_format(repo, date(2026, 4, 1), date(2026, 4, 30))
    assert not results[0].ok
    assert "no CSV/XLS transaction lists" in results[0].problem_summary()


def test_total_mismatch_lists_unmatched_transactions(repo):
    _seed(repo, "pdf", "pdf", "pdf,tabular", [-10.0], day=1)
    _seed(repo, "pdf", "pdf", "pdf", [-99.0], day=5)  # only in the PDF
    results = verify_cross_format(repo, date(2026, 4, 1), date(2026, 4, 30))
    r = results[0]
    assert not r.ok
    assert r.pdf_total == -109.0
    assert r.tabular_total == -10.0
    assert len(r.missing_from_tabular) == 1
    assert r.missing_from_tabular[0].amount == -99.0
    assert "difference" in r.problem_summary()


def test_accounts_verified_independently(repo):
    _seed(repo, "pdf", "pdf", "pdf,tabular", [-10.0], account="A", day=1)
    _seed(repo, "firstdirect", "csv", "tabular", [-20.0], account="B", day=5)
    results = verify_cross_format(repo, date(2026, 4, 1), date(2026, 4, 30))
    assert len(results) == 2
    by_account = {r.account: r for r in results}
    assert by_account["A"].ok
    assert not by_account["B"].ok


def test_transactions_outside_period_ignored(repo):
    _seed(repo, "firstdirect", "csv", "tabular", [-10.0], day=1)
    results = verify_cross_format(repo, date(2026, 5, 1), date(2026, 5, 31))
    assert results == []


def test_import_records_source_family_on_duplicate(repo, tmp_path):
    """A CSV re-confirming rows first imported from a PDF must stamp them
    'tabular' - this is what makes the cross-check pass without storing
    duplicates."""
    log = repo.create_import_log(
        filename="stmt.pdf", file_hash="hpdf", bank="pdf", fmt="pdf", record_count=1,
        period_start=date(2026, 4, 1), period_end=date(2026, 4, 30), account="Acc",
    )
    repo.create_raw_transaction(
        import_log_id=log.id, bank="pdf", txn_date=date(2026, 4, 17),
        description="DD EE LIMITED", amount=-13.98, dedup_hash="seedpdf",
        account="Acc", source_formats="pdf",
    )
    repo.commit()

    f = tmp_path / "list.csv"
    f.write_text("Date,Description,Amount,Balance\n17/04/2026,EE LIMITED,-13.98,100.00\n")
    outcome = import_statement(repo, f, account="Acc")
    repo.commit()

    assert outcome.duplicate_count == 1
    txn = repo.get_raw_transaction_by_hash("seedpdf")
    assert txn.has_source_family("pdf")
    assert txn.has_source_family("tabular")
    results = verify_cross_format(repo, date(2026, 4, 1), date(2026, 4, 30))
    assert results[0].ok


def test_day_skip_records_source_family(repo, tmp_path):
    """Day-level reconciliation skips (existing covers the incoming day)
    must still stamp the incoming file's family on the matched rows."""
    log = repo.create_import_log(
        filename="stmt.pdf", file_hash="hpdf", bank="pdf", fmt="pdf", record_count=2,
        period_start=date(2026, 4, 1), period_end=date(2026, 4, 30), account="Acc",
    )
    for i, (desc, amt) in enumerate([("DD TOWERGATE", -146.07), ("SO WINTERINGHAM", -1.80)]):
        repo.create_raw_transaction(
            import_log_id=log.id, bank="pdf", txn_date=date(2026, 4, 1),
            description=desc, amount=amt, dedup_hash=f"seedday{i}",
            account="Acc", source_formats="pdf",
        )
    repo.commit()

    # Same two amounts, but descriptions differ enough that per-transaction
    # hashing won't match - the day-level tie check handles it.
    f = tmp_path / "list.csv"
    f.write_text(
        "Date,Description,Amount,Balance\n"
        "01/04/2026,TOWERGATE INSURANCE PREMIUM,-146.07,2969.63\n"
        "01/04/2026,C WINTERINGHAM STANDING ORDER,-1.80,2967.83\n"
    )
    outcome = import_statement(repo, f, account="Acc")
    repo.commit()

    assert outcome.new_count == 0
    for i in range(2):
        txn = repo.get_raw_transaction_by_hash(f"seedday{i}")
        assert txn.has_source_family("tabular"), f"row {i} missing tabular stamp"
    results = verify_cross_format(repo, date(2026, 4, 1), date(2026, 4, 30))
    assert results[0].ok


# --- balance-chain completeness proof ---

def _seed_chain(repo, entries, account="Acc", bank="firstdirect", start_id_hint=""):
    """entries: list of (day, amount, balance) in file order."""
    log = repo.create_import_log(
        filename=f"chain{start_id_hint}.csv", file_hash=f"chain{start_id_hint}",
        bank=bank, fmt="csv", record_count=len(entries),
    )
    for i, (day, amount, balance) in enumerate(entries):
        repo.create_raw_transaction(
            import_log_id=log.id, bank=bank, txn_date=date(2026, 4, day),
            description=f"TXN D{day} #{i}", amount=amount, balance=balance,
            dedup_hash=f"ch{start_id_hint}{i}", account=account,
            source_formats="tabular",
        )
    repo.commit()


def test_unbroken_chain_with_margin_passes_without_pdf(repo):
    # Period 10-20 April; chain runs 8th-22nd with every delta reconciling
    _seed_chain(repo, [
        (8, -10.00, 990.00),
        (12, -20.00, 970.00),
        (15, 100.00, 1070.00),
        (22, -5.00, 1065.00),
    ])
    results = verify_cross_format(repo, date(2026, 4, 10), date(2026, 4, 20))
    assert len(results) == 1
    r = results[0]
    assert r.chain_ok
    assert r.ok  # no PDF source at all, chain alone is proof
    assert verification_report(results) == ""


def test_chain_without_margin_fails_with_guidance(repo):
    # All transactions inside the period - no anchor either side
    _seed_chain(repo, [
        (12, -20.00, 970.00),
        (15, 100.00, 1070.00),
    ])
    results = verify_cross_format(repo, date(2026, 4, 10), date(2026, 4, 20))
    r = results[0]
    assert not r.chain_ok
    assert "day either side" in r.chain_problem
    assert not r.ok


def test_broken_chain_fails(repo):
    # The 15th's balance implies a missing transaction between 12th and 15th
    _seed_chain(repo, [
        (8, -10.00, 990.00),
        (12, -20.00, 970.00),
        (15, 100.00, 1020.00),  # 970 + 100 = 1070, not 1020: gap of -50
        (22, -5.00, 1015.00),
    ])
    results = verify_cross_format(repo, date(2026, 4, 10), date(2026, 4, 20))
    r = results[0]
    assert not r.chain_ok
    assert "reconcile" in r.chain_problem
    assert not r.ok


def test_newest_first_insertion_order_chains(repo):
    # Rows inserted newest-first (ids increase as dates decrease), as a
    # newest-first CSV import would - same-day pair included
    _seed_chain(repo, [
        (22, -5.00, 1065.00),
        (15, 100.00, 1070.00),
        (15, -30.00, 970.00),
        (12, -20.00, 1000.00),
        (8, -10.00, 1020.00),
    ])
    results = verify_cross_format(repo, date(2026, 4, 10), date(2026, 4, 20))
    assert results[0].chain_ok


def test_missing_balance_in_window_fails(repo):
    _seed_chain(repo, [
        (8, -10.00, 990.00),
        (15, 100.00, None),
        (22, -5.00, 1085.00),
    ])
    results = verify_cross_format(repo, date(2026, 4, 10), date(2026, 4, 20))
    r = results[0]
    assert not r.chain_ok
    assert "no recorded balance" in r.chain_problem


def test_cross_format_agreement_still_passes_without_chain(repo):
    # No margin transactions (chain can't anchor) but PDF+tabular agree
    _seed(repo, "pdf", "pdf", "pdf,tabular", [-10.0, -20.0])
    results = verify_cross_format(repo, date(2026, 4, 1), date(2026, 4, 30))
    r = results[0]
    assert not r.chain_ok
    assert r.cross_format_ok
    assert r.ok


# --- statement-coverage check ---

def test_coverage_gap_fails_even_when_sources_agree(repo):
    """Both sources can agree on incomplete data (each simply stops
    mid-period) - the statements' own dates must span the export period."""
    _seed(repo, "pdf", "pdf", "pdf,tabular", [-10.0],
          period=(date(2026, 4, 1), date(2026, 4, 20)))
    results = verify_cross_format(repo, date(2026, 4, 1), date(2026, 4, 30))
    r = results[0]
    assert r.cross_format_ok  # totals agree...
    assert not r.coverage_ok  # ...but statements stop on the 20th
    assert not r.ok
    assert "no statement covers 2026-04-21 to 2026-04-30" in r.coverage_problem


def test_coverage_from_multiple_files_merges(repo):
    _seed(repo, "pdf", "pdf", "pdf,tabular", [-10.0], day=1,
          period=(date(2026, 4, 1), date(2026, 4, 15)))
    _seed(repo, "pdf", "pdf", "pdf,tabular", [-20.0], day=20,
          period=(date(2026, 4, 16), date(2026, 4, 30)))
    results = verify_cross_format(repo, date(2026, 4, 1), date(2026, 4, 30))
    assert results[0].coverage_ok
    assert results[0].ok


def test_coverage_gap_between_files_reported(repo):
    _seed(repo, "pdf", "pdf", "pdf,tabular", [-10.0], day=1,
          period=(date(2026, 4, 1), date(2026, 4, 10)))
    _seed(repo, "pdf", "pdf", "pdf,tabular", [-20.0], day=20,
          period=(date(2026, 4, 20), date(2026, 4, 30)))
    results = verify_cross_format(repo, date(2026, 4, 1), date(2026, 4, 30))
    r = results[0]
    assert not r.coverage_ok
    assert "2026-04-11 to 2026-04-19" in r.coverage_problem


def test_coverage_requirement_capped_at_today(repo):
    """Mid-quarter cumulative exports are legitimate: coverage is only
    required up to today, never into the future."""
    from datetime import date as real_date, timedelta
    today = real_date.today()
    _seed(repo, "pdf", "pdf", "pdf,tabular", [-10.0],
          period=(today - timedelta(days=30), today))
    # export period ends far in the future
    start = today - timedelta(days=30)
    end = today + timedelta(days=60)
    from mtd_classifier.services.verification import check_statement_coverage
    logs = repo.get_import_logs_by_account(bank=None, account="Acc")
    ok, problem = check_statement_coverage(logs, start, end)
    assert ok, problem


def test_chain_proof_does_not_require_coverage(repo):
    """A verified balance chain is complete proof by itself - even if the
    import logs carry no statement periods (e.g. pre-migration data)."""
    log = repo.create_import_log(
        filename="chain.csv", file_hash="chaincov", bank="firstdirect", fmt="csv",
        record_count=3,  # no period columns set
    )
    for i, (day, amount, balance) in enumerate(
        [(8, -10.00, 990.00), (15, -20.00, 970.00), (22, -5.00, 965.00)]
    ):
        repo.create_raw_transaction(
            import_log_id=log.id, bank="firstdirect", txn_date=date(2026, 4, day),
            description=f"T{i}", amount=amount, balance=balance,
            dedup_hash=f"chcov{i}", account="Acc", source_formats="tabular",
        )
    repo.commit()
    results = verify_cross_format(repo, date(2026, 4, 10), date(2026, 4, 20))
    r = results[0]
    assert r.chain_ok
    assert not r.coverage_ok  # logs have no periods recorded
    assert r.ok  # chain wins regardless
