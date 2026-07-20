"""First Direct CSV statement importer."""

from __future__ import annotations

import csv
from datetime import datetime, date
from pathlib import Path
from typing import Iterator

from mtd_classifier.importers.base import ParsedTransaction, TransactionImporter


class FirstDirectCSVImporter:
    """Importer for First Direct CSV bank statements.

    Expected format:
        Date,Description,Amount,Balance
        15/04/2026,ACME CORP SALARY,2500.00,5000.00
        16/04/2026,TESCO STORES,-45.67,4954.33
    """

    bank_name: str = "firstdirect"
    format_name: str = "csv"

    def can_handle(self, file_path: Path) -> bool:
        if file_path.suffix.lower() != ".csv":
            return False
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                first_line = f.readline().strip()
                # First Direct CSVs have these headers
                return (
                    "," in first_line
                    and first_line.lower().startswith("date")
                    and "description" in first_line.lower()
                )
        except (OSError, UnicodeDecodeError):
            return False

    def parse(self, file_path: Path) -> Iterator[ParsedTransaction]:
        with open(file_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                txn_date = _parse_date(row["Date"].strip())
                description = row["Description"].strip()
                amount = _parse_amount(row["Amount"].strip())
                balance_str = row.get("Balance", "").strip()
                balance = float(balance_str) if balance_str else None

                yield ParsedTransaction(
                    date=txn_date,
                    description=description,
                    amount=amount,
                    balance=balance,
                    raw_data=str(row),
                )


def _parse_date(date_str: str) -> date:
    """Parse DD/MM/YYYY date format."""
    return datetime.strptime(date_str, "%d/%m/%Y").date()


def _parse_amount(amount_str: str) -> float:
    """Parse amount string, handling currency symbols and commas."""
    cleaned = amount_str.replace("£", "").replace(",", "").strip()
    return float(cleaned)
