"""CSV export for quarterly summaries and transaction details."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Sequence

from mtd_classifier.disclaimer import DISCLAIMER
from mtd_classifier.export.mtd_summary import QuarterlySummary
from mtd_classifier.models.transaction import RawTransaction, CategorisedTransaction


def export_to_csv(
    summary: QuarterlySummary,
    transactions: Sequence[tuple[RawTransaction, CategorisedTransaction]],
    output_path: Path,
) -> Path:
    """Export quarterly summary and transactions to a CSV file."""
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        # Summary section
        writer.writerow(["MTD Quarterly Summary"])
        writer.writerow(["Period", summary.quarter.label])
        writer.writerow(
            ["Date Range", str(summary.quarter.start_date), str(summary.quarter.end_date)]
        )
        writer.writerow([])

        # Category totals
        writer.writerow(["Category", "HMRC Box", "Income", "Expenses", "Net", "Count"])
        for ct in summary.reportable_totals():
            if ct.unconfirmed:
                writer.writerow(
                    [
                        ct.label,
                        "UNCONFIRMED - enter the correct figure before using this total",
                        "***",
                        "***",
                        "***",
                        ct.transaction_count,
                    ]
                )
                continue
            box_label = ct.hmrc_box
            if ct.confirmation_note:
                box_label = f"{box_label} ({ct.confirmation_note})"
            if ct.excluded_note:
                box_label = f"{box_label} - {ct.excluded_note}"
            writer.writerow(
                [
                    ct.label,
                    box_label,
                    f"{ct.total_income:.2f}",
                    f"{ct.total_expense:.2f}",
                    f"{ct.net_total:.2f}",
                    ct.transaction_count,
                ]
            )
        writer.writerow([])

        # Totals row
        writer.writerow(
            [
                "TOTAL",
                "",
                f"{summary.total_income:.2f}",
                f"{summary.total_expenses:.2f}",
                f"{summary.net_profit:.2f}",
                summary.categorised_count,
            ]
        )
        writer.writerow([])

        if summary.finance_costs > 0:
            writer.writerow(["Mortgage/Residential Finance Costs (Estimate)"])
            writer.writerow(["Finance costs (not deducted from profit)", f"{summary.finance_costs:.2f}"])
            writer.writerow(
                ["Estimated 20% basic-rate tax reduction", f"{summary.estimated_finance_cost_relief:.2f}"]
            )
            writer.writerow([
                "Note", "Simplified estimate only - capped at property profit; "
                "does not account for other income, personal allowance, or "
                "carrying forward unused relief. Verify against HMRC's own "
                "working sheet."
            ])
            writer.writerow([])

        writer.writerow(["Estimated Income Tax (Simplified)"])
        writer.writerow(["Estimated income tax due", f"{summary.estimated_income_tax:.2f}"])
        writer.writerow(
            ["Estimated net income after mortgage interest relief, allowable expenses and tax",
             f"{summary.estimated_net_income_after_tax:.2f}"]
        )
        writer.writerow([
            "Note", "Simplified estimate using standard rest-of-UK income tax "
            "bands on the profit above only. Does NOT include National "
            "Insurance, Scottish rates, or any other income you have "
            "(employment, savings, dividends) - your actual tax bill will "
            "differ. Verify with HMRC's own calculator or a professional."
        ])
        writer.writerow([])

        writer.writerow(["Disclaimer"])
        writer.writerow([DISCLAIMER])
        writer.writerow([])

        # Transaction detail
        writer.writerow(["Transaction Details"])
        writer.writerow(
            ["Date", "Description", "Amount", "Category", "Confidence", "Bank"]
        )
        for raw_txn, cat_txn in transactions:
            writer.writerow(
                [
                    raw_txn.date.isoformat(),
                    raw_txn.description,
                    f"{raw_txn.amount:.2f}",
                    cat_txn.category,
                    cat_txn.confidence,
                    raw_txn.bank,
                ]
            )

    return output_path
