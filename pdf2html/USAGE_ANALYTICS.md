# Usage analytics for LIVEFROMNEWYORK

## What is captured

The service emits small JSON events to standard output. App Runner sends
application output to its CloudWatch application log group, outside the container
filesystem. There are no new database or model-service calls. Optional first-party
browser correlation is described below; it makes no separate tracking requests.
See [App Runner application logs](https://docs.aws.amazon.com/apprunner/latest/dg/monitor-cwl.html).

| Event | Meaning |
| --- | --- |
| submission_requested | A server-side POST to an upload/job endpoint, including invalid submissions. Not every button click. |
| submission_error | That submission returned HTTP 400 or higher. |
| job_queued | A validated submission created new work, rather than reusing a job. |
| job_started | Work acquired the processing lock. Queue time ends here. |
| job_completed | Generation and ZIP packaging finished; asynchronous job status was saved. |
| job_failed | Work failed, with a coarse error category, never the error message. |
| job_cache_hit | A POST reused an existing completed, queued or processing job. Client-side cache shortcuts may not POST. |
| html_response | A GET requested HTML; status and preview/report classification are recorded. |
| zip_response | A GET requested a results ZIP; HTTP status is recorded. |

No status-poll, health, OPTIONS or HEAD events are emitted. Queue, processing and
elapsed times use a monotonic clock. They exclude upload time and browser polling,
redirect/loading time. Completion is not a claim that a patron read the result.

Successful selected-preview responses (200/206) are delivery counts, not unique
visits. Refreshes, byte-range requests, automated traffic and prefetches can repeat
them; 304 responses are not fresh deliveries. For legacy/form jobs without saved
job status, only the known reader output paths are classified as legacy previews.
Other HTML, including reports, remains separate. View-mode switches, declines of
the AI notice, reading time and verified patrons are NOT tracked.

Completed and failed jobs include available provider totals: BDA documents/pages,
recorded Bedrock calls/tokens, and OpenAI attempts, responses, tokens, descriptions
and failures. OpenAI attempts include failed requests; Bedrock counts come from
the existing tracker and may omit failed calls/retries. Missing reports and
partial failures can omit token usage. Availability flags indicate a report or
tracker was present, not complete billing coverage. Optional partial dollar
estimates require configured rates; missing rates are not treated as free usage.

## Privacy and limitations

Events have random per-event IDs for export deduplication. When configured,
they also have keyed browser and document pseudonyms, not verified user IDs.
The allowlist excludes IP addresses, user agents, cookies, credentials, URLs,
raw job IDs, document hashes, raw transaction numbers, filenames, titles and document
content. Pseudonyms still link activity and should be treated as sensitive.
There is no new public analytics endpoint. This is not a privacy audit
of existing diagnostic logging or the existing run-file routes: older diagnostic
messages can contain paths or other sensitive information.

Analytics is best-effort and fail-open: a logging failure cannot fail a conversion.
An abruptly terminated container can have a queued/started job without a terminal
event. Events already collected in CloudWatch do not depend on local_runs, but
retention/deletion settings still apply. Do not subtract unrelated daily cohorts
to infer failure rates or treat missing completion events as confirmed failures.
There is no backfill for work completed before this feature was deployed.

## First AWS setup

1. Push/deploy the rebuilt LIVEFROMNEWYORK image using the normal release process.
   Nothing in this change deploys or resumes the service automatically.
2. Keep `READER_LAYOUT_MODE=geometry`. Analytics is enabled by the image setting
   `ACCESSIBLE_HTML_ANALYTICS_ENABLED=true`; set it to false to opt out. ILLiad
   website API settings do not need to change. Browser correlation requires the
   additional setup below; without it anonymous operational counts still work.
3. In AWS select us-east-1, open App Runner, select PDFtoHTML, then Logs.
4. Open its Application logs in CloudWatch. Select the APPLICATION group,
   `/aws/apprunner/PDFtoHTML/<service-id>/application`, not the service/deployment
   log group. Do not type the angle brackets; select the real group in the console.
5. Review access and set the institution-approved retention period on that log
   group. CloudWatch defaults to indefinite retention unless configured otherwise;
   changing retention can delete older logs. This implementation changes no AWS
   retention settings. See [CloudWatch retention](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/Working-with-log-groups-and-streams.html#SettingLogRetention).
6. Run one small, non-sensitive conversion and reopen its result. A new job should
   produce requested/queued/started/completed events and an HTML response. Reusing
   it through the API should produce cache_hit without another conversion event.
   The conversion still incurs its normal configured AI charges; analytics does not
   add AI calls. All implementation tests used mocked providers/offline rendering.
7. Open CloudWatch Logs Insights, choose that application group and a bounded time
   range, and paste a query below. JSON fields are discovered automatically.
   Save useful queries; optionally add their charts to a private CloudWatch
   dashboard. AWS IAM controls access; no patron-facing dashboard is added.
8. Check data after a fresh deployment before relying on it. Logs storage,
   ingestion and queries have AWS charges; this is not a zero-cost guarantee.
   Avoid unnecessary auto-refreshes and broad time ranges. No CloudWatch settings
   were changed or queries executed by the implementation.

CloudWatch supports saved queries/charts and JSON field discovery:
[Logs Insights documentation](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/AnalyzingLogData.html).

## Saved queries

### Daily event totals

```text
filter analytics = "pdf2html_usage" and schema_version = 1
| stats count(*) as events by bin(1d), event
```

### Completed-job performance

```text
filter analytics = "pdf2html_usage" and event = "job_completed"
| stats count(*) as completed, avg(queue_ms)/1000 as mean_queue_seconds,
        avg(processing_ms)/1000 as mean_processing_seconds,
        pct(elapsed_ms, 50)/1000 as p50_elapsed_seconds,
        pct(elapsed_ms, 95)/1000 as p95_elapsed_seconds by bin(1d)
```

### Provider usage, including captured partial failures

```text
filter analytics = "pdf2html_usage" and event in ["job_completed", "job_failed"]
| stats sum(bda_pages) as recorded_pages, sum(bedrock_calls) as recorded_bedrock_calls,
        sum(bedrock_input_tokens) as bedrock_input, sum(bedrock_output_tokens) as bedrock_output,
        sum(openai_attempts) as openai_attempts, sum(openai_input_tokens) as openai_input,
        sum(openai_output_tokens) as openai_output,
        sum(bedrock_usage_available) as jobs_with_tracker,
        sum(openai_usage_available) as jobs_with_openai_report by bin(1d)
```

### HTML delivery and missing-result responses

```text
filter analytics = "pdf2html_usage" and event = "html_response"
| stats count(*) as responses by html_kind, http_status
```

### Failures and cache reuse

```text
filter analytics = "pdf2html_usage" and event = "job_failed"
| stats count(*) as failures by error_category
```

```text
filter analytics = "pdf2html_usage" and event = "job_cache_hit"
| stats count(*) as reuse by cache_state
```

These are event counts, not audited transactions. Queries use CloudWatch ingestion
timestamps; local exports below use the event's UTC date. Check time-zone/range
settings before comparing daily totals. CloudWatch aggregation functions are
documented in [stats](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_QuerySyntax-Stats.html).

## Optional local export report

For exact event-ID deduplication of an exported sample, use the local utility.
It reads raw JSONL or CloudWatch JSON with an `events` array of `message` strings;
it does not read Insights CSV/table exports or arbitrary prefixed diagnostic text.
It needs no AWS credentials and makes no network calls.

```powershell
python analytics_report.py "C:\path\to\analytics-export.json" --output local_runs/analytics/summary.json
```

Replace the example input path with the actual export file. The optional output
must be new; an existing report is not overwritten. The report contains only
aggregate counts, approximate identified-browser/document counts, UTC daily buckets, provider totals, completed-job timing
statistics and coarse failure categories, never raw events. Its p95 uses nearest
rank and may differ slightly from CloudWatch's percentile implementation.
Keep raw exports private, including any unrelated diagnostic lines. Store them
under ignored `local_runs/analytics/`, not in Git. A zero/empty report means no
valid events were found in that export/range, not proof of zero service usage.

If you want to create that JSON export with your existing approved AWS login,
run this optional example from the pdf2html project folder. Paste the real
application log-group name when prompted. This downloads ALL application log
messages in the last seven days, so the raw file can contain diagnostic data;
the default summary omits pseudonyms. Automatic CLI pagination is left
enabled. See [FilterLogEvents](https://docs.aws.amazon.com/cli/latest/reference/logs/filter-log-events.html).

```powershell
New-Item -ItemType Directory -Force local_runs/analytics | Out-Null
$LogGroup = Read-Host "Paste the App Runner application log-group name"
$Stamp = Get-Date -Format yyyyMMdd-HHmmss
$End = [DateTimeOffset]::UtcNow
$StartMs = $End.AddDays(-7).ToUnixTimeMilliseconds()
$EndMs = $End.ToUnixTimeMilliseconds()
aws logs filter-log-events --log-group-name "$LogGroup" --start-time $StartMs --end-time $EndMs --region us-east-1 --no-cli-pager --output json | Out-File "local_runs/analytics/events-$Stamp.json" -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw "Export failed. Check AWS login and log permissions." }
python analytics_report.py "local_runs/analytics/events-$Stamp.json" --output "local_runs/analytics/summary-$Stamp.json"
```

## Optional private browser and PDF breakdowns

The NonShib helper stores a random UUID in first-party localStorage for up to
90 days from creation. It creates no cookies, reads no usernames, and honors
Do Not Track, Global Privacy Control, and
`window.ILLIAD_ACCESSIBLE_HTML_ANALYTICS_ENABLED = false`. Blocked storage or
missing crypto simply omits the ID. Clearing storage resets the ID; a shared
browser combines different patrons, and one person on two browsers is counted
twice. This is browser usage, NOT reliable per-person usage. Signing out does
not clear this browser ID. Obtain institutional approval for the tracking notice
and retention policy before enabling it for patrons.

The ID accompanies a conversion submission and the generated HTML fetch, not
polling requests. The server HMACs it with a secret before emitting `browser_id`.
It separately HMACs the ILLiad transaction number into `document_id`. That groups
the same delivered PDF across regeneration, not the same article delivered under
different transactions. Without a valid numeric transaction number, grouping is
per job (`document_scope = job`). Only CloudWatch receives the pseudonyms; the
job's public status and HTML do not gain analytics identifiers. A subsequent
reader is associated with their own browser, never the original submitter's.

### Configure the server first

1. Generate a 32-byte cryptographically random key locally. This command copies
   its hex representation to the clipboard without printing it:

   ```powershell
   $Bytes = New-Object byte[] 32
   $Rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
   try { $Rng.GetBytes($Bytes) } finally { $Rng.Dispose() }
   $Key = -join ($Bytes | ForEach-Object { $_.ToString("x2") })
   Set-Clipboard -Value $Key
   Remove-Variable Key, Bytes
   ```

2. Store the value as a server-only secret, using your existing Secrets Manager
   procedure. Add secret-backed runtime variable `ANALYTICS_ID_KEY` in App Runner.
   The value must be exactly 64 hexadecimal characters. Never put it in Git,
   the Dockerfile, website JavaScript or a public folder. Clear the clipboard
   afterward. Secret storage can have its own AWS charge.
3. Keep this key stable across deployments. Changing it splits all correlations;
   old log entries are not erased by key rotation. A missing/invalid key disables
   detailed IDs without stopping anonymous totals or the workflow.
4. Deploy the updated backend BEFORE publishing the NonShib changes. It must
   allow the `X-Accessible-Reader-Id` CORS request header. Then publish
   `js/accessibleHtmlAnalytics.js`, `js/custom.js`, `js/accessibleHtmlViewer.js`,
   `include_head.html`, and `AccessibleHtmlViewer.html` from NonShib.
5. Test two browsers and a repeated conversion. Check that document IDs match,
   browser IDs differ, and cache/HTML events do not create another processing
   charge or page count. Direct App Runner links do not supply a browser ID.

These client-supplied IDs are forgeable analytics hints. They never authorize
document access, enforce quotas or justify billing a patron. CORS is not access
control. Existing public job/file routes still need a separate authorization
design before handling production-sensitive documents. This analytics change
does not solve that limitation or audit all existing diagnostic logging.

CloudWatch remains the private data store. Restrict IAM access and set an approved
retention period. Do not publish raw logs or the following detailed reports to
the I: web folder or GitHub:

```powershell
python analytics_report.py local_runs/analytics/events.json --details --output local_runs/analytics/private-details.json
```

Example private Insights query for delivery counts by browser/document:

```text
filter analytics = "pdf2html_usage" and event = "html_response"
  and html_kind in ["selected_preview", "legacy_preview"] and http_status in [200, 206]
| stats count(*) as deliveries by browser_id, document_id
```

## Optional cost estimates

Set server runtime variable `ANALYTICS_RATES_JSON` to a JSON object containing
your verified USD rates for your region, project configuration and exact model
IDs. No rates are assumed or fetched automatically. Structure (numbers below
are deliberately fictional examples, NOT AWS/OpenAI prices):

```json
{
  "bda_per_page": 0.123,
  "bedrock": {
    "your-exact-bedrock-model-id": {"input_per_million": 1.23, "output_per_million": 4.56}
  },
  "openai": {
    "your-exact-openai-model-id": {"input_per_million": 1.23, "cached_input_per_million": 0.12, "output_per_million": 4.56}
  }
}
```

Do not paste these fictional rates into production. Keep a dated copy of each
approved rate configuration with your deployment record. Consult the official
[Bedrock pricing](https://aws.amazon.com/bedrock/pricing/) and
[OpenAI pricing](https://openai.com/api/pricing/) for the applicable rates.
Unknown models/missing rates are excluded, not priced at zero. Each terminal
job event records `estimated_reported_ai_cost_usd` when some captured usage can
be priced, always with `cost_coverage = partial`. Otherwise coverage is
`unavailable` and no dollar amount is emitted. OpenAI cached tokens use their
separate configured rate. Cost changes apply to future events, not historical
events. The estimate excludes unreported retries/failures, App Runner, S3,
CloudWatch, Secrets Manager, networking, taxes and discounts. Reconcile the
aggregate with AWS/OpenAI billing; do not treat it as a total monthly bill.

```text
filter analytics = "pdf2html_usage" and event in ["job_completed", "job_failed"]
| stats sum(bda_pages) as recorded_pages,
        sum(estimated_reported_ai_cost_usd) as partial_estimated_ai_usd by document_id
```

Use `by browser_id` instead to attribute captured processing to the requesting
browser. Viewing a cached result creates no new conversion cost estimate. Pages
are captured BDA pages processed, not independently verified accessible pages.
