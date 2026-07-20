"""Importer auto-detection registry."""

from __future__ import annotations

from pathlib import Path

from mtd_classifier.importers.base import TransactionImporter
from mtd_classifier.importers.first_direct_csv import FirstDirectCSVImporter
from mtd_classifier.importers.santander_csv import (
    SantanderTXTImporter,
    SantanderMidataImporter,
)
from mtd_classifier.importers.ofx_importer import OFXImporter
from mtd_classifier.importers.xlsx_importer import XlsxImporter
from mtd_classifier.importers.xls_importer import XlsImporter
from mtd_classifier.importers.csv_generic import GenericCsvImporter
from mtd_classifier.importers.pdf_importer import PdfImporter

# Ordered by specificity - more specific importers first; the generic
# spreadsheet/CSV and PDF importers are suffix-gated so they never shadow
# the bank-specific ones
IMPORTERS: list[TransactionImporter] = [
    FirstDirectCSVImporter(),       # type: ignore[list-item]
    SantanderMidataImporter(),      # type: ignore[list-item]
    SantanderTXTImporter(),         # type: ignore[list-item]
    OFXImporter(),                  # type: ignore[list-item]
    XlsxImporter(),                 # type: ignore[list-item]
    XlsImporter(),                  # type: ignore[list-item]
    GenericCsvImporter(),           # type: ignore[list-item]
    PdfImporter(),                  # type: ignore[list-item]
]

# Map bank name hints to importers for --bank flag
BANK_IMPORTERS: dict[str, list[TransactionImporter]] = {
    "firstdirect": [FirstDirectCSVImporter()],              # type: ignore[list-item]
    "santander": [                                           # type: ignore[list-item]
        SantanderMidataImporter(),
        SantanderTXTImporter(),
        OFXImporter(bank_name="santander"),
    ],
}


def detect_importer(
    file_path: Path, bank_hint: str | None = None
) -> TransactionImporter | None:
    """Auto-detect the correct importer for a file.

    If bank_hint is provided, only try importers for that bank.
    """
    path = Path(file_path)

    if bank_hint:
        candidates = BANK_IMPORTERS.get(bank_hint.lower(), IMPORTERS)
    else:
        candidates = IMPORTERS

    for importer in candidates:
        if importer.can_handle(path):
            return importer

    return None
