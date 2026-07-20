"""Typer CLI entry point for mtd-classifier."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from mtd_classifier.config import ensure_app_dir
from mtd_classifier.disclaimer import DISCLAIMER
from mtd_classifier.models.category import (
    TransactionCategory,
    CATEGORY_LABELS,
    CATEGORIES_REQUIRING_CONFIRMATION,
    categories_for_amount,
)
from mtd_classifier.periods import TaxQuarter, parse_period, quarter_for_date
from mtd_classifier.storage.database import get_engine, init_db, get_session
from mtd_classifier.storage.repository import Repository
from mtd_classifier.classifier.rule_engine import RuleBasedClassifier
from mtd_classifier.export.mtd_summary import summarise_quarter
from mtd_classifier.export.csv_exporter import export_to_csv
from mtd_classifier.services.import_service import (
    AlreadyImportedError,
    DayConflictResolution,
    UnknownFormatError,
    import_statement,
    parse_statement,
)
from mtd_classifier.services.verification import verification_report, verify_cross_format

app = typer.Typer(
    name="mtd",
    help="MTD Transaction Classifier - categorise bank transactions for Making Tax Digital.",
    no_args_is_help=True,
)
console = Console()

# Whether the active user is already known for this process - set by the
# --user option / MTD_USER env var, by interactive selection, or once at
# the start of a continuous session (`mtd shell`), after which commands in
# that session don't ask again.
_user_resolved = False


@app.callback()
def _cli_main(
    user: Optional[str] = typer.Option(
        None, "--user", envvar="MTD_USER",
        help="Which user's data to operate on (see 'mtd users list')",
    ),
) -> None:
    global _user_resolved
    if user and not _user_resolved:
        _activate_named_user(user)
        _user_resolved = True


def _activate_named_user(name: str) -> None:
    import mtd_classifier.config as config

    users = config.list_users()
    match = next((u for u in users if u.lower() == name.strip().lower()), None)
    if match is None:
        console.print(
            f"[red]Unknown user {name!r}.[/red] Known users: {', '.join(users)}"
        )
        raise typer.Exit(1)
    config.activate_user(match)


def _ensure_user() -> None:
    """Resolve which user's data this command operates on.

    Every data-touching command requires a user: --user/MTD_USER if given,
    the sole registered user automatically, otherwise an interactive
    selection. Unattended runs with several users must say which one -
    guessing whose tax data to modify is not acceptable.
    """
    global _user_resolved
    if _user_resolved:
        return
    import mtd_classifier.config as config

    users = config.list_users()
    if len(users) == 1:
        config.activate_user(users[0])
        _user_resolved = True
        return

    default = config.last_user()
    if default not in users:
        default = users[0]
    console.print("Select user:")
    for i, name in enumerate(users, start=1):
        marker = " (last used)" if name == default else ""
        console.print(f"  {i}. {name}{marker}")
    while True:
        try:
            raw = input(f"User [{default}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print(
                "[red]Multiple users exist - pass --user NAME or set the "
                "MTD_USER environment variable.[/red]"
            )
            raise typer.Exit(1)
        if not raw:
            chosen = default
        elif raw.isdigit() and 1 <= int(raw) <= len(users):
            chosen = users[int(raw) - 1]
        else:
            match = next((u for u in users if u.lower() == raw.lower()), None)
            if match is None:
                print(f"Enter 1-{len(users)} or a user name.")
                continue
            chosen = match
        break
    config.activate_user(chosen)
    _user_resolved = True
    console.print(f"[dim]Active user: {chosen}[/dim]")


def _get_repo() -> Repository:
    """Get a repository connected to the active user's database."""
    _ensure_user()
    ensure_app_dir()
    engine = get_engine()
    init_db(engine)
    session = get_session(engine)
    return Repository(session)


# --- IMPORT command ---

@app.command()
def import_file(
    file: Path = typer.Argument(..., help="Path to bank statement file"),
    bank: Optional[str] = typer.Option(None, help="Bank name hint (santander, firstdirect)"),
    account: Optional[str] = typer.Option(None, help="Account label"),
    force: bool = typer.Option(False, "--force", help="Re-import even if file was already imported"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Parse file without saving to database"),
    duplicates: str = typer.Option(
        "ask",
        help="What to do when the same day/description/amount appears again: ask, import or skip",
    ),
) -> None:
    """Import transactions from a bank statement file."""
    if not file.exists():
        console.print(f"[red]Error:[/red] File not found: {file}")
        raise typer.Exit(1)

    if duplicates not in ("ask", "import", "skip"):
        console.print("[red]--duplicates must be ask, import or skip[/red]")
        raise typer.Exit(1)

    repo = _get_repo()

    if dry_run:
        try:
            importer, transactions = parse_statement(file, bank_hint=bank)
        except UnknownFormatError:
            console.print(
                f"[red]Error:[/red] Could not detect file format for {file.name}. "
                "Try specifying --bank."
            )
            raise typer.Exit(1)
        console.print(
            f"Detected format: [cyan]{importer.bank_name}[/cyan] "
            f"([cyan]{importer.format_name}[/cyan])"
        )
        console.print(f"Parsed [green]{len(transactions)}[/green] transactions.")
        for txn in transactions[:10]:
            console.print(
                f"  {txn.date}  {txn.amount:>10.2f}  {txn.description}"
            )
        if len(transactions) > 10:
            console.print(f"  ... and {len(transactions) - 10} more")
        raise typer.Exit(0)

    def _on_parsed(importer, transactions):
        console.print(
            f"Detected format: [cyan]{importer.bank_name}[/cyan] "
            f"([cyan]{importer.format_name}[/cyan])"
        )
        console.print(f"Parsed [green]{len(transactions)}[/green] transactions.")

    def _on_duplicate(txn, previous) -> bool:
        _show_duplicate_context(txn, previous, file.name)
        if duplicates == "skip" or _prompt_duplicate_action() == "s":
            console.print("       -> Skipped as duplicate.\n")
            return False
        console.print("       -> Imported as separate transaction.\n")
        return True

    def _on_ambiguous_sign(txn) -> str:
        _show_ambiguous_sign_context(txn)
        return _prompt_ambiguous_sign_action(abs(txn.amount))

    def _on_day_conflict(txn_date, identity, existing, incoming):
        return _prompt_day_conflict(txn_date, identity, existing, incoming)

    try:
        outcome = import_statement(
            repo,
            file,
            bank_hint=bank,
            account=account,
            force=force,
            on_duplicate=None if duplicates == "import" else _on_duplicate,
            on_parsed=_on_parsed,
            on_ambiguous_sign=_on_ambiguous_sign,
            on_day_conflict=_on_day_conflict,
            column_mapping_provider=_prompt_column_mapping_cli,
            on_confirm_period=_prompt_confirm_period_cli,
        )
    except AlreadyImportedError as e:
        console.print(
            f"[yellow]File already imported:[/yellow] {e.import_log.filename} "
            f"({e.import_log.record_count} records on {e.import_log.imported_at:%Y-%m-%d})"
        )
        console.print("Use --force to re-import.")
        raise typer.Exit(0)
    except UnknownFormatError:
        console.print(
            f"[red]Error:[/red] Could not detect file format for {file.name}. "
            "Try specifying --bank."
        )
        raise typer.Exit(1)

    repo.commit()

    console.print(
        f"[green]Imported {outcome.new_count} new transactions[/green] "
        f"({outcome.duplicate_count} duplicates skipped)."
    )
    if outcome.ambiguous_skipped_count:
        console.print(
            f"[yellow]{outcome.ambiguous_skipped_count} line(s) skipped as "
            "ambiguous[/yellow] - add them manually after checking the paper statement."
        )
    if outcome.day_replaced_count:
        console.print(
            f"[cyan]{outcome.day_replaced_count} earlier transaction(s) replaced[/cyan] "
            "by a fuller same-day statement from another file format."
        )
    if outcome.day_conflicts:
        console.print(
            f"[yellow]{outcome.day_conflicts} day(s) need manual review[/yellow] - "
            "re-run the import to resolve them."
        )


def _warn_if_needs_confirmation(category: TransactionCategory) -> None:
    """Flag categories whose bank-deposit total may not be the tax-return figure."""
    if category in CATEGORIES_REQUIRING_CONFIRMATION:
        console.print(
            f"       [yellow]Note:[/yellow] {CATEGORY_LABELS[category]} shows the "
            "BANK DEPOSIT amount (net, after tax). Quarterly updates never "
            "include it, so nothing is held up - run 'mtd employment "
            "set-income' (or use the GUI) to confirm the gross figure before "
            "the year-end final declaration."
        )


def _show_duplicate_context(txn, previous, current_filename: str) -> None:
    """Show the raw statement extracts behind a potential duplicate."""
    console.print(
        f"[yellow]Potential duplicate:[/yellow] "
        f"{txn.date}  {txn.amount:>10.2f}  {txn.description}"
    )
    console.print(f"       This file ({current_filename}):")
    console.print(f"         {txn.raw_data or '(no raw statement line available)'}")
    if previous is not None:
        source = "earlier in this file"
        if previous.import_log is not None and previous.import_log.filename != current_filename:
            source = (
                f"from {previous.import_log.filename}, "
                f"imported {previous.imported_at:%Y-%m-%d}"
            )
        console.print(f"       Already recorded ({source}):")
        console.print(f"         {previous.raw_data or '(no raw statement line available)'}")


def _prompt_duplicate_action() -> str:
    """Ask whether a repeated statement line is a real transaction."""
    while True:
        try:
            response = input(
                "       [i]mport as separate transaction / [s]kip as duplicate? "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "i"  # unattended runs keep the statement's transaction count
        if response in ("i", "s"):
            return response
        print("       Invalid choice. Enter i or s.")


def _show_ambiguous_sign_context(txn) -> None:
    """Show the statement line behind a sign the importer couldn't verify."""
    console.print(
        f"[yellow]Ambiguous line:[/yellow] "
        f"{txn.date}  {abs(txn.amount):>10.2f}  {txn.description}"
    )
    console.print("       Balance movement didn't reconcile - could not tell in vs out.")
    console.print(f"       {txn.raw_data or '(no raw statement line available)'}")


def _prompt_ambiguous_sign_action(amount: float) -> str:
    """Ask whether an unsignable amount is money in, money out, or skipped."""
    while True:
        try:
            response = input(
                f"       [i]n (+{amount:.2f}) / [o]ut (-{amount:.2f}) / "
                "[s]kip - I'll add it manually? "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "skip"  # unattended runs must not silently guess a sign
        if response == "i":
            return "positive"
        if response == "o":
            return "negative"
        if response == "s":
            return "skip"
        print("       Invalid choice. Enter i, o or s.")


def _prompt_day_conflict(txn_date, identity, existing, incoming) -> "DayConflictResolution | None":
    """Ask which transactions should end up in the database for a day where
    existing and incoming statement data disagree in a way that couldn't be
    resolved automatically (see import_service's day-level reconciliation).
    """
    console.print(f"\n[yellow]Day needs review:[/yellow] {identity} - {txn_date}")
    console.print("       Already recorded:")
    for t in existing:
        src = t.import_log.filename if t.import_log is not None else "unknown file"
        console.print(f"         {t.amount:>10.2f}  {t.description}  (from {src})")
    console.print("       In the file being imported:")
    for t in incoming:
        console.print(f"         {t.amount:>10.2f}  {t.description}")

    while True:
        try:
            response = input(
                "       Keep [e]xisting only / [i]ncoming only / [b]oth / "
                "[s]kip this day for now? "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None  # unattended runs must not guess - leave the day untouched
        if response == "e":
            return DayConflictResolution(keep_incoming=[], delete_existing_ids=[])
        if response == "i":
            return DayConflictResolution(
                keep_incoming=list(incoming), delete_existing_ids=[t.id for t in existing],
            )
        if response == "b":
            return DayConflictResolution(keep_incoming=list(incoming), delete_existing_ids=[])
        if response == "s":
            return None
        print("       Invalid choice. Enter e, i, b or s.")


def _prompt_column_mapping_cli(file_path: Path, rows: list):
    """Text version of the column-mapping fallback: show real rows from an
    unrecognised tabular file, ask which columns hold which values."""
    from mtd_classifier.importers.column_mapping import (
        ColumnMapping,
        guess_header_signature,
        parse_with_mapping,
    )

    n_cols = max((len(r) for r in rows[:30]), default=0)
    preview = [r for r in rows[:30] if any(str(c or "").strip() for c in r)][:8]

    console.print(
        f"\n[yellow]{file_path.name} isn't a recognised statement format.[/yellow] "
        "Map its columns (real rows from the file shown below):"
    )
    table = Table(show_lines=False)
    for i in range(n_cols):
        table.add_column(f"[{i + 1}]", overflow="fold", max_width=24)
    for r in preview:
        table.add_row(*[str(r[i]) if i < len(r) and r[i] is not None else "" for i in range(n_cols)])
    console.print(table)

    def ask_col(prompt, allow_blank=False, multi=False):
        while True:
            try:
                raw = input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                raise KeyboardInterrupt
            if not raw and allow_blank:
                return [] if multi else None
            try:
                nums = [int(p) for p in raw.replace(",", " ").split()]
            except ValueError:
                print("Enter column number(s), e.g. 1 or 2,3")
                continue
            if all(1 <= n <= n_cols for n in nums) and nums:
                return [n - 1 for n in nums] if multi else nums[0] - 1
            print(f"Column numbers must be 1-{n_cols}.")

    try:
        date_col = ask_col("Date column: ")
        desc_cols = ask_col("Description column(s), comma-separated: ", multi=True)
        amount_col = ask_col("Signed amount column (blank if separate in/out): ", allow_blank=True)
        debit_col = credit_col = None
        if amount_col is None:
            debit_col = ask_col("Paid out column (blank if none): ", allow_blank=True)
            credit_col = ask_col("Paid in column (blank if none): ", allow_blank=True)
        balance_col = ask_col("Balance column (blank if none): ", allow_blank=True)
    except KeyboardInterrupt:
        return None

    mapping = ColumnMapping(
        date_col=date_col, description_cols=desc_cols, amount_col=amount_col,
        debit_col=debit_col, credit_col=credit_col, balance_col=balance_col,
        name=file_path.stem,
    )
    if not mapping.is_valid():
        console.print("[red]Need at least a description and an amount (or paid out/in).[/red]")
        return None
    parsed = list(parse_with_mapping(rows, mapping))
    if not parsed:
        console.print("[red]That mapping doesn't parse any transactions - check the date column.[/red]")
        return None
    first = parsed[0]
    console.print(
        f"{len(parsed)} transactions parsed. First: "
        f"{first.date}  {first.amount:,.2f}  {first.description}"
    )
    try:
        if input("Import with this mapping? [y/N] ").strip().lower() not in ("y", "yes"):
            return None
    except (EOFError, KeyboardInterrupt):
        return None
    mapping.header_signature = guess_header_signature(rows, mapping)
    return mapping


def _prompt_confirm_period_cli(file_path, period, transactions):
    """Ask the user to confirm the date range an export covers when it was
    only estimated from its first and last transactions."""
    if period is None:
        return None
    start_guess, end_guess = period
    console.print(
        f"\n[yellow]Please confirm the date range you exported[/yellow] that is "
        f"represented by these transactions ({file_path.name}).\n"
        f"First transaction {start_guess}, last {end_guess} - the export may "
        "cover extra days on which nothing happened."
    )
    try:
        raw_start = input(f"Covers from [{start_guess}]: ").strip()
        raw_end = input(f"Covers to [{end_guess}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None  # unattended: keep the estimate
    try:
        start = date.fromisoformat(raw_start) if raw_start else start_guess
        end = date.fromisoformat(raw_end) if raw_end else end_guess
    except ValueError:
        console.print("[red]Not a YYYY-MM-DD date - keeping the transaction-date estimate.[/red]")
        return None
    if start > end or start > start_guess or end < end_guess:
        console.print(
            "[red]The range must include all transactions - keeping the estimate.[/red]"
        )
        return None
    return (start, end)


def _confirm_cross_format(repo: Repository, year_start: date, end_date: date) -> bool:
    """Gate HMRC output generation on the PDF vs CSV/XLS cross-check.

    Returns True to proceed: verification passed, or the user explicitly
    typed yes. Unattended runs (EOF) abort - generating HMRC figures from
    unverified data must be a deliberate human decision.
    """
    results = verify_cross_format(repo, year_start, end_date)
    report = verification_report(results)
    if not report:
        return True
    console.print(
        "[yellow]Statement verification failed[/yellow] - the transactions for "
        f"{year_start} to {end_date} couldn't be verified as complete:"
    )
    console.print(report)
    console.print(
        "Either re-export the transaction list (CSV/XLS) covering at least one "
        "day beyond each end of the period - an unbroken balance chain is proof "
        "on its own - or import PDF statements for a cross-check. Or proceed "
        "anyway at your own risk."
    )
    while True:
        try:
            response = input("Generate HMRC output anyway? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if response in ("y", "yes"):
            return True
        if response in ("", "n", "no"):
            return False
        print("Enter y or n.")


# --- REVIEW command ---

@app.command()
def review(
    period: Optional[str] = typer.Option(None, help="Tax quarter e.g. Q1-2026"),
    all_transactions: bool = typer.Option(False, "--all", help="Review all, including already categorised"),
) -> None:
    """Interactively review and categorise transactions."""
    repo = _get_repo()

    start_date = None
    end_date = None
    if period:
        tq = parse_period(period)
        start_date = tq.start_date
        end_date = tq.end_date
        console.print(f"Reviewing period: [cyan]{tq.label}[/cyan] ({start_date} to {end_date})")

    if all_transactions:
        transactions = list(repo.get_all_transactions_for_review(start_date, end_date))
    else:
        transactions = list(repo.get_uncategorised_transactions(start_date, end_date))

    if not transactions:
        console.print("[green]No transactions to review.[/green]")
        raise typer.Exit(0)

    # Build classifier from current rules
    rules = repo.get_active_rules()
    classifier = RuleBasedClassifier(db_rules=rules)

    reviewed = 0
    total = len(transactions)
    console.print(f"[cyan]{total}[/cyan] transactions to review.\n")

    for idx, txn in enumerate(transactions):
        # Only offer categories that match the money's direction
        categories = categories_for_amount(txn.amount)
        cat_menu = "\n".join(
            f"  {i+1:2d}. {CATEGORY_LABELS[c]}"
            for i, c in enumerate(categories)
        )
        existing_cat = repo.get_categorisation(txn.id)
        result = classifier.classify(txn.description, txn.amount)

        # Display transaction
        console.print(
            f"[bold][{idx+1}/{total}][/bold] "
            f"{txn.date}  {txn.amount:>10.2f}  {txn.description}"
        )

        if existing_cat:
            console.print(
                f"       Current: [yellow]{CATEGORY_LABELS.get(TransactionCategory(existing_cat.category), existing_cat.category)}[/yellow] "
                f"({existing_cat.confidence})"
            )

        suggested_label = CATEGORY_LABELS.get(
            TransactionCategory(result.category) if result.category in [c.value for c in TransactionCategory] else TransactionCategory.PERSONAL,
            result.category,
        )
        console.print(
            f"       Suggested: [green]{suggested_label}[/green] ({result.confidence} match)"
        )

        # Refund/reversal detection: an opposite-amount transaction nearby
        # suggests an expense reversed into the account or income sent back.
        refund_candidates = [
            c for c in repo.find_refund_candidates(txn.amount, txn.date)
            if c.id != txn.id
        ]
        if refund_candidates:
            console.print("       [magenta]Possible refund/reversal of:[/magenta]")
            for i, cand in enumerate(refund_candidates):
                cand_cat = repo.get_categorisation(cand.id)
                cat_note = ""
                if cand_cat:
                    cat_label = CATEGORY_LABELS.get(
                        TransactionCategory(cand_cat.category), cand_cat.category
                    )
                    cat_note = f"  [{cat_label}]"
                console.print(
                    f"         {i+1}. {cand.date}  {cand.amount:>10.2f}  "
                    f"{cand.description}{cat_note}"
                )

        # Prompt
        action = _prompt_action(allow_refund=bool(refund_candidates))
        if action == "q":
            console.print("Quit review.")
            break
        elif action == "s":
            continue
        elif action == "a":
            # Accept suggestion
            repo.set_categorisation(
                raw_transaction_id=txn.id,
                category=result.category,
                confidence=result.confidence,
                matched_rule_id=result.rule_id,
                changed_by="user",
            )
            if result.rule_id:
                repo.increment_rule_hit(result.rule_id)
            if result.category in [c.value for c in TransactionCategory]:
                _warn_if_needs_confirmation(TransactionCategory(result.category))
            reviewed += 1
        elif action == "c":
            # Change category
            console.print(cat_menu)
            choice = _prompt_category_choice(len(categories))
            if choice is not None:
                chosen_cat = categories[choice]
                repo.set_categorisation(
                    raw_transaction_id=txn.id,
                    category=chosen_cat.value,
                    confidence="manual",
                    changed_by="user",
                )
                # Learn from correction
                repo.upsert_exact_rule(txn.description, chosen_cat.value)
                classifier.learn(txn.description, txn.amount, chosen_cat.value)
                console.print(
                    f"       -> Set to [green]{CATEGORY_LABELS[chosen_cat]}[/green] "
                    f"(rule saved for future)"
                )
                _warn_if_needs_confirmation(chosen_cat)
                reviewed += 1
        elif action == "r":
            # Mark as refund of an earlier transaction: same category, so it
            # nets against the same HMRC box instead of counting as income
            if len(refund_candidates) == 1:
                cand_idx = 0
            else:
                cand_idx = _prompt_category_choice(len(refund_candidates))
            if cand_idx is not None:
                cand = refund_candidates[cand_idx]
                cand_cat = repo.get_categorisation(cand.id)
                if cand_cat is None:
                    console.print(
                        "       Original transaction is uncategorised - "
                        "pick the category for both:"
                    )
                    # The category describes the ORIGINAL transaction, so
                    # filter by its direction, not the refund's
                    cand_categories = categories_for_amount(cand.amount)
                    console.print("\n".join(
                        f"  {i+1:2d}. {CATEGORY_LABELS[c]}"
                        for i, c in enumerate(cand_categories)
                    ))
                    choice = _prompt_category_choice(len(cand_categories))
                    if choice is None:
                        console.print()
                        continue
                    chosen = cand_categories[choice].value
                    repo.set_categorisation(
                        raw_transaction_id=cand.id,
                        category=chosen,
                        confidence="manual",
                        changed_by="user",
                    )
                else:
                    chosen = cand_cat.category
                repo.set_categorisation(
                    raw_transaction_id=txn.id,
                    category=chosen,
                    confidence="refund",
                    changed_by="user",
                )
                chosen_label = CATEGORY_LABELS.get(TransactionCategory(chosen), chosen)
                console.print(
                    f"       -> Marked as refund of {cand.date} {cand.description}: "
                    f"[green]{chosen_label}[/green] (nets against the same HMRC box)"
                )
                _warn_if_needs_confirmation(TransactionCategory(chosen))
                reviewed += 1

        console.print()

    repo.commit()
    console.print(f"[green]Reviewed {reviewed} transactions.[/green]")


def _prompt_action(allow_refund: bool = False) -> str:
    """Prompt user for review action."""
    options = "[a]ccept / [c]hange / [s]kip / [q]uit"
    valid = ("a", "c", "s", "q")
    if allow_refund:
        options = "[a]ccept / [c]hange / [r]efund / [s]kip / [q]uit"
        valid = ("a", "c", "r", "s", "q")
    while True:
        try:
            response = input(f"       {options}? ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "q"
        if response in valid:
            return response
        print(f"       Invalid choice. Enter one of: {', '.join(valid)}.")


def _prompt_category_choice(max_choice: int) -> int | None:
    """Prompt user to pick a category number."""
    while True:
        try:
            response = input(f"       Enter number (1-{max_choice}): ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        try:
            num = int(response)
            if 1 <= num <= max_choice:
                return num - 1
        except ValueError:
            pass
        print(f"       Invalid choice. Enter 1-{max_choice}.")


# --- SUMMARY command ---

@app.command()
def summary(
    period: str = typer.Option(..., help="Tax quarter e.g. Q1-2026"),
    format: str = typer.Option("table", help="Output format: table or csv"),
) -> None:
    """Show cumulative (6 April to quarter end) summary for MTD filing.

    Cumulative because that's what HMRC quarterly updates actually contain -
    matches the figures 'mtd bridge' will submit. Use --this-quarter-only to
    see just this quarter's own transactions instead.
    """
    repo = _get_repo()
    tq = parse_period(period)
    year_start = date(tq.tax_year, 4, 6)

    cat_txns = repo.get_categorised_transactions_in_period(year_start, tq.end_date)
    all_txns = repo.get_transactions_in_period(year_start, tq.end_date)
    employment_record = repo.get_employment_income(tq.tax_year)

    qsummary = summarise_quarter(
        quarter=tq,
        transactions=cat_txns,
        total_in_period=len(all_txns),
        employment_income_record=employment_record,
    )

    if format == "csv":
        # Print CSV to stdout
        for ct in qsummary.reportable_totals():
            if ct.unconfirmed:
                console.print(f"{ct.label},UNCONFIRMED - see 'mtd employment show',***,***,***,{ct.transaction_count}")
                continue
            console.print(f"{ct.label},{ct.hmrc_box},{ct.total_income:.2f},{ct.total_expense:.2f},{ct.net_total:.2f},{ct.transaction_count}")
        return

    # Rich table output
    console.print(f"\n[bold]MTD Summary: {tq.label} (cumulative)[/bold]")
    console.print(
        f"Period: {year_start} to {tq.end_date} "
        f"(6 April to end of {tq.label} - matches what 'mtd bridge' submits)\n"
    )

    table = Table(title="Category Breakdown")
    table.add_column("Category", style="cyan")
    table.add_column("HMRC Box", style="dim")
    table.add_column("Income", justify="right", style="green")
    table.add_column("Expenses", justify="right", style="red")
    table.add_column("Net", justify="right", style="bold")
    table.add_column("Count", justify="right")

    for ct in qsummary.reportable_totals():
        if ct.unconfirmed:
            table.add_row(
                ct.label,
                "[red]UNCONFIRMED - see 'mtd employment show'[/red]",
                "[red]***[/red]", "[red]***[/red]", "[red]***[/red]",
                str(ct.transaction_count),
            )
            continue
        box_label = ct.hmrc_box
        if ct.confirmation_note:
            box_label = f"{box_label}\n[dim]{ct.confirmation_note}[/dim]"
        table.add_row(
            ct.label,
            box_label,
            f"{ct.total_income:,.2f}",
            f"{ct.total_expense:,.2f}",
            f"{ct.net_total:,.2f}",
            str(ct.transaction_count),
        )

    console.print(table)
    console.print()
    console.print(f"Total Income:   [green]{qsummary.total_income:>14,.2f}[/green]")
    console.print(f"Total Expenses: [red]{qsummary.total_expenses:>14,.2f}[/red]")
    console.print(f"Net Profit:     [bold]{qsummary.net_profit:>14,.2f}[/bold]")
    if qsummary.finance_costs > 0:
        console.print()
        console.print(
            f"Mortgage/finance costs (not deducted): {qsummary.finance_costs:>14,.2f}"
        )
        console.print(
            f"Estimated 20% tax reduction:           "
            f"[bold]{qsummary.estimated_finance_cost_relief:>14,.2f}[/bold] "
            "[dim](simplified estimate - see disclaimer)[/dim]"
        )
    console.print()
    console.print(f"Estimated Income Tax:  [red]{qsummary.estimated_income_tax:>14,.2f}[/red]")
    console.print(
        f"Estimated Net Income:  [bold]{qsummary.estimated_net_income_after_tax:>14,.2f}[/bold] "
        "[dim](after mortgage interest relief, allowable expenses and tax)[/dim]"
    )
    console.print(
        "[dim]Income tax estimate uses standard rest-of-UK bands on the profit "
        "above only - excludes National Insurance, Scottish rates, and any "
        "other income you have (employment, savings, dividends).[/dim]"
    )
    console.print()
    console.print(
        f"Transactions: {qsummary.categorised_count} categorised / "
        f"{qsummary.total_transactions} total"
    )
    if qsummary.uncategorised_count > 0:
        console.print(
            f"[yellow]Warning: {qsummary.uncategorised_count} uncategorised transactions[/yellow]"
        )
    if any(ct.unconfirmed for ct in qsummary.reportable_totals()):
        console.print(
            "[yellow]Note: Employment Income shown above is UNCONFIRMED. "
            "Bank deposits are net (after-tax) pay, not the P60/P45 gross "
            "figure. Quarterly updates never include it - run 'mtd employment "
            "set-income' before the year-end final declaration.[/yellow]"
        )
    console.print()
    console.print(f"[dim]{DISCLAIMER}[/dim]")


# --- EXPORT command ---

@app.command()
def export(
    period: str = typer.Option(..., help="Tax quarter e.g. Q1-2026"),
    output: Path = typer.Option("mtd_export.csv", help="Output CSV file path"),
) -> None:
    """Export cumulative (6 April to quarter end) data to CSV - matches what
    'mtd bridge' submits, and includes the disclaimer required for all figures."""
    repo = _get_repo()
    tq = parse_period(period)
    year_start = date(tq.tax_year, 4, 6)

    if not _confirm_cross_format(repo, year_start, tq.end_date):
        console.print("[yellow]Export cancelled.[/yellow]")
        raise typer.Exit(1)

    cat_txns = repo.get_categorised_transactions_in_period(year_start, tq.end_date)
    all_txns = repo.get_transactions_in_period(year_start, tq.end_date)
    employment_record = repo.get_employment_income(tq.tax_year)

    qsummary = summarise_quarter(
        quarter=tq,
        transactions=cat_txns,
        total_in_period=len(all_txns),
        employment_income_record=employment_record,
    )

    result_path = export_to_csv(qsummary, cat_txns, output)
    console.print(f"[green]Exported to {result_path}[/green] (cumulative {year_start} to {tq.end_date})")
    if any(ct.unconfirmed for ct in qsummary.reportable_totals()):
        console.print(
            "[yellow]Note: the exported Employment Income row is UNCONFIRMED "
            "(bank deposits are net pay, not your P60/P45 gross figure). "
            "Run 'mtd employment set-income' to fix this before using the export.[/yellow]"
        )
    if qsummary.finance_costs > 0:
        console.print(
            f"Estimated 20% mortgage interest tax reduction: "
            f"{qsummary.estimated_finance_cost_relief:,.2f} (see CSV for details)"
        )
    console.print(f"[dim]{DISCLAIMER}[/dim]")


# --- BRIDGE command ---

@app.command()
def bridge(
    period: str = typer.Option(..., help="Tax quarter e.g. Q1-2026 (output is cumulative from 6 April)"),
    output_dir: Path = typer.Option(Path("."), help="Directory for the filled bridging templates"),
    form: str = typer.Option("both", help="Which template to produce: sa103, sa105 or both"),
) -> None:
    """Fill My Tax Digital SA103/SA105 bridging templates for an MTD quarterly update.

    HMRC quarterly updates contain cumulative year-to-date figures, so this
    aggregates every categorised transaction from 6 April up to the end of the
    given quarter and writes the totals into the official My Tax Digital
    templates, ready to upload in Bridging mode (Import figures).
    """
    from mtd_classifier.export.mytaxdigital import export_bridging_templates

    form = form.lower()
    if form not in ("sa103", "sa105", "both"):
        console.print("[red]--form must be sa103, sa105 or both[/red]")
        raise typer.Exit(1)
    forms = ("SA103", "SA105") if form == "both" else (form.upper(),)

    repo = _get_repo()
    tq = parse_period(period)
    year_start = date(tq.tax_year, 4, 6)

    if not _confirm_cross_format(repo, year_start, tq.end_date):
        console.print("[yellow]Bridging cancelled.[/yellow]")
        raise typer.Exit(1)

    cat_txns = repo.get_categorised_transactions_in_period(year_start, tq.end_date)
    all_txns = repo.get_transactions_in_period(year_start, tq.end_date)

    console.print(
        f"Cumulative period: [cyan]{year_start}[/cyan] to [cyan]{tq.end_date}[/cyan] "
        f"(tax year {tq.tax_year}-{(tq.tax_year + 1) % 100:02d}, up to end of {tq.label})"
    )

    uncategorised = len(all_txns) - len(cat_txns)
    if uncategorised > 0:
        console.print(
            f"[yellow]Warning: {uncategorised} uncategorised transactions in this period "
            f"are NOT included. Run 'mtd review' first.[/yellow]"
        )

    result = export_bridging_templates(
        cat_txns, output_dir, tq.label, forms=forms, tax_year=tq.tax_year,
    )

    table = Table(title="Figures written to templates (cumulative year-to-date)")
    table.add_column("Form")
    table.add_column("Box", justify="right")
    table.add_column("Description", style="cyan")
    table.add_column("Amount", justify="right", style="bold")
    table.add_column("Txns", justify="right")

    for bt in sorted(
        result.box_totals.values(), key=lambda b: (b.box_cell.form, b.box_cell.cell)
    ):
        table.add_row(
            bt.box_cell.form,
            bt.box_cell.box,
            bt.box_cell.label,
            f"{bt.amount:,.2f}",
            str(bt.transaction_count),
        )
    console.print(table)

    from mtd_classifier.models.category import NON_REPORTABLE_CATEGORIES

    non_reportable = {c.value for c in NON_REPORTABLE_CATEGORIES}
    skipped_reportable = [
        c for c in result.skipped_categories if c not in non_reportable
    ]
    if skipped_reportable:
        console.print(
            f"[yellow]Warning: categories with no template box (not exported): "
            f"{', '.join(skipped_reportable)}[/yellow]"
        )

    if not result.files_written:
        console.print("[yellow]No reportable figures found - nothing written.[/yellow]")
        raise typer.Exit(0)

    for path in result.files_written:
        console.print(f"[green]Written: {path}[/green]")
    rar = result.box_totals.get("SA105:I33")
    if rar is not None:
        console.print(
            f"[yellow]Rent-a-Room exempt amount (box 37) auto-filled: "
            f"{rar.amount:,.2f} = min(receipts to date, annual limit). If "
            "someone else shares that income the limit halves to 3,750.00 - "
            "adjust the figure in My Tax Digital before submitting.[/yellow]"
        )
    # Unconfirmed employment income never blocks bridging - it isn't part of
    # quarterly updates (HMRC gets it via PAYE) - but remind about the
    # year-end final declaration when salary deposits are in scope.
    if repo.get_employment_income(tq.tax_year) is None and any(
        ct.category == "employment_income" for _, ct in cat_txns
    ):
        console.print(
            "[yellow]Reminder: Employment Income for this year is not yet "
            "confirmed. It is never part of quarterly updates, so these files "
            "are complete and fine to upload - but run 'mtd employment "
            "set-income' before the year-end final declaration.[/yellow]"
        )
    console.print(
        "\nNext: log in to mytaxdigital.co.uk, open the income source, ensure "
        "Bridging mode, choose the open obligation, click 'Import figures', "
        f"upload the file and set the 'to' date to {tq.end_date}, then review and Submit."
    )
    console.print()
    console.print(f"[dim]{DISCLAIMER}[/dim]")


# --- REFINE-CATEGORIES command ---

@app.command("refine-categories")
def refine_categories(
    categories: str = typer.Option(
        "se_other_expense,property_other_expense",
        help="Comma-separated catch-all categories to send back for review",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would change without changing anything"
    ),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt"),
) -> None:
    """Send catch-all categorised transactions back for review against the
    refined category scheme (full SA103/SA105 box coverage).

    Transactions lumped into the given catch-all categories are
    uncategorised (audit-logged), and the learned exact rules that would
    re-apply the old category on the next auto-classify are deactivated -
    so Auto-classify and manual review can redistribute them into the
    finer categories.
    """
    valid = {c.value for c in TransactionCategory}
    cats = [c.strip() for c in categories.split(",") if c.strip()]
    unknown = [c for c in cats if c not in valid]
    if unknown:
        console.print(f"[red]Unknown categories: {', '.join(unknown)}[/red]")
        raise typer.Exit(1)

    repo = _get_repo()
    counts = repo.count_categorised_by_categories(cats)
    if not counts:
        console.print("Nothing to do - no transactions in those categories.")
        return
    for cat_value, n in sorted(counts.items()):
        label = CATEGORY_LABELS.get(TransactionCategory(cat_value), cat_value)
        console.print(f"  {label}: {n} transaction(s)")
    total = sum(counts.values())
    if dry_run:
        console.print(
            f"[yellow]Dry run - {total} transaction(s) would be sent back "
            "for review; nothing changed.[/yellow]"
        )
        return
    if not yes and not typer.confirm(
        f"Uncategorise these {total} transaction(s) for re-review?"
    ):
        console.print("[yellow]Cancelled.[/yellow]")
        return
    result = repo.uncategorise_by_categories(cats, changed_by="refine-categories")
    repo.commit()
    console.print(
        f"[green]{result['transactions']} transaction(s) sent back for review; "
        f"{result['rules_deactivated']} learned rule(s) deactivated.[/green]"
    )
    console.print(
        "Next: run Auto-classify (GUI) or 'mtd review' - built-in rules now "
        "place ground rent, service charges, council tax and water rates in "
        "box 24 (Rent, Rates & Insurance); pick the refined categories for "
        "the rest."
    )


# --- USERS commands ---

users_app = typer.Typer(help="Manage users - each has fully isolated data.")
app.add_typer(users_app, name="users")


@users_app.command("list")
def users_list() -> None:
    """List registered users."""
    import mtd_classifier.config as config

    last = config.last_user()
    for name in config.list_users():
        marker = " (last used)" if name == last else ""
        console.print(f"{name}{marker}")


@users_app.command("add")
def users_add(name: str = typer.Argument(..., help="Name for the new user")) -> None:
    """Register a new user with a fresh, isolated data directory."""
    import mtd_classifier.config as config

    try:
        config.add_user(name)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]Added user {name!r}.[/green]")


@users_app.command("rename")
def users_rename(
    old: str = typer.Argument(..., help="Current name"),
    new: str = typer.Argument(..., help="New name"),
) -> None:
    """Rename a user (their data stays put)."""
    import mtd_classifier.config as config

    try:
        config.rename_user(old, new)
    except (ValueError, KeyError) as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]Renamed {old!r} to {new!r}.[/green]")


# --- SHELL (continuous session) ---

@app.command()
def shell() -> None:
    """Continuous session: select the user once, then run commands without
    re-selecting. 'switch-user' changes user, 'exit' leaves."""
    import shlex

    import mtd_classifier.config as config

    global _user_resolved
    _ensure_user()
    console.print(
        f"Continuous session as [cyan]{config.ACTIVE_USER}[/cyan]. "
        "Type commands without the leading 'mtd' (e.g. 'summary --period Q1-2026'), "
        "'switch-user' to change user, 'exit' to quit."
    )
    click_cmd = typer.main.get_command(app)
    while True:
        try:
            line = input(f"mtd [{config.ACTIVE_USER}]> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line in ("exit", "quit"):
            break
        if line == "switch-user":
            _user_resolved = False
            _ensure_user()
            continue
        if line.split()[0] == "shell":
            console.print("[yellow]Already in a session.[/yellow]")
            continue
        try:
            args = shlex.split(line)
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            continue
        try:
            click_cmd.main(args=args, prog_name="mtd", standalone_mode=False)
        except SystemExit:
            pass
        except Exception as e:  # a failed command must not end the session
            console.print(f"[red]{type(e).__name__}: {e}[/red]")


# --- EMPLOYMENT command ---

employment_app = typer.Typer(
    help="Confirm the P60/P45 gross figure for Employment Income (never derived from bank deposits)."
)
app.add_typer(employment_app, name="employment")


@employment_app.command("show")
def employment_show(
    tax_year: int = typer.Option(..., help="Tax year, e.g. 2026 for 2026-27"),
) -> None:
    """Show the confirmed employment income figure for a tax year, if any."""
    repo = _get_repo()
    record = repo.get_employment_income(tax_year)
    if record is None:
        console.print(
            f"[yellow]No confirmed employment income for tax year {tax_year}-"
            f"{(tax_year + 1) % 100:02d}.[/yellow]"
        )
        return
    console.print(
        f"Tax year {tax_year}-{(tax_year + 1) % 100:02d}: "
        f"[green]{record.gross_amount:,.2f}[/green] "
        f"(source: {record.source}, confirmed {record.confirmed_at:%Y-%m-%d})"
    )
    if record.note:
        console.print(f"Note: {record.note}")


@employment_app.command("set-income")
def employment_set_income(
    tax_year: int = typer.Option(..., help="Tax year, e.g. 2026 for 2026-27"),
    gross: Optional[float] = typer.Option(
        None, help="Confirmed gross pay figure from your P60/P45"
    ),
    from_pdf: Optional[Path] = typer.Option(
        None, "--from-pdf", help="Extract candidate figures from a payslip/P60 PDF"
    ),
    note: Optional[str] = typer.Option(None, help="Optional note, e.g. 'P60 2025-26'"),
) -> None:
    """Confirm the gross employment income figure for a tax year.

    Either pass --gross directly, or --from-pdf to scan a payslip/P60 for
    candidate figures - you still choose which one is correct (or enter it
    manually); nothing is saved automatically from the PDF.
    """
    if gross is None and from_pdf is None:
        console.print("[red]Provide --gross or --from-pdf.[/red]")
        raise typer.Exit(1)

    source = "manual"
    chosen_amount = gross
    chosen_note = note

    if from_pdf is not None:
        if not from_pdf.exists():
            console.print(f"[red]File not found: {from_pdf}[/red]")
            raise typer.Exit(1)

        from mtd_classifier.services.payslip_extractor import (
            detect_document_type, extract_candidates,
        )

        doc_type = detect_document_type(from_pdf)
        candidates = extract_candidates(from_pdf)
        if doc_type == "p60":
            console.print(f"Detected [bold]P60[/bold] in {from_pdf.name} - the figure "
                          "you pick is used as the FULL YEAR gross pay.")
        else:
            console.print(f"Detected [bold]payslip[/bold] in {from_pdf.name} - the "
                          "figure you pick is THIS PERIOD's gross pay and will be "
                          "multiplied by 12 for a full-year figure.")
        if candidates:
            console.print(f"Found {len(candidates)} candidate figure(s):")
            for i, c in enumerate(candidates):
                period = "full year" if c.is_annual else "this period"
                console.print(f"  {i+1:2d}. {c.label} ({period}): {c.amount:,.2f}   ({c.line})")
            console.print(f"  {len(candidates)+1:2d}. Enter manually instead")
            choice = _prompt_category_choice(len(candidates) + 1)
            if choice is None:
                console.print("Cancelled.")
                raise typer.Exit(0)
            if choice < len(candidates):
                picked = candidates[choice]
                source = "payslip_pdf"
                if picked.is_annual:
                    chosen_amount = picked.amount
                    chosen_note = note or f"{picked.label} (P60, full year) from {from_pdf.name}: {picked.line}"
                else:
                    chosen_amount = round(picked.amount * 12, 2)
                    chosen_note = note or (
                        f"{picked.label} (payslip, monthly) x12 from "
                        f"{from_pdf.name}: {picked.line}"
                    )
                    console.print(
                        f"Monthly figure {picked.amount:,.2f} x 12 = "
                        f"[bold]{chosen_amount:,.2f}[/bold] for the full year."
                    )
            elif gross is None:
                console.print("[red]No figure chosen. Re-run with --gross to enter manually.[/red]")
                raise typer.Exit(1)
        elif gross is None:
            console.print(
                f"[yellow]No candidate figures found in {from_pdf.name}. "
                "Re-run with --gross to enter the figure manually.[/yellow]"
            )
            raise typer.Exit(1)

    console.print(
        f"About to confirm: [bold]{chosen_amount:,.2f}[/bold] as the gross employment "
        f"income for tax year {tax_year}-{(tax_year + 1) % 100:02d} (source: {source})."
    )
    if not typer.confirm("Is this the correct figure from your P60/P45/payslip?"):
        console.print("Cancelled - nothing saved.")
        raise typer.Exit(0)

    repo = _get_repo()
    repo.set_employment_income(
        tax_year=tax_year, gross_amount=chosen_amount, source=source, note=chosen_note
    )
    repo.commit()
    console.print(f"[green]Confirmed employment income for {tax_year}-{(tax_year + 1) % 100:02d}.[/green]")


# --- ATTACHMENTS command ---

attachments_app = typer.Typer(help="Attach receipts/invoices (PDF or image) to a transaction.")
app.add_typer(attachments_app, name="attachments")


@attachments_app.command("add")
def attachments_add(
    transaction_id: int = typer.Argument(..., help="Raw transaction id (see 'mtd review --all')"),
    file: Path = typer.Argument(..., help="Receipt/invoice file: PDF or image"),
) -> None:
    """Attach a receipt/invoice file to a transaction."""
    from mtd_classifier.services.attachments import attach_file, UnsupportedAttachmentType

    repo = _get_repo()
    txns = repo.get_all_transactions_for_review()
    if not any(t.id == transaction_id for t in txns):
        console.print(f"[red]No transaction with id {transaction_id}[/red]")
        raise typer.Exit(1)

    try:
        attachment = attach_file(repo, transaction_id, file)
    except FileNotFoundError:
        console.print(f"[red]File not found: {file}[/red]")
        raise typer.Exit(1)
    except UnsupportedAttachmentType as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    repo.commit()
    console.print(
        f"[green]Attached {attachment.original_filename} to transaction {transaction_id}[/green]"
    )


@attachments_app.command("list")
def attachments_list(
    transaction_id: int = typer.Argument(..., help="Raw transaction id"),
) -> None:
    """List attachments for a transaction."""
    repo = _get_repo()
    attachments = repo.get_attachments_for_transaction(transaction_id)
    if not attachments:
        console.print("No attachments.")
        return

    table = Table(title=f"Attachments for transaction {transaction_id}")
    table.add_column("ID", justify="right")
    table.add_column("Filename")
    table.add_column("Type")
    table.add_column("Added")
    for a in attachments:
        table.add_row(str(a.id), a.original_filename, a.content_type, f"{a.added_at:%Y-%m-%d}")
    console.print(table)


@attachments_app.command("open")
def attachments_open(
    attachment_id: int = typer.Argument(..., help="Attachment id (see 'mtd attachments list')"),
) -> None:
    """Open an attachment with the OS default viewer."""
    from mtd_classifier.services.attachments import open_attachment

    repo = _get_repo()
    attachment = repo.get_attachment(attachment_id)
    if attachment is None:
        console.print(f"[red]No attachment with id {attachment_id}[/red]")
        raise typer.Exit(1)
    try:
        open_attachment(attachment)
    except FileNotFoundError:
        console.print("[red]The attached file is missing from disk.[/red]")
        raise typer.Exit(1)


@attachments_app.command("remove")
def attachments_remove(
    attachment_id: int = typer.Argument(..., help="Attachment id (see 'mtd attachments list')"),
) -> None:
    """Remove an attachment (deletes the stored file)."""
    from mtd_classifier.services.attachments import delete_attachment

    repo = _get_repo()
    attachment = repo.get_attachment(attachment_id)
    if attachment is None:
        console.print(f"[red]No attachment with id {attachment_id}[/red]")
        raise typer.Exit(1)
    delete_attachment(repo, attachment)
    repo.commit()
    console.print(f"[green]Removed {attachment.original_filename}[/green]")


# --- GUI command ---

@app.command()
def gui() -> None:
    """Launch the graphical interface."""
    from mtd_classifier.gui.app import main as gui_main

    gui_main()


# --- RULES command ---

rules_app = typer.Typer(help="Manage classification rules.")
app.add_typer(rules_app, name="rules")


@rules_app.command("list")
def rules_list() -> None:
    """List all classification rules."""
    repo = _get_repo()
    all_rules = repo.get_all_rules()

    if not all_rules:
        console.print("No rules configured.")
        return

    table = Table(title="Classification Rules")
    table.add_column("ID", justify="right")
    table.add_column("Type")
    table.add_column("Match Value")
    table.add_column("Category")
    table.add_column("Priority", justify="right")
    table.add_column("Hits", justify="right")
    table.add_column("Active")

    for rule in all_rules:
        table.add_row(
            str(rule.id),
            rule.rule_type,
            rule.match_value[:50],
            rule.category,
            str(rule.priority),
            str(rule.hit_count),
            "Yes" if rule.is_active else "No",
        )

    console.print(table)


@rules_app.command("add")
def rules_add(
    rule_type: str = typer.Option(..., help="Rule type: exact or pattern"),
    match_value: str = typer.Option(..., help="Value to match (exact string or regex pattern)"),
    category: str = typer.Option(..., help="Category to assign"),
    priority: int = typer.Option(50, help="Priority (higher = checked first)"),
) -> None:
    """Add a new classification rule."""
    # Validate category
    valid_categories = [c.value for c in TransactionCategory]
    if category not in valid_categories:
        console.print(f"[red]Invalid category:[/red] {category}")
        console.print(f"Valid categories: {', '.join(valid_categories)}")
        raise typer.Exit(1)

    if rule_type not in ("exact", "pattern"):
        console.print("[red]Rule type must be 'exact' or 'pattern'[/red]")
        raise typer.Exit(1)

    repo = _get_repo()
    rule = repo.create_rule(
        rule_type=rule_type,
        match_value=match_value.lower() if rule_type == "exact" else match_value,
        category=category,
        priority=priority,
    )
    repo.commit()
    console.print(f"[green]Created rule #{rule.id}[/green]")


@rules_app.command("delete")
def rules_delete(
    rule_id: int = typer.Argument(..., help="Rule ID to delete"),
) -> None:
    """Delete (deactivate) a classification rule."""
    repo = _get_repo()
    if repo.delete_rule(rule_id):
        repo.commit()
        console.print(f"[green]Deactivated rule #{rule_id}[/green]")
    else:
        console.print(f"[red]Rule #{rule_id} not found[/red]")


if __name__ == "__main__":
    app()
