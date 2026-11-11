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
