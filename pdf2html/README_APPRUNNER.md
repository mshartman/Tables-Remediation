# App Runner Deployment

This service can be deployed to AWS App Runner as a low-traffic HTTPS API for on-demand accessible HTML generation.

## What This Gives You

- public HTTPS endpoint for the remediation API
- no reverse proxy required on your ILLiad host
- simple fit for low traffic such as fewer than 50 requests per day

## Important Limitation

This current App Runner packaging still stores job files on the container filesystem under `local_runs/`.

That means:

- completed jobs are not durable across container replacement or redeploys
- this is acceptable for a first pilot, but not ideal for long-term production
- the next production-hardening step is moving job state to DynamoDB and output files to S3

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
- `bedrock-data-automation:*` or a narrowed equivalent
- `bedrock-data-automation-runtime:*` or a narrowed equivalent
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

Once the pilot works, migrate:

- job metadata from `job_status.json` to DynamoDB
- generated files from `local_runs/` to S3

That will make results survive restarts and redeploys.
