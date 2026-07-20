"""Tests for My Tax Digital bridging template export."""

from datetime import date
from pathlib import Path

import openpyxl
import pytest

from mtd_classifier.models.transaction import RawTransaction, CategorisedTransaction
from mtd_classifier.export.mytaxdigital import (
    aggregate_box_totals,
    export_bridging_templates,
)


def _txn(d, desc, amount, category, i):
    raw = RawTransaction(
        import_log_id=1,
        bank="firstdirect",
        date=d,
        description=desc,
        amount=amount,
        dedup_hash=f"mtd_bridge_hash_{i}",
    )
    cat = CategorisedTransaction(raw_transaction_id=i, category=category, confidence="manual")
    return raw, cat


@pytest.fixture
def sample_transactions():
    data = [
        (date(2026, 4, 15), "INVOICE 1", 2500.00, "self_employed_income"),
        (date(2026, 5, 15), "INVOICE 2", 1500.00, "self_employed_income"),
        (date(2026, 4, 30), "RENT RECEIVED", 950.00, "rental_income"),
        (date(2026, 5, 31), "RENT RECEIVED", 950.00, "rental_income"),
        (date(2026, 4, 17), "MORTGAGE", -850.00, "mortgage_interest"),
        (date(2026, 4, 22), "LETTING AGENT", -150.00, "letting_agent_fees"),
        (date(2026, 4, 23), "SOLICITOR", -300.00, "property_legal_fees"),
        (date(2026, 5, 2), "PLUMBER", -220.50, "property_repairs"),
        (date(2026, 5, 3), "PLUMBER REFUND", 20.50, "property_repairs"),
        (date(2026, 5, 10), "TRAIN TICKET", -45.00, "se_travel"),
        (date(2026, 4, 16), "TESCO", -45.67, "personal"),
        (date(2026, 4, 18), "TO SAVINGS", -500.00, "transfer"),
    ]
    return [_txn(d, desc, amount, cat, i) for i, (d, desc, amount, cat) in enumerate(data)]


def test_templates_download_and_validate(bridging_templates):
    """Templates are NOT bundled (they're My Tax Digital's own files) -
    they download on first use, cache, and match this release's pinned
    SHA-256 and layout anchors."""
    from mtd_classifier.export.template_fetch import validate_template

    for form, path in bridging_templates.items():
        assert path.exists()
        validate_template(form, path)  # hash + anchors match the cell map


def test_changed_template_refuses_with_update_message(bridging_templates, tmp_path):
    """If mytaxdigital.co.uk ever publishes a different template, the hash
    pin must stop the export and ask for an app update - never fill an
    unverified layout."""
    from mtd_classifier.export.template_fetch import (
        TemplateChangedError,
        validate_template,
    )

    tampered = tmp_path / "MTD_SA103_template.xlsx"
    data = bytearray(bridging_templates["SA103"].read_bytes())
    data[-1] ^= 0xFF
    tampered.write_bytes(bytes(data))
    with pytest.raises(TemplateChangedError) as excinfo:
        validate_template("SA103", tampered)
    assert "update the app" in str(excinfo.value)


def test_aggregate_income_and_expense_signs(sample_transactions):
    totals, skipped = aggregate_box_totals(sample_transactions)

    # Income boxes keep their positive sign
    assert totals["SA103:C7"].amount == pytest.approx(4000.00)  # box 15 turnover
    assert totals["SA105:C7"].amount == pytest.approx(1900.00)  # box 20a rents

    # Expense boxes become positive magnitudes
    assert totals["SA105:C42"].amount == pytest.approx(850.00)  # box 44 mortgage interest
    assert totals["SA103:C31"].amount == pytest.approx(45.00)   # box 20 travel

    # Refund nets against the expense
    assert totals["SA105:C25"].amount == pytest.approx(200.00)  # box 25 repairs

    # Personal and transfer are skipped, not exported
    assert skipped == ["personal", "transfer"]


def test_letting_agent_and_legal_fees_share_box_27(sample_transactions):
    totals, _ = aggregate_box_totals(sample_transactions)
    bt = totals["SA105:C33"]
    assert bt.amount == pytest.approx(450.00)
    assert bt.transaction_count == 2


def test_export_writes_values_into_template_cells(bridging_templates, sample_transactions, tmp_path):
    result = export_bridging_templates(sample_transactions, tmp_path, "Q1-2026")

    assert len(result.files_written) == 2
    sa103 = tmp_path / "MTD_SA103_Q1-2026_cumulative.xlsx"
    sa105 = tmp_path / "MTD_SA105_Q1-2026_cumulative.xlsx"
    assert sa103.exists()
    assert sa105.exists()

    ws103 = openpyxl.load_workbook(sa103).worksheets[0]
    assert ws103["C7"].value == pytest.approx(4000.00)
    assert ws103["C31"].value == pytest.approx(45.00)
    # Untouched boxes stay empty
    assert ws103["C43"].value is None

    ws105 = openpyxl.load_workbook(sa105).worksheets[0]
    assert ws105["C7"].value == pytest.approx(1900.00)
    assert ws105["C21"].value is None
    assert ws105["C25"].value == pytest.approx(200.00)
    assert ws105["C33"].value == pytest.approx(450.00)
    assert ws105["C42"].value == pytest.approx(850.00)


def test_export_single_form_only(bridging_templates, sample_transactions, tmp_path):
    result = export_bridging_templates(
        sample_transactions, tmp_path, "Q1-2026", forms=("SA103",)
    )
    assert [p.name for p in result.files_written] == ["MTD_SA103_Q1-2026_cumulative.xlsx"]


def test_export_skips_form_with_no_data(bridging_templates, tmp_path):
    txns = [_txn(date(2026, 4, 20), "INVOICE", 100.0, "self_employed_income", 1)]
    result = export_bridging_templates(txns, tmp_path, "Q1-2026")
    assert [p.name for p in result.files_written] == ["MTD_SA103_Q1-2026_cumulative.xlsx"]


def test_unknown_category_reported_as_skipped(tmp_path):
    txns = [_txn(date(2026, 4, 20), "MYSTERY", -10.0, "not_a_real_category", 1)]
    totals, skipped = aggregate_box_totals(txns)
    assert totals == {}
    assert skipped == ["not_a_real_category"]
