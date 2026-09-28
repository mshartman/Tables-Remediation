# Local PDF-to-HTML Server

This adds a lightweight Flask app so you can run the PDF-to-HTML workflow from your own machine at `http://127.0.0.1:8000`.

## Important

The server runs locally, but the workflow still depends on AWS services:

- AWS credentials must be available locally
- Bedrock Data Automation access is still required
- An S3 bucket is still used for intermediate BDA processing

## Setup

From the `pdf2html` directory:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -e .
```

If you prefer an isolated environment, create and activate a virtual environment first, then run the same install command.

Optional environment variables:

```powershell
$env:BDA_PROJECT_ARN="arn:aws:bedrock:REGION:ACCOUNT_ID:data-automation-project/YOUR_PROJECT_ID"
$env:BDA_S3_BUCKET="your-bda-bucket"
$env:AWS_PROFILE="default"
$env:BEDROCK_MODEL_ID="us.amazon.nova-lite-v1:0"
```

## Run

```powershell
python local_server.py
```

Then open:

[http://127.0.0.1:8000](http://127.0.0.1:8000)

## Table Lab

Use the local Table Lab when you want to evaluate only table remediation without
other accessibility fixes changing the output.

1. Start the server with `python local_server.py`.
2. Open [http://127.0.0.1:8000/table-lab](http://127.0.0.1:8000/table-lab).
3. Upload a PDF and enter the same BDA/S3 settings used for the normal local
   workflow.
4. Open the resulting HTML and compare it with the conversion output in the ZIP.

Table Lab runs entirely locally. It extracts PDF tables using `pdfplumber`,
creates accessible HTML, audits the generated output, and applies only local,
rule-based table remediation. It does not call AWS, Bedrock, BDA, or S3. The
results ZIP includes the local extraction output, audit JSON, table-focused HTML,
and remediation report.

For scripts or PowerShell, send a PDF to the table-only endpoint:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/table-jobs" `
  -Method Post `
  -Form @{ pdf_file = Get-Item "C:\path\to\table-test.pdf" }
```

`POST /api/table-jobs` is the local-only endpoint. The existing `POST /api/jobs`
endpoint remains the AWS-backed full PDF-to-HTML workflow.

## API for Website Integration

Create an on-demand job:

```powershell
curl -X POST http://127.0.0.1:8000/api/jobs `
  -H "Content-Type: application/json" `
  -d '{
    "pdf_path": "C:/path/to/file.pdf",
    "bda_project_arn": "arn:aws:bedrock:REGION:ACCOUNT:data-automation-project/PROJECT_ID",
    "s3_bucket": "your-bda-bucket",
    "aws_profile": "default",
    "perform_remediation": true
  }'
```

Or upload a PDF directly as multipart form data to the same endpoint using `pdf_file`.

Poll job status:

```powershell
curl http://127.0.0.1:8000/api/jobs/JOB_ID
```

Fetch completed job result details:

```powershell
curl http://127.0.0.1:8000/api/jobs/JOB_ID/result
```

Typical flow:

- your website calls `POST /api/jobs` when the user clicks `View as accessible HTML`
- the frontend polls `GET /api/jobs/<job_id>`
- once status is `completed`, redirect the user to `viewer_url`

## Output

Each run is written to `pdf2html/local_runs/<job-id>/` and includes:

- the uploaded PDF
- converted HTML output
- accessibility audit JSON
- remediated HTML
- remediation report HTML
- usage data JSON
- a ZIP of the full run output
