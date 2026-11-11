# Offline layout comparison

## Public repository boundary

`iris_layout.py` is intentionally NOT distributed in this public repository.
It is kept only in the owner's local workspace pending redistribution permission.
The package marker and our integration wrappers are public; the private renderer
itself must be obtained separately with appropriate permission. Do not copy it
into a public branch, ZIP or release asset.

Without the private file, the App Runner integration falls back to the legacy
reader. Set `READER_LAYOUT_MODE=legacy` for public-clone deployments. Direct
`layout_compare.py` requests fail with an explanatory message instead of an
import-time crash. Tests that exercise the page-faithful renderer require the
private add-on; see the repository root README for public-only smoke tests.

This opt-in experiment adapts `src/iris_layout.py` from the colleague-supplied
`aws-iris-layout.zip`. The original archive remains in Downloads. Its deployment
scripts, Lambda handler, notifier and fonts are deliberately not installed.
Source licensing/redistribution terms must be confirmed before distributing this
adaptation. No source-code license was included in the archive (the font license
applies to the fonts, which are not copied).

The live App Runner service and ILLiad website are unchanged. The next container
build includes a feature-gated integration; see ../LAYOUT_BUILD.md. No AWS,
OpenAI, campus-model or other HTTP requests are made by this comparison command.

From the pdf2html directory, install the optional dependencies locally:

```powershell
python -m pip install --target .layout-deps -r requirements-layout.txt
python layout_compare.py "C:\Users\mshartman\Downloads\967168.pdf" --geometry-only --output local_runs/layout-comparison/967168
Start-Process "local_runs/layout-comparison/967168/layout.html"
```

The geometry-only demo uses our existing local heading/column heuristics. It is
NOT a test of BDA reading order. Only clearly ruled tables with sequential row
IDs are reconstructed locally; uncertain tables are flagged for review. It is useful
for comparing the visual page reconstruction with Reading view. Figure
descriptions remain flagged for review; no AI generation is performed.

When a matching saved BDA result.json becomes available:

```powershell
python layout_compare.py "original.pdf" --bda-json "result.json" --remediated "remediated.html" --output local_runs/layout-comparison/bda-test
```

Use the original PDF associated with that JSON. Page counts/coordinates are
validated, but matching content cannot be guaranteed automatically. Inputs stay
unchanged. Each run writes layout.html, layout-structure.json, layout-review.json
to its output directory. These are local document data, not source files to commit.

Use Article sections / Jump to page to navigate; focus moves to the destination.
Use Reading view to inspect the actual sequential reading experience. Scanned and
rotated pages use an explicit fallback instead of silently vanishing; scanned
pages without OCR require transcription. Fallback text order is unverified.

The inherited renderer retains its visual/semantic dual-layer design. Its BDA
table heuristics, figure grouping and contrast adjustments remain experimental.
The audit report is limited static checking, not a WCAG conformance claim.
Prince output and PDF/UA generation are outside this experiment.
