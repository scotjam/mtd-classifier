"""Generic Excel (.xlsx/.xlsm) statement importer.

Banks export spreadsheets with varying layouts, so this importer looks for a
header row anywhere in the first 15 rows containing a date column, a
description-like column and either a single amount column or a Money Out /
Money In pair. It cannot tell which bank produced the file, so it is marked
``generic`` and exempt from bank-mismatch checks.

See xls_importer.py for the legacy binary (.xls) equivalent; both share
layout-detection and row-parsing logic in _spreadsheet_common.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from mtd_classifier.importers.base import ParsedTransaction
from mtd_classifier.importers import _spreadsheet_common as common


class XlsxImporter:
    """Importer for Excel statement exports with recognisable headers."""

    bank_name: str = "spreadsheet"
    format_name: str = "xlsx"
    generic: bool = True  # cannot identify the bank; skip mismatch checks

    def can_handle(self, file_path: Path) -> bool:
        if file_path.suffix.lower() not in (".xlsx", ".xlsm"):
            return False
        try:
            return self._find_layout(file_path) is not None
        except Exception:
            return False

    def parse(self, file_path: Path) -> Iterator[ParsedTransaction]:
        layout = self._find_layout(file_path)
        if layout is None:
            return
        header_row, cols = layout

        import openpyxl

        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        try:
            ws = wb.worksheets[0]
            rows = ws.iter_rows(min_row=header_row + 2, values_only=True)
            yield from common.parse_rows(rows, cols)
        finally:
            wb.close()

    def _find_layout(self, file_path: Path) -> tuple[int, dict] | None:
        import openpyxl

        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        try:
            ws = wb.worksheets[0]
            rows = ws.iter_rows(
                min_row=1, max_row=common.MAX_HEADER_SCAN_ROWS, values_only=True
            )
            return common.find_layout(rows)
        finally:
            wb.close()
