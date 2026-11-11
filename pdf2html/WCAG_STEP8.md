# Step 8: Final-output static audit

New conversions save accessibility-report.json alongside the HTML. Existing jobs
can be checked at /article/<job>/accessibility-report, which audits current HTML
on demand. Older jobs without extraction reports explicitly request regeneration
for table checks.

Checks cover document title/language presence, main/h1 counts, duplicate IDs,
unresolved fragment and ARIA references, missing/placeholder image alternatives,
AI-description review, tables lacking header cells and extraction review findings.
Findings are not a complete accessibility audit and do not certify semantic
correctness. A clean static result is manual_review_required, never compliant.

21 regression tests passed. The latest 964894 output was checked via Flask's
local test client; invalid job IDs return 404. No cloud or model calls were made.

Remaining work is substantive: complex scientific tables and missing meaningful
image descriptions, followed by source comparison and assistive-technology testing.
This checklist must not be presented as a full WCAG 2.1 AA certification.
