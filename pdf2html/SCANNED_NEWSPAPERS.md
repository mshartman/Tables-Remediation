# Scanned newspaper reading order

The local reader and faculty demo now distinguish a page-sized scan with an
existing text layer from an ordinary born-digital document. They do not run OCR.

## Changes

- On scan-backed pages, repeated left edges of substantial text lines provide
  evidence for gutters. Three or more evidenced columns activate line-level
  recovery rather than the normal two-column article ordering.
- OCR blocks spanning several columns are separated using their line geometry.
  Same-row fragments are joined left to right. Short, adjacent title lines can
  be grouped using relative font size and neighboring prose, without a
  publisher-specific font or document-title lookup.
- Spanning blocks act as local barriers in the detected column hierarchy.
  This improves geometric reading order; it does not establish article identity
  or resolve a story continued on another page.
- Original source blocks remain in the structure report. Recovered render
  blocks, gutter positions and review requirements are recorded separately.
- Each scan-backed page has an OCR warning, an original-scan disclosure, and an
  `ocr_text_review` finding in the static audit.

## Local example

The four-page Statesman example is in
`local_runs/statesman-investigation/updated.html`, with the earlier extraction
in `baseline.html` and review notes in `review-notes.json`. This folder is
ignored by Git. No AWS or model calls were made for this comparison.

## Remaining limits

The source OCR has misrecognized words, missing headline text and, in places,
text already fused across columns. Reordering cannot recover those characters.
Column count is provisional, especially on pages containing advertisements,
mixed-width stories and bad word boxes. A clean-looking page is not proof of an
accurate transcription or accessible reading order.

For archival newspapers, the next substantive improvement is a separately
tested OCR/layout engine operating on the original page images. Compare it
against manually transcribed passages from several publications and layouts;
measure character accuracy, column order, article boundaries and omissions.
Do not silently replace source wording with generative guesses.

## Production article reader

The production path also has a scan-specific reading-layer repair, in
`layout_scan_reading.py`, called by `layout_compare.finish_output` before
article navigation is generated. It leaves the Page view's source background
and positioned visual lines intact. This is separate from `extract_article`.

`layout_pipeline.build_layout_preview` now forwards the saved BDA structure in
geometry mode as well as BDA mode. On scan-backed pages, a sufficiently complete
saved BDA transcription is preferred over the PDF's older OCR. Text and heading
labels come from that saved extraction, not from generative rewriting. The
geometry-only column recovery remains a fallback when saved BDA is unavailable.

Article grouping uses BDA headings and spatial overlap with their text columns.
This helps keep a spanning story's continuation together when a separate inset
story starts in the adjacent column. This remains a heuristic, not verified
article identity. Every selected text element is retained exactly once, either
in the reading layer or a source-details disclosure. Publication metadata and
small pre-title publication graphics are retained in that disclosure.

Saved figures are cropped from the original PDF; existing descriptions are
carried forward where identifiable. The usual final image-description stage can
then process missing descriptions under its existing call limit. Re-rendering
from saved job files does not itself invoke BDA or a model.

Pages with existing semantic tables or visible source annotations abstain from this replacement. Incomplete
or invalid BDA input cannot silently discard the existing page transcription.
The report records each page's source, method, status and review requirements in
`layout-review.json` under `scan_reading_recovery`.

The production-path comparison for the supplied job is in
`local_runs/statesman-production/production-fix/layout.html`. The job's existing
BDA JSON and remediated HTML were downloaded for this comparison; no new BDA or
AI processing was requested. Browser checks confirmed that the section link for
"Frosh Hold Elections" scrolls to and focuses its heading.

Deploy a rebuilt `Dockerfile.apprunner` image to activate the production change.
Both new runtime modules are explicitly copied into the image. Existing S3
results and browser/server cache entries are not rewritten by a deployment;
they need deliberate regeneration. Do not delete or extend a result's original
30-day retention deadline to test this change. Restart local Python services to
load source edits. No live website or stored production artifact was modified
as part of the local comparison.
