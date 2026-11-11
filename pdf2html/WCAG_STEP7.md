# Step 7: Conservative local table extraction

Added ruled-grid detection using the installed PyMuPDF engine. Accepted rectangular
tables require complete cells and bold first-row text as header evidence. HTML uses
thead, tbody and column-scoped th cells; nearby Table-number captions are associated
only within a bounded spatial range. Scientific spans use the existing inline
renderer. Caption and cell text are removed from the prose only after acceptance.

Merged cells, boundary-crossing text, embedded images, curved vector artwork and
uncertain header typography are rejected for manual review. This is conservative
heuristic support, not a guarantee of correct header relationships. Complex row
headers, multi-level headers and borderless scientific tables remain unsupported.

Every PDF upload saves table-extraction-report.json beside article.html. Detected
table/caption pages display a review notice. Undetected tables can still be missed;
absence of a notice does not prove absence of a table.

All 18 local regression tests pass, including real synthetic-PDF grid detection,
caption association, numeric preservation and uncertain-header fallback.

964894.pdf: default detection found no grids. Text-based detection of page 4 merged
neighboring prose into a 77-row, 10-column candidate and was deliberately NOT enabled.
The chemical-structure tables remain unresolved. No AWS or AI calls were used.
