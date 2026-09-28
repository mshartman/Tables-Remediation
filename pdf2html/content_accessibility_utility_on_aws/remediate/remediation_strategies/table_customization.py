"""Policy and prompts for PDF table remediation.

This is the intended customization point for table output. It contains no HTML
mutation logic, so prompt or policy changes can be reviewed independently from
the mechanics that modify converted documents.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TableRemediationPolicy:
    """Controls conservative AI-assisted decisions for converted PDF tables."""

    use_ai_context_analysis: bool = True
    minimum_confidence: str = "high"
    max_preceding_nodes: int = 3
    max_preview_rows: int = 4
    max_preview_columns: int = 6
    allow_caption_extraction: bool = True
    allow_header_inference: bool = True
    remove_promoted_source_text: bool = True


DEFAULT_TABLE_POLICY = TableRemediationPolicy()


PRE_TABLE_CONTEXT_PROMPT = """
You are analyzing a converted PDF table for WCAG-compatible HTML structure.

Decide whether text immediately before the table is:
- a caption/title for the table,
- a source for a missing column-header row,
- both,
- or ordinary surrounding prose that must remain outside the table.

Nearby text before the table:
{candidate_lines}

Table preview as rows of cell text:
{row_preview}

Column count: {column_count}

Return ONLY valid JSON with this shape:
{{
  "action": "caption_only" | "header_row" | "caption_and_header" | "ignore",
  "caption_text": "string",
  "headers": ["header 1", "header 2"],
  "confidence": "high" | "medium" | "low",
  "reason": "short explanation"
}}

Rules:
- Use "ignore" if the nearby text is probably body text rather than table
  structure.
- Only propose a header row when the headers are unambiguous.
- If proposing headers, provide exactly {column_count} non-empty labels.
- Do not invent data, table titles, or headers that are unsupported by the
  provided context.
- Prefer the document's own terminology and concise labels.
""".strip()
