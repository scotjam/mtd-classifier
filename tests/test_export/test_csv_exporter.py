"""Tests for CSV export."""

import csv
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mtd_classifier.models.base import Base
from mtd_classifier.models.transaction import RawTransaction, CategorisedTransaction
from mtd_classifier.models.import_log import ImportLog
from mtd_classifier.export.csv_exporter import export_to_csv
from mtd_classifier.export.mtd_summary import summarise_quarter
from mtd_classifier.periods import TaxQuarter


@pytest.fixture
def sample_export_data():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    log = ImportLog(
        filename="test.csv", file_hash="exp1", bank="firstdirect", format="csv",
        record_count=2, new_record_count=2,
    )
    session.add(log)
    session.flush()

    raw1 = RawTransaction(
        import_log_id=log.id, bank="firstdirect", date=date(2026, 4, 15),
        description="SALARY", amount=2500.00, dedup_hash="exp_h1",
    )
    raw2 = RawTransaction(
        import_log_id=log.id, bank="firstdirect", date=date(2026, 4, 16),
        description="TESCO", amount=-45.67, dedup_hash="exp_h2",
    )
    session.add_all([raw1, raw2])
    session.flush()

    cat1 = CategorisedTransaction(
        raw_transaction_id=raw1.id, category="self_employed_income", confidence="manual",
    )
    cat2 = CategorisedTransaction(
        raw_transaction_id=raw2.id, category="personal", confidence="pattern",
    )
    session.add_all([cat1, cat2])
    session.flush()

    data = [(raw1, cat1), (raw2, cat2)]
    session.close()
    return data


def test_export_creates_file(sample_export_data, tmp_path):
    tq = TaxQuarter(tax_year=2026, quarter=1)
    summary = summarise_quarter(tq, sample_export_data, total_in_period=2)
    output = tmp_path / "test_export.csv"

    result = export_to_csv(summary, sample_export_data, output)
    assert result.exists()


def test_export_contains_summary(sample_export_data, tmp_path):
    tq = TaxQuarter(tax_year=2026, quarter=1)
    summary = summarise_quarter(tq, sample_export_data, total_in_period=2)
    output = tmp_path / "test_export.csv"

    export_to_csv(summary, sample_export_data, output)

    content = output.read_text()
    assert "MTD Quarterly Summary" in content
    assert "Q1-2026" in content


def test_export_contains_transactions(sample_export_data, tmp_path):
    tq = TaxQuarter(tax_year=2026, quarter=1)
    summary = summarise_quarter(tq, sample_export_data, total_in_period=2)
    output = tmp_path / "test_export.csv"

    export_to_csv(summary, sample_export_data, output)

    content = output.read_text()
    assert "SALARY" in content
    assert "TESCO" in content
    assert "Transaction Details" in content
