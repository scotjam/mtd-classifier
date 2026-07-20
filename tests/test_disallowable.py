"""Tests for the full SA103/SA105 box coverage, the disallowable-expense
flag, and tax-deducted-at-source (tax credit) handling."""

from datetime import date

import openpyxl
import pytest
from sqlalchemy import create_engine, text

from mtd_classifier.export.mtd_summary import summarise_quarter
from mtd_classifier.export.mytaxdigital import (
    CATEGORY_BOX_CELLS,
    aggregate_box_totals,
    export_bridging_templates,
)
from mtd_classifier.models.category import (
    ALWAYS_DISALLOWABLE_CATEGORIES,
    NON_REPORTABLE_CATEGORIES,
    TAX_CREDIT_CATEGORIES,
    TransactionCategory,
    categories_for_amount,
)
from mtd_classifier.models.transaction import RawTransaction, CategorisedTransaction
from mtd_classifier.periods import TaxQuarter


def _txn(desc, amount, category, i, disallowable=False):
    raw = RawTransaction(
        import_log_id=1, bank="firstdirect", date=date(2026, 4, 20),
        description=desc, amount=amount, dedup_hash=f"disallow_{i}",
    )
    raw.id = i
    cat = CategorisedTransaction(
        raw_transaction_id=i, category=category, confidence="manual",
        disallowable=disallowable,
    )
    return raw, cat


# --- box coverage -----------------------------------------------------------

def test_new_sa105_categories_map_to_verified_cells():
    expected = {
        TransactionCategory.PROPERTY_OTHER_INCOME: ("20b", "C11"),
        TransactionCategory.RENT_A_ROOM_INCOME: ("20c", "C15"),
        TransactionCategory.LEASE_PREMIUM_INCOME: ("22", "I11"),
        TransactionCategory.REVERSE_PREMIUM_INCOME: ("23", "I15"),
        TransactionCategory.PROPERTY_TAX_DEDUCTED: ("21", "I7"),
        TransactionCategory.PROPERTY_SERVICES: ("28", "I21"),
        TransactionCategory.PROPERTY_TRAVEL: ("29b", "I29"),
        TransactionCategory.NON_RES_FINANCE_COSTS: ("26", "C29"),
    }
    for cat, (box, cell) in expected.items():
        assert CATEGORY_BOX_CELLS[cat].box == box
        assert CATEGORY_BOX_CELLS[cat].cell == cell
        # SA105 template has no disallowable column
        assert CATEGORY_BOX_CELLS[cat].disallowable_cell is None


def test_new_sa103_categories_map_to_verified_cells():
    expected = {
        TransactionCategory.SE_OTHER_INCOME: ("16", "I7", None),
        TransactionCategory.SE_TAX_DEDUCTED: ("81", "C11", None),
        TransactionCategory.SE_GOODS: ("17", "C19", "I19"),
        TransactionCategory.SE_SUBCONTRACTORS: ("18", "C23", "I23"),
        TransactionCategory.SE_STAFF_COSTS: ("19", "C27", "I27"),
        TransactionCategory.SE_PREMISES: ("21", "C35", "I35"),
        TransactionCategory.SE_REPAIRS: ("22", "C39", "I39"),
        TransactionCategory.SE_ENTERTAINMENT: ("24.2", "C51", "I51"),
        TransactionCategory.SE_BANK_CHARGES: ("26", "C59", "I59"),
        TransactionCategory.SE_BAD_DEBTS: ("27", "C63", "I63"),
        TransactionCategory.SE_DEPRECIATION: ("29", "C71", "I71"),
    }
    for cat, (box, cell, discell) in expected.items():
        assert CATEGORY_BOX_CELLS[cat].box == box
        assert CATEGORY_BOX_CELLS[cat].cell == cell
        assert CATEGORY_BOX_CELLS[cat].disallowable_cell == discell


def test_every_sa103_expense_box_has_disallowable_cell():
    for cat, bc in CATEGORY_BOX_CELLS.items():
        if bc.form == "SA103" and not bc.is_income and cat not in TAX_CREDIT_CATEGORIES:
            assert bc.disallowable_cell is not None, f"{cat} missing disallowable cell"


def test_new_categories_have_direction():
    assert TransactionCategory.PROPERTY_OTHER_INCOME in categories_for_amount(100.0)
    assert TransactionCategory.SE_OTHER_INCOME in categories_for_amount(100.0)
    assert TransactionCategory.PROPERTY_SERVICES in categories_for_amount(-100.0)
    assert TransactionCategory.SE_GOODS in categories_for_amount(-100.0)
    assert TransactionCategory.PROPERTY_TAX_DEDUCTED in categories_for_amount(-100.0)
    assert TransactionCategory.SE_TAX_DEDUCTED in categories_for_amount(-100.0)


# --- tax credits (boxes 21 / 81) ---------------------------------------------

def test_tax_deducted_reported_but_never_reduces_profit():
    txns = [
        _txn("RENT", 1000.0, "rental_income", 1),
        _txn("NRL TAX WITHHELD", -200.0, "property_tax_deducted", 2),
    ]
    summary = summarise_quarter(TaxQuarter(2026, 1), txns, total_in_period=2)
    assert summary.total_expenses == 0.0
    assert summary.net_profit == 1000.0
    # ...but it still reaches its bridging box
    totals, skipped = aggregate_box_totals(txns)
    assert totals["SA105:I7"].amount == 200.0
    assert skipped == []


# --- disallowable expenses ----------------------------------------------------

def test_flagged_disallowable_goes_to_both_sa103_cells():
    txns = [
        _txn("INVOICE", 10000.0, "self_employed_income", 1),
        _txn("FUEL BUSINESS", -300.0, "se_travel", 2),
        _txn("FUEL PRIVATE USE", -100.0, "se_travel", 3, disallowable=True),
    ]
    totals, _ = aggregate_box_totals(txns)
    box20 = totals["SA103:C31"]
    # Expense box holds the TOTAL including the disallowable share...
    assert box20.amount == 400.0
    # ...and the disallowable column holds the share HMRC subtracts
    assert box20.disallowable == 100.0


def test_always_disallowable_categories_fill_disallowable_cell():
    txns = [
        _txn("CLIENT DINNER", -80.0, "se_entertainment", 1),
        _txn("DEPRECIATION CHARGE", -500.0, "se_depreciation", 2),
    ]
    totals, _ = aggregate_box_totals(txns)
    assert totals["SA103:C51"].amount == 80.0
    assert totals["SA103:C51"].disallowable == 80.0
    assert totals["SA103:C71"].amount == 500.0
    assert totals["SA103:C71"].disallowable == 500.0


def test_disallowable_property_expense_left_out_of_sa105_box():
    txns = [
        _txn("REPAIR BUSINESS", -300.0, "property_repairs", 1),
        _txn("REPAIR OWN FLAT SHARE", -120.0, "property_repairs", 2, disallowable=True),
    ]
    totals, _ = aggregate_box_totals(txns)
    # No disallowable column on SA105: the disallowed amount is not entered
    assert totals["SA105:C25"].amount == 300.0
    assert totals["SA105:C25"].disallowable == 0.0


def test_disallowable_written_into_template_file(bridging_templates, tmp_path):
    txns = [
        _txn("INVOICE", 10000.0, "self_employed_income", 1),
        _txn("FUEL", -300.0, "se_travel", 2),
        _txn("FUEL PRIVATE", -100.0, "se_travel", 3, disallowable=True),
    ]
    result = export_bridging_templates(txns, tmp_path, "Q1-2026", forms=("SA103",))
    ws = openpyxl.load_workbook(result.files_written[0]).worksheets[0]
    assert ws["C31"].value == pytest.approx(400.0)
    assert ws["I31"].value == pytest.approx(100.0)


def test_disallowable_share_added_back_to_profit_and_tax():
    from mtd_classifier.services.tax_computation import compute_tax_from_category_totals
    from mtd_classifier.tax_year_rates import get_rates

    txns = [
        _txn("INVOICE", 30000.0, "self_employed_income", 1),
        _txn("FUEL", -300.0, "se_travel", 2),
        _txn("FUEL PRIVATE", -100.0, "se_travel", 3, disallowable=True),
        _txn("CLIENT DINNER", -80.0, "se_entertainment", 4),
    ]
    summary = summarise_quarter(TaxQuarter(2024, 4), txns, total_in_period=4)
    # Profit deducts only the allowable 300 (not the 100 private share nor
    # the wholly-disallowable entertainment)
    assert summary.net_profit == pytest.approx(29700.0)
    result = compute_tax_from_category_totals(summary.category_totals, get_rates(2024))
    assert result.self_employment_profit == pytest.approx(29700.0)


def test_se_other_income_counts_toward_se_profit():
    from mtd_classifier.services.tax_computation import compute_tax_from_category_totals
    from mtd_classifier.tax_year_rates import get_rates

    txns = [
        _txn("INVOICE", 1000.0, "self_employed_income", 1),
        _txn("SCRAP SALE", 50.0, "se_other_income", 2),
        _txn("RENT", 900.0, "rental_income", 3),
        _txn("ROOM RENT", 400.0, "rent_a_room_income", 4),
    ]
    summary = summarise_quarter(TaxQuarter(2024, 4), txns, total_in_period=4)
    result = compute_tax_from_category_totals(summary.category_totals, get_rates(2024))
    assert result.self_employment_profit == pytest.approx(1050.0)
    # 900 rent + 400 rent-a-room, of which the 400 is fully exempt (box 37)
    assert result.property_profit == pytest.approx(900.0)


# --- repository flag + migration ---------------------------------------------

def test_set_disallowable_flag_roundtrip(repo):
    log = repo.create_import_log(
        filename="d.csv", file_hash="dis_h1", bank="firstdirect", fmt="csv",
        record_count=1,
    )
    txn = repo.create_raw_transaction(
        import_log_id=log.id, bank="firstdirect", txn_date=date(2026, 4, 20),
        description="FUEL", amount=-50.0, dedup_hash="dis_t1",
    )
    repo.set_categorisation(
        raw_transaction_id=txn.id, category="se_travel",
        confidence="manual", changed_by="test",
    )
    assert repo.get_categorisation(txn.id).disallowable is False
    assert repo.set_disallowable_flag([txn.id], True) == 1
    repo.commit()
    assert repo.get_categorisation(txn.id).disallowable is True
    # Re-categorising without the parameter keeps the flag
    repo.set_categorisation(
        raw_transaction_id=txn.id, category="se_premises",
        confidence="manual", changed_by="test",
    )
    assert repo.get_categorisation(txn.id).disallowable is True
    assert repo.set_disallowable_flag([txn.id], False) == 1
    assert repo.get_categorisation(txn.id).disallowable is False


def test_migration_adds_disallowable_column_to_legacy_db():
    from mtd_classifier.storage.database import _migrate_disallowable_flag

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE categorised_transactions ("
            "id INTEGER PRIMARY KEY, raw_transaction_id INTEGER, "
            "category VARCHAR(50), confidence VARCHAR(20), "
            "matched_rule_id INTEGER, categorised_at DATETIME)"
        ))
        conn.execute(text(
            "INSERT INTO categorised_transactions "
            "(raw_transaction_id, category, confidence) VALUES (1, 'se_travel', 'manual')"
        ))
    _migrate_disallowable_flag(engine)
    with engine.begin() as conn:
        columns = [
            row[1] for row in conn.execute(text("PRAGMA table_info(categorised_transactions)"))
        ]
        assert "disallowable" in columns
        value = conn.execute(text(
            "SELECT disallowable FROM categorised_transactions WHERE raw_transaction_id = 1"
        )).scalar_one()
        assert not value  # legacy rows default to allowable
    # idempotent
    _migrate_disallowable_flag(engine)


# --- Rent-a-Room exemption (box 37, derived from box 20c) --------------------

def test_rent_a_room_exemption_auto_filled_from_receipts(bridging_templates, tmp_path):
    txns = [_txn("LODGER RENT", 5000.0, "rent_a_room_income", 1)]
    result = export_bridging_templates(txns, tmp_path, "Q2-2026", tax_year=2026)
    box37 = result.box_totals["SA105:I33"]
    assert box37.amount == 5000.0  # receipts below the limit: fully exempt
    ws = openpyxl.load_workbook(result.files_written[0]).worksheets[0]
    assert ws["C15"].value == pytest.approx(5000.0)   # box 20c receipts
    assert ws["I33"].value == pytest.approx(5000.0)   # box 37 exemption


def test_rent_a_room_exemption_capped_at_annual_limit(bridging_templates, tmp_path):
    txns = [_txn("LODGER RENT", 9000.0, "rent_a_room_income", 1)]
    result = export_bridging_templates(txns, tmp_path, "Q4-2026", tax_year=2026)
    assert result.box_totals["SA105:I33"].amount == 7500.0


def test_no_rent_a_room_receipts_no_box_37(bridging_templates, tmp_path):
    txns = [_txn("RENT", 900.0, "rental_income", 1)]
    result = export_bridging_templates(txns, tmp_path, "Q1-2026", tax_year=2026)
    assert "SA105:I33" not in result.box_totals


def test_rent_a_room_exemption_reduces_profit_and_tax():
    from mtd_classifier.services.tax_computation import compute_tax_from_category_totals
    from mtd_classifier.tax_year_rates import get_rates

    txns = [
        _txn("RENT", 1000.0, "rental_income", 1),
        _txn("LODGER RENT", 9000.0, "rent_a_room_income", 2),
    ]
    summary = summarise_quarter(TaxQuarter(2024, 4), txns, total_in_period=2)
    # Income stays gross; profit exempts min(9000, 7500)
    assert summary.total_income == 10000.0
    assert summary.rent_a_room_relief == 7500.0
    assert summary.net_profit == pytest.approx(2500.0)
    result = compute_tax_from_category_totals(summary.category_totals, get_rates(2024))
    assert result.property_profit == pytest.approx(2500.0)


def test_always_disallowable_set_matches_boxes():
    assert ALWAYS_DISALLOWABLE_CATEGORIES == {
        TransactionCategory.SE_ENTERTAINMENT,
        TransactionCategory.SE_DEPRECIATION,
    }
    for cat in ALWAYS_DISALLOWABLE_CATEGORIES | TAX_CREDIT_CATEGORIES:
        assert cat not in NON_REPORTABLE_CATEGORIES  # they all reach a box
