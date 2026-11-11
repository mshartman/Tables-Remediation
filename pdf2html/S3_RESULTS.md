# Durable completed results in private S3

## Scope and defaults

The implementation is opt-in (`RESULTS_S3_ENABLED=false` by default). It creates
no AWS resources, changes no bucket policies/lifecycle rules, and deploys nothing.
It uses the existing instance role via boto3; no new API key or secret is needed.
Use a new release tag, for example `LIVEFROMNEWYORK-s3-1`, not a reused tag.

When enabled, a job is not completed until its output files, results ZIP, private
job record and cache pointer are uploaded successfully. New job IDs contain 128
random bits. Existing 12-hex-character IDs remain accepted. The input PDF and
local job_status.json are NOT uploaded by this results store. BDA continues to
manage its own input/intermediate objects in its separate bucket/prefix.

Stored HTML and images use the existing /runs/... paths through App Runner, not
public S3 website hosting or permanent presigned URLs. The ILLiad viewer URL and
asset-resolution logic do not need to change. The service streams artifacts from
private S3; no local job folder is needed for a completed job. Job/cache records
are JSON objects in the same private bucket, so no DynamoDB table is required.
No public bucket listing is added. Requests do not download the original PDF
from S3 to serve completed pages. App Runner must still be running to serve them.
After a successful commit, the app clears that new job's local input/output/ZIP
working copies, retaining only small local status metadata. Failed publications
are not cleaned by this success-only path. No original I: or workstation source
PDF is removed. This does not migrate or delete preexisting local-only results.

## Approved expiration policy: first conversion

The approved default is `RESULTS_RETENTION_BASIS=first_conversion`, in both code
and the Docker image. It expires a job 30 days after its first conversion request.
Reopening or reusing the cached job never renews the deadline. No delivery-date
integration or website change is needed to enable this policy.

This HTML policy is separate from the website's existing PDF notice of 30 days
from ILLiad delivery; do not describe those two clocks as equivalent. Once an
expired cache record has been physically removed, this mode cannot remember that
an old delivery was previously converted; a new valid submission can start a new
window. Stored HTML is not retroactively migrated from the old ephemeral service.

An optional future setting is `RESULTS_RETENTION_BASIS=delivery`. This requires a
timezone-qualified `delivery_at` in the upload request, for example
`2026-11-10T14:00:00-05:00`, and expires access exactly 30 days after that timestamp.
Missing, malformed or future delivery timestamps fail before conversion; delivery
dates already 30 days old return HTTP 410. Your current NonShib template does NOT
send this date, so switching to strict delivery mode now would block its uploads.
Do not invent an ILLiad field name: first identify the actual delivery-date field
and a reliable integration. A supplied date is not authenticated by this service;
syntax validation alone does not prove the institution's original delivery date.

Every status/result/HTML/asset/ZIP request checks the stored expiry, even if a local
copy survives. Expired objects return HTTP 410 without starting new work. Responses
use Cache-Control: no-store. Already downloaded copies cannot be revoked.

## AWS console setup (us-east-1)

1. Create a GENERAL PURPOSE S3 bucket, suggested name
   `accessible-html-results-900119715649`, in US East (N. Virginia). If that name is
   unavailable, choose another unique name and use it consistently below.
2. Keep all four Block Public Access settings ON, Object Ownership set to Bucket
   owner enforced (ACLs disabled), default SSE-S3 encryption, and versioning OFF
   for this initial pilot. This implementation explicitly requests SSE-S3; a
   KMS-only bucket policy needs a separate code/configuration adjustment.
3. Do not enable static website hosting, add public-read policies, or configure
   browser CORS on this bucket. Browsers speak to the existing service, not S3.
4. On the App Runner INSTANCE ROLE (not ECR access role), add the policy below.
   Substitute your real bucket name if different. Preserve existing BDA/model
   permissions. This policy grants no DeleteObject permission; it does allow
   failed multipart uploads to be aborted so unfinished parts can be cleaned up.
5. In App Runner runtime environment variables set:

   | Name | Value |
   | --- | --- |
   | RESULTS_S3_ENABLED | true |
   | RESULTS_S3_BUCKET | accessible-html-results-900119715649 |
   | RESULTS_S3_PREFIX | completed-html/v1 |
   | RESULTS_RETENTION_BASIS | first_conversion |

   Use plain-text configuration values, not Secrets Manager references. Keep
   BDA_S3_BUCKET, READER_LAYOUT_MODE=geometry, analytics, CORS and the existing
   model/secret settings unchanged. Do not add AWS access keys or AWS_PROFILE.

   Keep bucket and prefix settings stable across future deployments. Changing
   them does not move old results or update existing links automatically.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::accessible-html-results-900119715649"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"],
      "Resource": "arn:aws:s3:::accessible-html-results-900119715649/completed-html/v1/*"
    }
  ]
}
```

ListBucket is intentionally on this dedicated bucket: without that permission,
S3 can return AccessDenied instead of NotFound for a missing cache key. The app
treats denied access/outages as HTTP 503, never as permission to pay for another
conversion. An existing broad S3 policy is not narrowed by adding this policy;
review overall role permissions separately.

## Lifecycle cleanup, scoped to results only

In the NEW bucket, Management > Lifecycle rules > Create lifecycle rule:

1. Name: `expire-completed-html-results`.
2. Limit the rule to prefix `completed-html/v1/`. Never apply a blanket deletion
   rule to a shared BDA bucket or unrelated content.
3. Expire current versions 30 days after object creation.
4. Optionally add a separate incomplete-multipart-upload cleanup rule for the same
   prefix after 1 day. Lifecycle cannot use an object tag filter for that action.
5. Review the scope and save. This affects all eligible objects under that prefix,
   including uploaded files, manifests, cache pointers and incomplete publications.

IMPORTANT: Lifecycle deletion is asynchronous and uses OBJECT CREATION time,
not the ILLiad delivery timestamp stored in metadata. The application enforces
the access cutoff, but bytes may remain privately stored after access expires.
In delivery-based mode a conversion near day 30 can leave inaccessible objects
stored for up to another 30 days plus lifecycle scheduling delay. If policy
requires physical deletion by the delivery-based deadline, a scheduled cleanup
process is still needed; the lifecycle rule alone does not satisfy that deadline.
If versioning is already enabled, current-version expiration leaves old versions;
configure approved noncurrent-version/deletion-marker cleanup separately rather
than claiming the files are fully deleted.

## Build, deploy and verify

Follow the normal Docker/ECR/App Runner procedure with a NEW tag after adding
the result_storage.py module to the image (Dockerfile.apprunner already does so).
Do not enable S3 variables on an old image. Wait for successful deployment and
Running before testing. No website update is needed for first_conversion mode;
delivery mode requires the delivery-date integration described above.

1. Convert one small, non-sensitive PDF via ILLiad. Normal model/BDA charges apply.
2. Confirm status/result responses include `storage: s3`, `expires_at`, and the
   chosen `retention_basis`. In S3, expect jobs/<id>/files/, jobs/<id>/record.json,
   and deliveries/<opaque-hash>.json under completed-html/v1/.
3. Check the HTML, its figures, page/section links, and ZIP download through ILLiad.
   Direct unsigned S3 access should be denied. Do not publish bucket metadata.
4. Record the viewer URL and transaction. Redeploy the SAME image while no jobs
   are running, then reopen the old viewer URL. It should still work without the
   original container's files. A redeployment is safer for this test than losing
   in-progress work by pausing. A deliberate pause/resume test also incurs normal
   resume/hosting behavior and should be done only after jobs complete.
5. Re-submit the same delivered PDF: it should return the same job ID, without a
   second BDA/OpenAI conversion. The backend requires the matching PDF upload for
   a durable cache lookup, not a transaction number alone. Matching uses a private
   SHA-256 of the bytes plus transaction/workflow; neither hash nor raw transaction
   is returned in public details. Identical PDFs under different deliveries are
   not shared across patrons. Changed PDFs or workflow modes create new work.
6. Verify expiry in offline tests, not by shortening production retention or
   changing a real document's deadline. The test suite simulates time and storage.

## Failure and security limits

- In-progress work and its input remain local. This change preserves COMPLETED
  jobs, not a durable distributed queue. A restart can still interrupt conversion.
- Storage failures produce retryable 503 responses on retrieval/cache lookup.
  Publication failures are not reported as completed jobs. Partial uploads are
  cleaned later by lifecycle. A dangling cache pointer fails closed, so staff
  should investigate rather than clients repeatedly paying to regenerate it.
- Multi-instance simultaneous duplicate submissions are not protected by a
  distributed lock. Retain the current single-instance pilot configuration;
  cross-instance job coordination is a separate production-hardening task.
- Existing local-only results are not automatically migrated. They remain
  ephemeral unless explicitly exported before the old container disappears.
- The bucket is private, but existing viewer/job URLs still act as bearer links.
  Random IDs and PDF-possession checks are NOT ILLiad patron authentication.
  CORS and browser analytics IDs are not authorization. Do not claim protected
  per-patron access without a separate access-control design.
- Reports/ZIPs contain diagnostic output; treat those links and metadata as
  sensitive. This does not audit or sanitize all existing conversion reports.
- Persisted metadata contains filenames and transactions privately. CloudWatch
  analytics continues to emit only its allowlisted pseudonyms and counters.
- App Runner still serves the files. S3 persistence alone does not make the
  viewer available while App Runner is paused or eliminate hosting charges.

## Offline verification

```powershell
python -m unittest test_result_storage test_usage_analytics test_layout_pipeline -q
```

The tests use an in-memory fake S3 client, prohibit outbound connections, simulate
an empty replacement instance, and check cache proof, expiry, missing artifacts,
private paths, failed publication, HEAD/range downloads and analytics compatibility.
They incur no AWS/model calls. Live IAM, lifecycle, bucket policy and deployment
configuration still require the non-sensitive smoke test above.

Sources: [private buckets](https://docs.aws.amazon.com/AmazonS3/latest/userguide/access-control-block-public-access.html),
[lifecycle expiration](https://docs.aws.amazon.com/AmazonS3/latest/userguide/lifecycle-expire-general-considerations.html),
[S3 pricing](https://aws.amazon.com/s3/pricing/).
