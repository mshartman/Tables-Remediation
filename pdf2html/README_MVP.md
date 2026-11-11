# Reading-view MVP

## Local image descriptions

Run `python reader_local.py` for localhost:8001. The optional image-description
checkbox calls the configured Ollama server only; AWS is never contacted.
Set `OLLAMA_API_KEY` and `OLLAMA_VISION_MODEL` in the server process environment.
The model must report `vision` capability. The server currently offers only
llama3.1:latest, a text model; a vision model must be provisioned by its operator.
Existing HTML alt attributes (including decorative empty alt) are preserved.
PDF-extracted raster images currently receive placeholders; PDF structure-tree
alt text is not yet mapped to extracted images. Vector-only diagrams are not
captured by this extraction path. AI descriptions are unverified and complex
scientific diagrams may need longer explanations in addition to alt text.
Up to 12 unique images per job are submitted with a 30-second timeout per call.
Descriptions are cached within a job, not across jobs. The job folder contains
`image-description-report.json` with counts and status, without credentials.

The existing AWS conversion service now renders documents through `reader_view.py`.
It produces a single article title, separate page navigation when `.page` sections
exist, page dividers, responsive images, and keyboard-focusable table scroll regions.
This improves presentation; it does not certify accessibility or repair extraction
errors. The demo `reader-demo.html` uses synthetic content, not a converted PDF.

## Optional Ollama heading suggestions

Set `OLLAMA_ENABLED=true`, `OLLAMA_MODEL=llama3.1:latest`, and `OLLAMA_API_KEY`
in the server environment. Keep the key out of source files and Docker build args.
For App Runner use a secret-backed environment variable with instance-role access.
The authorized endpoint is https://chatgpt.microsopht.com/ollama/api/chat.

At most 40 short paragraph candidates are sent once per AWS conversion, before the
audit. Only validated integer indexes are accepted. Selected text becomes an h2;
text and table values are not rewritten. Classification can still be wrong.
Requests time out after 20 seconds; errors leave the HTML untouched. The job's
`ollama_status.json` contains a status only, never credentials or remote output.
The existing Bedrock remediation step remains enabled and can still incur cost.

## Resume deployment

1. Open Docker Desktop and wait until its Linux engine is running.
2. Run `aws login` to renew the expired AWS session, then
   `aws sts get-caller-identity` to confirm the account.
3. From this directory build a new image:

```powershell
docker build -f Dockerfile.apprunner -t accessible-html-service:reader-mvp .
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 900119715649.dkr.ecr.us-east-1.amazonaws.com
docker tag accessible-html-service:reader-mvp 900119715649.dkr.ecr.us-east-1.amazonaws.com/accessible-html-service:reader-mvp
docker push 900119715649.dkr.ecr.us-east-1.amazonaws.com/accessible-html-service:reader-mvp
```

4. Set the App Runner image to that `reader-mvp` URI and deploy. If paused, resume
   the service first. Enable Ollama only after configuring its secret.
5. Submit a fresh test job and inspect output, page navigation, and tables.
   Existing output files are not automatically restyled. Redeployment can remove
   container-local jobs, as before.
