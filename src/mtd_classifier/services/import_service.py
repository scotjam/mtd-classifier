"""Statement-import logic shared by the CLI and GUI.

Deduplication works at two levels:
- whole files, via a SHA-256 of the file contents;
- individual transactions, via a hash of identity|date|description|amount
  plus an occurrence number so genuine same-day duplicates within one
  statement are kept while re-imports of overlapping statements still dedup.

``identity`` is the account name when one is given (the GUI always gives
one), falling back to the importer's bank tag otherwise. This matters
because the same real-world account is often re-exported in more than one
file format - e.g. a First Direct PDF statement and a First Direct CSV
export covering an overlapping period - and those don't agree on a bank tag
(a generic PDF import is tagged "pdf", not "firstdirect") or even on the
transaction's description (a PDF keeps the printed "DD " payment-type-code
prefix; a CSV export often strips it). Hashing by account instead of the
importer's bank tag, and normalizing the description (see
``normalize_description``) before hashing, lets the same real transaction
dedup correctly across formats. Without an account (bank-hint-only CLI
imports), cross-format dedup isn't possible - there's no reliable way to
know two differently-tagged files describe the same account.

When a repeated day/description/amount is about to be imported as a separate
transaction, the optional ``on_duplicate`` callback decides whether to keep it
(True) or skip it as a duplicated statement row (False). Without a callback it
is kept, preserving the statement's transaction count.

Some importers (the PDF one in particular) can't always tell whether an
amount is money in or out and mark it ``ParsedTransaction.ambiguous_sign``.
The optional ``on_ambiguous_sign`` callback lets the caller resolve each one:
"positive" or "negative" imports it with that sign, "skip" leaves it out of
the database entirely so the user can add it by hand after checking the
statement. Without a callback, ambiguous lines are imported as printed
(positive) - the pre-existing, non-interactive default.

Per-transaction hashing still won't always catch the same real transaction
described two different ways across formats (a wrapped/merged description,
a rounding-off difference), so there's a second, day-level check: for each
date the incoming file has transactions on, its amounts for that day are
compared against whatever's already recorded for the account on that day.
- Equal count and equal amounts: a tie: the already-loaded data wins, the
  incoming day is skipped (as if it were duplicates).
- Incoming has more, and every existing amount is present in it: the
  incoming file is more complete for that day - existing rows are replaced,
  unless any of them already carry a categorisation or attachment, which
  escalates to a conflict instead of silently discarding that work.
- Existing has more, and every incoming amount is present in it: existing
  already covers that day more fully - the incoming day is skipped.
- Otherwise (counts differ and one list isn't a subset of the other):
  a genuine conflict - handed to ``on_day_conflict`` (see
  ``DayConflictResolution``) so a person decides what to keep. Without a
  callback, that day's incoming transactions are skipped and the caller can
  see how many days needed a decision via ``ImportOutcome.day_conflicts``.

The day-level check only runs when the day's existing rows came from a
different format. Re-importing the same format relies on the transaction
hash - which misses the same real transaction if its description changed
(a re-downloaded statement, or an importer fix that reads descriptions
differently). So when an incoming line has no hash match but an existing
line on the same day has the same amount and a different description, the
optional ``on_same_amount(incoming, existing, incoming_file)`` callback asks
whether they are the same transaction (True: skip as a duplicate) or not
(False: import it). Without a callback it is imported, as before.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Literal, Optional, Sequence

from mtd_classifier.importers.base import ParsedTransaction, TransactionImporter
from mtd_classifier.importers.registry import detect_importer
from mtd_classifier.models.import_log import ImportLog
from mtd_classifier.models.transaction import RawTransaction
from mtd_classifier.storage.repository import Repository

DuplicateDecision = Callable[[ParsedTransaction, Optional[RawTransaction]], bool]
AmbiguousSignDecision = Callable[[ParsedTransaction], Literal["positive", "negative", "skip"]]
SameAmountDecision = Callable[[ParsedTransaction, RawTransaction, Path], bool]


@dataclass
class DayConflictResolution:
    """How to resolve a day where existing and incoming data disagree in a
    way that can't be auto-resolved by count/subset comparison alone.

    ``keep_incoming`` must be a subset of the ``incoming`` list the callback
    was given (the same objects, not reconstructed copies) - anything left
    out is skipped for this import. ``delete_existing_ids`` removes those
    existing transactions (and their categorisation/audit/attachment rows).
    """

    keep_incoming: list[ParsedTransaction]
    delete_existing_ids: list[int]


DayConflictDecision = Callable[
    ["object", str, Sequence[RawTransaction], Sequence[ParsedTransaction]],
    Optional[DayConflictResolution],
]


class AlreadyImportedError(Exception):
    """The exact same file was imported before (and force was not set)."""

    def __init__(self, import_log: ImportLog) -> None:
        self.import_log = import_log
        super().__init__(f"File already imported: {import_log.filename}")


class UnknownFormatError(Exception):
    """No importer recognised the file format."""


class BankMismatchError(Exception):
    """The file's detected bank differs from the account's expected bank."""

    def __init__(self, detected_bank: str, expected_bank: str) -> None:
        self.detected_bank = detected_bank
        self.expected_bank = expected_bank
        super().__init__(
            f"Statement looks like {detected_bank!r} but the account expects "
            f"{expected_bank!r}"
        )


@dataclass
class ImportOutcome:
    """Result of importing one statement file."""

    import_log: ImportLog
    bank: str
    format_name: str
    parsed_count: int
    new_count: int
    duplicate_count: int
    out_of_range_count: int = 0  # parsed but outside the requested date_range
    ambiguous_skipped_count: int = 0  # ambiguous-sign lines the user chose to skip
    period: "tuple[object, object] | None" = None  # (start_date, end_date) this file covers
    day_conflicts: int = 0  # days where existing/incoming amounts couldn't be reconciled
    day_replaced_count: int = 0  # existing transactions superseded by a fuller incoming day
    day_skipped_count: int = 0  # incoming transactions skipped - an existing day covered them
    # lines the user confirmed match an existing same-day, same-amount line
    # with a different description (included in duplicate_count)
    same_amount_confirmed_count: int = 0


def format_family(format_name: str) -> str:
    """Coarse source family for cross-format verification: a PDF statement
    is the bank's authoritative printed record; csv/xls/xlsx/txt/ofx exports
    are all machine-readable transaction lists ("tabular"). HMRC output
    should only be generated once both families agree on the period."""
    return "pdf" if format_name == "pdf" else "tabular"


def file_hash(file_path: Path) -> str:
    """Compute SHA-256 hash of file contents."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# Payment-type codes some statements print at the start of a description
# ("DD EE LIMITED") and others strip in their own export format ("EE
# LIMITED") - stripped before hashing so the same transaction matches
# regardless of which format it came from.
_TYPE_CODE_RE = re.compile(
    r"^(DD|SO|BP|CR|DR|TFR|BGC|FPI|FPO|ATM|DEB|CHG|INT)\s+", re.IGNORECASE
)


def normalize_description(description: str) -> str:
    """Normalize a description for dedup hashing (see module docstring)."""
    text = _TYPE_CODE_RE.sub("", description.strip(), count=1)
    return re.sub(r"\s+", " ", text).strip().lower()


def transaction_hash(identity: str, txn: ParsedTransaction, occurrence: int = 0) -> str:
    """Compute dedup hash for a single transaction.

    ``identity`` should be the account name when known, else a bank tag -
    see the module docstring for why. ``occurrence`` numbers genuine
    duplicates within one file (same date, description and amount) so they
    get distinct hashes instead of being dropped. The first occurrence keeps
    the suffix-free format so hashes in previously imported databases remain
    valid.
    """
    raw = f"{identity}|{txn.date.isoformat()}|{normalize_description(txn.description)}|{txn.amount:.2f}"
    if occurrence:
        raw += f"|{occurrence}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_statement(
    file: Path,
    bank_hint: str | None = None,
    column_mapping_provider: "Callable | None" = None,
) -> tuple[TransactionImporter, list[ParsedTransaction]]:
    """Detect the file format and parse it, without touching the database.

    When no importer recognises a tabular file (csv/txt/xls/xlsx), falls
    back to column mapping: a previously saved mapping matching the file's
    header layout is applied automatically; otherwise
    ``column_mapping_provider(file, rows)`` may ask the user to map the
    columns (returning a ColumnMapping, which is then saved for next time,
    or None to give up).
    """
    importer = detect_importer(file, bank_hint=bank_hint)
    if importer is not None:
        return importer, list(importer.parse(file))

    from mtd_classifier.importers.column_mapping import (
        TABULAR_SUFFIXES,
        MappedTabularImporter,
        find_saved_mapping,
        parse_with_mapping,
        read_tabular_rows,
        save_mapping,
    )

    if file.suffix.lower() in TABULAR_SUFFIXES:
        try:
            rows = read_tabular_rows(file)
        except Exception:
            rows = []
        if rows:
            mapping = find_saved_mapping(rows)
            newly_mapped = False
            if mapping is None and column_mapping_provider is not None:
                mapping = column_mapping_provider(file, rows)
                newly_mapped = mapping is not None
            if mapping is not None:
                transactions = list(parse_with_mapping(rows, mapping))
                if transactions:
                    if newly_mapped:
                        save_mapping(mapping)
                    return MappedTabularImporter(mapping, file.suffix), transactions

    raise UnknownFormatError(
        f"Could not detect file format for {file.name}. Try specifying the bank."
    )


# Dates embedded in export filenames: 20260401 / 2026-04-01 (year first)
# or 01042026 / 01-04-2026 (UK day first). Underscores and dots also
# separate; slashes can't appear in filenames.
_FILENAME_YMD_RE = re.compile(r"(?<!\d)(\d{4})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)")
_FILENAME_DMY_RE = re.compile(r"(?<!\d)(\d{2})[-_.]?(\d{2})[-_.]?(\d{4})(?!\d)")


def filename_date_range(name: str) -> "tuple | None":
    """A (start, end) date range embedded in an export's filename, e.g.
    "transactions_20260401_20260630.csv". Needs at least two distinct
    plausible dates - a single date is usually just the download day."""
    from datetime import date as _date

    found = set()
    for match in _FILENAME_YMD_RE.finditer(name):
        y, m, d = (int(g) for g in match.groups())
        if 2000 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31:
            try:
                found.add(_date(y, m, d))
            except ValueError:
                pass
    for match in _FILENAME_DMY_RE.finditer(name):
        d, m, y = (int(g) for g in match.groups())
        if 2000 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31:
            try:
                found.add(_date(y, m, d))
            except ValueError:
                pass
    if len(found) < 2:
        return None
    return (min(found), max(found))


def statement_period(
    importer: TransactionImporter, file: Path, transactions: Sequence[ParsedTransaction]
) -> "tuple | None":
    """What date range a file conclusively covers - see
    ``statement_period_with_source``."""
    return statement_period_with_source(importer, file, transactions)[0]


def statement_period_with_source(
    importer: TransactionImporter, file: Path, transactions: Sequence[ParsedTransaction]
) -> "tuple[tuple | None, str]":
    """What date range a file covers, and how that was determined.

    Prefers, in order: the importer's own account of its period (the PDF
    importer reads the statement's printed "2 March to 1 April 2025"); a
    date range in the filename (banks often export as
    "transactions_20260401_20260630.csv"); the first-to-last transaction
    dates. The fallbacks matter because a day with zero transactions would
    otherwise look like it fell outside the file. A filename range is only
    trusted when it contains every parsed transaction - otherwise the
    digits weren't really this file's date range.

    The source is "statement", "filename" or "transactions" - the last is
    only an estimate (quiet days at the edges are invisible), so callers
    may want the user to confirm it.
    """
    get_period = getattr(importer, "statement_period", None)
    if callable(get_period):
        period = get_period(file)
        if period is not None:
            return period, "statement"
    from_name = filename_date_range(file.name)
    if not transactions:
        return from_name, "filename" if from_name else "transactions"
    dates = [t.date for t in transactions]
    tmin, tmax = min(dates), max(dates)
    if from_name is not None and from_name[0] <= tmin and from_name[1] >= tmax:
        return from_name, "filename"
    return (tmin, tmax), "transactions"


def _reconcile_day(existing_amounts: Counter, incoming_amounts: Counter) -> str:
    """Compare one day's already-recorded amounts against an incoming
    file's amounts for that same day (see module docstring). Returns
    "skip_incoming", "replace_existing" or "conflict"."""
    existing_total = sum(existing_amounts.values())
    incoming_total = sum(incoming_amounts.values())
    if existing_total == incoming_total:
        return "skip_incoming" if existing_amounts == incoming_amounts else "conflict"
    if incoming_total > existing_total:
        remainder = incoming_amounts.copy()
        remainder.subtract(existing_amounts)
        return "replace_existing" if all(v >= 0 for v in remainder.values()) else "conflict"
    remainder = existing_amounts.copy()
    remainder.subtract(incoming_amounts)
    return "skip_incoming" if all(v >= 0 for v in remainder.values()) else "conflict"


def import_statement(
    repo: Repository,
    file: Path,
    bank_hint: str | None = None,
    account: str | None = None,
    force: bool = False,
    on_duplicate: DuplicateDecision | None = None,
    on_parsed: Callable[[TransactionImporter, Sequence[ParsedTransaction]], None] | None = None,
    date_range: "tuple | None" = None,
    expected_bank: str | None = None,
    on_bank_mismatch: Callable[[str, str], bool] | None = None,
    on_ambiguous_sign: AmbiguousSignDecision | None = None,
    on_day_conflict: DayConflictDecision | None = None,
    column_mapping_provider: "Callable | None" = None,
    on_confirm_period: "Callable | None" = None,
    on_same_amount: SameAmountDecision | None = None,
) -> ImportOutcome:
    """Import one statement file with file-, transaction- and day-level dedup.

    ``date_range`` (start, end) keeps only transactions within the period,
    e.g. the tax year being worked on; the rest are counted, not imported.

    ``expected_bank`` triggers a check after format detection: if the file's
    bank differs, ``on_bank_mismatch(detected, expected)`` decides whether to
    proceed (True) - without a callback the mismatch raises BankMismatchError.

    ``on_ambiguous_sign(txn)`` resolves lines the importer couldn't sign
    confidently - see module docstring. Without a callback they're imported
    as printed (positive).

    ``on_day_conflict(date, identity, existing, incoming)`` resolves a day
    where existing and incoming amounts can't be reconciled automatically -
    see module docstring. Without a callback, conflicting days are skipped.

    Raises AlreadyImportedError, UnknownFormatError or BankMismatchError.
    Does not commit; the caller decides when to commit the session.
    """
    fhash = file_hash(file)
    existing_log = repo.get_import_log_by_hash(fhash)
    if existing_log and not force:
        raise AlreadyImportedError(existing_log)

    importer, transactions = parse_statement(
        file, bank_hint, column_mapping_provider=column_mapping_provider
    )
    # Generic formats (spreadsheet, PDF) can't identify their bank, so a
    # mismatch check would always cry wolf - skip it for them
    if (expected_bank is not None and importer.bank_name != expected_bank
            and not getattr(importer, "generic", False)):
        if on_bank_mismatch is None or not on_bank_mismatch(
            importer.bank_name, expected_bank
        ):
            raise BankMismatchError(importer.bank_name, expected_bank)
    if on_parsed is not None:
        on_parsed(importer, transactions)

    # Coverage must reflect the whole file, so compute it before any
    # date_range filtering narrows the transaction list.
    period, period_source = statement_period_with_source(importer, file, transactions)
    if period_source == "transactions" and on_confirm_period is not None:
        # First-to-last transaction dates are only an estimate - quiet days
        # at the file's edges are invisible. Let the user confirm what
        # range their export actually covered.
        confirmed = on_confirm_period(file, period, transactions)
        if confirmed is not None:
            period = confirmed

    out_of_range = 0
    if date_range is not None:
        start, end = date_range
        in_range = [t for t in transactions if start <= t.date <= end]
        out_of_range = len(transactions) - len(in_range)
        transactions = in_range

    bank_name = bank_hint or importer.bank_name
    identity = account if account is not None else bank_name
    if existing_log is not None:
        # Forced re-import (e.g. re-scanning a folder for a different tax
        # year): file_hash is unique, so reuse the original log
        import_log = existing_log
        import_log.record_count = max(import_log.record_count, len(transactions))
        if period is not None:
            if import_log.period_start is None or period[0] < import_log.period_start:
                import_log.period_start = period[0]
            if import_log.period_end is None or period[1] > import_log.period_end:
                import_log.period_end = period[1]
        if import_log.account is None:
            import_log.account = account
        import_log.source_path = str(file.resolve())
    else:
        import_log = repo.create_import_log(
            filename=file.name,
            file_hash=fhash,
            bank=bank_name,
            fmt=importer.format_name,
            record_count=len(transactions),
            period_start=period[0] if period else None,
            period_end=period[1] if period else None,
            account=account,
            source_path=str(file.resolve()),
        )

    # Resolve sign ambiguity first - both the day-level check below and
    # per-transaction dedup need final signed amounts to compare.
    ambiguous_skipped = 0
    resolved: list[ParsedTransaction] = []
    for txn in transactions:
        if txn.ambiguous_sign:
            decision = (
                on_ambiguous_sign(txn) if on_ambiguous_sign is not None else "positive"
            )
            if decision == "skip":
                ambiguous_skipped += 1
                continue
            txn = replace(txn, amount=(abs(txn.amount) if decision == "positive"
                                        else -abs(txn.amount)))
        resolved.append(txn)

    # Day-level reconciliation (see module docstring): catches the same
    # real transaction described differently across formats, which
    # per-transaction hashing alone can miss.
    family = format_family(importer.format_name)
    excluded_ids: set[int] = set()
    # (date, amount) -> source families inherited from rows this import
    # replaced, so the replacements keep their cross-format evidence
    inherited_families: dict[tuple[object, float], set[str]] = {}
    day_conflicts = 0
    day_replaced = 0
    day_skipped = 0
    # date -> existing rows on days left to per-transaction dedup because
    # they came from this same format (see on_same_amount)
    same_format_existing: dict = {}
    by_date: dict = {}
    for txn in resolved:
        by_date.setdefault(txn.date, []).append(txn)

    for txn_date, day_txns in by_date.items():
        existing = repo.get_transactions_for_account(
            bank=bank_name, account=account, start_date=txn_date, end_date=txn_date,
        )
        if not existing:
            continue
        if all(t.bank == bank_name for t in existing):
            # Same format as what's already recorded for this day - the
            # ordinary per-transaction hash/occurrence flow below (including
            # on_duplicate) already handles this well. Day-level
            # reconciliation exists for cross-format mismatches, not this.
            same_format_existing[txn_date] = list(existing)
            continue
        existing_amounts = Counter(round(t.amount, 2) for t in existing)
        incoming_amounts = Counter(round(t.amount, 2) for t in day_txns)
        verdict = _reconcile_day(existing_amounts, incoming_amounts)

        if verdict == "replace_existing" and repo.has_categorisation_or_attachment(
            [t.id for t in existing]
        ):
            # Auto-replacing would silently discard the user's own work -
            # escalate to a conflict instead.
            verdict = "conflict"

        if verdict == "skip_incoming":
            excluded_ids.update(id(t) for t in day_txns)
            day_skipped += len(day_txns)
            # The incoming file still evidences the existing rows it matched
            # (by amount) - record its family so cross-format verification
            # knows this day was confirmed by both sources.
            budget = incoming_amounts.copy()
            for t in existing:
                amt = round(t.amount, 2)
                if budget[amt] > 0:
                    budget[amt] -= 1
                    t.add_source_family(family)
        elif verdict == "replace_existing":
            # The replacement rows created below inherit the families of the
            # deleted rows they match by amount (plus this file's own family)
            # - the old evidence is about the same real transactions.
            for t in existing:
                for fam in (t.source_formats or "").split(","):
                    if fam:
                        inherited_families.setdefault(
                            (txn_date, round(t.amount, 2)), set()
                        ).add(fam)
            result = repo.delete_raw_transactions([t.id for t in existing])
            for path in result["attachment_paths"]:
                if path.exists():
                    path.unlink()
            day_replaced += result["transactions_removed"]
        else:  # conflict
            resolution = (
                on_day_conflict(txn_date, identity, existing, day_txns)
                if on_day_conflict is not None else None
            )
            if resolution is None:
                excluded_ids.update(id(t) for t in day_txns)
                day_conflicts += 1
                continue
            if resolution.delete_existing_ids:
                result = repo.delete_raw_transactions(resolution.delete_existing_ids)
                for path in result["attachment_paths"]:
                    if path.exists():
                        path.unlink()
                day_replaced += result["transactions_removed"]
            keep_ids = {id(t) for t in resolution.keep_incoming}
            excluded_ids.update(id(t) for t in day_txns if id(t) not in keep_ids)

    new_count = 0
    dup_count = 0
    same_amount_confirmed = 0
    # Hash everything first: an existing row matched by hash anywhere in
    # this file must not also be offered as a same-amount match for an
    # earlier line.
    hashed = []
    claimed: set[int] = set()  # existing rows already matched this import
    occurrences: Counter[tuple[object, ...]] = Counter()
    for txn in resolved:
        if id(txn) in excluded_ids:
            continue
        key = (txn.date, normalize_description(txn.description), f"{txn.amount:.2f}")
        occurrence = occurrences[key]
        occurrences[key] += 1
        dhash = transaction_hash(identity, txn, occurrence)
        existing = repo.get_raw_transaction_by_hash(dhash)
        if existing is not None:
            claimed.add(existing.id)
        hashed.append((txn, occurrence, dhash, existing))

    for txn, occurrence, dhash, existing in hashed:
        if existing:
            # This file independently evidences the already-stored row -
            # record its source family for cross-format verification.
            existing.add_source_family(family)
            dup_count += 1
            continue

        # A repeated day/description/amount is about to be imported as a
        # separate transaction - let the caller confirm it isn't a
        # duplicated statement row.
        if occurrence > 0 and on_duplicate is not None:
            previous = repo.get_raw_transaction_by_hash(
                transaction_hash(identity, txn, occurrence - 1)
            )
            if not on_duplicate(txn, previous):
                dup_count += 1
                continue

        if on_same_amount is not None:
            match = _same_amount_match(
                txn, same_format_existing.get(txn.date, ()), claimed,
                import_log.id, on_same_amount, file,
            )
            if match is not None:
                claimed.add(match.id)
                match.add_source_family(family)
                dup_count += 1
                same_amount_confirmed += 1
                continue

        families = {family} | inherited_families.get(
            (txn.date, round(txn.amount, 2)), set()
        )
        repo.create_raw_transaction(
            import_log_id=import_log.id,
            bank=bank_name,
            txn_date=txn.date,
            description=txn.description,
            amount=txn.amount,
            dedup_hash=dhash,
            account=account,
            balance=txn.balance,
            raw_data=txn.raw_data,
            source_formats=",".join(sorted(families)),
        )
        new_count += 1

    if existing_log is not None:
        import_log.new_record_count += new_count
    else:
        import_log.new_record_count = new_count

    return ImportOutcome(
        import_log=import_log,
        bank=bank_name,
        format_name=importer.format_name,
        parsed_count=len(transactions) + out_of_range,
        new_count=new_count,
        # A day-level skip is still a duplicate, just caught at a coarser
        # grain than the per-transaction hash check - folded in here so
        # existing callers' totals stay meaningful; day_skipped_count below
        # has the finer-grained breakdown for callers that want it.
        duplicate_count=dup_count + day_skipped,
        out_of_range_count=out_of_range,
        ambiguous_skipped_count=ambiguous_skipped,
        period=period,
        day_conflicts=day_conflicts,
        day_replaced_count=day_replaced,
        day_skipped_count=day_skipped,
        same_amount_confirmed_count=same_amount_confirmed,
    )


def _same_amount_match(
    txn: ParsedTransaction,
    day_existing: Sequence[RawTransaction],
    claimed: set[int],
    import_log_id: int,
    decide: SameAmountDecision,
    file: Path,
) -> RawTransaction | None:
    """The existing row the user confirms is the same transaction as
    ``txn`` (same day and amount, different description), or None."""
    desc = normalize_description(txn.description)
    for row in day_existing:
        if (row.id in claimed
                or row.import_log_id == import_log_id  # this file's own rows
                or round(row.amount, 2) != round(txn.amount, 2)
                or normalize_description(row.description) == desc):
            continue
        if decide(txn, row, file):
            return row
    return None


def statement_source_path(log: ImportLog | None, near: Path | None = None) -> Path | None:
    """Where an import's source file can be opened from, if it still exists:
    its recorded location, else - for imports made before locations were
    recorded, or a moved folder - a file of the same name beside ``near``
    (statements usually sit together in one folder)."""
    if log is None:
        return None
    if log.source_path:
        recorded = Path(log.source_path)
        if recorded.is_file():
            return recorded
    if near is not None:
        sibling = near.parent / log.filename
        if sibling.is_file():
            return sibling
    return None
