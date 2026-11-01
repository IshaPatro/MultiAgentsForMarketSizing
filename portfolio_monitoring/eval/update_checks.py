"""Before/after checks for merging a late company report into an existing monitoring workbook.

Verifies the update is additive and surgical: the late company's data arrives and its status updates,
while every other company's inputs, reviewer ticks, flags, formulas, tables, named ranges and charts are
unchanged (rules in standards/update_rules.md).

Usage: python eval/update_checks.py <before.xlsx> <after.xlsx> ["Company Name"]
"""
import json
import re
import sys
import tempfile
import warnings
from pathlib import Path

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chart_checks import inspect  # noqa: E402
from checks import recalc  # noqa: E402

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
INPUTS, METRICS, FLAGS = "Inputs & Review", "Company Metrics", "Flags"
KPI_KEYS = ("arr", "revenue", "gm", "burn", "cash", "headcount", "customers", "nrr")
ALIASES = {"arr": "arr", "revenue": "revenue", "gm": "gm", "gross margin": "gm", "burn": "burn",
           "net burn": "burn", "cash": "cash", "headcount": "headcount", "customers": "customers",
           "nrr": "nrr", "net revenue retention": "nrr"}


def _metric_key(label):
    """Map 'ARR', 'arr', 'Net burn (monthly)', 'NRR (TTM)' ... to the standard key; None for other rows."""
    text = re.sub(r"\(.*?\)", "", str(label or "")).strip().lower()
    return ALIASES.get(text)


def _table_rows(ws, first_header, *needed):
    """(header row, rows) of the block whose header row has first_header (in any column) and all `needed`
    headers; rows run until first_header's column is blank (a key column such as A may hold formulas)."""
    for r in range(1, ws.max_row + 1):
        heads = [ws.cell(r, c).value for c in range(1, ws.max_column + 1)]
        if first_header in heads and all(n in heads for n in needed):
            key = heads.index(first_header) + 1
            out, rr = [], r + 1
            while ws.cell(rr, key).value not in (None, ""):
                row = {h: ws.cell(rr, i + 1).value for i, h in enumerate(heads) if h}
                row["_row"] = rr
                out.append(row)
                rr += 1
            return r, out
    return None, []


def _kpi_block(wb):
    """The reported-KPI table: header row has Company, Value and Method; metric column is 'Metric ID' or 'Metric'."""
    _, rows = _table_rows(wb[INPUTS], "Company", "Value", "Method")
    for row in rows:
        row["_metric"] = row.get("Metric ID") or row.get("Metric")
    return rows


def _kpis(wb):
    return {(row["Company"], row["_metric"]): row for row in _kpi_block(wb)}


def _master(wb):
    """Portfolio master: the first Company-headed block that has a plan or stage column."""
    for needed in (("Stage",), ("Sector",)):
        _, rows = _table_rows(wb[INPUTS], "Company", *needed)
        if rows:
            return {row["Company"]: row for row in rows}
    return {}


def _id_num(flag_id):
    """Trailing number of a flag ID: 'F-07', 'F07', 'FLAG_7' and 7 all give 7; None if there is none."""
    m = re.search(r"(\d+)\s*$", str(flag_id))
    return int(m.group(1)) if m else None


def _same_but_ranges(f1, f2):
    """True when two formulas differ only in the end rows of absolute ranges (e.g. Flags!$B$5:$B$19 → $B$25)."""
    norm = lambda f: re.sub(r"(:\$?[A-Z]+\$)\d+", r"\1#", str(f))
    return isinstance(f1, str) and f1.startswith("=") and norm(f1) == norm(f2)


def _flags(wb):
    return {row["ID"]: row for row in _table_rows(wb[FLAGS], "ID", "Finding")[1]}


def _statuses(path):
    with tempfile.TemporaryDirectory() as tmp:
        calc = recalc(path, tmp)
        if calc is None:
            return None
        ws = load_workbook(calc, data_only=True)[METRICS]
        hr = next(r for r in range(1, 15) if ws.cell(r, 1).value == "Company")
        col = next(c for c in range(1, ws.max_column + 1) if ws.cell(hr, c).value == "Status")
        return {ws.cell(r, 1).value: ws.cell(r, col).value for r in range(hr + 1, ws.max_row + 1)
                if ws.cell(r, 1).value}


def run_update_checks(before, after, company="Halyard Robotics"):
    out = []

    def res(check, ok, detail=""):
        out.append({"check": check, "status": "PASS" if ok else "FAIL", "detail": detail})

    b, a = load_workbook(before), load_workbook(after)
    res("Same sheets in the same order", b.sheetnames == a.sheetnames, f"{b.sheetnames} → {a.sheetnames}")

    kb, ka = _kpis(b), _kpis(a)
    changed = [f"{c}/{m}: {k}" for (c, m), row in kb.items() if c != company
               for k in ("Value", "Method", "Confidence") if (c, m) not in ka or ka[(c, m)].get(k) != row.get(k)]
    res("Other companies' inputs unchanged", not changed, "; ".join(changed[:8]))
    lost = [f"{c}/{m}" for (c, m), row in kb.items() if c != company and row.get("Reviewed") and
            ka.get((c, m), {}).get("Reviewed") != row.get("Reviewed")]
    res("Reviewer ticks preserved", not lost, ", ".join(lost[:8]))
    extra = [f"{c}/{m}" for (c, m) in ka if c != company and (c, m) not in kb]
    res("No rows added for other companies", not extra, ", ".join(extra[:8]))

    rows_a = [row for row in _kpi_block(a) if row["Company"] == company]
    new = {_metric_key(row["_metric"]): row for row in rows_a if _metric_key(row["_metric"])}
    missing = [m for m in KPI_KEYS if m not in new or new[m].get("Value") in (None, "")]
    res(f"{company}: all eight metrics added with values", not missing, f"missing: {', '.join(missing)}")
    placeholders = [str(row["_metric"]) for row in rows_a if str(row.get("Method")).lower() == "missing"
                    and _metric_key(row["_metric"]) in KPI_KEYS + (None,)]
    res(f"{company}: 'Missing' placeholders replaced", not placeholders, ", ".join(placeholders))
    weak = [m for m, row in new.items() if not row.get("Method") or not row.get("Location in source")
            or not isinstance(row.get("Confidence"), (int, float))]
    res(f"{company}: every new row has method, source location and confidence", not weak, ", ".join(weak))
    mb, ma = _master(b).get(company, {}), _master(a).get(company, {})
    flipped = [k for k, v in mb.items() if str(v).strip().lower() in ("no", "not received") and ma.get(k) != v]
    res(f"{company}: master shows the report as received", ma.get("Report received") == "Yes" or bool(flipped),
        ", ".join(f"{k}: {mb[k]} → {ma.get(k)}" for k in flipped) or "no 'not received' marker changed")

    kpi_rows = [row["_row"] for row in _kpi_block(a)]
    first, last = (min(kpi_rows), max(kpi_rows)) if kpi_rows else (0, 0)
    short = {}
    for n, d in a.defined_names.items():
        m = re.fullmatch(r"'?Inputs & Review'?!\$[A-Z]+\$(\d+):\$[A-Z]+\$(\d+)", d.attr_text or "")
        if m and int(m.group(1)) == first and int(m.group(2)) != last:
            short[n] = d.attr_text
    res("Named ranges over the KPI table reach its last row", bool(kpi_rows) and not short,
        json.dumps(short) or f"rows {first}–{last}")
    res("Named ranges kept", set(b.defined_names) <= set(a.defined_names),
        ", ".join(sorted(set(b.defined_names) - set(a.defined_names))))
    tb = sorted(t for ws in b.worksheets for t in ws.tables)
    ta = sorted(t for ws in a.worksheets for t in ws.tables)
    res("Tables kept", tb == ta, f"{tb} → {ta}")

    diffs, extended = [], []
    for name in (METRICS, "Dashboard"):
        wsb, wsa = b[name], a[name]
        for r in range(1, max(wsb.max_row, wsa.max_row) + 1):
            for c in range(1, max(wsb.max_column, wsa.max_column) + 1):
                vb, va = wsb.cell(r, c).value, wsa.cell(r, c).value
                if vb != va:
                    (extended if _same_but_ranges(vb, va) else diffs).append(f"{name}!{wsb.cell(r, c).coordinate}")
    res("Company Metrics and Dashboard formulas untouched", not diffs,
        ", ".join(diffs[:8]) or (f"only range ends extended: {', '.join(extended[:4])}" if extended else ""))

    fb, fa = _flags(b), _flags(a)
    rewritten = [i for i, row in fb.items() if i not in fa or
                 any(fa[i].get(k) != row.get(k) for k in ("Company", "Severity", "Category", "Finding"))]
    res("Existing flags kept with the same ID and text", not rewritten, ", ".join(rewritten))
    superseded = [i for i, row in fb.items() if row.get("Company") == company
                  and re.search(r"report", f"{row.get('Category')} {row.get('Finding')}", re.I)
                  and re.search(r"not received|no q\d( \d{4})? report|missing|overdue", f"{row.get('Category')} {row.get('Finding')}",
                                re.I)]
    res(f"{company}: 'report missing' flag set to Resolved",
        bool(superseded) and all(fa.get(i, {}).get("Status") == "Resolved" for i in superseded),
        ", ".join(f"{i}={fa.get(i, {}).get('Status')}" for i in superseded))
    added = [i for i in fa if i not in fb]
    old_nums = [n for n in map(_id_num, fb) if n is not None]
    new_nums = [n for n in map(_id_num, added) if n is not None]
    res(f"{company}: new flags appended with new IDs",
        bool(added) and all(fa[i].get("Company") == company for i in added)
        and (not old_nums or (len(new_nums) == len(added) and min(new_nums) > max(old_nums))), ", ".join(map(str, added)))

    cb, ca = inspect(before)[0], inspect(after)[0]
    res("Charts kept", len(ca) >= len(cb) and len(ca) > 0, f"{len(cb)} → {len(ca)}")

    sb, sa = _statuses(before), _statuses(after)
    if sb is None or sa is None:
        out.append({"check": "Statuses after recalculation", "status": "SKIP", "detail": "LibreOffice not found"})
    else:
        companies = set(_master(b)) or set(sb)  # skip portfolio total rows such as "Portfolio (reporting companies)"
        moved = [f"{c}: {sb[c]} → {sa.get(c)}" for c in sb if c in companies and c != company and sa.get(c) != sb[c]]
        res("Other companies' statuses unchanged", not moved, "; ".join(moved))
        res(f"{company}: status no longer 'No report'", sa.get(company) not in (None, "", "No report"),
            f"{sb.get(company)} → {sa.get(company)}")
    return out


if __name__ == "__main__":
    r = run_update_checks(sys.argv[1], sys.argv[2], *(sys.argv[3:4] or []))
    print(json.dumps(r, indent=2, ensure_ascii=False))
    sys.exit(0 if all(x["status"] != "FAIL" for x in r) else 1)
