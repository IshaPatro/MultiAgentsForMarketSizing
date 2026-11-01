# House style (human-readable view of `style_spec.json`)

`style_spec.json` is the source of truth; the builder applies it and `lint_model.py` enforces it.
To change the firm's look, edit the JSON and publish a new skill version.

- **Font:** Arial 10; titles 16 on a navy band.
- **Cell colours:** blue = hardcoded input, black = formula, green = pure link to another sheet
  or a named input, yellow fill = low confidence. Only the Inputs sheet holds hardcoded numbers.
- **Numbers:** `$#,##0`, negatives in parentheses, zero as `-`; percentages `0.0%` stored as
  fractions; multiples `0.0x`; months `0.0 mo`; period headers `mmm yy`.
- **Structure:** Inputs first, Checks & Flags last. Monthly sheets: labels, unit, opening
  column, 36 forecast months; panes frozen at D8; one formula pattern per row.
- **No magic numbers** in formulas outside Inputs (allowed: 0, 1, 2, 12, 365).
- **Every model ships with:** integrity checks, weakest-link calculation confidence, and an
  agent review log with owner and status.
