"""Tests for quarterly summary aggregation."""

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mtd_classifier.models.base import Base
from mtd_classifier.models.transaction import RawTransaction, CategorisedTransaction
from mtd_classifier.models.import_log import ImportLog
from mtd_classifier.models.category import TransactionCategory
from mtd_classifier.export.mtd_summary import summarise_quarter, QuarterlySummary
from mtd_classifier.periods import TaxQuarter


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def sample_data(db_session):
    """Create sample transactions and categorisations."""
    log = ImportLog(
        filename="test.csv", file_hash="h1", bank="firstdirect", format="csv",
        record_count=5, new_record_count=5,
    )
    db_session.add(log)
    db_session.flush()

    txns = []
    cats = []

    test_data = [
        (date(2026, 4, 15), "SALARY", 2500.00, "self_employed_income", "manual"),
        (date(2026, 4, 16), "TESCO", -45.67, "personal", "pattern"),
        (date(2026, 4, 17), "MORTGAGE", -850.00, "mortgage_interest", "pattern"),
        (date(2026, 4, 22), "LETTING AGENT", -150.00, "letting_agent_fees", "pattern"),
        (date(2026, 4, 30), "RENT RECEIVED", 950.00, "rental_income", "exact"),
    ]

    for i, (d, desc, amount, cat, conf) in enumerate(test_data):
        raw = RawTransaction(
            import_log_id=log.id,
            bank="firstdirect",
            date=d,
            description=desc,
            amount=amount,
            dedup_hash=f"summary_hash_{i}",
        )
        db_session.add(raw)
        db_session.flush()

        cat_txn = CategorisedTransaction(
            raw_transaction_id=raw.id,
            category=cat,
            confidence=conf,
        )
        db_session.add(cat_txn)
        db_session.flush()

        txns.append(raw)
        cats.append(cat_txn)

    return list(zip(txns, cats))


def test_summarise_quarter_basic(sample_data):
    tq = TaxQuarter(tax_year=2026, quarter=1)
    summary = summarise_quarter(tq, sample_data, total_in_period=5)

    assert summary.total_transactions == 5
    assert summary.categorised_count == 5
    assert summary.uncategorised_count == 0


def test_summarise_quarter_income(sample_data):
    tq = TaxQuarter(tax_year=2026, quarter=1)
    summary = summarise_quarter(tq, sample_data, total_in_period=5)

    # Self-employed income: 2500.00
    se_income = summary.category_totals["self_employed_income"]
    assert se_income.total_income == 2500.00
    assert se_income.transaction_count == 1

    # Rental income: 950.00
    rental = summary.category_totals["rental_income"]
    assert rental.total_income == 950.00


def test_summarise_quarter_expenses(sample_data):
    tq = TaxQuarter(tax_year=2026, quarter=1)
    summary = summarise_quarter(tq, sample_data, total_in_period=5)

    # Mortgage interest: -850.00
    mortgage = summary.category_totals["mortgage_interest"]
    assert mortgage.total_expense == -850.00

    # Letting agent fees: -150.00
    letting = summary.category_totals["letting_agent_fees"]
    assert letting.total_expense == -150.00


def test_summary_totals(sample_data):
    tq = TaxQuarter(tax_year=2026, quarter=1)
    summary = summarise_quarter(tq, sample_data, total_in_period=5)

    # Total income = 2500 + 950 = 3450
    assert summary.total_income == 3450.00

    # Total expenses = -150 (letting agent fees; personal excluded, and
    # mortgage interest is no longer a deductible expense since 2020-21 -
    # only a 20% tax reduction applies, see finance_costs/estimated relief)
    assert summary.total_expenses == -150.00

    # Net = 3450 - 150 = 3300
    assert summary.net_profit == 3300.00

    # Mortgage interest tracked separately, not folded into profit
    assert summary.finance_costs == 850.00
    assert summary.estimated_finance_cost_relief == round(0.20 * 850.00, 2)


def test_finance_cost_relief_capped_at_profit():
    """The 20% relief can't exceed profit, and can't exceed the finance cost itself."""
    def txn(desc, amount, category, i):
        raw = RawTransaction(
            import_log_id=1, bank="firstdirect", date=date(2026, 4, 20),
            description=desc, amount=amount, dedup_hash=f"relief_{i}",
        )
        cat = CategorisedTransaction(raw_transaction_id=i, category=category, confidence="manual")
        return raw, cat

    # Profit before finance costs is only 200, but finance costs are 850 -
    # relief must cap at 20% of the 200 profit, not 20% of the 850 cost
    txns = [
        txn("RENT", 200.00, "rental_income", 1),
        txn("MORTGAGE", -850.00, "mortgage_interest", 2),
    ]
    summary = summarise_quarter(TaxQuarter(2026, 1), txns, total_in_period=2)
    assert summary.net_profit == 200.00  # mortgage interest doesn't reduce this
    assert summary.finance_costs == 850.00
    assert summary.estimated_finance_cost_relief == round(0.20 * 200.00, 2)


def test_finance_cost_relief_zero_when_no_profit():
    def txn(desc, amount, category, i):
        raw = RawTransaction(
            import_log_id=1, bank="firstdirect", date=date(2026, 4, 20),
            description=desc, amount=amount, dedup_hash=f"relief_zero_{i}",
        )
        cat = CategorisedTransaction(raw_transaction_id=i, category=category, confidence="manual")
        return raw, cat

    txns = [
        txn("RENT", 100.00, "rental_income", 1),
        txn("REPAIRS", -400.00, "property_repairs", 2),  # profit is negative
        txn("MORTGAGE", -850.00, "mortgage_interest", 3),
    ]
    summary = summarise_quarter(TaxQuarter(2026, 1), txns, total_in_period=3)
    assert summary.net_profit == -300.00
    assert summary.estimated_finance_cost_relief == 0.0


def test_mortgage_interest_row_carries_excluded_note():
    def txn(desc, amount, category, i):
        raw = RawTransaction(
            import_log_id=1, bank="firstdirect", date=date(2026, 4, 20),
            description=desc, amount=amount, dedup_hash=f"excl_note_{i}",
        )
        cat = CategorisedTransaction(raw_transaction_id=i, category=category, confidence="manual")
        return raw, cat

    txns = [txn("MORTGAGE", -850.00, "mortgage_interest", 1)]
    summary = summarise_quarter(TaxQuarter(2026, 1), txns, total_in_period=1)
    ct = summary.category_totals["mortgage_interest"]
    assert ct.excluded_note is not None
    assert "20%" in ct.excluded_note


def test_reportable_totals_excludes_personal(sample_data):
    tq = TaxQuarter(tax_year=2026, quarter=1)
    summary = summarise_quarter(tq, sample_data, total_in_period=5)

    reportable = summary.reportable_totals()
    categories = [ct.category for ct in reportable]
    assert TransactionCategory.PERSONAL not in categories
    assert TransactionCategory.TRANSFER not in categories


def test_empty_quarter():
    tq = TaxQuarter(tax_year=2026, quarter=2)
    summary = summarise_quarter(tq, [], total_in_period=0)

    assert summary.total_transactions == 0
    assert summary.categorised_count == 0
    assert summary.total_income == 0.0
    assert summary.total_expenses == 0.0
    assert len(summary.reportable_totals()) == 0
