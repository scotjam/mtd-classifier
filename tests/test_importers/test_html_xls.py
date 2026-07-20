"""Tests for the HTML-table-disguised-as-.xls statement format.

Several banks (Santander among them) export "Excel" statements that are
actually an HTML table saved with a .xls extension - Excel opens these
happily, but they are not real workbooks, so xlrd rejects them. The fixture
here reproduces the exact tag structure of a real export (verified against
a live Santander download) with anonymised transaction data.
"""

from datetime import date
from pathlib import Path

from mtd_classifier.importers.registry import detect_importer
from mtd_classifier.importers.xls_importer import XlsImporter
from mtd_classifier.importers import _html_table

FIXTURE = Path(__file__).parent.parent / "fixtures" / "santander_html_xls.xls"


def test_looks_like_html_detects_content_not_extension():
    assert _html_table.looks_like_html(FIXTURE) is True


def test_can_handle_and_registry():
    importer = detect_importer(FIXTURE)
    assert isinstance(importer, XlsImporter)
    assert importer.generic is True


def test_parses_three_transactions_with_correct_signs_and_balances():
    txns = list(XlsImporter().parse(FIXTURE))
    assert len(txns) == 3

    debit = txns[0]
    assert debit.date == date(2026, 6, 19)
    assert debit.amount == -123.45  # Money Out -> negative
    assert debit.balance == 3116.55
    assert "DIRECT DEBIT" in debit.description

    credit = txns[1]
    assert credit.date == date(2026, 6, 18)
    assert credit.amount == 1250.00  # Money In -> positive
    assert credit.balance == 3240.00
    assert "EXAMPLE LETTING AGENT LTD" in credit.description

    small_debit = txns[2]
    assert small_debit.date == date(2026, 6, 17)
    assert small_debit.amount == -10.00
    assert small_debit.balance == 1990.00


def test_raw_data_preserved_for_review():
    txns = list(XlsImporter().parse(FIXTURE))
    assert all(t.raw_data for t in txns)


def test_binary_xls_still_works_alongside_html_detection(tmp_path):
    """The HTML branch must not accidentally catch real binary workbooks."""
    xlwt = __import__("pytest").importorskip("xlwt")
    from datetime import datetime

    wb = xlwt.Workbook()
    ws = wb.add_sheet("Sheet1")
    date_style = xlwt.XFStyle()
    date_style.num_format_str = "DD/MM/YYYY"
    for r, row in enumerate([
        ["Date", "Description", "Amount", "Balance"],
        [datetime(2026, 4, 15), "REAL BINARY ROW", 100.00, 200.00],
    ]):
        for c, v in enumerate(row):
            if isinstance(v, datetime):
                ws.write(r, c, v, date_style)
            else:
                ws.write(r, c, v)
    path = tmp_path / "binary.xls"
    wb.save(str(path))

    assert _html_table.looks_like_html(path) is False
    txns = list(XlsImporter().parse(path))
    assert len(txns) == 1
    assert txns[0].description == "REAL BINARY ROW"
