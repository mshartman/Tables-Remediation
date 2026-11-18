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
| `local_table_extractor.py` | Uses the shared local table resolver and writes its semantic tables to HTML. |

The Table Lab is available at `/table-lab` and `/api/table-jobs`. It does not import
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

## Table resolution across the HTML readers

The shared implementation is in `content_accessibility_utility_on_aws/table_resolution/`.
Its page geometry routines come from [pdf-table-structure at d95e548](https://github.com/rishrm/pdf-table-structure/tree/d95e548).
It runs locally, assigns characters to detected cell boxes, and emits native HTML tables with row
and column spans, header IDs and associations. Missing or ambiguous text requires review.

- **Local article upload and geometry layout** use resolved cells alongside existing table detection.
  Source lines are removed only when their visible character inventory matches the resolved cells.
  Bold header evidence, common page coordinates and a region free of images and links are required
  for the new inline replacement. Captions, surrounding prose and page order stay in the article.
- **Combined and individual BDA HTML outputs** pass through the same resolver before the
  normal audit/remediation steps used by the service, CLI and PDF-to-HTML Lambda. A unique run
  of plain paragraphs accounting for all cell words can be replaced. Otherwise a labeled
  disclosure provides the reconstructed reading option alongside the original content. Existing
  semantic tables with matching cell contents are kept. Nonstandard combined-page mappings and
  page-range selections skip this step. Set conversion option `resolve_local_tables=False` to
  skip this postprocessing.
- **Saved BDA layout mode** substitutes a TABLE element where the page, bounds and source
  characters agree. A conflict retains the original elements. Rendering the optional layout
  preview still requires the authorized private renderer described in experimental_layout/README.md.
- **Table Lab**, including the portable student package, uses the same cell renderer.
  `first_row_is_header` controls its top header row. `first_column_is_row_header=None` recognizes
  common identifier headings such as Gene and Compound. Set it explicitly for other conventions.
  A table continuing across pages is represented as one segment per page.

Run `python -m unittest test_table_resolution test_reader_tables` from `pdf2html`.
The new tests use generated source documents and substitute a fixture for the remote BDA call.
This integration produces HTML. The separate Adobe PDF-to-PDF workflow generates its own PDF tags.
