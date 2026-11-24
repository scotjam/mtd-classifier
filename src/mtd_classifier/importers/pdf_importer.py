"""Heuristic PDF statement importer.

PDF statements are laid out for print, not for machines, so this importer
works from extracted text lines. A transaction starts on a line beginning
with a date, but its description and amount can wrap onto following lines -
and some banks (First Direct among them) only print the date once per day,
so a second same-day transaction may start with no date at all.

To handle this, lines are accumulated into a "pending" transaction until one
of them contains money figures, at which point the transaction closes.

Sign convention: many statements print every amount unsigned, distinguished
only by which column (paid out / paid in) they sit in - information lost
once the PDF is flattened to text. Amounts with no reliable sign of their
own (no leading minus, no DR marker) are buffered into a "group" alongside
any other sign-less amounts since the last confirmed balance; as soon as a
balance figure appears, the group's total is compared against the balance
movement, and if it matches, that movement's sign is applied to every
amount in the group. This also correctly splits multiple unsigned
transactions that share a single trailing balance. If the totals don't
match, the group falls back to the printed (positive) sign and the import
will need manual review.

The importer cannot tell which bank produced the file, so it is marked
``generic`` and exempt from bank-mismatch checks. Always check the imported
lines against the paper statement.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from mtd_classifier.importers.base import ParsedTransaction

# Dates like 15/04/2026, 15-04-2026, 15 Apr 2026, 15 Apr 26
DATE_RE = re.compile(
    r"^\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2} [A-Za-z]{3,9} \d{2,4})\b"
)
# Money like 1,234.56 or £1,234.56, optionally negative or marked DR/CR.
# (?!\d) after the decimals: without it, an exchange rate like "1.1573"
# would partial-match as "1.15". Plain digit runs ("1500.04") are accepted
# too - Monzo and Starling print large amounts without thousands separators.
MONEY_RE = re.compile(
    r"(?:£\s*)?(-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2})(?!\d)\s*(DR|CR)?", re.IGNORECASE
)

# "Balance brought forward", "Opening balance", "STATEMENT OPENING
# BALANCE", "BROUGHT FORWARD" - these state the running total at the start
# of a table, not a transaction. \s* (not \s+) between words: pdfplumber
# sometimes drops the space glyph between words entirely, e.g.
# "Balancebrought forward" or "Balancecarriedforward".
OPENING_TOTAL_RE = re.compile(
    r"^((balance\s*)?brought\s*forward|(statement\s*)?opening\s*balance|balance\s*b/f)\b",
    re.IGNORECASE,
)
# "Balance carried forward", "Closing balance", "STATEMENT CLOSING
# BALANCE", "CARRIED FORWARD" - the running total at the end of a table.
CLOSING_TOTAL_RE = re.compile(
    r"^((balance\s*)?carried\s*forward|(statement\s*)?closing\s*balance|balance\s*c/f)\b",
    re.IGNORECASE,
)
# Some statements print a payment-type code at the start of the description
# ("SO", "DD", "ATM", ...); "CR" is First Direct's code for an incoming
# credit and, like a trailing DR marker, is an explicit and reliable sign.
CREDIT_PREFIX_RE = re.compile(r"^CR\b", re.IGNORECASE)

# The statement's own printed period, e.g. "2 March to 1 April 2025" - the
# most conclusive source of what date range a file covers (the year is
# printed only once, on the end date).
PERIOD_RE = re.compile(r"(\d{1,2} [A-Za-z]{3,9}) to (\d{1,2} [A-Za-z]{3,9} \d{4})")
# Numeric variant, e.g. Starling's "Summary 01/04/2025 - 30/04/2025"
PERIOD_NUMERIC_RE = re.compile(r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})")
# Named-month range with both years: Barclays' "21 Nov 2017 - 20 Feb 2018",
# NatWest's "02 DEC 2023 to 03 JAN 2024", Halifax's "02 March 2023 to
# 31 March 2023", Lloyds' "01 Oct 2023 to 31 Oct 2023"
PERIOD_NAMED_RE = re.compile(
    r"(\d{1,2} [A-Za-z]{3,9} \d{4})\s*(?:-|to)\s*(\d{1,2} [A-Za-z]{3,9} \d{4})",
    re.IGNORECASE,
)

# Column-label synonyms for the money-columns header, matched on words
# normalised to letters only (so "In(£)", "£ Out", "Withdrawn(£)" work):
# Starling "IN OUT BALANCE", Barclays/Lloyds/Halifax "Money out/Money in",
# NatWest "Paid In(£) Withdrawn(£)", Nationwide "£ Out £ In £ Balance".
_OUT_LABELS = {"OUT", "WITHDRAWN", "MONEYOUT", "PAIDOUT", "DEBIT", "DEBITS"}
_IN_LABELS = {"IN", "PAIDIN", "MONEYIN", "CREDIT", "CREDITS", "DEPOSITS"}
_BALANCE_LABELS = {"BALANCE", "ACCOUNT"}

# A money amount as a single extracted word, e.g. "£1,234.56" or "£15.99".
# Starling prints large amounts without thousands separators ("£40000.00"),
# so plain digit runs are accepted too.
MONEY_WORD_RE = re.compile(r"^£?-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}$")
# How close (points) a right-aligned amount must sit to a column's right
# edge to belong to it - column gaps in real statements are ~50pt+.
COLUMN_ALIGN_TOLERANCE = 40.0

DATE_FORMATS = (
    "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y",
    "%d %b %Y", "%d %b %y", "%d %B %Y", "%d %B %y",
)

# A transaction description wraps at most a couple of lines in practice;
# beyond this, treat it as unrelated text (page footer, T&Cs) and drop it.
MAX_CONTINUATION_LINES = 4


class PdfImporter:
    """Importer for text-based PDF bank statements (not scanned images)."""

    bank_name: str = "pdf"
    format_name: str = "pdf"
    generic: bool = True  # cannot identify the bank; skip mismatch checks

    def can_handle(self, file_path: Path) -> bool:
        if file_path.suffix.lower() != ".pdf":
            return False
        try:
            return any(True for _ in self.parse(file_path))
        except Exception:
            return False

    def parse(self, file_path: Path) -> Iterator[ParsedTransaction]:
        import pdfplumber

        with pdfplumber.open(file_path) as pdf:
            pages = [
                (page.extract_text() or "", page.extract_words())
                for page in pdf.pages
            ]
        # The printed period also resolves yearless dates ("04 Dec") on
        # statements that span a year boundary.
        period = _find_period_in_texts(text for text, _ in pages)
        if period is None:
            # No period phrase (Nationwide prints none): anchor yearless
            # dates to the latest full date in the document (the statement
            # date) - transactions can only fall in the year up to it.
            period = _year_window_from_latest_date(text for text, _ in pages)

        column_txns: list[ParsedTransaction] = []
        any_columns = False
        last_date: date | None = None
        for _, words in pages:
            # Statements with separate money-in / money-out columns
            # (Starling, Barclays) lose the distinction when flattened to
            # text - but word x-positions preserve it exactly, so
            # column-aware parsing takes precedence when a page has a
            # money-columns header.
            parsed = _parse_words_by_columns(words, period, last_date)
            if parsed is not None:
                any_columns = True
                page_txns, last_date = parsed
                column_txns.extend(page_txns)
        if any_columns:
            yield from column_txns
        else:
            lines = [line for text, _ in pages for line in text.splitlines()]
            txns = list(_parse_lines(lines))
            if any(t.ambiguous_sign for t in txns):
                # The line parser resolves signs by forward balance deltas;
                # statements printed newest-first (Monzo) defeat that, but
                # the order-agnostic inference can still prove them.
                from mtd_classifier.importers._spreadsheet_common import (
                    infer_signs_from_balance,
                )
                txns = infer_signs_from_balance(txns)
            yield from txns

    def statement_period(self, file_path: Path) -> tuple[date, date] | None:
        """The statement's own printed period, e.g. "2 March to 1 April
        2025" - conclusive proof of what date range this file covers,
        rather than inferring it from which transaction dates happened to
        parse (a day with zero transactions would otherwise look uncovered).
        """
        import pdfplumber

        with pdfplumber.open(file_path) as pdf:
            return _find_period_in_texts(
                page.extract_text() or "" for page in pdf.pages
            )


def _find_period_in_texts(texts) -> "tuple[date, date] | None":
    """The statement's printed period, from any of the known phrasings."""
    for text in texts:
        for line in text.splitlines():
            m = PERIOD_NAMED_RE.search(line)
            if m:
                start, end = _parse_date(m.group(1)), _parse_date(m.group(2))
                if start is not None and end is not None and start <= end:
                    return (start, end)
            m = PERIOD_RE.search(line)
            if m:
                period = _parse_period(m.group(1), m.group(2))
                if period is not None:
                    return period
            m = PERIOD_NUMERIC_RE.search(line)
            if m:
                start, end = _parse_date(m.group(1)), _parse_date(m.group(2))
                if start is not None and end is not None and start <= end:
                    return (start, end)
    return None


_FULL_DATE_RE = re.compile(r"\b(\d{1,2} [A-Za-z]{3,9} \d{4}|\d{2}/\d{2}/\d{4})\b")


def _year_window_from_latest_date(texts) -> "tuple[date, date] | None":
    """A pseudo-period ending at the latest full date printed anywhere in
    the document, for resolving yearless transaction dates only - it is
    NOT the statement's coverage period. A statement is generated at its
    period end, so its transactions fall within the year before it."""
    from datetime import timedelta

    latest = None
    for text in texts:
        for m in _FULL_DATE_RE.finditer(text):
            d = _parse_date(m.group(1))
            if d is not None and (latest is None or d > latest):
                latest = d
    if latest is None:
        return None
    return (latest - timedelta(days=366), latest)


def _parse_row_date(line_words: list, period) -> "tuple[date | None, int]":
    """Parse a transaction date from a row's leading word(s).

    Returns (date, words consumed). Handles one-word dates ("01/04/2025"),
    two-word dates ("04 Dec"/"4 Dec 2017"), and yearless dates resolved
    against the statement period (Barclays prints "04 Dec" on a statement
    spanning a year boundary).
    """
    first = line_words[0]["text"]
    d = _parse_date(first)
    if d is not None:
        return d, 1
    if len(line_words) >= 3:
        d = _parse_date(f"{first} {line_words[1]['text']} {line_words[2]['text']}")
        if d is not None:
            return d, 3
    if len(line_words) >= 2:
        two = f"{first} {line_words[1]['text']}"
        d = _parse_date(two)
        if d is not None:
            return d, 2
        if period is not None:
            for year in {period[0].year, period[1].year}:
                d = _parse_date(f"{two} {year}")
                if d is not None and period[0] <= d <= period[1]:
                    return d, 2
    return None, 0


def _parse_words_by_columns(
    words, period, last_date: "date | None" = None,
) -> "tuple[list[ParsedTransaction], date | None] | None":
    """Parse one page's words using money-column x-positions, or None if
    the page has no recognisable money-columns header.

    ``last_date`` is the date in force at the end of the previous page and
    the return value carries it on: banks that print a day's date only once
    (First Direct) can continue that day at the top of the next page with
    an undated row, which would otherwise be silently dropped.

    Handles both header shapes seen in the wild: Starling's "IN OUT
    ACCOUNT BALANCE" and Barclays' "Money out Money in Balance" (either
    column order). Each amount word is assigned to the column whose right
    edge it aligns with, which determines its sign directly - no
    balance-movement inference needed.
    """
    if not words:
        return None

    # Group words into visual lines by their vertical position
    lines: dict[float, list[dict]] = {}
    for w in words:
        key = round(w["top"] / 3) * 3.0
        lines.setdefault(key, []).append(w)
    ordered_lines = [sorted(ws, key=lambda w: w["x0"]) for _, ws in sorted(lines.items())]

    # Find the column header: a line with DATE plus money-in and money-out
    # column labels (any of the known synonyms, either order), and a
    # BALANCE (or stacked ACCOUNT/BALANCE) column to their right.
    def norm(text: str) -> str:
        return re.sub(r"[^A-Za-z]", "", text).upper()

    header = None
    for line_words in ordered_lines:
        texts = [norm(w["text"]) for w in line_words]
        if "DATE" not in texts:
            continue
        in_w = next((w for w, t in zip(line_words, texts) if t in _IN_LABELS), None)
        out_w = next((w for w, t in zip(line_words, texts) if t in _OUT_LABELS), None)
        if in_w is None or out_w is None:
            continue
        rightmost = max(in_w["x1"], out_w["x1"])
        right = [w for w, t in zip(line_words, texts)
                 if t in _BALANCE_LABELS and w["x0"] > rightmost]
        if right:
            header = {
                "top": in_w["top"], "in_x1": in_w["x1"],
                "out_x1": out_w["x1"], "balance_x1": right[0]["x1"],
            }
            break
    if header is None:
        return None

    columns = [
        (header["in_x1"], 1), (header["out_x1"], -1), (header["balance_x1"], 0),
    ]

    txns = []
    # Description-only rows (no figures) waiting for the row that carries
    # the amount. First Direct wraps every transaction this way - the
    # dated first line holds the type and payee ("02 Mar 26 SO ACME CHARITY
    # TRUST"), the undated second the reference and amount ("DONATION
    # 50.00") - and dropping the first line loses the payee.
    pending_desc: list[str] = []
    pending_raw: list[str] = []
    for line_words in ordered_lines:
        if line_words[0]["top"] <= header["top"]:
            continue
        txn_date, consumed = _parse_row_date(line_words, period)
        rest = line_words[consumed:]
        if txn_date is not None and not rest:
            continue  # a date-only sub-line (e.g. Barclays' purchase date)
        if consumed:
            # A dated row starts a new transaction: text left pending from
            # earlier rows (e.g. Revolut's "To: 22782735" metadata sub-row
            # after a transaction) doesn't belong to it.
            pending_desc, pending_raw = [], []
        if txn_date is None:
            # Consecutive transactions can share one printed date
            # (Barclays groups several rows under a posting date).
            txn_date = last_date
        if txn_date is None:
            continue

        amounts_seen: dict[int, float] = {}  # column sign -> value
        balance = None
        desc_parts = []
        for i, w in enumerate(rest):
            if MONEY_WORD_RE.match(w["text"]):
                distance, sign = min(
                    ((abs(w["x1"] - x1), s) for x1, s in columns), key=lambda t: t[0]
                )
                if distance > COLUMN_ALIGN_TOLERANCE:
                    desc_parts.append(w["text"])  # money-like text outside the table
                elif sign == 0:
                    balance = float(w["text"].lstrip("£").replace(",", ""))
                    # TSB marks overdrawn balances with an OD/O/D suffix
                    nxt = rest[i + 1]["text"].upper().strip() if i + 1 < len(rest) else ""
                    if nxt in ("OD", "O/D"):
                        balance = -balance
                else:
                    amounts_seen[sign] = float(w["text"].lstrip("£").replace(",", ""))
            elif w["text"].upper().strip() in ("OD", "O/D") and balance is not None:
                continue  # overdraft marker, not description text
            else:
                desc_parts.append(w["text"])

        description = " ".join(desc_parts).strip()
        if consumed and (description or amounts_seen):
            last_date = txn_date
        row_raw = " ".join(w["text"] for w in line_words)
        if not amounts_seen and balance is None:
            # No figures at all: the first line(s) of a wrapped transaction
            if description and not (OPENING_TOTAL_RE.match(description)
                                    or CLOSING_TOTAL_RE.match(description)):
                pending_desc.append(description)
                pending_raw.append(row_raw)
            continue
        lead_desc, lead_raw = pending_desc, pending_raw
        pending_desc, pending_raw = [], []
        # A row with figures in BOTH money columns is a summary (Halifax's
        # STATEMENT CLOSING BALANCE prints the period's total out AND in),
        # and running-total rows are not transactions.
        if len(amounts_seen) != 1 or not description:
            continue
        if OPENING_TOTAL_RE.match(description) or CLOSING_TOTAL_RE.match(description):
            continue
        sign, value = next(iter(amounts_seen.items()))
        txns.append(ParsedTransaction(
            date=txn_date,
            description=" ".join(lead_desc + [description]),
            amount=round(sign * value, 2),
            balance=balance,
            raw_data="\n".join(lead_raw + [row_raw]),
        ))
    return txns, last_date


def _parse_lines(lines: list[str]) -> Iterator[ParsedTransaction]:
    previous_balance: float | None = None
    # Signed total of explicit-sign items seen since previous_balance was
    # last confirmed, for those printed with no balance of their own -
    # needed so a later group's delta resolution isn't thrown off by them.
    known_offset = 0.0
    in_table = True
    last_date: date | None = None
    pending: dict | None = None
    group: list[dict] = []

    for line in lines:
        date_match = DATE_RE.match(line)
        if date_match:
            txn_date = _parse_date(date_match.group(1))
            if txn_date is None:
                continue
            pending = {"date": txn_date, "desc_parts": [], "raw_lines": []}
            last_date = txn_date
            rest = line[date_match.end():]
        else:
            if pending is None:
                if not in_table or last_date is None or not group:
                    continue
                # Same-day continuation: this bank only prints the date once.
                # Only opened while a group is still waiting on a balance -
                # once a day's transactions are fully resolved, unheralded
                # text is page noise (T&Cs, footers), not another transaction.
                pending = {"date": last_date, "desc_parts": [], "raw_lines": []}
            rest = line

        # "EUR 59.96 @ 1.1573" - a foreign-currency amount immediately
        # followed by "@ <rate>" is exchange-rate detail, not the
        # transaction's amount; the converted GBP figure is on another line.
        monies = [
            m for m in MONEY_RE.finditer(rest)
            if not re.match(r"\s*@", rest[m.end():])
        ]
        if not monies:
            text = rest.strip()
            if text:
                pending["desc_parts"].append(text)
                pending["raw_lines"].append(line.strip())
            if len(pending["desc_parts"]) > MAX_CONTINUATION_LINES:
                pending = None
            continue

        has_balance_col = len(monies) >= 2
        amount_m = monies[-2] if has_balance_col else monies[0]
        balance_m = monies[-1] if has_balance_col else None
        balance = _to_float(balance_m) if balance_m is not None else None

        desc_here = rest[: amount_m.start()].strip()
        if desc_here:
            pending["desc_parts"].append(desc_here)
        pending["raw_lines"].append(line.strip())
        description = " ".join(pending["desc_parts"]).strip()
        txn_date = pending["date"]
        raw_data = "\n".join(pending["raw_lines"])
        pending = None

        if not description:
            continue

        if OPENING_TOTAL_RE.match(description):
            in_table = True
            previous_balance = balance if balance is not None else _to_float(amount_m)
            known_offset = 0.0
            continue

        if CLOSING_TOTAL_RE.match(description):
            close_balance = balance if balance is not None else _to_float(amount_m)
            adjusted_end = close_balance - known_offset
            yield from _resolve_group(group, previous_balance, adjusted_end)
            group = []
            previous_balance = close_balance
            known_offset = 0.0
            in_table = False
            continue

        amount_abs = abs(_to_float(amount_m))
        explicit_sign = None
        if _is_debit(amount_m):
            explicit_sign = -1
        elif CREDIT_PREFIX_RE.match(description):
            explicit_sign = 1

        if explicit_sign is not None:
            signed_amount = explicit_sign * amount_abs
            if balance is not None:
                adjusted_end = balance - known_offset - signed_amount
                yield from _resolve_group(group, previous_balance, adjusted_end)
                group = []
                previous_balance = balance
                known_offset = 0.0
            else:
                known_offset += signed_amount
            yield ParsedTransaction(
                date=txn_date, description=description,
                amount=round(signed_amount, 2), balance=balance, raw_data=raw_data,
            )
            continue

        group.append({
            "date": txn_date, "description": description,
            "amount_abs": amount_abs, "own_balance": balance, "raw_data": raw_data,
        })
        if balance is not None:
            adjusted_end = balance - known_offset
            yield from _resolve_group(group, previous_balance, adjusted_end)
            group = []
            previous_balance = balance
            known_offset = 0.0

    yield from _resolve_group(group, None, None)


def _resolve_group(
    group: list[dict], start_balance: float | None, end_balance: float | None
) -> Iterator[ParsedTransaction]:
    """Flush buffered sign-less amounts, inferring sign from balance movement.

    If the group's total doesn't reconcile with the movement (or there's no
    balance to compare against), falls back to the printed positive sign.
    """
    if not group:
        return
    sign = None
    if start_balance is not None and end_balance is not None:
        delta = round(end_balance - start_balance, 2)
        abs_sum = round(sum(g["amount_abs"] for g in group), 2)
        if abs_sum != 0 and abs(delta) == abs_sum:
            sign = 1 if delta > 0 else -1
    for g in group:
        amount = g["amount_abs"] if sign is None else sign * g["amount_abs"]
        yield ParsedTransaction(
            date=g["date"], description=g["description"],
            amount=round(amount, 2), balance=g["own_balance"], raw_data=g["raw_data"],
            ambiguous_sign=(sign is None),
        )


def _parse_period(start_text: str, end_text: str) -> tuple[date, date] | None:
    """The start date has no printed year - borrow the end date's, unless
    the statement crosses a calendar year boundary (e.g. "28 December to
    27 January 2026"), in which case the start is the year before."""
    end = _parse_date(end_text)
    if end is None:
        return None
    start = _parse_date(f"{start_text} {end.year}")
    if start is not None and start > end:
        start = _parse_date(f"{start_text} {end.year - 1}")
    return (start, end) if start is not None else None


def _parse_date(text: str) -> date | None:
    text = text.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _to_float(match: re.Match) -> float:
    return float(match.group(1).replace(",", ""))


def _is_debit(match: re.Match) -> bool:
    marker = match.group(2)
    if marker and marker.upper() == "DR":
        return True
    return match.group(1).startswith("-")
