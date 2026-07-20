"""Shared layout-detection and row-parsing logic for spreadsheet importers.

Both the modern (.xlsx, via openpyxl) and legacy (.xls, via xlrd) importers
read into plain Python rows and hand them to this module, which finds the
header row and turns subsequent rows into ParsedTransaction objects. Banks
export spreadsheets with varying layouts, so this looks for a header row
anywhere in the first 15 rows containing a date column, a description-like
column and either a single amount column or a Money Out / Money In pair.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, datetime
from typing import Iterable, Iterator

from mtd_classifier.importers.base import ParsedTransaction

DESCRIPTION_HEADERS = ("description", "details", "narrative", "transaction", "memo")
AMOUNT_HEADERS = ("amount", "value")
DEBIT_HEADERS = ("money out", "debit", "paid out", "withdrawals")
CREDIT_HEADERS = ("money in", "credit", "paid in", "deposits")
BALANCE_HEADERS = ("balance",)

DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d %b %Y", "%d-%b-%Y", "%d-%b-%y")

MAX_HEADER_SCAN_ROWS = 15


def find_layout(rows: Iterable[tuple]) -> tuple[int, dict] | None:
    """Locate the header row and map column indices.

    ``rows`` yields raw cell tuples for at least the first
    MAX_HEADER_SCAN_ROWS rows. Returns (0-based header row index, {role: col})
    or None if no recognisable header is found.
    """
    for row_num, row in enumerate(rows):
        if row_num >= MAX_HEADER_SCAN_ROWS:
            break
        headers = [str(c).strip().lower() if c is not None else "" for c in row]
        cols: dict[str, object] = {}
        desc_cols: list[int] = []
        for i, h in enumerate(headers):
            if not h:
                continue
            if "date" in h and "date" not in cols:
                cols["date"] = i
            elif any(k in h for k in DESCRIPTION_HEADERS):
                # All description-like columns combine (e.g. Nationwide's
                # "Transaction type" + "Description")
                desc_cols.append(i)
            elif any(k in h for k in DEBIT_HEADERS) and "debit" not in cols:
                cols["debit"] = i
            elif any(k in h for k in CREDIT_HEADERS) and "credit" not in cols:
                cols["credit"] = i
            elif any(k in h for k in AMOUNT_HEADERS) and "amount" not in cols:
                cols["amount"] = i
            elif any(k in h for k in BALANCE_HEADERS) and "balance" not in cols:
                cols["balance"] = i
        if desc_cols:
            cols["description"] = desc_cols
        has_money = "amount" in cols or ("debit" in cols or "credit" in cols)
        if "date" in cols and "description" in cols and has_money:
            return row_num, cols
    return None


def parse_rows(
    rows: Iterable[tuple], cols: dict
) -> Iterator[ParsedTransaction]:
    """Turn data rows (after the header) into ParsedTransaction objects.

    With a single amount column and a balance column, each amount's sign is
    verified against the running balance movement (see
    ``infer_signs_from_balance``); a Money Out / Money In pair already
    carries its own sign, so no inference is needed there.
    """
    txns = (t for t in (_parse_row(row, cols) for row in rows) if t is not None)
    if "amount" in cols and "balance" in cols:
        yield from infer_signs_from_balance(list(txns))
    else:
        yield from txns


def _parse_row(row: tuple, cols: dict) -> ParsedTransaction | None:
    def cell(role: str):
        i = cols.get(role)
        return row[i] if i is not None and i < len(row) else None

    txn_date = parse_date(cell("date"))
    if txn_date is None:
        return None  # blank row, running totals, footer text

    desc_cols = cols["description"]
    if isinstance(desc_cols, int):  # single-column form still accepted
        desc_cols = [desc_cols]
    parts = []
    for i in desc_cols:
        value = row[i] if i < len(row) else None
        text = str(value).strip() if value is not None else ""
        if text:
            parts.append(text)
    description = " ".join(parts)
    if not description:
        return None

    if "amount" in cols:
        amount = parse_amount(cell("amount"))
    else:
        debit = parse_amount(cell("debit")) or 0.0
        credit = parse_amount(cell("credit")) or 0.0
        amount = credit - abs(debit)
        if debit == 0.0 and credit == 0.0:
            amount = None
    if amount is None:
        return None

    balance = parse_amount(cell("balance"))
    return ParsedTransaction(
        date=txn_date,
        description=description,
        amount=amount,
        balance=balance,
        raw_data=str([c for c in row if c is not None]),
    )


def infer_signs_from_balance(
    txns: list[ParsedTransaction],
) -> list[ParsedTransaction]:
    """Determine credit/debit from the running balance movement.

    For consecutive rows in chronological order, balance = previous balance
    + this row's signed amount, so when |balance movement| equals |amount|
    the movement's sign is proof of the transaction's direction - exactly
    the technique the PDF importer uses. Files come newest-first as often
    as oldest-first, so both orders are scored and the one where more rows
    reconcile wins.

    Rows whose sign can't be proven directly (the chronologically-first row
    has no predecessor; a missing balance breaks the chain) get the file's
    sign convention as established by the proven rows: if every proven row
    printed the way this row is printed turned out to be the same direction
    (e.g. unsigned amounts all proved to be debits, or printed signs all
    proved truthful), that direction is applied. Only when the proven rows
    contradict each other - the same printed form was both a credit and a
    debit - is the row marked ``ambiguous_sign`` for the user to resolve.
    """
    if len(txns) < 2:
        return txns

    def score(ordered: list[ParsedTransaction]) -> int:
        matches = 0
        for prev, cur in zip(ordered, ordered[1:]):
            if prev.balance is None or cur.balance is None:
                continue
            delta = round(cur.balance - prev.balance, 2)
            if delta != 0 and abs(delta) == round(abs(cur.amount), 2):
                matches += 1
        return matches

    forward, backward = txns, list(reversed(txns))
    ordered = forward if score(forward) >= score(backward) else backward
    if score(ordered) == 0:
        return txns  # balances don't reconcile at all - leave everything as printed

    verified: dict[int, float] = {}  # id(txn) -> proven signed amount
    for prev, cur in zip(ordered, ordered[1:]):
        if prev.balance is None or cur.balance is None:
            continue
        delta = round(cur.balance - prev.balance, 2)
        if delta != 0 and abs(delta) == round(abs(cur.amount), 2):
            verified[id(cur)] = delta

    # The file's sign convention, learned from the proven rows: how a row
    # is printed (positive or negative) -> which direction(s) it proved to
    # be. A single direction per printed form is a usable convention.
    convention: dict[int, set[int]] = {}
    for t in txns:
        proven = verified.get(id(t))
        if proven:
            printed = 1 if t.amount >= 0 else -1
            convention.setdefault(printed, set()).add(1 if proven > 0 else -1)

    file_has_printed_signs = any(t.amount < 0 for t in txns)

    result = []
    for t in txns:
        if id(t) in verified:
            result.append(replace(t, amount=verified[id(t)], ambiguous_sign=False))
            continue
        printed = 1 if t.amount >= 0 else -1
        directions = convention.get(printed, set())
        if len(directions) == 1:
            # Every proven row printed this way was the same direction -
            # apply the file's established convention.
            sign = next(iter(directions))
            result.append(replace(t, amount=sign * abs(t.amount), ambiguous_sign=False))
        elif len(directions) > 1:
            # Rows printed this way proved to be both credits and debits -
            # genuinely undeterminable, ask the user.
            result.append(replace(t, ambiguous_sign=True))
        elif file_has_printed_signs:
            # No proven examples of this printed form, but the file prints
            # explicit signs (some amounts negative), so the printed sign
            # is itself evidence - not a guess.
            result.append(replace(t, ambiguous_sign=False))
        else:
            result.append(replace(t, ambiguous_sign=True))
    return result


def parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")  # accounting negatives
    # Strip currency symbols and thousands separators - keep only digits, a
    # decimal point and a leading minus. Matches any currency symbol/mojibake
    # (£, $, badly-decoded bytes) without needing to name each one.
    cleaned = re.sub(r"[^0-9.\-]", "", text.strip("()"))
    if not cleaned or not re.fullmatch(r"-?\d+(\.\d+)?", cleaned):
        return None
    amount = float(cleaned)
    return -amount if negative else amount
