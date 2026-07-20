"""Quarterly aggregation logic with SA105/SA103 box mapping."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Sequence

from mtd_classifier.models.category import (
    TransactionCategory,
    ALWAYS_DISALLOWABLE_CATEGORIES,
    CATEGORY_BOX_MAPPING,
    CATEGORY_LABELS,
    CATEGORIES_REQUIRING_CONFIRMATION,
    FINANCE_COST_CATEGORIES,
    NON_REPORTABLE_CATEGORIES,
    TAX_CREDIT_CATEGORIES,
    rent_a_room_limit,
)
from mtd_classifier.models.employment_income import EmploymentIncomeRecord
from mtd_classifier.models.transaction import RawTransaction, CategorisedTransaction
from mtd_classifier.periods import TaxQuarter
from mtd_classifier.tax_estimates import estimate_tax_and_net_income


@dataclass
class CategoryTotal:
    """Aggregated total for a single category."""

    category: TransactionCategory
    label: str
    hmrc_box: str
    total_income: float = 0.0
    total_expense: float = 0.0
    # The share of total_expense that is disallowable for tax (negative,
    # like total_expense): per-transaction flags plus wholly-disallowable
    # categories. Included in the expense box on the SA103 template with a
    # matching disallowable entry, but never deducted from profit.
    total_disallowable: float = 0.0
    transaction_count: int = 0
    # True when this category needs an explicit confirmed figure (e.g.
    # Employment Income) and none has been supplied yet - the bank-deposit
    # total above must NOT be trusted or exported while this is True.
    unconfirmed: bool = False
    confirmation_note: str | None = None
    # Set on categories excluded from the profit calculation for a reason
    # other than confirmation (e.g. mortgage interest - see FINANCE_COST_CATEGORIES)
    excluded_note: str | None = None

    @property
    def net_total(self) -> float:
        return self.total_income + self.total_expense  # expenses are negative


@dataclass
class QuarterlySummary:
    """Complete summary for one MTD quarter."""

    quarter: TaxQuarter
    category_totals: dict[str, CategoryTotal] = field(default_factory=dict)
    total_transactions: int = 0
    categorised_count: int = 0
    uncategorised_count: int = 0

    @property
    def total_income(self) -> float:
        return sum(
            ct.total_income
            for ct in self.category_totals.values()
            if ct.category not in NON_REPORTABLE_CATEGORIES
        )

    @property
    def total_expenses(self) -> float:
        """Deductible expenses only: excludes mortgage/residential finance
        costs (FINANCE_COST_CATEGORIES), tax deducted at source
        (TAX_CREDIT_CATEGORIES - a credit, not an expense) and every
        disallowable share (added back, as HMRC does)."""
        return sum(
            ct.total_expense - ct.total_disallowable
            for ct in self.category_totals.values()
            if ct.category not in NON_REPORTABLE_CATEGORIES
            and ct.category not in FINANCE_COST_CATEGORIES
            and ct.category not in TAX_CREDIT_CATEGORIES
        )

    @property
    def rent_a_room_relief(self) -> float:
        """Rent-a-Room exempt amount (SA105 box 37), derived from the
        scheme's receipts: min(receipts, annual limit). Positive number.
        The limit halves when the letting income is shared - the user
        adjusts the auto-filled figure in My Tax Digital in that case."""
        receipts = self.category_totals[
            TransactionCategory.RENT_A_ROOM_INCOME.value
        ].total_income
        if receipts <= 0:
            return 0.0
        return round(min(receipts, rent_a_room_limit(self.quarter.tax_year)), 2)

    @property
    def net_profit(self) -> float:
        # expenses are negative; the Rent-a-Room exempt amount is not taxed
        return self.total_income + self.total_expenses - self.rent_a_room_relief

    @property
    def finance_costs(self) -> float:
        """Residential mortgage/loan interest not deducted from profit (positive number)."""
        return abs(sum(
            self.category_totals[c.value].total_expense
            for c in FINANCE_COST_CATEGORIES
        ))

    @property
    def estimated_finance_cost_relief(self) -> float:
        """Estimated 20% (basic rate) tax reduction for finance costs.

        A simplified estimate only: capped at property profit, as HMRC
        requires, but does not account for personal allowance, other income,
        or carrying forward unused relief when profit is too low to absorb
        it. Always verify against HMRC's own working sheet.
        """
        profit = max(0.0, self.net_profit)
        return round(0.20 * min(self.finance_costs, profit), 2)

    @property
    def estimated_income_tax(self) -> float:
        """Simplified income tax estimate on net_profit - see tax_estimates.py."""
        tax, _ = estimate_tax_and_net_income(self.net_profit, self.finance_costs)
        return tax

    @property
    def estimated_net_income_after_tax(self) -> float:
        """net_profit minus the estimated income tax above."""
        _, net_income = estimate_tax_and_net_income(self.net_profit, self.finance_costs)
        return net_income

    def reportable_totals(self) -> list[CategoryTotal]:
        """Categories worth showing: everything except personal and transfers.

        Gift Aid appears here (its yearly total is needed for the final
        declaration) but is excluded from the business totals above and from
        the bridging files.
        """
        return [
            ct
            for ct in self.category_totals.values()
            if ct.category
            not in (TransactionCategory.PERSONAL, TransactionCategory.TRANSFER)
            and ct.transaction_count > 0
        ]


def summarise_quarter(
    quarter: TaxQuarter,
    transactions: Sequence[tuple[RawTransaction, CategorisedTransaction]],
    total_in_period: int = 0,
    employment_income_record: EmploymentIncomeRecord | None = None,
) -> QuarterlySummary:
    """Aggregate categorised transactions into a quarterly summary.

    ``employment_income_record`` is the confirmed P60/P45 gross figure for
    this quarter's tax year, if one has been entered (see
    repository.get_employment_income). Employment Income transactions never
    count towards total_income/net_profit regardless (see
    NON_REPORTABLE_CATEGORIES), but without a confirmed record their row is
    marked unconfirmed rather than showing the (wrong, net-of-tax) bank total.
    """
    summary = QuarterlySummary(quarter=quarter)
    summary.total_transactions = total_in_period

    # Initialise all categories
    for cat in TransactionCategory:
        summary.category_totals[cat.value] = CategoryTotal(
            category=cat,
            label=CATEGORY_LABELS.get(cat, cat.value),
            hmrc_box=CATEGORY_BOX_MAPPING.get(cat, "Unknown"),
        )

    categorised = 0
    for raw_txn, cat_txn in transactions:
        category_str = cat_txn.category
        try:
            cat_enum = TransactionCategory(category_str)
        except ValueError:
            cat_enum = TransactionCategory.PERSONAL

        ct = summary.category_totals[cat_enum.value]
        ct.transaction_count += 1
        if raw_txn.amount >= 0:
            ct.total_income += raw_txn.amount
        else:
            ct.total_expense += raw_txn.amount
            if cat_enum in ALWAYS_DISALLOWABLE_CATEGORIES or getattr(
                cat_txn, "disallowable", False
            ):
                ct.total_disallowable += raw_txn.amount
        categorised += 1

    summary.categorised_count = categorised
    summary.uncategorised_count = total_in_period - categorised

    for cat in CATEGORIES_REQUIRING_CONFIRMATION:
        ct = summary.category_totals[cat.value]
        if ct.transaction_count == 0:
            continue
        if employment_income_record is None:
            ct.unconfirmed = True
        else:
            # Show the confirmed gross figure, not the (net-of-tax) bank total
            ct.total_income = employment_income_record.gross_amount
            ct.total_expense = 0.0
            ct.confirmation_note = (
                f"Confirmed gross pay ({employment_income_record.source}), "
                f"entered {employment_income_record.confirmed_at:%Y-%m-%d}"
            )

    for cat in FINANCE_COST_CATEGORIES:
        ct = summary.category_totals[cat.value]
        if ct.transaction_count > 0:
            ct.excluded_note = (
                "Not deducted from profit - since 2020-21, only a 20% tax "
                "reduction applies (see the estimate below), not a full deduction"
            )

    for cat in TAX_CREDIT_CATEGORIES:
        ct = summary.category_totals[cat.value]
        if ct.transaction_count > 0:
            ct.excluded_note = (
                "A tax credit (tax already deducted at source), not an "
                "expense - reported to HMRC but never deducted from profit"
            )

    return summary
