"""CLI integration tests using Typer CliRunner."""

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mtd_classifier.cli import app
from mtd_classifier.models.base import Base
from mtd_classifier.models.category import TransactionCategory, categories_for_amount
from mtd_classifier.storage.database import reset_engine
from mtd_classifier.storage.repository import Repository

FIXTURES = Path(__file__).parent / "fixtures"
runner = CliRunner()


@pytest.fixture(autouse=True)
def mock_db(tmp_path):
    """Use in-memory database for all CLI tests, with user resolution
    skipped (these tests exercise commands, not user selection - see
    test_cli_users.py for that)."""
    import mtd_classifier.cli as cli_module

    db_path = tmp_path / "test.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with patch("mtd_classifier.cli.get_engine", return_value=engine), \
         patch("mtd_classifier.cli.init_db"), \
         patch("mtd_classifier.cli.get_session", return_value=Session()), \
         patch.object(cli_module, "_user_resolved", True):
        yield engine

    reset_engine()


class TestImportCommand:
    def test_import_first_direct(self):
        result = runner.invoke(app, [
            "import-file",
            str(FIXTURES / "first_direct_sample.csv"),
        ])
        assert result.exit_code == 0
        assert "Imported" in result.stdout
        assert "10 new transactions" in result.stdout

    def test_import_santander_txt(self):
        result = runner.invoke(app, [
            "import-file",
            str(FIXTURES / "santander_sample.txt"),
        ])
        assert result.exit_code == 0
        assert "Imported" in result.stdout
        assert "5 new transactions" in result.stdout

    def test_import_nonexistent_file(self):
        result = runner.invoke(app, [
            "import-file",
            "nonexistent.csv",
        ])
        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_import_dry_run(self):
        result = runner.invoke(app, [
            "import-file",
            str(FIXTURES / "first_direct_sample.csv"),
            "--dry-run",
        ])
        assert result.exit_code == 0
        assert "Parsed" in result.stdout

    def test_import_duplicate_file(self):
        # First import
        runner.invoke(app, [
            "import-file",
            str(FIXTURES / "first_direct_sample.csv"),
        ])
        # Second import of same file
        result = runner.invoke(app, [
            "import-file",
            str(FIXTURES / "first_direct_sample.csv"),
        ])
        assert result.exit_code == 0
        assert "already imported" in result.stdout

    def test_import_with_bank_hint(self):
        result = runner.invoke(app, [
            "import-file",
            str(FIXTURES / "santander_sample.txt"),
            "--bank", "santander",
        ])
        assert result.exit_code == 0
        assert "Imported" in result.stdout

    def test_import_unknown_format(self, tmp_path):
        f = tmp_path / "mystery.dat"
        f.write_text("random data")
        result = runner.invoke(app, [
            "import-file",
            str(f),
        ])
        assert result.exit_code == 1
        assert "Could not detect" in result.stdout


class TestSameDayDuplicates:
    HEADER = "Date,Description,Amount,Balance\n"
    COFFEE = "20/04/2026,COFFEE SHOP,-3.20,{balance}\n"

    def _write_csv(self, path, rows):
        path.write_text(self.HEADER + "".join(rows))
        return path

    def test_identical_same_day_transactions_both_imported(self, tmp_path):
        f = self._write_csv(tmp_path / "statement.csv", [
            self.COFFEE.format(balance="100.00"),
            self.COFFEE.format(balance="96.80"),
        ])
        result = runner.invoke(app, [
            "import-file", str(f), "--duplicates", "import",
        ])
        assert result.exit_code == 0
        assert "Imported 2 new transactions" in result.stdout

    def test_duplicate_prompt_shows_statement_extracts(self, tmp_path):
        f = self._write_csv(tmp_path / "statement.csv", [
            self.COFFEE.format(balance="100.00"),
            self.COFFEE.format(balance="96.80"),
        ])
        with patch("mtd_classifier.cli._prompt_duplicate_action", return_value="i"):
            result = runner.invoke(app, ["import-file", str(f)])
        assert result.exit_code == 0
        assert "Potential duplicate" in result.stdout
        assert "This file" in result.stdout
        assert "Already recorded" in result.stdout
        # The raw statement lines are shown for both occurrences
        assert result.stdout.count("COFFEE SHOP") >= 3  # header line + 2 extracts
        assert "Imported 2 new transactions" in result.stdout

    def test_duplicate_prompt_skip_answer_drops_transaction(self, tmp_path):
        f = self._write_csv(tmp_path / "statement.csv", [
            self.COFFEE.format(balance="100.00"),
            self.COFFEE.format(balance="96.80"),
        ])
        with patch("mtd_classifier.cli._prompt_duplicate_action", return_value="s"):
            result = runner.invoke(app, ["import-file", str(f)])
        assert result.exit_code == 0
        assert "Imported 1 new transactions" in result.stdout
        assert "1 duplicates skipped" in result.stdout

    def test_duplicates_skip_flag_never_prompts(self, tmp_path):
        f = self._write_csv(tmp_path / "statement.csv", [
            self.COFFEE.format(balance="100.00"),
            self.COFFEE.format(balance="96.80"),
        ])
        result = runner.invoke(app, [
            "import-file", str(f), "--duplicates", "skip",
        ])
        assert result.exit_code == 0
        assert "Imported 1 new transactions" in result.stdout

    def test_duplicates_invalid_flag(self, tmp_path):
        f = self._write_csv(tmp_path / "statement.csv", [
            self.COFFEE.format(balance="100.00"),
        ])
        result = runner.invoke(app, [
            "import-file", str(f), "--duplicates", "maybe",
        ])
        assert result.exit_code == 1

    def test_reimport_of_same_content_still_dedupes(self, tmp_path):
        f1 = self._write_csv(tmp_path / "statement1.csv", [
            self.COFFEE.format(balance="100.00"),
            self.COFFEE.format(balance="96.80"),
        ])
        runner.invoke(app, ["import-file", str(f1)])
        # Same rows in a byte-different file (extra row), so the file-level
        # hash doesn't short-circuit the transaction-level dedup
        f2 = self._write_csv(tmp_path / "statement2.csv", [
            self.COFFEE.format(balance="100.00"),
            self.COFFEE.format(balance="96.80"),
            "21/04/2026,SAINSBURYS,-12.00,84.80\n",
        ])
        result = runner.invoke(app, ["import-file", str(f2)])
        assert result.exit_code == 0
        assert "Imported 1 new transactions" in result.stdout
        assert "2 duplicates skipped" in result.stdout

    def test_overlapping_export_with_extra_occurrence(self, tmp_path):
        # Morning export contains one coffee
        f1 = self._write_csv(tmp_path / "morning.csv", [
            self.COFFEE.format(balance="100.00"),
        ])
        runner.invoke(app, ["import-file", str(f1)])
        # Full-day export contains both coffees: first dedupes, second
        # prompts (its twin came from the earlier file) and imports
        f2 = self._write_csv(tmp_path / "fullday.csv", [
            self.COFFEE.format(balance="100.00"),
            self.COFFEE.format(balance="96.80"),
        ])
        with patch("mtd_classifier.cli._prompt_duplicate_action", return_value="i"):
            result = runner.invoke(app, ["import-file", str(f2)])
        assert result.exit_code == 0
        assert "morning.csv" in result.stdout  # extract names the earlier file
        assert "Imported 1 new transactions" in result.stdout
        assert "1 duplicates skipped" in result.stdout


class TestRefundReview:
    def _seed(self, engine, rows):
        """Insert raw transactions directly; rows = (date, desc, amount, category|None)."""
        Session = sessionmaker(bind=engine)
        repo = Repository(Session())
        log = repo.create_import_log(
            filename="seed.csv", file_hash=f"seed_{len(rows)}", bank="firstdirect",
            fmt="csv", record_count=len(rows),
        )
        ids = []
        for i, (d, desc, amount, category) in enumerate(rows):
            txn = repo.create_raw_transaction(
                import_log_id=log.id,
                bank="firstdirect",
                txn_date=d,
                description=desc,
                amount=amount,
                dedup_hash=f"refund_seed_{len(rows)}_{i}",
            )
            if category:
                repo.set_categorisation(
                    raw_transaction_id=txn.id,
                    category=category,
                    confidence="manual",
                    changed_by="test",
                )
            ids.append(txn.id)
        repo.commit()
        return repo, ids

    def test_refund_candidates_shown(self, mock_db):
        self._seed(mock_db, [
            (date(2026, 5, 1), "PLUMBER J SMITH", -180.00, "property_repairs"),
            (date(2026, 5, 9), "PLUMBER J SMITH REFUND", 180.00, None),
        ])
        with patch("mtd_classifier.cli._prompt_action", return_value="q"):
            result = runner.invoke(app, ["review", "--period", "Q1-2026"])
        assert "Possible refund/reversal of" in result.stdout
        assert "Property Repairs" in result.stdout

    def test_refund_action_inherits_original_category(self, mock_db):
        repo, ids = self._seed(mock_db, [
            (date(2026, 5, 1), "PLUMBER J SMITH", -180.00, "property_repairs"),
            (date(2026, 5, 9), "PLUMBER J SMITH REFUND", 180.00, None),
        ])
        with patch("mtd_classifier.cli._prompt_action", side_effect=["r", "q"]):
            result = runner.invoke(app, ["review", "--period", "Q1-2026"])
        assert result.exit_code == 0
        assert "Marked as refund" in result.stdout
        cat = repo.get_categorisation(ids[1])
        assert cat is not None
        assert cat.category == "property_repairs"
        assert cat.confidence == "refund"

    def test_refund_of_uncategorised_original_prompts_for_both(self, mock_db):
        repo, ids = self._seed(mock_db, [
            (date(2026, 4, 20), "ACME CORP", 500.00, None),
            (date(2026, 4, 28), "ACME CORP DOUBLE PAYMENT RETURN", -500.00, None),
        ])
        # Review both: skip the first, mark second as refund, then pick
        # the category for both from the menu. The menu is filtered by the
        # ORIGINAL transaction's direction (+500 = income categories)
        cat_index = [
            c.value for c in categories_for_amount(500.0)
        ].index("self_employed_income")
        with patch("mtd_classifier.cli._prompt_action", side_effect=["s", "r"]), \
             patch("mtd_classifier.cli._prompt_category_choice", return_value=cat_index):
            result = runner.invoke(app, ["review", "--period", "Q1-2026"])
        assert result.exit_code == 0
        # Both the original and the refund get the chosen category
        original = repo.get_categorisation(ids[0])
        refund = repo.get_categorisation(ids[1])
        assert original is not None and original.category == "self_employed_income"
        assert refund is not None and refund.category == "self_employed_income"
        assert refund.confidence == "refund"


class TestSummaryCommand:
    def test_summary_empty(self):
        result = runner.invoke(app, [
            "summary",
            "--period", "Q1-2026",
        ])
        assert result.exit_code == 0

    def test_summary_with_data(self):
        # Import data first
        runner.invoke(app, [
            "import-file",
            str(FIXTURES / "first_direct_sample.csv"),
        ])
        result = runner.invoke(app, [
            "summary",
            "--period", "Q1-2026",
        ])
        assert result.exit_code == 0
        assert "Q1-2026" in result.stdout


class TestRulesCommand:
    def test_rules_list_empty(self):
        result = runner.invoke(app, ["rules", "list"])
        assert result.exit_code == 0
        assert "No rules" in result.stdout

    def test_rules_add(self):
        result = runner.invoke(app, [
            "rules", "add",
            "--rule-type", "exact",
            "--match-value", "test merchant",
            "--category", "personal",
        ])
        assert result.exit_code == 0
        assert "Created rule" in result.stdout

    def test_rules_add_invalid_category(self):
        result = runner.invoke(app, [
            "rules", "add",
            "--rule-type", "exact",
            "--match-value", "test",
            "--category", "invalid_category",
        ])
        assert result.exit_code == 1
        assert "Invalid category" in result.stdout

    def test_rules_add_and_list(self):
        runner.invoke(app, [
            "rules", "add",
            "--rule-type", "pattern",
            "--match-value", r"(?i)\btest\b",
            "--category", "se_office_costs",
        ])
        result = runner.invoke(app, ["rules", "list"])
        assert result.exit_code == 0
        assert "se_office_costs" in result.stdout

    def test_rules_delete(self):
        # Add a rule
        runner.invoke(app, [
            "rules", "add",
            "--rule-type", "exact",
            "--match-value", "to delete",
            "--category", "personal",
        ])
        # Delete it
        result = runner.invoke(app, ["rules", "delete", "1"])
        assert result.exit_code == 0
        assert "Deactivated" in result.stdout


class TestReviewCommand:
    def _import_fixture(self):
        """Import the first_direct fixture to seed the database."""
        runner.invoke(app, [
            "import-file",
            str(FIXTURES / "first_direct_sample.csv"),
        ])

    def test_review_no_transactions(self):
        result = runner.invoke(app, [
            "review",
            "--period", "Q2-2026",
        ])
        assert result.exit_code == 0
        assert "No transactions to review" in result.stdout

    def test_review_quit_immediately(self):
        self._import_fixture()
        with patch("mtd_classifier.cli._prompt_action", return_value="q"):
            result = runner.invoke(app, ["review", "--period", "Q1-2026"])
        assert result.exit_code == 0
        assert "Reviewed 0" in result.stdout

    def test_review_accept(self):
        self._import_fixture()
        with patch("mtd_classifier.cli._prompt_action", side_effect=["a", "q"]):
            result = runner.invoke(app, ["review", "--period", "Q1-2026"])
        assert result.exit_code == 0
        assert "Reviewed 1" in result.stdout

    def test_review_skip(self):
        self._import_fixture()
        with patch("mtd_classifier.cli._prompt_action", side_effect=["s", "q"]):
            result = runner.invoke(app, ["review", "--period", "Q1-2026"])
        assert result.exit_code == 0
        assert "Reviewed 0" in result.stdout

    def test_review_change(self):
        self._import_fixture()
        with patch("mtd_classifier.cli._prompt_action", side_effect=["c", "q"]), \
             patch("mtd_classifier.cli._prompt_category_choice", return_value=0):
            result = runner.invoke(app, ["review", "--period", "Q1-2026"])
        assert result.exit_code == 0
        assert "rule saved" in result.stdout

    def test_review_period_filter(self):
        self._import_fixture()
        # Q2-2026 starts July 6 - no fixture data there
        result = runner.invoke(app, [
            "review",
            "--period", "Q2-2026",
        ])
        assert result.exit_code == 0
        assert "No transactions to review" in result.stdout

        # Q1-2026 has fixture data
        with patch("mtd_classifier.cli._prompt_action", return_value="q"):
            result = runner.invoke(app, ["review", "--period", "Q1-2026"])
        assert result.exit_code == 0
        assert "10" in result.stdout

    def test_review_all_flag(self):
        self._import_fixture()
        # Accept one transaction first
        with patch("mtd_classifier.cli._prompt_action", side_effect=["a", "q"]):
            runner.invoke(app, ["review", "--period", "Q1-2026"])

        # Without --all: should show 9 uncategorised
        with patch("mtd_classifier.cli._prompt_action", return_value="q"):
            result = runner.invoke(app, ["review", "--period", "Q1-2026"])
        assert "9" in result.stdout

        # With --all: should show all 10 including the categorised one
        with patch("mtd_classifier.cli._prompt_action", return_value="q"):
            result = runner.invoke(app, [
                "review", "--period", "Q1-2026", "--all",
            ])
        assert "10" in result.stdout


class TestExportCommand:
    def _import_fixture(self):
        """Import the first_direct fixture to seed the database."""
        runner.invoke(app, [
            "import-file",
            str(FIXTURES / "first_direct_sample.csv"),
        ])

    def test_export_basic(self, tmp_path):
        self._import_fixture()
        out_file = tmp_path / "export.csv"
        result = runner.invoke(app, [
            "export",
            "--period", "Q1-2026",
            "--output", str(out_file),
        ], input="y\n")  # CSV-only seed data: confirm past the PDF cross-check
        assert result.exit_code == 0
        assert "Exported to" in result.stdout

    def test_export_empty_period(self, tmp_path):
        out_file = tmp_path / "empty_export.csv"
        result = runner.invoke(app, [
            "export",
            "--period", "Q2-2026",
            "--output", str(out_file),
        ])
        assert result.exit_code == 0

    def test_export_output_path(self, tmp_path):
        self._import_fixture()
        out_file = tmp_path / "output.csv"
        runner.invoke(app, [
            "export",
            "--period", "Q1-2026",
            "--output", str(out_file),
        ], input="y\n")  # CSV-only seed data: confirm past the PDF cross-check
        assert out_file.exists()


class TestEmploymentIncomeWarnings:
    def test_review_warns_when_choosing_employment_income(self, mock_db):
        Session = sessionmaker(bind=mock_db)
        repo = Repository(Session())
        log = repo.create_import_log(
            filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=1
        )
        repo.create_raw_transaction(
            import_log_id=log.id, bank="firstdirect", txn_date=date(2026, 4, 20),
            description="SALARY", amount=2000.0, dedup_hash="emp_cli_1",
        )
        repo.commit()

        cat_index = [c.value for c in categories_for_amount(2000.0)].index("employment_income")
        with patch("mtd_classifier.cli._prompt_action", side_effect=["c", "q"]), \
             patch("mtd_classifier.cli._prompt_category_choice", return_value=cat_index):
            result = runner.invoke(app, ["review", "--period", "Q1-2026"])
        assert result.exit_code == 0
        assert "BANK DEPOSIT amount" in result.stdout
        # normalise whitespace: rich wraps long lines at arbitrary points
        assert "mtd employment set-income" in " ".join(result.stdout.split())

    def test_review_no_warning_for_other_categories(self, mock_db):
        Session = sessionmaker(bind=mock_db)
        repo = Repository(Session())
        log = repo.create_import_log(
            filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=1
        )
        repo.create_raw_transaction(
            import_log_id=log.id, bank="firstdirect", txn_date=date(2026, 4, 20),
            description="RENT RECEIVED", amount=950.0, dedup_hash="rent_cli_1",
        )
        repo.commit()

        cat_index = [c.value for c in categories_for_amount(950.0)].index("rental_income")
        with patch("mtd_classifier.cli._prompt_action", side_effect=["c", "q"]), \
             patch("mtd_classifier.cli._prompt_category_choice", return_value=cat_index):
            result = runner.invoke(app, ["review", "--period", "Q1-2026"])
        assert result.exit_code == 0
        assert "BANK DEPOSIT amount" not in result.stdout


class TestEmploymentCommand:
    def test_show_when_unset(self):
        result = runner.invoke(app, ["employment", "show", "--tax-year", "2026"])
        assert result.exit_code == 0
        assert "No confirmed employment income" in result.stdout

    def test_set_income_manual_requires_confirmation(self):
        result = runner.invoke(
            app,
            ["employment", "set-income", "--tax-year", "2026", "--gross", "45000"],
            input="y\n",
        )
        assert result.exit_code == 0
        assert "Confirmed employment income" in result.stdout

        show = runner.invoke(app, ["employment", "show", "--tax-year", "2026"])
        assert "45,000.00" in show.stdout
        assert "manual" in show.stdout

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

    def test_set_income_from_payslip_multiplies_by_12(self, tmp_path):
        pdf = self._make_pdf(tmp_path / "payslip.pdf", [
            "Payslip for April 2026", "Gross Pay 3200.00",
        ])
        result = runner.invoke(
            app,
            ["employment", "set-income", "--tax-year", "2026", "--from-pdf", str(pdf)],
            input="1\ny\n",
        )
        assert result.exit_code == 0
        assert "Detected" in result.stdout and "payslip" in result.stdout
        assert "3200.00 x 12" in result.stdout or "3,200.00 x 12" in result.stdout

        show = runner.invoke(app, ["employment", "show", "--tax-year", "2026"])
        assert "38,400.00" in show.stdout  # 3200 x 12

    def test_set_income_from_p60_uses_full_year_unscaled(self, tmp_path):
        pdf = self._make_pdf(tmp_path / "p60.pdf", [
            "P60 End of Year Certificate", "Total pay to date 45000.00",
        ])
        result = runner.invoke(
            app,
            ["employment", "set-income", "--tax-year", "2026", "--from-pdf", str(pdf)],
            input="1\ny\n",
        )
        assert result.exit_code == 0
        assert "P60" in result.stdout

        show = runner.invoke(app, ["employment", "show", "--tax-year", "2026"])
        assert "45,000.00" in show.stdout

    def test_set_income_declined_confirmation_saves_nothing(self):
        result = runner.invoke(
            app,
            ["employment", "set-income", "--tax-year", "2026", "--gross", "45000"],
            input="n\n",
        )
        assert result.exit_code == 0
        assert "Cancelled" in result.stdout

        show = runner.invoke(app, ["employment", "show", "--tax-year", "2026"])
        assert "No confirmed employment income" in show.stdout

    def test_set_income_requires_gross_or_pdf(self):
        result = runner.invoke(app, ["employment", "set-income", "--tax-year", "2026"])
        assert result.exit_code == 1
        assert "Provide --gross or --from-pdf" in result.stdout

    def test_summary_shows_unconfirmed_employment_income(self, mock_db):
        Session = sessionmaker(bind=mock_db)
        repo = Repository(Session())
        log = repo.create_import_log(
            filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=1
        )
        txn = repo.create_raw_transaction(
            import_log_id=log.id, bank="firstdirect", txn_date=date(2026, 4, 20),
            description="SALARY", amount=2000.0, dedup_hash="emp_summary_1",
        )
        repo.set_categorisation(
            raw_transaction_id=txn.id, category="employment_income",
            confidence="manual", changed_by="test",
        )
        repo.commit()

        result = runner.invoke(app, ["summary", "--period", "Q1-2026"])
        assert result.exit_code == 0
        assert "UNCONFIRMED" in result.stdout
        assert "2000.00" not in result.stdout  # wrong figure never shown as a total


class TestBridgeCommand:
    CATEGORIES = {
        "ACME CORP SALARY": "self_employed_income",
        "TESCO STORES": "personal",
        "MORTGAGE PAYMENT": "mortgage_interest",
        "NETFLIX.COM": "personal",
        "TRANSFER TO SAVINGS": "transfer",
        "LETTING AGENTS LTD": "letting_agent_fees",
        "SAINSBURYS": "personal",
        "NATIONAL RAIL": "se_travel",
        "RENT RECEIVED FROM TENANT": "rental_income",
        "PLUMBER J SMITH": "property_repairs",
    }

    def _seed_categorised(self, engine):
        """Import the fixture and categorise every transaction."""
        runner.invoke(app, [
            "import-file",
            str(FIXTURES / "first_direct_sample.csv"),
        ])
        Session = sessionmaker(bind=engine)
        repo = Repository(Session())
        for txn in repo.get_transactions_in_period(
            date(2026, 4, 6), date(2026, 7, 5)
        ):
            repo.set_categorisation(
                raw_transaction_id=txn.id,
                category=self.CATEGORIES[txn.description],
                confidence="manual",
                changed_by="test",
            )
        repo.commit()

    def test_bridge_empty(self, tmp_path):
        result = runner.invoke(app, [
            "bridge", "--period", "Q1-2026", "--output-dir", str(tmp_path),
        ])
        assert result.exit_code == 0
        assert "nothing written" in result.stdout

    def test_bridge_writes_both_templates(self, mock_db, tmp_path):
        self._seed_categorised(mock_db)
        result = runner.invoke(app, [
            "bridge", "--period", "Q1-2026", "--output-dir", str(tmp_path),
        ], input="y\n")  # CSV-only seed data: confirm past the PDF cross-check
        assert result.exit_code == 0
        assert (tmp_path / "MTD_SA103_Q1-2026_cumulative.xlsx").exists()
        assert (tmp_path / "MTD_SA105_Q1-2026_cumulative.xlsx").exists()
        assert "Written" in result.stdout

    def test_bridge_single_form(self, mock_db, tmp_path):
        self._seed_categorised(mock_db)
        result = runner.invoke(app, [
            "bridge", "--period", "Q1-2026",
            "--output-dir", str(tmp_path),
            "--form", "sa105",
        ], input="y\n")  # CSV-only seed data: confirm past the PDF cross-check
        assert result.exit_code == 0
        assert (tmp_path / "MTD_SA105_Q1-2026_cumulative.xlsx").exists()
        assert not (tmp_path / "MTD_SA103_Q1-2026_cumulative.xlsx").exists()

    def test_bridge_invalid_form(self, tmp_path):
        result = runner.invoke(app, [
            "bridge", "--period", "Q1-2026",
            "--output-dir", str(tmp_path),
            "--form", "sa999",
        ])
        assert result.exit_code == 1

    def test_bridge_warns_on_uncategorised(self, tmp_path):
        # Import without categorising anything
        runner.invoke(app, [
            "import-file",
            str(FIXTURES / "first_direct_sample.csv"),
        ])
        result = runner.invoke(app, [
            "bridge", "--period", "Q1-2026", "--output-dir", str(tmp_path),
        ], input="y\n")  # CSV-only seed data: confirm past the PDF cross-check
        assert result.exit_code == 0
        assert "uncategorised" in result.stdout


class TestColumnMappingFallback:
    COOP_CSV = (
        "Transaction date,Account Number,Bank reference,Type of payment,"
        "Customer reference,Amount (GBP),Additional Info\n"
        "18/11/2023,12345678,REF001,Faster Payment,INVOICE 42,-120.50,ACME SUPPLIES\n"
        "20/11/2023,12345678,REF002,BACS Credit,CLIENT PAYMENT,850.00,BLOGGS LTD\n"
    )

    def test_unrecognised_csv_mapped_interactively(self, tmp_path, monkeypatch):
        from mtd_classifier.importers import column_mapping as cm
        monkeypatch.setattr(cm, "MAPPINGS_PATH", tmp_path / "mappings.json")

        f = tmp_path / "coop_export.csv"
        f.write_text(self.COOP_CSV, encoding="utf-8")

        # date col, description cols, signed amount col, no balance, confirm
        result = runner.invoke(
            app, ["import-file", str(f), "--account", "Co-op Business"],
            input="1\n4,5,7\n6\n\ny\n",
        )
        assert result.exit_code == 0, result.stdout
        assert "2 transactions parsed" in result.stdout
        assert "Imported 2 new transactions" in result.stdout
        assert (tmp_path / "mappings.json").exists()

    def test_declining_mapping_reports_unknown_format(self, tmp_path, monkeypatch):
        from mtd_classifier.importers import column_mapping as cm
        monkeypatch.setattr(cm, "MAPPINGS_PATH", tmp_path / "mappings.json")

        f = tmp_path / "coop_export.csv"
        f.write_text(self.COOP_CSV, encoding="utf-8")

        # Map columns but answer n at the confirmation
        result = runner.invoke(
            app, ["import-file", str(f), "--account", "Co-op Business"],
            input="1\n4,5,7\n6\n\nn\n",
        )
        assert result.exit_code == 1
        assert "Could not detect file format" in result.stdout
