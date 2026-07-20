"""Tests for the legacy .xls statement importer."""

from datetime import date, datetime

import pytest

xlwt = pytest.importorskip("xlwt")

from mtd_classifier.importers.xls_importer import XlsImporter
from mtd_classifier.importers.registry import detect_importer


def _write_xls(path, rows):
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Sheet1")
    date_style = xlwt.XFStyle()
    date_style.num_format_str = "DD/MM/YYYY"
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            if isinstance(value, (date, datetime)):
                ws.write(r, c, value, date_style)
            elif value is not None:
                ws.write(r, c, value)
    wb.save(str(path))
    return path


@pytest.fixture
def simple_xls(tmp_path):
    return _write_xls(tmp_path / "statement.xls", [
        ["Date", "Description", "Amount", "Balance"],
        [datetime(2026, 4, 15), "ACME CORP SALARY", 2500.00, 5000.00],
        [datetime(2026, 4, 16), "TESCO STORES", -45.67, 4954.33],
    ])


@pytest.fixture
def money_in_out_xls(tmp_path):
    return _write_xls(tmp_path / "inout.xls", [
        ["My Bank plc"],
        [],
        ["Date", "Details", "Money Out", "Money In", "Balance"],
        [datetime(2026, 4, 20), "PLUMBER J SMITH", 180.00, None, 4774.33],
        [datetime(2026, 4, 30), "RENT RECEIVED", None, 950.00, 5724.33],
    ])


def test_can_handle_and_registry(simple_xls):
    importer = detect_importer(simple_xls)
    assert isinstance(importer, XlsImporter)
    assert importer.generic is True


def test_parse_simple_layout(simple_xls):
    txns = list(XlsImporter().parse(simple_xls))
    assert len(txns) == 2
    assert txns[0].date == date(2026, 4, 15)
    assert txns[0].description == "ACME CORP SALARY"
    assert txns[0].amount == 2500.00
    assert txns[0].balance == 5000.00
    assert txns[1].amount == -45.67


def test_parse_money_in_out_layout(money_in_out_xls):
    txns = list(XlsImporter().parse(money_in_out_xls))
    assert len(txns) == 2
    assert txns[0].amount == -180.00
    assert txns[1].amount == 950.00


def test_rejects_spreadsheet_without_statement_headers(tmp_path):
    path = _write_xls(tmp_path / "notes.xls", [
        ["Shopping list"],
        ["Eggs", 12],
        ["Milk", 2],
    ])
    assert not XlsImporter().can_handle(path)
    assert detect_importer(path) is None


def test_xlsx_and_xls_produce_identical_results(tmp_path):
    """Both engines share parsing logic, so the same data should match."""
    import openpyxl

    rows = [
        ["Date", "Description", "Amount", "Balance"],
        [datetime(2026, 4, 15), "ACME CORP SALARY", 2500.00, 5000.00],
        [datetime(2026, 4, 16), "TESCO STORES", -45.67, 4954.33],
    ]
    xls_path = _write_xls(tmp_path / "same.xls", rows)

    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    xlsx_path = tmp_path / "same.xlsx"
    wb.save(xlsx_path)

    from mtd_classifier.importers.xlsx_importer import XlsxImporter

    xls_txns = list(XlsImporter().parse(xls_path))
    xlsx_txns = list(XlsxImporter().parse(xlsx_path))
    assert len(xls_txns) == len(xlsx_txns) == 2
    for a, b in zip(xls_txns, xlsx_txns):
        assert (a.date, a.description, a.amount, a.balance) == (
            b.date, b.description, b.amount, b.balance,
        )
