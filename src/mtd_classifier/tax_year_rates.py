"""Historical UK Income Tax and NIC rates/thresholds, by tax year.

Each entry is deliberately explicit (no inheritance/defaulting between years)
so a given year's figures can be checked against HMRC's own archives without
tracing through a chain of "same as last year" logic. Sources are cited in
comments above each block of years. Where a feature didn't exist for a year
(e.g. the additional rate before 2010-11, the Dividend Allowance before
2016-17), the field is None rather than a zero, so the computation engine can
tell "not applicable" apart from "zero".

This is a simplified model - see disclaimer.py. It does not cover: Scottish
Income Tax rates (different since 2017-18), Welsh rates, marriage allowance,
blind person's allowance, student loan repayments, or NIC categories other
than Class 2/4 self-employment NIC.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaxYearRates:
    """All the figures needed to reproduce a Personal Tax Computation."""

    tax_year: int  # starting year, e.g. 2020 means the 2020-21 tax year

    # --- Personal Allowance ---
    personal_allowance: float
    pa_taper_threshold: float | None  # income above which PA tapers away; None = no taper that year
    pa_taper_divisor: float  # PA reduced by £1 per this many £ of income over the threshold

    # --- Non-savings, non-dividend income ---
    # higher_rate_threshold/additional_rate_threshold are GROSS income
    # figures exactly as HMRC publishes them (e.g. £50,270 / £125,140 for
    # 2024-25) - the computation engine subtracts the taxpayer's own
    # (already-tapered) Personal Allowance to get the taxable-income band
    # width, so a fully-tapered high earner correctly gets a WIDER basic-rate
    # band in taxable-income terms (there's no PA left to subtract) - this
    # is standard UK tax behaviour, not a special case.
    basic_rate: float
    higher_rate: float
    additional_rate: float | None  # None before the additional rate existed (pre 2010-11)
    higher_rate_threshold: float  # gross income at which the higher rate begins
    additional_rate_threshold: float | None  # gross income; None if no additional rate that year

    # --- Dividends ---
    # Pre-2016-17 dividends used a notional 10% tax credit system instead of
    # an allowance; dividend_allowance is None for those years and the
    # ordinary/upper/additional dividend rates apply to the GROSSED-UP
    # dividend (net x 100/90), with the notional credit covering basic rate.
    dividend_allowance: float | None
    dividend_basic_rate: float
    dividend_higher_rate: float
    dividend_additional_rate: float

    # --- Savings ---
    personal_savings_allowance_basic: float  # 0 before 2016-17 (no PSA existed)
    personal_savings_allowance_higher: float
    starting_rate_for_savings_band: float  # width of the special savings band
    starting_rate_for_savings_pct: float  # 0.0 from 2015-16; 0.10 "10p savings rate" before

    # --- Self-employment National Insurance ---
    ni_class2_weekly_rate: float | None  # None if abolished for this year
    ni_class2_small_profits_threshold: float | None
    ni_class4_lower_profits_limit: float
    ni_class4_upper_profits_limit: float
    ni_class4_main_rate: float
    ni_class4_upper_rate: float

    # --- Residential mortgage/finance cost relief phase-in (property income) ---
    # Fraction of finance costs still deductible as a normal expense; the
    # remainder gets the 20% basic-rate tax reduction instead. 1.0 before the
    # restriction began (2016-17 and earlier), stepping down to 0.0 by 2020-21.
    mortgage_relief_expense_fraction: float

    source: str = ""  # citation for this year's figures


# Sources (see also each entry's `source` field):
# - 2006-07 to 2015-16 income tax: House of Commons Library Research Papers
#   RP06-22 through RP15-17 (researchbriefings.files.parliament.uk).
# - 2016-17 to 2026-27 income tax/dividends/savings: gov.uk current + archived
#   ("Income Tax rates and Personal Allowances"), legislation.gov.uk Finance
#   Act 2023 s.6, Deloitte TaxScape/ICAEW/Accountancy Daily for the 2026-27
#   dividend rate rise (Autumn Budget 2025 / Finance Act 2026, Royal Assent
#   18 March 2026).
# - NIC: gov.uk self-employed NI rates page, HMRC NI Manual (NIM21001,
#   NIM24050, NIM70001), Autumn Statement 2023/Spring Budget 2024 policy
#   papers, legislation.gov.uk (NICs Act 2015), Hansard re-rating orders.
# - Mortgage relief phase-in: gov.uk "Restricting finance cost relief for
#   individual landlords", HMRC Property Income Manual PIM2054/2058/4460.
#
# Known simplifications (see also each field's docstring):
# - 2006-07/2007-08: the small 10% non-savings "starting rate" band
#   (~£2,150/£2,320) is folded into a flat 22% basic rate rather than
#   modelled as its own band - real liability for a basic-rate taxpayer in
#   those two years would be a little (~£150-200) lower than this shows.
# - Pre-2016-17 dividends: modelled as net-of-notional-10%-credit rates
#   (dividend_allowance=None) rather than a separate gross-up-plus-credit
#   code path - mathematically equivalent to the real system for a single
#   dividend-income figure.
# - 2022-23 Class 4 NIC: HMRC's own blended/annualised figures are used
#   (the Health & Social Care Levy and a mid-year LPL rise on 6 Jul 2022
#   make this a genuinely non-uniform year).
# - Age-related allowances, Scottish/Welsh rates, marriage allowance, blind
#   person's allowance and student loan repayments are out of scope.
TAX_YEAR_RATES: dict[int, TaxYearRates] = {
    2006: TaxYearRates(
        tax_year=2006,
        personal_allowance=5035.0, pa_taper_threshold=None, pa_taper_divisor=2.0,
        basic_rate=0.22, higher_rate=0.40, additional_rate=None,
        higher_rate_threshold=38335.0, additional_rate_threshold=None,
        dividend_allowance=None, dividend_basic_rate=0.0,
        dividend_higher_rate=0.225, dividend_additional_rate=0.225,
        personal_savings_allowance_basic=0.0, personal_savings_allowance_higher=0.0,
        starting_rate_for_savings_band=2150.0, starting_rate_for_savings_pct=0.10,
        ni_class2_weekly_rate=2.10, ni_class2_small_profits_threshold=4465.0,
        ni_class4_lower_profits_limit=5035.0, ni_class4_upper_profits_limit=33540.0,
        ni_class4_main_rate=0.08, ni_class4_upper_rate=0.01,
        mortgage_relief_expense_fraction=1.0,
        source="HoC Library RP06-22; basic rate approximates away the 10% starting-rate band",
    ),
    2007: TaxYearRates(
        tax_year=2007,
        personal_allowance=5225.0, pa_taper_threshold=None, pa_taper_divisor=2.0,
        basic_rate=0.22, higher_rate=0.40, additional_rate=None,
        higher_rate_threshold=39825.0, additional_rate_threshold=None,
        dividend_allowance=None, dividend_basic_rate=0.0,
        dividend_higher_rate=0.225, dividend_additional_rate=0.225,
        personal_savings_allowance_basic=0.0, personal_savings_allowance_higher=0.0,
        starting_rate_for_savings_band=2230.0, starting_rate_for_savings_pct=0.10,
        ni_class2_weekly_rate=2.20, ni_class2_small_profits_threshold=4635.0,
        ni_class4_lower_profits_limit=5225.0, ni_class4_upper_profits_limit=34840.0,
        ni_class4_main_rate=0.08, ni_class4_upper_rate=0.01,
        mortgage_relief_expense_fraction=1.0,
        source="HoC Library RP07-18; basic rate approximates away the 10% starting-rate band",
    ),
    2008: TaxYearRates(
        tax_year=2008,
        personal_allowance=5435.0, pa_taper_threshold=None, pa_taper_divisor=2.0,
        basic_rate=0.20, higher_rate=0.40, additional_rate=None,
        higher_rate_threshold=41435.0, additional_rate_threshold=None,
        dividend_allowance=None, dividend_basic_rate=0.0,
        dividend_higher_rate=0.225, dividend_additional_rate=0.225,
        personal_savings_allowance_basic=0.0, personal_savings_allowance_higher=0.0,
        starting_rate_for_savings_band=2320.0, starting_rate_for_savings_pct=0.10,
        ni_class2_weekly_rate=2.30, ni_class2_small_profits_threshold=4825.0,
        ni_class4_lower_profits_limit=5435.0, ni_class4_upper_profits_limit=40040.0,
        ni_class4_main_rate=0.08, ni_class4_upper_rate=0.01,
        mortgage_relief_expense_fraction=1.0,
        source="HoC Library RP08-20 - 10% non-savings starting rate abolished 6 Apr 2008, basic cut 22%->20%",
    ),
    2009: TaxYearRates(
        tax_year=2009,
        personal_allowance=6475.0, pa_taper_threshold=None, pa_taper_divisor=2.0,
        basic_rate=0.20, higher_rate=0.40, additional_rate=None,
        higher_rate_threshold=43875.0, additional_rate_threshold=None,
        dividend_allowance=None, dividend_basic_rate=0.0,
        dividend_higher_rate=0.225, dividend_additional_rate=0.225,
        personal_savings_allowance_basic=0.0, personal_savings_allowance_higher=0.0,
        starting_rate_for_savings_band=2320.0, starting_rate_for_savings_pct=0.10,
        ni_class2_weekly_rate=2.40, ni_class2_small_profits_threshold=5075.0,
        ni_class4_lower_profits_limit=5715.0, ni_class4_upper_profits_limit=43875.0,
        ni_class4_main_rate=0.08, ni_class4_upper_rate=0.01,
        mortgage_relief_expense_fraction=1.0,
        source="HoC Library RP09-14",
    ),
    2010: TaxYearRates(
        tax_year=2010,
        personal_allowance=6475.0, pa_taper_threshold=100000.0, pa_taper_divisor=2.0,
        basic_rate=0.20, higher_rate=0.40, additional_rate=0.50,
        higher_rate_threshold=43875.0, additional_rate_threshold=150000.0,
        dividend_allowance=None, dividend_basic_rate=0.0,
        dividend_higher_rate=0.225, dividend_additional_rate=0.325,
        personal_savings_allowance_basic=0.0, personal_savings_allowance_higher=0.0,
        starting_rate_for_savings_band=2440.0, starting_rate_for_savings_pct=0.10,
        ni_class2_weekly_rate=2.40, ni_class2_small_profits_threshold=5075.0,
        ni_class4_lower_profits_limit=5715.0, ni_class4_upper_profits_limit=43875.0,
        ni_class4_main_rate=0.08, ni_class4_upper_rate=0.01,
        mortgage_relief_expense_fraction=1.0,
        source="HoC Library RP10-40 - PA taper and new 50% additional rate/GBP150,000 threshold introduced",
    ),
    2011: TaxYearRates(
        tax_year=2011,
        personal_allowance=7475.0, pa_taper_threshold=100000.0, pa_taper_divisor=2.0,
        basic_rate=0.20, higher_rate=0.40, additional_rate=0.50,
        higher_rate_threshold=42475.0, additional_rate_threshold=150000.0,
        dividend_allowance=None, dividend_basic_rate=0.0,
        dividend_higher_rate=0.225, dividend_additional_rate=0.325,
        personal_savings_allowance_basic=0.0, personal_savings_allowance_higher=0.0,
        starting_rate_for_savings_band=2560.0, starting_rate_for_savings_pct=0.10,
        ni_class2_weekly_rate=2.50, ni_class2_small_profits_threshold=5315.0,
        ni_class4_lower_profits_limit=7225.0, ni_class4_upper_profits_limit=42475.0,
        ni_class4_main_rate=0.09, ni_class4_upper_rate=0.02,
        mortgage_relief_expense_fraction=1.0,
        source="HoC Library RP11-31; Class 4 main/additional rates rose to 9%/2% this year",
    ),
    2012: TaxYearRates(
        tax_year=2012,
        personal_allowance=8105.0, pa_taper_threshold=100000.0, pa_taper_divisor=2.0,
        basic_rate=0.20, higher_rate=0.40, additional_rate=0.50,
        higher_rate_threshold=42475.0, additional_rate_threshold=150000.0,
        dividend_allowance=None, dividend_basic_rate=0.0,
        dividend_higher_rate=0.225, dividend_additional_rate=0.325,
        personal_savings_allowance_basic=0.0, personal_savings_allowance_higher=0.0,
        starting_rate_for_savings_band=2710.0, starting_rate_for_savings_pct=0.10,
        ni_class2_weekly_rate=2.65, ni_class2_small_profits_threshold=5595.0,
        ni_class4_lower_profits_limit=7605.0, ni_class4_upper_profits_limit=42475.0,
        ni_class4_main_rate=0.09, ni_class4_upper_rate=0.02,
        mortgage_relief_expense_fraction=1.0,
        source="HoC Library RP12-38",
    ),
    2013: TaxYearRates(
        tax_year=2013,
        personal_allowance=9440.0, pa_taper_threshold=100000.0, pa_taper_divisor=2.0,
        basic_rate=0.20, higher_rate=0.40, additional_rate=0.45,
        higher_rate_threshold=41450.0, additional_rate_threshold=150000.0,
        dividend_allowance=None, dividend_basic_rate=0.0,
        dividend_higher_rate=0.225, dividend_additional_rate=0.275,
        personal_savings_allowance_basic=0.0, personal_savings_allowance_higher=0.0,
        starting_rate_for_savings_band=2790.0, starting_rate_for_savings_pct=0.10,
        ni_class2_weekly_rate=2.70, ni_class2_small_profits_threshold=5725.0,
        ni_class4_lower_profits_limit=7755.0, ni_class4_upper_profits_limit=41450.0,
        ni_class4_main_rate=0.09, ni_class4_upper_rate=0.02,
        mortgage_relief_expense_fraction=1.0,
        source="HoC Library RP13-25 - additional rate cut 50%->45% (net-of-credit dividend additional 32.5%->27.5%)",
    ),
    2014: TaxYearRates(
        tax_year=2014,
        personal_allowance=10000.0, pa_taper_threshold=100000.0, pa_taper_divisor=2.0,
        basic_rate=0.20, higher_rate=0.40, additional_rate=0.45,
        higher_rate_threshold=41865.0, additional_rate_threshold=150000.0,
        dividend_allowance=None, dividend_basic_rate=0.0,
        dividend_higher_rate=0.225, dividend_additional_rate=0.275,
        personal_savings_allowance_basic=0.0, personal_savings_allowance_higher=0.0,
        starting_rate_for_savings_band=2880.0, starting_rate_for_savings_pct=0.10,
        ni_class2_weekly_rate=2.75, ni_class2_small_profits_threshold=5885.0,
        ni_class4_lower_profits_limit=7956.0, ni_class4_upper_profits_limit=41865.0,
        ni_class4_main_rate=0.09, ni_class4_upper_rate=0.02,
        mortgage_relief_expense_fraction=1.0,
        source="HoC Library RP14-24",
    ),
    2015: TaxYearRates(
        tax_year=2015,
        personal_allowance=10600.0, pa_taper_threshold=100000.0, pa_taper_divisor=2.0,
        basic_rate=0.20, higher_rate=0.40, additional_rate=0.45,
        higher_rate_threshold=42385.0, additional_rate_threshold=150000.0,
        dividend_allowance=None, dividend_basic_rate=0.0,
        dividend_higher_rate=0.225, dividend_additional_rate=0.275,
        personal_savings_allowance_basic=0.0, personal_savings_allowance_higher=0.0,
        starting_rate_for_savings_band=5000.0, starting_rate_for_savings_pct=0.0,
        ni_class2_weekly_rate=2.80, ni_class2_small_profits_threshold=5965.0,
        ni_class4_lower_profits_limit=8060.0, ni_class4_upper_profits_limit=42385.0,
        ni_class4_main_rate=0.09, ni_class4_upper_rate=0.02,
        mortgage_relief_expense_fraction=1.0,
        source="HoC Library RP15-17 - new GBP5,000 @ 0% Starting Rate for Savings replaces the old 10% savings band; dividends still on the tax-credit system",
    ),
    2016: TaxYearRates(
        tax_year=2016,
        personal_allowance=11000.0, pa_taper_threshold=100000.0, pa_taper_divisor=2.0,
        basic_rate=0.20, higher_rate=0.40, additional_rate=0.45,
        higher_rate_threshold=43000.0, additional_rate_threshold=150000.0,
        dividend_allowance=5000.0, dividend_basic_rate=0.075,
        dividend_higher_rate=0.325, dividend_additional_rate=0.381,
        personal_savings_allowance_basic=1000.0, personal_savings_allowance_higher=500.0,
        starting_rate_for_savings_band=5000.0, starting_rate_for_savings_pct=0.0,
        ni_class2_weekly_rate=2.80, ni_class2_small_profits_threshold=5965.0,
        ni_class4_lower_profits_limit=8060.0, ni_class4_upper_profits_limit=43000.0,
        ni_class4_main_rate=0.09, ni_class4_upper_rate=0.02,
        mortgage_relief_expense_fraction=1.0,
        source="gov.uk - Dividend Allowance (GBP5,000) and Personal Savings Allowance both begin this year; mortgage restriction not yet started",
    ),
    2017: TaxYearRates(
        tax_year=2017,
        personal_allowance=11500.0, pa_taper_threshold=100000.0, pa_taper_divisor=2.0,
        basic_rate=0.20, higher_rate=0.40, additional_rate=0.45,
        higher_rate_threshold=45000.0, additional_rate_threshold=150000.0,
        dividend_allowance=5000.0, dividend_basic_rate=0.075,
        dividend_higher_rate=0.325, dividend_additional_rate=0.381,
        personal_savings_allowance_basic=1000.0, personal_savings_allowance_higher=500.0,
        starting_rate_for_savings_band=5000.0, starting_rate_for_savings_pct=0.0,
        ni_class2_weekly_rate=2.85, ni_class2_small_profits_threshold=6025.0,
        ni_class4_lower_profits_limit=8164.0, ni_class4_upper_profits_limit=45000.0,
        ni_class4_main_rate=0.09, ni_class4_upper_rate=0.02,
        mortgage_relief_expense_fraction=0.75,
        source="gov.uk - mortgage interest relief restriction phase-in begins: 75% expense / 25% as 20% credit",
    ),
    2018: TaxYearRates(
        tax_year=2018,
        personal_allowance=11850.0, pa_taper_threshold=100000.0, pa_taper_divisor=2.0,
        basic_rate=0.20, higher_rate=0.40, additional_rate=0.45,
        higher_rate_threshold=46350.0, additional_rate_threshold=150000.0,
        dividend_allowance=2000.0, dividend_basic_rate=0.075,
        dividend_higher_rate=0.325, dividend_additional_rate=0.381,
        personal_savings_allowance_basic=1000.0, personal_savings_allowance_higher=500.0,
        starting_rate_for_savings_band=5000.0, starting_rate_for_savings_pct=0.0,
        ni_class2_weekly_rate=2.95, ni_class2_small_profits_threshold=6205.0,
        ni_class4_lower_profits_limit=8424.0, ni_class4_upper_profits_limit=46350.0,
        ni_class4_main_rate=0.09, ni_class4_upper_rate=0.02,
        mortgage_relief_expense_fraction=0.50,
        source="gov.uk - Dividend Allowance cut GBP5,000->GBP2,000; mortgage relief 50% expense / 50% credit",
    ),
    2019: TaxYearRates(
        tax_year=2019,
        personal_allowance=12500.0, pa_taper_threshold=100000.0, pa_taper_divisor=2.0,
        basic_rate=0.20, higher_rate=0.40, additional_rate=0.45,
        higher_rate_threshold=50000.0, additional_rate_threshold=150000.0,
        dividend_allowance=2000.0, dividend_basic_rate=0.075,
        dividend_higher_rate=0.325, dividend_additional_rate=0.381,
        personal_savings_allowance_basic=1000.0, personal_savings_allowance_higher=500.0,
        starting_rate_for_savings_band=5000.0, starting_rate_for_savings_pct=0.0,
        ni_class2_weekly_rate=3.00, ni_class2_small_profits_threshold=6365.0,
        ni_class4_lower_profits_limit=8632.0, ni_class4_upper_profits_limit=50000.0,
        ni_class4_main_rate=0.09, ni_class4_upper_rate=0.02,
        mortgage_relief_expense_fraction=0.25,
        source="gov.uk - mortgage relief 25% expense / 75% credit",
    ),
    2020: TaxYearRates(
        tax_year=2020,
        personal_allowance=12500.0, pa_taper_threshold=100000.0, pa_taper_divisor=2.0,
        basic_rate=0.20, higher_rate=0.40, additional_rate=0.45,
        higher_rate_threshold=50000.0, additional_rate_threshold=150000.0,
        dividend_allowance=2000.0, dividend_basic_rate=0.075,
        dividend_higher_rate=0.325, dividend_additional_rate=0.381,
        personal_savings_allowance_basic=1000.0, personal_savings_allowance_higher=500.0,
        starting_rate_for_savings_band=5000.0, starting_rate_for_savings_pct=0.0,
        ni_class2_weekly_rate=3.05, ni_class2_small_profits_threshold=6475.0,
        ni_class4_lower_profits_limit=9500.0, ni_class4_upper_profits_limit=50000.0,
        ni_class4_main_rate=0.09, ni_class4_upper_rate=0.02,
        mortgage_relief_expense_fraction=0.0,
        source="gov.uk - mortgage interest relief restriction complete: 0% expense / 100% as 20% credit. Cross-checked against a real 2020-21 accountant's computation (PA/basic-band/finance-cost-relief figures match)",
    ),
    2021: TaxYearRates(
        tax_year=2021,
        personal_allowance=12570.0, pa_taper_threshold=100000.0, pa_taper_divisor=2.0,
        basic_rate=0.20, higher_rate=0.40, additional_rate=0.45,
        higher_rate_threshold=50270.0, additional_rate_threshold=150000.0,
        dividend_allowance=2000.0, dividend_basic_rate=0.075,
        dividend_higher_rate=0.325, dividend_additional_rate=0.381,
        personal_savings_allowance_basic=1000.0, personal_savings_allowance_higher=500.0,
        starting_rate_for_savings_band=5000.0, starting_rate_for_savings_pct=0.0,
        ni_class2_weekly_rate=3.05, ni_class2_small_profits_threshold=6515.0,
        ni_class4_lower_profits_limit=9568.0, ni_class4_upper_profits_limit=50270.0,
        ni_class4_main_rate=0.09, ni_class4_upper_rate=0.02,
        mortgage_relief_expense_fraction=0.0,
        source="gov.uk",
    ),
    2022: TaxYearRates(
        tax_year=2022,
        personal_allowance=12570.0, pa_taper_threshold=100000.0, pa_taper_divisor=2.0,
        basic_rate=0.20, higher_rate=0.40, additional_rate=0.45,
        higher_rate_threshold=50270.0, additional_rate_threshold=150000.0,
        dividend_allowance=2000.0, dividend_basic_rate=0.0875,
        dividend_higher_rate=0.3375, dividend_additional_rate=0.3935,
        personal_savings_allowance_basic=1000.0, personal_savings_allowance_higher=500.0,
        starting_rate_for_savings_band=5000.0, starting_rate_for_savings_pct=0.0,
        ni_class2_weekly_rate=3.15, ni_class2_small_profits_threshold=6725.0,
        ni_class4_lower_profits_limit=11908.0, ni_class4_upper_profits_limit=50270.0,
        ni_class4_main_rate=0.0973, ni_class4_upper_rate=0.0273,
        mortgage_relief_expense_fraction=0.0,
        source="gov.uk - dividend rates +1.25pp (Health & Social Care Levy); Class 4 rates/LPL are HMRC's own blended/annualised figures for the mid-year 6 Jul 2022 threshold change",
    ),
    2023: TaxYearRates(
        tax_year=2023,
        personal_allowance=12570.0, pa_taper_threshold=100000.0, pa_taper_divisor=2.0,
        basic_rate=0.20, higher_rate=0.40, additional_rate=0.45,
        higher_rate_threshold=50270.0, additional_rate_threshold=125140.0,
        dividend_allowance=1000.0, dividend_basic_rate=0.0875,
        dividend_higher_rate=0.3375, dividend_additional_rate=0.3935,
        personal_savings_allowance_basic=1000.0, personal_savings_allowance_higher=500.0,
        starting_rate_for_savings_band=5000.0, starting_rate_for_savings_pct=0.0,
        ni_class2_weekly_rate=3.45, ni_class2_small_profits_threshold=6725.0,
        ni_class4_lower_profits_limit=12570.0, ni_class4_upper_profits_limit=50270.0,
        ni_class4_main_rate=0.09, ni_class4_upper_rate=0.02,
        mortgage_relief_expense_fraction=0.0,
        source="gov.uk/Finance Act 2023 s.6 - additional-rate threshold cut GBP150,000->GBP125,140; Dividend Allowance cut GBP2,000->GBP1,000",
    ),
    2024: TaxYearRates(
        tax_year=2024,
        personal_allowance=12570.0, pa_taper_threshold=100000.0, pa_taper_divisor=2.0,
        basic_rate=0.20, higher_rate=0.40, additional_rate=0.45,
        higher_rate_threshold=50270.0, additional_rate_threshold=125140.0,
        dividend_allowance=500.0, dividend_basic_rate=0.0875,
        dividend_higher_rate=0.3375, dividend_additional_rate=0.3935,
        personal_savings_allowance_basic=1000.0, personal_savings_allowance_higher=500.0,
        starting_rate_for_savings_band=5000.0, starting_rate_for_savings_pct=0.0,
        ni_class2_weekly_rate=None, ni_class2_small_profits_threshold=6725.0,
        ni_class4_lower_profits_limit=12570.0, ni_class4_upper_profits_limit=50270.0,
        ni_class4_main_rate=0.06, ni_class4_upper_rate=0.02,
        mortgage_relief_expense_fraction=0.0,
        source="gov.uk/HMRC NIM70001 - Class 2 abolished as a mandatory charge above the Small Profits Threshold (voluntary GBP3.45/wk below it, not modelled); Class 4 main rate cut 9%->6% (an announced 8% step never took effect, superseded by Spring Budget 2024); Dividend Allowance cut to GBP500",
    ),
    2025: TaxYearRates(
        tax_year=2025,
        personal_allowance=12570.0, pa_taper_threshold=100000.0, pa_taper_divisor=2.0,
        basic_rate=0.20, higher_rate=0.40, additional_rate=0.45,
        higher_rate_threshold=50270.0, additional_rate_threshold=125140.0,
        dividend_allowance=500.0, dividend_basic_rate=0.0875,
        dividend_higher_rate=0.3375, dividend_additional_rate=0.3935,
        personal_savings_allowance_basic=1000.0, personal_savings_allowance_higher=500.0,
        starting_rate_for_savings_band=5000.0, starting_rate_for_savings_pct=0.0,
        ni_class2_weekly_rate=None, ni_class2_small_profits_threshold=6845.0,
        ni_class4_lower_profits_limit=12570.0, ni_class4_upper_profits_limit=50270.0,
        ni_class4_main_rate=0.06, ni_class4_upper_rate=0.02,
        mortgage_relief_expense_fraction=0.0,
        source="gov.uk - PA/thresholds frozen (Nov 2022 Autumn Statement)",
    ),
    2026: TaxYearRates(
        tax_year=2026,
        personal_allowance=12570.0, pa_taper_threshold=100000.0, pa_taper_divisor=2.0,
        basic_rate=0.20, higher_rate=0.40, additional_rate=0.45,
        higher_rate_threshold=50270.0, additional_rate_threshold=125140.0,
        dividend_allowance=500.0, dividend_basic_rate=0.1075,
        dividend_higher_rate=0.3575, dividend_additional_rate=0.3935,
        personal_savings_allowance_basic=1000.0, personal_savings_allowance_higher=500.0,
        starting_rate_for_savings_band=5000.0, starting_rate_for_savings_pct=0.0,
        ni_class2_weekly_rate=None, ni_class2_small_profits_threshold=7105.0,
        ni_class4_lower_profits_limit=12570.0, ni_class4_upper_profits_limit=50270.0,
        ni_class4_main_rate=0.06, ni_class4_upper_rate=0.02,
        mortgage_relief_expense_fraction=0.0,
        source="Autumn Budget 2025 / Finance Act 2026 (Royal Assent 18 Mar 2026) - dividend ordinary/upper rates raised +2pp to 10.75%/35.75% (additional rate unchanged at 39.35%); PA/thresholds freeze extended to 5 Apr 2031",
    ),
}


def get_rates(tax_year: int) -> TaxYearRates | None:
    """The rate set for a tax year, or None if not (yet) in the table."""
    return TAX_YEAR_RATES.get(tax_year)


def available_tax_years() -> list[int]:
    """Tax years currently in the table, newest first."""
    return sorted(TAX_YEAR_RATES.keys(), reverse=True)
