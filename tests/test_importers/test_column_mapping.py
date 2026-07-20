"""Tests for the user-guided column-mapping fallback.

Sample data reconstructs the real export layouts of banks with no
dedicated importer: Nationwide (metadata rows, DD-Mon-YY dates, GBP
symbols, separate Paid out/Paid in columns), Co-op Business (single signed
amount, description spread over several reference columns) and Metro Bank
(signed Money In/Out column, DD-Mon-YY dates).
"""

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mtd_classifier.importers import column_mapping as cm
from mtd_classifier.importers.column_mapping import (
    ColumnMapping,
    find_saved_mapping,
    guess_header_signature,
    parse_with_mapping,
    read_tabular_rows,
    save_mapping,
)
from mtd_classifier.models.base import Base
from mtd_classifier.services.import_service import (
    UnknownFormatError,
    import_statement,
    parse_statement,
)
from mtd_classifier.storage.repository import Repository


@pytest.fixture(autouse=True)
def isolated_mappings(tmp_path, monkeypatch):
    """Point the saved-mappings store at a per-test file."""
    monkeypatch.setattr(cm, "MAPPINGS_PATH", tmp_path / "column_mappings.json")


@pytest.fixture
def repo():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield Repository(session)
    session.close()


NATIONWIDE_CSV = (
    '"Account Name:","Flexaccount ****2345"\n'
    '"Account Balance:","£175.50"\n'
    '"Available Balance:","£175.50"\n'
    "\n"
    '"Date","Transaction type","Description","Paid out","Paid in","Balance"\n'
    '"29-Dec-14","Transfer to","J SMITH SAVINGS","£1.23","","£201.27"\n'
    '"02-Jan-15","Direct Debit","BRITISH GAS","£25.77","","£175.50"\n'
    '"05-Jan-15","Bank credit","ACME LTD SALARY","","£1,500.00","£1,675.50"\n'
)

NATIONWIDE_MAPPING = ColumnMapping(
    date_col=0, description_cols=[1, 2], debit_col=3, credit_col=4,
    balance_col=5, name="Nationwide",
)

COOP_CSV = (
    "Transaction date,Account Number,Bank reference,Type of payment,Customer reference,Amount (GBP),Additional Info\n"
    "18/11/2023,12345678,REF001,Faster Payment,INVOICE 42,-120.50,ACME SUPPLIES\n"
    "20/11/2023,12345678,REF002,BACS Credit,CLIENT PAYMENT,850.00,BLOGGS LTD\n"
)

COOP_MAPPING = ColumnMapping(
    date_col=0, description_cols=[3, 4, 6], amount_col=5, name="Co-op",
)

METRO_CSV = (
    "Booking Date,Value Date,Transaction Type,Transaction Reference,Payer/Payee Name,Payment Reference,Money In/Out,Balance\n"
    "16-Apr-15,16-Apr-15,Account to Account Transfer,FT151068NJG2,Fred Bloggs,Test Payment 1,-0.20,5.00\n"
    "17-Apr-15,17-Apr-15,Inward Payment,FT151069ABC1,Jane Doe,Invoice 7,250.00,255.00\n"
)

METRO_MAPPING = ColumnMapping(
    date_col=0, description_cols=[2, 4, 5], amount_col=6, balance_col=7,
    name="Metro",
)


def test_nationwide_layout(tmp_path):
    f = tmp_path / "nationwide.csv"
    f.write_text(NATIONWIDE_CSV, encoding="utf-8")
    txns = list(parse_with_mapping(read_tabular_rows(f), NATIONWIDE_MAPPING))
    assert len(txns) == 3
    assert txns[0].date == date(2014, 12, 29)
    assert txns[0].description == "Transfer to J SMITH SAVINGS"
    assert txns[0].amount == -1.23  # Paid out column -> money out
    assert txns[0].balance == 201.27
    assert txns[2].amount == 1500.00  # Paid in, thousands separator stripped
    assert txns[2].date == date(2015, 1, 5)


def test_nationwide_cp1252_pound_sign(tmp_path):
    f = tmp_path / "nationwide_1252.csv"
    f.write_bytes(NATIONWIDE_CSV.encode("cp1252"))  # £ as 0xA3, not UTF-8
    txns = list(parse_with_mapping(read_tabular_rows(f), NATIONWIDE_MAPPING))
    assert [t.amount for t in txns] == [-1.23, -25.77, 1500.00]


def test_coop_layout(tmp_path):
    f = tmp_path / "coop.csv"
    f.write_text(COOP_CSV, encoding="utf-8")
    txns = list(parse_with_mapping(read_tabular_rows(f), COOP_MAPPING))
    assert len(txns) == 2
    assert txns[0].date == date(2023, 11, 18)
    assert txns[0].amount == -120.50  # single signed amount column
    assert txns[0].description == "Faster Payment INVOICE 42 ACME SUPPLIES"
    assert txns[1].amount == 850.00


def test_metro_layout(tmp_path):
    f = tmp_path / "metro.csv"
    f.write_text(METRO_CSV, encoding="utf-8")
    txns = list(parse_with_mapping(read_tabular_rows(f), METRO_MAPPING))
    assert len(txns) == 2
    assert txns[0].date == date(2015, 4, 16)
    assert txns[0].amount == -0.20
    assert txns[0].description == "Account to Account Transfer Fred Bloggs Test Payment 1"
    assert txns[1].balance == 255.00


def test_signature_guessed_from_row_above_first_data_row(tmp_path):
    f = tmp_path / "nationwide.csv"
    f.write_text(NATIONWIDE_CSV, encoding="utf-8")
    rows = read_tabular_rows(f)
    sig = guess_header_signature(rows, NATIONWIDE_MAPPING)
    assert sig == "date|transaction type|description|paid out|paid in|balance"


def test_saved_mapping_reused_without_asking(tmp_path):
    f = tmp_path / "nationwide.csv"
    f.write_text(NATIONWIDE_CSV, encoding="utf-8")
    rows = read_tabular_rows(f)
    mapping = ColumnMapping(
        **{**NATIONWIDE_MAPPING.__dict__,
           "header_signature": guess_header_signature(rows, NATIONWIDE_MAPPING)},
    )
    save_mapping(mapping)

    # A later file with the same layout but different data
    f2 = tmp_path / "nationwide_feb.csv"
    f2.write_text(NATIONWIDE_CSV.replace("29-Dec-14", "28-Feb-15"), encoding="utf-8")
    found = find_saved_mapping(read_tabular_rows(f2))
    assert found is not None
    assert found.name == "Nationwide"


def test_parse_statement_uses_provider_and_saves(tmp_path):
    f = tmp_path / "coop.csv"
    f.write_text(COOP_CSV, encoding="utf-8")
    calls = []

    def provider(file, rows):
        calls.append(file.name)
        mapping = ColumnMapping(**COOP_MAPPING.__dict__)
        mapping.header_signature = guess_header_signature(rows, mapping)
        return mapping

    importer, txns = parse_statement(f, column_mapping_provider=provider)
    assert calls == ["coop.csv"]
    assert importer.bank_name == "custom"
    assert importer.format_name == "csv"
    assert importer.generic is True
    assert len(txns) == 2

    # Same layout again: saved mapping applies, provider not asked
    f2 = tmp_path / "coop2.csv"
    f2.write_text(COOP_CSV.replace("18/11/2023", "19/12/2023"), encoding="utf-8")
    importer2, txns2 = parse_statement(f2, column_mapping_provider=provider)
    assert calls == ["coop.csv"]  # not called again
    assert len(txns2) == 2


def test_provider_declining_raises_unknown_format(tmp_path):
    f = tmp_path / "mystery.csv"
    f.write_text(COOP_CSV, encoding="utf-8")
    with pytest.raises(UnknownFormatError):
        parse_statement(f, column_mapping_provider=lambda file, rows: None)


def test_no_provider_still_raises_unknown_format(tmp_path):
    f = tmp_path / "mystery.csv"
    f.write_text(COOP_CSV, encoding="utf-8")
    with pytest.raises(UnknownFormatError):
        parse_statement(f)


def test_import_statement_via_mapping_records_tabular_family(repo, tmp_path):
    # Starling-style headers: no description-like column, so the generic
    # CSV importer can't claim it and the mapping fallback is exercised
    f = tmp_path / "starling.csv"
    f.write_text(
        "Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)\n"
        "01/04/2025,Insurer,DD123,DIRECT DEBIT,-15.99,4438.09\n"
        "22/04/2025,Tenant,RENT,FASTER PAYMENT,638.00,5076.09\n",
        encoding="utf-8",
    )
    mapping_spec = ColumnMapping(
        date_col=0, description_cols=[1, 2, 3], amount_col=4, balance_col=5,
    )

    def provider(file, rows):
        mapping = ColumnMapping(**mapping_spec.__dict__)
        mapping.header_signature = guess_header_signature(rows, mapping)
        return mapping

    outcome = import_statement(
        repo, f, account="Starling Business", column_mapping_provider=provider,
    )
    repo.commit()
    assert outcome.new_count == 2
    assert outcome.bank == "custom"
    txns = repo.get_transactions_for_account(bank=None, account="Starling Business")
    assert all(t.source_formats == "tabular" for t in txns)


def test_xlsx_files_work_through_mapping(tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    # A layout the generic spreadsheet importer can't detect: no
    # recognisable header keywords at all.
    ws.append(["When", "Who", "How much", "Left"])
    ws.append(["16/04/2026", "ACME LTD", -45.67, 954.33])
    ws.append(["17/04/2026", "BLOGGS & CO", 100.00, 1054.33])
    f = tmp_path / "odd_layout.xlsx"
    wb.save(f)

    mapping = ColumnMapping(date_col=0, description_cols=[1], amount_col=2, balance_col=3)
    txns = list(parse_with_mapping(read_tabular_rows(f), mapping))
    assert len(txns) == 2
    assert txns[0].date == date(2026, 4, 16)
    assert txns[0].amount == -45.67
    assert txns[1].description == "BLOGGS & CO"


def test_stale_mapping_that_parses_nothing_is_ignored(tmp_path):
    # Saved mapping whose signature matches but whose columns no longer fit
    f = tmp_path / "coop.csv"
    f.write_text(COOP_CSV, encoding="utf-8")
    rows = read_tabular_rows(f)
    stale = ColumnMapping(
        date_col=5,  # points at the amount column - dates never parse
        description_cols=[3], amount_col=0,
        header_signature=cm.row_signature(rows[0]),
    )
    save_mapping(stale)
    assert find_saved_mapping(rows) is None


# --- credit/debit inference from the running balance ---

UNSIGNED_CSV = (
    "Date,Description,Amount,Balance\n"
    "01/04/2026,OPENING PURCHASE,25.00,975.00\n"
    "02/04/2026,SALARY RECEIVED,500.00,1475.00\n"
    "03/04/2026,GROCERIES,45.50,1429.50\n"
)
UNSIGNED_MAPPING = ColumnMapping(
    date_col=0, description_cols=[1], amount_col=2, balance_col=3,
)


def test_unsigned_amounts_signed_from_balance_movement(tmp_path):
    f = tmp_path / "unsigned.csv"
    f.write_text(UNSIGNED_CSV, encoding="utf-8")
    txns = list(parse_with_mapping(read_tabular_rows(f), UNSIGNED_MAPPING))
    assert len(txns) == 3
    # Rows 2 and 3 proven by balance movement
    assert txns[1].amount == 500.00 and not txns[1].ambiguous_sign
    assert txns[2].amount == -45.50 and not txns[2].ambiguous_sign
    # Row 1 has no predecessor, and the proven rows contradict each other
    # (unsigned amounts proved to be both a credit and a debit) - no
    # convention to apply, so it is flagged for the user
    assert txns[0].amount == 25.00
    assert txns[0].ambiguous_sign


def test_consistent_convention_applied_to_unprovable_row(tmp_path):
    """Every proven row is a debit printed unsigned, so the file's
    convention (positive = debit) is applied to the unprovable first row
    instead of flagging it."""
    f = tmp_path / "all_debits.csv"
    f.write_text(
        "Date,Description,Amount,Balance\n"
        "01/04/2026,PHONE BILL,13.98,986.02\n"
        "02/04/2026,DONATION,56.00,930.02\n"
        "03/04/2026,GROCERIES,45.50,884.52\n",
        encoding="utf-8",
    )
    txns = list(parse_with_mapping(read_tabular_rows(f), UNSIGNED_MAPPING))
    assert [t.amount for t in txns] == [-13.98, -56.00, -45.50]
    assert not any(t.ambiguous_sign for t in txns)


def test_inverted_sign_convention_applied(tmp_path):
    """A file printing debits positive and credits negative: the proven
    rows establish the inversion, which is then applied to the unprovable
    first row."""
    f = tmp_path / "inverted.csv"
    f.write_text(
        "Date,Description,Amount,Balance\n"
        "01/04/2026,FIRST DEBIT,50.00,950.00\n"        # unprovable (no predecessor)
        "02/04/2026,SECOND DEBIT,30.00,920.00\n"        # proven: -30
        "03/04/2026,REFUND IN,-100.00,1020.00\n"        # proven: +100
        "04/04/2026,THIRD DEBIT,20.00,1000.00\n",       # proven: -20
        encoding="utf-8",
    )
    txns = list(parse_with_mapping(read_tabular_rows(f), UNSIGNED_MAPPING))
    by_desc = {t.description: t for t in txns}
    assert by_desc["SECOND DEBIT"].amount == -30.00
    assert by_desc["REFUND IN"].amount == 100.00
    # Convention learned: printed-positive means debit
    assert by_desc["FIRST DEBIT"].amount == -50.00
    assert not by_desc["FIRST DEBIT"].ambiguous_sign


def test_newest_first_file_order_detected(tmp_path):
    # Same data exported newest-first (how First Direct's CSV comes out)
    lines = UNSIGNED_CSV.splitlines()
    f = tmp_path / "newest_first.csv"
    f.write_text("\n".join([lines[0]] + lines[1:][::-1]) + "\n", encoding="utf-8")
    txns = list(parse_with_mapping(read_tabular_rows(f), UNSIGNED_MAPPING))
    by_desc = {t.description: t for t in txns}
    assert by_desc["SALARY RECEIVED"].amount == 500.00
    assert not by_desc["SALARY RECEIVED"].ambiguous_sign
    assert by_desc["GROCERIES"].amount == -45.50
    # OPENING PURCHASE is chronologically first - still unprovable
    assert by_desc["OPENING PURCHASE"].ambiguous_sign


def test_wrong_printed_sign_corrected_by_balance(tmp_path):
    # A file that prints a debit as positive despite having signed credits
    f = tmp_path / "wrong_sign.csv"
    f.write_text(
        "Date,Description,Amount,Balance\n"
        "01/04/2026,START,-10.00,990.00\n"
        "02/04/2026,SNEAKY DEBIT,30.00,960.00\n",  # balance fell: really -30
        encoding="utf-8",
    )
    txns = list(parse_with_mapping(read_tabular_rows(f), UNSIGNED_MAPPING))
    assert txns[1].amount == -30.00
    assert not txns[1].ambiguous_sign


def test_signed_file_without_balance_untouched(tmp_path):
    f = tmp_path / "coop.csv"
    f.write_text(COOP_CSV, encoding="utf-8")
    txns = list(parse_with_mapping(read_tabular_rows(f), COOP_MAPPING))
    assert [t.amount for t in txns] == [-120.50, 850.00]
    assert not any(t.ambiguous_sign for t in txns)


def test_unreconcilable_balances_leave_amounts_as_printed(tmp_path):
    # Balance column mapped but the figures don't relate to the amounts
    # (e.g. user mapped the wrong column) - don't invent signs
    f = tmp_path / "odd.csv"
    f.write_text(
        "Date,Description,Amount,Balance\n"
        "01/04/2026,A,10.00,7.77\n"
        "02/04/2026,B,20.00,9.99\n",
        encoding="utf-8",
    )
    txns = list(parse_with_mapping(read_tabular_rows(f), UNSIGNED_MAPPING))
    assert [t.amount for t in txns] == [10.00, 20.00]
    assert not any(t.ambiguous_sign for t in txns)
