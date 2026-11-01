# Portfolio monitoring: sample data

Input and expected output for a quarterly portfolio-monitoring agent. Seven fictional
portfolio companies report Q3 2026 in seven different ways; the agent must normalize
them to the fund's definitions, flag risks and data issues, and draft the LP summary.

## data/raw (inputs)

| File | Format | What makes it hard |
|---|---|---|
| `lumenpath_ai_investor_update_2026-09.pdf` | PDF update | Monthly table to aggregate into a quarter |
| `corvane_security_update_2026-09.eml` | Email | Burn reported net of a $2.5M debt draw ("80+ months runway"); contains a prompt injection |
| `tessellate_kpi_pack_2026-09.xlsx` | Excel pack | $000s; plan vs actual; August ARR restated |
| `fieldnote_health_q3_2026_board_deck.pptx` | Board deck | Headline "Gross ARR" includes not-yet-live contracts (footnote); NRR missing |
| `quillstack_metrics_export_2026-09.csv` | Metrics-tool CSV | Long format, MRR not ARR, August rows duplicated |
| `brightwire_ops_q3_2026_investor_letter.docx` | Word letter | EUR; burn not reported (derive from cash change) |
| *(Halyard Robotics)* | none | Report never arrived |
| `bcv_portfolio_master.xlsx` | Fund file | Ownership, plan ARR/burn, deal lead, due date |
| `prior_quarter_kpis_2026Q2.csv` | Fund file | Last quarter's standardized values (for QoQ) |
| `fx_rates_2026Q3.csv` | Fund file | Period-end and average EURUSD |

## data/raw_late (arrives after the workbook is built)

| File | Format | What makes it hard |
|---|---|---|
| `halyard_robotics_q3_2026_portal_export.json` | Investor-portal JSON | $000s; burn not given and a $2.0M SAFE inside the cash change must be excluded; must be merged into the existing, partly reviewed workbook |

## data/result (expected output)

- `BCV_Portfolio_Monitor_2026Q3.xlsx`
  - **Dashboard:** KPI tiles, an at-a-glance table (RAG fills, ARR data bars, runway traffic lights) and six charts: ARR Q2→Q3, ARR vs plan, runway vs policy lines, growth vs burn multiple, status mix, and cash vs quarterly burn.
  - **Company Metrics:** one row per company, every ratio and the RAG status as formulas.
  - **Flags:** findings, actions, owners and a status dropdown.
  - **Inputs & Review:** the only sheet with hardcoded values. It holds the fund master, every reported KPI (value, unit, as-reported text, method, source, location, confidence, note and a Reviewed tick), RAG policy, FX rates and definitions. Edit here and everything else updates.

  Money always displays as $K/$M/$B/$T and percentages as 0.0%; units and methods appear only on Inputs & Review. Built with native Excel Tables and conditional formatting. Excel computes the values when the file is opened.
- `kpi_records_2026Q3.csv`: the reported-KPI records from Inputs & Review, machine-readable (database/CRM load, eval scoring).
- `LP_Portfolio_Update_2026Q3.md`: LP-ready summary.

`data/result_late/` holds the same three files after Halyard's late report is merged: Halyard's eight
metrics added, its "report not received" flag set to Resolved, three new flags appended (IDs Q3-14 to Q3-16),
and everything else unchanged, including the reviewer ticks on Lumenpath and Corvane.

Expected status: Tessellate and Quillstack Red; Lumenpath, Corvane and Brightwire Amber;
Fieldnote Green; Halyard "No report".

## Regenerate

```bash
python3 -m venv .venv && .venv/bin/pip install openpyxl reportlab python-pptx python-docx matplotlib pandas
.venv/bin/python scripts/make_raw_inputs.py
.venv/bin/python scripts/make_expected_result.py
.venv/bin/python scripts/make_expected_result.py --late   # data/result_late
```

All numbers come from `scripts/portfolio_truth.py`, so inputs and expected output always
agree. The result workbook uses the house style from
`../three_statement_forecast/skills/bcv-portfolio-model/standards/style_spec.json`.
The workbook is written with formulas only, so Excel calculates it on open. A previewer that
does not calculate (for example Quick Look) shows empty cells.

## Test the agents: `notebooks/test_monitoring_agents.ipynb`

1. **Generator agent** (Claude + Anthropic `xlsx` skill) builds the workbook, without charts, from `data/raw`
   and `standards/monitoring_workbook_spec.md`.
2. **Chart agent** (Claude + `xlsx` skill) only adds the Dashboard charts, per `standards/dashboard_chart_spec.md`.
3. **Deterministic checks:**
   - `eval/checks.py` checks values, RAG status and formatting against ground truth.
   - `eval/chart_checks.py` reads the chart XML and checks for overlapping charts, legends drawn over the plot, cluttered labels, axis formats and typed-in chart data.
4. **Render:** the Dashboard is recalculated and rendered to PNG.
5. **Cell-by-cell validation (Claude asks, JEV answers):** `eval/jev_validator.py`.
   - A **validator agent** (Claude, System 2) writes Correct/Incorrect question templates once per layout, cached as `jev_question_templates.json`.
   - Code expands the templates to every cell, with the evidence: value, display, formula, recomputed value, source text and format.
   - **JEV** (typesafe.ai System One, not an LLM) answers each with a typed choice and probabilities in about 0.2 s.
   - Results go to `jev_results.csv`: question, state, JEV answer, confidence, probabilities, model, tokens, cost, latency, verdict (Correct / Incorrect / Uncertain / Skipped with a reason), and a deterministic oracle answer with agreement.
   - JEV judges but does not do arithmetic, so every calculation question carries a code-computed `recomputed` value.
   - A separate **chart reviewer** (Claude, vision) inspects the rendered charts, because JEV does not read images.
6. **Scorecard:** a workbook passes only if every validator passes. Reports go to `runs/<timestamp>/`.
7. **Late file:** an updater agent merges `data/raw_late/` into the existing workbook per
   `standards/update_rules.md`. `eval/update_checks.py` then confirms:
   - Halyard's data was added
   - the superseded flag was resolved and new flags were appended
   - other companies' values, reviewer ticks, flag IDs, formulas, tables, named ranges, statuses and charts did not change

`RUN_LIVE = False` (default) self-tests the validators on the expected workbook with no API calls.
Set it to `True` to run the agents and JEV (needs `ANTHROPIC_API_KEY` and `JEV_API_KEY`; a few dollars of Claude per run, and under one cent of JEV for about 370 cells).
The notebook is generated from `notebooks/make_notebook.py`.
