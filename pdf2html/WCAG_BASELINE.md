# WCAG 2.1 AA baseline: local article reader

Date: 2026-10-18. Step 1 only. No application fixes made in this step.

## Scope and method

Scope: localhost upload form, its missing-file and invalid-PDF error responses,
and the saved article outputs for 965899.pdf and 964971.pdf. The synthetic demo,
ILLiad, AWS deployment, and future uploads are outside this baseline.

Methods: source inspection, parsed HTML structure, local Flask response tests,
and calculated contrast for explicit interface colors. No live browser layout,
keyboard interaction, automated browser accessibility engine, or NVDA session was
performed. Those results are deliberately unverified. This is an initial audit,
not a conformance certification. Sampling cannot establish that arbitrary future
PDF conversions conform.

Evidence is in `local_runs/wcag-baseline-2026-10-18/`: unmodified article copies,
SHA-256 hashes and DOM inventory in `evidence.json`, upload/error HTML snapshots,
and `upload-evidence.json`. `audit_reader_baseline.py` reproduces the article
inventory and contrast calculations without modifying source outputs.

## Findings

### F1: image alternatives are placeholders (1.1.1, Fail)

965899 contains 8 images; 964971 contains 12. Their alt attributes state that a
descriptive alternative has not been supplied. Attribute presence does not provide
an equivalent alternative for informative figures. Publisher logos and other
decorative content also need classification. No actual vision-generated alt text
is available. The older article lacks data-alt-status markers, but has the same
placeholder wording; checking markers alone would miss it.

### F2: tables lose programmatic structure (1.3.1, Fail)

Both documents contain zero HTML tables. The lithium article has 3 paragraphs
starting with 'Table '; the kinase article has 19 such paragraphs (mentions may
include continuations and are not a count of distinct source tables). Titles,
headers and values have been flattened into text. Screen readers cannot navigate
cells or establish row/column associations. CSS changes cannot repair this.

### F3: heading semantics are incorrect (1.3.1 and 2.4.6, Fail)

All detected article section headings are h2, including numbered subsections such
as 5.1 and 3.1. Both source titles repeat below the generated h1. In 965899,
chemical equations, '87.50 Li, 99.18 Co Tao et al.,[87]', and 'No data was used for
the research described in the article.' are also h2 elements. These do not form
a meaningful heading hierarchy. The numeric-prefix/font-size heuristic in
pdf_reader_extract.py requires correction.

### F4: reading order is suspect (1.3.2, Needs manual verification)

964971 has heading order 1, 4, 5, 2, 3, 3.1... in the generated DOM. Compare this
against the original PDF to distinguish extraction errors from intentional source
placement. Review multi-column transitions, captions, references, and scientific
notation. Do not automatically reorder based only on section numbers.

### F5: visual adaptability and keyboard behavior are unverified

The renderer supplies a skip link, native details/summary page navigation,
focusable page anchors, and explicit focus styling for links, summary and tabindex
elements. IDs are unique and fragment targets resolve in both articles. These are
positive structural checks, not evidence of successful keyboard operation.
No word-breaking rule exists for long DOI/URL/formula strings; narrow-screen
overflow is a risk to test, not an observed failure. Upload controls use native
focus presentation rather than the explicit reader focus style.

### F6: error/status process needs interaction testing

Empty and malformed PDF submissions return HTTP 400 with visible role=alert text.
The form has associated labels. File-size limit is 40 MB, but the form does not
state it and no custom 413 handler is implemented. No progress indicator is
implemented during synchronous processing. Its absence alone is not a 4.1.3
failure; verify announcements and behavior during actual navigation and processing.

### Positive measurements

Both articles have one main landmark, one h1, meaningful document titles, lang=en,
unique IDs, and valid internal link targets. Explicit color contrast ratios:

| Pair | Ratio |
| --- | --- |
| Body #192b3b / white | 14.48:1 |
| Muted #435c70 / #f2f5f8 | 6.39:1 |
| Link #125d91 / white | 7.00:1 |
| Button white / #143d5a | 11.38:1 |
| Focus #a14b00 / white | 5.96:1 |

These cover specified interface colors, not raster-image text or every computed
browser state. Native controls, image text, hover states and visited links need
visual testing.

## Complete A/AA checklist

P = pass within the stated static/sample scope; F = demonstrated failure;
M = needs manual or additional technical verification; N = not applicable in the
sampled workflow. N does not carry over to other documents or future features.

| Criterion | Level | Status | Evidence / next check |
| --- | --- | --- | --- |
| 1.1.1 Non-text Content | A | F | F1: 20 image placeholders; classify and describe figures. |
| 1.2.1 Audio-only and Video-only (Prerecorded) | A | N | No media in sample. |
| 1.2.2 Captions (Prerecorded) | A | N | No media. |
| 1.2.3 Audio Description or Media Alternative (Prerecorded) | A | N | No media. |
| 1.2.4 Captions (Live) | AA | N | No live media. |
| 1.2.5 Audio Description (Prerecorded) | AA | N | No video. |
| 1.3.1 Info and Relationships | A | F | F2/F3: absent tables and incorrect headings. |
| 1.3.2 Meaningful Sequence | A | M | F4: compare DOM order to source. |
| 1.3.3 Sensory Characteristics | A | M | Check article instructions and figure references. |
| 1.3.4 Orientation | AA | M | No explicit orientation lock; test both orientations. |
| 1.3.5 Identify Input Purpose | AA | N | No fields collecting listed personal information. |
| 1.4.1 Use of Color | A | M | Interface links underlined; inspect source figure encodings. |
| 1.4.2 Audio Control | A | N | No audio. |
| 1.4.3 Contrast (Minimum) | AA | M | Explicit UI colors pass; image text/computed states untested. |
| 1.4.4 Resize Text | AA | M | Test 200% text resizing for loss/clipping. |
| 1.4.5 Images of Text | AA | M | Inspect raster figures; distinguish essential diagrams from text. |
| 1.4.10 Reflow | AA | M | Test 320 CSS pixels / equivalent zoom; table exceptions only where justified. |
| 1.4.11 Non-text Contrast | AA | M | Test controls, focus states and meaningful graphics. |
| 1.4.12 Text Spacing | AA | M | Apply required line, paragraph, letter and word spacing overrides. |
| 1.4.13 Content on Hover or Focus | AA | N | No author-supplied hover/focus popovers in samples. |
| 2.1.1 Keyboard | A | M | Exercise all controls, anchors and scroll regions. |
| 2.1.2 No Keyboard Trap | A | M | Verify forward/backward navigation and escaping regions. |
| 2.1.4 Character Key Shortcuts | A | N | No character shortcuts implemented. |
| 2.2.1 Timing Adjustable | A | N | No user time limit; backend timeout does not require timed user input. |
| 2.2.2 Pause, Stop, Hide | A | N | No auto-updating, moving or blinking content in sampled markup. |
| 2.3.1 Three Flashes or Below Threshold | A | N | Static images and text; no flashing content found. |
| 2.4.1 Bypass Blocks | A | M | Skip target exists; verify focus transfer in browser. |
| 2.4.2 Page Titled | A | P | Descriptive titles on these articles, upload and error pages. Filename fallback not universally adequate. |
| 2.4.3 Focus Order | A | M | Check interaction order and page-anchor destination. |
| 2.4.4 Link Purpose (In Context) | A | P | Sampled reader links name article/page/example destinations. |
| 2.4.5 Multiple Ways | AA | M | Define page-set scope; verify applicability of process-step exception. |
| 2.4.6 Headings and Labels | AA | F | F3: equations/data incorrectly exposed as headings. |
| 2.4.7 Focus Visible | AA | M | CSS present; check all controls and clipping in browser. |
| 2.5.1 Pointer Gestures | A | N | No multipoint or path-based gesture requirements. |
| 2.5.2 Pointer Cancellation | A | M | Native controls used; test pointer activation/cancellation. |
| 2.5.3 Label in Name | A | M | Native labels present; verify computed accessible names. |
| 2.5.4 Motion Actuation | A | N | No motion controls. |
| 3.1.1 Language of Page | A | P | lang=en matches sampled English content. Hardcoded language unsafe for other uploads. |
| 3.1.2 Language of Parts | AA | M | Review any foreign-language passages; technical terms alone need not be tagged. |
| 3.2.1 On Focus | A | M | No focus handlers found; check actual browser behavior. |
| 3.2.2 On Input | A | M | Explicit submit button; verify changing controls causes no unexpected navigation. |
| 3.2.3 Consistent Navigation | AA | P | Same reader navigation construction on both sampled articles. |
| 3.2.4 Consistent Identification | AA | P | Shared controls use consistent labels in sampled markup. |
| 3.3.1 Error Identification | A | M | Errors returned as text; verify association and all error paths, including 413. |
| 3.3.2 Labels or Instructions | A | M | Labels and formats provided; assess size-limit instruction and model setup requirements. |
| 3.3.3 Error Suggestion | AA | M | Missing/bad-file guidance present; oversized-file recovery untested. |
| 3.3.4 Error Prevention (Legal, Financial, Data) | AA | N | No legal commitment, financial transaction or deletion/modification of user-controlled stored data in scoped flow. |
| 4.1.1 Parsing | A | M | No duplicate IDs/broken fragments; full HTML validation still needed for WCAG 2.1 baseline. |
| 4.1.2 Name, Role, Value | A | M | Native controls/labels present; inspect accessibility tree and screen-reader operation. |
| 4.1.3 Status Messages | AA | M | Error alert markup present; full-page response and processing behavior need assistive-tech testing. |

## Next step

Step 2 should correct heading detection, remove duplicated presentation headings,
retain source document language, and verify reading order against the PDFs.
Keep table reconstruction and image alternatives tracked as explicit blockers
for their dedicated steps. Do not mark either article conformant at this point.

## References

- WCAG 2.1: https://www.w3.org/TR/WCAG21/
- Conformance requirements: https://www.w3.org/TR/WCAG21/#conformance-reqs
- Evaluation methodology: https://www.w3.org/WAI/test-evaluate/conformance/wcag-em/
- Tables guidance: https://www.w3.org/WAI/tutorials/tables/
