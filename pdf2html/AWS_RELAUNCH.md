# AWS testing relaunch

## Update: OpenAI image descriptions

The next image defaults IMAGE_ALT_PROVIDER to openai. BDA conversion and non-image
Bedrock remediation remain unchanged. Campus vision stays disabled. No OpenAI key
is included in the image. Existing meaningful alt text and empty decorative alt
attributes are preserved; missing descriptions and generic placeholders are targeted.

Before deploying, create a fresh OpenAI project API key (do not reuse keys pasted
into chat). Store it as a plain secret value in AWS Secrets Manager and use App
Runner's secret-backed environment variable OPENAI_API_KEY to reference it. Grant
the App Runner instance role secretsmanager:GetSecretValue on that secret only,
plus kms:Decrypt if required for its customer-managed encryption key. Do not place
the key in Docker build arguments, website JavaScript, source control or logs.

Runtime settings for this deployment:

- IMAGE_ALT_PROVIDER=openai
- OPENAI_ALT_MODEL=gpt-5-nano
- OPENAI_ALT_DETAIL=high
- OPENAI_ALT_MAX_IMAGES=4 (unique requests per job; range 0-40)
- CAMPUS_VISION_ENABLED=false
- OLLAMA_ENABLED=false

OpenAI receives the targeted image and up to 1,000 caption characters. API charges
are separate from AWS. Calls request store=false; this is not a zero-retention
guarantee. Images are read from within the job output directory or embedded data;
arbitrary remote image URLs are not fetched. Failed calls are not retried and do not
fall back to Bedrock or campus models. The job retains readable HTML and flags
unresolved images. Jobs with more than four distinct images may remain incomplete;
increase the cap deliberately after reviewing quality, latency and spending.

Inspect output/openai-image-report.json for model, calls, usage, generated, skipped,
remaining and status. The final reader audit is written after descriptions are applied.
Bedrock's earlier remediation report does not include these later OpenAI changes.
Rebuild and redeploy; existing saved results are not automatically regenerated.
The older instruction below to remove OPENAI_API_KEY is superseded for this deployment.

## Scope and stop conditions

This procedure relaunches the existing AWS BDA/Bedrock workflow in local_server.py.
It does NOT deploy reader_local.py's PyMuPDF extraction pipeline. Shared reader
presentation code is included, but extraction results will differ from localhost previews.
Campus vision is shelved; Bedrock remediation remains enabled and billable.
Nothing in these instructions has already deployed a service or incurred conversion costs.

Use only approved test documents. The API does not currently enforce patron identity
and ownership, CORS is not authentication, and unguessable result URLs are not access
control. Do not enable for patrons until authorization, upload abuse controls, and
protected result retrieval have been implemented and tested. The JSON pdf_path input
also needs to be disabled or restricted before public production use.

Jobs currently run in daemon threads and save state/results inside the container.
App Runner does not guarantee that work or files survive beyond a request. Polling
does not make these jobs durable. Use a durable queue/worker, external job state,
and private S3 outputs before production. The existing BDA S3 bucket alone does not
make this application's final HTML or job status durable. Relaunching may lose old links.

## 1. Open the right folder and start Docker Desktop

Start Docker Desktop and wait for its Linux engine to be running. In PowerShell:

```powershell
Set-Location 'C:\Users\mshartman\Documents\New project\PDF_Accessibility\pdf2html'
docker version
aws --version
```

Docker must show both Client and Server. A missing dockerDesktopLinuxEngine pipe
means the engine is not running; do not continue to build until fixed.

## 2. Sign into AWS and verify the account

Use your organization's approved AWS CLI login method (for example aws login if
supported by your installed CLI, or aws sso login for a configured SSO profile).
Do not paste access keys into source files or Docker build arguments.

```powershell
aws configure set region us-east-1
aws sts get-caller-identity
```

Verify Account is 900119715649. If you use a named profile, set AWS_PROFILE in this
PowerShell session, not in App Runner. Stop if the account is different.

## 3. Verify existing resources, do not recreate them

```powershell
aws ecr describe-repositories --repository-names accessible-html-service --region us-east-1
aws s3api head-bucket --bucket accessible-html-900119715649 --region us-east-1
aws bedrock-data-automation get-data-automation-project --project-arn arn:aws:bedrock:us-east-1:900119715649:data-automation-project/51cd7a98d8ed --project-stage LIVE --region us-east-1
aws apprunner list-services --region us-east-1 --query 'ServiceSummaryList[].{Name:ServiceName,Status:Status,Url:ServiceUrl}'
```

Confirm the BDA project is usable and still configured for document HTML output.
An access-denied response does not prove a resource is missing. Resolve permissions
instead of creating duplicate resources. In AWS Billing, configure a budget alert;
alerts are not a hard spending cap. Keep this pilot to a few small documents.

## 4. Build a new, uniquely tagged image

Use the same PowerShell session for the commands below:

```powershell
$Tag = 'relaunch-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$Registry = '900119715649.dkr.ecr.us-east-1.amazonaws.com'
$Image = "$Registry/accessible-html-service:$Tag"
docker build --platform linux/amd64 -f Dockerfile.apprunner -t $Image .
```

Stop on any build error. Do not push an old image by mistake. The Dockerfile explicitly
copies only runtime code, not local_runs or API credentials. It now includes the
reader presentation dependencies. This procedure does not need --no-cache routinely.

## 5. Smoke-test the container before paying for a deployment

```powershell
docker run --rm --entrypoint python $Image -c "import local_server; print(local_server.app.test_client().get('/health').json)"
docker run --rm -d --name accessible-html-smoke -p 127.0.0.1:8080:8080 $Image
curl.exe -i http://127.0.0.1:8080/health
docker stop accessible-html-smoke
```

Expect status 200 and {"status":"ok"}. This tests imports and HTTP serving, not
AWS permissions or conversion quality. Do not send a conversion without credentials.

## 6. Authenticate Docker to ECR and push

```powershell
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin $Registry
docker push $Image
aws ecr describe-images --repository-name accessible-html-service --image-ids imageTag=$Tag --region us-east-1
Write-Output $Image
```

Expect Login Succeeded and a push digest. If push says 403, renew login and check
account/repository permissions. If it says tag does not exist, the build/tag did not
complete in the same Docker engine. Never put angle brackets around the account ID.
Record the full new URI and the previous deployed URI for rollback. Keep the old image.

## 7. Review App Runner configuration in us-east-1

Open the existing service (historically named PDFtoHTML). Record its current settings.
If paused, Resume it first and wait for Running. Resuming runs the PREVIOUS image;
it does not automatically deploy the image you just pushed.

In Source and deployment, edit the image URI to the exact $Image value printed above.
Use the existing ECR access role (image download) and the existing instance role
(application's AWS permissions). These are different roles. Prefer manual deployment
during testing. Keep port 8080. Configure HTTP health checks on /health.
For an initial pilot, select a supported 1 vCPU / 2 GB configuration if memory errors
were an issue; this costs more than the old 0.25 vCPU / 0.5 GB setting. Confirm pricing
before selecting resources. A maximum of one instance limits cross-instance state
problems but does NOT solve restarts, background job reliability, or concurrent jobs.

Set runtime environment variables:

| Name | Value |
| --- | --- |
| AWS_DEFAULT_REGION | us-east-1 |
| BDA_PROJECT_ARN | arn:aws:bedrock:us-east-1:900119715649:data-automation-project/51cd7a98d8ed |
| BDA_S3_BUCKET | accessible-html-900119715649 |
| BEDROCK_MODEL_ID | us.amazon.nova-lite-v1:0 |
| ACCESSIBLE_HTML_ALLOWED_ORIGIN | https://sunysb.hosts.atlas-sys.com |
| LOCAL_SERVER_MAX_UPLOAD_MB | 40 |
| CAMPUS_VISION_ENABLED | false |
| OLLAMA_ENABLED | false |

Verify that the configured model remains available to this account and region.
Remove LITELLM_API_KEY, OLLAMA_API_KEY, OLLAMA_VISION_MODEL and OPENAI_API_KEY from
this AWS service if present. Do not set AWS_PROFILE or static AWS access keys there.
Removing these optional credentials does not disable the Bedrock pipeline.

## 8. Verify IAM before the first conversion

The instance role trust principal must be tasks.apprunner.amazonaws.com. Reuse the
previous working role if available, rather than replacing it with administrator access.
The application requires BDA invocation/status and any project/profile discovery
operations exercised by its converter, Bedrock model invocation, and S3 input/output
access. IAM action names for BDA use bedrock:, NOT bedrock-data-automation: or
bedrock-data-automation-runtime:. Those names are SDK/CLI service names, not IAM prefixes.

Relevant actions include bedrock:InvokeDataAutomationAsync,
bedrock:GetDataAutomationStatus and bedrock:InvokeModel. Scope model and inference
profile resources appropriately, including destinations allowed by the US inference
profile. S3 ListBucket applies to the bucket ARN; GetObject/PutObject apply to object
ARNs below it. DeleteObject is needed only for enabled cleanup. Customer-managed KMS
encryption may require additional key permissions. Use access-denied diagnostics to
identify exact missing actions/resources; do not solve errors by attaching admin access.

## 9. Deploy and verify HTTP/CORS

Save/deploy the updated configuration. Wait for successful deployment and Running.
Check deployment logs for import failures and application logs for runtime errors.
Use the URL shown by App Runner; the historical URL is below.

```powershell
$Base = 'https://ymb3yppypa.us-east-1.awsapprunner.com'
curl.exe -i "$Base/health"
curl.exe -i -X OPTIONS "$Base/api/jobs" -H 'Origin: https://sunysb.hosts.atlas-sys.com' -H 'Access-Control-Request-Method: POST' -H 'Access-Control-Request-Headers: Content-Type'
```

Expect health 200 and preflight 204 with Access-Control-Allow-Origin matching the
ILLiad origin. Use curl.exe, not PowerShell's older curl alias. A 200 health response
does not establish that BDA works. Missing CORS headers usually mean the environment
variable is missing, the wrong image is running, or an upstream error occurred.

## 10. Run ONE approved test PDF (incurs AWS usage)

Replace the sample path with a real approved test file. The old Desktop/FedEX/967168.pdf
was missing at the last check. Do not repeatedly click while a job is running.

```powershell
$Pdf = Read-Host 'Full path to the test PDF'
if (-not (Test-Path -LiteralPath $Pdf -PathType Leaf)) { throw 'PDF not found' }
$Job = curl.exe --fail-with-body -sS -X POST "$Base/api/jobs" -F "pdf_file=@$Pdf" | ConvertFrom-Json
$Job | ConvertTo-Json -Depth 6
```

Use the job status URL returned by the API to poll, waiting several seconds between
checks. Once completed, open its returned result URL. All URLs must use HTTPS.
If failed, inspect application logs before resubmitting. Review the actual text,
headings, reading order, tables and image alternatives against the PDF. Automated
success is not WCAG certification. Usage reports are useful but AWS Billing is the
source of truth for charges. Keep records of the sample, image tag and job ID.

## 11. Connect ILLiad testweb, not production

The local ILLiad_testweb folder contains the Accessible HTML button and AWS base URL;
NonShib_testweb did not contain the same integration at the last inspection. Confirm
which folder actually serves the intended test URL with the website administrator.
Before js/custom.js loads, the deployed pages need:

```html
<script>
window.ILLIAD_ACCESSIBLE_HTML_API_BASE = "https://ymb3yppypa.us-east-1.awsapprunner.com";
</script>
```

Publish the matching template, custom.js, viewer HTML/JS and styles through your normal
website deployment process. Pushing an ECR image does not publish ILLiad website files.
Test with a signed-in authorized test user: consent, PDF upload, progress, successful
result, keyboard navigation, leaving/reopening the page and stale-link recovery.
Use Network tools to confirm /api/jobs is called, rather than the synchronous /process
route. App Runner's HTTP timeout is 120 seconds, so multi-minute synchronous requests
are not suitable. The current daemon-thread jobs still require the durability upgrade.

## 12. End the testing session and rollback

Wait for active test jobs to finish, save needed results/reports, then use Actions >
Pause in App Runner if you want to stop its compute charges. This removes ephemeral
state. ECR, S3 and other retained resources may still incur charges. Do not expect old
result URLs to survive. To roll back, select the previous image URI and deploy it;
rollback does not restore deleted job files. Do not delete that image until the new
one is verified. Do not redeploy or pause during conversions.

## Revisit campus vision later

The implementation remains in local_vision_descriptions.py with its mocked tests.
describe_local now returns campus_vision_disabled without network requests unless
CAMPUS_VISION_ENABLED=true, even when a key is present. No paid fallback is triggered.
To run a controlled local experiment later, set CAMPUS_VISION_ENABLED=true in the
server process environment, start start-local-vision-reader.ps1, enter a fresh campus
key through its prompt, and explicitly select image descriptions in the upload form.
The launcher does not override the pause flag. Restart the service after environment
changes. Remove the flag or set false to shelve it again. Compare generated descriptions
against a fixed reviewed sample set before enabling anything for patrons. Do not reuse
previously disclosed credentials. This App Runner image does not currently call that
campus adapter; enabling its flag alone does not wire it into the AWS pipeline.

## Official references

- https://docs.aws.amazon.com/apprunner/latest/dg/develop.html
- https://docs.aws.amazon.com/apprunner/latest/dg/manage-pause.html
- https://docs.aws.amazon.com/apprunner/latest/dg/security_iam_service-with-iam.html
- https://docs.aws.amazon.com/bedrock/latest/userguide/bda-using-api.html
- https://aws.amazon.com/apprunner/pricing/
