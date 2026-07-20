"""Tests for category direction filtering and box coverage."""

from mtd_classifier.models.category import (
    CATEGORY_BOX_MAPPING,
    CATEGORY_LABELS,
    EXPENSE_CATEGORIES,
    INCOME_CATEGORIES,
    NEUTRAL_CATEGORIES,
    TransactionCategory,
    categories_for_amount,
)


def test_every_category_has_exactly_one_direction():
    for c in TransactionCategory:
        memberships = sum([
            c in INCOME_CATEGORIES, c in EXPENSE_CATEGORIES, c in NEUTRAL_CATEGORIES,
        ])
        assert memberships == 1, f"{c} must be in exactly one direction set"


def test_income_amount_offers_income_and_neutral_only():
    cats = categories_for_amount(950.0)
    assert TransactionCategory.RENTAL_INCOME in cats
    assert TransactionCategory.SELF_EMPLOYED_INCOME in cats
    assert TransactionCategory.EMPLOYMENT_INCOME in cats
    assert TransactionCategory.PERSONAL in cats
    assert TransactionCategory.TRANSFER in cats
    assert TransactionCategory.SE_TRAVEL not in cats
    assert TransactionCategory.MORTGAGE_INTEREST not in cats


def test_expense_amount_offers_expense_and_neutral_only():
    cats = categories_for_amount(-45.67)
    assert TransactionCategory.SE_TRAVEL in cats
    assert TransactionCategory.SE_TELECOMS in cats
    assert TransactionCategory.SE_IT in cats
    assert TransactionCategory.PERSONAL in cats
    assert TransactionCategory.RENTAL_INCOME not in cats


def test_new_categories_fully_wired():
    from mtd_classifier.export.mytaxdigital import CATEGORY_BOX_CELLS

    for cat in (TransactionCategory.SE_TELECOMS, TransactionCategory.SE_IT):
        assert cat in CATEGORY_LABELS
        assert "Box 23" in CATEGORY_BOX_MAPPING[cat]
        assert CATEGORY_BOX_CELLS[cat].cell == "C43"  # shares box 23


def test_every_reportable_category_reaches_a_bridging_cell():
    from mtd_classifier.export.mytaxdigital import CATEGORY_BOX_CELLS
    from mtd_classifier.models.category import NON_REPORTABLE_CATEGORIES

    for c in TransactionCategory:
        if c in NON_REPORTABLE_CATEGORIES:
            assert c not in CATEGORY_BOX_CELLS
        else:
            assert c in CATEGORY_BOX_CELLS, f"{c} has no bridging template cell"


def test_gift_aid_tracked_but_not_in_business_totals():
    from datetime import date

    from mtd_classifier.export.mtd_summary import summarise_quarter
    from mtd_classifier.models.transaction import RawTransaction, CategorisedTransaction
    from mtd_classifier.periods import TaxQuarter

    def txn(desc, amount, category, i):
        raw = RawTransaction(
            import_log_id=1, bank="firstdirect", date=date(2026, 4, 20),
            description=desc, amount=amount, dedup_hash=f"ga_{i}",
        )
        cat = CategorisedTransaction(
            raw_transaction_id=i, category=category, confidence="manual"
        )
        return raw, cat

    txns = [
        txn("INVOICE", 1000.00, "self_employed_income", 1),
        txn("OXFAM MONTHLY", -25.00, "gift_aid", 2),
    ]
    summary = summarise_quarter(TaxQuarter(2026, 1), txns, total_in_period=2)

    # Gift Aid must not reduce business profit...
    assert summary.total_expenses == 0.0
    assert summary.net_profit == 1000.00
    # ...but its total stays visible for the final declaration
    reportable = {ct.category: ct for ct in summary.reportable_totals()}
    assert TransactionCategory.GIFT_AID in reportable
    assert reportable[TransactionCategory.GIFT_AID].total_expense == -25.00
    assert "final declaration" in reportable[TransactionCategory.GIFT_AID].hmrc_box


def test_gift_aid_is_expense_direction():
    assert TransactionCategory.GIFT_AID in categories_for_amount(-25.0)
    assert TransactionCategory.GIFT_AID not in categories_for_amount(25.0)


def test_gift_aid_never_reaches_bridging_files():
    from mtd_classifier.export.mytaxdigital import aggregate_box_totals
    from datetime import date

    from mtd_classifier.models.transaction import RawTransaction, CategorisedTransaction

    raw = RawTransaction(
        import_log_id=1, bank="firstdirect", date=date(2026, 4, 20),
        description="OXFAM", amount=-25.0, dedup_hash="ga_bridge",
    )
    cat = CategorisedTransaction(raw_transaction_id=1, category="gift_aid", confidence="manual")
    totals, skipped = aggregate_box_totals([(raw, cat)])
    assert totals == {}
    assert skipped == ["gift_aid"]


def test_employment_interest_dividend_cashback_tracked_but_not_in_business_totals():
    from datetime import date

    from mtd_classifier.export.mtd_summary import summarise_quarter
    from mtd_classifier.models.transaction import RawTransaction, CategorisedTransaction
    from mtd_classifier.periods import TaxQuarter

    def txn(desc, amount, category, i):
        raw = RawTransaction(
            import_log_id=1, bank="firstdirect", date=date(2026, 4, 20),
            description=desc, amount=amount, dedup_hash=f"nrp_{i}",
        )
        cat = CategorisedTransaction(
            raw_transaction_id=i, category=category, confidence="manual"
        )
        return raw, cat

    txns = [
        txn("INVOICE", 1000.00, "self_employed_income", 1),
        txn("SALARY", 2000.00, "employment_income", 2),
        txn("BANK INTEREST", 12.50, "interest_income", 3),
        txn("SHARE DIVIDEND", 30.00, "dividend_income", 4),
        txn("CARD CASHBACK", 5.00, "cashback", 5),
    ]
    summary = summarise_quarter(TaxQuarter(2026, 1), txns, total_in_period=5)

    # None of the four should inflate business income
    assert summary.total_income == 1000.00
    assert summary.net_profit == 1000.00

    # ...but all stay visible for reference / the final declaration
    reportable = {ct.category: ct for ct in summary.reportable_totals()}
    for cat, expected_note in [
        (TransactionCategory.EMPLOYMENT_INCOME, "P60/P45"),
        (TransactionCategory.INTEREST_INCOME, "interest certificate"),
        (TransactionCategory.DIVIDEND_INCOME, "dividend voucher"),
        (TransactionCategory.CASHBACK, "Usually not taxable"),
    ]:
        assert cat in reportable
        assert expected_note in reportable[cat].hmrc_box


def test_employment_interest_dividend_cashback_are_income_direction():
    for cat_value in ("employment_income", "interest_income", "dividend_income",
                      "cashback", "bank_rewards"):
        cat = TransactionCategory(cat_value)
        assert cat in categories_for_amount(100.0)
        assert cat not in categories_for_amount(-100.0)


def test_bank_rewards_taxable_but_cashback_not():
    """Fixed monthly rewards are taxable (year-end other income); variable
    cashback is not - both stay out of quarterly business figures."""
    from mtd_classifier.classifier.rule_engine import RuleBasedClassifier
    from mtd_classifier.models.category import NON_REPORTABLE_CATEGORIES

    assert TransactionCategory.BANK_REWARDS in NON_REPORTABLE_CATEGORIES
    assert TransactionCategory.CASHBACK in NON_REPORTABLE_CATEGORIES
    assert "TAXABLE" in CATEGORY_BOX_MAPPING[TransactionCategory.BANK_REWARDS]
    assert CATEGORY_LABELS[TransactionCategory.CASHBACK] == "Variable Cashback"

    classifier = RuleBasedClassifier()
    assert classifier.classify("MONTHLY REWARD PAYMENT", 5.0).category == "bank_rewards"
    assert classifier.classify("CASHBACK MARCH", 12.50).category == "cashback"
    assert classifier.classify("SWITCH BONUS", 175.0).category == "cashback"


def test_new_income_categories_never_reach_bridging_files():
    from mtd_classifier.export.mytaxdigital import aggregate_box_totals
    from datetime import date

    from mtd_classifier.models.transaction import RawTransaction, CategorisedTransaction

    for i, category in enumerate(
        ["employment_income", "interest_income", "dividend_income", "cashback"]
    ):
        raw = RawTransaction(
            import_log_id=1, bank="firstdirect", date=date(2026, 4, 20),
            description="TEST", amount=10.0, dedup_hash=f"nrp_bridge_{i}",
        )
        cat = CategorisedTransaction(
            raw_transaction_id=i, category=category, confidence="manual"
        )
        totals, skipped = aggregate_box_totals([(raw, cat)])
        assert totals == {}
        assert skipped == [category]


def test_se_telecoms_and_it_share_box_23_with_office_costs():
    from datetime import date

    from mtd_classifier.export.mytaxdigital import aggregate_box_totals
    from mtd_classifier.models.transaction import RawTransaction, CategorisedTransaction

    def txn(desc, amount, category, i):
        raw = RawTransaction(
            import_log_id=1, bank="firstdirect", date=date(2026, 4, 20),
            description=desc, amount=amount, dedup_hash=f"b23_{i}",
        )
        cat = CategorisedTransaction(
            raw_transaction_id=i, category=category, confidence="manual"
        )
        return raw, cat

    totals, _ = aggregate_box_totals([
        txn("BT BROADBAND", -30.00, "se_telecoms", 1),
        txn("GITHUB SUBSCRIPTION", -10.00, "se_it", 2),
        txn("STAPLES", -5.00, "se_office_costs", 3),
    ])
    box23 = totals["SA103:C43"]
    assert box23.amount == 45.00
    assert box23.transaction_count == 3
