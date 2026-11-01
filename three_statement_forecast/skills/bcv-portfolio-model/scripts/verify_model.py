"""Compute twice: rebuild the model in plain Python from the Inputs sheet and compare it
with the values Excel/LibreOffice computed from the formulas. A mismatch means a formula
is wrong (or this reference model is), and either way a human should look.

Usage: python verify_model.py model.xlsx   (run after recalc; exit code 1 on mismatch)
"""
import json
import sys

from openpyxl import load_workbook

TOL = 1.0  # dollars
DEPTS = ["RD", "SM", "GA", "CS"]


def inputs(wb):
    out = {}
    for name, dn in wb.defined_names.items():
        for sheet, coord in dn.destinations:
            out[name] = wb[sheet][coord.replace("$", "")].value
    return out


def reference_model(x, months=36):
    cust, arr, dr = x["Open_Customers"], x["Open_ARR"], x["Open_Deferred_Rev"]
    hc = {d: x[f"Open_HC_{d}"] for d in DEPTS}
    cash, ar, prepaid, ppe = x["Open_Cash"], x["Open_AR"], x["Open_Prepaid"], x["Open_PPE"]
    ap, accrued, debt = x["Open_AP"], x["Open_Accrued"], x["Open_Debt"]
    pic, re = x["Open_Paid_In_Capital"], x["Open_Retained_Earnings"]
    out = {k: [] for k in ("arr", "revenue", "ebitda", "net_income", "cash", "total_assets", "tle")}
    for m in range(1, months + 1):
        y = (m - 1) // 12 + 1
        new = x[f"New_Logos_Y{y}"]
        acv = x["New_ACV"] * (1 + x["Price_Increase_Annual"]) ** (y - 1)
        arr_new, arr_exp, arr_churn = new * acv, arr * x["Expansion_Monthly"], arr * x["Logo_Churn_Monthly"]
        cust = cust + new - cust * x["Logo_Churn_Monthly"]
        arr_bop, arr = arr, arr + arr_new + arr_exp - arr_churn
        sub = (arr_bop + arr) / 2 / 12
        svc = sub * x["Services_Pct_Sub"]
        rev = sub + svc
        dr_bop, dr = dr, arr * x["Annual_Prepay_Mix"] * x["Avg_Deferred_Months"] / 12
        billings = rev + dr - dr_bop

        hires = {d: x[f"Hires_{d}_Y{y}"] for d in DEPTS}
        hc = {d: hc[d] + hires[d] for d in DEPTS}
        pay = {d: hc[d] * x[f"Cost_FTE_{d}"] * (1 + x["Comp_Inflation"]) ** (y - 1) / 12 for d in DEPTS}
        hc_total = sum(hc.values())
        commissions = x["Commission_Rate"] * (arr_new + arr_exp)
        hosting = x["Hosting_Pct"] * sub
        svc_cost = svc * (1 - x["Services_Margin"])
        marketing = x["Marketing_Programs_Monthly"] * (1 + x["Marketing_Growth_Annual"]) ** (y - 1)
        software = hc_total * x["Software_Per_FTE_Monthly"]
        rent = hc_total * x["Rent_Per_FTE_Monthly"]
        other_ga = x["Other_GA_Monthly"] * (1 + x["Opex_Inflation"]) ** (y - 1)
        nonhc = hosting + svc_cost + marketing + software + rent + other_ga

        ebitda = rev - sum(pay.values()) - commissions - nonhc
        da = ppe / x["Depr_Life_Months"]
        capex = sum(hires.values()) * x["Capex_Per_New_Hire"] + x["Capex_Maintenance_Monthly"]
        int_inc = cash * x["Interest_Income_Rate"] / 12
        int_exp = debt * x["Debt_Rate"] / 12
        ebt = ebitda - da + int_inc - int_exp
        ni = ebt - max(0, ebt) * x["Tax_Rate"]

        ar_new = billings * 12 * x["DSO_Days"] / 365
        prepaid_new = nonhc * x["Prepaid_Months"]
        ap_new = nonhc * 12 * x["DPO_Days"] / 365
        accrued_new = sum(pay.values()) * x["Accrued_Pct_Payroll"]
        equity = x["Equity_Raise_Amount"] if m == x["Equity_Raise_Month"] else 0
        draw = x["Debt_Draw_Amount"] if m == x["Debt_Draw_Month"] else 0
        cfo = ni + da - (ar_new - ar) - (prepaid_new - prepaid) + (ap_new - ap) + (accrued_new - accrued) + (dr - dr_bop)
        cash = cash + cfo - capex + equity + draw
        ar, prepaid, ap, accrued = ar_new, prepaid_new, ap_new, accrued_new
        ppe, debt, pic, re = ppe + capex - da, debt + draw, pic + equity, re + ni

        for k, val in (("arr", arr), ("revenue", rev), ("ebitda", ebitda), ("net_income", ni), ("cash", cash),
                       ("total_assets", cash + ar + prepaid + ppe), ("tle", ap + accrued + dr + debt + pic + re)):
            out[k].append(val)
    return out


def model_row(ws, label, months=36):
    for r in range(1, ws.max_row + 1):
        if ws[f"A{r}"].value == label:
            return [ws.cell(r, 4 + i).value for i in range(months)]
    raise KeyError(f"{ws.title}: row '{label}' not found")


def verify(path):
    f = load_workbook(path)
    v = load_workbook(path, data_only=True)
    ref = reference_model(inputs(f))
    pairs = {
        "arr": ("Revenue", "Ending ARR"), "revenue": ("P&L", "Total revenue"), "ebitda": ("P&L", "EBITDA"),
        "net_income": ("P&L", "Net income"), "cash": ("Cash Flow", "Ending cash"),
        "total_assets": ("Balance Sheet", "Total assets"), "tle": ("Balance Sheet", "Total liabilities & equity"),
    }
    report = {}
    for key, (sheet, label) in pairs.items():
        got = model_row(v[sheet], label)
        diffs = [abs((g or 0) - r) for g, r in zip(got, ref[key])]
        worst = max(range(len(diffs)), key=diffs.__getitem__)
        report[key] = {"max_abs_diff": round(diffs[worst], 6), "at_month": worst + 1,
                       "status": "match" if diffs[worst] <= TOL else "MISMATCH"}
    ok = all(x["status"] == "match" for x in report.values())
    return {"status": "pass" if ok else "fail", "tolerance": TOL, "lines": report}


if __name__ == "__main__":
    out = verify(sys.argv[1])
    print(json.dumps(out, indent=2))
    sys.exit(0 if out["status"] == "pass" else 1)
