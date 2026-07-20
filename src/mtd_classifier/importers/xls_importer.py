"""Generic legacy Excel (.xls) statement importer.

Two distinct file contents wear the .xls extension in the wild:

- Real binary (BIFF) workbooks, read here with xlrd (the only maintained
  reader for that format).
- HTML tables saved with a .xls extension - a common trick used by several
  banks' "export as Excel" statement downloads (Santander among them) that
  Excel happily opens but is not a spreadsheet at all. Detected by content,
  not extension, and parsed with _html_table.py.

Both share layout-detection and row-parsing with the .xlsx importer via
_spreadsheet_common.py, so all three behave identically once rows are
extracted.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterator

from mtd_classifier.importers.base import ParsedTransaction
from mtd_classifier.importers import _spreadsheet_common as common
from mtd_classifier.importers import _html_table


class XlsImporter:
    """Importer for legacy .xls statement exports with recognisable headers."""

    bank_name: str = "spreadsheet"
    format_name: str = "xls"
    generic: bool = True  # cannot identify the bank; skip mismatch checks

    def can_handle(self, file_path: Path) -> bool:
        if file_path.suffix.lower() != ".xls":
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

        if _html_table.looks_like_html(file_path):
            all_rows = _html_table.extract_rows(file_path)
            rows = all_rows[header_row + 1:]
            yield from common.parse_rows(rows, cols)
            return

        import xlrd

        book = xlrd.open_workbook(file_path)
        sheet = book.sheet_by_index(0)
        rows = (
            self._row_values(sheet, book, r)
            for r in range(header_row + 1, sheet.nrows)
        )
        yield from common.parse_rows(rows, cols)

    def _find_layout(self, file_path: Path) -> tuple[int, dict] | None:
        if _html_table.looks_like_html(file_path):
            rows = _html_table.extract_rows(file_path)
            return common.find_layout(rows[: common.MAX_HEADER_SCAN_ROWS])

        import xlrd

        book = xlrd.open_workbook(file_path)
        sheet = book.sheet_by_index(0)
        scan = min(sheet.nrows, common.MAX_HEADER_SCAN_ROWS)
        rows = (self._row_values(sheet, book, r) for r in range(scan))
        return common.find_layout(rows)

    @staticmethod
    def _row_values(sheet, book, row_index: int) -> tuple:
        """Cell values for one row, with xlrd date cells converted to date."""
        import xlrd

        values = []
        for cell in sheet.row(row_index):
            if cell.ctype == xlrd.XL_CELL_DATE:
                dt = xlrd.xldate_as_datetime(cell.value, book.datemode)
                values.append(dt.date() if dt.time() == datetime.min.time() else dt)
            elif cell.ctype == xlrd.XL_CELL_EMPTY:
                values.append(None)
            else:
                values.append(cell.value)
        return tuple(values)
