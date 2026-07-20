"""Base protocol for transaction importers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable


@dataclass
class ParsedTransaction:
    """A single parsed transaction from a bank statement."""

    date: date
    description: str
    amount: float
    balance: float | None = None
    raw_data: str | None = None
    # True when the importer could not determine whether this amount is
    # money in or out (e.g. a PDF group whose total didn't reconcile against
    # the balance movement) and fell back to a guess. ``amount`` is printed
    # positive in this case - the caller should give the user a chance to
    # confirm the sign or skip the line rather than trusting it blindly.
    ambiguous_sign: bool = False


@runtime_checkable
class TransactionImporter(Protocol):
    """Protocol for bank statement importers."""

    bank_name: str
    format_name: str

    def can_handle(self, file_path: Path) -> bool:
        """Check if this importer can handle the given file."""
        ...

    def parse(self, file_path: Path) -> Iterator[ParsedTransaction]:
        """Parse the file and yield transactions."""
        ...
