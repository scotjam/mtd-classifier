"""Tests for the generic CSV importer (Lloyds/Halifax/Monzo/Nationwide...).

These banks' CSV exports have recognisable headers, so they import with no
user interaction - the column-mapping fallback stays for anything odder.
"""

from datetime import date

from mtd_classifier.importers.csv_generic import GenericCsvImporter
from mtd_classifier.importers.registry import detect_importer
from mtd_classifier.services.import_service import parse_statement

# Lloyds Banking Group format (Lloyds and Halifax both use it)
LLOYDS_CSV = (
    "Transaction Date,Transaction Type,Sort Code,Account Number,"
    "Transaction Description,Debit Amount,Credit Amount,Balance\n"
    "01/10/2023,DEB,'11-22-33,12345678,TFL TRAVEL CH,4.80,,995.20\n"
    "02/10/2023,FPI,'11-22-33,12345678,ACME LTD SALARY,,1500.00,2495.20\n"
    "03/10/2023,DD,'11-22-33,12345678,BRITISH GAS,85.50,,2409.70\n"
)

MONZO_CSV = (
    "Transaction ID,Date,Time,Type,Name,Category,Amount,Currency,"
    "Notes and #tags,Description,Money Out,Money In\n"
    "tx_001,01/07/2023,09:15:00,Card payment,Pret,eating_out,-4.50,GBP,,"
    "PRET A MANGER LONDON,-4.50,\n"
    "tx_002,02/07/2023,12:00:00,Faster payment,Bloggs Ltd,income,2000.00,GBP,,"
    "BLOGGS LTD INVOICE 7,,2000.00\n"
)

NATIONWIDE_CSV = (
    '"Account Name:","Flexaccount ****2345"\n'
    '"Account Balance:","£175.50"\n'
    "\n"
    '"Date","Transaction type","Description","Paid out","Paid in","Balance"\n'
    '"29-Dec-14","Transfer to","J SMITH SAVINGS","£1.23","","£201.27"\n'
    '"05-Jan-15","Bank credit","ACME LTD SALARY","","£1,500.00","£1,675.50"\n'
)


def test_lloyds_csv_auto_detected(tmp_path):
    f = tmp_path / "lloyds.csv"
    f.write_text(LLOYDS_CSV, encoding="utf-8")
    importer = detect_importer(f)
    assert isinstance(importer, GenericCsvImporter)
    txns = list(importer.parse(f))
    assert len(txns) == 3
    assert txns[0].date == date(2023, 10, 1)
    assert txns[0].amount == -4.80  # debit column
    assert "TFL TRAVEL" in txns[0].description
    assert txns[1].amount == 1500.00  # credit column
    assert txns[2].balance == 2409.70


def test_halifax_same_format_as_lloyds(tmp_path):
    # Halifax is Lloyds Banking Group - identical CSV layout
    f = tmp_path / "halifax.csv"
    f.write_text(LLOYDS_CSV.replace("ACME LTD", "HALIFAX EMPLOYER"), encoding="utf-8")
    importer, txns = parse_statement(f)
    assert importer.format_name == "csv"
    assert len(txns) == 3
    assert txns[1].amount == 1500.00


def test_monzo_csv_uses_signed_amount(tmp_path):
    f = tmp_path / "monzo.csv"
    f.write_text(MONZO_CSV, encoding="utf-8")
    importer = detect_importer(f)
    assert isinstance(importer, GenericCsvImporter)
    txns = list(importer.parse(f))
    assert len(txns) == 2
    assert txns[0].amount == -4.50
    assert txns[1].amount == 2000.00
    assert "BLOGGS LTD" in txns[1].description


def test_nationwide_csv_auto_detected_with_joined_description(tmp_path):
    f = tmp_path / "nationwide.csv"
    f.write_text(NATIONWIDE_CSV, encoding="utf-8")
    importer = detect_importer(f)
    assert isinstance(importer, GenericCsvImporter)
    txns = list(importer.parse(f))
    assert len(txns) == 2
    # Both description-like columns combine
    assert txns[0].description == "Transfer to J SMITH SAVINGS"
    assert txns[0].amount == -1.23
    assert txns[1].amount == 1500.00


def test_first_direct_csv_not_shadowed(tmp_path):
    from mtd_classifier.importers.first_direct_csv import FirstDirectCSVImporter

    f = tmp_path / "fd.csv"
    f.write_text(
        "Date,Description,Amount,Balance\n15/04/2026,ACME,-10.00,990.00\n"
    )
    assert isinstance(detect_importer(f), FirstDirectCSVImporter)


def test_unrecognisable_csv_still_falls_through(tmp_path):
    # Starling-style headers have no description-like column - the
    # column-mapping fallback handles those, not the generic importer
    f = tmp_path / "starling.csv"
    f.write_text(
        "Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)\n"
        "01/04/2025,Insurer,DD123,DIRECT DEBIT,-15.99,4438.09\n"
    )
    assert detect_importer(f) is None
