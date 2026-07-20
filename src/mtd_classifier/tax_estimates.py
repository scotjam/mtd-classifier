"""Simplified UK income tax estimate.

Uses standard rest-of-UK income tax bands for the current tax year and does
NOT account for: National Insurance (Class 2/4), Scottish rates, any other
income you have (employment, savings, dividends), the savings/dividend
allowances, marriage allowance, student loan repayments, or any other
relief. It exists purely to give a rough sense of scale against the
self-employment/property profit this app tracks - always verify with HMRC's
own calculator or a professional. See disclaimer.py, shown wherever this
figure is displayed.
"""

from __future__ import annotations

PERSONAL_ALLOWANCE = 12_570.0
PERSONAL_ALLOWANCE_TAPER_THRESHOLD = 100_000.0  # PA reduced £1 per £2 earned above this
BASIC_RATE_LIMIT = 50_270.0
HIGHER_RATE_LIMIT = 125_140.0
BASIC_RATE = 0.20
HIGHER_RATE = 0.40
ADDITIONAL_RATE = 0.45


def estimate_income_tax(taxable_income: float) -> float:
    """Estimate income tax due on ``taxable_income`` using rest-of-UK bands."""
    if taxable_income <= 0:
        return 0.0

    if taxable_income > PERSONAL_ALLOWANCE_TAPER_THRESHOLD:
        allowance = max(
            0.0,
            PERSONAL_ALLOWANCE - (taxable_income - PERSONAL_ALLOWANCE_TAPER_THRESHOLD) / 2,
        )
    else:
        allowance = PERSONAL_ALLOWANCE

    tax = 0.0
    band_start = allowance
    for band_end, rate in (
        (BASIC_RATE_LIMIT, BASIC_RATE),
        (HIGHER_RATE_LIMIT, HIGHER_RATE),
        (float("inf"), ADDITIONAL_RATE),
    ):
        if taxable_income <= band_start:
            break
        band_top = min(taxable_income, band_end)
        tax += (band_top - band_start) * rate
        band_start = band_top

    return round(tax, 2)


def estimate_tax_and_net_income(
    net_profit: float, finance_costs: float
) -> tuple[float, float]:
    """Estimated income tax (after the 20% finance-cost relief) and the
    resulting net income. Returns (estimated_tax, estimated_net_income)."""
    gross_tax = estimate_income_tax(max(0.0, net_profit))
    relief = round(0.20 * min(finance_costs, max(0.0, net_profit)), 2)
    net_tax = round(max(0.0, gross_tax - relief), 2)
    return net_tax, round(net_profit - net_tax, 2)
