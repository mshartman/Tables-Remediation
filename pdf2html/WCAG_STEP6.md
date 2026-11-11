# Step 6: Image-alternative safeguards

The reader detects missing alt attributes, explicitly marked missing alternatives,
and the exact legacy extraction placeholder. It preserves authored descriptions
and empty alt attributes rather than guessing which images are decorative.
Missing alt attributes get an honest unavailable-description fallback, still
marked missing. This fallback does NOT satisfy WCAG 1.1.1 for informative images.

A visible, named image-accessibility notice reports unresolved and AI-generated
alternatives. Repeated rendering replaces the notice rather than duplicating it.
AI output remains explicitly unverified. Existing empty alt text is preserved,
not certified as appropriate for the image.

The optional vision workflow now reports remaining candidates on early exits,
including unconfigured or unavailable vision services. Its per-document cache
includes the source caption, preventing reuse across differing caption contexts.
The existing vision-capability gate and explicit upload opt-in remain intact.

Verification: 16 local unit tests pass. AI responses in tests are mocked; no images
were sent to AWS or Ollama. Both Step 2 article outputs and spacing QA copies were
rerendered, with baseline snapshots preserved. The actual descriptive alternatives
are still missing: this step exposes and tracks the blocker rather than claiming
to have resolved it. Screen-reader announcement testing remains outstanding.

Complex scientific figures may need a short alternative plus a detailed textual
description or data table. Neither captions alone nor a short AI description
automatically supplies all essential information.
Reference: https://www.w3.org/WAI/tutorials/images/complex/

Next: table extraction and semantic structure, still using local processing.
