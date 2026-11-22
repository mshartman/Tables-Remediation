# Current Reader Integration Snapshot

Branch: `codex/current-reader-20261122` (November 22, 2026).

This is a shareable snapshot of the current local code, not a statement about
which image is running in AWS. It does not merge PR #2, implement its header fix,
deploy AWS resources, or change the ILLiad website. Start from this branch when
testing the table integration against the newer application code.

## Included Changes

- Shared reader/navigation, scanned-page reading order and newspaper column work.
- Selective local Tesseract re-OCR before the full-service BDA pipeline, with
  quality/review reports, time budgets and preservation of the original PDF.
- The 100-page preflight and readable upload rejection, configurable BDA timeout,
  structured failure diagnostics and CloudWatch dashboard tooling.
- `AI generated alt text:` disclosure across image-description providers.
- The optional local faculty-upload demo; it remains distinct from the full
  AWS pipeline and does not perform OCR or make model/API calls.
- A public synthetic fixture at `examples/headerless-table/untagged-headerless.pdf`,
  its expected HTML, generator and fixture-validation tests.

The private `experimental_layout/iris_layout.py` is deliberately absent. Integration
wrappers and its README remain public. Public-only runs should use
`READER_LAYOUT_MODE=legacy`; optional page-layout rendering requires an authorized
private copy. Do not redistribute it in a PR, test archive, or generated package.
Local documents, conversion outputs, review checkouts, credentials, private corpus
benchmark scripts and operational job mappings are not part of this snapshot.

## Public-Only Setup

Use Python 3.13 and a virtual environment. From the repository's `pdf2html` directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-reader.txt -r requirements-layout.txt
$env:AWS_EC2_METADATA_DISABLED = 'true'
$env:RESULTS_S3_ENABLED = 'false'
$env:READER_LAYOUT_MODE = 'legacy'
$env:CAMPUS_VISION_ENABLED = 'false'
$env:OLLAMA_ENABLED = 'false'
.\.venv\Scripts\python.exe -m unittest test_headerless_fixture test_public_distribution test_document_limits test_bda_wait test_ocr_preflight test_ai_alt_label test_image_descriptions test_openai_descriptions test_local_vision_descriptions test_usage_analytics test_usage_dashboard test_result_storage
```

These tests use synthetic PDFs and mocked cloud/model/OCR calls. They require no
API keys or AWS login. For manual, real local OCR, install Tesseract with English
language data on `PATH`; the App Runner Dockerfile installs it automatically.
Tests directly exercising the private layout renderer are not part of the
public-only test command above. Do not run `test_openai_live.py` as an offline test.

The full `local_server.py` conversion path still calls BDA/Bedrock and may call
OpenAI when configured. For the local-only article extractor, use `reader_local.py`.
For the faculty demo, use `faculty_demo.py`. Neither replaces a live AWS integration
test. See `README_APPRUNNER.md`, `SCANNED_NEWSPAPERS.md` and `FACULTY_DEMO.md`.

## Table Integration Checks

The desired policy is to keep the first row as data unless tags or clear labels
and formatting establish headers. For confirmed continuations, use repeated or
verified carried-forward headings without consuming the first data row.

Please exercise the tagged This Land survey, repeated kidney-table headings,
a continuation beginning directly with data, and the untagged/headerless fixture.
Also check row-header associations after `render_reader`, not just resolver output.
In our isolated PR #2 review at `f97dd91`, its `test_reader_and_geometry` failed
because the `Second` cell did not reference `Gene-B` as its row header.

Test the interaction with re-OCR too: accepted pages use a raster-backed working
PDF. An image-overlap guard that rejects every raster-backed region can prevent
otherwise recoverable OCR tables from reaching the reader. Keep conservative
fallbacks, but report what could not be recovered instead of inventing semantics.
