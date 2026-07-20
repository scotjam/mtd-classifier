"""Fill My Tax Digital SA103/SA105 bridging templates for MTD quarterly updates.

My Tax Digital (mytaxdigital.co.uk, free HMRC-recognised bridging software)
accepts its own XLSX templates via the "Import figures" screen in Bridging
mode. HMRC requires each quarterly update to contain CUMULATIVE year-to-date
figures (6 April to the period end date), not single-quarter figures, so this
module aggregates from the start of the tax year.

Cell coordinates below were taken from the official templates downloaded from
https://mytaxdigital.co.uk/app/templates/MTD_SA103_template.xlsx and
.../MTD_SA105_template.xlsx (July 2026). Values are written into the wide
entry cell immediately right of each box's £ symbol - column C for left-hand
boxes and column I for right-hand ones. (Columns D/J are narrow spacers; a
decorative merged range is anchored there, but My Tax Digital reads C/I.)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import openpyxl

from mtd_classifier.export.template_fetch import get_template
from mtd_classifier.models.category import (
    ALWAYS_DISALLOWABLE_CATEGORIES,
    TransactionCategory,
    rent_a_room_limit,
)
from mtd_classifier.models.transaction import RawTransaction, CategorisedTransaction


@dataclass(frozen=True)
class BoxCell:
    """A single HMRC box on a bridging template.

    ``disallowable_cell`` is the matching entry in the SA103 template's
    right-hand "Disallowable expenses" column (boxes 32-45): the expense
    box holds the TOTAL (allowable + disallowable) and the disallowable
    box holds the share HMRC subtracts back out. The SA105 template has no
    such column, so its boxes leave this None - disallowable property
    amounts are simply not entered.
    """

    form: str      # "SA103" or "SA105"
    box: str       # HMRC box number as shown on the template
    label: str     # HMRC box description
    cell: str      # entry cell coordinate in the template
    is_income: bool = False
    disallowable_cell: str | None = None


# Category -> template box. Expense boxes receive positive (absolute) values.
CATEGORY_BOX_CELLS: dict[TransactionCategory, BoxCell] = {
    # SA103F - Self Employment (sheet "Self-employment")
    TransactionCategory.SELF_EMPLOYED_INCOME: BoxCell(
        "SA103", "15", "Turnover", "C7", is_income=True
    ),
    TransactionCategory.SE_OTHER_INCOME: BoxCell(
        "SA103", "16", "Any other business income not included in box 15", "I7",
        is_income=True,
    ),
    TransactionCategory.SE_TAX_DEDUCTED: BoxCell(
        "SA103", "81", "Tax taken off trading income", "C11"
    ),
    TransactionCategory.SE_GOODS: BoxCell(
        "SA103", "17", "Cost of goods bought for resale or goods used", "C19",
        disallowable_cell="I19",
    ),
    TransactionCategory.SE_SUBCONTRACTORS: BoxCell(
        "SA103", "18", "Construction industry - payments to subcontractors", "C23",
        disallowable_cell="I23",
    ),
    TransactionCategory.SE_STAFF_COSTS: BoxCell(
        "SA103", "19", "Wages, salaries and other staff costs", "C27",
        disallowable_cell="I27",
    ),
    TransactionCategory.SE_TRAVEL: BoxCell(
        "SA103", "20", "Car, van and travel expenses", "C31",
        disallowable_cell="I31",
    ),
    TransactionCategory.SE_PREMISES: BoxCell(
        "SA103", "21", "Rent, rates, power and insurance costs", "C35",
        disallowable_cell="I35",
    ),
    TransactionCategory.SE_REPAIRS: BoxCell(
        "SA103", "22", "Repairs and maintenance of property and equipment", "C39",
        disallowable_cell="I39",
    ),
    TransactionCategory.SE_OFFICE_COSTS: BoxCell(
        "SA103", "23", "Phone, fax, stationery and other office costs", "C43",
        disallowable_cell="I43",
    ),
    TransactionCategory.SE_TELECOMS: BoxCell(
        "SA103", "23", "Phone, fax, stationery and other office costs", "C43",
        disallowable_cell="I43",
    ),
    TransactionCategory.SE_IT: BoxCell(
        "SA103", "23", "Phone, fax, stationery and other office costs", "C43",
        disallowable_cell="I43",
    ),
    TransactionCategory.SE_ADVERTISING: BoxCell(
        "SA103", "24.1", "Advertising costs", "C47",
        disallowable_cell="I47",
    ),
    TransactionCategory.SE_ENTERTAINMENT: BoxCell(
        "SA103", "24.2", "Business entertainment costs", "C51",
        disallowable_cell="I51",
    ),
    TransactionCategory.LOAN_INTEREST: BoxCell(
        "SA103", "25", "Interest on bank and other loans", "C55",
        disallowable_cell="I55",
    ),
    TransactionCategory.SE_BANK_CHARGES: BoxCell(
        "SA103", "26", "Bank, credit card and other financial charges", "C59",
        disallowable_cell="I59",
    ),
    TransactionCategory.SE_BAD_DEBTS: BoxCell(
        "SA103", "27", "Irrecoverable debts written off", "C63",
        disallowable_cell="I63",
    ),
    TransactionCategory.SE_PROFESSIONAL_FEES: BoxCell(
        "SA103", "28", "Accountancy, legal and other professional fees", "C67",
        disallowable_cell="I67",
    ),
    TransactionCategory.SE_DEPRECIATION: BoxCell(
        "SA103", "29", "Depreciation and loss or profit on sale of assets", "C71",
        disallowable_cell="I71",
    ),
    TransactionCategory.SE_OTHER_EXPENSE: BoxCell(
        "SA103", "30", "Other business expenses", "C75",
        disallowable_cell="I75",
    ),
    # SA105 - UK Property (sheet "Property Business")
    TransactionCategory.RENTAL_INCOME: BoxCell(
        "SA105", "20a", "Total rents and other income from property", "C7", is_income=True
    ),
    TransactionCategory.PROPERTY_OTHER_INCOME: BoxCell(
        "SA105", "20b", "Other income from property", "C11", is_income=True
    ),
    TransactionCategory.RENT_A_ROOM_INCOME: BoxCell(
        "SA105", "20c", "Total rents received from rent-a-room properties", "C15",
        is_income=True,
    ),
    TransactionCategory.PROPERTY_TAX_DEDUCTED: BoxCell(
        "SA105", "21", "Tax already deducted from the rental income", "I7"
    ),
    TransactionCategory.LEASE_PREMIUM_INCOME: BoxCell(
        "SA105", "22", "Premiums for the grant of a lease", "I11", is_income=True
    ),
    TransactionCategory.REVERSE_PREMIUM_INCOME: BoxCell(
        "SA105", "23", "Reverse premiums and inducements", "I15", is_income=True
    ),
    TransactionCategory.PROPERTY_INSURANCE: BoxCell(
        "SA105", "24", "Rent, rates, insurance and ground rents", "C21"
    ),
    TransactionCategory.PROPERTY_REPAIRS: BoxCell(
        "SA105", "25", "Property repairs and maintenance", "C25"
    ),
    TransactionCategory.NON_RES_FINANCE_COSTS: BoxCell(
        "SA105", "26", "Non-residential property finance costs", "C29"
    ),
    TransactionCategory.LETTING_AGENT_FEES: BoxCell(
        "SA105", "27", "Legal, management and other professional fees", "C33"
    ),
    TransactionCategory.PROPERTY_LEGAL_FEES: BoxCell(
        "SA105", "27", "Legal, management and other professional fees", "C33"
    ),
    TransactionCategory.PROPERTY_SERVICES: BoxCell(
        "SA105", "28", "Costs of services provided, including wages", "I21"
    ),
    TransactionCategory.PROPERTY_OTHER_EXPENSE: BoxCell(
        "SA105", "29a", "Other allowable property expenses", "I25"
    ),
    TransactionCategory.PROPERTY_TRAVEL: BoxCell(
        "SA105", "29b", "Car, van and travel costs", "I29"
    ),
    TransactionCategory.MORTGAGE_INTEREST: BoxCell(
        "SA105", "44", "Residential property finance costs", "C42"
    ),
}

# Box 37 has no category of its own - it is DERIVED from the Rent-a-Room
# receipts (box 20c): the exempt amount claimable is min(receipts, annual
# limit). Auto-filled by export_bridging_templates when box 20c has income.
RENT_A_ROOM_EXEMPT_CELL = BoxCell(
    "SA105", "37", "Rent a Room exempt amount", "I33"
)


@dataclass
class BoxTotal:
    """Cumulative value for one template box."""

    box_cell: BoxCell
    amount: float = 0.0
    # Share of ``amount`` that is disallowable for tax - written to the
    # SA103 disallowable column so HMRC subtracts it from the total.
    disallowable: float = 0.0
    transaction_count: int = 0


@dataclass
class BridgingExport:
    """Result of filling one or both templates."""

    box_totals: dict[str, BoxTotal]  # keyed by "<form>:<cell>"
    files_written: list[Path]
    skipped_categories: list[str]  # categorised but not reportable (personal/transfer)


def aggregate_box_totals(
    transactions: Sequence[tuple[RawTransaction, CategorisedTransaction]],
) -> tuple[dict[str, BoxTotal], list[str]]:
    """Sum transactions into template boxes.

    Income boxes get the net of positive and negative amounts; expense boxes
    get the positive magnitude of the net (refunds reduce the expense).

    Disallowable amounts (per-transaction flag, or a wholly-disallowable
    category): on SA103 they stay in the expense box TOTAL and are also
    written to the matching disallowable box, so HMRC subtracts them; the
    SA105 template has no disallowable column, so there they are simply
    left out of the box (an unclaimable property expense is not entered).
    """
    totals: dict[str, BoxTotal] = {}
    skipped: set[str] = set()

    for raw_txn, cat_txn in transactions:
        try:
            category = TransactionCategory(cat_txn.category)
        except ValueError:
            skipped.add(cat_txn.category)
            continue

        box_cell = CATEGORY_BOX_CELLS.get(category)
        if box_cell is None:
            skipped.add(category.value)
            continue

        disallowed = (
            category in ALWAYS_DISALLOWABLE_CATEGORIES
            or bool(getattr(cat_txn, "disallowable", False))
        )
        key = f"{box_cell.form}:{box_cell.cell}"
        bt = totals.setdefault(key, BoxTotal(box_cell=box_cell))
        if disallowed and not box_cell.is_income:
            if box_cell.disallowable_cell is None:
                continue  # no disallowable column on this form: don't enter it
            bt.disallowable += raw_txn.amount
        bt.amount += raw_txn.amount
        bt.transaction_count += 1

    # Expense boxes are entered as positive figures on the template
    for bt in totals.values():
        if not bt.box_cell.is_income:
            bt.amount = -bt.amount
            bt.disallowable = -bt.disallowable
        bt.amount = round(bt.amount, 2)
        bt.disallowable = round(bt.disallowable, 2)

    return totals, sorted(skipped)


def _add_rent_a_room_exemption(
    box_totals: dict[str, BoxTotal], tax_year: int | None,
) -> None:
    """Derive box 37 from the Rent-a-Room receipts in box 20c.

    The exempt amount is min(receipts to date, annual limit). The limit is
    HALVED when someone else shares the letting income - the app can't know
    that, so callers surface a note telling the user to adjust the figure
    in My Tax Digital before submitting if that applies.
    """
    receipts = box_totals.get("SA105:C15")
    if receipts is None or receipts.amount <= 0:
        return
    limit = rent_a_room_limit(tax_year if tax_year is not None else 9999)
    key = f"{RENT_A_ROOM_EXEMPT_CELL.form}:{RENT_A_ROOM_EXEMPT_CELL.cell}"
    box_totals[key] = BoxTotal(
        box_cell=RENT_A_ROOM_EXEMPT_CELL,
        amount=round(min(receipts.amount, limit), 2),
    )


def fill_template(
    form: str,
    box_totals: dict[str, BoxTotal],
    output_path: Path,
) -> Path:
    """Copy the official template and write box values into it.

    The template is downloaded from mytaxdigital.co.uk on first use and
    cached (see template_fetch - the templates are My Tax Digital's own
    files and are not redistributed with this app). May raise
    TemplateDownloadError / TemplateLayoutError.
    """
    template = get_template(form)
    wb = openpyxl.load_workbook(template)
    ws = wb.worksheets[0]

    for bt in box_totals.values():
        if bt.box_cell.form != form:
            continue
        ws[bt.box_cell.cell] = bt.amount
        if bt.box_cell.disallowable_cell is not None and bt.disallowable:
            ws[bt.box_cell.disallowable_cell] = bt.disallowable

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    return output_path


def export_bridging_templates(
    transactions: Sequence[tuple[RawTransaction, CategorisedTransaction]],
    output_dir: Path,
    period_label: str,
    forms: Sequence[str] = ("SA103", "SA105"),
    tax_year: int | None = None,
) -> BridgingExport:
    """Fill the requested templates with cumulative figures.

    ``transactions`` must cover the whole tax year to date (6 April to the
    period end), because HMRC quarterly updates are cumulative. ``tax_year``
    selects the Rent-a-Room exempt-amount limit for box 37 (current-era
    limit assumed when omitted).
    """
    box_totals, skipped = aggregate_box_totals(transactions)
    _add_rent_a_room_exemption(box_totals, tax_year)

    files_written: list[Path] = []
    for form in forms:
        has_data = any(bt.box_cell.form == form for bt in box_totals.values())
        if not has_data:
            continue
        output_path = output_dir / f"MTD_{form}_{period_label}_cumulative.xlsx"
        files_written.append(fill_template(form, box_totals, output_path))

    return BridgingExport(
        box_totals=box_totals,
        files_written=files_written,
        skipped_categories=skipped,
    )
