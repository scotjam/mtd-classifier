"""Tests for OFX/QFX file importer."""

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from mtd_classifier.importers.ofx_importer import OFXImporter

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def importer():
    return OFXImporter()


def test_can_handle_ofx_extension(importer):
    assert importer.can_handle(FIXTURES / "sample.ofx") is True


def test_can_handle_qfx_extension(importer, tmp_path):
    f = tmp_path / "statement.qfx"
    f.write_text("OFXHEADER:100\nDATA:OFXSGML\n")
    assert importer.can_handle(f) is True


def test_can_handle_wrong_extension(importer, tmp_path):
    f = tmp_path / "statement.csv"
    f.write_text("OFXHEADER:100\nDATA:OFXSGML\n")
    assert importer.can_handle(f) is False


def test_can_handle_wrong_header(importer, tmp_path):
    f = tmp_path / "statement.ofx"
    f.write_text("Date,Description,Amount\n1,2,3\n")
    assert importer.can_handle(f) is False


def test_can_handle_nonexistent_file(importer):
    assert importer.can_handle(Path("/nonexistent/file.ofx")) is False


def test_parse_transactions(importer):
    txns = list(importer.parse(FIXTURES / "sample.ofx"))
    assert len(txns) == 3

    # TXN001: name only
    assert txns[0].description == "ACME CORP SALARY"
    # TXN002: memo only
    assert txns[1].description == "TESCO STORES WEEKLY SHOP"
    # TXN003: name + different memo
    assert txns[2].description == "LETTING AGENTS LTD - MONTHLY MANAGEMENT FEE"


def test_parse_amounts_and_dates(importer):
    txns = list(importer.parse(FIXTURES / "sample.ofx"))

    assert txns[0].amount == 2500.00
    assert txns[1].amount == -45.67
    assert txns[2].amount == -150.00

    assert txns[0].date == date(2026, 4, 15)
    assert txns[1].date == date(2026, 4, 16)
    assert txns[2].date == date(2026, 4, 17)

    # Verify types
    assert isinstance(txns[0].amount, float)
    assert isinstance(txns[0].date, date)


def test_bank_name_and_format(importer):
    assert importer.bank_name == "unknown"
    assert importer.format_name == "ofx"

    custom = OFXImporter(bank_name="hsbc")
    assert custom.bank_name == "hsbc"


def test_parse_missing_ofxtools(importer):
    with patch.dict("sys.modules", {"ofxtools": None, "ofxtools.Parser": None}):
        with pytest.raises(ImportError, match="pip install ofxtools"):
            list(importer.parse(FIXTURES / "sample.ofx"))
