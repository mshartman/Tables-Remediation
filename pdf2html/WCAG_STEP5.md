# Step 5: Upload instructions and error recovery

Scope: local preview form, 2026-10-18. No AWS or AI calls.

The file field now describes supported formats, the 40 MiB total request limit,
password restrictions and embedded HTML images. Processing instructions explain
the wait and discourage duplicate submissions without making an unverified live
progress claim.

Server error responses have a distinct page title, an error heading, a recovery
link to the file input, and error text associated using aria-describedby and
aria-invalid. These are full-page navigation responses, not dynamic alerts;
screen-reader announcement behavior still needs manual verification.

Ordinary validation/conversion failures retain the optional title and explicit
image-description choice. Values remain template-escaped. Browsers cannot restore
file inputs, so instructions request file reselection. Oversized requests return
a custom 413 recovery page without reparsing the rejected body; this page explains
that title and checkbox state could not be recovered. AI remains unchecked by
default and is never silently selected following an oversized request.

Fourteen tests pass across upload, reflow, keyboard, structure and image-description
modules. New tests cover initial instructions, missing/unsupported files, mocked
conversion failure, escaped title retention, checkbox retention, the 413 handler,
error associations and retained security headers. Tests use Flask's local client;
they are not NVDA/JAWS or browser-native file-picker interaction tests.

Remaining: manual announcements and focus recovery checks, real native file-size
selection, and service-failure classification. The broad existing conversion error
handler can include non-document failures, so its guidance allows contacting the
local tool operator rather than claiming the document is always invalid.

Next: image alternatives and the remaining missing-description blockers, without
claiming that placeholder alt text satisfies accessibility requirements.
