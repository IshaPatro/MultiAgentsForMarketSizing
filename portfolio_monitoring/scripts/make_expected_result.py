"""Write the expected (gold) monitoring output for the Q3 2026 sample pack.

  data/result/BCV_Portfolio_Monitor_2026Q3.xlsx
      Dashboard        KPI tiles, at-a-glance table, six charts
      Company Metrics  one row per company, every ratio and the RAG status as formulas
      Flags            findings, actions, owners, status
      Inputs & Review  the only sheet with hardcoded values: reported KPIs with unit, method,
                       source, confidence and a Reviewed column; fund master; policy; FX; definitions
  data/result/kpi_records_2026Q3.csv     normalized long table (for loading and eval scoring)
  data/result/LP_Portfolio_Update_2026Q3.md
  data/result_late/...                    same files after Halyard's late report is merged (--late)

Money is always shown as $K / $M / $B / $T (conditional number formats), percentages as 0.0%.

Usage: python scripts/make_expected_result.py
"""
import csv
import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, DoughnutChart, LineChart, Reference, ScatterChart, Series
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.marker import Marker
from openpyxl.chart.series import DataPoint
from openpyxl.formatting.rule import CellIsRule, DataBarRule, FormulaRule, IconSetRule, Rule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.styles.differential import DifferentialStyle
from openpyxl.styles.numbers import NumberFormat
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo

import sys

from portfolio_truth import (COMPANIES, FX, HALYARD_FILE, LATE_RECEIVED, PERIOD_END, POLICY, PRIOR, Q3, Q3_LATE,
                             REPORT_DUE)
from result_records import FLAGS, FLAGS_LATE, R, R_HALYARD, RESOLVED_BY_LATE, REVIEWED

LATE = False  # True = the version after Halyard's late report has been merged (python ... --late)

ROOT = Path(__file__).resolve().parent.parent
RESULT = ROOT / "data" / "result"
SPEC = json.loads((ROOT.parent / "three_statement_forecast" / "skills" / "bcv-portfolio-model" / "standards" /
                   "style_spec.json").read_text())
C, FONT = SPEC["colors"], SPEC["font"]["name"]
CO = {c["key"]: c for c in COMPANIES}
N = len(COMPANIES)

# Display formats (outside the Inputs sheet no unit text is shown; the format carries it)
PCT = '0.0%;(0.0%);"–"'
CONF = "0%"
MULT = '0.0"x"'
MONTHS = '0.0" mo"'
COUNT = "#,##0"
MONEY_BASE = '[>=1000000]$#,##0.0,,"M";[>=1000]$#,##0,"K";$#,##0'
MONEY_SCALES = [(1e12, '$#,##0.0,,,,"T";($#,##0.0,,,,"T")'), (1e9, '$#,##0.0,,,"B";($#,##0.0,,,"B")'),
                (1e6, '$#,##0.0,,"M";($#,##0.0,,"M")'), (1e3, '$#,##0,"K";($#,##0,"K")')]
INPUT_MONEY = "$#,##0"

METRICS = [  # id, label, unit, input format
    ("arr", "ARR (live, period end)", "USD", INPUT_MONEY),
    ("revenue", "Revenue (quarter)", "USD", INPUT_MONEY),
    ("gm", "Gross margin", "%", PCT),
    ("burn", "Net burn (monthly avg, ex-financing)", "USD / month", INPUT_MONEY),
    ("cash", "Cash (period end)", "USD", INPUT_MONEY),
    ("headcount", "Headcount", "FTE", COUNT),
    ("customers", "Customers", "count", COUNT),
    ("nrr", "Net revenue retention (TTM)", "%", PCT),
]
RAG = {"Red": "F4C7C3", "Amber": "FCE4B6", "Green": "CDEBD3", "No report": "E5E7EB"}
RAG_FONT = {"Red": "9B1C1C", "Amber": "8A5A00", "Green": "1E6B3A", "No report": "4B5563"}
SERIES = ["1F5FAD", "9DB7DC", "E07A5F", "3D9970", "F2CC8F", "6C5B7B", "81B29A"]


# --------------------------------------------------------------------------------------
# Styling helpers
# --------------------------------------------------------------------------------------
def fill(h):
    return PatternFill("solid", start_color=h, end_color=h)


def put(ws, ref, v, fmt=None, bold=False, italic=False, color="1F2937", bg=None, align=None, size=None,
        wrap=False):
    c = ws[ref]
    c.value = v
    c.font = Font(name=FONT, size=size or 10, bold=bold, italic=italic, color=color)
    if fmt:
        c.number_format = fmt
    if bg:
        c.fill = fill(bg)
    c.alignment = Alignment(horizontal=align, vertical="center", wrap_text=wrap)
    return c


def band(ws, title, sub, last_col):
    ws.sheet_view.showGridLines = False
    for r in (1, 2):
        for col in range(1, last_col + 1):
            ws.cell(r, col).fill = fill(C["navy"])
    put(ws, "A1", title, bold=True, color="FFFFFF", size=18)
    put(ws, "A2", sub, italic=True, color="C7D5EA")
    ws.row_dimensions[1].height = 32
    ws.row_dimensions[2].height = 18


def section(ws, row, text, last_col):
    for col in range(1, last_col + 1):
        ws.cell(row, col).border = Border(bottom=Side(style="medium", color=C["accent"]))
    put(ws, f"A{row}", text, bold=True, color=C["accent"], size=12)
    ws.row_dimensions[row].height = 22


def widths(ws, spec):
    for col, w in spec.items():
        ws.column_dimensions[col].width = w


def table(ws, name, ref, style="TableStyleMedium2"):
    t = Table(displayName=name, ref=ref)
    t.tableStyleInfo = TableStyleInfo(name=style, showRowStripes=True, showColumnStripes=False)
    ws.add_table(t)


_fmt_ids = iter(range(300, 400))


def money(ws, rng):
    """$K/$M/$B/$T everywhere: a base format plus conditional number formats (Excel CF)."""
    first = rng.split(":")[0].replace("$", "")
    for c in ws[rng] if ":" in rng else [[ws[rng]]]:
        for cell in c:
            cell.number_format = MONEY_BASE
    for limit, code in MONEY_SCALES:
        dxf = DifferentialStyle(numFmt=NumberFormat(numFmtId=next(_fmt_ids), formatCode=code))
        ws.conditional_formatting.add(rng, Rule(type="expression", dxf=dxf, stopIfTrue=True,
                                                formula=[f"AND(ISNUMBER({first}),ABS({first})>={limit:.0E})"]))


def rag_cf(ws, rng):
    for label, hexc in RAG.items():
        ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=[f'"{label}"'], fill=fill(hexc),
                                                      font=Font(bold=True, color=RAG_FONT[label])))


# --------------------------------------------------------------------------------------
# Inputs & Review
# --------------------------------------------------------------------------------------
def build_inputs(wb, records):
    ws = wb.create_sheet("Inputs & Review")
    ws.sheet_properties.tabColor = "0000FF"
    band(ws, "Inputs & Review", "The only sheet with hardcoded values. Check each blue value against its source "
                                "and tick Reviewed; every other sheet updates from here.", 12)
    widths(ws, dict(A=22, B=14, C=34, D=16, E=12, F=30, G=13, H=42, I=40, J=12, K=62, L=11))
    put(ws, "A4", "Blue = value to review", color=C["input_font"], bg=C["input_fill"])
    put(ws, "C4", "Yellow = confidence below policy", bg=C["low_conf_fill"])
    put(ws, "F4", "Red = metric missing", bg=C["fail_fill"])

    # 1. Portfolio master
    section(ws, 6, "1  Portfolio master (fund records)", 12)
    mh = ["Company", "Stage", "Sector", "Plan ARR Q3 (USD)", "Ownership (FD)", "Plan net burn (USD / month)",
          "Prior ARR Q2 (USD)", "Prior cash Q2 (USD)", "Deal lead", "Report received", "Reporting format"]
    for i, h in enumerate(mh):
        put(ws, f"{chr(65 + i)}{7}", h, bold=True, color="FFFFFF")
    m0 = 8
    for i, c in enumerate(COMPANIES):
        r = m0 + i
        p = PRIOR[c["key"]]
        vals = [c["name"], c["stage"], c["sector"], c["plan_arr"], c["own"], c["plan_burn"], p["arr"], p["cash"],
                c["lead"], "No" if c["file"] is None else "Yes", c["fmt"]]
        fmts = [None, None, None, INPUT_MONEY, PCT, INPUT_MONEY, INPUT_MONEY, INPUT_MONEY, None, None, None]
        for j, (v, f) in enumerate(zip(vals, fmts)):
            put(ws, f"{chr(65 + j)}{r}", v, f, color=C["input_font"])
    m1 = m0 + N - 1
    table(ws, "tblMaster", f"A7:K{m1}", "TableStyleLight9")
    for name, col in dict(M_Company="A", M_Stage="B", M_Sector="C", M_PlanARR="D", M_Own="E", M_PlanBurn="F",
                          M_PriorARR="G", M_PriorCash="H", M_Lead="I").items():
        wb.defined_names[name] = DefinedName(name, attr_text=f"'Inputs & Review'!${col}${m0}:${col}${m1}")

    # 2. Reported KPIs
    k_hdr = m1 + 3
    section(ws, k_hdr - 1, "2  Reported KPIs: one row per company × metric (fund definitions, USD)", 12)
    kh = ["Company", "Metric ID", "Metric", "Value", "Unit", "As reported", "Method", "Source file",
          "Location in source", "Confidence", "Note", "Reviewed"]
    for i, h in enumerate(kh):
        put(ws, f"{chr(65 + i)}{k_hdr}", h, bold=True, color="FFFFFF")
    meta = {m: (lbl, unit, fmt) for m, lbl, unit, fmt in METRICS}
    r = k_hdr + 1
    for (k, m), (val, rep, method, loc, conf, note) in records.items():
        lbl, unit, fmt = meta.get(m, ("Q3 report", "", None))
        row = [CO[k]["name"], m, lbl, val, unit, rep, method, CO[k]["file"] or "—", loc, conf, note,
               "✓" if k in REVIEWED else ""]
        for j, v in enumerate(row):
            col = chr(65 + j)
            put(ws, f"{col}{r}", v, fmt if col == "D" else (CONF if col == "J" else None),
                color=C["input_font"] if col in "DJ" else ("6B7280" if col in "EHIK" else "1F2937"),
                wrap=col == "K", align="center" if col in "BEGJL" else None)
        r += 1
    k0, k1 = k_hdr + 1, r - 1
    table(ws, "tblKPIs", f"A{k_hdr}:L{k1}", "TableStyleLight9")
    for name, col in dict(KPI_Company="A", KPI_Metric="B", KPI_Value="D", KPI_Conf="J").items():
        wb.defined_names[name] = DefinedName(name, attr_text=f"'Inputs & Review'!${col}${k0}:${col}${k1}")
    ws.conditional_formatting.add(f"J{k0}:J{k1}", FormulaRule(formula=[f"J{k0}<Conf_Amber"],
                                                              fill=fill(C["low_conf_fill"])))
    ws.conditional_formatting.add(f"G{k0}:G{k1}", CellIsRule(operator="equal", formula=['"Missing"'],
                                                             fill=fill(C["fail_fill"])))
    ws.conditional_formatting.add(f"L{k0}:L{k1}", CellIsRule(operator="equal", formula=['"✓"'],
                                                             fill=fill(C["pass_fill"])))
    dv = DataValidation(type="list", formula1='"✓,✗,?"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"L{k0}:L{k1}")

    # 3. Policy thresholds
    p_hdr = k1 + 3
    section(ws, p_hdr - 1, "3  RAG policy (named ranges; change here to re-rate the portfolio)", 12)
    for i, (name, (val, desc)) in enumerate(POLICY.items()):
        rr = p_hdr + i
        put(ws, f"A{rr}", name, bold=True)
        fmt = MULT if name.startswith("BM") else (MONTHS if name.startswith("Runway") else PCT)
        put(ws, f"D{rr}", val, fmt, color=C["input_font"], bg=C["input_fill"], align="right")
        put(ws, f"F{rr}", desc, color="6B7280")
        wb.defined_names[name] = DefinedName(name, attr_text=f"'Inputs & Review'!$D${rr}")

    # 4. FX
    f_hdr = p_hdr + len(POLICY) + 2
    section(ws, f_hdr - 1, "4  FX rates", 12)
    for i, (k, v) in enumerate(FX.items()):
        put(ws, f"A{f_hdr + i}", k, bold=True)
        put(ws, f"D{f_hdr + i}", v, "0.000", color=C["input_font"], bg=C["input_fill"], align="right")
        put(ws, f"F{f_hdr + i}", "Balances (ARR, cash) at period-end spot" if "avg" not in k
            else "Flows (revenue, burn) at quarter average", color="6B7280")

    # 5. Definitions
    d_hdr = f_hdr + len(FX) + 2
    section(ws, d_hdr - 1, "5  Metric definitions", 12)
    defs = [
        ("ARR", "Annualized recurring revenue from live subscriptions at period end; excludes signed-not-live "
                "(CARR), services and uncontracted usage. MRR × 12 when only MRR is reported."),
        ("Net burn", "Average monthly operating + investing cash outflow, excluding equity and debt proceeds."),
        ("Runway", "Cash ÷ net burn. 'CF+' when cash-flow positive."),
        ("Burn multiple", "Quarterly net burn ÷ net new ARR. 'n/m' when net new ARR ≤ 0."),
        ("NRR", "Trailing-twelve-month net revenue retention as reported by the company."),
        ("Method", "Reported · Converted (units / FX) · Derived (computed from reported items) · Adjusted (company "
                   "definition differs from the fund's) · Missing."),
        ("Confidence", "0.95 reported and tied out · 0.90 reported · 0.85 adjusted · 0.70–0.80 derived with an "
                       "assumption · 0 missing."),
    ]
    for i, (k, v) in enumerate(defs):
        rr = d_hdr + i
        put(ws, f"A{rr}", k, bold=True)
        ws.merge_cells(f"C{rr}:K{rr}")
        put(ws, f"C{rr}", v, wrap=True)
        ws.row_dimensions[rr].height = 28
    ws.freeze_panes = "B5"
    return ws


# --------------------------------------------------------------------------------------
# Company Metrics (formula layer)
# --------------------------------------------------------------------------------------
CM_COLS = ["Company", "Sector", "Stage", "Status", "ARR", "Prior ARR", "QoQ growth", "Plan ARR", "vs Plan",
           "Net new ARR", "Revenue", "Gross margin", "Net burn", "Cash", "Runway", "Burn multiple", "NRR",
           "Headcount", "Customers", "Ownership", "Look-through ARR", "Data confidence", "Deal lead"]
CMC = {name: chr(65 + i) for i, name in enumerate(CM_COLS)}
CM0 = 6  # first data row


def build_metrics(wb):
    ws = wb.create_sheet("Company Metrics")
    ws.sheet_properties.tabColor = C["accent"]
    band(ws, "Company Metrics · Q3 2026", "Every value below is a formula on Inputs & Review. Status uses the RAG "
                                          "policy defined there.", len(CM_COLS))
    widths(ws, {CMC[c]: w for c, w in zip(CM_COLS, [20, 18, 10, 11, 11, 11, 11, 11, 10, 12, 11, 11, 11, 11, 10,
                                                     12, 9, 11, 11, 11, 13, 13, 12])})
    hdr = CM0 - 1
    for name, col in CMC.items():
        put(ws, f"{col}{hdr}", name, bold=True, color="FFFFFF", wrap=True)
    ws.row_dimensions[hdr].height = 30

    def kpi(m, r):
        return (f'IF(COUNTIFS(KPI_Company,$A{r},KPI_Metric,"{m}",KPI_Value,"<>")=0,"",'
                f'SUMIFS(KPI_Value,KPI_Company,$A{r},KPI_Metric,"{m}"))')

    def master(name, r):
        return f"INDEX({name},MATCH($A{r},M_Company,0))"

    for i in range(N):
        r = CM0 + i
        x = {k: f"{v}{r}" for k, v in CMC.items()}
        f = {
            "Company": f"=INDEX(M_Company,{i + 1})",
            "Sector": f"={master('M_Sector', r)}",
            "Stage": f"={master('M_Stage', r)}",
            "Status": (f'=IF(NOT(ISNUMBER({x["ARR"]})),"No report",IF(OR(AND(ISNUMBER({x["Runway"]}),'
                       f'{x["Runway"]}<Runway_Red),{x["vs Plan"]}<Plan_Red,{x["Burn multiple"]}="n/m",'
                       f'AND(ISNUMBER({x["Burn multiple"]}),{x["Burn multiple"]}>BM_Red)),"Red",IF(OR(AND('
                       f'ISNUMBER({x["Runway"]}),{x["Runway"]}<Runway_Amber),{x["vs Plan"]}<Plan_Amber,AND('
                       f'ISNUMBER({x["Burn multiple"]}),{x["Burn multiple"]}>BM_Amber),AND(ISNUMBER({x["NRR"]}),'
                       f'{x["NRR"]}<NRR_Amber),{x["Data confidence"]}<Conf_Amber),"Amber","Green")))'),
            "ARR": f"={kpi('arr', r)}",
            "Prior ARR": f"={master('M_PriorARR', r)}",
            "QoQ growth": f'=IF(ISNUMBER({x["ARR"]}),{x["ARR"]}/{x["Prior ARR"]}-1,"")',
            "Plan ARR": f"={master('M_PlanARR', r)}",
            "vs Plan": f'=IF(ISNUMBER({x["ARR"]}),{x["ARR"]}/{x["Plan ARR"]}-1,"")',
            "Net new ARR": f'=IF(ISNUMBER({x["ARR"]}),{x["ARR"]}-{x["Prior ARR"]},"")',
            "Revenue": f"={kpi('revenue', r)}",
            "Gross margin": f"={kpi('gm', r)}",
            "Net burn": f"={kpi('burn', r)}",
            "Cash": f"={kpi('cash', r)}",
            "Runway": f'=IF(ISNUMBER({x["Net burn"]}),IF({x["Net burn"]}>0,{x["Cash"]}/{x["Net burn"]},"CF+"),"")',
            "Burn multiple": (f'=IF(ISNUMBER({x["Net new ARR"]}),IF({x["Net new ARR"]}>0,'
                              f'3*{x["Net burn"]}/{x["Net new ARR"]},"n/m"),"")'),
            "NRR": f"={kpi('nrr', r)}",
            "Headcount": f"={kpi('headcount', r)}",
            "Customers": f"={kpi('customers', r)}",
            "Ownership": f"={master('M_Own', r)}",
            "Look-through ARR": f'=IF(ISNUMBER({x["ARR"]}),{x["ARR"]}*{x["Ownership"]},"")',
            "Data confidence": (f'=IF(ISNUMBER({x["ARR"]}),MIN(' + ",".join(
                f'_xlfn.MINIFS(KPI_Conf,KPI_Company,$A{r},KPI_Metric,"{m}")' for m in ("arr", "burn", "cash"))
                                + '),"")'),
            "Deal lead": f"={master('M_Lead', r)}",
        }
        for name, formula in f.items():
            put(ws, x[name], formula, bold=name == "Company",
                align="center" if name in ("Status", "Stage") else ("right" if name in ("Runway", "Burn multiple")
                                                                    else None))
    r1 = CM0 + N - 1
    table(ws, "tblMetrics", f"A{hdr}:{CMC['Deal lead']}{r1}")
    for name in ("ARR", "Prior ARR", "Plan ARR", "Net new ARR", "Revenue", "Net burn", "Cash", "Look-through ARR"):
        money(ws, f"{CMC[name]}{CM0}:{CMC[name]}{r1}")
    for name, fmt in (("QoQ growth", PCT), ("vs Plan", PCT), ("Gross margin", PCT), ("NRR", PCT),
                      ("Ownership", PCT), ("Data confidence", CONF), ("Runway", MONTHS),
                      ("Burn multiple", MULT), ("Headcount", COUNT), ("Customers", COUNT)):
        for cell in ws[f"{CMC[name]}{CM0}:{CMC[name]}{r1}"]:
            cell[0].number_format = fmt
    rag_cf(ws, f"{CMC['Status']}{CM0}:{CMC['Status']}{r1}")
    ws.freeze_panes = f"B{CM0}"
    return ws


# --------------------------------------------------------------------------------------
# Flags
# --------------------------------------------------------------------------------------
def flag_rows():
    """IDs are fixed by the original run; a late update resolves superseded flags and appends new IDs."""
    order = {"Red": 0, "Amber": 1, "Info": 2}
    rows = []
    for i, f in enumerate(sorted(FLAGS, key=lambda f: order[f[1]]), 1):
        resolved = LATE and any(f[0] == c and f[2] == cat and f[3].startswith(pre) for c, cat, pre in RESOLVED_BY_LATE)
        rows.append((f"Q3-{i:02d}", f, "Resolved" if resolved else "Open"))
    if LATE:
        rows += [(f"Q3-{j:02d}", f, "Open") for j, f in enumerate(FLAGS_LATE, len(rows) + 1)]
    return sorted(rows, key=lambda r: (r[2] == "Resolved", order[r[1][1]], r[0]))


def build_flags(wb):
    ws = wb.create_sheet("Flags")
    ws.sheet_properties.tabColor = "C2410C"
    band(ws, "Flags · Q3 2026", "Sorted by severity. Evidence for each value is on Inputs & Review.", 8)
    widths(ws, dict(A=8, B=20, C=11, D=14, E=70, F=55, G=13, H=12))
    for i, h in enumerate(["ID", "Company", "Severity", "Category", "Finding", "Action", "Owner", "Status"]):
        put(ws, f"{chr(65 + i)}{4}", h, bold=True, color="FFFFFF")
    rows = flag_rows()
    for i, (fid, (k, sev, cat, finding, _ev, action), status) in enumerate(rows, 1):
        r = 4 + i
        for col, v in zip("ABCDEFGH", [fid, CO[k]["name"], sev, cat, finding, action, CO[k]["lead"], status]):
            put(ws, f"{col}{r}", v, wrap=col in "EF", bold=col == "B", align="center" if col in "ACH" else None)
        ws.row_dimensions[r].height = 34
    last = 4 + len(rows)
    table(ws, "tblFlags", f"A4:H{last}", "TableStyleLight1")
    sev = f"C5:C{last}"
    for label, hexc, fc in (("Red", RAG["Red"], RAG_FONT["Red"]), ("Amber", RAG["Amber"], RAG_FONT["Amber"]),
                            ("Info", "DBEAFE", "1E40AF")):
        ws.conditional_formatting.add(sev, CellIsRule(operator="equal", formula=[f'"{label}"'], fill=fill(hexc),
                                                      font=Font(bold=True, color=fc)))
    ws.conditional_formatting.add(f"H5:H{last}", CellIsRule(operator="equal", formula=['"Resolved"'],
                                                            fill=fill(RAG["Green"])))
    dv = DataValidation(type="list", formula1='"Open,In progress,Resolved"')
    ws.add_data_validation(dv)
    dv.add(f"H5:H{last}")
    ws.freeze_panes = "A5"
    return ws


# --------------------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------------------
def cm(name, r=None):
    col = CMC[name]
    return f"'Company Metrics'!${col}${CM0}:${col}${CM0 + N - 1}" if r is None else f"'Company Metrics'!{col}{r}"


def style_chart(ch, title, y_fmt=None, legend=True):
    ch.title = title
    ch.style = 10
    ch.height, ch.width = 7.5, 14.6  # leaves a gap inside each 2-column grid cell
    if hasattr(ch, "y_axis"):
        ch.y_axis.delete = False
        ch.x_axis.delete = False
        ch.y_axis.majorGridlines.spPr = None if ch.y_axis.majorGridlines is None else ch.y_axis.majorGridlines.spPr
        if y_fmt:
            ch.y_axis.number_format = y_fmt
            ch.y_axis.numFmt.sourceLinked = False
    if not legend:
        ch.legend = None
    else:
        ch.legend.position = "b"


def values_only(num_fmt=None):
    lbl = DataLabelList(showVal=True, showSerName=False, showCatName=False, showLegendKey=False,
                        showPercent=False, showLeaderLines=False)
    if num_fmt:
        lbl.numFmt = num_fmt
    return lbl


def build_chart_data(wb):
    ws = wb.create_sheet("_chart")
    ws.sheet_state = "hidden"
    for i, h in enumerate(["Company", "Red line", "Amber line", "Quarterly burn", "", "Status", "Count"]):
        put(ws, f"{chr(65 + i)}{1}", h, bold=True, color="FFFFFF")
    for i in range(N):
        r, src = 2 + i, CM0 + i
        ws[f"A{r}"] = f"={cm('Company', src)}"
        ws[f"B{r}"] = "=Runway_Red"
        ws[f"C{r}"] = "=Runway_Amber"
        ws[f"D{r}"] = f'=IF(ISNUMBER({cm("Net burn", src)}),3*{cm("Net burn", src)},"")'
    for i, s in enumerate(RAG):
        ws[f"F{2 + i}"] = s
        ws[f"G{2 + i}"] = f'=COUNTIF({cm("Status")},F{2 + i})'
    return ws


def build_dashboard(wb):
    ws = wb.active
    ws.title = "Dashboard"
    ws.sheet_properties.tabColor = C["navy"]
    band(ws, "Portfolio Monitor · Q3 2026", f"Quarter ending {PERIOD_END:%d %B %Y} · fund definitions · "
                                            "sample data (fictional companies)", 11)
    widths(ws, dict(A=22, B=19, C=13, D=13, E=12, F=12, G=13, H=12, I=12, J=11, K=58))

    tiles = [
        ("Portfolio ARR", f"=SUM({cm('ARR')})", "money", "live, reporting cos."),
        ("ARR growth QoQ", f"=SUM({cm('ARR')})/SUMPRODUCT(ISNUMBER({cm('ARR')})*{cm('Prior ARR')})-1", PCT,
         "same cos. vs Q2"),
        ("ARR vs plan", f"=SUM({cm('ARR')})/SUMPRODUCT(ISNUMBER({cm('ARR')})*{cm('Plan ARR')})-1", PCT,
         "vs board plan"),
        ("Look-through ARR", f"=SUM({cm('Look-through ARR')})", "money", "BCV ownership share"),
        ("Portfolio cash", f"=SUM({cm('Cash')})", "money", "period end"),
        ("Reporting", f'=COUNT({cm("ARR")})&" / "&COUNTA({cm("Company")})', None, "companies"),
        ("Red", f'=COUNTIF({cm("Status")},"Red")', COUNT, "need action"),
        ("Amber", f'=COUNTIF({cm("Status")},"Amber")', COUNT, "watch"),
    ]
    for i, (label, f, fmt, sub) in enumerate(tiles):
        col = "ABCDEFGH"[i]
        for r in (4, 5, 6):
            ws[f"{col}{r}"].fill = fill("F3F6FB")
        ws[f"{col}4"].border = Border(top=Side(style="thick", color=C["accent"] if label not in ("Red", "Amber")
                                               else ("D64545" if label == "Red" else "E0A030")))
        put(ws, f"{col}4", label, color="6B7280", bg="F3F6FB", align="center", size=9)
        put(ws, f"{col}5", f, None if fmt == "money" else fmt, bold=True, size=18, bg="F3F6FB", align="center",
            color=C["navy"])
        put(ws, f"{col}6", sub, italic=True, color="9CA3AF", bg="F3F6FB", align="center", size=8)
        if fmt == "money":
            money(ws, f"{col}5")
    ws.row_dimensions[5].height = 34

    # At-a-glance table
    section(ws, 8, "Portfolio at a glance", 11)
    gcols = ["Company", "Sector", "Status", "ARR", "QoQ growth", "vs Plan", "Net burn", "Runway", "Burn multiple",
             "NRR", "Top flag"]
    for i, h in enumerate(gcols):
        put(ws, f"{chr(65 + i)}{9}", h, bold=True, color="FFFFFF")
    for i in range(N):
        r, src = 10 + i, CM0 + i
        for j, h in enumerate(gcols[:-1]):
            col = chr(65 + j)
            put(ws, f"{col}{r}", f'={cm(h, src)}', bold=h == "Company",
                align="center" if h in ("Status",) else ("right" if h in ("Runway", "Burn multiple") else None))
        put(ws, f"K{r}", f'=IFERROR(INDEX(Flags!$E$5:$E$100,MATCH($A{r},Flags!$B$5:$B$100,0)),"")', italic=True,
            color="4B5563", wrap=True)
        ws.row_dimensions[r].height = 30
    g1 = 10 + N - 1
    table(ws, "tblGlance", f"A9:K{g1}", "TableStyleLight9")
    money(ws, f"D10:D{g1}")
    money(ws, f"G10:G{g1}")
    for col, fmt in (("E", PCT), ("F", PCT), ("J", PCT), ("H", MONTHS), ("I", MULT)):
        for cell in ws[f"{col}10:{col}{g1}"]:
            cell[0].number_format = fmt
    rag_cf(ws, f"C10:C{g1}")
    ws.conditional_formatting.add(f"D10:D{g1}", DataBarRule(start_type="num", start_value=0, end_type="max",
                                                            color="9DB7DC", showValue=True))
    ws.conditional_formatting.add(f"H10:H{g1}", IconSetRule("3TrafficLights1", "num",
                                                            [0, POLICY["Runway_Red"][0], POLICY["Runway_Amber"][0]]))
    ws.conditional_formatting.add(f"F10:F{g1}", FormulaRule(formula=["AND(ISNUMBER(F10),F10<Plan_Amber)"],
                                                            font=Font(color="B42318", bold=True)))
    ws.conditional_formatting.add(f"I10:I{g1}", FormulaRule(formula=["AND(ISNUMBER(I10),I10>BM_Amber)"],
                                                            font=Font(color="B42318", bold=True)))

    # Charts
    section(ws, g1 + 2, "Charts", 11)
    sheet = wb["Company Metrics"]
    cd = wb["_chart"]
    rows = (CM0 - 1, CM0 + N - 1)
    cats = Reference(sheet, min_col=1, min_row=CM0, max_row=rows[1])

    def col_idx(name):
        return CM_COLS.index(name) + 1

    # 1 ARR Q2 vs Q3
    c1 = BarChart()
    c1.type, c1.grouping = "col", "clustered"
    for name, color in (("Prior ARR", "B8C7DE"), ("ARR", C["accent"])):
        c1.add_data(Reference(sheet, min_col=col_idx(name), min_row=rows[0], max_row=rows[1]), titles_from_data=True)
        c1.series[-1].graphicalProperties.solidFill = color
        c1.series[-1].graphicalProperties.line.noFill = True
    c1.set_categories(cats)
    style_chart(c1, "ARR by company: Q2 → Q3", '$#,##0,,"M"')

    # 2 ARR vs plan
    c2 = BarChart()
    c2.type = "bar"
    c2.add_data(Reference(sheet, min_col=col_idx("vs Plan"), min_row=rows[0], max_row=rows[1]),
                titles_from_data=True)
    c2.set_categories(cats)
    for i, c in enumerate(COMPANIES):
        q = Q3.get(c["key"])
        pt = DataPoint(idx=i)
        pt.graphicalProperties.solidFill = ("D64545" if q and q["arr"] / c["plan_arr"] - 1 < POLICY["Plan_Amber"][0]
                                            else "E0A030" if q and q["arr"] < c["plan_arr"] else "3D9970")
        pt.graphicalProperties.line.noFill = True
        c2.series[0].dPt.append(pt)
    c2.series[0].dLbls = values_only("0%")
    style_chart(c2, "ARR vs plan", "0%", legend=False)
    c2.x_axis.tickLblPos = "low"
    c2.y_axis.scaling.min, c2.y_axis.scaling.max = -0.25, 0.15  # room for outside-end labels

    # 3 Runway with policy lines
    c3 = BarChart()
    c3.type = "col"
    c3.add_data(Reference(sheet, min_col=col_idx("Runway"), min_row=rows[0], max_row=rows[1]),
                titles_from_data=True)
    c3.series[0].graphicalProperties.solidFill = C["accent"]
    c3.set_categories(cats)
    lines = LineChart()
    for col, color in ((2, "D64545"), (3, "E0A030")):
        lines.add_data(Reference(cd, min_col=col, min_row=1, max_row=1 + N), titles_from_data=True)
        s = lines.series[-1]
        s.graphicalProperties.line.solidFill = color
        s.graphicalProperties.line.dashStyle = "dash"
        s.graphicalProperties.line.width = 19050
        s.marker = Marker(symbol="none")
        s.smooth = False
    lines.y_axis.axId = 100
    c3 += lines
    style_chart(c3, "Runway (months) vs policy lines", "0")

    # 4 Growth vs burn multiple
    c4 = ScatterChart()
    c4.style = 10
    for i, c in enumerate(COMPANIES):
        if c["key"] not in Q3:
            continue
        r = CM0 + i
        s = Series(Reference(sheet, min_col=col_idx("Burn multiple"), min_row=r),
                   Reference(sheet, min_col=col_idx("QoQ growth"), min_row=r), title=c["name"])
        s.marker = Marker(symbol="circle", size=11)
        s.marker.graphicalProperties.solidFill = SERIES[i % len(SERIES)]
        s.marker.graphicalProperties.line.noFill = True
        s.graphicalProperties.line.noFill = True
        c4.series.append(s)
    style_chart(c4, "Growth (x) vs burn multiple (y)", '0.0"x"')
    c4.x_axis.number_format = "0%"
    c4.x_axis.numFmt.sourceLinked = False

    # 5 Status mix
    c5 = DoughnutChart()
    c5.add_data(Reference(cd, min_col=7, min_row=1, max_row=1 + len(RAG)), titles_from_data=True)
    c5.set_categories(Reference(cd, min_col=6, min_row=2, max_row=1 + len(RAG)))
    for i, hexc in enumerate(["D64545", "E0A030", "3D9970", "9CA3AF"]):
        pt = DataPoint(idx=i)
        pt.graphicalProperties.solidFill = hexc
        c5.series[0].dPt.append(pt)
    c5.series[0].dLbls = values_only()
    c5.holeSize = 55
    style_chart(c5, "Status mix")

    # 6 Cash vs quarterly burn
    c6 = BarChart()
    c6.type, c6.grouping = "col", "clustered"
    c6.add_data(Reference(sheet, min_col=col_idx("Cash"), min_row=rows[0], max_row=rows[1]), titles_from_data=True)
    c6.series[0].graphicalProperties.solidFill = C["accent"]
    c6.add_data(Reference(cd, min_col=4, min_row=1, max_row=1 + N), titles_from_data=True)
    c6.series[1].graphicalProperties.solidFill = "E07A5F"
    c6.set_categories(cats)
    style_chart(c6, "Cash vs quarterly net burn", '$#,##0,,"M"')

    top = g1 + 3
    for i, ch in enumerate([c1, c2, c3, c4, c5, c6]):
        ws.add_chart(ch, f"{'A' if i % 2 == 0 else 'F'}{top + (i // 2) * 16}")
    ws.freeze_panes = "A4"
    return ws


# --------------------------------------------------------------------------------------
def records_table():
    records = {}
    for c in COMPANIES:
        k = c["key"]
        if k not in R and not (LATE and k == "halyard"):
            records[(k, "report")] = (None, "Not received", "Missing", "—", 0.0,
                                      f"No Q3 report by the {REPORT_DUE:%Y-%m-%d} due date")
            continue
        for m, *_ in METRICS:
            rep, method, loc, conf, note = (R_HALYARD if k == "halyard" else R[k])[m]
            records[(k, m)] = (Q3[k][m], rep, method, loc, conf, note)
    return records


def main():
    global LATE, Q3, RESULT
    if "--late" in sys.argv:
        LATE, Q3, RESULT = True, Q3_LATE, RESULT.parent / "result_late"
        CO["halyard"].update(file=HALYARD_FILE, fmt="Investor portal export (JSON)")
    RESULT.mkdir(parents=True, exist_ok=True)
    records = records_table()
    wb = Workbook()
    build_inputs(wb, records)  # names first, so later sheets' formulas resolve
    build_metrics(wb)
    build_flags(wb)
    build_chart_data(wb)
    build_dashboard(wb)
    wb.move_sheet("Dashboard", offset=-wb.index(wb["Dashboard"]))
    wb.move_sheet("Inputs & Review", offset=len(wb.sheetnames) - 1 - wb.index(wb["Inputs & Review"]))
    wb.active = 0
    for ws in wb.worksheets:
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
    wb["Dashboard"].page_setup.fitToHeight = 1  # whole dashboard on one page
    wb.calculation.fullCalcOnLoad = True
    wb.save(RESULT / "BCV_Portfolio_Monitor_2026Q3.xlsx")

    with open(RESULT / "kpi_records_2026Q3.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["company", "period_end", "metric", "value", "as_reported", "method", "source_file", "location",
                    "confidence", "note"])
        for (k, m), (val, rep, method, loc, conf, note) in records.items():
            w.writerow([CO[k]["name"], PERIOD_END.isoformat(), m, "" if val is None else round(val, 4), rep, method,
                        CO[k]["file"] or "", loc, conf, note])

    rep = [c for c in COMPANIES if c["key"] in Q3]
    arr_q3 = sum(Q3[c["key"]]["arr"] for c in rep)
    arr_q2 = sum(PRIOR[c["key"]]["arr"] for c in rep)
    plan = sum(c["plan_arr"] for c in rep)
    runway = {c["key"]: Q3[c["key"]]["cash"] / Q3[c["key"]]["burn"] for c in rep}
    lines = [
        "# Portfolio update: Q3 2026", "",
        "*Sample output generated from fictional data. Figures use the fund's standard definitions (live ARR, "
        "net burn excluding financing, USD).*", "",
        "## At a glance", "",
        f"- **{len(rep)} of {len(COMPANIES)}** companies reported for the quarter ending 30 September 2026.",
        f"- Aggregate live ARR **${arr_q3 / 1e6:.1f}M**, up **{arr_q3 / arr_q2 - 1:.1%}** quarter on quarter "
        f"for the same companies and **{arr_q3 / plan - 1:+.1%}** vs plan.",
        ("- Status: 3 Red, 3 Amber, 1 Green." if LATE else
         "- Status: 2 Red, 3 Amber, 1 Green, 1 not yet reported."), "",
        "## Highlights", "",
        f"- **Lumenpath AI** crossed $5.0M ARR (+{Q3['lumenpath']['arr'] / PRIOR['lumenpath']['arr'] - 1:.0%} QoQ), "
        "with gross margin improving as inference moves to a cheaper serving stack.",
        "- **Fieldnote Health** reached $22.8M live ARR with 38 health systems live; implementation backlog fell "
        "from 14 to 9 weeks.",
        f"- **Quillstack** is ahead of plan (ARR ${Q3['quillstack']['arr'] / 1e6:.2f}M vs $2.30M) with NRR of 104%.",
        "", "## Watch list", "",
        f"- **Tessellate Data**: ARR 13% below plan and runway of {runway['tessellate']:.1f} months. A revised "
        "operating plan is due at the November board meeting.",
        f"- **Quillstack**: runway of {runway['quillstack']:.1f} months; Series A preparation starting.",
        (f"- **Halyard Robotics**: report arrived late ({LATE_RECEIVED:%d %b}); runway "
         f"{Q3['halyard']['cash'] / Q3['halyard']['burn']:.1f} months even after a $2.0M SAFE." if LATE else
         "- **Halyard Robotics**: Q3 report outstanding; follow-up in progress."), "",
        "## Notes on methodology", "",
        "- Corvane's reported burn was net of a $2.5M venture-debt draw; the fund figure excludes financing "
        f"(${Q3['corvane']['burn'] / 1e6:.2f}M/month, runway {runway['corvane']:.1f} months).",
        "- Fieldnote's headline 'Gross ARR' includes contracted, not-yet-live revenue; live ARR is shown above.",
        "- Brightwire reports in EUR; converted at period-end spot (balances) and Q3 average (flows).",
    ]
    (RESULT / "LP_Portfolio_Update_2026Q3.md").write_text("\n".join(lines) + "\n")
    print("\n".join(sorted(p.name for p in RESULT.iterdir())))


if __name__ == "__main__":
    main()
