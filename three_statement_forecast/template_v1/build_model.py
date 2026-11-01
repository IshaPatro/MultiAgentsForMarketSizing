"""Build an Inputs-driven, VC-style portfolio company model (B2B SaaS, 36 months).

Every hardcoded number lives on the Inputs sheet as a named range. All other
sheets are formulas that reference those names, so an agent only ever writes
INPUTS (and AGENT_FLAGS); it never writes numbers into the calculation sheets.

Usage: python build_model.py [output.xlsx]
"""
import sys
from datetime import date

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.hyperlink import Hyperlink

from model_rules import SPEC, cell_kind, font_color

C = SPEC["colors"]
NF = SPEC["number_formats"]
FONT = SPEC["font"]["name"]
LAY = SPEC["layout"]
N_MONTHS = LAY["months"]

OPEN = "C"
MONTHS = [get_column_letter(i) for i in range(4, 4 + N_MONTHS)]
LAST = MONTHS[-1]
PREV = {m: get_column_letter(i + 3) for i, m in enumerate(MONTHS)}  # D->C, E->D, ...

SOURCES = ["User", "Data-derived", "Agent default", "Benchmark"]
BCV_SECTORS = ["AI", "Cybersecurity", "Infrastructure", "Healthcare", "Business Application", "Open Source"]
STAGES = ["Seed", "Series A", "Series B", "Series C", "Growth"]
DEPTS = [("RD", "R&D"), ("SM", "Sales & Marketing"), ("GA", "G&A"), ("CS", "Customer Success (COGS)")]

# --------------------------------------------------------------------------------------
# INPUTS — the only hardcoded numbers in the workbook. An agent fills this table from
# messy raw data and sets source + confidence for each row.
# (name, label, value, unit, format, source, confidence, note, dropdown)
# --------------------------------------------------------------------------------------
def I(name, label, value, unit, fmt, source="User", conf=1.0, note="", dropdown=None):
    return dict(name=name, label=label, value=value, unit=unit, fmt=fmt, source=source,
                conf=conf, note=note, dropdown=dropdown)


INPUTS = [
    ("Company & model setup", [
        I("Company_Name", "Company name", "Sample SaaS Co. (Illustrative)", "", "text",
          note="Illustrative placeholder, not actual portfolio data"),
        I("Sector", "BCV sector", "Cybersecurity", "", "text", dropdown=BCV_SECTORS),
        I("Stage", "Stage", "Series B", "", "text", dropdown=STAGES),
        I("Currency", "Reporting currency", "USD", "", "text"),
        I("Model_Start_Date", "First forecast month", date(2026, 1, 1), "date", "date_long"),
        I("Last_Round_Post_Money", "Last round post-money valuation", 180_000_000, "$", "currency",
          note="From cap table / last financing"),
        I("BCV_Ownership", "BCV fully-diluted ownership", 0.09, "%", "pct"),
    ]),
    ("Model controls", [
        I("Conf_Threshold", "Confidence threshold for flagging", 0.70, "%", "conf",
          note="Inputs and calculations below this are flagged on Checks & Flags"),
        I("Check_Tolerance", "Integrity-check tolerance", 1, "$", "currency"),
        I("Min_Runway_Months", "Minimum acceptable runway", 12, "months", "dec1"),
    ]),
    ("Opening balance sheet (as of month before first forecast month)", [
        I("Open_Cash", "Cash", 22_000_000, "$", "currency", "Data-derived", 0.95, "Bank statements, Dec-25"),
        I("Open_AR", "Accounts receivable", 1_450_000, "$", "currency", "Data-derived", 0.85, "AR aging export"),
        I("Open_Prepaid", "Prepaid expenses", 300_000, "$", "currency", "Data-derived", 0.80),
        I("Open_PPE", "PP&E, net", 600_000, "$", "currency", "Data-derived", 0.85),
        I("Open_AP", "Accounts payable", 450_000, "$", "currency", "Data-derived", 0.85),
        I("Open_Accrued", "Accrued liabilities", 410_000, "$", "currency", "Data-derived", 0.75),
        I("Open_Deferred_Rev", "Deferred revenue", 3_600_000, "$", "currency", "Data-derived", 0.80),
        I("Open_Debt", "Debt", 0, "$", "currency", "Data-derived", 0.95),
        I("Open_Paid_In_Capital", "Paid-in capital", 55_000_000, "$", "currency", "Data-derived", 0.95,
          "Cap table"),
        I("Open_Retained_Earnings", "Retained earnings (accumulated deficit)", -35_110_000, "$", "currency",
          "Data-derived", 0.90, "Plug to trial balance; verified by opening-balance check"),
    ]),
    ("Revenue drivers", [
        I("Open_Customers", "Opening customers", 220, "#", "int", "Data-derived", 0.90, "CRM export"),
        I("Open_ARR", "Opening ARR", 12_000_000, "$", "currency", "Data-derived", 0.90, "Billing system"),
        I("New_ACV", "ACV of new logos (Year 1)", 60_000, "$", "currency", "Data-derived", 0.75),
        I("Price_Increase_Annual", "Annual price increase on new ACV", 0.03, "%", "pct", "Agent default", 0.65,
          "No pricing history in data room"),
        I("New_Logos_Y1", "New logos per month: Year 1", 8, "#", "dec1", "User", 0.80, "Mgmt plan"),
        I("New_Logos_Y2", "New logos per month: Year 2", 11, "#", "dec1", "User", 0.70, "Mgmt plan"),
        I("New_Logos_Y3", "New logos per month: Year 3", 14, "#", "dec1", "User", 0.60, "Mgmt plan, aggressive"),
        I("Logo_Churn_Monthly", "Monthly logo / ARR churn", 0.008, "%", "pct", "Benchmark", 0.55,
          "No churn field in raw export; mid-market SaaS benchmark"),
        I("Expansion_Monthly", "Monthly expansion (% of BoP ARR)", 0.015, "%", "pct", "Data-derived", 0.60,
          "Derived from only 4 months of upsell data"),
        I("Services_Pct_Sub", "Services revenue (% of subscription)", 0.06, "%", "pct", "Data-derived", 0.80),
    ]),
    ("Billing & working capital", [
        I("Annual_Prepay_Mix", "Share of ARR billed annually upfront", 0.60, "%", "pct", "Agent default", 0.50,
          "Billing terms not in data room"),
        I("Avg_Deferred_Months", "Avg. unearned months on annual contracts", 6, "months", "dec1", "Agent default",
          0.70, "Assumes renewals spread evenly through the year"),
        I("DSO_Days", "DSO", 45, "days", "dec1", "Data-derived", 0.80),
        I("DPO_Days", "DPO (on non-headcount spend)", 30, "days", "dec1", "Data-derived", 0.80),
        I("Accrued_Pct_Payroll", "Accrued liabilities (% of monthly payroll)", 0.25, "%", "pct", "Agent default",
          0.70),
        I("Prepaid_Months", "Prepaid (months of non-headcount spend)", 0.5, "months", "dec1", "Agent default",
          0.70),
    ]),
    ("Cost of revenue", [
        I("Hosting_Pct", "Hosting & infra (% of subscription revenue)", 0.09, "%", "pct", "Data-derived", 0.85,
          "AWS invoices"),
        I("Services_Margin", "Professional services gross margin", 0.20, "%", "pct", "Benchmark", 0.65),
    ]),
    ("Headcount plan", [
        *[I(f"Open_HC_{d}", f"Opening headcount: {lbl}", v, "FTE", "dec1", "Data-derived", 0.95, "HRIS")
          for (d, lbl), v in zip(DEPTS, [45, 35, 12, 14])],
        *[I(f"Hires_{d}_Y{y}", f"Net hires per month: {lbl}, Year {y}", v, "FTE", "dec1", "User",
            0.80 if y == 1 else 0.70, "Mgmt hiring plan")
          for (d, lbl), vs in zip(DEPTS, [[1.5, 1.5, 1.0], [1.5, 2.0, 2.0], [0.5, 0.5, 0.5], [0.5, 0.75, 1.0]])
          for y, v in zip((1, 2, 3), vs)],
        *[I(f"Cost_FTE_{d}", f"Fully-loaded annual cost per FTE: {lbl}", v, "$", "currency", "Data-derived",
            0.85, "Payroll register + 22% burden")
          for (d, lbl), v in zip(DEPTS, [210_000, 190_000, 170_000, 120_000])],
        I("Comp_Inflation", "Annual compensation inflation", 0.04, "%", "pct", "Benchmark", 0.75),
    ]),
    ("Operating expenses (non-headcount)", [
        I("Commission_Rate", "Sales commission (% of new + expansion ARR)", 0.10, "%", "pct", "Data-derived", 0.75,
          "Comp plan"),
        I("Marketing_Programs_Monthly", "Marketing programs per month (Year 1)", 200_000, "$", "currency",
          "Data-derived", 0.80),
        I("Marketing_Growth_Annual", "Annual growth in marketing programs", 0.25, "%", "pct", "Agent default", 0.60),
        I("Software_Per_FTE_Monthly", "Software & tools per FTE per month", 400, "$", "currency", "Benchmark", 0.75),
        I("Rent_Per_FTE_Monthly", "Rent & facilities per FTE per month", 900, "$", "currency", "Data-derived",
          0.80),
        I("Other_GA_Monthly", "Other G&A per month (legal, audit, insurance)", 120_000, "$", "currency",
          "Data-derived", 0.75),
        I("Opex_Inflation", "Annual inflation on other G&A", 0.03, "%", "pct", "Benchmark", 0.75),
    ]),
    ("Capex & depreciation", [
        I("Capex_Per_New_Hire", "Capex per new hire (laptop, equipment)", 3_500, "$", "currency", "Benchmark", 0.80),
        I("Capex_Maintenance_Monthly", "Maintenance capex per month", 15_000, "$", "currency", "Data-derived", 0.75),
        I("Depr_Life_Months", "Depreciation life (on net book value)", 36, "months", "int", "User", 0.90),
    ]),
    ("Financing, interest & tax", [
        I("Equity_Raise_Month", "Equity raise: model month #", 18, "month #", "int", "User", 0.70),
        I("Equity_Raise_Amount", "Equity raise: amount", 40_000_000, "$", "currency", "User", 0.60,
          "Next round size not yet committed"),
        I("Debt_Draw_Month", "Venture debt draw: model month #", 9, "month #", "int", "User", 0.80),
        I("Debt_Draw_Amount", "Venture debt draw: amount", 5_000_000, "$", "currency", "User", 0.80),
        I("Debt_Rate", "Venture debt interest rate (interest-only)", 0.11, "%", "pct", "Agent default", 0.55,
          "Term sheet not provided"),
        I("Interest_Income_Rate", "Interest income on cash", 0.035, "%", "pct", "Benchmark", 0.80),
        I("Tax_Rate", "Cash tax rate (on positive pre-tax income)", 0.21, "%", "pct", "Benchmark", 0.85),
    ]),
]

# Calculation-level flags the agent raises itself (beyond low-confidence inputs).
AGENT_FLAGS = [
    dict(sheet="Balance Sheet", key="dr", col=LAST, item="Deferred revenue, Month 36", conf=0.50,
         reason="Modeled as ARR × prepay mix × unearned months. Raw data had no invoice-level billing schedule.",
         action="Request billing schedule / invoice export from company"),
    dict(sheet="Revenue", key="arr_churn", col=LAST, item="Churned ARR, Month 36", conf=0.55,
         reason="Churn applied uniformly to logos and ARR; no cohort data to separate logo vs. dollar churn.",
         action="Request cohort retention table"),
    dict(sheet="KPIs", key="cac_payback", col=LAST, item="CAC payback, Month 36", conf=0.60,
         reason="All S&M spend attributed to new logos; expansion selling cost not separable in GL.",
         action="Confirm S&M split between new-logo and expansion teams"),
]

# Key calculations: confidence = weakest input they depend on (weakest-link rule).
_ARR_DEPS = ["Open_ARR", "New_ACV", "Price_Increase_Annual", "New_Logos_Y1", "New_Logos_Y2", "New_Logos_Y3",
             "Logo_Churn_Monthly", "Expansion_Monthly"]
_COST_DEPS = [f"Cost_FTE_{d}" for d, _ in DEPTS] + [f"Hires_{d}_Y{y}" for d, _ in DEPTS for y in (1, 2, 3)] + \
             ["Marketing_Programs_Monthly", "Marketing_Growth_Annual", "Hosting_Pct", "Commission_Rate"]
KEY_CALCS = [
    ("Ending ARR (Month 36)", "Revenue", "arr_eop", LAST, "currency", _ARR_DEPS),
    ("Total revenue, Year 3", "Summary", "rev", "E", "currency", _ARR_DEPS + ["Services_Pct_Sub"]),
    ("Gross margin, Year 3", "Summary", "gm", "E", "pct",
     ["Hosting_Pct", "Services_Margin", "Cost_FTE_CS", "Hires_CS_Y3"]),
    ("Net revenue retention, Year 3", "Summary", "nrr", "E", "pct", ["Expansion_Monthly", "Logo_Churn_Monthly"]),
    ("Deferred revenue (Month 36)", "Balance Sheet", "dr", LAST, "currency",
     _ARR_DEPS + ["Annual_Prepay_Mix", "Avg_Deferred_Months"]),
    ("Ending cash (Month 36)", "Cash Flow", "cash_eop", LAST, "currency",
     ["Open_Cash", "Equity_Raise_Amount", "Equity_Raise_Month", "Debt_Draw_Amount", "Annual_Prepay_Mix",
      "DSO_Days"] + _ARR_DEPS + _COST_DEPS),
    ("Burn multiple, Year 3", "Summary", "burn_mult", "E", "multiple", _ARR_DEPS + _COST_DEPS),
    ("CAC payback (Month 36)", "KPIs", "cac_payback", LAST, "months",
     ["Marketing_Programs_Monthly", "Marketing_Growth_Annual", "Commission_Rate", "Cost_FTE_SM", "Hires_SM_Y3",
      "New_ACV", "Hosting_Pct"]),
    ("Interest expense, Year 2", "Summary", "int_exp", "D", "currency", ["Debt_Rate", "Debt_Draw_Amount"]),
]

# --------------------------------------------------------------------------------------
# Styling helpers
# --------------------------------------------------------------------------------------
thin = Side(style="thin", color=C["grid"])
med = Side(style="medium", color=C["navy"])


def fill(hex_):
    return PatternFill("solid", start_color=hex_, end_color=hex_)


def font(**kw):
    kw.setdefault("name", FONT)
    kw.setdefault("size", SPEC["font"]["size"])
    return Font(**kw)


def put(ws, coord, value, fmt=None, bold=False, italic=False, fill_hex=None, align=None, color=None, size=None):
    cell = ws[coord]
    cell.value = value
    col = color or font_color(cell_kind(value))
    cell.font = font(bold=bold, italic=italic, color=col, **({"size": size} if size else {}))
    if fmt:
        cell.number_format = NF[fmt]
    if fill_hex:
        cell.fill = fill(fill_hex)
    if align:
        cell.alignment = Alignment(horizontal="left" if align == "wrap" else align, vertical="center",
                                   wrap_text=align == "wrap")
    return cell


def q(sheet):
    return f"'{sheet}'" if any(ch in sheet for ch in " &") else sheet


def title_band(ws, title, subtitle, width_cols):
    ws.sheet_view.showGridLines = False
    for col in range(1, width_cols + 1):
        ws.cell(row=1, column=col).fill = fill(C["navy"])
        ws.cell(row=2, column=col).fill = fill(C["navy"])
    put(ws, "A1", title, bold=True, color=C["white"], size=SPEC["font"]["title_size"])
    put(ws, "A2", subtitle, italic=True, color="D6E2F2", size=SPEC["font"]["subtitle_size"])
    ws.row_dimensions[1].height = 28


# --------------------------------------------------------------------------------------
# Monthly sheets: layout declared as rows, row numbers resolved before formulas are written
# --------------------------------------------------------------------------------------
ROWS = {}  # sheet -> key -> row number


def R(sheet, key):
    return ROWS[sheet][key]


def ref(sheet, key, col, here=None):
    return f"{col}{R(sheet, key)}" if sheet == here else f"{q(sheet)}!{col}{R(sheet, key)}"


def section(title):
    return ("section", title)


def row(key, label, unit, fmt, month, open_=None, style="normal"):
    return ("row", dict(key=key, label=label, unit=unit, fmt=fmt, month=month, open=open_, style=style))


def blank():
    return ("blank", None)


def assign_rows(sheet, layout):
    r = LAY["first_content_row"]
    ROWS[sheet] = {"idx": 4, "date": 5, "year": 6}
    for kind, spec in layout:
        if kind == "row":
            ROWS[sheet][spec["key"]] = r
        r += 1


def write_monthly(ws, sheet, subtitle, layout):
    width = 3 + N_MONTHS
    title_band(ws, sheet, subtitle, width)
    ws.column_dimensions["A"].width = LAY["label_width"]
    ws.column_dimensions["B"].width = LAY["unit_width"]
    for col in [OPEN] + MONTHS:
        ws.column_dimensions[col].width = LAY["period_width"]

    # Timeline (identical formula logic on every sheet; anchored only on Model_Start_Date)
    hdr = fill(C["navy"])
    for r_, label in ((4, "Month #"), (5, "Period ending"), (6, "Model year")):
        for col in range(1, width + 1):
            ws.cell(row=r_, column=col).fill = hdr if r_ == 5 else fill(C["section_fill"])
        put(ws, f"A{r_}", label, bold=r_ == 5, color=C["white"] if r_ == 5 else C["muted"],
            fill_hex=C["navy"] if r_ == 5 else C["section_fill"])
    put(ws, "C3", "Opening", italic=True, color=C["muted"], align="right")
    put(ws, "D3", "Forecast →", italic=True, color=C["muted"])
    put(ws, "C4", 0, "int", align="right", color=C["muted"], fill_hex=C["section_fill"])
    put(ws, "C5", "=EOMONTH(Model_Start_Date,C4-1)", "date", bold=True, align="right", color=C["white"],
        fill_hex=C["navy"])
    for m in MONTHS:
        p = PREV[m]
        put(ws, f"{m}4", f"={p}4+1", "int", align="right", color=C["muted"], fill_hex=C["section_fill"])
        put(ws, f"{m}5", f"=EOMONTH(Model_Start_Date,{m}4-1)", "date", bold=True, align="right",
            color=C["white"], fill_hex=C["navy"])
        put(ws, f"{m}6", f"=INT(({m}4-1)/12)+1", "int", align="right", color=C["muted"],
            fill_hex=C["section_fill"])

    r = LAY["first_content_row"]
    for kind, spec in layout:
        if kind == "section":
            for col in range(1, width + 1):
                ws.cell(row=r, column=col).fill = fill(C["section_fill"])
            put(ws, f"A{r}", spec, bold=True, color=C["accent"], fill_hex=C["section_fill"])
        elif kind == "row":
            st = spec["style"]
            indent = 0 if st in ("total", "check") else 1
            put(ws, f"A{r}", spec["label"], bold=st == "total", italic=st == "pct")
            ws[f"A{r}"].alignment = Alignment(indent=indent)
            put(ws, f"B{r}", spec["unit"], italic=True, color=C["muted"], align="center")
            if spec["open"]:
                put(ws, f"{OPEN}{r}", spec["open"](OPEN), spec["fmt"], bold=st == "total")
            for m in MONTHS:
                put(ws, f"{m}{r}", spec["month"](m, PREV[m]), spec["fmt"], bold=st == "total",
                    italic=st == "pct")
            if st == "total":
                for col in range(3, width + 1):
                    ws.cell(row=r, column=col).border = Border(top=thin)
            if st == "check":
                rng = f"{OPEN}{r}:{LAST}{r}"
                ws.conditional_formatting.add(rng, FormulaRule(
                    formula=[f"ABS({OPEN}{r})>Check_Tolerance"], fill=fill(C["fail_fill"])))
                ws.conditional_formatting.add(rng, FormulaRule(
                    formula=[f"ABS({OPEN}{r})<=Check_Tolerance"], fill=fill(C["pass_fill"])))
        r += 1
    ws.freeze_panes = LAY["freeze_at"]
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True


def chs(names, m):
    return f"CHOOSE({m}6,{','.join(names)})"


def growth(base, rate, m):
    return f"{base}*(1+{rate})^({m}6-1)"


# ---- Revenue -------------------------------------------------------------------------
def revenue_layout():
    S = "Revenue"
    s = lambda k, c: ref(S, k, c, S)
    return [
        section("Customers"),
        row("cust_bop", "Beginning customers", "#", "dec1", lambda m, p: f"={s('cust_eop', p)}"),
        row("cust_new", "New logos", "#", "dec1",
            lambda m, p: f"={chs(['New_Logos_Y1', 'New_Logos_Y2', 'New_Logos_Y3'], m)}"),
        row("cust_churn", "Churned logos", "#", "dec1",
            lambda m, p: f"=-{s('cust_bop', m)}*Logo_Churn_Monthly"),
        row("cust_eop", "Ending customers", "#", "dec1",
            lambda m, p: f"={s('cust_bop', m)}+{s('cust_new', m)}+{s('cust_churn', m)}",
            open_=lambda c: "=Open_Customers", style="total"),
        blank(),
        section("ARR bridge"),
        row("acv", "ACV of new logos", "$", "currency", lambda m, p: f"={growth('New_ACV', 'Price_Increase_Annual', m)}"),
        row("arr_bop", "Beginning ARR", "$", "currency", lambda m, p: f"={s('arr_eop', p)}"),
        row("arr_new", "New logo ARR", "$", "currency", lambda m, p: f"={s('cust_new', m)}*{s('acv', m)}"),
        row("arr_exp", "Expansion ARR", "$", "currency", lambda m, p: f"={s('arr_bop', m)}*Expansion_Monthly"),
        row("arr_churn", "Churned ARR", "$", "currency", lambda m, p: f"=-{s('arr_bop', m)}*Logo_Churn_Monthly"),
        row("arr_eop", "Ending ARR", "$", "currency",
            lambda m, p: f"={s('arr_bop', m)}+{s('arr_new', m)}+{s('arr_exp', m)}+{s('arr_churn', m)}",
            open_=lambda c: "=Open_ARR", style="total"),
        row("arr_netnew", "Net new ARR", "$", "currency", lambda m, p: f"={s('arr_eop', m)}-{s('arr_bop', m)}"),
        row("arpa", "ARR per customer", "$", "currency",
            lambda m, p: f"=IFERROR({s('arr_eop', m)}/{s('cust_eop', m)},0)",
            open_=lambda c: f"=IFERROR({s('arr_eop', c)}/{s('cust_eop', c)},0)"),
        blank(),
        section("Revenue recognition"),
        row("rev_sub", "Subscription revenue", "$", "currency",
            lambda m, p: f"=({s('arr_bop', m)}+{s('arr_eop', m)})/2/12"),
        row("rev_svc", "Professional services revenue", "$", "currency",
            lambda m, p: f"={s('rev_sub', m)}*Services_Pct_Sub"),
        row("rev_total", "Total revenue", "$", "currency", lambda m, p: f"={s('rev_sub', m)}+{s('rev_svc', m)}",
            style="total"),
        blank(),
        section("Billings & deferred revenue"),
        row("dr_eop", "Deferred revenue (ending)", "$", "currency",
            lambda m, p: f"={s('arr_eop', m)}*Annual_Prepay_Mix*Avg_Deferred_Months/12",
            open_=lambda c: "=Open_Deferred_Rev"),
        row("billings", "Billings", "$", "currency",
            lambda m, p: f"={s('rev_total', m)}+{s('dr_eop', m)}-{s('dr_eop', p)}", style="total"),
    ]


# ---- Headcount & Opex ------------------------------------------------------------------
def opex_layout():
    S = "Headcount & Opex"
    s = lambda k, c: ref(S, k, c, S)
    rv = lambda k, c: ref("Revenue", k, c)
    lay = [section("Net hires (FTE / month)")]
    lay += [row(f"hires_{d}", lbl, "FTE", "dec1",
                lambda m, p, d=d: f"={chs([f'Hires_{d}_Y1', f'Hires_{d}_Y2', f'Hires_{d}_Y3'], m)}")
            for d, lbl in DEPTS]
    lay += [row("hires_total", "Total net hires", "FTE", "dec1",
                lambda m, p: "=" + "+".join(s(f"hires_{d}", m) for d, _ in DEPTS), style="total"),
            blank(), section("Headcount (end of period)")]
    lay += [row(f"hc_{d}", lbl, "FTE", "dec1", lambda m, p, d=d: f"={s(f'hc_{d}', p)}+{s(f'hires_{d}', m)}",
                open_=lambda c, d=d: f"=Open_HC_{d}") for d, lbl in DEPTS]
    lay += [row("hc_total", "Total headcount", "FTE", "dec1",
                lambda m, p: "=" + "+".join(s(f"hc_{d}", m) for d, _ in DEPTS),
                open_=lambda c: "=" + "+".join(s(f"hc_{d}", c) for d, _ in DEPTS), style="total"),
            blank(), section("Fully-loaded cost per FTE (annual, with comp inflation)")]
    lay += [row(f"cost_{d}", lbl, "$", "currency",
                lambda m, p, d=d: f"={growth(f'Cost_FTE_{d}', 'Comp_Inflation', m)}") for d, lbl in DEPTS]
    lay += [blank(), section("Payroll (monthly)")]
    lay += [row(f"pay_{d}", lbl, "$", "currency", lambda m, p, d=d: f"={s(f'hc_{d}', m)}*{s(f'cost_{d}', m)}/12")
            for d, lbl in DEPTS]
    lay += [row("pay_total", "Total payroll", "$", "currency",
                lambda m, p: "=" + "+".join(s(f"pay_{d}", m) for d, _ in DEPTS), style="total"),
            blank(), section("Variable & non-headcount costs"),
            row("commissions", "Sales commissions", "$", "currency",
                lambda m, p: f"=Commission_Rate*({rv('arr_new', m)}+{rv('arr_exp', m)})"),
            row("hosting", "Hosting & infrastructure", "$", "currency",
                lambda m, p: f"=Hosting_Pct*{rv('rev_sub', m)}"),
            row("svc_cost", "Services delivery cost", "$", "currency",
                lambda m, p: f"={rv('rev_svc', m)}*(1-Services_Margin)"),
            row("marketing", "Marketing programs", "$", "currency",
                lambda m, p: f"={growth('Marketing_Programs_Monthly', 'Marketing_Growth_Annual', m)}"),
            row("software", "Software & tools", "$", "currency",
                lambda m, p: f"={s('hc_total', m)}*Software_Per_FTE_Monthly"),
            row("rent", "Rent & facilities", "$", "currency",
                lambda m, p: f"={s('hc_total', m)}*Rent_Per_FTE_Monthly"),
            row("other_ga", "Other G&A", "$", "currency",
                lambda m, p: f"={growth('Other_GA_Monthly', 'Opex_Inflation', m)}"),
            row("nonhc_total", "Non-headcount spend (AP basis)", "$", "currency",
                lambda m, p: "=" + "+".join(s(k, m) for k in
                                            ("hosting", "svc_cost", "marketing", "software", "rent", "other_ga")),
                style="total"),
            blank(), section("Allocation to P&L lines"),
            row("alloc_cogs", "Cost of revenue", "$", "currency",
                lambda m, p: f"={s('pay_CS', m)}+{s('hosting', m)}+{s('svc_cost', m)}"),
            row("alloc_rd", "Research & development", "$", "currency", lambda m, p: f"={s('pay_RD', m)}"),
            row("alloc_sm", "Sales & marketing", "$", "currency",
                lambda m, p: f"={s('pay_SM', m)}+{s('commissions', m)}+{s('marketing', m)}"),
            row("alloc_ga", "General & administrative", "$", "currency",
                lambda m, p: f"={s('pay_GA', m)}+{s('software', m)}+{s('rent', m)}+{s('other_ga', m)}"),
            row("alloc_total", "Total costs allocated", "$", "currency",
                lambda m, p: "=" + "+".join(s(k, m) for k in ("alloc_cogs", "alloc_rd", "alloc_sm", "alloc_ga")),
                style="total"),
            ]
    return lay


# ---- P&L -------------------------------------------------------------------------------
def pl_layout():
    S = "P&L"
    s = lambda k, c: ref(S, k, c, S)
    rv = lambda k, c: ref("Revenue", k, c)
    ox = lambda k, c: ref("Headcount & Opex", k, c)
    return [
        section("Revenue"),
        row("sub", "Subscription revenue", "$", "currency", lambda m, p: f"={rv('rev_sub', m)}"),
        row("svc", "Professional services revenue", "$", "currency", lambda m, p: f"={rv('rev_svc', m)}"),
        row("rev", "Total revenue", "$", "currency", lambda m, p: f"={s('sub', m)}+{s('svc', m)}", style="total"),
        blank(),
        section("Cost of revenue"),
        row("cogs_host", "Hosting & infrastructure", "$", "currency", lambda m, p: f"={ox('hosting', m)}"),
        row("cogs_cs", "Customer success payroll", "$", "currency", lambda m, p: f"={ox('pay_CS', m)}"),
        row("cogs_svc", "Services delivery", "$", "currency", lambda m, p: f"={ox('svc_cost', m)}"),
        row("cogs", "Total cost of revenue", "$", "currency",
            lambda m, p: f"={s('cogs_host', m)}+{s('cogs_cs', m)}+{s('cogs_svc', m)}", style="total"),
        row("gp", "Gross profit", "$", "currency", lambda m, p: f"={s('rev', m)}-{s('cogs', m)}", style="total"),
        row("gm", "Gross margin %", "%", "pct", lambda m, p: f"=IFERROR({s('gp', m)}/{s('rev', m)},0)", style="pct"),
        blank(),
        section("Operating expenses"),
        row("rd", "Research & development", "$", "currency", lambda m, p: f"={ox('alloc_rd', m)}"),
        row("sm", "Sales & marketing", "$", "currency", lambda m, p: f"={ox('alloc_sm', m)}"),
        row("ga", "General & administrative", "$", "currency", lambda m, p: f"={ox('alloc_ga', m)}"),
        row("opex", "Total operating expenses", "$", "currency",
            lambda m, p: f"={s('rd', m)}+{s('sm', m)}+{s('ga', m)}", style="total"),
        blank(),
        row("ebitda", "EBITDA", "$", "currency", lambda m, p: f"={s('gp', m)}-{s('opex', m)}", style="total"),
        row("ebitda_m", "EBITDA margin %", "%", "pct", lambda m, p: f"=IFERROR({s('ebitda', m)}/{s('rev', m)},0)",
            style="pct"),
        row("da", "Depreciation", "$", "currency", lambda m, p: f"={ref('Balance Sheet', 'ppe_da', m)}"),
        row("ebit", "EBIT", "$", "currency", lambda m, p: f"={s('ebitda', m)}-{s('da', m)}", style="total"),
        row("int_inc", "Interest income", "$", "currency",
            lambda m, p: f"={ref('Cash Flow', 'cash_bop', m)}*Interest_Income_Rate/12"),
        row("int_exp", "Interest expense", "$", "currency",
            lambda m, p: f"={ref('Balance Sheet', 'debt_eop', p)}*Debt_Rate/12"),
        row("ebt", "Pre-tax income", "$", "currency",
            lambda m, p: f"={s('ebit', m)}+{s('int_inc', m)}-{s('int_exp', m)}", style="total"),
        row("tax", "Cash taxes", "$", "currency", lambda m, p: f"=MAX(0,{s('ebt', m)})*Tax_Rate"),
        row("ni", "Net income", "$", "currency", lambda m, p: f"={s('ebt', m)}-{s('tax', m)}", style="total"),
    ]


# ---- Balance sheet ---------------------------------------------------------------------
def bs_layout():
    S = "Balance Sheet"
    s = lambda k, c: ref(S, k, c, S)
    ox = lambda k, c: ref("Headcount & Opex", k, c)
    return [
        section("Assets"),
        row("cash", "Cash", "$", "currency", lambda m, p: f"={ref('Cash Flow', 'cash_eop', m)}",
            open_=lambda c: "=Open_Cash"),
        row("ar", "Accounts receivable", "$", "currency",
            lambda m, p: f"={ref('Revenue', 'billings', m)}*12*DSO_Days/365", open_=lambda c: "=Open_AR"),
        row("prepaid", "Prepaid expenses", "$", "currency", lambda m, p: f"={ox('nonhc_total', m)}*Prepaid_Months",
            open_=lambda c: "=Open_Prepaid"),
        row("ppe", "PP&E, net", "$", "currency", lambda m, p: f"={s('ppe_eop', m)}",
            open_=lambda c: f"={s('ppe_eop', c)}"),
        row("ta", "Total assets", "$", "currency",
            lambda m, p: f"=SUM({m}{R(S, 'cash')}:{m}{R(S, 'ppe')})",
            open_=lambda c: f"=SUM({c}{R(S, 'cash')}:{c}{R(S, 'ppe')})", style="total"),
        blank(),
        section("Liabilities"),
        row("ap", "Accounts payable", "$", "currency",
            lambda m, p: f"={ox('nonhc_total', m)}*12*DPO_Days/365", open_=lambda c: "=Open_AP"),
        row("accrued", "Accrued liabilities", "$", "currency",
            lambda m, p: f"={ox('pay_total', m)}*Accrued_Pct_Payroll", open_=lambda c: "=Open_Accrued"),
        row("dr", "Deferred revenue", "$", "currency", lambda m, p: f"={ref('Revenue', 'dr_eop', m)}",
            open_=lambda c: f"={ref('Revenue', 'dr_eop', c)}"),
        row("debt", "Venture debt", "$", "currency", lambda m, p: f"={s('debt_eop', m)}",
            open_=lambda c: f"={s('debt_eop', c)}"),
        row("tl", "Total liabilities", "$", "currency",
            lambda m, p: f"=SUM({m}{R(S, 'ap')}:{m}{R(S, 'debt')})",
            open_=lambda c: f"=SUM({c}{R(S, 'ap')}:{c}{R(S, 'debt')})", style="total"),
        blank(),
        section("Equity"),
        row("pic", "Paid-in capital", "$", "currency",
            lambda m, p: f"={s('pic', p)}+{ref('Cash Flow', 'equity', m)}", open_=lambda c: "=Open_Paid_In_Capital"),
        row("re", "Retained earnings", "$", "currency", lambda m, p: f"={s('re', p)}+{ref('P&L', 'ni', m)}",
            open_=lambda c: "=Open_Retained_Earnings"),
        row("te", "Total equity", "$", "currency", lambda m, p: f"={s('pic', m)}+{s('re', m)}",
            open_=lambda c: f"={s('pic', c)}+{s('re', c)}", style="total"),
        row("tle", "Total liabilities & equity", "$", "currency", lambda m, p: f"={s('tl', m)}+{s('te', m)}",
            open_=lambda c: f"={s('tl', c)}+{s('te', c)}", style="total"),
        row("check", "Balance check (assets − L&E)", "$", "currency",
            lambda m, p: f"=ROUND({s('ta', m)}-{s('tle', m)},2)",
            open_=lambda c: f"=ROUND({s('ta', c)}-{s('tle', c)},2)", style="check"),
        blank(),
        section("Fixed asset schedule"),
        row("ppe_bop", "Beginning PP&E, net", "$", "currency", lambda m, p: f"={s('ppe_eop', p)}"),
        row("capex", "Capex", "$", "currency",
            lambda m, p: f"={ox('hires_total', m)}*Capex_Per_New_Hire+Capex_Maintenance_Monthly"),
        row("ppe_da", "Depreciation", "$", "currency", lambda m, p: f"={s('ppe_bop', m)}/Depr_Life_Months"),
        row("ppe_eop", "Ending PP&E, net", "$", "currency",
            lambda m, p: f"={s('ppe_bop', m)}+{s('capex', m)}-{s('ppe_da', m)}", open_=lambda c: "=Open_PPE",
            style="total"),
        blank(),
        section("Debt schedule"),
        row("debt_bop", "Beginning debt", "$", "currency", lambda m, p: f"={s('debt_eop', p)}"),
        row("debt_draw", "Draws", "$", "currency",
            lambda m, p: f"=IF({m}4=Debt_Draw_Month,Debt_Draw_Amount,0)"),
        row("debt_eop", "Ending debt", "$", "currency", lambda m, p: f"={s('debt_bop', m)}+{s('debt_draw', m)}",
            open_=lambda c: "=Open_Debt", style="total"),
    ]


# ---- Cash flow -------------------------------------------------------------------------
def cf_layout():
    S = "Cash Flow"
    s = lambda k, c: ref(S, k, c, S)
    bs = lambda k, c: ref("Balance Sheet", k, c)
    return [
        section("Operating activities"),
        row("ni", "Net income", "$", "currency", lambda m, p: f"={ref('P&L', 'ni', m)}"),
        row("da", "Depreciation", "$", "currency", lambda m, p: f"={ref('P&L', 'da', m)}"),
        row("d_ar", "(Increase) / decrease in AR", "$", "currency", lambda m, p: f"={bs('ar', p)}-{bs('ar', m)}"),
        row("d_prepaid", "(Increase) / decrease in prepaids", "$", "currency",
            lambda m, p: f"={bs('prepaid', p)}-{bs('prepaid', m)}"),
        row("d_ap", "Increase / (decrease) in AP", "$", "currency", lambda m, p: f"={bs('ap', m)}-{bs('ap', p)}"),
        row("d_accrued", "Increase / (decrease) in accruals", "$", "currency",
            lambda m, p: f"={bs('accrued', m)}-{bs('accrued', p)}"),
        row("d_dr", "Increase / (decrease) in deferred revenue", "$", "currency",
            lambda m, p: f"={bs('dr', m)}-{bs('dr', p)}"),
        row("cfo", "Cash flow from operations", "$", "currency",
            lambda m, p: f"=SUM({m}{R(S, 'ni')}:{m}{R(S, 'd_dr')})", style="total"),
        blank(),
        section("Investing activities"),
        row("capex", "Capex", "$", "currency", lambda m, p: f"=-{bs('capex', m)}"),
        row("cfi", "Cash flow from investing", "$", "currency", lambda m, p: f"={s('capex', m)}", style="total"),
        blank(),
        section("Financing activities"),
        row("equity", "Equity raised", "$", "currency",
            lambda m, p: f"=IF({m}4=Equity_Raise_Month,Equity_Raise_Amount,0)"),
        row("debt", "Debt drawn", "$", "currency", lambda m, p: f"={bs('debt_draw', m)}"),
        row("cff", "Cash flow from financing", "$", "currency", lambda m, p: f"={s('equity', m)}+{s('debt', m)}",
            style="total"),
        blank(),
        section("Cash"),
        row("net", "Net change in cash", "$", "currency",
            lambda m, p: f"={s('cfo', m)}+{s('cfi', m)}+{s('cff', m)}"),
        row("cash_bop", "Beginning cash", "$", "currency", lambda m, p: f"={s('cash_eop', p)}"),
        row("cash_eop", "Ending cash", "$", "currency", lambda m, p: f"={s('cash_bop', m)}+{s('net', m)}",
            open_=lambda c: "=Open_Cash", style="total"),
    ]


# ---- KPIs ------------------------------------------------------------------------------
def kpi_layout():
    S = "KPIs"
    s = lambda k, c: ref(S, k, c, S)
    rv = lambda k, c: ref("Revenue", k, c)
    pl = lambda k, c: ref("P&L", k, c)
    cf = lambda k, c: ref("Cash Flow", k, c)
    return [
        section("Growth"),
        row("arr", "Ending ARR", "$", "currency", lambda m, p: f"={rv('arr_eop', m)}",
            open_=lambda c: f"={rv('arr_eop', c)}"),
        row("netnew", "Net new ARR", "$", "currency", lambda m, p: f"={rv('arr_netnew', m)}"),
        row("growth", "ARR growth (MoM, annualized)", "%", "pct",
            lambda m, p: f"=IFERROR(({s('arr', m)}/{s('arr', p)})^12-1,0)", style="pct"),
        blank(),
        section("Retention (monthly flows, annualized)"),
        row("nrr", "Net revenue retention", "%", "pct",
            lambda m, p: f"=IFERROR((1+({rv('arr_exp', m)}+{rv('arr_churn', m)})/{rv('arr_bop', m)})^12,0)",
            style="pct"),
        row("grr", "Gross revenue retention", "%", "pct",
            lambda m, p: f"=IFERROR((1+{rv('arr_churn', m)}/{rv('arr_bop', m)})^12,0)", style="pct"),
        blank(),
        section("Efficiency & runway"),
        row("burn", "Net burn (operating + investing outflow)", "$", "currency",
            lambda m, p: f"=-({cf('cfo', m)}+{cf('cfi', m)})"),
        row("burn_mult", "Burn multiple (– when n/m)", "x", "multiple",
            lambda m, p: f"=IF(AND({s('burn', m)}>0,{s('netnew', m)}>0),{s('burn', m)}/{s('netnew', m)},0)"),
        row("runway", "Runway at current burn (– when cash-generative)", "mo", "months",
            lambda m, p: f"=IF({s('burn', m)}>0,{cf('cash_eop', m)}/{s('burn', m)},0)"),
        row("cac_payback", "CAC payback (gross-margin adjusted)", "mo", "months",
            lambda m, p: f"=IFERROR({pl('sm', m)}/({rv('arr_new', m)}*{pl('gm', m)}/12),0)"),
        row("rule40", "Rule of 40 (growth + EBITDA margin)", "%", "pct",
            lambda m, p: f"={s('growth', m)}+{pl('ebitda_m', m)}", style="pct"),
        row("gm", "Gross margin %", "%", "pct", lambda m, p: f"={pl('gm', m)}", style="pct"),
        blank(),
        section("Team"),
        row("hc", "Total headcount", "FTE", "dec1", lambda m, p: f"={ref('Headcount & Opex', 'hc_total', m)}",
            open_=lambda c: f"={ref('Headcount & Opex', 'hc_total', c)}"),
        row("arr_fte", "ARR per FTE", "$", "currency", lambda m, p: f"=IFERROR({s('arr', m)}/{s('hc', m)},0)"),
    ]


SHEETS = [
    ("Revenue", "Customer and ARR bridge, revenue recognition, billings", revenue_layout),
    ("Headcount & Opex", "Hiring plan, payroll and non-headcount spend, allocated to P&L lines", opex_layout),
    ("P&L", "Monthly income statement", pl_layout),
    ("Balance Sheet", "Monthly balance sheet with fixed-asset and debt schedules", bs_layout),
    ("Cash Flow", "Indirect-method cash flow statement", cf_layout),
    ("KPIs", "VC operating metrics: growth, retention, efficiency, runway", kpi_layout),
]


# --------------------------------------------------------------------------------------
# Inputs sheet
# --------------------------------------------------------------------------------------
INPUT_ROW = {}  # name -> row


def build_inputs(wb):
    ws = wb.active
    ws.title = "Inputs"
    ws.sheet_properties.tabColor = "0000FF"
    title_band(ws, "Inputs & Assumptions", "Single source of truth: every hardcoded number in this workbook lives "
                                           "on this sheet. Other sheets reference these cells by name.", 8)
    widths = {"A": 2, "B": 46, "C": 28, "D": 10, "E": 15, "F": 12, "G": 13, "H": 70}
    for k, v in widths.items():
        ws.column_dimensions[k].width = v

    # Legend
    put(ws, "B4", "Legend", bold=True, color=C["accent"])
    put(ws, "C4", 1000, "currency", fill_hex=C["input_fill"], align="right")
    put(ws, "D4", "Input (blue)", italic=True, color=C["muted"])
    put(ws, "E4", "Formula", color=C["formula_font"])
    put(ws, "F4", "Link", color=C["link_font"])
    put(ws, "G4", "Low confidence", fill_hex=C["low_conf_fill"])
    put(ws, "H4", "Edit only blue cells on this sheet. Set Source and Confidence for every input you change.",
        italic=True, color=C["muted"])
    put(ws, "B5", "Illustrative template: figures are placeholders, not Blue Cloud Ventures or portfolio-company "
                  "actuals.", italic=True, color="B42318")

    hdr_row = 7
    for col, h in zip("BCDEFGH", ["Assumption", "Value", "Unit", "Source", "Confidence", "Flag", "Notes / evidence"]):
        put(ws, f"{col}{hdr_row}", h, bold=True, color=C["white"], fill_hex=C["navy"],
            align="left" if col in "BH" else "center")

    dv_src = DataValidation(type="list", formula1=f'"{",".join(SOURCES)}"', allow_blank=False)
    dv_conf = DataValidation(type="decimal", operator="between", formula1="0", formula2="1",
                             error="Confidence must be between 0% and 100%", showErrorMessage=True)
    ws.add_data_validation(dv_src)
    ws.add_data_validation(dv_conf)

    r = hdr_row + 1
    for title, items in INPUTS:
        r += 1
        for col in "BCDEFGH":
            ws[f"{col}{r}"].fill = fill(C["section_fill"])
        put(ws, f"B{r}", title, bold=True, color=C["accent"], fill_hex=C["section_fill"])
        r += 1
        for it in items:
            INPUT_ROW[it["name"]] = r
            put(ws, f"B{r}", it["label"])
            ws[f"B{r}"].alignment = Alignment(indent=1)
            v = put(ws, f"C{r}", it["value"], it["fmt"], fill_hex=C["input_fill"],
                    align="left" if isinstance(it["value"], str) else "right",
                    color=C["input_font"])
            v.border = Border(bottom=thin, top=thin, left=thin, right=thin)
            put(ws, f"D{r}", it["unit"], italic=True, color=C["muted"], align="center")
            put(ws, f"E{r}", it["source"], color=C["input_font"], align="center")
            put(ws, f"F{r}", it["conf"], "conf", color=C["input_font"], align="center")
            put(ws, f"G{r}", f'=IF(F{r}<Conf_Threshold,"▲ REVIEW","✓")', align="center")
            put(ws, f"H{r}", it["note"], italic=True, color=C["muted"])
            dv_src.add(f"E{r}")
            dv_conf.add(f"F{r}")
            if it["dropdown"]:
                dv = DataValidation(type="list", formula1=f'"{",".join(it["dropdown"])}"')
                ws.add_data_validation(dv)
                dv.add(f"C{r}")
            wb.defined_names[it["name"]] = DefinedName(it["name"], attr_text=f"Inputs!$C${r}")
            r += 1
    last = r - 1
    low = fill(C["low_conf_fill"])
    ws.conditional_formatting.add(f"C{hdr_row + 1}:C{last}", FormulaRule(
        formula=[f"AND(ISNUMBER($F{hdr_row + 1}),$F{hdr_row + 1}<Conf_Threshold)"], fill=low))
    ws.conditional_formatting.add(f"G{hdr_row + 1}:G{last}", FormulaRule(
        formula=[f'LEFT($G{hdr_row + 1},1)="▲"'], fill=low, font=Font(name=FONT, bold=True, color="9A6700")))

    # Summary counters next to the legend
    put(ws, "B6", f'=COUNTIF(G{hdr_row + 1}:G{last},"▲*")&" of "&COUNTA(F{hdr_row + 1}:F{last})'
                  f'&" inputs below the confidence threshold"', bold=True, color="9A6700")
    ws.freeze_panes = f"C{hdr_row + 1}"
    return last


# --------------------------------------------------------------------------------------
# Summary (annual dashboard)
# --------------------------------------------------------------------------------------
SUMMARY_ROWS = {}


def build_summary(wb):
    ws = wb.create_sheet("Summary", 1)
    ws.sheet_properties.tabColor = C["navy"]
    title_band(ws, "Portfolio Company Summary", "Annual roll-up for portfolio monitoring and LP reporting", 9)
    for col, w in zip("ABCDEFGHI", [40, 9, 16, 16, 16, 4, 30, 18, 4]):
        ws.column_dimensions[col].width = w

    put(ws, "A3", '=Company_Name&"  ·  "&Sector&"  ·  "&Stage', bold=True, size=12, color=C["navy"])
    put(ws, "A4", '="Model start "&TEXT(Model_Start_Date,"mmm yyyy")&"  ·  Currency: "&Currency',
        italic=True, color=C["muted"])

    # Header row: FY columns
    hr = 6
    put(ws, f"A{hr}", "Annual summary", bold=True, color=C["white"], fill_hex=C["navy"])
    put(ws, f"B{hr}", "", fill_hex=C["navy"])
    put(ws, f"A{hr - 1}", "Model year #", italic=True, color=C["muted"])
    put(ws, f"C{hr - 1}", 1, "int", color=C["muted"], align="right")
    put(ws, f"D{hr - 1}", f"=C{hr - 1}+1", "int", color=C["muted"], align="right")
    put(ws, f"E{hr - 1}", f"=D{hr - 1}+1", "int", color=C["muted"], align="right")
    ys = {"C": f"C${hr - 1}", "D": f"D${hr - 1}", "E": f"E${hr - 1}"}
    for col in "CDE":
        put(ws, f"{col}{hr}", f'="FY"&YEAR(EOMONTH(Model_Start_Date,12*{ys[col]}-1))', bold=True,
            color=C["white"], fill_hex=C["navy"], align="right")

    def rng(sheet, key):
        return f"{q(sheet)}!$D${R(sheet, key)}:${LAST}${R(sheet, key)}"

    def yr(sheet):
        return f"{q(sheet)}!$D$6:${LAST}$6"

    def flow(sheet, key):
        return lambda c: f"=SUMIFS({rng(sheet, key)},{yr(sheet)},{ys[c]})"

    def bal(sheet, key):
        return lambda c: f"=INDEX({rng(sheet, key)},12*{ys[c]})"

    def prior_arr(c):
        return f"IF({ys[c]}=1,Open_ARR,INDEX({rng('Revenue', 'arr_eop')},12*({ys[c]}-1)))"

    S = lambda k, c: f"{c}{SUMMARY_ROWS[k]}"
    lines = [
        ("arr", "Ending ARR", "currency", bal("Revenue", "arr_eop"), "total"),
        ("arr_g", "ARR growth (YoY)", "pct", lambda c: f"=IFERROR({S('arr', c)}/{prior_arr(c)}-1,0)", "pct"),
        ("nrr", "Net revenue retention", "pct",
         lambda c: f"=IFERROR(1+(SUMIFS({rng('Revenue', 'arr_exp')},{yr('Revenue')},{ys[c]})"
                   f"+SUMIFS({rng('Revenue', 'arr_churn')},{yr('Revenue')},{ys[c]}))/{prior_arr(c)},0)", "pct"),
        ("cust", "Ending customers", "dec1", bal("Revenue", "cust_eop"), None),
        (None,),
        ("rev", "Total revenue", "currency", flow("P&L", "rev"), "total"),
        ("gp", "Gross profit", "currency", flow("P&L", "gp"), None),
        ("gm", "Gross margin %", "pct", lambda c: f"=IFERROR({S('gp', c)}/{S('rev', c)},0)", "pct"),
        ("ebitda", "EBITDA", "currency", flow("P&L", "ebitda"), None),
        ("ebitda_m", "EBITDA margin %", "pct", lambda c: f"=IFERROR({S('ebitda', c)}/{S('rev', c)},0)", "pct"),
        ("int_exp", "Interest expense", "currency", flow("P&L", "int_exp"), None),
        ("ni", "Net income", "currency", flow("P&L", "ni"), "total"),
        (None,),
        ("burn", "Net burn", "currency", flow("KPIs", "burn"), None),
        ("burn_mult", "Burn multiple", "multiple",
         lambda c: f"=IF(AND({S('burn', c)}>0,{S('arr', c)}>{prior_arr(c)}),"
                   f"{S('burn', c)}/({S('arr', c)}-{prior_arr(c)}),0)", None),
        ("rule40", "Rule of 40", "pct", lambda c: f"={S('arr_g', c)}+{S('ebitda_m', c)}", "pct"),
        ("cash", "Ending cash", "currency", bal("Cash Flow", "cash_eop"), "total"),
        ("hc", "Ending headcount", "dec1", bal("Headcount & Opex", "hc_total"), None),
        ("arr_fte", "ARR per FTE", "currency", lambda c: f"=IFERROR({S('arr', c)}/{S('hc', c)},0)", None),
    ]
    r = hr + 1
    for ln in lines:
        if ln[0] is None:
            r += 1
            continue
        SUMMARY_ROWS[ln[0]] = r
        r += 1
    for ln in lines:
        if ln[0] is None:
            continue
        key, label, fmt, fn, st = ln
        rr = SUMMARY_ROWS[key]
        put(ws, f"A{rr}", label, bold=st == "total", italic=st == "pct")
        for c in "CDE":
            cell = put(ws, f"{c}{rr}", fn(c), fmt, bold=st == "total", italic=st == "pct", align="right")
            if st == "total":
                cell.border = Border(top=thin)
    end = r

    # Valuation & ownership panel
    put(ws, "G6", "Last-round mark", bold=True, color=C["white"], fill_hex=C["navy"])
    put(ws, "H6", "", fill_hex=C["navy"])
    panel = [
        ("Post-money valuation", "=Last_Round_Post_Money", "currency"),
        ("BCV ownership", "=BCV_Ownership", "pct"),
        ("BCV stake at last mark", "=Last_Round_Post_Money*BCV_Ownership", "currency"),
        ("Post-money / opening ARR", "=IFERROR(Last_Round_Post_Money/Open_ARR,0)", "multiple"),
        ("Post-money / FY1 ending ARR", f"=IFERROR(Last_Round_Post_Money/C{SUMMARY_ROWS['arr']},0)", "multiple"),
    ]
    for i, (lbl, f, fmt) in enumerate(panel):
        put(ws, f"G{7 + i}", lbl)
        put(ws, f"H{7 + i}", f, fmt, align="right")

    put(ws, "G13", "Model health", bold=True, color=C["white"], fill_hex=C["navy"])
    put(ws, "H13", "", fill_hex=C["navy"])
    SUMMARY_ROWS["_health"] = 14

    # Charts
    for sheet, key, title, kind, anchor in (
            ("Revenue", "arr_eop", "Ending ARR ($)", "line", f"A{end + 2}"),
            ("Cash Flow", "cash_eop", "Ending cash ($)", "bar", f"E{end + 2}")):
        src = wb[sheet]
        data = Reference(src, min_col=4, max_col=3 + N_MONTHS, min_row=R(sheet, key), max_row=R(sheet, key))
        cats = Reference(src, min_col=4, max_col=3 + N_MONTHS, min_row=5, max_row=5)
        ch = LineChart() if kind == "line" else BarChart()
        ch.add_data(data, from_rows=True, titles_from_data=False)
        ch.set_categories(cats)
        ch.title = title
        ch.legend = None
        ch.height, ch.width = 7.5, 15
        ch.y_axis.numFmt = '$#,##0,,"M"'
        ch.x_axis.number_format = NF["date"]
        ch.y_axis.majorGridlines = None
        ch.x_axis.delete = False
        ch.y_axis.delete = False
        s0 = ch.series[0]
        s0.graphicalProperties.line.solidFill = C["accent"]
        if kind == "bar":
            s0.graphicalProperties.solidFill = C["accent"]
        else:
            s0.graphicalProperties.line.width = 28000
        ws.add_chart(ch, anchor)
    ws.freeze_panes = "A7"
    return ws


# --------------------------------------------------------------------------------------
# Checks & Flags (last sheet)
# --------------------------------------------------------------------------------------
def build_checks(wb, inputs_last):
    ws = wb.create_sheet("Checks & Flags")
    ws.sheet_properties.tabColor = "C2410C"
    title_band(ws, "Checks & Flags", "Integrity checks, weakest-link calculation confidence, and the agent's "
                                     "review log. Resolve every FAIL and open flag before sharing.", 11)
    for col, w in zip("ABCDEFGHIJK", [6, 40, 22, 16, 50, 13, 14, 52, 40, 12, 12]):
        ws.column_dimensions[col].width = w

    pass_f, fail_f, alert_f, low_f = (fill(C[k]) for k in ("pass_fill", "fail_fill", "alert_fill", "low_conf_fill"))

    def header(r, cols):
        for i, h in enumerate(cols):
            col = get_column_letter(i + 1)
            put(ws, f"{col}{r}", h, bold=True, color=C["white"], fill_hex=C["navy"],
                align="center" if i else "left")

    def sec(r, title):
        for col in range(1, 12):
            ws.cell(row=r, column=col).fill = fill(C["section_fill"])
        put(ws, f"A{r}", title, bold=True, color=C["accent"], fill_hex=C["section_fill"])

    def span(sheet, key, cols=f"D:{LAST}"):
        a, b = cols.split(":")
        return f"{q(sheet)}!{a}{R(sheet, key)}:{b}{R(sheet, key)}"

    # ---- A. Integrity checks
    sec(8, "A. Integrity checks (live)")
    header(9, ["#", "Check", "Sheet", "Abs. difference", "Method", "Tolerance", "Status"])
    open_bs = ("ABS((Open_Cash+Open_AR+Open_Prepaid+Open_PPE)-(Open_AP+Open_Accrued+Open_Deferred_Rev+Open_Debt"
               "+Open_Paid_In_Capital+Open_Retained_Earnings))")
    err_count = "+".join(f"SUMPRODUCT(--ISERROR({q(s)}!A1:{LAST}80))" for s, _, _ in SHEETS) + \
                "+SUMPRODUCT(--ISERROR(Summary!A1:E40))"
    checks = [
        ("Opening balance sheet balances", "Inputs", f"={open_bs}", "Opening assets vs. liabilities + equity"),
        ("Balance sheet balances, every month", "Balance Sheet",
         f"=SUMPRODUCT(ABS({span('Balance Sheet', 'check', f'C:{LAST}')}))", "Σ|assets − L&E| across all periods"),
        ("Cash: balance sheet = cash flow", "Cash Flow",
         f"=SUMPRODUCT(ABS({span('Balance Sheet', 'cash')}-{span('Cash Flow', 'cash_eop')}))",
         "Σ|BS cash − CF ending cash|"),
        ("Net income: P&L = cash flow", "P&L",
         f"=SUMPRODUCT(ABS({span('P&L', 'ni')}-{span('Cash Flow', 'ni')}))", "Σ|P&L NI − CF NI|"),
        ("Revenue: P&L = revenue build", "Revenue",
         f"=SUMPRODUCT(ABS({span('P&L', 'rev')}-{span('Revenue', 'rev_total')}))", "Σ|P&L revenue − build|"),
        ("Every cost allocated to a P&L line", "Headcount & Opex",
         f"=SUMPRODUCT(ABS({span('Headcount & Opex', 'alloc_total')}-{span('Headcount & Opex', 'pay_total')}"
         f"-{span('Headcount & Opex', 'commissions')}-{span('Headcount & Opex', 'nonhc_total')}))",
         "Σ|allocated − (payroll + commissions + non-HC)|"),
        ("ARR bridge rolls forward", "Revenue",
         f"=SUMPRODUCT(ABS({span('Revenue', 'arr_eop')}-{span('Revenue', 'arr_bop')}-{span('Revenue', 'arr_new')}"
         f"-{span('Revenue', 'arr_exp')}-{span('Revenue', 'arr_churn')}))", "Σ|EoP − (BoP + new + exp − churn)|"),
        ("Customers never negative", "Revenue", f"=-MIN(0,MIN({span('Revenue', 'cust_eop')}))",
         "Most negative customer count"),
        ("Deferred revenue never negative", "Balance Sheet", f"=-MIN(0,MIN({span('Balance Sheet', 'dr')}))",
         "Most negative deferred revenue"),
        ("No formula errors in model", "All", f"={err_count}", "Cells returning an Excel error (Summary health panel excluded)"),
    ]
    r = 10
    first_check = r
    for i, (name, sheet, f, method) in enumerate(checks, 1):
        put(ws, f"A{r}", f"C-{i:02d}", align="center", color=C["muted"])
        put(ws, f"B{r}", name)
        put(ws, f"C{r}", sheet, color=C["muted"], align="center")
        put(ws, f"D{r}", f, "currency", align="right")
        put(ws, f"E{r}", method, italic=True, color=C["muted"])
        put(ws, f"F{r}", "=Check_Tolerance", "currency", align="right")
        put(ws, f"G{r}", f'=IF(D{r}<=F{r},"PASS","FAIL")', bold=True, align="center")
        r += 1
    last_check = r - 1
    # Business alerts (not integrity failures)
    alerts = [
        ("Minimum runway ≥ threshold", "KPIs",
         f"=_xlfn.MINIFS({span('KPIs', 'runway')},{span('KPIs', 'runway')},\">0\")",
         "Lowest runway in forecast (months)", "=Min_Runway_Months",
         f'=IF(AND(D{{r}}>0,D{{r}}<F{{r}}),"ALERT","OK")', "months"),
        ("Cash never negative", "Cash Flow", f"=MIN({span('Cash Flow', 'cash_eop')})", "Lowest ending cash", 0,
         '=IF(D{r}<F{r},"ALERT","OK")', "currency"),
    ]
    for i, (name, sheet, f, method, tol, status, fmt) in enumerate(alerts, len(checks) + 1):
        put(ws, f"A{r}", f"C-{i:02d}", align="center", color=C["muted"])
        put(ws, f"B{r}", name)
        put(ws, f"C{r}", sheet, color=C["muted"], align="center")
        put(ws, f"D{r}", f, fmt, align="right")
        put(ws, f"E{r}", method, italic=True, color=C["muted"])
        put(ws, f"F{r}", tol, fmt, align="right")
        put(ws, f"G{r}", status.format(r=r), bold=True, align="center")
        r += 1
    last_alert = r - 1
    g = f"G{first_check}:G{last_alert}"
    ws.conditional_formatting.add(g, CellIsRule(operator="equal", formula=['"PASS"'], fill=pass_f))
    ws.conditional_formatting.add(g, CellIsRule(operator="equal", formula=['"OK"'], fill=pass_f))
    ws.conditional_formatting.add(g, CellIsRule(operator="equal", formula=['"FAIL"'], fill=fail_f))
    ws.conditional_formatting.add(g, CellIsRule(operator="equal", formula=['"ALERT"'], fill=alert_f))

    # ---- B. Calculation confidence
    r += 1
    sec(r, "B. Calculation confidence: weakest-link rule (live; a calculation is only as reliable as its "
           "least-confident input)")
    r += 1
    header(r, ["#", "Calculation", "Go to", "Value", "Driving inputs", "Confidence", "Status", "Weakest input"])
    r += 1
    first_calc = r
    for i, (name, sheet, key, col, fmt, deps) in enumerate(KEY_CALCS, 1):
        row_ = SUMMARY_ROWS[key] if sheet == "Summary" else R(sheet, key)
        loc = f"{q(sheet)}!{col}{row_}"
        deps = list(dict.fromkeys(deps))
        confs = ",".join(f"Inputs!$F${INPUT_ROW[d]}" for d in deps)
        weakest = '""'
        for d in reversed(deps):
            weakest = f"IF(F{r}=Inputs!$F${INPUT_ROW[d]},Inputs!$B${INPUT_ROW[d]},{weakest})"
        put(ws, f"A{r}", f"K-{i:02d}", align="center", color=C["muted"])
        put(ws, f"B{r}", name)
        link = put(ws, f"C{r}", f"{sheet}!{col}{row_} →", color=C["accent"], align="center")
        link.hyperlink = Hyperlink(ref=f"C{r}", location=loc)
        link.font = font(color=C["accent"], underline="single")
        put(ws, f"D{r}", f"={loc}", fmt, align="right")
        put(ws, f"E{r}", ", ".join(deps), italic=True, color=C["muted"], align="wrap")
        put(ws, f"F{r}", f"=MIN({confs})", "conf", align="center")
        put(ws, f"G{r}", f'=IF(F{r}<Conf_Threshold,"▲ LOW","OK")', bold=True, align="center")
        put(ws, f"H{r}", f"={weakest}", italic=True)
        ws.row_dimensions[r].height = 30
        r += 1
    last_calc = r - 1
    g = f"G{first_calc}:G{last_calc}"
    ws.conditional_formatting.add(g, CellIsRule(operator="equal", formula=['"OK"'], fill=pass_f))
    ws.conditional_formatting.add(g, CellIsRule(operator="equal", formula=['"▲ LOW"'], fill=low_f))

    # ---- C. Agent review log
    r += 1
    sec(r, "C. Agent review log: low-confidence items raised by the agent (one row per flag; update Status as "
           "they are resolved)")
    r += 1
    header(r, ["ID", "Item", "Go to", "Value", "Reason", "Confidence", "Raised by", "Suggested action", "Owner",
               "Status"])
    r += 1
    first_log = r
    log = []
    threshold = next(it["value"] for _, items in INPUTS for it in items if it["name"] == "Conf_Threshold")
    for title, items in INPUTS:
        for it in items:
            if isinstance(it["value"], (int, float)) and it["conf"] < threshold:
                log.append(dict(item=f"Input: {it['label']}", loc=f"Inputs!C{INPUT_ROW[it['name']]}",
                                fmt=it["fmt"], conf=it["conf"], reason=f"{it['source']}. {it['note']}".strip(),
                                action="Confirm with company or replace with data-derived value"))
    for fl in AGENT_FLAGS:
        log.append(dict(item=f"Calc: {fl['item']}", loc=f"{q(fl['sheet'])}!{fl['col']}{R(fl['sheet'], fl['key'])}",
                        fmt="months" if fl["key"] == "cac_payback" else "currency", conf=fl["conf"],
                        reason=fl["reason"], action=fl["action"]))
    dv_status = DataValidation(type="list", formula1='"Open,In review,Resolved"')
    ws.add_data_validation(dv_status)
    for i, f in enumerate(log, 1):
        put(ws, f"A{r}", f"F-{i:02d}", align="center", color=C["muted"])
        put(ws, f"B{r}", f["item"], align="wrap")
        link = put(ws, f"C{r}", f"{f['loc'].replace(chr(39), '')} →", align="center")
        link.hyperlink = Hyperlink(ref=f"C{r}", location=f["loc"])
        link.font = font(color=C["accent"], underline="single")
        put(ws, f"D{r}", f"={f['loc']}", f["fmt"], align="right")
        put(ws, f"E{r}", f["reason"], italic=True, color=C["muted"], align="wrap")
        put(ws, f"F{r}", f["conf"], "conf", align="center", color=C["input_font"])
        put(ws, f"G{r}", "Agent", align="center", color=C["muted"])
        put(ws, f"H{r}", f["action"], align="wrap")
        put(ws, f"I{r}", "Deal team", color=C["input_font"])
        put(ws, f"J{r}", "Open", align="center", color=C["input_font"])
        dv_status.add(f"J{r}")
        ws.row_dimensions[r].height = 30
        r += 1
    last_log = r - 1
    ws.conditional_formatting.add(f"F{first_log}:F{last_log}", FormulaRule(
        formula=[f"F{first_log}<Conf_Threshold"], fill=low_f))
    ws.conditional_formatting.add(f"J{first_log}:J{last_log}", CellIsRule(
        operator="equal", formula=['"Resolved"'], fill=pass_f))
    ws.conditional_formatting.add(f"J{first_log}:J{last_log}", CellIsRule(
        operator="equal", formula=['"Open"'], fill=alert_f))

    # ---- Scoreboard at top
    score = [
        ("Integrity checks failed", f'=COUNTIF(G{first_check}:G{last_check},"FAIL")'),
        ("Business alerts", f'=COUNTIF(G{last_check + 1}:G{last_alert},"ALERT")'),
        ("Low-confidence calculations", f'=COUNTIF(G{first_calc}:G{last_calc},"▲*")'),
        ("Open agent flags", f'=COUNTIF(J{first_log}:J{last_log},"<>Resolved")'),
        ("Inputs below threshold", f'=COUNTIF(Inputs!G1:G{inputs_last},"▲*")'),
    ]
    for i, (lbl, f) in enumerate(score):
        col = get_column_letter(2 + i) if i < 1 else get_column_letter(2 + i)
        put(ws, f"{col}4", lbl, italic=True, color=C["muted"], align="center")
        put(ws, f"{col}5", f, "int", bold=True, size=16, align="center")
    ws["B4"].alignment = Alignment(horizontal="left")
    ws["B5"].alignment = Alignment(horizontal="left")
    put(ws, "B6", '=IF(B5>0,"✗ MODEL HAS INTEGRITY FAILURES",IF(D5+E5>0,"▲ REVIEW REQUIRED BEFORE SHARING",'
                  '"✓ CLEAN"))', bold=True, size=12)
    ws.conditional_formatting.add("B6", FormulaRule(formula=['LEFT(B6,1)="✗"'], fill=fail_f))
    ws.conditional_formatting.add("B6", FormulaRule(formula=['LEFT(B6,1)="▲"'], fill=low_f))
    ws.conditional_formatting.add("B6", FormulaRule(formula=['LEFT(B6,1)="✓"'], fill=pass_f))
    ws.freeze_panes = "A7"

    # Feed model health to the Summary sheet
    sm = wb["Summary"]
    h = SUMMARY_ROWS["_health"]
    for i, (lbl, col) in enumerate((("Integrity checks failed", "B"), ("Low-confidence calculations", "D"),
                                    ("Open agent flags", "E"))):
        put(sm, f"G{h + i}", lbl)
        put(sm, f"H{h + i}", f"='Checks & Flags'!{col}5", "int", align="right")
    put(sm, f"G{h + 3}", "='Checks & Flags'!B6", bold=True)


# --------------------------------------------------------------------------------------
def fit_width(ws):
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True


def build(path):
    wb = Workbook()
    inputs_last = build_inputs(wb)
    layouts = [(name, sub, fn()) for name, sub, fn in SHEETS]
    for name, _, lay in layouts:
        assign_rows(name, lay)
    for name, sub, lay in layouts:
        ws = wb.create_sheet(name)
        ws.sheet_properties.tabColor = "8A9BB4"
        write_monthly(ws, name, sub, lay)
    build_summary(wb)
    build_checks(wb, inputs_last)
    for name in ("Inputs", "Summary", "Checks & Flags"):
        fit_width(wb[name])
    wb.calculation.fullCalcOnLoad = True
    wb.save(path)
    return path


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "Portfolio_Company_Model.xlsx"
    print(build(out))
