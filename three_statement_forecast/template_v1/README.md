# Template v1: standalone model generator

The first, hand-run version of the three-statement template (no agent). The agent version lives in
`../skills/bcv-portfolio-model/scripts/`.

A VC-style, 36-month monthly model for a B2B SaaS portfolio company, built for portfolio
monitoring and LP reporting. An agent fills **only** the Inputs sheet (value, source,
confidence) and the agent review log. Everything else is generated formulas.

| Sheet | Role |
|---|---|
| Inputs | All hardcoded numbers as named ranges, each with Source + Confidence |
| Summary | Annual roll-up, last-round mark, model health, charts |
| Revenue · Headcount & Opex · P&L · Balance Sheet · Cash Flow · KPIs | Formulas only |
| Checks & Flags | Integrity checks, weakest-link calculation confidence, agent review log |

## Pipeline

```bash
python build_model.py Portfolio_Company_Model.xlsx   # generate from INPUTS + style_spec.yaml
python <xlsx-skill>/scripts/recalc.py Portfolio_Company_Model.xlsx   # compute values (LibreOffice)
python lint_model.py Portfolio_Company_Model.xlsx    # house standard: hardcodes, colours, formats, row consistency
python verify_model.py Portfolio_Company_Model.xlsx  # compute twice: independent Python model vs workbook
```

`style_spec.yaml` is the house formatting standard. Change the look there, never in the workbook.
