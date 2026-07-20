"""Personal Tax Computation engine - mirrors a professional tax computation
printout (income by source, allowances, tax borne by band, rate-restricted
relief, Class 2/4 NIC, total liability).

This is a simplified model - see disclaimer.py. Known simplifications:
- Rest-of-UK rates only (no Scottish/Welsh rates).
- No marriage allowance, blind person's allowance, or student loan.
- Employment income is assumed already taxed correctly via PAYE for NIC
  purposes - only self-employment Class 2/4 NIC is computed here.
- Pre-2016-17 dividends are modelled via net-of-notional-credit rates baked
  into TaxYearRates (see tax_year_rates.py) rather than a separate code path,
  so the same band-stacking logic works for every year uniformly.

HMRC's real ordering - and the one used here - taxes income in this order,
each stacking on top of the last for band purposes: non-savings, then
savings, then dividends. The Personal Allowance is set against non-savings
first. Gift Aid donations widen the basic-rate and additional-rate
thresholds (grossed up at that year's basic rate) rather than reducing tax
directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mtd_classifier.models.category import TransactionCategory, rent_a_room_limit
from mtd_classifier.tax_year_rates import TaxYearRates

# Expense categories per source. Disallowable shares (per-transaction flag
# or wholly-disallowable categories like entertainment/depreciation) are
# excluded via CategoryTotal.total_disallowable, so listing a category here
# only deducts its ALLOWABLE portion. Tax deducted at source (boxes 81/21)
# is a credit against the final liability, not an expense - not listed.
_SELF_EMPLOYMENT_EXPENSE_CATEGORIES = (
    TransactionCategory.LOAN_INTEREST,
    TransactionCategory.SE_GOODS,
    TransactionCategory.SE_SUBCONTRACTORS,
    TransactionCategory.SE_STAFF_COSTS,
    TransactionCategory.SE_TRAVEL,
    TransactionCategory.SE_PREMISES,
    TransactionCategory.SE_REPAIRS,
    TransactionCategory.SE_OFFICE_COSTS,
    TransactionCategory.SE_TELECOMS,
    TransactionCategory.SE_IT,
    TransactionCategory.SE_PROFESSIONAL_FEES,
    TransactionCategory.SE_ADVERTISING,
    TransactionCategory.SE_ENTERTAINMENT,
    TransactionCategory.SE_BANK_CHARGES,
    TransactionCategory.SE_BAD_DEBTS,
    TransactionCategory.SE_DEPRECIATION,
    TransactionCategory.SE_OTHER_EXPENSE,
)
_PROPERTY_INCOME_CATEGORIES = (
    TransactionCategory.RENTAL_INCOME,
    TransactionCategory.PROPERTY_OTHER_INCOME,
    TransactionCategory.RENT_A_ROOM_INCOME,
    TransactionCategory.LEASE_PREMIUM_INCOME,
    TransactionCategory.REVERSE_PREMIUM_INCOME,
)
_PROPERTY_EXPENSE_CATEGORIES = (
    TransactionCategory.PROPERTY_REPAIRS,
    TransactionCategory.PROPERTY_INSURANCE,
    TransactionCategory.PROPERTY_SERVICES,
    TransactionCategory.PROPERTY_TRAVEL,
    TransactionCategory.LETTING_AGENT_FEES,
    TransactionCategory.PROPERTY_LEGAL_FEES,
    TransactionCategory.PROPERTY_OTHER_EXPENSE,
    TransactionCategory.NON_RES_FINANCE_COSTS,
)


@dataclass
class BandTax:
    """One slice of income taxed at one rate."""

    label: str
    amount: float
    rate: float
    tax: float


@dataclass
class TaxComputationResult:
    tax_year: int

    # Income by source
    self_employment_profit: float
    property_profit: float
    employment_income: float
    interest_income: float
    dividend_income: float
    total_income: float

    # Allowances
    personal_allowance_before_taper: float
    personal_allowance: float
    taxable_income: float

    # Tax borne, by income type then band
    non_savings_bands: list[BandTax] = field(default_factory=list)
    savings_bands: list[BandTax] = field(default_factory=list)
    dividend_bands: list[BandTax] = field(default_factory=list)

    gross_tax: float = 0.0
    finance_costs: float = 0.0
    finance_costs_expense_portion: float = 0.0  # already reflected in property_profit
    rate_restricted_relief: float = 0.0  # 20% credit on the non-expense portion
    income_tax_liability: float = 0.0

    class2_nic: float = 0.0
    class4_nic: float = 0.0

    total_tax_and_nic: float = 0.0

    gift_aid_grossed_up: float = 0.0  # basic/additional-rate band widening


def _tax_slice(
    amount: float, start_position: float, edges: list[tuple[float, float, str]]
) -> tuple[float, float, list[BandTax]]:
    """Tax ``amount`` starting from ``start_position`` on a shared band ladder.

    ``edges`` is a list of (upper_bound, rate, label) in ascending order of
    upper_bound. Returns (tax, new_position, band breakdown for non-zero
    slices only).
    """
    tax = 0.0
    pos = start_position
    remaining = amount
    bands: list[BandTax] = []
    for upper, rate, label in edges:
        if remaining <= 0:
            break
        capacity = max(0.0, upper - pos)
        slice_amount = min(remaining, capacity)
        if slice_amount > 0:
            slice_tax = round(slice_amount * rate, 2)
            tax += slice_tax
            bands.append(BandTax(label=label, amount=round(slice_amount, 2), rate=rate, tax=slice_tax))
            pos += slice_amount
            remaining -= slice_amount
    return round(tax, 2), pos, bands


def _non_savings_edges(rates: TaxYearRates, basic_limit: float, additional_threshold: float | None):
    edges = [(basic_limit, rates.basic_rate, "Basic rate")]
    if rates.additional_rate is not None and additional_threshold is not None:
        edges.append((additional_threshold, rates.higher_rate, "Higher rate"))
        edges.append((float("inf"), rates.additional_rate, "Additional rate"))
    else:
        edges.append((float("inf"), rates.higher_rate, "Higher rate"))
    return edges


def compute_personal_tax(
    rates: TaxYearRates,
    self_employment_profit: float = 0.0,
    property_profit: float = 0.0,
    employment_income: float = 0.0,
    interest_income: float = 0.0,
    dividend_income: float = 0.0,
    gift_aid_net_donations: float = 0.0,
    finance_costs: float = 0.0,
) -> TaxComputationResult:
    """Compute a full Personal Tax Computation for one tax year.

    All income figures are non-negative (a loss in one source is not offset
    against another here - that requires explicit loss relief claims, which
    this simplified model doesn't attempt). ``finance_costs`` is the
    residential mortgage/loan interest for property income (see
    FINANCE_COST_CATEGORIES) - the year's phase-in fraction determines how
    much is deducted as a normal expense vs given as a 20% tax reduction.
    """
    self_employment_profit = max(0.0, self_employment_profit)
    property_profit_before_finance = max(0.0, property_profit)
    employment_income = max(0.0, employment_income)
    interest_income = max(0.0, interest_income)
    dividend_income = max(0.0, dividend_income)
    finance_costs = max(0.0, finance_costs)

    # Mortgage interest relief phase-in: part of the finance cost is still a
    # normal deductible expense; the rest gets a 20% credit instead.
    expense_portion = round(finance_costs * rates.mortgage_relief_expense_fraction, 2)
    credit_portion = round(finance_costs - expense_portion, 2)
    property_profit = max(0.0, property_profit_before_finance - expense_portion)

    non_savings_total = self_employment_profit + property_profit + employment_income
    total_income = non_savings_total + interest_income + dividend_income

    # Personal Allowance taper: lost £1 per pa_taper_divisor of income above
    # the threshold (no taper at all for years where the threshold is None).
    if rates.pa_taper_threshold is not None and total_income > rates.pa_taper_threshold:
        reduction = min(
            rates.personal_allowance,
            (total_income - rates.pa_taper_threshold) / rates.pa_taper_divisor,
        )
        personal_allowance = max(0.0, rates.personal_allowance - reduction)
    else:
        personal_allowance = rates.personal_allowance
    personal_allowance = round(personal_allowance, 2)

    # Gift Aid: grossed up at this year's basic rate, widens the basic-rate
    # and additional-rate thresholds (not a direct deduction from tax).
    gift_aid_grossed_up = round(
        gift_aid_net_donations / (1 - rates.basic_rate), 2
    ) if gift_aid_net_donations > 0 else 0.0

    # rates.higher_rate_threshold/additional_rate_threshold are GROSS income
    # figures (as HMRC publishes them). Converting to a taxable-income-
    # position edge means subtracting the taxpayer's own (already-tapered)
    # Personal Allowance - so someone whose PA has tapered to zero correctly
    # gets a WIDER basic-rate band in taxable-income terms, matching real
    # HMRC behaviour.
    basic_limit = (rates.higher_rate_threshold - personal_allowance) + gift_aid_grossed_up
    additional_threshold = (
        (rates.additional_rate_threshold - personal_allowance) + gift_aid_grossed_up
        if rates.additional_rate_threshold is not None else None
    )

    # PA is set against non-savings income first.
    taxable_non_savings = max(0.0, non_savings_total - personal_allowance)
    pa_remaining = max(0.0, personal_allowance - non_savings_total)
    taxable_savings = max(0.0, interest_income - pa_remaining)
    pa_remaining = max(0.0, pa_remaining - interest_income)
    taxable_dividends = max(0.0, dividend_income - pa_remaining)
    taxable_income = round(taxable_non_savings + taxable_savings + taxable_dividends, 2)

    # --- Non-savings band ---
    ns_edges = _non_savings_edges(rates, basic_limit, additional_threshold)
    ns_tax, position, non_savings_bands = _tax_slice(taxable_non_savings, 0.0, ns_edges)

    # --- Savings band: starting rate for savings, then Personal Savings
    # Allowance (tier depends on the band non-savings income already reached),
    # then the standard bands. ---
    starting_rate_upper = max(position, rates.starting_rate_for_savings_band)
    if additional_threshold is not None and position >= additional_threshold:
        psa = 0.0
    elif position >= basic_limit:
        psa = rates.personal_savings_allowance_higher
    else:
        psa = rates.personal_savings_allowance_basic
    psa_upper = starting_rate_upper + psa

    savings_edges = [
        (rates.starting_rate_for_savings_band, rates.starting_rate_for_savings_pct, "Starting rate for savings"),
        (psa_upper, 0.0, "Personal Savings Allowance"),
    ] + _non_savings_edges(rates, basic_limit, additional_threshold)
    # Relabel the standard bands for savings context
    savings_edges = savings_edges[:2] + [
        (upper, rate, f"Savings - {label}") for upper, rate, label in savings_edges[2:]
    ]
    sv_tax, position, savings_bands = _tax_slice(taxable_savings, position, savings_edges)

    # --- Dividend band: Dividend Allowance (modern years) or baked-in
    # net-of-credit rates (pre-2016-17, dividend_allowance is None). ---
    if rates.dividend_allowance is not None:
        div_edges = [(position + rates.dividend_allowance, 0.0, "Dividend allowance")]
    else:
        div_edges = []
    div_edges += [
        (basic_limit, rates.dividend_basic_rate, "Dividends - basic rate"),
    ]
    if additional_threshold is not None:
        div_edges.append((additional_threshold, rates.dividend_higher_rate, "Dividends - higher rate"))
        div_edges.append((float("inf"), rates.dividend_additional_rate, "Dividends - additional rate"))
    else:
        div_edges.append((float("inf"), rates.dividend_higher_rate, "Dividends - higher rate"))
    dv_tax, position, dividend_bands = _tax_slice(taxable_dividends, position, div_edges)

    gross_tax = round(ns_tax + sv_tax + dv_tax, 2)
    # HMRC caps the 20% relief at the property profit itself (the "profit"
    # test) - it can't create or increase a loss. No carry-forward of unused
    # relief to a later year is modelled here (a further simplification).
    rate_restricted_relief = round(rates.basic_rate * min(credit_portion, max(0.0, property_profit)), 2)
    # It also can't exceed the tax otherwise due overall.
    rate_restricted_relief = min(rate_restricted_relief, gross_tax)
    income_tax_liability = round(max(0.0, gross_tax - rate_restricted_relief), 2)

    # --- Class 2/4 NIC (self-employment profit only, not property) ---
    class2_nic = 0.0
    if (
        rates.ni_class2_weekly_rate is not None
        and self_employment_profit > (rates.ni_class2_small_profits_threshold or 0.0)
    ):
        class2_nic = round(rates.ni_class2_weekly_rate * 52, 2)

    class4_nic = 0.0
    if self_employment_profit > rates.ni_class4_lower_profits_limit:
        main_band = min(self_employment_profit, rates.ni_class4_upper_profits_limit) - rates.ni_class4_lower_profits_limit
        class4_nic += max(0.0, main_band) * rates.ni_class4_main_rate
        if self_employment_profit > rates.ni_class4_upper_profits_limit:
            class4_nic += (self_employment_profit - rates.ni_class4_upper_profits_limit) * rates.ni_class4_upper_rate
    class4_nic = round(class4_nic, 2)

    total_tax_and_nic = round(income_tax_liability + class2_nic + class4_nic, 2)

    return TaxComputationResult(
        tax_year=rates.tax_year,
        self_employment_profit=round(self_employment_profit, 2),
        property_profit=round(property_profit, 2),
        employment_income=round(employment_income, 2),
        interest_income=round(interest_income, 2),
        dividend_income=round(dividend_income, 2),
        total_income=round(total_income, 2),
        personal_allowance_before_taper=rates.personal_allowance,
        personal_allowance=personal_allowance,
        taxable_income=taxable_income,
        non_savings_bands=non_savings_bands,
        savings_bands=savings_bands,
        dividend_bands=dividend_bands,
        gross_tax=gross_tax,
        finance_costs=round(finance_costs, 2),
        finance_costs_expense_portion=expense_portion,
        rate_restricted_relief=rate_restricted_relief,
        income_tax_liability=income_tax_liability,
        class2_nic=class2_nic,
        class4_nic=class4_nic,
        total_tax_and_nic=total_tax_and_nic,
        gift_aid_grossed_up=gift_aid_grossed_up,
    )


def compute_tax_from_category_totals(
    category_totals: dict,
    rates: TaxYearRates,
    employment_income: float = 0.0,
) -> TaxComputationResult:
    """Build the computation's income inputs from a year's CategoryTotal
    dict (as produced by mtd_summary.summarise_quarter, keyed by category
    value) and run the full computation.

    ``employment_income`` is passed separately since it must come from the
    CONFIRMED EmploymentIncomeRecord, never derived from bank deposits - see
    models/employment_income.py.
    """

    def income(cat: TransactionCategory) -> float:
        return category_totals[cat.value].total_income

    def expense(cat: TransactionCategory) -> float:
        """Allowable portion only (both figures are stored negative)."""
        ct = category_totals[cat.value]
        return ct.total_expense - getattr(ct, "total_disallowable", 0.0)

    self_employment_profit = (
        income(TransactionCategory.SELF_EMPLOYED_INCOME)
        + income(TransactionCategory.SE_OTHER_INCOME)
        + sum(expense(c) for c in _SELF_EMPLOYMENT_EXPENSE_CATEGORIES)
    )
    property_profit = sum(
        income(c) for c in _PROPERTY_INCOME_CATEGORIES
    ) + sum(expense(c) for c in _PROPERTY_EXPENSE_CATEGORIES)
    # Rent-a-Room exempt amount (box 37): the scheme's receipts are only
    # taxable above the annual limit (halved if shared - not modelled).
    rent_a_room_receipts = income(TransactionCategory.RENT_A_ROOM_INCOME)
    if rent_a_room_receipts > 0:
        property_profit -= min(rent_a_room_receipts, rent_a_room_limit(rates.tax_year))
    finance_costs = abs(expense(TransactionCategory.MORTGAGE_INTEREST))
    gift_aid_net_donations = abs(expense(TransactionCategory.GIFT_AID))
    interest_income = income(TransactionCategory.INTEREST_INCOME)
    dividend_income = income(TransactionCategory.DIVIDEND_INCOME)

    return compute_personal_tax(
        rates,
        self_employment_profit=self_employment_profit,
        property_profit=property_profit,
        employment_income=employment_income,
        interest_income=interest_income,
        dividend_income=dividend_income,
        gift_aid_net_donations=gift_aid_net_donations,
        finance_costs=finance_costs,
    )
