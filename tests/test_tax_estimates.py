"""Tests for the simplified UK income tax estimate."""

from mtd_classifier.tax_estimates import estimate_income_tax, estimate_tax_and_net_income


def test_income_under_personal_allowance_is_untaxed():
    assert estimate_income_tax(10000.0) == 0.0
    assert estimate_income_tax(0.0) == 0.0
    assert estimate_income_tax(-500.0) == 0.0


def test_basic_rate_band():
    # (20000 - 12570) x 20% = 1486.00
    assert estimate_income_tax(20000.0) == 1486.00


def test_higher_rate_band():
    # Basic band: (50270 - 12570) x 20% = 7540.00
    # Higher band: (60000 - 50270) x 40% = 3892.00
    assert estimate_income_tax(60000.0) == round(7540.00 + 3892.00, 2)


def test_additional_rate_band():
    # At 150000 the personal allowance has fully tapered to zero (gone by 125140)
    basic = (50270.0 - 0.0) * 0.20
    higher = (125140.0 - 50270.0) * 0.40
    additional = (150000.0 - 125140.0) * 0.45
    assert estimate_income_tax(150000.0) == round(basic + higher + additional, 2)


def test_personal_allowance_tapers_above_100k():
    # At exactly 100000, PA is untouched, but 50270-100000 falls in the higher band
    basic = (50270.0 - 12570.0) * 0.20
    higher = (100000.0 - 50270.0) * 0.40
    assert estimate_income_tax(100000.0) == round(basic + higher, 2)
    # At 125140 the PA has fully tapered to zero (100000 + 2*12570)
    tax_at_taper_end = estimate_income_tax(125140.0)
    basic = (50270.0 - 0.0) * 0.20
    higher = (125140.0 - 50270.0) * 0.40
    assert tax_at_taper_end == round(basic + higher, 2)


def test_estimate_tax_and_net_income_applies_finance_cost_relief():
    tax, net_income = estimate_tax_and_net_income(net_profit=20000.0, finance_costs=850.0)
    # Gross tax 1486.00, relief = 20% x min(850, 20000) = 170.00
    assert tax == round(1486.00 - 170.00, 2)
    assert net_income == round(20000.0 - tax, 2)


def test_estimate_tax_and_net_income_relief_cannot_make_tax_negative():
    tax, net_income = estimate_tax_and_net_income(net_profit=500.0, finance_costs=10000.0)
    assert tax == 0.0  # relief capped at profit, tax can't go negative
    assert net_income == 500.0


def test_zero_or_negative_profit_means_no_tax():
    tax, net_income = estimate_tax_and_net_income(net_profit=-1000.0, finance_costs=0.0)
    assert tax == 0.0
    assert net_income == -1000.0
