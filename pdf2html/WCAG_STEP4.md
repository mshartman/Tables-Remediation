# Step 4: Reflow, text resizing and spacing

Scope: local reader, 2026-10-18. No AWS or model calls.

## Changes

Long unbroken text can wrap without changing its underlying contents. Reader text,
page badges and navigation now use root-relative font sizes. Narrow layouts use
a zero-minimum grid track. Page badges can grow vertically; preformatted text
wraps while retaining source whitespace. Table overflow remains localized rather
than being hidden at page level. Blockquote margins scale with text size.

## Verification

Nine regression tests pass. The two Step 2 articles were rerendered in place;
baseline snapshots are unchanged. build_reflow_checks.py creates separate QA
copies with a 200% root font size and simultaneous spacing overrides: line-height
1.5, paragraph spacing 2em, letter spacing .12em and word spacing .16em.

In-app browser measurements for both 15-page and 17-page samples:

| Configuration | Client width | Scroll width | Body font |
| --- | --- | --- | --- |
| 320px viewport, normal text | 305 | 305 | 17px |
| 320px viewport, enlarged text and spacing | 305 | 305 | 34px |
| 1280px viewport, enlarged text and spacing | 1265 | 1265 | 36px |

The 15px difference is the vertical scrollbar. No page-level horizontal overflow
was measured. Narrow stress copies also had no h1, h2, paragraph, figure, page
marker or navigation bounding box extending beyond the viewport. Temporary browser
viewport settings were reset after testing.

## Limits and next work

Root-font enlargement is a test fixture, not a completed browser 200%/400% zoom
matrix. Bounding-box measurements do not prove absence of every overlap or loss
of information. Cross-browser visual inspection, real user text-only zoom, and
wide-table layout checks remain open. These samples still lack extracted HTML
tables and adequate image alternatives; neither is fully conformant.

Next: upload instructions, error identification and recovery (including oversized
uploads), without adding cloud services.

References:
- https://www.w3.org/WAI/WCAG21/Understanding/reflow.html
- https://www.w3.org/WAI/WCAG21/Understanding/text-spacing.html
