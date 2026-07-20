"""In-memory example data shown during the first-run tour.

The demo database lives entirely in memory and is discarded when the tour
finishes (or the app closes), so it can never contaminate real records.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mtd_classifier.models.base import Base
from mtd_classifier.periods import quarter_for_date
from mtd_classifier.storage.repository import Repository

DEMO_ACCOUNT = "Demo Account"
DEMO_BANK = "firstdirect"
DEMO_TAB_LABEL = "Demo Account (tour)"


def create_demo_repo(today: date | None = None) -> tuple[Repository, int]:
    """Build a seeded in-memory repository for the tour.

    Returns the repository and the id of the refund line the tour selects to
    demonstrate refund marking.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    repo = Repository(sessionmaker(bind=engine)())

    tq = quarter_for_date(today or date.today())
    base = tq.start_date

    log = repo.create_import_log(
        filename="demo-statement.csv",
        file_hash="demo",
        bank=DEMO_BANK,
        fmt="csv",
        record_count=8,
    )

    def day(offset: int) -> date:
        return base + timedelta(days=offset)

    # (offset, description, amount, category or None)
    rows = [
        (9, "INVOICE 1042 WEB DESIGN", 1200.00, "self_employed_income"),
        (12, "RENT RECEIVED 1 EXAMPLE RD", 950.00, "rental_income"),
        (15, "B&Q BUILDING SUPPLIES", -86.40, "property_repairs"),
        (20, "PLUMBER J SMITH", -180.00, "property_repairs"),
        (27, "PLUMBER J SMITH", 180.00, None),   # refund - tour marks this one
        (17, "TESCO STORES", -45.67, None),       # left for the user to see yellow
        (22, "NATIONAL RAIL", -24.80, None),
        (25, "TRANSFER TO SAVINGS", -500.00, "transfer"),
    ]

    refund_id = -1
    for i, (offset, desc, amount, category) in enumerate(rows):
        d = day(offset)
        txn = repo.create_raw_transaction(
            import_log_id=log.id,
            bank=DEMO_BANK,
            txn_date=d,
            description=desc,
            amount=amount,
            dedup_hash=f"demo_{i}",
            account=DEMO_ACCOUNT,
            raw_data=f"{d:%d/%m/%Y},{desc},{amount:.2f}",
        )
        if category is not None:
            repo.set_categorisation(
                raw_transaction_id=txn.id,
                category=category,
                confidence="manual",
                changed_by="demo",
            )
        if amount == 180.00:
            refund_id = txn.id

    repo.commit()
    return repo, refund_id
