"""Tests for the generic XLSX statement importer."""

from datetime import date, datetime

import openpyxl
import pytest

from mtd_classifier.importers.xlsx_importer import XlsxImporter
from mtd_classifier.importers.registry import detect_importer


def _write_xlsx(path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    wb.save(path)
    return path


@pytest.fixture
def simple_xlsx(tmp_path):
    return _write_xlsx(tmp_path / "statement.xlsx", [
        ["Date", "Description", "Amount", "Balance"],
        [datetime(2026, 4, 15), "ACME CORP SALARY", 2500.00, 5000.00],
        ["16/04/2026", "TESCO STORES", -45.67, 4954.33],
        [None, None, None, None],                      # blank row
        [None, "Total", 2454.33, None],                # footer, no date
    ])


@pytest.fixture
def money_in_out_xlsx(tmp_path):
    return _write_xlsx(tmp_path / "inout.xlsx", [
        ["My Bank plc"],                                # junk title rows
        ["Statement for account 12345678"],
        [],
        ["Date", "Details", "Money Out", "Money In", "Balance"],
        [datetime(2026, 4, 20), "PLUMBER J SMITH", 180.00, None, 4774.33],
        [datetime(2026, 4, 30), "RENT RECEIVED", None, 950.00, 5724.33],
    ])


def test_can_handle_and_registry(simple_xlsx):
    importer = detect_importer(simple_xlsx)
    assert isinstance(importer, XlsxImporter)
    assert importer.generic is True


def test_parse_simple_layout(simple_xlsx):
    txns = list(XlsxImporter().parse(simple_xlsx))
    assert len(txns) == 2  # blank and footer rows skipped
    assert txns[0].date == date(2026, 4, 15)
    assert txns[0].description == "ACME CORP SALARY"
    assert txns[0].amount == 2500.00
    assert txns[0].balance == 5000.00
    # string dates parse too
    assert txns[1].date == date(2026, 4, 16)
    assert txns[1].amount == -45.67


def test_parse_money_in_out_layout(money_in_out_xlsx):
    txns = list(XlsxImporter().parse(money_in_out_xlsx))
    assert len(txns) == 2
    assert txns[0].amount == -180.00   # Money Out becomes negative
    assert txns[1].amount == 950.00    # Money In stays positive


def test_rejects_spreadsheet_without_statement_headers(tmp_path):
    path = _write_xlsx(tmp_path / "notes.xlsx", [
        ["Shopping list"],
        ["Eggs", 12],
        ["Milk", 2],
    ])
    assert not XlsxImporter().can_handle(path)
    assert detect_importer(path) is None


def test_unsigned_amounts_inferred_from_balance(tmp_path):
    """The generic spreadsheet importer applies the same balance-movement
    sign inference as the column-mapping fallback: an export printing all
    amounts unsigned comes out correctly signed."""
    import openpyxl
    from mtd_classifier.importers.xlsx_importer import XlsxImporter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Date", "Description", "Amount", "Balance"])
    ws.append(["01/04/2026", "PHONE BILL", 13.98, 986.02])
    ws.append(["02/04/2026", "SALARY", 500.00, 1486.02])
    ws.append(["03/04/2026", "GROCERIES", 45.50, 1440.52])
    path = tmp_path / "unsigned.xlsx"
    wb.save(path)

    txns = list(XlsxImporter().parse(path))
    assert len(txns) == 3
    assert txns[1].amount == 500.00 and not txns[1].ambiguous_sign
    assert txns[2].amount == -45.50 and not txns[2].ambiguous_sign
    # First row: unprovable, and proven rows are mixed-direction -> flagged
    assert txns[0].ambiguous_sign


def test_money_out_in_pair_not_touched_by_inference(tmp_path):
    """A Money Out / Money In pair already carries its sign - inference
    must not run (a coincidental balance mismatch must not flag rows)."""
    import openpyxl
    from mtd_classifier.importers.xlsx_importer import XlsxImporter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Date", "Description", "Money Out", "Money In", "Balance"])
    ws.append(["01/04/2026", "RENT", 800.00, None, 200.00])
    ws.append(["02/04/2026", "SALARY", None, 2000.00, 2200.00])
    path = tmp_path / "pair.xlsx"
    wb.save(path)

    txns = list(XlsxImporter().parse(path))
    assert [t.amount for t in txns] == [-800.00, 2000.00]
    assert not any(t.ambiguous_sign for t in txns)
