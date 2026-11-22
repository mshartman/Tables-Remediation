# Faculty Document Studio: local demo

A working faculty PDF-upload concept, styled to match the ILLiad site's maroon,
navy, gray cards, Open Sans and Zilla Slab typography. It does not change the live
ILLiad pages, App Runner service, or existing upload routes.

From this directory run `python faculty_demo.py` (or `./start-faculty-demo.ps1`)
and open http://127.0.0.1:8002. Use the sample button or choose your own PDF,
confirm permission, and create a reading view. The actual local extractor builds
HTML, applies the existing reader presentation, and runs static structure checks.
Open/download the HTML or download its JSON review notes. The source PDF is read
in memory and is not saved by this demo. Limits: 20 MB per PDF, 80 pages, one
processing worker, at most four pending jobs across the demo.

Uses the dependencies in requirements-reader.txt. No AWS or AI calls are made;
no API credentials are required. Fonts are served locally, with their upstream
Open Font License texts in faculty_demo_assets/fonts. Tests use synthetic PDFs:
`python -m unittest test_faculty_demo`.

This is not a production faculty service or a WCAG certification. It does not
perform OCR, create tagged PDFs, supply image descriptions, or repair all table
and reading-order problems. Human review remains required, even when static
checks find no issues. Do not submit confidential material to a demonstration.

Output stays under ignored local_runs/faculty_demo until manually removed. Job
state is memory-only. Browser session ownership protects the demo's result routes
from other demo sessions, and submissions use CSRF tokens, but there is no campus
authentication, durable job queue, robust abuse protection or PDF sandbox. Links
stop working when the demo server restarts. Keep the server bound to loopback;
do not publish it or copy its files into the I: public folder as a live service.
