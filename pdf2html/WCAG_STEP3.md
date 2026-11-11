# Step 3: Keyboard navigation

Scope: local reader and upload form, October 18, 2026. No AWS or AI calls.

## Changes

- Shared visible-focus outline now covers all controls, including upload inputs and buttons.
- Page-link list has padding to accommodate focus outlines inside its scrolling area.
- Focused skip link is layered above page content.
- Forced-colors mode uses the system Highlight color for focus outlines.
- Regression test protects skip/page targets, native disclosure markup, focusable table regions, and repeat-render behavior.

## Verification

In-app browser keyboard checks confirmed:

- Tab reaches the skip link; Enter moves focus to reader-content.
- Upload controls follow file, title, optional image-description checkbox, submit, example link order. Shift+Tab returns from the example link to submit.
- Space collapses and expands the Pages disclosure, with its expanded state reflected in the accessibility tree.
- Page 2 is keyboard-reachable and Enter navigates to its fragment.

Seven unit tests pass across test_reader_keyboard, test_reader_structure, and test_image_descriptions. The two Step 2 local article outputs were rerendered; baseline snapshots were not changed.

## Remaining checks

This is not a full WCAG conformance finding. NVDA/JAWS testing, other browsers, forced-colors visual inspection, and keyboard scrolling of a genuinely wide table remain to be verified. The real sample outputs currently contain no extracted HTML tables. Source-document reading order and image-description quality still require review. Existing saved outputs other than the two Step 2 examples need regeneration to receive new styling.
