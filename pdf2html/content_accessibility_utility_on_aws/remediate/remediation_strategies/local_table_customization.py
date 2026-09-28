"""Student-editable settings for the fully local Table Lab.

These settings affect only ``/table-lab`` and ``/api/table-jobs``. They never
call AWS, Bedrock, S3, or any external service.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LocalTableLabPolicy:
    """Controls for interpreting tables extracted from a PDF."""

    # Most source PDFs place column headings in the first extracted row.
    first_row_is_header: bool = True
    # Add a heading and a navigation link for each PDF page containing tables.
    include_page_headings: bool = True
    # Prefer the visible grid drawn in many research-PDF tables. This prevents
    # pdfplumber from guessing columns from the text's horizontal positions.
    table_settings: dict | None = None


LOCAL_TABLE_LAB_POLICY = LocalTableLabPolicy(
    table_settings={
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "snap_tolerance": 4,
        "join_tolerance": 4,
        "intersection_tolerance": 4,
    }
)
