"""Tests for importer auto-detection registry."""

from pathlib import Path

import pytest

from mtd_classifier.importers.registry import detect_importer
from mtd_classifier.importers.first_direct_csv import FirstDirectCSVImporter
from mtd_classifier.importers.santander_csv import (
    SantanderTXTImporter,
    SantanderMidataImporter,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_detect_first_direct():
    importer = detect_importer(FIXTURES / "first_direct_sample.csv")
    assert importer is not None
    assert isinstance(importer, FirstDirectCSVImporter)


def test_detect_santander_txt():
    importer = detect_importer(FIXTURES / "santander_sample.txt")
    assert importer is not None
    assert isinstance(importer, SantanderTXTImporter)


def test_detect_santander_midata():
    importer = detect_importer(FIXTURES / "santander_midata.csv")
    assert importer is not None
    assert isinstance(importer, SantanderMidataImporter)


def test_detect_with_bank_hint():
    importer = detect_importer(FIXTURES / "santander_sample.txt", bank_hint="santander")
    assert importer is not None
    assert isinstance(importer, SantanderTXTImporter)


def test_detect_unknown_file(tmp_path):
    f = tmp_path / "mystery.dat"
    f.write_text("random data")
    importer = detect_importer(f)
    assert importer is None
