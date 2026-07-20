"""Tests for Santander importers (TXT and midata CSV)."""

from datetime import date
from pathlib import Path

import pytest

from mtd_classifier.importers.santander_csv import (
    SantanderTXTImporter,
    SantanderMidataImporter,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


class TestSantanderTXT:
    @pytest.fixture
    def importer(self):
        return SantanderTXTImporter()

    def test_can_handle_valid_file(self, importer):
        assert importer.can_handle(FIXTURES / "santander_sample.txt") is True

    def test_can_handle_wrong_extension(self, importer, tmp_path):
        f = tmp_path / "test.csv"
        f.write_text("Date,Description,Amount\n")
        assert importer.can_handle(f) is False

    def test_parse_transactions(self, importer):
        txns = list(importer.parse(FIXTURES / "santander_sample.txt"))
        assert len(txns) == 5

        # First transaction
        assert txns[0].date == date(2026, 4, 15)
        assert txns[0].description == "ACME CORP SALARY"
        assert txns[0].amount == 2500.00
        assert txns[0].balance == 5000.00

        # Negative amount
        assert txns[1].description == "TESCO STORES"
        assert txns[1].amount == -45.67

    def test_bank_name(self, importer):
        assert importer.bank_name == "santander"
        assert importer.format_name == "txt"

    def test_handles_non_breaking_spaces(self, importer, tmp_path):
        """Santander TXT files use non-breaking spaces (\xa0)."""
        content = (
            "Date:\xa015/04/2026\n"
            "Description:\xa0TEST\xa0MERCHANT\n"
            "Amount:\xa0-10.00\xa0GBP\n"
            "Balance:\xa0100.00\xa0GBP\n"
        )
        f = tmp_path / "test.txt"
        f.write_bytes(content.encode("latin-1"))
        txns = list(importer.parse(f))
        assert len(txns) == 1
        assert txns[0].description == "TEST MERCHANT"
        assert txns[0].amount == -10.00


class TestSantanderMidata:
    @pytest.fixture
    def importer(self):
        return SantanderMidataImporter()

    def test_can_handle_valid_file(self, importer):
        assert importer.can_handle(FIXTURES / "santander_midata.csv") is True

    def test_can_handle_comma_csv(self, importer, tmp_path):
        """Should not match regular comma-separated CSV."""
        f = tmp_path / "test.csv"
        f.write_text("Date,Description,Amount\n01/01/2026,TEST,10.00\n")
        assert importer.can_handle(f) is False

    def test_parse_transactions(self, importer):
        txns = list(importer.parse(FIXTURES / "santander_midata.csv"))
        assert len(txns) == 4

        assert txns[0].date == date(2026, 4, 15)
        assert txns[0].description == "ACME CORP SALARY"
        assert txns[0].amount == 2500.00

        assert txns[2].description == "MORTGAGE PAYMENT"
        assert txns[2].amount == -850.00
