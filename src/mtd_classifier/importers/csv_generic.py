"""Generic CSV statement importer.

The CSV counterpart of the generic xlsx/xls importers: many banks' CSV
exports (Lloyds, Nationwide, Metro, ...) use recognisable headers - a date
column, description-like columns and either a signed amount or a
debit/credit pair - so they can import with no user interaction. Layout
detection and row parsing are shared with the spreadsheet importers
(_spreadsheet_common), including balance-movement sign verification.

Files this can't recognise fall through to the user-guided column-mapping
fallback, exactly as before. Bank-specific CSV importers (First Direct)
run earlier in the registry, so this never shadows them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from mtd_classifier.importers.base import ParsedTransaction
from mtd_classifier.importers import _spreadsheet_common as common


class GenericCsvImporter:
    """Importer for CSV statement exports with recognisable headers."""

    bank_name: str = "spreadsheet"
    format_name: str = "csv"
    generic: bool = True  # cannot identify the bank; skip mismatch checks

    def can_handle(self, file_path: Path) -> bool:
        if file_path.suffix.lower() != ".csv":
            return False
        try:
            return self._find_layout(file_path) is not None
        except Exception:
            return False

    def parse(self, file_path: Path) -> Iterator[ParsedTransaction]:
        rows = self._read_rows(file_path)
        layout = common.find_layout(rows)
        if layout is None:
            return
        header_row, cols = layout
        yield from common.parse_rows(rows[header_row + 1:], cols)

    def _read_rows(self, file_path: Path) -> list[list]:
        from mtd_classifier.importers.column_mapping import read_tabular_rows

        return read_tabular_rows(file_path)

    def _find_layout(self, file_path: Path):
        return common.find_layout(self._read_rows(file_path))
