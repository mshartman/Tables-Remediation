# Layout build and release

Public-clone note: the optional `experimental_layout/iris_layout.py` renderer is
private and omitted from GitHub. Public deployments should select legacy mode
unless an authorized private copy is supplied. These layout instructions apply
to the owner's workspace with that private file present; it remains untouched.

The App Runner image can now serve the approved page-faithful layout, with a
Reading view switch and article/page navigation. Nothing is deployed by changing
these source files. Existing completed jobs are unchanged.

## Enable or roll back

Build and push Dockerfile.apprunner using the normal ECR process. In App Runner,
add a plain-text environment variable:

| Name | Value | Effect |
| --- | --- | --- |
| READER_LAYOUT_MODE | geometry | The locally approved visual build; local structure heuristics. |
| READER_LAYOUT_MODE | bda | Same renderer using BDA element geometry/order; test separately. |
| READER_LAYOUT_MODE | legacy | Original reading view; default and rollback setting. |

Start with `geometry` for internal testing: that is the mode the user previewed.
It reconstructs a limited class of ruled tables with explicit header bands and
sequential row identifiers. Ambiguous tables remain source text and are flagged
in local_table_findings. Do not advertise it as full accessibility remediation.
`bda` retains semantic BDA tables but still requires source and screen-reader
comparison. The full conversion still invokes BDA and existing remediation,
even in geometry mode. These settings do not remove AWS processing costs.

OpenAI configuration is unchanged. For the new view, missing descriptions are
generated on its figure crops, subject to the existing four-call cap. Full-page
scan fallbacks are excluded. There is no campus-model or paid-provider fallback.
Authored image descriptions can only transfer when the source asset identifiers
match; review the report for remaining gaps. Source-page raster backgrounds have
empty alternatives; semantic text is provided separately.

## Job files and delivery

The original converted/remediated HTML stays in place. The new file is
`output/layout/layout.html`. Both the asynchronous job API and the local upload
results select it when successful, so ILLiad uses its existing preview URL flow.
Do not replace it by running reader_view.render_reader afterward.

Optional rendering errors retain the original reader and are recorded in
`output/layout-status.json`. Table-only jobs remain unchanged. Reports:

- `output/html/bda-layout-structure.json`: full saved BDA structure, retained
  when a layout mode is requested. This is NOT the image-only result_data map.
- `output/layout/layout-structure.json`: structure used for this render.
- `output/layout/layout-review.json`: source/contrast/fallback notes and final
  static audit, refreshed after image descriptions. Not WCAG certification.
- `output/openai-image-report.json`: separate image-description usage/status.

Generated document data is private local job data; do not commit it. App Runner
storage remains ephemeral. Redeployment can remove existing jobs/results.

## Before rollout

1. Confirm the colleague's source-code licensing permits the planned use before
   public distribution; their ZIP included no source-code license.
2. Run `python -m unittest test_layout_compare test_layout_pipeline test_openai_descriptions -v`.
3. Build the image and smoke-test `/health` and import of layout_pipeline/layout_compare.
4. Test one NEW job with READER_LAYOUT_MODE=geometry. Cached jobs will not rebuild.
5. Check page/section links on the AWS URL AND inside ILLiad. Fragment navigation
   must retain the current origin; the previously updated website JS is required.
6. Review Reading view, keyboard/screen-reader focus, zoom/text spacing, tables, alt text,
   and both layout/OpenAI reports. Use legacy to roll back if needed.

The image installs only screen-rendering dependencies. PrinceXML, font bundles,
Lambda deployment scripts and the IRIS notifier are not included.

## Reader presentation corrections

Navigation labels come only from semantic text, not the duplicated visual layer.
Repeated running headers are excluded from the section hierarchy. Geometry mode
also uses repeated heading typography and adjacent prose to recover sections
whose font names are opaque. This remains heuristic, not verified structure.

Semantic text has source-positioned bounding boxes in page view rather than
clipped 1px boxes at the page origin. Navigation, focus and browser selection
events scroll vertically without changing horizontal position. Not all screen
readers expose browse-cursor movement as DOM focus/selection events: test with
the actual assistive technology, and use Reading view for fully flowing text.
At narrow widths, Reading view activates automatically and the toggle reflects
that state. Technical details are collapsed, and notices share the page width.
Reading view now expands up to 70rem instead of retaining the PDF's physical
width. Page view scales the original geometry as a unit, targeting at least 72%
of wide windows (often more on smaller windows), instead of displaying a small
physical-size page. Background graphics, text, tables and accessible text bounds
scale together. When browser zoom changes the device-pixel ratio, the intrinsic
page scale is retained rather than counteracting that zoom; if it no longer fits,
Reading view activates to avoid horizontal overflow. Test browser zoom and actual
screen-reader navigation manually. Page navigation uses Page N.

## Page-image text layers

Development-corpus evaluation found that page-sized scan images (including tiled
strips) could take ownership of overlapping OCR text and leave semantic HTML
empty. Geometry mode now recognizes near-complete raster coverage with a
substantial text layer and retains that text instead of making each page a
figure. The original page graphics remain in Page view and an optional visual
reference disclosure in Reading view. Full-page references are excluded from
OpenAI image-description calls.

Visible reconstructed text is suppressed only when all traced source text was
invisible or covered by later images; native text and mixed visible annotations
are retained. OCR text, figure segmentation and image alternatives remain
unverified and are explicitly flagged. No OCR correction or language inference
is claimed. This is a general source-geometry/paint-order rule, not an exception
for a document, publisher, font, language or filename.

## Minor rotated margin notices

Small vertical notices confined to the outer page margins no longer force a
whole-page fallback. This requires substantial horizontal body text, a small
rotated-text proportion, and near-vertical margin geometry. Central, substantial
or otherwise rotated text still uses the conservative fallback. No publisher,
font, filename or notice wording is used to make this decision.

The original notice is retained visually in Page view and as readable text in a
Source margin notes disclosure in Reading view. It is not an article heading.
Inset scan backgrounds are recognized only with strong hidden-OCR evidence;
ordinary visible text over an inset image does not satisfy that rule. Visible
vertical notices do not cause the invisible horizontal OCR to be painted twice.

Five fresh development PDFs avoided 13 unnecessary fallback pages with this
change. That is a layout result, not a reading-order or accessibility accuracy
score. The earlier development sample retains three text-coverage review flags;
running-header removal and word normalization explain much of the difference,
but the cases remain unreviewed for release. Complex tables and actual
screen-reader behavior still need source-grounded human checks.

## Headings merged into paragraphs and mixed scan text

The next five-document development batch exposed section titles embedded at the
start of long PDF text blocks. Geometry mode now separates a short, consistently
styled leading group only when the same larger style recurs, the following text
has body typography, and a clear whole-line style change is present. Captions,
inline emphasis, body-sized labels and isolated styles do not qualify. Source
lines and their coordinates are retained, not rewritten by AI.

Repeated larger parent labels followed by recovered subheadings can establish a
main-section/subsection hierarchy. The parent style must occur at two such
transitions; a single large label is insufficient. Bold main headings and a
recurring plain subordinate style also remain distinguishable. These rules are
source-typography heuristics, not a guarantee of correct document semantics.
The saved structure records the inference method on recovered elements.

Some scanned pages contain hidden OCR plus a small visible annotation. Hiding
the entire reconstructed text layer would remove that annotation; leaving it all
visible duplicates the scan text. For recognized page-image composites, glyph
origins and Unicode values now map reconstruction spans to PDF paint visibility.
Only spans whose glyphs are all confirmed hidden/occluded are omitted visually.
Semantic OCR remains intact. Visible, unknown and ambiguous mappings remain
visible. This is separate from the existing whole-page suppression safeguard.
The optional print path retains its prior tagged-text behavior.

The private next-five-20261109 final run and comparison-final report document
this batch. Earlier runs, including an intermediate whitespace-block failure,
remain frozen. A whitespace-only source-block regression test covers the fix.
No changes to source PDFs, cloud services or paid-model configuration are needed
for these local checks. Existing deployed and cached documents do not change.

## Article-navigation cleanup

The section list now omits generated page-fallback labels and headings embedded
inside figures/tables; those remain readable in the document. Repeated margin
text is compared across pages and against paragraph as well as heading markup.
Long OCR-corrupted headers can match conservatively through text similarity,
but only in page margins. Body headings are not removed merely because they
share a label with a running header.

An early explicit Abstract, Summary or Introduction label with supporting prose
can establish the body boundary, including after a publisher cover page. Heading
candidates before it become ordinary paragraphs, retaining source text, geometry
and IDs. The boundary is not used when substantial earlier prose makes that
inference uncertain. Without such a boundary, only a sufficiently long exact
document-title match on the first page is excluded. Ambiguous front matter can
therefore remain; this is not an author-name or publisher blacklist.

Article sections use nested native lists, not an ARIA tree/menu. Repeated
subsection links include parent context in their accessible names. Existing
unique target IDs are retained and generated IDs avoid collisions. Page links
are unchanged. When no reliable sections remain, an explanatory note directs
the reader to Jump to page instead of inventing a section list.

`layout-review.json` records `article_navigation` with exclusions/reasons and
the inferred body boundary for review. It is not human sign-off. See
SCREEN_READER_TEST.md for the next manual check. No AWS deployment occurs
through these local changes; existing cached documents are unchanged.

## LIVEFROMNEWYORK release image

Local image: `accessible-html-service:LIVEFROMNEWYORK` (Linux amd64).
ECR target: `900119715649.dkr.ecr.us-east-1.amazonaws.com/accessible-html-service:LIVEFROMNEWYORK`.
The tag is case-sensitive. Building/tagging locally does not push or deploy it.

Keep `READER_LAYOUT_MODE=geometry` in App Runner for this reader; the image's
legacy default/rollback behavior has not changed. AI-provider settings and
credentials are unchanged and remain server-side configuration.

The Reading view control is above the title and is an on/off switch with a
stable name, `role=switch`, and `aria-checked`. On means Reading view; off means
Page view. Its help and tooltip do not use the phrase flowing text. At constrained
widths/zoom, the switch reflects automatic Reading view and explains why it is
unavailable. Native button keyboard activation remains in place. This follows
the [W3C switch pattern](https://www.w3.org/WAI/ARIA/apg/patterns/switch/).

This release tag does not certify WCAG conformance, change ephemeral job storage
or remove source-review notices. Existing completed jobs will not automatically
acquire the new control. Test a fresh job after an explicitly approved deployment,
including NVDA with Chrome and the ILLiad viewer's same-origin fragment behavior.

Usage analytics is included in the rebuilt release. See USAGE_ANALYTICS.md for
the optional server-only pseudonym key, NonShib browser helper, private per-delivery
reports and configured partial cost estimates. These IDs measure browsers, not
authenticated patrons. The guide also covers event definitions, privacy limits,
CloudWatch setup and queries. Deploy the backend before publishing the NonShib
helper. No database
or public reporting route is added; existing per-job usage files are unchanged.

Optional private S3 persistence for completed pages, assets, ZIPs and cache/job
records is described in S3_RESULTS.md. It is disabled until explicitly configured.
The approved default is 30 days from first conversion, without extending the
deadline on reuse. No NonShib delivery-date integration is required. This does not
make unfinished jobs restart-proof or add ILLiad patron authentication.
