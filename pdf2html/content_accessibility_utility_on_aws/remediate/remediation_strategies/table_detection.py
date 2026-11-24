# Copyright 2025 Amazon.com, Inc. or its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""
Enhanced table detection and preprocessing for accessibility remediation.

This module provides functions to detect and preprocess tables for remediation,
particularly handling cases where tables from PDF conversions lack proper semantic markup.
"""

import re
from content_accessibility_utility_on_aws.table_resolution.header_policy import established_column_headers
from bs4 import BeautifulSoup, Tag

from content_accessibility_utility_on_aws.utils.logging_helper import setup_logger

# Set up module-level logger
logger = setup_logger(__name__)


def detect_header_like_cells(table: Tag) -> bool:
    """
    Detect if a table has cells that look like headers but are marked as td.

    Args:
        table: The table element to check

    Returns:
        True if header-like cells are detected, False otherwise
    """
    if table.has_attr('data-local-header-status') or table.get('data-local-table') == 'tagged':
        return False
    rows = [r for r in table.select('tr') if r.find_parent('table') is table]
    if len(rows) < 2:
        return False
    cells = rows[0].find_all(['td','th'], recursive=False)
    if not cells:
        return False
    if all(c.name == 'th' and c.get('scope') in ('col','colgroup') for c in cells):
        return True
    if any(c.name != 'td' for c in cells):
        return False

    def bold(cell):
        text = cell.get_text(strip=True)
        marked = cell.find(['strong', 'b'])
        return bool((marked and text and marked.get_text(strip=True) == text)
                    or re.search(r'font-weight:\s*(?:bold|[789]00)\b', cell.get('style', ''), re.I))
    body = [c for r in rows[1:] for c in r.find_all(['th','td'], recursive=False)]
    return established_column_headers([c.get_text(' ', strip=True) for c in cells],
                                      sum(map(bold, cells))/len(cells),
                                      sum(map(bold, body))/max(1,len(body)))



def preprocess_tables(html: str) -> str:
    """
    Preprocess tables in HTML to improve semantic structure before remediation.

    Args:
        html: The HTML content to process

    Returns:
        Preprocessed HTML with improved table structure with proper borders
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")

    tables_processed = 0

    for table in tables:
        # Skip empty tables
        if not table.find("tr"):
            continue

        # Add 1px solid border to table
        table_style = table.get("style", "")
        if "border" not in table_style.lower():
            table["style"] = f"{table_style}; border: 1px solid black; border-collapse: collapse;"

        # Add borders to all cells (th and td)
        for cell in table.find_all(["th", "td"]):
            cell_style = cell.get("style", "")
            if "border" not in cell_style.lower():
                cell["style"] = f"{cell_style}; border: 1px solid black;"

        # Add borders to all rows
        for row in table.find_all("tr"):
            row_style = row.get("style", "")
            if "border" not in row_style.lower():
                row["style"] = f"{row_style}; border: 1px solid black;"

        # Skip tables that already have proper structure
        if table.find("thead") or table.find("th"):
            continue

        # Get all rows
        rows = table.find_all("tr")

        # Check if first row looks like headers
        first_row = rows[0] if rows else None
        if first_row and detect_header_like_cells(table):
            # Create new thead and tbody elements
            thead = soup.new_tag("thead")
            tbody = soup.new_tag("tbody")

            # Extract the first row
            first_row.extract()

            # Convert all td cells in first row to th with scope
            for td in first_row.find_all("td"):
                new_th = soup.new_tag("th")
                new_th["scope"] = "col"
                # Copy attributes
                for attr, value in td.attrs.items():
                    if attr != "scope":  # Don't overwrite scope
                        new_th[attr] = value
                # Copy contents
                new_th.extend(td.contents)
                # Ensure header cells have border
                th_style = new_th.get("style", "")
                if "border" not in th_style.lower():
                    new_th["style"] = f"{th_style}; border: 1px solid black;"
                td.replace_with(new_th)

            # Add the modified first row to thead
            thead.append(first_row)

            # Move all remaining rows to tbody
            for remaining_row in table.find_all("tr"):
                remaining_row.extract()
                tbody.append(remaining_row)

            # Add thead and tbody to table
            table.append(thead)
            table.append(tbody)

            tables_processed += 1

    logger.debug(f"Preprocessed {tables_processed} tables with improved structure")
    return str(soup)
