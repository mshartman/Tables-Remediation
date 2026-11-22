# Untagged, Headerless Table

`untagged-headerless.pdf` is a synthetic, one-page PDF containing only this grid:

```text
+------+----+
| 2022 | 12 |
+------+----+
| 2023 | 15 |
+------+----+
| 2024 | 19 |
+------+----+
```

All six values are data. There are no printed column labels, bold header fonts,
shaded heading bands, marked-content IDs, or PDF structure-tree tags. The first
row is styled exactly like the remaining rows. It contains no patron or article data.

## Expected Result

Keep all three rows and six values in their original row/column positions.
Do not turn `2022` and `12` into column headers. `expected.html` demonstrates
the row-preservation contract with three body rows and no `thead` or `th`.
If headings cannot be established, retain data and flag the table for review,
or conservatively leave the original content intact. Do not claim the headers
were repaired. A caption identifies a table; it does not establish column labels.

This is not a WCAG compliance sample: a human may need to supply meaningful
labels from the wider document context. In this deliberately isolated case,
that context is absent, so automatic header invention would be misleading.

## Regression Test

Run `python -m unittest test_headerless_fixture` from the `pdf2html` directory.
This validates the fixture and expected markup, not a header-remediation fix.
Rishabh's branch should add integration assertions against its resolver:

- All three rows remain data rows, in order.
- No values become automatic column headers.
- Uncertain header semantics produce a review finding.
- Row/column assignments and all six values survive downstream reader processing.

To regenerate the committed PDF, install `reportlab` separately and run
`python examples/headerless-table/generate_fixture.py`. It is an authoring-only
dependency, not a new application runtime requirement.
