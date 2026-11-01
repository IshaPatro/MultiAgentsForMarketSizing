"""Deterministic checks for a generated portfolio-monitoring workbook.

Ground truth comes from scripts/portfolio_truth.py; formatting rules from
standards/monitoring_workbook_spec.md. Every check returns {check, status, detail}.

Usage: python eval/checks.py <workbook.xlsx>
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from portfolio_truth import COMPANIES, POLICY, PRIOR, Q3, Q3_LATE  # noqa: E402
from result_records import R, R_HALYARD  # noqa: E402

REQUIRED_SHEETS = ["Dashboard", "Company Metrics", "Flags", "Inputs & Review"]
INPUT_SHEET = "Inputs & Review"
ERRORS = ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NUM!", "#NULL!")
UNIT_TOKENS = re.compile(r"\bUSD\b|\(\$|\$000|\bUnit\b|\bMethod\b|Source file|As reported", re.I)
MONEY_COLS = ("ARR", "Prior ARR", "Plan ARR", "Net new ARR", "Revenue", "Net burn", "Cash", "Look-through ARR")
PCT_COLS = ("QoQ growth", "vs Plan", "Gross margin", "NRR")
SOFFICE = ["soffice", "libreoffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice"]


# --------------------------------------------------------------------------------------
# Expected values (fund definitions + RAG policy applied to the truth)
# --------------------------------------------------------------------------------------
def expected(late=False):
    """Expected per-company metrics; late=True includes Halyard's late report."""
    pol = {k: v[0] for k, v in POLICY.items()}
    truth, recs = (Q3_LATE, {**R, "halyard": R_HALYARD}) if late else (Q3, R)
    out = {}
    for c in COMPANIES:
        q = truth.get(c["key"])
        if q is None:
            out[c["name"]] = {"Status": "No report"}
            continue
        prior = PRIOR[c["key"]]["arr"]
        net_new = q["arr"] - prior
        runway = q["cash"] / q["burn"]
        bm = 3 * q["burn"] / net_new if net_new > 0 else None
        vs_plan = q["arr"] / c["plan_arr"] - 1
        red = runway < pol["Runway_Red"] or vs_plan < pol["Plan_Red"] or bm is None or bm > pol["BM_Red"]
        amber = (runway < pol["Runway_Amber"] or vs_plan < pol["Plan_Amber"] or (bm or 0) > pol["BM_Amber"]
                 or (q["nrr"] is not None and q["nrr"] < pol["NRR_Amber"])
                 or min(recs[c["key"]][m][3] for m in ("arr", "burn", "cash")) < pol["Conf_Amber"])
        out[c["name"]] = {"ARR": q["arr"], "Net burn": q["burn"], "Cash": q["cash"], "QoQ growth": q["arr"] / prior - 1,
                          "vs Plan": vs_plan, "Runway": runway, "Burn multiple": bm,
                          "Status": "Red" if red else "Amber" if amber else "Green"}
    return out


# --------------------------------------------------------------------------------------
def recalc(path, out_dir):
    exe = next((shutil.which(s) or s for s in SOFFICE if shutil.which(s) or Path(s).exists()), None)
    if not exe:
        return None
    subprocess.run([exe, "--headless", "--calc", "--convert-to", "xlsx:Calc MS Excel 2007 XML", "--outdir",
                    str(out_dir), str(path)], capture_output=True, timeout=300)
    p = Path(out_dir) / Path(path).name
    return p if p.exists() else None


def header_map(ws, must="Company", max_row=15):
    for r in range(1, max_row + 1):
        names = {str(c.value).strip(): c.column for c in ws[r] if isinstance(c.value, str)}
        if must in names:
            return r, names
    return None, {}


def close(got, want, name):
    if want is None:
        return got in (None, "", "n/m")
    if not isinstance(got, (int, float)):
        return False
    tol = 0.01 if name in ("QoQ growth", "vs Plan") else 0.015 * abs(want)
    return abs(got - want) <= max(tol, 1e-9)


def result(check, ok, detail=""):
    return {"check": check, "status": "PASS" if ok else "FAIL", "detail": detail}


def run_checks(path, late=False):
    path = Path(path)
    out = []
    wb = load_workbook(path)
    out.append(result("Required sheets present", all(s in wb.sheetnames for s in REQUIRED_SHEETS),
                      f"found {wb.sheetnames}"))

    with tempfile.TemporaryDirectory() as tmp:
        calc = recalc(path, tmp)
        if calc is None:
            out.append({"check": "Recalculation", "status": "SKIP", "detail": "LibreOffice not found"})
            return out
        vals = load_workbook(calc, data_only=True)

        # #N/A from an explicit NA() is deliberate (a gap in a chart series), not an error
        errs = [f"{ws.title}!{c.coordinate}" for ws in vals.worksheets for row in ws.iter_rows() for c in row
                if isinstance(c.value, str) and c.value in ERRORS
                and not (c.value == "#N/A" and "NA()" in str(wb[ws.title][c.coordinate].value).upper())]
        out.append(result("No formula errors", not errs, ", ".join(errs[:10])))

        # Values vs truth on Company Metrics
        if "Company Metrics" in vals.sheetnames:
            ws = vals["Company Metrics"]
            hr, cols = header_map(ws)
            rows = {ws.cell(r, cols["Company"]).value: r for r in range(hr + 1, ws.max_row + 1)
                    if ws.cell(r, cols["Company"]).value} if hr else {}
            misses, status_misses = [], []
            for name, exp in expected(late).items():
                r = rows.get(name)
                if r is None:
                    misses.append(f"{name}: row missing")
                    continue
                for metric, want in exp.items():
                    if metric not in cols:
                        misses.append(f"column '{metric}' missing")
                        continue
                    got = ws.cell(r, cols[metric]).value
                    if metric == "Status":
                        if str(got).strip() != want:
                            status_misses.append(f"{name}: {got} ≠ {want}")
                    elif not close(got, want, metric):
                        misses.append(f"{name} {metric}: {got!r} ≠ {want:,.4g}" if want is not None else
                                      f"{name} {metric}: {got!r} ≠ n/m")
            out.append(result("Metric values match truth (ARR, burn, cash, growth, plan, runway, burn multiple)",
                              not misses, "; ".join(sorted(set(misses))[:12])))
            out.append(result("RAG status matches policy", not status_misses, "; ".join(status_misses)))

        # Dashboard tile sanity: portfolio ARR present somewhere on Dashboard
        if "Dashboard" in vals.sheetnames:
            total = sum(q["arr"] for q in (Q3_LATE if late else Q3).values())
            found = any(isinstance(c.value, (int, float)) and abs(c.value - total) <= 0.01 * total
                        for row in vals["Dashboard"].iter_rows(max_row=12) for c in row)
            out.append(result("Dashboard shows portfolio ARR", found, f"expected ≈ {total:,.0f}"))

    # Formatting and structure (formula workbook, not recalculated copy)
    hard, units, money_bad, pct_bad = [], [], [], []
    for ws in wb.worksheets:
        if ws.title == INPUT_SHEET or ws.sheet_state != "visible":
            continue
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if isinstance(v, (int, float)) and not isinstance(v, bool) and v not in (0, 1):
                    hard.append(f"{ws.title}!{c.coordinate}={v}")
                if (isinstance(v, str) and len(v) <= 40 and not v.startswith("=") and not re.search(r"\d", v)
                        and UNIT_TOKENS.search(v)):  # header-like text, not prose findings
                    units.append(f"{ws.title}!{c.coordinate} '{v[:30]}'")
    if "Company Metrics" in wb.sheetnames:
        ws = wb["Company Metrics"]
        hr, cols = header_map(ws)
        for name in MONEY_COLS:
            if hr and name in cols:
                fmt = ws.cell(hr + 1, cols[name]).number_format
                if not re.search(r'[KMBT]"|\\[KMBT]', fmt) and not has_scaled_cf(ws, cols[name]):  # "M" or \M
                    money_bad.append(f"{name}: {fmt}")
        for name in PCT_COLS:
            if hr and name in cols and "%" not in ws.cell(hr + 1, cols[name]).number_format:
                pct_bad.append(f"{name}: {ws.cell(hr + 1, cols[name]).number_format}")
    out.append(result("Hardcoded numbers only on Inputs & Review", not hard, ", ".join(hard[:10])))
    out.append(result("Units / methods only on Inputs & Review", not units, ", ".join(units[:8])))
    out.append(result("Money shown as $K/$M/$B/$T", not money_bad, ", ".join(money_bad)))
    out.append(result("Percentages formatted as %", not pct_bad, ", ".join(pct_bad)))

    with zipfile.ZipFile(path) as z:
        n_charts = len([n for n in z.namelist() if re.match(r"xl/charts/chart\d+\.xml", n)])
    out.append(result("Dashboard has ≥ 4 charts", n_charts >= 4, f"{n_charts} charts"))
    fonts = {c.font.name for ws in wb.worksheets if ws.sheet_state == "visible" for row in ws.iter_rows()
             for c in row if c.value is not None}
    out.append(result("Font is Arial", fonts <= {"Arial"}, f"fonts used: {sorted(f for f in fonts if f)}"))
    return out


def has_scaled_cf(ws, col):
    for rng in ws.conditional_formatting:
        if any(c[0] <= col <= c[2] for c in [(b.min_col, 0, b.max_col) for b in rng.sqref.ranges]):
            for rule in rng.rules:
                code = getattr(getattr(getattr(rule, "dxf", None), "numFmt", None), "formatCode", "") or ""
                if re.search(r'[KMBT]"|\\[KMBT]', code):
                    return True
    return False


if __name__ == "__main__":
    res = run_checks(sys.argv[1], late="--late" in sys.argv)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    sys.exit(0 if all(r["status"] != "FAIL" for r in res) else 1)
