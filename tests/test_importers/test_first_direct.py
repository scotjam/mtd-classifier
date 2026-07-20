"""Tests for First Direct CSV importer."""

from datetime import date
from pathlib import Path

import pytest

from mtd_classifier.importers.first_direct_csv import FirstDirectCSVImporter

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def importer():
    return FirstDirectCSVImporter()


def test_can_handle_valid_file(importer):
    assert importer.can_handle(FIXTURES / "first_direct_sample.csv") is True


def test_can_handle_wrong_extension(importer, tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("Date,Description,Amount,Balance\n")
    assert importer.can_handle(f) is False


def test_can_handle_wrong_format(importer, tmp_path):
    f = tmp_path / "test.csv"
    f.write_text("Name;Value;Other\nfoo;1;2\n")
    assert importer.can_handle(f) is False


def test_parse_transactions(importer):
    txns = list(importer.parse(FIXTURES / "first_direct_sample.csv"))
    assert len(txns) == 10

    # First transaction
    assert txns[0].date == date(2026, 4, 15)
    assert txns[0].description == "ACME CORP SALARY"
    assert txns[0].amount == 2500.00
    assert txns[0].balance == 5000.00

    # Negative amount
    assert txns[1].description == "TESCO STORES"
    assert txns[1].amount == -45.67
    assert txns[1].balance == 4954.33


def test_parse_all_dates_correct(importer):
    txns = list(importer.parse(FIXTURES / "first_direct_sample.csv"))
    # All should be April/May 2026
    for txn in txns:
        assert txn.date.year == 2026
        assert txn.date.month in (4, 5)


def test_bank_name(importer):
    assert importer.bank_name == "firstdirect"
    assert importer.format_name == "csv"
