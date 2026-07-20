"""OFX/QFX file importer using ofxtools."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from mtd_classifier.importers.base import ParsedTransaction


class OFXImporter:
    """Shared OFX/QFX importer for any bank.

    Uses the ofxtools library for spec-compliant parsing.
    """

    bank_name: str = "unknown"
    format_name: str = "ofx"

    def __init__(self, bank_name: str = "unknown") -> None:
        self.bank_name = bank_name

    def can_handle(self, file_path: Path) -> bool:
        suffix = file_path.suffix.lower()
        if suffix not in (".ofx", ".qfx"):
            return False
        try:
            with open(file_path, "rb") as f:
                header = f.read(200)
                return b"OFX" in header.upper() or b"<?xml" in header.lower()
        except OSError:
            return False

    def parse(self, file_path: Path) -> Iterator[ParsedTransaction]:
        try:
            from ofxtools.Parser import OFXTree
        except ImportError:
            raise ImportError(
                "ofxtools is required for OFX import. Install with: pip install ofxtools"
            )

        parser = OFXTree()
        parser.parse(str(file_path))
        ofx = parser.convert()

        for statement in ofx.statements:
            for txn in statement.transactions:
                description = txn.name or txn.memo or ""
                if txn.memo and txn.name and txn.memo != txn.name:
                    description = f"{txn.name} - {txn.memo}"

                amount = float(txn.trnamt)
                balance = None  # OFX doesn't have per-transaction balance

                yield ParsedTransaction(
                    date=txn.dtposted.date() if hasattr(txn.dtposted, 'date') else txn.dtposted,
                    description=description.strip(),
                    amount=amount,
                    balance=balance,
                    raw_data=f"FITID:{txn.fitid} NAME:{txn.name} MEMO:{txn.memo}",
                )
