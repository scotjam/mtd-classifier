"""Completeness verification before HMRC output is generated.

An account's figures are considered verified for a period when EITHER:

- **The balance chain proves them.** If every transaction carries a
  running balance and each one equals the previous balance plus the
  transaction's amount - an unbroken chain from before the period starts
  to after it ends - then nothing is missing and nothing was imported
  twice: any gap or duplicate would break the chain. The margin either
  side matters: a break can only be detected *between* two known rows, so
  the export must reach at least a day beyond both ends of the period.

- **Two independent sources agree.** A transaction list export (csv/xls/
  xlsx - the "tabular" family) and the bank's printed PDF statements
  describe the account through two independent routes; when both cover
  the period and their totals match, the figures corroborate each other.
  Each stored transaction records which families evidenced it
  (``RawTransaction.source_formats``, maintained by the import service
  even when a duplicate file is skipped).

A failed check must not hard-block an export - accounts genuinely may
have only one incomplete source available - but the caller is expected to
show the report and let the user explicitly decide to proceed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Sequence

from mtd_classifier.models.transaction import RawTransaction
from mtd_classifier.storage.repository import Repository


@dataclass
class AccountVerification:
    """Completeness check result for one account over a period."""

    account: str  # display label (account name, or bank tag if unlabelled)
    pdf_total: float
    tabular_total: float
    pdf_count: int
    tabular_count: int
    # Transactions evidenced by only one family - exactly what a person
    # needs to look at to explain a total mismatch.
    missing_from_pdf: list[RawTransaction] = field(default_factory=list)
    missing_from_tabular: list[RawTransaction] = field(default_factory=list)
    # Unbroken running-balance chain spanning beyond both period ends -
    # proof of completeness on its own, no second source needed.
    chain_ok: bool = False
    chain_problem: str = ""
    # Whether the imported statements' own date ranges span the export
    # period. Two sources can agree perfectly on incomplete data (both
    # simply end mid-quarter), so cross-format agreement needs this too.
    coverage_ok: bool = False
    coverage_problem: str = ""

    @property
    def has_both_sources(self) -> bool:
        return self.pdf_count > 0 and self.tabular_count > 0

    @property
    def totals_match(self) -> bool:
        return round(self.pdf_total, 2) == round(self.tabular_total, 2)

    @property
    def cross_format_ok(self) -> bool:
        return self.has_both_sources and self.totals_match

    @property
    def ok(self) -> bool:
        return self.chain_ok or (self.cross_format_ok and self.coverage_ok)

    def problem_summary(self) -> str:
        parts = []
        if self.coverage_problem:
            parts.append(f"incomplete data: {self.coverage_problem}")
        if self.chain_problem:
            parts.append(f"balance chain: {self.chain_problem}")
        if not self.has_both_sources:
            missing = []
            if self.pdf_count == 0:
                missing.append("no PDF statements")
            if self.tabular_count == 0:
                missing.append("no CSV/XLS transaction lists")
            parts.append(f"{' and '.join(missing)} cover this period")
        elif not self.totals_match:
            parts.append(
                f"PDF total {self.pdf_total:,.2f} != "
                f"CSV/XLS total {self.tabular_total:,.2f} "
                f"(difference {self.pdf_total - self.tabular_total:+,.2f})"
            )
        return f"{self.account}: " + "; ".join(parts)


def check_balance_chain(
    account_txns: Sequence[RawTransaction], start_date: date, end_date: date
) -> tuple[bool, str]:
    """Whether an unbroken running-balance chain proves the period complete.

    ``account_txns`` must be the account's FULL recorded history, not just
    the period - the chain has to be anchored on a transaction dated before
    the period and one dated after it, because a missing transaction can
    only be detected between two known rows. Returns (ok, problem).
    """
    rows = [t for t in account_txns]
    if not rows:
        return False, "no transactions recorded"

    before = [t for t in rows if t.date < start_date]
    after = [t for t in rows if t.date > end_date]
    if not before or not after:
        missing = []
        if not before:
            missing.append("before it starts")
        if not after:
            missing.append("after it ends")
        return False, (
            f"no transaction recorded {' or '.join(missing)} - re-export the "
            "transaction list including at least a day either side of the period"
        )

    # The chain window: from the last transaction before the period to the
    # first one after it. Same-day order isn't recorded in the dates, so the
    # insertion order (id) is used - forwards for oldest-first files,
    # backwards for newest-first - and the direction with fewer breaks wins.
    anchor_start = max(t.date for t in before)
    anchor_end = min(t.date for t in after)
    window = [t for t in rows if anchor_start <= t.date <= anchor_end]

    missing_balance = [t for t in window if t.balance is None]
    if missing_balance:
        return False, (
            f"{len(missing_balance)} transaction(s) in the period have no "
            "recorded balance"
        )

    def breaks(ordered: list[RawTransaction]) -> int:
        count = 0
        for prev, cur in zip(ordered, ordered[1:]):
            delta = round(cur.balance - prev.balance, 2)
            if delta != round(cur.amount, 2):
                count += 1
        return count

    forward = sorted(window, key=lambda t: (t.date, t.id))
    backward = sorted(window, key=lambda t: (t.date, -t.id))
    n_breaks = min(breaks(forward), breaks(backward))
    if n_breaks:
        return False, (
            f"balance movements don't reconcile at {n_breaks} point(s) - "
            "a transaction may be missing, duplicated or mis-signed"
        )
    return True, ""


def check_statement_coverage(
    logs, start_date: date, end_date: date
) -> tuple[bool, str]:
    """Whether imported statements' own date ranges span the export period.

    ``logs`` are the account's ImportLog rows; each carries the period its
    file covered (a PDF's printed statement period, or min/max transaction
    dates). The requirement is capped at today - data for future dates
    can't exist, and HMRC cumulative updates mid-quarter are legitimate.
    """
    required_end = min(end_date, date.today())
    if start_date > required_end:
        return True, ""  # entirely-future period: nothing coverable yet

    intervals = sorted(
        (log.period_start, log.period_end)
        for log in logs
        if log.period_start is not None and log.period_end is not None
    )
    if not intervals:
        return False, "no statement covers this period"

    gaps = []
    cursor = start_date
    for span_start, span_end in intervals:
        if span_end < cursor:
            continue
        if span_start > cursor:
            gaps.append((cursor, min(span_start - timedelta(days=1), required_end)))
        cursor = max(cursor, span_end + timedelta(days=1))
        if cursor > required_end:
            break
    if cursor <= required_end:
        gaps.append((cursor, required_end))

    if not gaps:
        return True, ""
    gap_text = ", ".join(
        f"{g0}" if g0 == g1 else f"{g0} to {g1}" for g0, g1 in gaps[:4]
    )
    if len(gaps) > 4:
        gap_text += f" (and {len(gaps) - 4} more gaps)"
    return False, f"no statement covers {gap_text}"


def verify_cross_format(
    repo: Repository, start_date: date, end_date: date
) -> list[AccountVerification]:
    """Check every account's completeness over a period: an unbroken
    balance chain spanning the period, or agreeing PDF vs tabular totals.

    Returns one result per account that has any transactions in the period,
    ordered by account label. The caller decides what a failure means
    (typically: warn and ask before generating HMRC output).
    """
    txns: Sequence[RawTransaction] = repo.get_transactions_in_period(start_date, end_date)
    all_txns: Sequence[RawTransaction] = repo.get_transactions_in_period(
        date(1900, 1, 1), date(2999, 12, 31)
    )
    by_account: dict[str, list[RawTransaction]] = {}
    for t in txns:
        label = t.account if t.account is not None else t.bank
        by_account.setdefault(label, []).append(t)
    history_by_account: dict[str, list[RawTransaction]] = {}
    for t in all_txns:
        label = t.account if t.account is not None else t.bank
        history_by_account.setdefault(label, []).append(t)

    results = []
    for label in sorted(by_account):
        rows = by_account[label]
        pdf_rows = [t for t in rows if t.has_source_family("pdf")]
        tab_rows = [t for t in rows if t.has_source_family("tabular")]
        chain_ok, chain_problem = check_balance_chain(
            history_by_account.get(label, []), start_date, end_date
        )
        # The label is an account name when its rows carry one, otherwise a
        # bank tag for unlabelled imports - fetch that account's logs.
        if rows[0].account is not None:
            logs = repo.get_import_logs_by_account(bank=None, account=label)
        else:
            logs = repo.get_import_logs_by_account(bank=label, account=None)
        coverage_ok, coverage_problem = check_statement_coverage(
            logs, start_date, end_date
        )
        results.append(AccountVerification(
            account=label,
            pdf_total=round(sum(t.amount for t in pdf_rows), 2),
            tabular_total=round(sum(t.amount for t in tab_rows), 2),
            pdf_count=len(pdf_rows),
            tabular_count=len(tab_rows),
            missing_from_pdf=[t for t in rows if not t.has_source_family("pdf")],
            missing_from_tabular=[t for t in rows if not t.has_source_family("tabular")],
            chain_ok=chain_ok,
            chain_problem=chain_problem,
            coverage_ok=coverage_ok,
            coverage_problem=coverage_problem,
        ))
    return results


def verification_report(results: list[AccountVerification], limit: int = 8) -> str:
    """Human-readable multi-line report of the failures in a verification
    run, for a warning dialog or console output. Empty string if all ok."""
    problems = [r for r in results if not r.ok]
    if not problems:
        return ""
    lines = []
    for r in problems:
        lines.append(r.problem_summary())
        unmatched = r.missing_from_pdf + r.missing_from_tabular
        shown = 0
        for t in r.missing_from_pdf:
            if shown >= limit:
                break
            lines.append(f"    {t.date}  {t.amount:>10,.2f}  {t.description}  (no PDF statement backs this)")
            shown += 1
        for t in r.missing_from_tabular:
            if shown >= limit:
                break
            lines.append(f"    {t.date}  {t.amount:>10,.2f}  {t.description}  (no CSV/XLS list backs this)")
            shown += 1
        remaining = len(unmatched) - shown
        if remaining > 0:
            lines.append(f"    ... and {remaining} more")
    return "\n".join(lines)
