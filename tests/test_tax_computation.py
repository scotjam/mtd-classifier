"""Tests for the Personal Tax Computation engine, using synthetic (but
realistically-shaped) rate fixtures so the band-stacking mechanics can be
hand-verified independent of the real historical rates table.
"""

import pytest

from mtd_classifier.tax_year_rates import TaxYearRates
from mtd_classifier.services.tax_computation import compute_personal_tax


def _rates(**overrides) -> TaxYearRates:
    """A simplified, round-number rate set for easy hand verification -
    shaped like the modern (2016-17+) system unless overridden."""
    defaults = dict(
        tax_year=2099,
        personal_allowance=10000.0,
        pa_taper_threshold=100000.0,
        pa_taper_divisor=2.0,
        basic_rate=0.20,
        higher_rate=0.40,
        additional_rate=0.45,
        # Gross-income thresholds (as HMRC publishes them): PA(10000) + the
        # intended taxable-income band widths (50000, 150000) used below.
        higher_rate_threshold=60000.0,
        additional_rate_threshold=160000.0,
        dividend_allowance=2000.0,
        dividend_basic_rate=0.075,
        dividend_higher_rate=0.325,
        dividend_additional_rate=0.381,
        personal_savings_allowance_basic=1000.0,
        personal_savings_allowance_higher=500.0,
        starting_rate_for_savings_band=5000.0,
        starting_rate_for_savings_pct=0.0,
        ni_class2_weekly_rate=3.45,
        ni_class2_small_profits_threshold=6500.0,
        ni_class4_lower_profits_limit=12500.0,
        ni_class4_upper_profits_limit=50000.0,
        ni_class4_main_rate=0.09,
        ni_class4_upper_rate=0.02,
        mortgage_relief_expense_fraction=0.0,
    )
    defaults.update(overrides)
    return TaxYearRates(**defaults)


class TestBasicScenario:
    def test_self_employment_profit_with_nic(self):
        result = compute_personal_tax(_rates(), self_employment_profit=20000.0)
        assert result.personal_allowance == 10000.0
        assert result.taxable_income == 10000.0
        assert result.income_tax_liability == 2000.0  # 10000 @ 20%
        assert result.class2_nic == round(3.45 * 52, 2)
        assert result.class4_nic == round((20000 - 12500) * 0.09, 2)
        assert result.total_tax_and_nic == round(
            2000.0 + 3.45 * 52 + (20000 - 12500) * 0.09, 2
        )

    def test_no_nic_below_thresholds(self):
        result = compute_personal_tax(_rates(), self_employment_profit=5000.0)
        assert result.class2_nic == 0.0
        assert result.class4_nic == 0.0
        assert result.income_tax_liability == 0.0  # fully within PA


class TestPersonalAllowanceTaper:
    def test_partial_taper(self):
        result = compute_personal_tax(_rates(), self_employment_profit=120000.0)
        # (120000 - 100000) / 2 = 10000 reduction -> PA fully gone
        assert result.personal_allowance == 0.0
        assert result.taxable_income == 120000.0
        # With PA at 0, the basic-rate band widens to the full gross
        # threshold (60000, not 60000-10000=50000) - a fully-tapered
        # taxpayer has no PA left to subtract, per real HMRC behaviour.
        # 60000 @ 20% + 60000 @ 40% (up to 160000 additional threshold)
        assert result.gross_tax == round(60000 * 0.20 + 60000 * 0.40, 2)

    def test_no_taper_below_threshold(self):
        result = compute_personal_tax(_rates(), self_employment_profit=90000.0)
        assert result.personal_allowance == 10000.0


class TestAdditionalRate:
    def test_additional_rate_band(self):
        result = compute_personal_tax(_rates(), self_employment_profit=200000.0)
        # PA fully tapered to 0, so bands widen to the full gross thresholds:
        # 60000@20% + 100000@40% (60000 to 160000) + 40000@45% (above 160000)
        expected = 60000 * 0.20 + 100000 * 0.40 + 40000 * 0.45
        assert result.gross_tax == round(expected, 2)

    def test_no_additional_rate_year(self):
        rates = _rates(additional_rate=None, additional_rate_threshold=None)
        result = compute_personal_tax(rates, self_employment_profit=200000.0)
        # PA still tapers to 0 (taper mechanism independent of additional rate
        # existing); everything above the (now PA-widened) higher_rate_threshold
        # taxed at higher_rate: 60000@20% + 140000@40%
        expected = 60000 * 0.20 + 140000 * 0.40
        assert result.gross_tax == round(expected, 2)


class TestSavingsAndDividendStacking:
    def test_psa_and_dividend_allowance_stack_above_non_savings(self):
        result = compute_personal_tax(
            _rates(),
            employment_income=40000.0,
            interest_income=2000.0,
            dividend_income=3000.0,
        )
        assert result.personal_allowance == 10000.0
        # non-savings: 40000 - 10000 PA = 30000 @ 20% = 6000
        assert sum(b.tax for b in result.non_savings_bands) == 6000.0
        # savings: PSA(basic)=1000 free, remaining 1000 @ 20% = 200
        assert sum(b.tax for b in result.savings_bands) == 200.0
        # dividends: allowance 2000 free, remaining 1000 @ 7.5% = 75
        assert sum(b.tax for b in result.dividend_bands) == 75.0
        assert result.gross_tax == round(6000 + 200 + 75, 2)

    def test_psa_reduced_to_500_for_higher_rate_taxpayer(self):
        result = compute_personal_tax(
            _rates(), employment_income=55000.0, interest_income=2000.0,
        )
        # non-savings taxable = 45000 (all in basic band, since limit=50000)
        # so PSA should still be basic tier (1000) here - use a case that
        # actually crosses into higher rate first:
        result2 = compute_personal_tax(
            _rates(), employment_income=65000.0, interest_income=2000.0,
        )
        # non-savings taxable = 55000 -> position ends at 55000 > basic_limit(50000)
        # -> PSA = 500 (higher rate tier)
        savings_psa_amount = next(
            b.amount for b in result2.savings_bands
            if b.label == "Personal Savings Allowance"
        )
        assert savings_psa_amount == 500.0


class TestGiftAid:
    def test_gift_aid_widens_basic_rate_band(self):
        without = compute_personal_tax(_rates(), self_employment_profit=65000.0)
        with_gift_aid = compute_personal_tax(
            _rates(), self_employment_profit=65000.0, gift_aid_net_donations=800.0,
        )
        # 800 grossed up at 20% = 1000 extra at basic rate instead of higher
        # rate: saves (0.40-0.20) x 1000 = 200
        assert without.gift_aid_grossed_up == 0.0
        assert with_gift_aid.gift_aid_grossed_up == 1000.0
        assert round(without.gross_tax - with_gift_aid.gross_tax, 2) == 200.0


class TestMortgageInterestReliefPhaseIn:
    """A baseline of other income (employment) is included in each case so
    the Personal Allowance doesn't fully absorb the property profit itself -
    otherwise there'd be no tax liability left for the relief to offset,
    which is correct HMRC behaviour (relief can't create a refund) but would
    make these tests about PA absorption rather than the phase-in mechanic."""

    def test_full_credit_only_modern_years(self):
        result = compute_personal_tax(
            _rates(mortgage_relief_expense_fraction=0.0),
            employment_income=30000.0, property_profit=10000.0, finance_costs=4000.0,
        )
        assert result.finance_costs_expense_portion == 0.0
        assert result.property_profit == 10000.0  # nothing deducted as expense
        assert result.rate_restricted_relief == round(0.20 * 4000, 2)

    def test_full_expense_pre_restriction_years(self):
        rates = _rates(mortgage_relief_expense_fraction=1.0)
        result = compute_personal_tax(
            rates, employment_income=30000.0, property_profit=10000.0, finance_costs=4000.0,
        )
        assert result.finance_costs_expense_portion == 4000.0
        assert result.property_profit == 6000.0  # fully deducted as expense
        assert result.rate_restricted_relief == 0.0

    def test_fifty_fifty_transition_year(self):
        rates = _rates(mortgage_relief_expense_fraction=0.5)
        result = compute_personal_tax(
            rates, employment_income=30000.0, property_profit=10000.0, finance_costs=4000.0,
        )
        assert result.finance_costs_expense_portion == 2000.0
        assert result.property_profit == 8000.0
        assert result.rate_restricted_relief == round(0.20 * 2000, 2)

    def test_relief_capped_at_property_profit(self):
        rates = _rates(mortgage_relief_expense_fraction=0.0)
        result = compute_personal_tax(
            rates, employment_income=30000.0, property_profit=500.0, finance_costs=10000.0,
        )
        # Relief can't exceed 20% of the (much smaller) property profit
        assert result.rate_restricted_relief == round(0.20 * 500, 2)

    def test_relief_is_zero_when_no_tax_liability_to_offset(self):
        """Correct HMRC behaviour: relief can't create a refund. With no
        other income and the property profit fully covered by the Personal
        Allowance, there's nothing left to relieve this year."""
        result = compute_personal_tax(
            _rates(mortgage_relief_expense_fraction=0.0),
            property_profit=10000.0, finance_costs=4000.0,
        )
        assert result.gross_tax == 0.0
        assert result.rate_restricted_relief == 0.0


class TestNoIncome:
    def test_zero_income_is_all_zero(self):
        result = compute_personal_tax(_rates())
        assert result.total_income == 0.0
        assert result.income_tax_liability == 0.0
        assert result.total_tax_and_nic == 0.0
