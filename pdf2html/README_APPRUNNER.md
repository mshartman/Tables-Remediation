# App Runner Deployment

For the current step-by-step testing relaunch, use [AWS_RELAUNCH.md](AWS_RELAUNCH.md).
This image runs the existing BDA/Bedrock pipeline, not reader_local.py's local PDF extractor.
Do not treat this service as production-ready: in-progress jobs still use ephemeral
storage and in-process threads, and patron authorization and durable background
execution still need work. Optional completed-result persistence is described in
[S3_RESULTS.md](S3_RESULTS.md); it is disabled until explicitly configured.

This service can be deployed to AWS App Runner as a low-traffic HTTPS API for on-demand accessible HTML generation.

## What This Gives You

- public HTTPS endpoint for the remediation API
- no reverse proxy required on your ILLiad host
- simple fit for low traffic such as fewer than 50 requests per day

## Important Limitation

By default, job files live on the container filesystem under `local_runs/` and
do not survive container replacement reliably. The optional S3 results mode now
stores completed pages, assets, ZIPs, job metadata and cache mappings in a private
bucket. Completed results then survive redeployments without a DynamoDB table.
It also clears successful jobs' temporary working copies after saving them.

See [S3_RESULTS.md](S3_RESULTS.md) for permissions, lifecycle cleanup, retention
choices and deployment tests. This does not preserve unfinished jobs, migrate
old local-only results, or add patron authentication. The current website sends
no delivery timestamp: the approved default is 30 days from the first conversion
request (`RESULTS_RETENTION_BASIS=first_conversion`). Reuse does not extend it.
This is separate from the ILLiad PDF notice of 30 days from delivery. Integrate
the real delivery date before selecting optional delivery-based retention.

## Processing Limits And Failures

The full service accepts documents of **1 to 100 pages**, including table-only
uploads. The API rejects larger files with HTTP 422 before creating a job or
starting paid processing. Its response explains the page count and asks the
user to split the document into PDFs of 100 pages or fewer. Password-protected,
empty and unreadable files receive a separate explanation. The existing ILLiad
JavaScript displays the API's `error` string. The direct conversion API and BDA
processing entry point enforce the same limit as defense in depth.

`BDA_TIMEOUT_SECONDS` controls how long the worker polls an already-submitted BDA
job. The default is **300 seconds** for compatibility; valid values are integers
from **30 to 3600**. For example, set the plain-text App Runner environment
variable to `900` to allow a 15-minute polling budget after deploying the updated
image. Invalid settings fail before PDF upload/submission to BDA. This is a
polling budget, not an AWS cancellation request; in-flight SDK calls may delay
the final timeout. Longer waits occupy the service's single conversion worker
for longer, and do not guarantee success for malformed documents.

Timeouts retain their exception type, configured limit and last known status.
`ClientError` and `ServiceError` BDA statuses fail immediately rather than waiting
until the deadline. Transient status-request errors retry within the same budget;
no new BDA invocation is automatically submitted. Changing this setting requires
the updated image, but subsequent timeout changes need only the App Runner
environment setting, not a code rebuild.

Analytics emit controlled failure codes, readable reasons, stage, timeout and
known AWS error codes. They never copy arbitrary exception messages, raw BDA
error bodies, document filenames, credentials or traceback text into the usage
dashboard. Detailed application diagnostics remain restricted operational logs.

## AI Image Descriptions

AI-generated image alternatives start with the literal phrase
`AI generated alt text:` in the `alt` attribute so screen readers announce the
disclosure. The same rule applies to OpenAI, Bedrock and optional campus model
paths. Re-rendering also labels existing descriptions with `data-alt-status="ai-generated"`
without duplicating the prefix. Authored descriptions, decorative empty
alternatives and failure placeholders are not relabeled. Existing cached HTML
is unchanged until regenerated or re-rendered with the updated application.

## Selective Local Re-OCR

New full-service conversions run a local OCR preflight **after the 100-page
validation and before BDA**. Clean native text is retained. Scanned/hidden text
layers are not assumed accurate simply because they contain selectable text;
pages with no text, damaged character mappings, or excessive Latin word
fragmentation are also candidates. This is a conservative quality heuristic,
not a measurement of transcription accuracy, language correctness or WCAG compliance.

Tesseract re-recognizes selected pages from a 300-dpi raster (capped at 24 million
pixels per page). A candidate must have character-weighted word confidence of at least 85,
no more than 15% low-confidence characters, and pass character/fragmentation
checks. Major losses in text length are rejected; otherwise readable old text
must retain at least 85% of its character count. These checks reduce risk but
cannot prove that new wording or numbers are correct. Equations, handwriting,
historical print, names and tables still need human review. Arbitrary non-Latin
text is not treated as garbled merely for being non-ASCII.

Accepted pages go into a **temporary working PDF**, used consistently by BDA,
the reader and the layout renderer. The uploaded PDF stays untouched. Page
count, dimensions, ordinary link destinations and simple bookmarks are
preserved; selected pages become raster-backed OCR pages, so native font/tag
structure, editable forms and annotation interactivity are not preserved on
those working-copy pages. Their visible appearance is rendered into the image.
Unselected pages retain their original content. Temporary files are removed
after processing, including on failure, and are not added to the results ZIP.

`ocr-quality.json` records page numbers, selection reasons, outcomes and confidence
summaries, without document text or filenames. The HTML shows an OCR review
notice, including the count of selected pages that could not be safely replaced.
Missing engines, timeouts and rejected candidates keep the source layer rather
than silently advertising successful OCR. Cached conversions are reused as
before; this does not retroactively reprocess existing cached results.

The App Runner image installs Tesseract and English language data. Settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `PDF_OCR_MODE` | `auto` | Set `off` to disable the preflight. |
| `PDF_OCR_LANGUAGE` | `eng` | Tesseract language(s), such as `eng+spa`; install additional language data in the image first. |
| `PDF_OCR_PAGE_TIMEOUT_SECONDS` | `45` | Per-engine-call timeout, 5-300 seconds. |
| `PDF_OCR_BUDGET_SECONDS` | `600` | Soft document OCR budget, 30-3600 seconds. Pending pages retain source text when exhausted. |

The budget is checked between pages; raster rendering and saving may extend it.
OCR runs one engine thread at a time. It makes no external OCR/model API calls,
but it adds App Runner CPU time and queue latency; BDA still runs afterward and
its normal charges remain. Native Windows runs need Tesseract on `PATH` and
the PDF libraries from `requirements-layout.txt`. Rebuild/deploy the image to
activate this in production. The standalone local Table Lab is unchanged.

## Build The Container

From the `pdf2html` directory:

```powershell
docker build -f Dockerfile.apprunner -t accessible-html-service .
```

## Push To Amazon ECR

Create an ECR repository if you do not already have one:

```powershell
aws ecr create-repository --repository-name accessible-html-service
```

Log in to ECR:

```powershell
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com
```

Tag and push:

```powershell
docker tag accessible-html-service:latest <account-id>.dkr.ecr.us-east-1.amazonaws.com/accessible-html-service:latest
docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/accessible-html-service:latest
```

## Create The App Runner Service

In App Runner:

1. Create service
2. Source: Container registry
3. Provider: Amazon ECR
4. Image URI: your pushed image
5. Port: `8080`

Suggested runtime settings for your traffic:

- CPU: `0.25 vCPU`
- Memory: `0.5 GB` or `1 GB`

## Environment Variables

Set these in App Runner if you want defaults at the service level:

- `BDA_PROJECT_ARN`
- `BDA_S3_BUCKET`
- `BEDROCK_MODEL_ID`
- `LOCAL_SERVER_MAX_UPLOAD_MB`

Do not set `AWS_PROFILE` in App Runner.
Use the App Runner instance role instead.

## IAM Permissions

Attach an instance role that allows the service to:

- use Bedrock Data Automation
- invoke the Bedrock model used for remediation
- read and write to the S3 bucket used by the workflow
- write CloudWatch logs

At minimum, review permissions for:

- `bedrock:InvokeModel`
- `bedrock:InvokeDataAutomationAsync`
- `bedrock:GetDataAutomationStatus`
- Additional project/profile operations required by the converter; scope these using IAM and actual access-denied diagnostics.
- `s3:GetObject`
- `s3:PutObject`
- `s3:ListBucket`
- `s3:DeleteObject` if cleanup is enabled

## Health Check

Use:

```text
/health
```

## Connect It To ILLiad

Point the website JavaScript to the App Runner service URL:

```html
<script>
window.ILLIAD_ACCESSIBLE_HTML_API_BASE = "https://your-app-runner-url";
</script>
```

If you keep the default `/accessible-html`, place CloudFront or another reverse proxy in front of App Runner and map that path there.

## Recommended Next Step

Enable and verify the optional S3 completed-results mode first. For further
hardening, add a durable queue/worker design, distributed job coordination and
patron authorization. S3 persistence by itself does not solve those concerns.
