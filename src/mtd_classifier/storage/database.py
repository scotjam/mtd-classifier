"""Database engine and session management."""

from __future__ import annotations

from sqlalchemy import create_engine, text, Engine
from sqlalchemy.orm import Session, sessionmaker

import mtd_classifier.config as config
from mtd_classifier.models.base import Base

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine(url: str | None = None) -> Engine:
    """Get or create the SQLAlchemy engine."""
    global _engine
    if _engine is None or url is not None:
        if url is None:
            config.ensure_app_dir()
            # Read lazily: activate_user() repoints DB_URL at the active
            # user's database, so an import-time copy would go stale.
            url = config.DB_URL
        _engine = create_engine(url, echo=False)
    return _engine


def get_session(engine: Engine | None = None) -> Session:
    """Create a new database session."""
    global _session_factory
    if engine is None:
        engine = get_engine()
    if _session_factory is None or engine != _engine:
        _session_factory = sessionmaker(bind=engine)
    return _session_factory()


# Bump when the dedup-hash formula changes; _migrate_dedup_hashes recomputes
# every stored hash so old rows keep matching new imports.
HASH_SCHEMA_VERSION = 1


def init_db(engine: Engine | None = None) -> None:
    """Create all database tables, then apply lightweight migrations
    (create_all never alters existing tables)."""
    if engine is None:
        engine = get_engine()
    Base.metadata.create_all(engine)
    _migrate_source_formats(engine)
    _migrate_import_log_coverage(engine)
    _migrate_dedup_hashes(engine)
    _migrate_disallowable_flag(engine)


def _migrate_disallowable_flag(engine: Engine) -> None:
    """Add categorised_transactions.disallowable to pre-existing databases
    (defaults to allowable - the flag is set per transaction by the user)."""
    with engine.begin() as conn:
        columns = [
            row[1] for row in conn.execute(
                text("PRAGMA table_info(categorised_transactions)")
            )
        ]
        if "disallowable" in columns:
            return
        conn.execute(text(
            "ALTER TABLE categorised_transactions "
            "ADD COLUMN disallowable BOOLEAN DEFAULT 0"
        ))


def _migrate_source_formats(engine: Engine) -> None:
    """Add raw_transactions.source_formats to pre-existing databases and
    backfill it from each row's import log format (the file that first
    added a row is by definition one of its evidencing sources)."""
    with engine.begin() as conn:
        columns = [
            row[1] for row in conn.execute(text("PRAGMA table_info(raw_transactions)"))
        ]
        if "source_formats" in columns:
            return
        conn.execute(text(
            "ALTER TABLE raw_transactions ADD COLUMN source_formats VARCHAR(50) DEFAULT ''"
        ))
        conn.execute(text(
            "UPDATE raw_transactions SET source_formats = ("
            "  SELECT CASE WHEN il.format = 'pdf' THEN 'pdf' ELSE 'tabular' END"
            "  FROM import_logs il WHERE il.id = raw_transactions.import_log_id"
            ") WHERE source_formats = '' OR source_formats IS NULL"
        ))


def _migrate_import_log_coverage(engine: Engine) -> None:
    """Add period_start/period_end/account to pre-existing import_logs and
    backfill from each log's transactions (min/max date, their account).
    Conservative for PDFs - the printed statement period may extend a few
    days beyond its first/last transaction - but never wrong."""
    with engine.begin() as conn:
        columns = [
            row[1] for row in conn.execute(text("PRAGMA table_info(import_logs)"))
        ]
        if "period_start" in columns:
            return
        conn.execute(text("ALTER TABLE import_logs ADD COLUMN period_start DATE"))
        conn.execute(text("ALTER TABLE import_logs ADD COLUMN period_end DATE"))
        conn.execute(text("ALTER TABLE import_logs ADD COLUMN account VARCHAR(100)"))
        conn.execute(text(
            "UPDATE import_logs SET "
            "period_start = (SELECT MIN(t.date) FROM raw_transactions t"
            "  WHERE t.import_log_id = import_logs.id), "
            "period_end = (SELECT MAX(t.date) FROM raw_transactions t"
            "  WHERE t.import_log_id = import_logs.id), "
            "account = (SELECT t.account FROM raw_transactions t"
            "  WHERE t.import_log_id = import_logs.id LIMIT 1)"
        ))


def _migrate_dedup_hashes(engine: Engine) -> None:
    """Recompute every stored dedup hash with the current formula.

    The formula changed (bank tag -> account identity, plus description
    normalization) to make the same real transaction match across file
    formats. Rows hashed with an older formula would never match a new
    import of the same transactions, so overlapping re-imports would
    silently double up. PRAGMA user_version records which formula the
    database's hashes were computed with; anything older is recomputed.
    """
    with engine.connect() as conn:
        version = conn.execute(text("PRAGMA user_version")).scalar()
    if version >= HASH_SCHEMA_VERSION:
        return

    from collections import Counter

    from sqlalchemy.orm import sessionmaker

    from mtd_classifier.models.transaction import RawTransaction
    from mtd_classifier.services.import_service import (
        normalize_description,
        transaction_hash,
    )
    from mtd_classifier.importers.base import ParsedTransaction

    session = sessionmaker(bind=engine)()
    try:
        rows = session.query(RawTransaction).order_by(RawTransaction.id).all()
        # Two passes: dedup_hash is UNIQUE, and a recomputed hash can
        # transiently equal another row's not-yet-recomputed one.
        for t in rows:
            t.dedup_hash = f"migrating-{t.id}"
        session.flush()
        occurrences: Counter = Counter()
        for t in rows:
            identity = t.account if t.account is not None else t.bank
            key = (identity, t.date, normalize_description(t.description),
                   f"{t.amount:.2f}")
            occurrence = occurrences[key]
            occurrences[key] += 1
            t.dedup_hash = transaction_hash(
                identity,
                ParsedTransaction(date=t.date, description=t.description,
                                  amount=t.amount),
                occurrence,
            )
        session.commit()
        with engine.begin() as conn:
            conn.execute(text(f"PRAGMA user_version = {HASH_SCHEMA_VERSION}"))
    finally:
        session.close()


def reset_engine() -> None:
    """Reset the global engine (for testing)."""
    global _engine, _session_factory
    _engine = None
    _session_factory = None
