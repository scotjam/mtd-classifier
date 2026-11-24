"""CRUD operations for all models."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Optional, Sequence

from sqlalchemy import select, func, delete
from sqlalchemy.orm import Session

from mtd_classifier.models.import_log import ImportLog
from mtd_classifier.models.transaction import RawTransaction, CategorisedTransaction
from mtd_classifier.models.rule import ClassificationRule
from mtd_classifier.models.audit import AuditLog
from mtd_classifier.models.employment_income import (
    EmploymentGrossEntry,
    EmploymentIncomeRecord,
)
from mtd_classifier.models.attachment import Attachment


class ImportOutOfOrderError(Exception):
    """Raised by undo_import when a newer import exists for the same account.

    A later import may have skipped some of its own rows as duplicates of
    an earlier import's transactions (they were never linked to the later
    import_log_id). Undoing the earlier import while the later one still
    stands would delete transactions the later import's source file still
    logically contains. Imports must be undone in reverse (most recent
    first) order per account to stay safe.
    """

    def __init__(self, import_log_id: int, blocking_log_id: int):
        self.import_log_id = import_log_id
        self.blocking_log_id = blocking_log_id
        super().__init__(
            f"Cannot undo import {import_log_id}: a more recent import "
            f"({blocking_log_id}) exists for this account. Undo imports in "
            "reverse order (most recent first)."
        )


class Repository:
    """Database CRUD operations."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # --- Import Logs ---

    def get_import_log_by_hash(self, file_hash: str) -> ImportLog | None:
        stmt = select(ImportLog).where(ImportLog.file_hash == file_hash)
        return self.session.execute(stmt).scalar_one_or_none()

    def create_import_log(
        self,
        filename: str,
        file_hash: str,
        bank: str,
        fmt: str,
        record_count: int = 0,
        new_record_count: int = 0,
        period_start: date | None = None,
        period_end: date | None = None,
        account: str | None = None,
        source_path: str | None = None,
    ) -> ImportLog:
        log = ImportLog(
            filename=filename,
            file_hash=file_hash,
            bank=bank,
            format=fmt,
            record_count=record_count,
            new_record_count=new_record_count,
            period_start=period_start,
            period_end=period_end,
            account=account,
            source_path=source_path,
        )
        self.session.add(log)
        self.session.flush()
        return log

    def get_import_logs_by_account(
        self, bank: str | None, account: str | None
    ) -> Sequence[ImportLog]:
        """Import logs for one account via the log's own account column
        (set at import time), including files whose every transaction
        deduped away - their statement coverage still counts."""
        stmt = select(ImportLog)
        if account is not None:
            stmt = stmt.where(ImportLog.account == account)
        elif bank is not None:
            stmt = stmt.where(ImportLog.bank == bank).where(ImportLog.account.is_(None))
        else:
            return []
        return self.session.execute(stmt).scalars().all()

    def get_import_logs_for_account(
        self, bank: str | None, account: str | None
    ) -> Sequence[ImportLog]:
        """Import history relevant to one account, newest first.

        Includes imports that added no transactions (every line was already
        recorded) - they still verified the period, and hiding them made a
        multi-file import look like most files never went in. Such logs are
        found by their recorded ``account``; older logs predating that
        column are found via the transactions they created.
        """
        via_txns = select(RawTransaction.import_log_id)
        if account is not None:
            via_txns = via_txns.where(RawTransaction.account == account)
            own = ImportLog.account == account
        elif bank is not None:
            via_txns = via_txns.where(RawTransaction.bank == bank).where(
                RawTransaction.account.is_(None)
            )
            own = (ImportLog.account.is_(None)) & (ImportLog.bank == bank)
        else:
            return []
        # id desc as tie-break: imports in the same second share an
        # imported_at, but id strictly increases in import order.
        stmt = select(ImportLog).where(own | ImportLog.id.in_(via_txns)).order_by(
            ImportLog.imported_at.desc(), ImportLog.id.desc()
        )
        return self.session.execute(stmt).scalars().all()

    def count_current_transactions_for_import(self, import_log_id: int) -> int:
        """How many transactions from this import still exist (undo removes
        exactly these - it never touches transactions from OTHER imports,
        including ones that were skipped as duplicates of this one)."""
        stmt = select(func.count(RawTransaction.id)).where(
            RawTransaction.import_log_id == import_log_id
        )
        return self.session.execute(stmt).scalar_one()

    def undo_import(self, import_log_id: int) -> dict:
        """Remove every transaction this import actually created, along with
        their categorisations, audit entries and attachments, then remove
        the import log itself (freeing the file hash so it can be
        re-imported later if wanted).

        Transactions that were SKIPPED as duplicates during this import are
        untouched - they were never linked to this import_log_id (they still
        belong to whichever import first added them), so this can never
        remove a transaction that predates this import.

        Must be called in reverse (most recent first) order per account -
        raises ImportOutOfOrderError if a newer import exists for the same
        account, since that newer import may have skipped some of its own
        rows as duplicates of this one, and deleting this one's rows would
        then remove transactions the newer import's file still contains.

        Returns a summary dict; the caller is responsible for deleting any
        attachment files on disk (paths are returned under "attachment_paths"
        so this stays independent of the filesystem service layer).
        """
        from mtd_classifier.services.attachments import attachment_path

        log = self.session.get(ImportLog, import_log_id)

        txn_ids = [
            row[0] for row in self.session.execute(
                select(RawTransaction.id).where(
                    RawTransaction.import_log_id == import_log_id
                )
            ).all()
        ]

        if txn_ids and log is not None:
            sample = self.session.get(RawTransaction, txn_ids[0])
            # ImportLog.id increases in import order, so this is equivalent
            # to comparing timestamps but immune to timestamp-precision ties.
            scope_stmt = select(ImportLog.id).join(
                RawTransaction, RawTransaction.import_log_id == ImportLog.id
            ).where(ImportLog.id > import_log_id)
            if sample.account is not None:
                scope_stmt = scope_stmt.where(RawTransaction.account == sample.account)
            else:
                scope_stmt = scope_stmt.where(
                    RawTransaction.bank == sample.bank
                ).where(RawTransaction.account.is_(None))
            blocking_id = self.session.execute(
                scope_stmt.distinct().order_by(ImportLog.id.asc()).limit(1)
            ).scalar_one_or_none()
            if blocking_id is not None:
                raise ImportOutOfOrderError(import_log_id, blocking_id)

        attachment_paths = []
        if txn_ids:
            attachments = self.session.execute(
                select(Attachment).where(Attachment.raw_transaction_id.in_(txn_ids))
            ).scalars().all()
            attachment_paths = [attachment_path(a) for a in attachments]

            # SQLite FK enforcement isn't enabled for this app, so dependent
            # rows must be removed explicitly - otherwise they'd be orphaned.
            self.session.execute(
                delete(CategorisedTransaction).where(
                    CategorisedTransaction.raw_transaction_id.in_(txn_ids)
                )
            )
            self.session.execute(
                delete(AuditLog).where(AuditLog.raw_transaction_id.in_(txn_ids))
            )
            self.session.execute(
                delete(Attachment).where(Attachment.raw_transaction_id.in_(txn_ids))
            )
            self.session.execute(
                delete(EmploymentGrossEntry).where(
                    EmploymentGrossEntry.raw_transaction_id.in_(txn_ids)
                )
            )
            self.session.execute(
                delete(RawTransaction).where(RawTransaction.id.in_(txn_ids))
            )

        log = self.session.get(ImportLog, import_log_id)
        filename = log.filename if log else ""
        if log is not None:
            self.session.delete(log)
        self.session.flush()

        return {
            "filename": filename,
            "transactions_removed": len(txn_ids),
            "attachment_paths": attachment_paths,
        }

    # --- Raw Transactions ---

    def get_raw_transaction_by_hash(self, dedup_hash: str) -> RawTransaction | None:
        stmt = select(RawTransaction).where(
            RawTransaction.dedup_hash == dedup_hash
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def create_raw_transaction(
        self,
        import_log_id: int,
        bank: str,
        txn_date: date,
        description: str,
        amount: float,
        dedup_hash: str,
        account: str | None = None,
        balance: float | None = None,
        raw_data: str | None = None,
        source_formats: str = "",
    ) -> RawTransaction:
        txn = RawTransaction(
            import_log_id=import_log_id,
            bank=bank,
            account=account,
            date=txn_date,
            description=description,
            amount=amount,
            balance=balance,
            raw_data=raw_data,
            dedup_hash=dedup_hash,
            source_formats=source_formats,
        )
        self.session.add(txn)
        self.session.flush()
        return txn

    def has_categorisation_or_attachment(self, txn_ids: Sequence[int]) -> bool:
        """Whether any of these transactions already carry user work
        (a categorisation or an attachment) - checked before automatically
        deleting transactions superseded by a fuller re-import, so that work
        is never silently lost; it escalates to a conflict needing a human
        decision instead."""
        txn_ids = list(txn_ids)
        if not txn_ids:
            return False
        has_cat = self.session.execute(
            select(CategorisedTransaction.id).where(
                CategorisedTransaction.raw_transaction_id.in_(txn_ids)
            ).limit(1)
        ).first()
        if has_cat is not None:
            return True
        has_attachment = self.session.execute(
            select(Attachment.id).where(
                Attachment.raw_transaction_id.in_(txn_ids)
            ).limit(1)
        ).first()
        return has_attachment is not None

    def delete_raw_transactions(self, txn_ids: Sequence[int]) -> dict:
        """Remove specific transactions and their categorisation/audit/
        attachment rows, without touching the import log(s) they came from
        beyond adjusting the recorded counts.

        Used when a day's transactions from an earlier, less-complete import
        are superseded by a fuller re-import of the same real-world
        transactions in a different file format. Callers should check
        ``has_categorisation_or_attachment`` first - this method does not
        guard against deleting categorised/attached work.
        """
        from mtd_classifier.services.attachments import attachment_path

        txn_ids = list(txn_ids)
        if not txn_ids:
            return {"transactions_removed": 0, "attachment_paths": []}

        log_counts = Counter(
            row[0] for row in self.session.execute(
                select(RawTransaction.import_log_id).where(RawTransaction.id.in_(txn_ids))
            ).all()
        )

        attachments = self.session.execute(
            select(Attachment).where(Attachment.raw_transaction_id.in_(txn_ids))
        ).scalars().all()
        attachment_paths = [attachment_path(a) for a in attachments]

        self.session.execute(
            delete(CategorisedTransaction).where(
                CategorisedTransaction.raw_transaction_id.in_(txn_ids)
            )
        )
        self.session.execute(
            delete(AuditLog).where(AuditLog.raw_transaction_id.in_(txn_ids))
        )
        self.session.execute(
            delete(Attachment).where(Attachment.raw_transaction_id.in_(txn_ids))
        )
        self.session.execute(
            delete(EmploymentGrossEntry).where(
                EmploymentGrossEntry.raw_transaction_id.in_(txn_ids)
            )
        )
        self.session.execute(
            delete(RawTransaction).where(RawTransaction.id.in_(txn_ids))
        )

        for log_id, count in log_counts.items():
            log = self.session.get(ImportLog, log_id)
            if log is not None:
                log.new_record_count = max(0, log.new_record_count - count)
                log.record_count = max(0, log.record_count - count)

        self.session.flush()
        return {"transactions_removed": len(txn_ids), "attachment_paths": attachment_paths}

    def get_transactions_in_period(
        self,
        start_date: date,
        end_date: date,
    ) -> Sequence[RawTransaction]:
        stmt = (
            select(RawTransaction)
            .where(RawTransaction.date >= start_date)
            .where(RawTransaction.date <= end_date)
            .order_by(RawTransaction.date)
        )
        return self.session.execute(stmt).scalars().all()

    def find_refund_candidates(
        self,
        amount: float,
        on_or_before: date,
        window_days: int = 90,
        limit: int = 3,
    ) -> Sequence[RawTransaction]:
        """Find transactions that a given transaction could be refunding.

        Returns transactions with the opposite amount, dated on or before the
        given date within the lookback window, newest first. Covers both an
        expense reversed back into the account (positive amount refunding an
        earlier negative) and income sent back (negative amount refunding an
        earlier positive).
        """
        if amount == 0:
            return []
        target = -amount
        stmt = (
            select(RawTransaction)
            .where(RawTransaction.date <= on_or_before)
            .where(RawTransaction.date >= on_or_before - timedelta(days=window_days))
            .where(RawTransaction.amount >= target - 0.005)
            .where(RawTransaction.amount <= target + 0.005)
            .order_by(RawTransaction.date.desc(), RawTransaction.id.desc())
            .limit(limit)
        )
        return self.session.execute(stmt).scalars().all()

    def get_distinct_accounts(self) -> Sequence[tuple[str, str | None]]:
        """Return distinct (bank, account) pairs present in the database."""
        stmt = (
            select(RawTransaction.bank, RawTransaction.account)
            .distinct()
            .order_by(RawTransaction.bank, RawTransaction.account)
        )
        return [(row[0], row[1]) for row in self.session.execute(stmt).all()]

    def get_transactions_for_account(
        self,
        bank: str | None,
        account: str | None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> Sequence[RawTransaction]:
        """All transactions for one account, oldest first.

        With an account label, matches on the label alone (a statement from a
        different bank can be assigned to the account deliberately). With only
        a bank, matches that bank's unlabelled transactions.
        """
        stmt = select(RawTransaction)
        if account is not None:
            stmt = stmt.where(RawTransaction.account == account)
        elif bank is not None:
            stmt = stmt.where(RawTransaction.bank == bank)
            stmt = stmt.where(RawTransaction.account.is_(None))
        else:
            raise ValueError("Provide a bank or an account label")
        if start_date is not None:
            stmt = stmt.where(RawTransaction.date >= start_date)
        if end_date is not None:
            stmt = stmt.where(RawTransaction.date <= end_date)
        stmt = stmt.order_by(RawTransaction.date, RawTransaction.id)
        return self.session.execute(stmt).scalars().all()

    def get_uncategorised_transactions(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> Sequence[RawTransaction]:
        stmt = (
            select(RawTransaction)
            .outerjoin(CategorisedTransaction)
            .where(CategorisedTransaction.id.is_(None))
            .order_by(RawTransaction.date)
        )
        if start_date is not None:
            stmt = stmt.where(RawTransaction.date >= start_date)
        if end_date is not None:
            stmt = stmt.where(RawTransaction.date <= end_date)
        return self.session.execute(stmt).scalars().all()

    def get_all_transactions_for_review(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> Sequence[RawTransaction]:
        stmt = select(RawTransaction).order_by(RawTransaction.date)
        if start_date is not None:
            stmt = stmt.where(RawTransaction.date >= start_date)
        if end_date is not None:
            stmt = stmt.where(RawTransaction.date <= end_date)
        return self.session.execute(stmt).scalars().all()

    # --- Categorised Transactions ---

    def get_categorisation(
        self, raw_transaction_id: int
    ) -> CategorisedTransaction | None:
        stmt = select(CategorisedTransaction).where(
            CategorisedTransaction.raw_transaction_id == raw_transaction_id
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def set_categorisation(
        self,
        raw_transaction_id: int,
        category: str,
        confidence: str,
        matched_rule_id: int | None = None,
        changed_by: str = "system",
        disallowable: bool | None = None,
    ) -> CategorisedTransaction:
        """``disallowable`` None keeps an existing row's flag (new rows
        default to allowable); True/False sets it explicitly."""
        existing = self.get_categorisation(raw_transaction_id)
        old_category = None
        if existing:
            old_category = existing.category
            existing.category = category
            existing.confidence = confidence
            existing.matched_rule_id = matched_rule_id
            existing.categorised_at = datetime.utcnow()
            if disallowable is not None:
                existing.disallowable = disallowable
            cat = existing
        else:
            cat = CategorisedTransaction(
                raw_transaction_id=raw_transaction_id,
                category=category,
                confidence=confidence,
                matched_rule_id=matched_rule_id,
                disallowable=bool(disallowable),
            )
            self.session.add(cat)

        # Audit log
        audit = AuditLog(
            raw_transaction_id=raw_transaction_id,
            old_category=old_category,
            new_category=category,
            changed_by=changed_by,
        )
        self.session.add(audit)
        self.session.flush()
        return cat

    def count_categorised_by_categories(
        self, categories: Sequence[str],
    ) -> dict[str, int]:
        """How many transactions sit in each of the given categories."""
        stmt = (
            select(CategorisedTransaction.category, func.count(CategorisedTransaction.id))
            .where(CategorisedTransaction.category.in_(list(categories)))
            .group_by(CategorisedTransaction.category)
        )
        return dict(self.session.execute(stmt).all())

    def uncategorise_by_categories(
        self, categories: Sequence[str], changed_by: str = "system",
    ) -> dict:
        """Send every transaction in the given categories back for review.

        Used after the category scheme is refined: transactions lumped into
        a coarse catch-all are uncategorised (with an audit trail) so they
        can be re-reviewed against the finer categories, and the learned
        exact rules that would re-apply the old category on the next
        auto-classify are deactivated (not deleted - the audit stays).
        """
        cats = list(categories)
        rows = self.session.execute(
            select(CategorisedTransaction).where(
                CategorisedTransaction.category.in_(cats)
            )
        ).scalars().all()
        for cat_row in rows:
            self.session.add(AuditLog(
                raw_transaction_id=cat_row.raw_transaction_id,
                old_category=cat_row.category,
                new_category="(uncategorised)",
                changed_by=changed_by,
            ))
            self.session.delete(cat_row)
        rules = self.session.execute(
            select(ClassificationRule).where(
                ClassificationRule.category.in_(cats),
                ClassificationRule.rule_type == "exact",
                ClassificationRule.is_active.is_(True),
            )
        ).scalars().all()
        for rule in rules:
            rule.is_active = False
            rule.notes = (
                (rule.notes + " | " if rule.notes else "")
                + "deactivated: category scheme refined"
            )[:500]
        self.session.flush()
        return {
            "transactions": len(rows),
            "rules_deactivated": len(rules),
        }

    def set_disallowable_flag(
        self, raw_transaction_ids: Sequence[int], disallowable: bool,
    ) -> int:
        """Mark categorised transactions (dis)allowable for tax. Returns how
        many rows were updated - transactions without a categorisation are
        skipped (the flag belongs to the categorisation)."""
        updated = 0
        for txn_id in raw_transaction_ids:
            cat = self.get_categorisation(txn_id)
            if cat is None:
                continue
            if cat.disallowable != disallowable:
                cat.disallowable = disallowable
                updated += 1
        self.session.flush()
        return updated

    def get_categorised_transactions_in_period(
        self,
        start_date: date,
        end_date: date,
    ) -> Sequence[tuple[RawTransaction, CategorisedTransaction]]:
        stmt = (
            select(RawTransaction, CategorisedTransaction)
            .join(CategorisedTransaction)
            .where(RawTransaction.date >= start_date)
            .where(RawTransaction.date <= end_date)
            .order_by(RawTransaction.date)
        )
        return self.session.execute(stmt).all()  # type: ignore[return-value]

    # --- Classification Rules ---

    def get_active_rules(self) -> Sequence[ClassificationRule]:
        stmt = (
            select(ClassificationRule)
            .where(ClassificationRule.is_active.is_(True))
            .order_by(ClassificationRule.priority.desc())
        )
        return self.session.execute(stmt).scalars().all()

    def get_exact_rule(self, match_value: str) -> ClassificationRule | None:
        stmt = (
            select(ClassificationRule)
            .where(ClassificationRule.rule_type == "exact")
            .where(ClassificationRule.match_value == match_value.lower())
            .where(ClassificationRule.is_active.is_(True))
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def create_rule(
        self,
        rule_type: str,
        match_value: str,
        category: str,
        match_field: str = "description",
        priority: int = 0,
        notes: str | None = None,
    ) -> ClassificationRule:
        rule = ClassificationRule(
            rule_type=rule_type,
            match_field=match_field,
            match_value=match_value,
            category=category,
            priority=priority,
            notes=notes,
        )
        self.session.add(rule)
        self.session.flush()
        return rule

    def upsert_exact_rule(
        self, description: str, category: str
    ) -> ClassificationRule:
        """Create or update an exact-match rule for a description."""
        normalised = description.lower().strip()
        existing = self.get_exact_rule(normalised)
        if existing:
            existing.category = category
            existing.hit_count += 1
            self.session.flush()
            return existing
        return self.create_rule(
            rule_type="exact",
            match_value=normalised,
            category=category,
            priority=100,
            notes="Learned from user correction",
        )

    def increment_rule_hit(self, rule_id: int) -> None:
        stmt = select(ClassificationRule).where(ClassificationRule.id == rule_id)
        rule = self.session.execute(stmt).scalar_one_or_none()
        if rule:
            rule.hit_count += 1
            self.session.flush()

    def delete_rule(self, rule_id: int) -> bool:
        stmt = select(ClassificationRule).where(ClassificationRule.id == rule_id)
        rule = self.session.execute(stmt).scalar_one_or_none()
        if rule:
            rule.is_active = False
            self.session.flush()
            return True
        return False

    def get_all_rules(self) -> Sequence[ClassificationRule]:
        stmt = select(ClassificationRule).order_by(
            ClassificationRule.priority.desc(), ClassificationRule.id
        )
        return self.session.execute(stmt).scalars().all()

    # --- Employment Income confirmation ---

    def get_employment_income(self, tax_year: int) -> EmploymentIncomeRecord | None:
        stmt = select(EmploymentIncomeRecord).where(
            EmploymentIncomeRecord.tax_year == tax_year
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def set_employment_income(
        self,
        tax_year: int,
        gross_amount: float,
        source: str,
        note: str | None = None,
    ) -> EmploymentIncomeRecord:
        """Confirm (or replace) the gross P60/P45 figure for a tax year."""
        existing = self.get_employment_income(tax_year)
        if existing:
            existing.gross_amount = gross_amount
            existing.source = source
            existing.note = note
            existing.confirmed_at = datetime.utcnow()
            record = existing
        else:
            record = EmploymentIncomeRecord(
                tax_year=tax_year,
                gross_amount=gross_amount,
                source=source,
                note=note,
            )
            self.session.add(record)
        self.session.flush()
        return record

    def get_employment_gross_entries(
        self, raw_transaction_ids: Sequence[int]
    ) -> dict[int, EmploymentGrossEntry]:
        """Saved per-deposit gross amounts, keyed by raw transaction id."""
        if not raw_transaction_ids:
            return {}
        stmt = select(EmploymentGrossEntry).where(
            EmploymentGrossEntry.raw_transaction_id.in_(raw_transaction_ids)
        )
        return {
            e.raw_transaction_id: e
            for e in self.session.execute(stmt).scalars().all()
        }

    def set_employment_gross_entry(
        self,
        raw_transaction_id: int,
        gross_amount: float,
        source: str,
        note: str | None = None,
    ) -> EmploymentGrossEntry:
        """Record (or replace) the gross pay behind one salary deposit."""
        existing = self.get_employment_gross_entries([raw_transaction_id]).get(
            raw_transaction_id
        )
        if existing:
            existing.gross_amount = gross_amount
            existing.source = source
            existing.note = note
            existing.entered_at = datetime.utcnow()
            entry = existing
        else:
            entry = EmploymentGrossEntry(
                raw_transaction_id=raw_transaction_id,
                gross_amount=gross_amount,
                source=source,
                note=note,
            )
            self.session.add(entry)
        self.session.flush()
        return entry

    def delete_employment_gross_entries(
        self, raw_transaction_ids: Sequence[int]
    ) -> None:
        if not raw_transaction_ids:
            return
        self.session.execute(
            delete(EmploymentGrossEntry).where(
                EmploymentGrossEntry.raw_transaction_id.in_(raw_transaction_ids)
            )
        )
        self.session.flush()

    # --- Attachments (receipts/invoices) ---

    def create_attachment(
        self,
        raw_transaction_id: int,
        original_filename: str,
        stored_filename: str,
        content_type: str,
    ) -> Attachment:
        attachment = Attachment(
            raw_transaction_id=raw_transaction_id,
            original_filename=original_filename,
            stored_filename=stored_filename,
            content_type=content_type,
        )
        self.session.add(attachment)
        self.session.flush()
        return attachment

    def get_attachments_for_transaction(
        self, raw_transaction_id: int
    ) -> Sequence[Attachment]:
        stmt = (
            select(Attachment)
            .where(Attachment.raw_transaction_id == raw_transaction_id)
            .order_by(Attachment.added_at)
        )
        return self.session.execute(stmt).scalars().all()

    def get_attachment(self, attachment_id: int) -> Attachment | None:
        stmt = select(Attachment).where(Attachment.id == attachment_id)
        return self.session.execute(stmt).scalar_one_or_none()

    def delete_attachment(self, attachment_id: int) -> bool:
        attachment = self.get_attachment(attachment_id)
        if attachment is None:
            return False
        self.session.delete(attachment)
        self.session.flush()
        return True

    def count_attachments_for_transactions(
        self, raw_transaction_ids: Sequence[int]
    ) -> dict[int, int]:
        """Attachment counts per transaction id, for badging a transaction list."""
        if not raw_transaction_ids:
            return {}
        stmt = (
            select(Attachment.raw_transaction_id, func.count(Attachment.id))
            .where(Attachment.raw_transaction_id.in_(raw_transaction_ids))
            .group_by(Attachment.raw_transaction_id)
        )
        return dict(self.session.execute(stmt).all())

    # --- Stats ---

    def count_transactions(self) -> int:
        stmt = select(func.count(RawTransaction.id))
        return self.session.execute(stmt).scalar_one()

    def count_categorised(self) -> int:
        stmt = select(func.count(CategorisedTransaction.id))
        return self.session.execute(stmt).scalar_one()

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()
