"""Santander statement importers - TXT record format and midata CSV."""

from __future__ import annotations

import csv
import re
from datetime import datetime, date
from pathlib import Path
from typing import Iterator

from mtd_classifier.importers.base import ParsedTransaction, TransactionImporter


class SantanderTXTImporter:
    """Importer for Santander TXT record-based statements.

    Santander exports a TXT file with ISO-8859-1 (Latin1) encoding.
    Records are separated by blank lines with fields like:
        Date: 15/04/2026
        Description: ACME CORP
        Amount: -450.00 GBP
        Balance: 1234.56 GBP
    """

    bank_name: str = "santander"
    format_name: str = "txt"

    def can_handle(self, file_path: Path) -> bool:
        if file_path.suffix.lower() != ".txt":
            return False
        try:
            with open(file_path, "r", encoding="latin-1") as f:
                content = f.read(2000)
                # Santander TXT files contain these field labels
                return "Date:" in content and "Description:" in content and "Amount:" in content
        except OSError:
            return False

    def parse(self, file_path: Path) -> Iterator[ParsedTransaction]:
        with open(file_path, "r", encoding="latin-1") as f:
            content = f.read()

        # Replace non-breaking spaces with regular spaces
        content = content.replace("\xa0", " ")

        # Split into records by blank lines
        blocks = re.split(r"\n\s*\n", content)

        for block in blocks:
            block = block.strip()
            if not block:
                continue

            fields = _parse_santander_block(block)
            if not fields.get("date") or not fields.get("description") or not fields.get("amount"):
                continue

            txn_date = _parse_date(fields["date"])
            description = fields["description"].strip()
            amount = _parse_santander_amount(fields["amount"])
            balance = (
                _parse_santander_amount(fields["balance"])
                if fields.get("balance")
                else None
            )

            yield ParsedTransaction(
                date=txn_date,
                description=description,
                amount=amount,
                balance=balance,
                raw_data=block,
            )


class SantanderMidataImporter:
    """Importer for Santander midata semicolon-separated CSV.

    Expected format (semicolon-separated):
        Date;Description;Amount;Balance
        15/04/2026;ACME CORP;-450.00;1234.56
    """

    bank_name: str = "santander"
    format_name: str = "midata_csv"

    def can_handle(self, file_path: Path) -> bool:
        if file_path.suffix.lower() != ".csv":
            return False
        try:
            with open(file_path, "r", encoding="latin-1") as f:
                first_line = f.readline().strip()
                # Midata uses semicolons and typically starts with "Date"
                return ";" in first_line and "date" in first_line.lower()
        except OSError:
            return False

    def parse(self, file_path: Path) -> Iterator[ParsedTransaction]:
        with open(file_path, "r", encoding="latin-1") as f:
            content = f.read().replace("\xa0", " ")

        reader = csv.DictReader(content.splitlines(), delimiter=";")
        for row in reader:
            date_key = _find_key(row, "date")
            desc_key = _find_key(row, "description")
            amount_key = _find_key(row, "amount")
            balance_key = _find_key(row, "balance")

            if not date_key or not desc_key or not amount_key:
                continue

            txn_date = _parse_date(row[date_key].strip())
            description = row[desc_key].strip()
            amount = _parse_santander_amount(row[amount_key].strip())
            balance = (
                _parse_santander_amount(row[balance_key].strip())
                if balance_key and row.get(balance_key, "").strip()
                else None
            )

            yield ParsedTransaction(
                date=txn_date,
                description=description,
                amount=amount,
                balance=balance,
                raw_data=str(row),
            )


def _find_key(row: dict, name: str) -> str | None:
    """Find a key in a dict case-insensitively."""
    for k in row:
        if k.strip().lower() == name:
            return k
    return None


def _parse_santander_block(block: str) -> dict[str, str]:
    """Parse a Santander TXT record block into field dict."""
    fields: dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip().lower()] = value.strip()
    return fields


def _parse_date(date_str: str) -> date:
    """Parse DD/MM/YYYY date format."""
    return datetime.strptime(date_str.strip(), "%d/%m/%Y").date()


def _parse_santander_amount(amount_str: str) -> float:
    """Parse Santander amount, stripping currency codes and symbols."""
    cleaned = amount_str.replace("£", "").replace("GBP", "").replace(",", "").strip()
    return float(cleaned)
