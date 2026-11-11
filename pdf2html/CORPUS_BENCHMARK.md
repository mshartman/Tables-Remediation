# Evaluate general rules, not one PDF

This corpus is a development/evaluation collection, not automatically an AI
training set. The current work changes extraction rules; it does not fine-tune
models. No institutional documents should be uploaded or committed for this lab.

## Freeze the collection first

The metadata inventory found 1,471 PDFs directly under I:\Borrowing. This is a
snapshot of a working directory, not a promise its contents will stay unchanged.
The benchmark utility streams source bytes read-only to compute SHA-256 hashes.
It does not parse/render/convert PDFs, log in to AWS, or contact model providers.

```powershell
python corpus_benchmark.py "I:\Borrowing" --output local_runs/corpus-benchmark/initial --known-reviewed 963688 964971 965899 964894 967013 966990 967095 967168 968905
```

Outputs stay in an ignored private directory. Never overwrite a frozen manifest.
Save later inventories to new directories and use the hashes to detect changes.
Byte-identical copies share one split. Known reviewed examples and their exact
duplicates stay in development. The seeded 80/20 assignment is by unique hash
group, so file counts need not be exactly 80/20. A 24-document development sample
spans four file-size ranges; it is NOT yet layout-stratified.

## Before tuning more rules

1. Check for near-duplicate articles, rescans, editions and shared publisher
   templates. Group related cases before interpreting holdout independence.
2. Profile development files locally: native text vs scan, columns, page count,
   tables, figures, equations, language, and unusual page geometry.
3. Select development cases across those categories, including negative cases
   such as aligned prose that must NOT become a table.
4. Record source-grounded expectations in review-template.json. Null means
   unreviewed, not pass. Do not infer accuracy from heading/table counts alone.
5. Freeze this build's output as a baseline, then compare rule changes on the
   same source hashes. Track content loss, false table detections, cell/value
   errors, heading errors, reading order, rendering time, and accessibility.
6. Review improvements and regressions across categories. Add synthetic tests
   for the underlying pattern rather than a filename/font exception.
7. Keep the holdout out of routine tuning. Use it at release checkpoints. If its
   failures guide changes, move those cases to development and reserve new tests.

Document-level SHA-256 grouping does not detect rescans or template similarity.
The known-example list is not exhaustive. This is infrastructure for evaluation,
not evidence that the current reader generalizes or meets WCAG 2.1 AA.

## Run a bounded development baseline

```powershell
python corpus_evaluate.py --manifest local_runs/corpus-benchmark/complete-20261109/manifest.json --output local_runs/corpus-benchmark/new-baseline --max-pages 40 --timeout 90
```

This uses only the manifest's 24 fresh development examples. Every source hash is
verified before conversion, and exact source bytes are copied into the private
case directory. Code snapshots and runtime versions preserve the baseline.
Two isolated workers run with Python socket networking disabled, provider
credentials removed, a per-document timeout and a page-count limit. Oversized,
encrypted, changed, failed and timed-out sources are separate outcomes, not passes.
The holdout is never implicitly opened. Source profiling is heuristic; page count
and file size do not establish representative layout coverage.

Open index.html in the output folder for source/reader/report links. Every case
starts unreviewed in human-review.json. A converted status is NOT a correct,
accessible or manually verified result. Text coverage compares the PDF's own text
layer with semantic HTML; it does not validate OCR, tables, reading order or alt
text. Notices and original-page disclosures are excluded from this measurement.

Compare two complete runs with the same manifest and limits:

```powershell
python corpus_compare.py local_runs/corpus-benchmark/baseline-20261109-v1/baseline.json local_runs/corpus-benchmark/baseline-20261109-v2/baseline.json --output local_runs/corpus-benchmark/new-comparison
```

Never overwrite a frozen run. Review improvement candidates AND unchanged and
negative cases. Programmatic regression flags are not a substitute for human
comparison or screen-reader testing. Larger documents remain an explicit test gap.

## Fresh development batches and consistent measurements

`corpus_benchmark.next_batch(manifest, count=5)` selects another deterministic
development batch, excluding the current and previously sampled source hashes.
It returns a new manifest with provenance; it does not modify the frozen input
or select holdout files. Save it to a new private run directory.

Measurement version 2 joins inline HTML text without inserting artificial spaces
inside chemical formulas and other subscript/superscript text. Block and table
cell boundaries still separate words. Comparing old and new metric versions is
rejected. Remeasure frozen artifacts without converting again:

```powershell
python corpus_evaluate.py --remeasure local_runs/corpus-benchmark/baseline-20261109-v2/baseline.json --output local_runs/corpus-benchmark/remeasured-example
```

The new report retains the original artifact/code provenance, verifies the saved
source hashes, and records the measurement code separately. It does not change
the original HTML or baseline. Always compare runs measured with the same version.

The five-more-20261109 comparison uses before-measured and after-final. All five
converted with no automated regression flags; 13 whole-page fallbacks were
avoided. This does not mean all five are correct. The prior 24-case sample was
also rerun: 20 converted and four exceeded the page limit. Its v2-v4 comparison
retains three coverage-drop review flags. Separate agent-triage.json notes record
token-level evidence, not human sign-off. No holdout documents or paid providers
were used. Intermediate runs remain frozen, including the first trial that
exposed an inset-scan regression before the final safeguard was added.

The next-five-20261109 batch excludes the previous 29 sampled hashes. Its final
results are compared with before in comparison-final; earlier experiments are
not the release comparison. The larger-parent safeguard was rerun on all three
development batches. There are 34 selected documents in total, with four still
over the 40-page limit. No holdout documents were opened for tuning.

Heading counts are descriptive, not a score: changes must be checked against
source typography and paragraph boundaries. The mixed scan case also compares
the background-image data and semantic text before/after; those stay unchanged
while the redundant reconstructed visual OCR is removed. Actual screen-reader
behavior and complex table correctness are still outstanding human checks.
