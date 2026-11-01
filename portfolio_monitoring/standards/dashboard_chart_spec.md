# Dashboard charts: house specification

The chart agent adds native Excel charts to the **Dashboard** sheet of an existing monitoring workbook.
It changes nothing else: no values, formulas, tables, formats or other sheets.

## Required charts (all data referenced from `Company Metrics`, never typed in)

| # | Title | Type | Data |
|---|---|---|---|
| 1 | ARR by company: Q2 → Q3 | clustered column | Prior ARR, ARR (two series) |
| 2 | ARR vs plan | horizontal bar | vs Plan; green ≥ 0, amber −5–0%, red < −5% (per point) |
| 3 | Runway (months) vs policy lines | column + two dashed lines | Runway; Runway_Red and Runway_Amber as constant lines (helper range allowed on a hidden sheet) |
| 4 | Efficiency: QoQ growth vs burn multiple | scatter, one series per company | x = QoQ growth, y = Burn multiple; companies without a report omitted |
| 5 | Status mix | doughnut | count of Red / Amber / Green / No report; slices in RAG colours (D64545, E0A030, 3D9970, 9CA3AF) |
| 6 | Cash vs quarterly net burn | clustered column | Cash, 3 × Net burn (helper range allowed) |

## Layout and clarity rules

- Grid of **2 charts per row**, every chart **below** the at-a-glance table, with a visible gap between charts.
  No chart may overlap another chart, a table, or the KPI tiles.
- Size about **15 cm × 7.5 cm** each; keep the whole chart inside its grid cell.
- **Legend:** at the bottom, **not overlaid** on the plot (`overlay = 0`). Single-series bar charts have no legend.
  Never more than 7 legend entries.
- **Data labels:** values only (no series name, category name or legend key), and only on charts 2 and 5.
- Every chart has a **title**; every value axis has an explicit number format: money `$#,##0,,"M"`, percentages `0%`,
  multiples `0.0"x"`, months `0`.
- Category labels must not overlap the bars: for horizontal bars with negative values, put axis labels at `low`.
- **Category labels must stay separate on column charts.** Excel and LibreOffice do not reliably wrap long names,
  so neighbouring labels run together ("Lumenpath AICorvane Security"). Do not rely on automatic wrapping: build
  the categories from a helper range of labels with a line break between words,
  `=SUBSTITUTE(<company-name cell on Company Metrics>," ",CHAR(10))`, on the hidden helper sheet, and point every column chart's
  categories at it. Set the category-axis font to 8 pt and do not rotate labels.
- **Scatter axis origin:** the two axes' tick labels must not meet at the corner ("0.0x0%"). Start the x-axis one
  major unit below its lowest value (e.g. −10% when the lowest growth is ≥ 0) and set the y-axis to cross at that
  minimum (`crosses = min`), so the y-axis "0.0x" label and the x-axis "0%" label sit apart.
- **Label headroom:** when a chart has outside-end data labels, set the value-axis minimum and maximum at least
  25% beyond the most extreme value, so no label touches a category label, the axis or the plot edge.
- **Outliers:** if one point is more than 3× the median of the others, do not let it squash the chart. Cap the
  axis at about 2× the next-largest value and name the off-scale point in the title (e.g. "Tessellate 17.4x, off
  scale").
- **Palette:** primary `1F5FAD`, comparison `B8C7DE`, accent `E07A5F`. For one series per company, use in order
  `1F5FAD, E07A5F, 3D9970, E0A030, 6C5B7B, 9DB7DC, 4B5563`. No 3-D, no shadows.
- Set the Dashboard print setup to fit **one page wide and one page tall**, landscape.

## Delivery

Save the workbook under its original name to `$OUTPUT_DIR` and list the directory in the same command. Before
saving, confirm the number of tables and conditional-format ranges is unchanged. openpyxl can lose extensions
that Excel or LibreOffice added (x14 data bars, icon sets); re-add any that are lost.
