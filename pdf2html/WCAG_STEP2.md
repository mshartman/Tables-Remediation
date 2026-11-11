# Step 2: document structure

The heading detector now requires font-style evidence for numbered sections,
rejects formula fonts and equation operators, and uses h3 for numbered subsections.
Unnumbered bold section labels are recognized. Letter-spaced Abstract is normalized
to a readable heading. Size alone no longer promotes ordinary text to headings.

The reader removes exact duplicate title blocks on the first page and preserves
the source HTML lang attribute and language-of-parts attributes. PDF language
still defaults to English; automatic language detection is not claimed.

## Source-order finding

The suspicious section sequence in 964971 is present in the supplied PDF:
section 4 on physical page 2, section 5 on page 3, and sections 2 and 3 on page 5.
No numerical reordering was applied. Other column, caption, equation and table
reading-order checks remain open. This finding is not a complete 1.3.2 pass.

## Verification

Six automated tests passed across reader structure and the existing image
description tests. Structure checks cover styled main/subheadings, false numeric
and equation headings, geometry-based ordering, language preservation, title
deduplication, repeated rendering and subscript retention.

Both source PDFs were regenerated as new jobs, leaving baseline copies intact.
All 15 and 17 pages remained. The lithium article no longer exposes chemical
equations, 87.50 table data, or ordinary declaration sentences as headings.
Section labels such as References, Data availability and Acknowledgments are
recognized. Lists and complex tables still need reconstruction; no complete
1.3.1 conformance claim is made. Document-specific heading accuracy remains a
human verification item beyond these samples.

New URLs and heading inventories are recorded in local_runs/step2-results.json.
No AWS or model calls were used. The next step is keyboard navigation testing.
