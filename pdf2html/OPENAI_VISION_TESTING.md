# OpenAI image-description testing

Run start-openai-reader.ps1 from PowerShell after stopping the existing preview
server on port 8001. Its hidden prompt passes the key to the Python process for
that session; it does not save the key to a file. Open http://127.0.0.1:8001/,
select an article, check image descriptions and select OpenAI.

The current testing model is gpt-5-nano. Official documentation lists image input
and $0.05 input / $0.40 output per million tokens, but marks the model deprecated.
Account availability has not been live-tested. There is no automatic model fallback.
Model rates alone do not establish cost per image; image tokenization and reasoning
output also affect billing.

Limits: four calls per upload, 600 output tokens per call including reasoning,
low image detail, no automatic retries, 60-second request timeout, maximum
4-million-character data URL, per-document image/caption reuse. Usage returned by
the API is included in image-description-report.json. These limits are not an
account-wide dollar budget. Repeated uploads can incur repeated charges.

Existing authored and empty alt attributes are preserved. Full-page scan reference
images are skipped. Generated alternatives are marked AI-generated and need review;
small chart text and complex scientific relationships may be missed at low detail.
Only selected images and available captions are sent, not the complete PDF. Requests
set store=false; this is not a promise of zero retention under all API policies.

Tests are mocked. No supplied API key has been written into this project or used
for a live test. Never copy credentials or private generated articles into the
public I: hosting folder. Rotate the key shared in chat.

Sources:
- https://developers.openai.com/api/docs/models/gpt-5-nano
- https://developers.openai.com/api/docs/guides/images-vision
