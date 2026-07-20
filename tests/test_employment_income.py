"""Tests for confirmed employment income: repository, summary, CSV export,
and the payslip/P60 PDF candidate extractor.
"""

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mtd_classifier.models.base import Base
from mtd_classifier.models.transaction import RawTransaction, CategorisedTransaction
from mtd_classifier.storage.repository import Repository
from mtd_classifier.export.mtd_summary import summarise_quarter
from mtd_classifier.export.csv_exporter import export_to_csv
from mtd_classifier.periods import TaxQuarter


@pytest.fixture
def repo():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield Repository(session)
    session.close()


def _employment_txn(repo, amount, i, log_id=1):
    txn = repo.create_raw_transaction(
        import_log_id=log_id, bank="firstdirect", txn_date=date(2026, 4, 20),
        description="SALARY", amount=amount, dedup_hash=f"emp_{i}",
    )
    repo.set_categorisation(
        raw_transaction_id=txn.id, category="employment_income",
        confidence="manual", changed_by="test",
    )
    return txn


class TestRepository:
    def test_no_record_returns_none(self, repo):
        assert repo.get_employment_income(2026) is None

    def test_set_and_get(self, repo):
        record = repo.set_employment_income(2026, 45000.0, source="manual", note="P60")
        fetched = repo.get_employment_income(2026)
        assert fetched is not None
        assert fetched.id == record.id
        assert fetched.gross_amount == 45000.0
        assert fetched.source == "manual"
        assert fetched.note == "P60"

    def test_re_setting_overwrites_not_duplicates(self, repo):
        repo.set_employment_income(2026, 40000.0, source="manual")
        repo.set_employment_income(2026, 45000.0, source="payslip_pdf", note="corrected")
        repo.commit()
        assert repo.get_employment_income(2026).gross_amount == 45000.0
        assert repo.get_employment_income(2026).source == "payslip_pdf"

    def test_different_tax_years_independent(self, repo):
        repo.set_employment_income(2025, 40000.0, source="manual")
        repo.set_employment_income(2026, 45000.0, source="manual")
        assert repo.get_employment_income(2025).gross_amount == 40000.0
        assert repo.get_employment_income(2026).gross_amount == 45000.0


class TestSummaryConfirmation:
    def test_unconfirmed_when_no_record(self, repo):
        log = repo.create_import_log(
            filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=1
        )
        txn = _employment_txn(repo, 2000.0, 1, log.id)
        repo.commit()

        cat_txns = repo.get_categorised_transactions_in_period(date(2026, 4, 6), date(2026, 7, 5))
        tq = TaxQuarter(2026, 1)
        summary = summarise_quarter(tq, cat_txns, total_in_period=1, employment_income_record=None)

        ct = summary.category_totals["employment_income"]
        assert ct.unconfirmed is True
        assert ct.transaction_count == 1
        # Never counted in business totals regardless of confirmation state
        assert summary.total_income == 0.0

    def test_confirmed_shows_gross_not_bank_total(self, repo):
        log = repo.create_import_log(
            filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=1
        )
        _employment_txn(repo, 2000.0, 1, log.id)  # bank deposit: net pay
        record = repo.set_employment_income(2026, 45000.0, source="manual", note="P60 2026-27")
        repo.commit()

        cat_txns = repo.get_categorised_transactions_in_period(date(2026, 4, 6), date(2026, 7, 5))
        tq = TaxQuarter(2026, 1)
        summary = summarise_quarter(
            tq, cat_txns, total_in_period=1, employment_income_record=record
        )

        ct = summary.category_totals["employment_income"]
        assert ct.unconfirmed is False
        assert ct.total_income == 45000.0  # the CONFIRMED gross, not the £2000 bank deposit
        assert "manual" in ct.confirmation_note
        assert summary.total_income == 0.0  # still excluded from business totals

    def test_no_transactions_means_no_flag_either_way(self, repo):
        tq = TaxQuarter(2026, 1)
        summary = summarise_quarter(tq, [], total_in_period=0, employment_income_record=None)
        ct = summary.category_totals["employment_income"]
        assert ct.unconfirmed is False
        assert ct.transaction_count == 0


class TestCsvExportUnconfirmed:
    def test_unconfirmed_row_shows_placeholder(self, repo, tmp_path):
        log = repo.create_import_log(
            filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=1
        )
        _employment_txn(repo, 2000.0, 1, log.id)
        repo.commit()

        cat_txns = repo.get_categorised_transactions_in_period(date(2026, 4, 6), date(2026, 7, 5))
        tq = TaxQuarter(2026, 1)
        summary = summarise_quarter(tq, cat_txns, total_in_period=1, employment_income_record=None)

        out = tmp_path / "export.csv"
        export_to_csv(summary, cat_txns, out)
        lines = out.read_text().splitlines()
        summary_line = next(l for l in lines if l.startswith("Employment Income,"))
        assert "UNCONFIRMED" in summary_line
        # The bank deposit total must not appear as if it were the reportable figure
        assert "2000.00" not in summary_line
        # It's still fine (expected) for the raw transaction ledger below to
        # show the real deposit amount - only the summary total is guarded
        assert any("2000.00" in l for l in lines)

    def test_confirmed_row_shows_gross_and_note(self, repo, tmp_path):
        log = repo.create_import_log(
            filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=1
        )
        _employment_txn(repo, 2000.0, 1, log.id)
        record = repo.set_employment_income(2026, 45000.0, source="manual")
        repo.commit()

        cat_txns = repo.get_categorised_transactions_in_period(date(2026, 4, 6), date(2026, 7, 5))
        tq = TaxQuarter(2026, 1)
        summary = summarise_quarter(
            tq, cat_txns, total_in_period=1, employment_income_record=record
        )

        out = tmp_path / "export.csv"
        export_to_csv(summary, cat_txns, out)
        content = out.read_text()
        assert "45000.00" in content
        assert "Confirmed gross pay" in content


class TestPayslipExtractor:
    def _make_pdf(self, path, lines):
        def esc(s):
            return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

        content = "BT /F1 10 Tf 40 750 Td " + " ".join(
            f"({esc(line)}) Tj 0 -14 Td" for line in lines
        ) + " ET"
        content_bytes = content.encode("latin-1")

        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(content_bytes)).encode()
            + b" >>\nstream\n" + content_bytes + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
        out = bytearray(b"%PDF-1.4\n")
        offsets = []
        for i, obj in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
        xref_pos = len(out)
        out += f"xref\n0 {len(objects) + 1}\n".encode()
        out += b"0000000000 65535 f \n"
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode()
        out += (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode()
        path.write_bytes(bytes(out))
        return path

    def test_p60_style_total_pay_extracted(self, tmp_path):
        from mtd_classifier.services.payslip_extractor import extract_candidates

        pdf = self._make_pdf(tmp_path / "p60.pdf", [
            "P60 End of Year Certificate",
            "Total pay to date 45000.00",
            "Total tax to date 8500.00",
        ])
        candidates = extract_candidates(pdf)
        labels = {c.label: c.amount for c in candidates}
        assert labels.get("Total pay to date") == 45000.00
        # A P60 figure is the full year, used directly - never x12'd
        assert all(c.is_annual for c in candidates)

    def test_payslip_style_gross_pay_extracted(self, tmp_path):
        from mtd_classifier.services.payslip_extractor import extract_candidates

        pdf = self._make_pdf(tmp_path / "payslip.pdf", [
            "Payslip for April 2026",
            "Gross Pay 3200.00",
            "Gross Pay YTD 12800.00",
            "Net Pay 2540.55",
        ])
        candidates = extract_candidates(pdf)
        labels = {c.label: c.amount for c in candidates}
        assert labels.get("Gross pay") == 3200.00
        # A payslip's YTD figure is deliberately excluded - "confirm monthly
        # pay" means the period figure only, not a partial-year cumulative one
        assert "Gross YTD" not in labels
        # Net pay must never be offered as a candidate - that's the wrong figure
        assert 2540.55 not in [c.amount for c in candidates]
        # A payslip figure is one pay period - the caller multiplies by 12
        assert all(not c.is_annual for c in candidates)

    def test_document_type_detection(self, tmp_path):
        from mtd_classifier.services.payslip_extractor import detect_document_type

        p60 = self._make_pdf(tmp_path / "p60.pdf", ["P60 End of Year Certificate"])
        payslip = self._make_pdf(tmp_path / "payslip.pdf", ["Payslip for April 2026"])
        assert detect_document_type(p60) == "p60"
        assert detect_document_type(payslip) == "payslip"

    def test_no_candidates_on_irrelevant_pdf(self, tmp_path):
        from mtd_classifier.services.payslip_extractor import extract_candidates

        pdf = self._make_pdf(tmp_path / "letter.pdf", ["Dear Customer,", "Thank you."])
        assert extract_candidates(pdf) == []

    def test_missing_file_returns_empty_not_raises(self, tmp_path):
        from mtd_classifier.services.payslip_extractor import extract_candidates

        assert extract_candidates(tmp_path / "does_not_exist.pdf") == []
