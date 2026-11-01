# Updating an existing monitoring workbook with a late file

A company's report can arrive after the quarter's workbook has been built and partly reviewed. Merge it
into the **existing** workbook. Never rebuild from scratch.

## Change only

- **Inputs & Review, reported KPIs:** replace the company's placeholder row (Metric ID `report`, Method
  `Missing`) with one row per metric (arr, revenue, gm, burn, cash, headcount, customers, nrr), with value,
  unit, as-reported text, method, source file, location, confidence and note, under the fund definitions in
  `monitoring_workbook_spec.md`. Extend the `tblKPIs` table and the `KPI_*` named ranges so they cover the
  new rows, and move any sections below the table down rather than overwriting them.
- **Inputs & Review, portfolio master:** set the company's *Report received* to `Yes` and its reporting
  format to the new file's format.
- **Flags:** set superseded flags (e.g. "report not received") to `Resolved`. Do not delete them or change
  their ID or text. Append new flags with the **next unused IDs**, and keep resolved flags at the bottom.

## Preserve exactly

- Every other company's values, methods, confidences and **Reviewed** ticks.
- All formulas on Company Metrics and Dashboard. They already look up the company by name, so its row
  updates by itself.
- Sheets, sheet order, tables, named ranges, number formats, conditional formats and fonts.
- **Charts.** Keep every Dashboard chart. openpyxl round-trips charts it can read, but it can lose
  extensions that Excel or LibreOffice added (x14 data bars and icon sets, sparklines, slicers) and simplify
  chart styling. After saving, compare chart and conditional-format counts with the original. If anything
  is lost, edit the worksheet XML inside the .xlsx directly instead, or say so in your summary (the chart
  agent will run again).

## Deliver

The updated workbook under its original name in `$OUTPUT_DIR` (list the directory in the same command),
plus a short change log in your final message: rows added, flags resolved, flags added.
