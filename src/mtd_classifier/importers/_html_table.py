"""Extract table rows from HTML-formatted bank statements.

Several banks (Santander among them) export statements as an HTML table
saved with a spreadsheet extension (.xls) rather than a real binary
workbook - Excel silently opens these correctly, which is why the trick
persists. This module reads such a file into the same kind of row tuples
the real .xlsx/.xls readers produce, so it can share layout-detection and
parsing logic with them (see _spreadsheet_common.py).
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from pathlib import Path

_CHARSET_RE = re.compile(rb"charset\s*=\s*[\"']?([\w-]+)", re.IGNORECASE)


def looks_like_html(file_path: Path, peek_bytes: int = 2048) -> bool:
    """True if the file's content is HTML regardless of its extension."""
    try:
        with open(file_path, "rb") as f:
            head = f.read(peek_bytes)
    except OSError:
        return False
    text = head.lstrip().lower()
    return text.startswith(b"<!doctype html") or text.startswith(b"<html") or b"<table" in text


def extract_rows(file_path: Path) -> list[tuple]:
    """Parse the first HTML table in the file into rows of cell text.

    Each row is a tuple of cell strings (empty cells become None), mirroring
    what openpyxl/xlrd row iteration yields, so callers can reuse the shared
    header-detection and row-parsing logic unchanged.
    """
    with open(file_path, "rb") as f:
        raw = f.read()

    match = _CHARSET_RE.search(raw[:2048])
    encodings = [match.group(1).decode("ascii")] if match else []
    encodings += ["cp1252", "utf-8"]

    text = None
    for enc in encodings:
        try:
            text = raw.decode(enc)
            break
        except (LookupError, UnicodeDecodeError):
            continue
    if text is None:
        text = raw.decode("latin-1", errors="replace")

    parser = _TableParser()
    parser.feed(text)
    return parser.rows


class _TableParser(HTMLParser):
    """Collects <tr>/<td> text content into row tuples, ignoring markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple] = []
        self._row: list | None = None
        self._cell_parts: list[str] | None = None
        self._in_cell = False
        self._done = False  # stop after the first table closes

    def handle_starttag(self, tag: str, attrs) -> None:
        if self._done:
            return
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._flush_cell()
            self._in_cell = True
            self._cell_parts = []

    def handle_startendtag(self, tag: str, attrs) -> None:
        # Self-closing tags like <td /> - still open and close a cell
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._done:
            return
        if tag in ("td", "th"):
            self._flush_cell()
            self._in_cell = False
        elif tag == "tr":
            self._flush_cell()
            if self._row is not None:
                self.rows.append(tuple(self._row))
            self._row = None
        elif tag == "table" and self.rows:
            self._done = True  # only the first table matters

    def handle_data(self, data: str) -> None:
        if self._in_cell and self._cell_parts is not None:
            self._cell_parts.append(data)

    def _flush_cell(self) -> None:
        if self._row is None:
            self._cell_parts = None
            return
        text = "".join(self._cell_parts or [])
        text = " ".join(text.split())  # collapse internal whitespace/newlines
        self._row.append(text if text else None)
        self._cell_parts = None
