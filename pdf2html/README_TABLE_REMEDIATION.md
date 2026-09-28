# Table Remediation Customization

Table handling is isolated under:

`content_accessibility_utility_on_aws/remediate/remediation_strategies/`

The files have separate responsibilities:

| File | Purpose | Edit when you want to... |
| --- | --- | --- |
| `table_customization.py` | Policy switches and Bedrock prompt | Change what the AI is allowed to infer or how it is instructed. |
| `table_pipeline.py` | High-confidence AI context analysis | Change how text near a table is evaluated and promoted into semantic HTML. |
| `table_remediation.py` | Low-level HTML repairs | Change the mechanics for `caption`, `thead`, `tbody`, `th`, `scope`, and `headers`. |
| `table_detection.py` | Non-AI preprocessing | Change the rules that identify header-like cells before remediation. |
| `table_remediation_direct.py` | Direct structural fallback | Change table-only fallback behavior when an audit finding is already known. |

## Fully local Table Lab

The student-worker Table Lab follows a separate, offline path:

| File | Purpose |
| --- | --- |
| `local_table_customization.py` | Student-editable local extraction policy. |
| `local_table_extractor.py` | Extracts PDF tables with `pdfplumber` and produces accessible starter HTML. |

This path is used only by `/table-lab` and `/api/table-jobs`. It does not import
the Bedrock table pipeline or make AWS, S3, BDA, or network calls. Start local
experiments in `local_table_customization.py`; the first extracted row is treated
as the column-header row by default.

## Current AI decision

When a table is missing a caption or headers, `TableRemediationPipeline` can send
Bedrock a limited preview of the table and a few text nodes immediately preceding
it. Bedrock returns one of four decisions:

- `caption_only`
- `header_row`
- `caption_and_header`
- `ignore`

The current policy only applies a decision with `high` confidence. Header-row
recommendations are additionally rejected unless the returned header count exactly
matches the number of columns in the table.

## Safe first changes

Start in `table_customization.py`.

- Set `allow_caption_extraction=False` to prevent nearby text becoming a caption.
- Set `allow_header_inference=False` to prevent Bedrock from creating headers.
- Set `remove_promoted_source_text=False` to retain the original text after it has
  been copied into a caption or header.
- Edit `PRE_TABLE_CONTEXT_PROMPT` to provide examples from your journal PDFs.

Avoid changing `table_remediation.py` until you know whether the output problem is
an AI classification problem or a structural HTML problem. That file directly
changes document markup and has broader effects.
