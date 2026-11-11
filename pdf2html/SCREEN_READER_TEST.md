# First manual reader test

Status: initial informal user feedback received; task-level results are not yet
recorded. The user reports that the page works but returning to the navigation
panels is not obvious. This is not a completed manual pass.
Automated DOM/JavaScript tests do not verify speech,
the virtual reading cursor, real scrolling, or usability with assistive technology.
This is a focused navigation test, not a complete WCAG conformance evaluation.

## Setup

Selected first combination: NVDA with Chrome. Record the browser,
screen-reader version, keyboard layout, Windows display scaling and browser zoom.
Do not install or change assistive-technology settings without the tester's consent.

Open these freshly generated local results, not an older deployment/cached job:

1. Cover-page case: `local_runs/corpus-benchmark/navigation-cleanup-20261109/final/new-five/cases/03-d2a86f575222/render/layout.html`.
2. Nested/repeated subsections: `local_runs/corpus-benchmark/navigation-cleanup-20261109/final/new-five/cases/05-da1d314ebdcc/render/layout.html`.
3. Optional fallback-only case: `local_runs/corpus-benchmark/navigation-cleanup-20261109/final/original-sample/cases/13-9fee5f9fcf0c/render/layout.html`.

Start in Reading view at 100% zoom. Afterward, repeat the navigation checks in
Page view and at 200% zoom. Record each combination separately; do not assume
that passing in one view/browser establishes the others.

The LIVEFROMNEWYORK preview is `local_runs/layout-comparison/LIVEFROMNEWYORK/layout.html`.
Its Reading view switch is above the title: on means Reading view, off means
Page view. At narrow widths/zoom the switch is on automatically and unavailable
until Page view fits again. Include this switch in the next keyboard/speech check.

## Tasks and expected results

1. Reach Skip to document and activate it. Reading should continue in the
   document, not remain in the header. The document title should be available
   once as the reader's top-level heading.
2. Expand Article sections. In the cover-page case, the authors and repeated
   article title must not appear as sections. They must still be readable in
   ordinary document text. Check both the page links and the screen reader's
   own heading list; hiding a link alone would not fix false heading semantics.
3. Activate Methodology, then continue reading with the screen reader. Confirm
   that reading starts at that section rather than another navigation link.
   Repeat with a nested subsection in the second document. Repeated Outcomes
   links should be distinguishable by their parent context.
4. Expand Jump to page and activate Page 3. Continue reading into that page's
   text. Check that the reading cursor does not remain in the page selector.
   Repeat with a later page and then navigate backward.
5. Read continuously across a page boundary. Record missing/duplicated text,
   incorrect column order, and whether the visible content follows vertically.
   A reading-cursor movement may not produce a browser focus event; observe it
   directly rather than infer success from a programmatic focus test.
6. Use browser Back after a section jump. The previous fragment should remain
   within the same document. Confirm no navigation trap, horizontal-only jump,
   lost place or console SecurityError. Repeat at 200% zoom.
7. In the optional fallback case, Jump to page should remain usable and the
   absence of a reliable section list should be explained. Extracted page text
   headings may remain in the screen reader's heading list; they are not article
   sections. Unverified OCR/tables are known limitations, not a pass.

## Optional NVDA commands

In Browse mode, H and Shift+H move between headings. NVDA+F7 opens the Elements
List, where Headings can be selected. Say All is NVDA+Down Arrow on the desktop
layout or NVDA+A on the laptop layout. NVDA is the configured modifier key.
Tools > Speech Viewer in the NVDA menu can display spoken output for review.
These commands are documented in the [official NVDA User Guide](https://download.nvaccess.org/documentation/userGuide.html).

To return to the header panels, open NVDA+F7, choose Landmarks, select Document
navigation, and use Move to. Then reach Article sections or Jump to page.
Verify this on the actual page; discovering the return route is part of the test,
not something to dismiss as user error.

## Record findings

Keep notes or speech transcripts in ignored `local_runs/screen-reader-results/`.
Do not commit patron information, full article transcripts or screenshots.
For each task record: document, view, browser/reader versions, exact steps,
expected result, actual speech/behavior, pass/fail/not-tested and reproduction
notes. Separate cursor/focus, scroll, and reading-order failures.

After the local test, repeat the critical jumps in a new ILLiad testweb job when
deployment is explicitly approved. Local success does not verify cross-origin
delivery, login/session behavior, or production accessibility.
