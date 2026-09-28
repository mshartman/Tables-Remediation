"""AI-assisted context analysis for PDF-derived HTML tables.

The pipeline intentionally owns semantic decisions, while table_remediation.py
continues to own lower-level HTML repairs such as adding scope or tbody.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from bs4 import BeautifulSoup, NavigableString, Tag

from content_accessibility_utility_on_aws.remediate.remediation_strategies.table_customization import (
    DEFAULT_TABLE_POLICY,
    PRE_TABLE_CONTEXT_PROMPT,
    TableRemediationPolicy,
)
from content_accessibility_utility_on_aws.remediate.services.bedrock_client import BedrockClient
from content_accessibility_utility_on_aws.utils.logging_helper import setup_logger


logger = setup_logger(__name__)


def _normalize_text(value: str) -> str:
    return " ".join((value or "").lower().split())


def _normalized_action(value: str) -> str:
    aliases = {
        "caption": "caption_only",
        "caption-only": "caption_only",
        "caption_only": "caption_only",
        "header": "header_row",
        "headers": "header_row",
        "header-row": "header_row",
        "header_row": "header_row",
        "both": "caption_and_header",
        "caption_and_header": "caption_and_header",
        "ignore": "ignore",
        "none": "ignore",
    }
    return aliases.get((value or "").strip().lower(), "ignore")


class TableRemediationPipeline:
    """Classifies nearby PDF text and applies high-confidence table semantics."""

    def __init__(
        self,
        soup: BeautifulSoup,
        bedrock_client: Optional[BedrockClient],
        policy: TableRemediationPolicy = DEFAULT_TABLE_POLICY,
    ) -> None:
        self.soup = soup
        self.bedrock_client = bedrock_client
        self.policy = policy

    def analyze_and_apply_context(
        self,
        table: Tag,
        *,
        apply_caption: bool = True,
        apply_header_row: bool = True,
    ) -> dict[str, str]:
        """Apply only high-confidence caption/header recommendations."""
        analysis = self._analyze_context(table)
        if not analysis:
            return {}

        changes: dict[str, str] = {}
        if apply_caption and self.policy.allow_caption_extraction:
            caption = self._apply_caption(table, analysis)
            if caption:
                changes["caption"] = caption

        if apply_header_row and self.policy.allow_header_inference:
            headers = self._apply_header_row(table, analysis)
            if headers:
                changes["header_row"] = headers

        return changes

    def _preceding_candidates(self, table: Tag) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        current = table.previous_sibling

        while current and len(candidates) < self.policy.max_preceding_nodes:
            if isinstance(current, NavigableString):
                text = str(current).strip()
                if text:
                    candidates.append({"node": current, "text": text})
            elif isinstance(current, Tag):
                text = current.get_text(" ", strip=True)
                if text and not current.find("table"):
                    candidates.append({"node": current, "text": text})
            current = current.previous_sibling

        candidates.reverse()
        return candidates

    def _row_preview(self, table: Tag) -> list[list[str]]:
        rows: list[list[str]] = []
        for row in table.find_all("tr")[: self.policy.max_preview_rows]:
            cells = [
                cell.get_text(" ", strip=True)
                for cell in row.find_all(["th", "td"], recursive=False)[
                    : self.policy.max_preview_columns
                ]
            ]
            if cells:
                rows.append(cells)
        return rows

    def _analyze_context(self, table: Tag) -> Optional[dict[str, Any]]:
        if not self.policy.use_ai_context_analysis or not self.bedrock_client:
            return None

        candidates = self._preceding_candidates(table)
        preview = self._row_preview(table)
        if not candidates or not preview:
            return None

        column_count = max(len(row) for row in preview)
        prompt = PRE_TABLE_CONTEXT_PROMPT.format(
            candidate_lines=json.dumps([item["text"] for item in candidates], ensure_ascii=False),
            row_preview=json.dumps(preview, ensure_ascii=False),
            column_count=column_count,
        )

        try:
            response = self.bedrock_client.generate_text(
                prompt, purpose="table_remediation", max_tokens=1200
            )
            start = response.find("{")
            end = response.rfind("}") + 1
            if start < 0 or end <= start:
                logger.warning("Table context analysis returned no JSON")
                return None

            analysis = json.loads(response[start:end])
        except Exception as error:
            logger.warning("Table context analysis failed: %s", error)
            return None

        analysis["action"] = _normalized_action(analysis.get("action", "ignore"))
        analysis["confidence"] = str(analysis.get("confidence") or "low").lower()
        analysis["caption_text"] = str(analysis.get("caption_text") or "").strip()
        analysis["headers"] = [
            str(value).strip() for value in analysis.get("headers", []) if str(value).strip()
        ]
        analysis["candidates"] = candidates
        analysis["column_count"] = column_count
        return analysis

    def _is_confident(self, analysis: dict[str, Any]) -> bool:
        return analysis.get("confidence") == self.policy.minimum_confidence

    def _apply_caption(self, table: Tag, analysis: dict[str, Any]) -> Optional[str]:
        if table.find("caption") or not self._is_confident(analysis):
            return None
        if analysis.get("action") not in {"caption_only", "caption_and_header"}:
            return None

        caption_text = analysis.get("caption_text", "")
        if not caption_text:
            return None

        caption = self.soup.new_tag("caption")
        caption.string = caption_text
        table.insert(0, caption)
        self._remove_matching_source(analysis, caption_text)
        return caption_text

    def _apply_header_row(self, table: Tag, analysis: dict[str, Any]) -> Optional[str]:
        if table.find("th") or not self._is_confident(analysis):
            return None
        if analysis.get("action") not in {"header_row", "caption_and_header"}:
            return None

        headers = analysis.get("headers", [])
        if len(headers) != analysis.get("column_count"):
            logger.warning("Skipping AI header row: header count does not match table columns")
            return None

        thead = table.find("thead")
        if not thead:
            thead = self.soup.new_tag("thead")
            table.insert(0, thead)

        row = self.soup.new_tag("tr")
        for text in headers:
            header = self.soup.new_tag("th")
            header["scope"] = "col"
            header.string = text
            row.append(header)
        thead.insert(0, row)

        self._remove_matching_source(analysis, headers[0])
        return ", ".join(headers)

    def _remove_matching_source(self, analysis: dict[str, Any], expected: str) -> None:
        if not self.policy.remove_promoted_source_text:
            return
        for candidate in reversed(analysis.get("candidates", [])):
            if _normalize_text(candidate.get("text", "")) != _normalize_text(expected):
                continue
            candidate["node"].extract()
            return
