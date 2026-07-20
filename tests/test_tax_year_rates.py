"""Sanity checks on the historical UK tax rates table.

The to-the-penny cross-checks against real accountants' computations live
in the LOCAL-ONLY, gitignored tests/private/ folder - they contain real
personal figures and are never committed."""

import pytest

from mtd_classifier.tax_year_rates import TAX_YEAR_RATES, available_tax_years, get_rates


def test_covers_at_least_twenty_years():
    years = available_tax_years()
    assert len(years) >= 20
    assert max(years) - min(years) >= 19


def test_no_year_left_as_unverified_placeholder():
    unverified = [y for y, r in TAX_YEAR_RATES.items() if "UNVERIFIED" in r.source]
    assert unverified == [], f"Unverified placeholder years remain: {unverified}"


def test_every_year_has_a_source_citation():
    for year, rates in TAX_YEAR_RATES.items():
        assert rates.source, f"{year} has no source citation"


@pytest.mark.parametrize("year", list(TAX_YEAR_RATES.keys()))
def test_structural_consistency(year):
    rates = get_rates(year)
    assert rates.tax_year == year
    # additional_rate and additional_rate_threshold must agree on existing
    assert (rates.additional_rate is None) == (rates.additional_rate_threshold is None)
    # thresholds must be increasing
    assert rates.higher_rate_threshold > rates.personal_allowance
    if rates.additional_rate_threshold is not None:
        assert rates.additional_rate_threshold > rates.higher_rate_threshold
    # NIC: Class 2 rate and threshold must agree on existing (both or neither)
    assert (rates.ni_class2_weekly_rate is None) == (
        year >= 2024
    ), f"{year}: Class 2 should be abolished (None) from 2024-25 onwards only"
    assert rates.ni_class4_upper_profits_limit > rates.ni_class4_lower_profits_limit
    assert 0.0 <= rates.mortgage_relief_expense_fraction <= 1.0


def test_pa_taper_introduced_2010_11_not_before():
    assert get_rates(2009).pa_taper_threshold is None
    assert get_rates(2010).pa_taper_threshold == 100000.0


def test_additional_rate_introduced_2010_11():
    assert get_rates(2009).additional_rate is None
    assert get_rates(2010).additional_rate == 0.50


def test_additional_rate_cut_to_45_percent_2013_14():
    assert get_rates(2012).additional_rate == 0.50
    assert get_rates(2013).additional_rate == 0.45


def test_additional_rate_threshold_cut_2023_24():
    assert get_rates(2022).additional_rate_threshold == 150000.0
    assert get_rates(2023).additional_rate_threshold == 125140.0


def test_dividend_allowance_system_starts_2016_17():
    assert get_rates(2015).dividend_allowance is None
    assert get_rates(2016).dividend_allowance == 5000.0


def test_dividend_allowance_cuts_over_time():
    assert get_rates(2017).dividend_allowance == 5000.0
    assert get_rates(2018).dividend_allowance == 2000.0
    assert get_rates(2022).dividend_allowance == 2000.0
    assert get_rates(2023).dividend_allowance == 1000.0
    assert get_rates(2024).dividend_allowance == 500.0


def test_dividend_rates_rise_2022_23_and_2026_27():
    assert get_rates(2021).dividend_basic_rate == 0.075
    assert get_rates(2022).dividend_basic_rate == 0.0875  # Health & Social Care Levy
    assert get_rates(2025).dividend_basic_rate == 0.0875
    assert get_rates(2026).dividend_basic_rate == 0.1075  # Autumn Budget 2025
    assert get_rates(2026).dividend_higher_rate == 0.3575
    assert get_rates(2026).dividend_additional_rate == 0.3935  # unchanged


def test_personal_savings_allowance_starts_2016_17():
    assert get_rates(2015).personal_savings_allowance_basic == 0.0
    assert get_rates(2016).personal_savings_allowance_basic == 1000.0


def test_starting_rate_for_savings_becomes_zero_percent_2015_16():
    assert get_rates(2014).starting_rate_for_savings_pct == 0.10
    assert get_rates(2015).starting_rate_for_savings_pct == 0.0
    assert get_rates(2015).starting_rate_for_savings_band == 5000.0


def test_class2_nic_abolished_2024_25():
    assert get_rates(2023).ni_class2_weekly_rate is not None
    assert get_rates(2024).ni_class2_weekly_rate is None
    assert get_rates(2025).ni_class2_weekly_rate is None


def test_class4_main_rate_cut_9_to_6_percent_2024_25():
    """An announced 8% step for 2024-25 (Autumn Statement 2023) was
    superseded before taking effect by a deeper cut to 6% (Spring Budget
    2024) - so 2024-25 jumps straight from 9% to 6%, never passing through
    8%. (2006-07 to 2010-11 separately and genuinely had an 8% main rate -
    an unrelated, older historical rate, not to be confused with this.)"""
    assert get_rates(2023).ni_class4_main_rate == 0.09
    assert get_rates(2024).ni_class4_main_rate == 0.06


def test_mortgage_relief_phase_in_sequence():
    assert get_rates(2016).mortgage_relief_expense_fraction == 1.0
    assert get_rates(2017).mortgage_relief_expense_fraction == 0.75
    assert get_rates(2018).mortgage_relief_expense_fraction == 0.50
    assert get_rates(2019).mortgage_relief_expense_fraction == 0.25
    assert get_rates(2020).mortgage_relief_expense_fraction == 0.0
    assert get_rates(2026).mortgage_relief_expense_fraction == 0.0
