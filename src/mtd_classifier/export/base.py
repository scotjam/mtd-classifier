"""Base protocol for summary exporters."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class SummaryExporter(Protocol):
    """Protocol for exporting quarterly summaries."""

    def export(self, data: dict, output_path: Path) -> None:
        """Export summary data to the given path."""
        ...
