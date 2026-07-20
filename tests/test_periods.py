"""Tests for UK tax quarter calculations."""

from datetime import date

import pytest

from mtd_classifier.periods import (
    TaxQuarter,
    tax_year_for_date,
    quarter_for_date,
    parse_period,
    quarters_for_tax_year,
)


class TestTaxYearForDate:
    def test_date_in_april_after_6th(self):
        assert tax_year_for_date(date(2026, 4, 6)) == 2026

    def test_date_in_april_before_6th(self):
        assert tax_year_for_date(date(2026, 4, 5)) == 2025

    def test_date_in_january(self):
        assert tax_year_for_date(date(2027, 1, 1)) == 2026

    def test_date_in_march(self):
        assert tax_year_for_date(date(2027, 3, 31)) == 2026

    def test_date_in_december(self):
        assert tax_year_for_date(date(2026, 12, 31)) == 2026

    def test_date_in_july(self):
        assert tax_year_for_date(date(2026, 7, 15)) == 2026


class TestQuarterForDate:
    def test_q1_start(self):
        tq = quarter_for_date(date(2026, 4, 6))
        assert tq.quarter == 1
        assert tq.tax_year == 2026

    def test_q1_end(self):
        tq = quarter_for_date(date(2026, 7, 5))
        assert tq.quarter == 1
        assert tq.tax_year == 2026

    def test_q2_start(self):
        tq = quarter_for_date(date(2026, 7, 6))
        assert tq.quarter == 2
        assert tq.tax_year == 2026

    def test_q2_end(self):
        tq = quarter_for_date(date(2026, 10, 5))
        assert tq.quarter == 2
        assert tq.tax_year == 2026

    def test_q3_start(self):
        tq = quarter_for_date(date(2026, 10, 6))
        assert tq.quarter == 3
        assert tq.tax_year == 2026

    def test_q3_end(self):
        """Q3 ends 5 Jan of the next calendar year."""
        tq = quarter_for_date(date(2027, 1, 5))
        assert tq.quarter == 3
        assert tq.tax_year == 2026

    def test_q4_start(self):
        tq = quarter_for_date(date(2027, 1, 6))
        assert tq.quarter == 4
        assert tq.tax_year == 2026

    def test_q4_end(self):
        tq = quarter_for_date(date(2027, 4, 5))
        assert tq.quarter == 4
        assert tq.tax_year == 2026

    def test_year_boundary_december(self):
        """December falls in Q3 of the current tax year."""
        tq = quarter_for_date(date(2026, 12, 25))
        assert tq.quarter == 3
        assert tq.tax_year == 2026


class TestTaxQuarter:
    def test_q1_dates(self):
        tq = TaxQuarter(tax_year=2026, quarter=1)
        assert tq.start_date == date(2026, 4, 6)
        assert tq.end_date == date(2026, 7, 5)

    def test_q2_dates(self):
        tq = TaxQuarter(tax_year=2026, quarter=2)
        assert tq.start_date == date(2026, 7, 6)
        assert tq.end_date == date(2026, 10, 5)

    def test_q3_dates(self):
        tq = TaxQuarter(tax_year=2026, quarter=3)
        assert tq.start_date == date(2026, 10, 6)
        assert tq.end_date == date(2027, 1, 5)

    def test_q4_dates(self):
        tq = TaxQuarter(tax_year=2026, quarter=4)
        assert tq.start_date == date(2027, 1, 6)
        assert tq.end_date == date(2027, 4, 5)

    def test_contains(self):
        tq = TaxQuarter(tax_year=2026, quarter=1)
        assert tq.contains(date(2026, 4, 6)) is True
        assert tq.contains(date(2026, 5, 15)) is True
        assert tq.contains(date(2026, 7, 5)) is True
        assert tq.contains(date(2026, 7, 6)) is False
        assert tq.contains(date(2026, 4, 5)) is False

    def test_label(self):
        tq = TaxQuarter(tax_year=2026, quarter=1)
        assert tq.label == "Q1-2026"

    def test_str(self):
        tq = TaxQuarter(tax_year=2026, quarter=1)
        assert str(tq) == "Q1-2026"


class TestParsePeriod:
    def test_valid_period(self):
        tq = parse_period("Q1-2026")
        assert tq.quarter == 1
        assert tq.tax_year == 2026

    def test_lowercase(self):
        tq = parse_period("q3-2026")
        assert tq.quarter == 3

    def test_with_spaces(self):
        tq = parse_period("  Q2-2026  ")
        assert tq.quarter == 2

    def test_invalid_format(self):
        with pytest.raises(ValueError):
            parse_period("2026-Q1")

    def test_invalid_quarter(self):
        with pytest.raises(ValueError):
            parse_period("Q5-2026")

    def test_invalid_quarter_zero(self):
        with pytest.raises(ValueError):
            parse_period("Q0-2026")

    def test_no_dash(self):
        with pytest.raises(ValueError):
            parse_period("Q12026")


class TestQuartersForTaxYear:
    def test_returns_four_quarters(self):
        quarters = quarters_for_tax_year(2026)
        assert len(quarters) == 4
        assert quarters[0].quarter == 1
        assert quarters[3].quarter == 4
        assert all(q.tax_year == 2026 for q in quarters)

    def test_consecutive_dates(self):
        """Each quarter should start the day after the previous one ends."""
        quarters = quarters_for_tax_year(2026)
        for i in range(len(quarters) - 1):
            gap = (quarters[i + 1].start_date - quarters[i].end_date).days
            assert gap == 1, f"Gap between Q{i+1} and Q{i+2} is {gap} days"
