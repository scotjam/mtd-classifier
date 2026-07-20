"""User-guided column mapping for unrecognised tabular statement files.

When no importer recognises a csv/xls/xlsx file, the import flow falls back
to asking the user which columns hold which values (date, description,
amount or paid-out/paid-in pair, balance), showing real rows from their
file as examples. The resulting ``ColumnMapping`` is applied to parse the
file, and saved keyed by the file's header-row signature so the next file
with the same layout imports without asking again.

This deliberately replaces per-bank importers for the long tail of export
formats (Nationwide, Co-op, Metro Bank, ...) - the user maps their bank's
layout once, against their own real data, instead of this code guessing
from third-party format descriptions.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator

from mtd_classifier.config import APP_DIR
from mtd_classifier.importers.base import ParsedTransaction
from mtd_classifier.importers._spreadsheet_common import (
    MAX_HEADER_SCAN_ROWS,
    infer_signs_from_balance,
    parse_amount,
    parse_date,
)

TABULAR_SUFFIXES = (".csv", ".txt", ".xlsx", ".xlsm", ".xls")

MAPPINGS_PATH = APP_DIR / "column_mappings.json"


@dataclass
class ColumnMapping:
    """Which columns hold which values in one bank's export layout.

    Column indices are 0-based. Either ``amount_col`` (single signed
    column) or at least one of ``debit_col``/``credit_col`` (unsigned
    paid-out / paid-in pair) must be set.
    """

    date_col: int
    description_cols: list[int] = field(default_factory=list)
    amount_col: int | None = None
    debit_col: int | None = None
    credit_col: int | None = None
    balance_col: int | None = None
    # Lowercased, pipe-joined cells of the layout's header row - how a
    # saved mapping recognises future files with the same layout.
    header_signature: str = ""
    name: str = ""  # optional user-facing label, e.g. "Nationwide"

    def is_valid(self) -> bool:
        has_money = self.amount_col is not None or (
            self.debit_col is not None or self.credit_col is not None
        )
        return bool(self.description_cols) and has_money


def row_signature(row: list) -> str:
    """Canonical form of a header row for layout recognition."""
    return "|".join(str(c).strip().lower() for c in row if str(c or "").strip())


def read_tabular_rows(file_path: Path) -> list[list]:
    """Read raw cell rows from a csv/txt/xlsx/xls file, no interpretation."""
    suffix = file_path.suffix.lower()
    if suffix in (".csv", ".txt"):
        raw = file_path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")
        return [row for row in csv.reader(text.splitlines())]
    if suffix in (".xlsx", ".xlsm"):
        import openpyxl

        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        try:
            return [list(row) for row in wb.worksheets[0].iter_rows(values_only=True)]
        finally:
            wb.close()
    if suffix == ".xls":
        import xlrd

        book = xlrd.open_workbook(str(file_path))
        sheet = book.sheet_by_index(0)
        rows = []
        for r in range(sheet.nrows):
            row = []
            for c in range(sheet.ncols):
                cell = sheet.cell(r, c)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    row.append(xlrd.xldate.xldate_as_datetime(cell.value, book.datemode))
                else:
                    row.append(cell.value)
            rows.append(row)
        return rows
    raise ValueError(f"Not a tabular file: {file_path.name}")


def parse_with_mapping(
    rows: list[list], mapping: ColumnMapping
) -> Iterator[ParsedTransaction]:
    """Apply a column mapping to raw rows.

    Rows whose date column doesn't parse (metadata headers, summary lines,
    blank rows) are skipped - the same convention the generic spreadsheet
    importers use, so files with leading account-info rows just work.

    When the mapping has both a single amount column and a balance column,
    each amount's sign is verified against the running balance movement
    (see ``infer_signs_from_balance``) - exports that print debits unsigned
    come out correctly signed instead of all-positive.
    """
    txns = list(_parse_rows_with_mapping(rows, mapping))
    if mapping.amount_col is not None and mapping.balance_col is not None:
        txns = infer_signs_from_balance(txns)
    yield from txns


def _parse_rows_with_mapping(
    rows: list[list], mapping: ColumnMapping
) -> Iterator[ParsedTransaction]:
    def cell(row: list, i: int | None):
        return row[i] if i is not None and i < len(row) else None

    for row in rows:
        txn_date = parse_date(cell(row, mapping.date_col))
        if txn_date is None:
            continue
        parts = []
        for i in mapping.description_cols:
            value = cell(row, i)
            text = str(value).strip() if value is not None else ""
            if text:
                parts.append(text)
        description = " ".join(parts)
        if not description:
            continue

        if mapping.amount_col is not None:
            amount = parse_amount(cell(row, mapping.amount_col))
        else:
            debit = parse_amount(cell(row, mapping.debit_col))
            credit = parse_amount(cell(row, mapping.credit_col))
            if debit is None and credit is None:
                amount = None
            else:
                amount = (credit or 0.0) - abs(debit or 0.0)
        if amount is None:
            continue

        yield ParsedTransaction(
            date=txn_date,
            description=description,
            amount=round(amount, 2),
            balance=parse_amount(cell(row, mapping.balance_col)),
            raw_data=str([c for c in row if c is not None and str(c).strip()]),
        )


class MappedTabularImporter:
    """Importer protocol adapter for a user-supplied column mapping."""

    bank_name: str = "custom"
    generic: bool = True  # the user mapped columns; the bank is unknown

    def __init__(self, mapping: ColumnMapping, suffix: str) -> None:
        self.mapping = mapping
        self.format_name = suffix.lstrip(".").lower() or "csv"

    def can_handle(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in TABULAR_SUFFIXES

    def parse(self, file_path: Path) -> Iterator[ParsedTransaction]:
        yield from parse_with_mapping(read_tabular_rows(file_path), self.mapping)


# --- saved-mapping store ---


def load_saved_mappings(path: Path | None = None) -> list[ColumnMapping]:
    path = path or MAPPINGS_PATH
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    mappings = []
    for entry in data if isinstance(data, list) else []:
        try:
            mappings.append(ColumnMapping(**entry))
        except TypeError:
            continue  # written by a newer/older version - skip, don't crash
    return mappings


def save_mapping(mapping: ColumnMapping, path: Path | None = None) -> None:
    path = path or MAPPINGS_PATH
    mappings = [
        m for m in load_saved_mappings(path)
        # A re-mapped layout replaces the old mapping for that signature
        if not (m.header_signature and m.header_signature == mapping.header_signature)
    ]
    mappings.append(mapping)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(m) for m in mappings], indent=2), encoding="utf-8"
    )


def find_saved_mapping(
    rows: list[list], path: Path | None = None
) -> ColumnMapping | None:
    """Match a file against saved layouts by header-row signature.

    The signature row may sit anywhere in the first MAX_HEADER_SCAN_ROWS
    rows (files often carry account-info rows above the table). A match
    must also actually parse at least one transaction - a stale mapping
    that no longer fits the bank's current export is ignored rather than
    silently importing nothing.
    """
    signatures = {
        row_signature(row) for row in rows[:MAX_HEADER_SCAN_ROWS] if row_signature(row)
    }
    for mapping in load_saved_mappings(path):
        if not mapping.header_signature or mapping.header_signature not in signatures:
            continue
        if next(parse_with_mapping(rows, mapping), None) is not None:
            return mapping
    return None


def guess_header_signature(rows: list[list], mapping: ColumnMapping) -> str:
    """The signature of the row just above the first data row - i.e. the
    column-headers row of the table the mapping describes."""
    for i, row in enumerate(rows):
        if parse_date(row[mapping.date_col] if mapping.date_col < len(row) else None):
            return row_signature(rows[i - 1]) if i > 0 else ""
    return ""
